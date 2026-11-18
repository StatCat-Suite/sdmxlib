"""`write_observations(mode="merge")`: a revision delta applied by key (GH-393)."""

from collections.abc import Iterator
from pathlib import Path
from typing import Literal

import duckdb
import polars as pl
import pytest

import sdmxlib as sl
from sdmxlib.edition import Producer, check_edition, seal
from sdmxlib.local.data_store import DuckDBDataStore
from sdmxlib.model.binding import DataflowBinding
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure, Dimension, Measure
from sdmxlib.model.ref import Ref
from tests.support.factories import concept_ref

pytestmark = pytest.mark.unit

_TABLE = pl.DataFrame({"GEO": ["CA", "CA", "ON"], "T": ["2020", "2021", "2020"], "V": [1.0, 2.0, 3.0]})
#: Revises CA/2021, adds ON/2021, and leaves CA/2020 and ON/2020 alone. The
#: columns are in another order than the table's, which a merge by name allows.
_DELTA = pl.DataFrame({"V": [20.0, 4.0], "T": ["2021", "2021"], "GEO": ["CA", "ON"]})
_MERGED = [("CA", "2020", 1.0), ("CA", "2021", 20.0), ("ON", "2020", 3.0), ("ON", "2021", 4.0)]
_KEYS = ("GEO", "T")


@pytest.fixture
def store(tmp_path: Path) -> Iterator[DuckDBDataStore]:
    """A store with a DuckLake attached as ``lake``, holding `_TABLE` as ``lake.d.obs``."""
    with sl.LocalRegistry(":memory:") as registry:
        data_store = DuckDBDataStore.from_registry(registry)
        data_store.connection.execute(
            f"ATTACH 'ducklake:{tmp_path / 'tables.ducklake'}' AS lake (DATA_PATH '{tmp_path}/data/')"
        )
        data_store.write_observations(schema="d", table="obs", source=_TABLE)
        yield data_store


def _rows(conn: duckdb.DuckDBPyConnection, table: str = "lake.d.obs") -> list[tuple[object, ...]]:
    return sorted(conn.execute(f"SELECT GEO, T, V FROM {table}").fetchall(), key=str)


def _snapshots(conn: duckdb.DuckDBPyConnection) -> int:
    ((count,),) = conn.execute("SELECT count(*) FROM lake.snapshots()").fetchall()
    return int(count)


def test_a_merge_revises_matched_rows_inserts_new_ones_and_keeps_the_rest(store: DuckDBDataStore) -> None:
    before = _snapshots(store.connection)
    store.write_observations(schema="d", table="obs", source=_DELTA, mode="merge", keys=_KEYS)
    assert _rows(store.connection) == _MERGED
    assert _snapshots(store.connection) == before + 1, "one snapshot for the whole delta"


def test_an_append_of_the_same_delta_matches_no_keys(store: DuckDBDataStore) -> None:
    """The discrimination check: the same delta appended keeps CA/2021 twice."""
    store.write_observations(schema="d", table="obs", source=_DELTA.select("GEO", "T", "V"), mode="append")
    assert len(_rows(store.connection)) == 5


def test_a_null_key_matches_a_null_key(store: DuckDBDataStore) -> None:
    store.write_observations(
        schema="d", table="obs", source=pl.DataFrame({"GEO": [None], "T": ["2020"], "V": [7.0]}), mode="append"
    )
    revision = pl.DataFrame({"GEO": [None], "T": ["2020"], "V": [8.0]}, schema=_TABLE.schema)
    store.write_observations(schema="d", table="obs", source=revision, mode="merge", keys=_KEYS)
    assert [row for row in _rows(store.connection) if row[0] is None] == [(None, "2020", 8.0)]


def test_a_table_keyed_on_every_column_only_gains_rows(store: DuckDBDataStore) -> None:
    store.write_observations(
        schema="d", table="obs", source=_DELTA.select("GEO", "T", "V"), mode="merge", keys=("GEO", "T", "V")
    )
    assert len(_rows(store.connection)) == 5


def test_a_failed_merge_leaves_the_table_and_adds_no_snapshot(store: DuckDBDataStore) -> None:
    before = (_rows(store.connection), _snapshots(store.connection))
    # CA/2021 is a valid revision; "x" fails the cast to the table's DOUBLE.
    bad = pl.DataFrame({"GEO": ["CA", "QC"], "T": ["2021", "2021"], "V": ["21", "x"]})
    with pytest.raises(duckdb.ConversionException):
        store.write_observations(schema="d", table="obs", source=bad, mode="merge", keys=_KEYS)
    assert (_rows(store.connection), _snapshots(store.connection)) == before


@pytest.mark.parametrize(
    ("source", "keys", "match"),
    [
        (
            pl.DataFrame({"GEO": ["CA", "CA"], "T": ["2021", "2021"], "V": [1.0, 2.0]}),
            _KEYS,
            r"holds the key \(GEO='CA', T='2021'\) more than once",
        ),
        (_DELTA, ("GEO", "TIME_PERIOD"), r"cannot merge on TIME_PERIOD: keys must be columns of both"),
        (_DELTA.drop("V"), _KEYS, r"same columns; only in the source: none; only in the table: V"),
        (_DELTA, (), r"a merge needs keys="),
    ],
    ids=["duplicate key", "missing key column", "different columns", "no keys"],
)
def test_a_merge_that_does_not_fit_is_refused_before_writing(
    store: DuckDBDataStore, source: pl.DataFrame, keys: tuple[str, ...], match: str
) -> None:
    before = (_rows(store.connection), _snapshots(store.connection))
    with pytest.raises(ValueError, match=match):
        store.write_observations(schema="d", table="obs", source=source, mode="merge", keys=keys)
    assert (_rows(store.connection), _snapshots(store.connection)) == before


@pytest.mark.parametrize("mode", ["overwrite", "append"])
def test_keys_with_another_mode_is_refused(store: DuckDBDataStore, mode: Literal["overwrite", "append"]) -> None:
    with pytest.raises(ValueError, match=rf"keys= applies only to mode='merge'; mode='{mode}'"):
        store.write_observations(schema="d", table="obs", source=_DELTA, mode=mode, keys=_KEYS)


@pytest.mark.parametrize(
    ("partition_by", "sorted_by"), [(("GEO",), ()), ((), ("GEO",))], ids=["partition_by", "sorted_by"]
)
def test_a_merge_keeps_the_layout_and_refuses_a_new_one(
    store: DuckDBDataStore, partition_by: tuple[str, ...], sorted_by: tuple[str, ...]
) -> None:
    with pytest.raises(ValueError, match=r"a merge keeps \"lake\".d.obs's partitioning and sort"):
        store.write_observations(
            schema="d",
            table="obs",
            source=_DELTA,
            mode="merge",
            keys=_KEYS,
            partition_by=partition_by,
            sorted_by=sorted_by,
        )


def test_a_merge_into_a_missing_table_is_refused(store: DuckDBDataStore) -> None:
    with pytest.raises(ValueError, match=r'"lake".d.absent does not exist'):
        store.write_observations(schema="d", table="absent", source=_DELTA, mode="merge", keys=_KEYS)


def test_a_merge_into_a_plain_duckdb_catalog() -> None:
    with sl.LocalRegistry(":memory:") as registry:
        data_store = DuckDBDataStore.from_registry(registry)
        data_store.connection.execute("ATTACH ':memory:' AS plain")
        data_store.write_observations(schema="d", table="obs", source=_TABLE, lake_alias="plain")
        data_store.write_observations(
            schema="d", table="obs", source=_DELTA, mode="merge", keys=_KEYS, lake_alias="plain"
        )
        assert _rows(data_store.connection, "plain.d.obs") == _MERGED


def test_a_registry_merges_into_its_lake(tmp_path: Path) -> None:
    with sl.LocalRegistry.build(tmp_path / "b") as registry:
        registry.write_observations(schema="d", table="obs", source=_TABLE)
        registry.write_observations(schema="d", table="obs", source=_DELTA, mode="merge", keys=_KEYS)
        merged = _rows(registry.connection, f"{registry.lake_alias}.d.obs")
        with pytest.raises(ValueError, match="keys= applies only to mode='merge'"):
            registry.write_observations(schema="d", table="obs", source=_DELTA, mode="append", keys=_KEYS)
    assert merged == _MERGED


# ── an edition revised by a merge ────────────────────────────────────────────

_CONCEPTS = sl.ConceptScheme(
    id="CS",
    maintainer="T",
    version="1.0",
    name={"en": "concepts"},
    concepts=[sl.Concept(id=c, name={"en": c}) for c in ("REGION", "GEO", "OBS_VALUE")],
)
_DSD = DataStructure(
    id="DSD",
    maintainer="T",
    version="1.0",
    name={"en": "DSD"},
    dimensions=[
        Dimension(id="REGION", concept=concept_ref("REGION")),
        Dimension(id="GEO", concept=concept_ref("GEO")),
    ],
    measures=[Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
)
_FLOW = Dataflow(id="F", maintainer="T", version="1.0", name={"en": "F"}, structure=Ref.to(_DSD))
_PRODUCER = Producer(name="tests", version="0")
#: Enough rows that DuckLake writes data files rather than inlining them.
_EDITION_ROWS = 20_000


def _edition_rows(offset: float) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "REGION": [f"R{i % 2}" for i in range(_EDITION_ROWS)],
            "GEO": [f"G{i}" for i in range(_EDITION_ROWS)],
            "OBS_VALUE": [float(i) + offset for i in range(_EDITION_ROWS)],
        }
    )


def _delete_files(edition: Path) -> list[object]:
    conn = duckdb.connect()
    conn.execute(
        f"ATTACH 'ducklake:{edition / 'tables.ducklake'}' AS lake "
        f"(DATA_PATH '{(edition.parent / 'data').resolve()}/', OVERRIDE_DATA_PATH true, READ_ONLY)"
    )
    try:
        files = conn.execute("SELECT delete_file FROM ducklake_list_files('lake', 'obs', schema => 'd')").fetchall()
    finally:
        conn.close()
    return [delete for (delete,) in files]


def test_an_edition_revised_by_a_merge_on_a_partitioned_table_seals_without_a_delete_file(tmp_path: Path) -> None:
    root = tmp_path / "b"
    with sl.LocalRegistry.build(root) as builder:
        builder.write_observations(schema="d", table="obs", source=_edition_rows(0), partition_by=("REGION",))
        builder.add_all([_CONCEPTS, _DSD, _FLOW])
        builder.add(DataflowBinding.from_dsd(_DSD, dataflow=Ref.to(_FLOW), schema="d", table="obs"))
        first = builder.edition_dir
    seal(first, product_id="b", producer=_PRODUCER)

    # Revise the first 100 rows of each region and add 10 new ones.
    delta = pl.concat(
        [
            _edition_rows(0.5).head(200),
            pl.DataFrame({"REGION": ["R0"] * 10, "GEO": [f"N{i}" for i in range(10)], "OBS_VALUE": [1.0] * 10}),
        ]
    )
    with sl.LocalRegistry.build(root, previous=first.name) as builder:
        builder.write_observations(schema="d", table="obs", source=delta, mode="merge", keys=("REGION", "GEO"))
        assert any(_delete_files_of(builder)), "the merge left a delete file for seal to rewrite"
        second = builder.edition_dir
    seal(second, product_id="b", producer=_PRODUCER)

    assert _delete_files(second) == [None] * len(_delete_files(second))
    report = check_edition(second, at="publish")
    assert report.ok, "\n".join(map(str, report.findings))
    with sl.LocalRegistry.edition(second) as reg:
        frame = reg.obs(_FLOW.urn).frame()
    assert frame.height == _EDITION_ROWS + 10
    assert frame.filter(pl.col("GEO") == "G1").get_column("OBS_VALUE").to_list() == [1.5]
    assert frame.filter(pl.col("GEO") == "G500").get_column("OBS_VALUE").to_list() == [500.0]


def _delete_files_of(builder: sl.LocalRegistry) -> list[object]:
    """The delete file of each current data file of ``d.obs`` in a builder's lake."""
    rows = builder.connection.execute(
        "SELECT delete_file FROM ducklake_list_files($lake, 'obs', schema => 'd')", {"lake": builder.lake_alias}
    ).fetchall()
    return [delete for (delete,) in rows]
