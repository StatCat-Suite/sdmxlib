"""A lake table's layout, which DuckLake owns (GH-205).

`write_observations` sets a table's partitioning (``partition_by``) and sort
(``sorted_by``) in DuckLake's catalog, and queries read the partitioning back
from there. The catalog ships with a bundle, so a reader sees the layout the
producer chose, and nothing on the binding can disagree with it.

**Sorted storage does not let DuckDB skip a sort.** It has no read-in-order
optimisation for base tables, so an `ORDER BY` over already-sorted rows still
sorts, measured at 102 MB against 251 MB shuffled at 4M rows (#330). And a
scan comes back in order only while `preserve_insertion_order` is true. So the
sort serves pruning and compression, and never promises a reader rows in
order; `test_sorted_storage_is_not_a_promise_of_read_order` pins that.
"""

import polars as pl
import pytest

from sdmxlib.local.data_store import DuckDBDataStore
from sdmxlib.local.registry import LocalRegistry

pytestmark = pytest.mark.unit

#: Deliberately not in key order, so a write that fails to sort is visible.
_ROWS = pl.DataFrame(
    {
        "GEO": ["QC", "CA", "ON", "CA"],
        "T": ["2021", "2021", "2020", "2020"],
        "V": [4.0, 1.0, 3.0, 2.0],
    }
)


@pytest.fixture
def store(tmp_path):
    """A store with a DuckLake attached as ``lake``."""
    with LocalRegistry(":memory:") as registry:
        data_store = DuckDBDataStore.from_registry(registry)
        data_store.connection.execute(
            f"ATTACH 'ducklake:{tmp_path / 'tables.ducklake'}' AS lake (DATA_PATH '{tmp_path}/data/')"
        )
        yield data_store


@pytest.fixture
def plain():
    """A store whose ``lake`` is a plain DuckDB database."""
    with LocalRegistry(":memory:") as registry:
        data_store = DuckDBDataStore.from_registry(registry)
        data_store.connection.execute("ATTACH ':memory:' AS lake")
        yield data_store


def _keys(store: DuckDBDataStore, table: str) -> list[tuple[str, str]]:
    return [
        (str(geo), str(period)) for geo, period in store.connection.execute(f"SELECT GEO, T FROM {table}").fetchall()
    ]


def _sorted_by(store: DuckDBDataStore) -> list[str]:
    """The sort expressions DuckLake's catalog holds for ``lake.d.obs``."""
    rows = store.connection.execute(
        """
        SELECT e.expression
        FROM __ducklake_metadata_lake.ducklake_sort_expression e
        JOIN __ducklake_metadata_lake.ducklake_sort_info i USING (sort_id)
        JOIN __ducklake_metadata_lake.ducklake_table t ON t.table_id = i.table_id
        WHERE t.table_name = 'obs' AND t.end_snapshot IS NULL AND i.end_snapshot IS NULL
        ORDER BY e.sort_key_index
        """
    ).fetchall()
    return [str(expression) for (expression,) in rows]


# ── the sort ──────────────────────────────────────────────────────────────


def test_sorted_by_is_the_tables_sort_in_the_catalog(store) -> None:
    store.write_observations(schema="d", table="obs", source=_ROWS, sorted_by=("GEO", "T"))
    assert _sorted_by(store) == ["GEO", "T"]
    assert _keys(store, "lake.d.obs") == [("CA", "2020"), ("CA", "2021"), ("ON", "2020"), ("QC", "2021")]


def test_sorted_by_takes_expressions(store) -> None:
    """A sort key is DuckLake's: an expression with a direction, not only a column."""
    store.write_observations(schema="d", table="obs", source=_ROWS, sorted_by=("GEO", "T DESC"))
    assert _keys(store, "lake.d.obs") == [("CA", "2021"), ("CA", "2020"), ("ON", "2020"), ("QC", "2021")]


def test_without_sorted_by_the_rows_arrive_as_they_came(store) -> None:
    """A write that silently reordered would surprise anyone who arranged their source deliberately."""
    store.write_observations(schema="d", table="obs", source=_ROWS)
    assert _sorted_by(store) == []
    assert _keys(store, "lake.d.obs") == [("QC", "2021"), ("CA", "2021"), ("ON", "2020"), ("CA", "2020")]


def test_a_sort_ducklake_refuses_leaves_the_table_as_it_was(store) -> None:
    """The create, layout and insert are one transaction."""
    store.write_observations(schema="d", table="obs", source=_ROWS)
    with pytest.raises(Exception, match="NOPE"):
        store.write_observations(schema="d", table="obs", source=_ROWS.head(1), sorted_by=("NOPE",))
    assert len(_keys(store, "lake.d.obs")) == 4


def test_sorted_by_outside_ducklake_is_refused(plain) -> None:
    with pytest.raises(ValueError, match="not a DuckLake catalog"):
        plain.write_observations(schema="d", table="obs", source=_ROWS, sorted_by=("GEO",))


def test_an_append_keeps_the_tables_layout_and_refuses_a_new_one(store) -> None:
    store.write_observations(schema="d", table="obs", source=_ROWS, sorted_by=("GEO", "T"), partition_by=("GEO",))
    for layout in ({"sorted_by": ("T",)}, {"partition_by": ("T",)}):
        with pytest.raises(ValueError, match="an append keeps"):
            store.write_observations(schema="d", table="obs", source=_ROWS, mode="append", **layout)
    store.write_observations(schema="d", table="obs", source=_ROWS, mode="append")
    assert _sorted_by(store) == ["GEO", "T"]
    assert store.inspect().partition_columns("lake", "d.obs") == ("GEO",)


# ── the partitioning ──────────────────────────────────────────────────────


def test_partition_by_is_read_back_from_the_catalog(store) -> None:
    store.write_observations(schema="d", table="obs", source=_ROWS, partition_by=("GEO",))
    assert store.inspect().partition_columns("lake", "d.obs") == ("GEO",)


def test_an_unpartitioned_table_has_no_partition_columns(store, plain) -> None:
    store.write_observations(schema="d", table="obs", source=_ROWS)
    plain.write_observations(schema="d", table="obs", source=_ROWS)
    assert store.inspect().partition_columns("lake", "d.obs") == ()
    assert plain.inspect().partition_columns("lake", "d.obs") == ()


def test_a_rewrite_reads_the_new_partitioning(store) -> None:
    """The inspector caches a table's partitions; a write invalidates them."""
    store.write_observations(schema="d", table="obs", source=_ROWS, partition_by=("GEO",))
    assert store.inspect().partition_columns("lake", "d.obs") == ("GEO",)
    store.write_observations(schema="d", table="obs", source=_ROWS, partition_by=("T",))
    assert store.inspect().partition_columns("lake", "d.obs") == ("T",)


def test_a_column_the_source_lacks_is_refused(store) -> None:
    with pytest.raises(ValueError, match="not a column of the source"):
        store.write_observations(schema="d", table="obs", source=_ROWS, partition_by=("NOPE",))


def test_an_sdmx_id_where_a_column_was_wanted_is_named_as_such(store) -> None:
    """`REF_AREA` when the column is `GEO` is the mistake a caller will make, so the message names it."""
    with pytest.raises(ValueError, match="physical column names, not SDMX ids"):
        store.write_observations(schema="d", table="obs", source=_ROWS, partition_by=("REF_AREA",))


# ── what a sort does not promise ──────────────────────────────────────────


def test_sorted_storage_is_not_a_promise_of_read_order(store) -> None:
    """A scan of a sorted table returns rows in order *while*
    `preserve_insertion_order` is true. It is true by default, and it is what
    a caller turns off for a very large result: measured, the ordering goes and
    the memory does not move. So `stream()` keeps the `ORDER BY` rather than
    trusting the layout.
    """
    store.write_observations(schema="d", table="obs", source=_ROWS, sorted_by=("GEO", "T"))
    assert _keys(store, "lake.d.obs") == sorted(_keys(store, "lake.d.obs")), "ordered, by default"

    store.connection.execute("SET preserve_insertion_order=false")
    reread = store.connection.execute("SELECT GEO, T FROM lake.d.obs").fetchall()
    assert len(reread) == 4, "the rows are all still there"
    # Four rows will not actually reorder; nothing promises they will not,
    # which is why the query language does not rely on it.
    assert store.connection.execute("SELECT current_setting('preserve_insertion_order')").fetchone() == (False,)
