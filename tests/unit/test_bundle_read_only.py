"""A published bundle opens read-only, as a `BundleRegistry` (GH-122).

`LocalRegistry.bundle(path)` opened `meta.duckdb` read-write, so a second
process opening the same bundle failed with DuckDB's "Conflicting lock", and a
server could run one worker process per bundle. A published bundle is never
written, so it opens read-only, and the type it returns has no writes.
"""

import subprocess
import sys
import textwrap
from pathlib import Path
from typing import assert_type

import duckdb
import pytest

import sdmxlib as sl
from sdmxlib.bundle import ManifestError, check_bundle
from tests.support.bundles import FLOW, make_bundle, publish

pytestmark = pytest.mark.unit

#: Every method `LocalRegistry` adds to `BundleRegistry`: the writes, and raw SQL.
_WRITES = ("add", "add_all", "drop", "replace", "ingest", "scan_csv", "scan_json", "transaction", "sql")


def _hold_open_and_wait(root: Path) -> subprocess.Popen[str]:
    """A second process holding `root` open as a bundle until its stdin closes."""
    code = textwrap.dedent(f"""
        import sys
        import sdmxlib as sl
        with sl.LocalRegistry.bundle({str(root)!r}) as reg:
            print(reg.obs(reg.browse()[0].dataflow_urn).count(), flush=True)
            sys.stdin.read()
    """)
    holder = subprocess.Popen(  # noqa: S603 — fixed `sys.executable` and script, as the three existing sites
        [sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
    )
    assert holder.stdout is not None
    assert holder.stdout.readline().strip() == "10", "the holder opened the bundle and read it"
    return holder


def test_bundle_returns_the_read_type_and_build_the_write_type(tmp_path: Path) -> None:
    root = publish(make_bundle(tmp_path / "b", 10))
    with sl.LocalRegistry.bundle(root) as reg:
        assert_type(reg, sl.BundleRegistry)
        assert type(reg) is sl.BundleRegistry
        assert [name for name in _WRITES if hasattr(reg, name)] == []
    with sl.LocalRegistry.build(root) as builder:
        assert_type(builder, sl.LocalRegistry)
        assert [name for name in _WRITES if not hasattr(builder, name)] == []


def test_two_processes_read_one_bundle_at_once(tmp_path: Path) -> None:
    root = publish(make_bundle(tmp_path / "b", 10))
    holder = _hold_open_and_wait(root)
    try:
        with sl.LocalRegistry.bundle(root) as reg:
            assert reg.obs(FLOW.urn).count() == 10
    finally:
        holder.communicate("")
    assert holder.returncode == 0


def test_check_bundle_runs_beside_a_process_that_holds_the_bundle(tmp_path: Path) -> None:
    root = publish(make_bundle(tmp_path / "b", 10))
    holder = _hold_open_and_wait(root)
    try:
        report = check_bundle(root, references=False)
    finally:
        holder.communicate("")
    assert report.ok, "\n".join(map(str, report.findings))


def test_a_write_through_the_connection_is_refused_by_duckdb(tmp_path: Path) -> None:
    """Read-only is the database's mode, not only the type's surface."""
    root = publish(make_bundle(tmp_path / "b", 10))
    with sl.LocalRegistry.bundle(root) as reg, pytest.raises(duckdb.Error, match="read-only"):
        reg.connection.execute("CREATE TABLE sdmx.probe (x INTEGER)")


def test_a_read_only_bundle_spills_outside_the_bundle(tmp_path: Path) -> None:
    """DuckDB's default for a read-only database is `<file>.tmp` beside it."""
    root = publish(make_bundle(tmp_path / "b", 10))
    with sl.LocalRegistry.bundle(root) as reg:
        (spill,) = reg.connection.execute("SELECT current_setting('temp_directory')").fetchone() or ("",)
    assert spill
    assert not Path(str(spill)).resolve().is_relative_to(root.resolve()), spill


def test_publishing_while_a_builder_holds_the_store_is_refused(tmp_path: Path) -> None:
    """A builder still holding the store keeps its writes in a write-ahead log
    outside the file the manifest checksums; DuckDB checkpoints only when the
    last connection closes. Publishing then is refused, naming the fix, and
    succeeds once the builder has closed."""
    root = make_bundle(tmp_path / "b", 10)
    with sl.LocalRegistry.build(root) as builder:
        builder.connection.execute("SET checkpoint_threshold = '1GB'")
        builder.connection.execute("CREATE TABLE sdmx.late_write AS SELECT 1 AS x")
        with pytest.raises(ManifestError, match=r"meta\.duckdb\.wal.*Close every builder before publishing"):
            publish(root)
    publish(root)
    assert sorted(p.name for p in root.rglob("*.wal")) == []
    assert check_bundle(root, references=False).ok


def test_the_files_check_names_a_write_ahead_log(tmp_path: Path) -> None:
    root = publish(make_bundle(tmp_path / "b", 10))
    (root / "meta.duckdb.wal").write_bytes(b"\x00")
    findings = [(f.check, f.subject, f.detail) for f in check_bundle(root, references=False).findings]
    assert (
        "files",
        "meta.duckdb.wal",
        "a write-ahead log: its database was not checkpointed before publishing",
    ) in findings
