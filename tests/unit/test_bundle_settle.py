"""The lake a publication ships: flushed, copy-on-write, optionally expired and compacted (GH-128).

`write_manifest` settles the lake before its inventory: inlined rows flushed
into data files, every table holding delete files rewritten without them, a
``final`` publication compacted to one file per table, and with
``expire_snapshots`` one snapshot left. Nothing is deleted from ``data/``.
"""

from pathlib import Path

import duckdb
import pytest

import sdmxlib as sl
from sdmxlib.bundle import Lifecycle, Manifest, Producer, check_bundle, read_manifest, write_manifest
from tests.support.bundles import FLOW, make_bundle

pytestmark = pytest.mark.unit

_PRODUCER = Producer(name="tests", version="0")


def _lake(publication: Path) -> duckdb.DuckDBPyConnection:
    """The publication's lake, attached read-only on a connection of its own."""
    conn = duckdb.connect()
    conn.execute(
        f"ATTACH 'ducklake:{publication / 'lake.ducklake'}' AS lake "
        f"(DATA_PATH '{(publication.parent / 'data').resolve()}/', OVERRIDE_DATA_PATH true, READ_ONLY)"
    )
    return conn


def _current(conn: duckdb.DuckDBPyConnection) -> list[tuple[str, str | None]]:
    """The current snapshot's data files and their delete files."""
    return [
        (data, delete)
        for (data, delete) in conn.execute(
            "SELECT data_file, delete_file FROM ducklake_list_files('lake', 'obs', schema => 'd')"
        ).fetchall()
    ]


def _rows_in_data_files(conn: duckdb.DuckDBPyConnection) -> int:
    (rows,) = conn.execute(
        """
        SELECT coalesce(sum(record_count), 0)
        FROM __ducklake_metadata_lake.ducklake_data_file
        WHERE end_snapshot IS NULL
        """
    ).fetchone() or (0,)
    return int(rows)


def _frame(publication: Path) -> list[tuple[str, float]]:
    with sl.LocalRegistry.bundle(publication) as reg:
        return sorted(reg.obs(FLOW.urn).frame().rows())


def test_inlined_rows_are_flushed_into_a_data_file_and_read_the_same(tmp_path: Path) -> None:
    started = make_bundle(tmp_path / "b", 10)
    with sl.LocalRegistry.build(started.parent, resume=started.name) as builder:
        before = sorted(builder.obs(FLOW.urn).frame().rows())
        assert _rows_in_data_files(builder.connection) == 0, "ten rows are inlined into the catalog"
    manifest = write_manifest(started, bundle_id="b", producer=_PRODUCER)
    with _lake(started) as conn:
        assert _rows_in_data_files(conn) == 10, "every row is in a data file"
    assert len([f for f in manifest.files if f.root == "data"]) == 1
    assert _frame(started) == before


def test_a_revising_republish_ships_no_delete_file(tmp_path: Path) -> None:
    """A small delete in a large table is what DuckLake's default threshold
    keeps as a delete file: the daily delta."""
    first = make_bundle(tmp_path / "b", 200_000)
    write_manifest(first, bundle_id="b", producer=_PRODUCER)
    with sl.LocalRegistry.build(first.parent, previous=first.name) as builder:
        builder._data_plane()  # ATTACHes the lake
        builder.connection.execute("DELETE FROM lake.d.obs WHERE OBS_VALUE < 3")
        second = builder.publication
    write_manifest(second, bundle_id="b", producer=_PRODUCER)
    with _lake(second) as conn:
        files = _current(conn)
        assert files
        assert [delete for _, delete in files] == [None] * len(files)
    with sl.LocalRegistry.bundle(second) as reg:
        assert reg.obs(FLOW.urn).count() == 199_997
    with sl.LocalRegistry.bundle(first) as reg:
        assert reg.obs(FLOW.urn).count() == 200_000, "the first publication still reads its rows"
    for publication in (first, second):
        report = check_bundle(publication, at="publish")
        assert report.ok, "\n".join(map(str, report.findings))


def test_a_final_publication_has_one_file_and_the_same_content(tmp_path: Path) -> None:
    def build(name: str, *, lifecycle: Lifecycle) -> Manifest:
        started = make_bundle(tmp_path / name, 200_000)
        with sl.LocalRegistry.build(started.parent, resume=started.name) as builder:
            builder._data_plane()
            for offset in (1, 2, 3):
                builder.connection.execute(
                    "INSERT INTO lake.d.obs SELECT 'G' || (range % 7), (range + ?)::DOUBLE FROM range(50000)",
                    [offset * 1_000_000],
                )
        return write_manifest(started, bundle_id="b", producer=_PRODUCER, lifecycle=lifecycle)

    live = build("live", lifecycle="live")
    final = build("final", lifecycle="final")
    publication = tmp_path / "final" / final.token
    with _lake(publication) as conn:
        ((_, delete),) = _current(conn)
        assert delete is None
        (sorts,) = conn.execute("SELECT count(*) FROM __ducklake_metadata_lake.ducklake_sort_info").fetchone() or (0,)
    assert sorts == 0, "a final publication sets no sort of its own (GH-205)"
    with _lake(tmp_path / "live" / live.token) as conn:
        assert len(_current(conn)) == 4, "one file per insert while live"
    assert live.bindings[0].fingerprint == final.bindings[0].fingerprint, "compaction changes no row"


def test_a_sort_the_producer_set_is_kept(tmp_path: Path) -> None:
    started = make_bundle(tmp_path / "b", 200_000)
    with sl.LocalRegistry.build(started.parent, resume=started.name) as builder:
        builder._data_plane()
        builder.connection.execute("ALTER TABLE lake.d.obs SET SORTED BY (OBS_VALUE DESC)")
        builder.connection.execute("INSERT INTO lake.d.obs SELECT 'G1', (range + 1000000)::DOUBLE FROM range(1000)")
    write_manifest(started, bundle_id="b", producer=_PRODUCER, lifecycle="final")
    with _lake(started) as conn:
        ((data, _),) = _current(conn)
        values = [v for (v,) in conn.execute("SELECT OBS_VALUE FROM read_parquet(?)", [data]).fetchall()]
    assert values == sorted(values, reverse=True)


def test_expiring_snapshots_leaves_one_and_deletes_no_file(tmp_path: Path) -> None:
    started = make_bundle(tmp_path / "b", 200_000)
    with sl.LocalRegistry.build(started.parent, resume=started.name) as builder:
        builder._data_plane()
        builder.connection.execute("DELETE FROM lake.d.obs WHERE OBS_VALUE < 100000")
    data = tmp_path / "b" / "data"
    on_disk = sorted(p.name for p in data.rglob("*.parquet"))
    manifest = write_manifest(started, bundle_id="b", producer=_PRODUCER, expire_snapshots=True)
    with _lake(started) as conn:
        (snapshots,) = conn.execute("SELECT count(*) FROM ducklake_snapshots('lake')").fetchone() or (0,)
        current = {Path(d).name for d, _ in _current(conn)}
    assert snapshots == 1
    assert {Path(f.path).name for f in manifest.files if f.root == "data"} == current, "only what the snapshot reads"
    assert set(on_disk) <= {p.name for p in data.rglob("*.parquet")}, "nothing deleted from data/"


def test_without_expiry_the_earlier_snapshots_files_stay_listed(tmp_path: Path) -> None:
    started = make_bundle(tmp_path / "b", 200_000)
    with sl.LocalRegistry.build(started.parent, resume=started.name) as builder:
        builder._data_plane()
        builder.connection.execute("DELETE FROM lake.d.obs WHERE OBS_VALUE < 100000")
    manifest = write_manifest(started, bundle_id="b", producer=_PRODUCER)
    with _lake(started) as conn:
        current = {Path(d).name for d, _ in _current(conn)}
    listed = {Path(f.path).name for f in manifest.files if f.root == "data"}
    assert current < listed, "the rewritten file's predecessor is still reachable by time travel"


def test_a_delete_file_in_a_published_lake_is_a_finding(tmp_path: Path) -> None:
    publication = make_bundle(tmp_path / "b", 200_000)
    write_manifest(publication, bundle_id="b", producer=_PRODUCER)
    conn = duckdb.connect()
    conn.execute(
        f"ATTACH 'ducklake:{publication / 'lake.ducklake'}' AS lake "
        f"(DATA_PATH '{(publication.parent / 'data').resolve()}/', OVERRIDE_DATA_PATH true)"
    )
    conn.execute("DELETE FROM lake.d.obs WHERE OBS_VALUE < 3")
    conn.execute("CALL ducklake_flush_inlined_data('lake')")
    conn.close()
    findings = [f for f in check_bundle(publication, at="publish").findings if f.check == "lake"]
    assert any("a delete file in the current snapshot" in f.detail for f in findings), findings


def test_the_manifest_still_reads_back(tmp_path: Path) -> None:
    started = make_bundle(tmp_path / "b", 10)
    manifest = write_manifest(started, bundle_id="b", producer=_PRODUCER, lifecycle="final", expire_snapshots=True)
    assert read_manifest(started) == manifest
