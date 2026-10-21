"""DuckLake catalog-format version-mismatch handling (#156).

When a bundle's DuckLake catalog format is newer than the installed
``ducklake`` extension supports, the raw DuckDB exception is opaque.
``DuckDBDataStore`` translates it into a clear, actionable
``DuckLakeVersionError`` while leaving every other ATTACH failure
untouched.

A real format-1.0 catalog can't reproduce the failure here (CI runs
duckdb >= 1.5.4, which reads format 1.0), so the ATTACH-time tests
simulate the DuckDB exception.
"""

import duckdb
import pytest

from sdmxlib.local.data_store import (
    DuckDBDataStore,
    DuckLakeVersionError,
    _is_ducklake_version_mismatch,
)

pytestmark = pytest.mark.unit


# ── _is_ducklake_version_mismatch ─────────────────────────────────────


def test_detects_catalog_version_mismatch_message():
    exc = duckdb.InvalidInputException(
        "Invalid Input Error: DuckLake catalog version mismatch: catalog version "
        "is 1.0, but the extension requires version 0.4. To automatically migrate, "
        "set AUTOMATIC_MIGRATION to TRUE when attaching."
    )
    assert _is_ducklake_version_mismatch(exc)


def test_detects_only_supported_versions_message():
    exc = duckdb.NotImplementedException(
        "Not implemented Error: Only DuckLake versions 0.1, 0.2, 0.3-dev1, 0.3, 0.4-dev1, 0.4 are supported"
    )
    assert _is_ducklake_version_mismatch(exc)


def test_ignores_unrelated_duckdb_error():
    assert not _is_ducklake_version_mismatch(duckdb.IOException("No such file: lake.ducklake"))
    assert not _is_ducklake_version_mismatch(ValueError("ducklake something else entirely"))


# ── ATTACH-time translation ───────────────────────────────────────────


class _FakeConn:
    """Minimal connection that raises ``exc`` on any ATTACH statement."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.executed: list[str] = []

    def execute(self, sql: str, *args: object, **kwargs: object) -> None:
        self.executed.append(sql)
        if sql.strip().upper().startswith("ATTACH"):
            raise self._exc


def _data_store_with_conn(conn: _FakeConn, monkeypatch: pytest.MonkeyPatch) -> DuckDBDataStore:
    """Build a DuckDBDataStore bound to a fake connection and a ducklake attach."""
    ds = DuckDBDataStore.__new__(DuckDBDataStore)
    # A fake connection standing in for DuckDB's, which cannot be subclassed
    # or constructed bare; the store only calls `execute` on it here, which
    # the fake provides. Installed as the fake it is, not typed as the real one.
    monkeypatch.setattr(ds, "_conn", conn, raising=False)
    attach: dict[str, str | tuple[str, str]] = {
        "lake": ("'ducklake:/tmp/lake.ducklake'", "(DATA_PATH '/tmp/parquet/')")
    }
    ds._attach = attach
    ds._extensions = ["ducklake"]
    ds._azure_credentials = None
    return ds


def test_attach_version_mismatch_raises_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    original = duckdb.InvalidInputException(
        "Invalid Input Error: DuckLake catalog version mismatch: catalog version is "
        "1.0, but the extension requires version 0.4."
    )
    ds = _data_store_with_conn(_FakeConn(original), monkeypatch)

    with pytest.raises(DuckLakeVersionError) as excinfo:
        ds._configure_session()

    # Actionable guidance and the chained original.
    assert "ducklake extension" in str(excinfo.value).lower()
    assert ">=1.5.3" in str(excinfo.value)
    assert excinfo.value.__cause__ is original


def test_attach_unrelated_duckdb_error_propagates_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    original = duckdb.IOException("IO Error: No such file: /tmp/lake.ducklake")
    ds = _data_store_with_conn(_FakeConn(original), monkeypatch)

    with pytest.raises(duckdb.IOException):
        ds._configure_session()
