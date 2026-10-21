"""The unit tier's DuckDB runs on one thread (`conftest._one_duckdb_thread`).

A fixture that patches a library function stops working quietly when the
library or the import path moves; this is what notices.
"""

import duckdb
import pytest

from sdmxlib.local import LocalRegistry

pytestmark = pytest.mark.unit


def _threads(conn: duckdb.DuckDBPyConnection) -> int:
    row = conn.execute("SELECT current_setting('threads')").fetchone()
    assert row is not None
    threads = row[0]
    assert isinstance(threads, int)
    return threads


def test_a_connection_the_unit_tier_opens_has_one_thread() -> None:
    with duckdb.connect() as conn:
        assert _threads(conn) == 1


def test_a_registry_s_connection_has_one_thread() -> None:
    """`LocalRegistry` opens its own, through `duckdb.connect`, in `src/`."""
    with LocalRegistry(":memory:") as reg:
        assert _threads(reg._require_open()) == 1


def test_a_caller_s_own_thread_count_stands() -> None:
    with duckdb.connect(config={"threads": 3}) as conn:
        assert _threads(conn) == 3


@pytest.mark.serial
def test_a_serial_test_keeps_duckdb_s_default() -> None:
    """The #75 reproducers need DuckDB's own threads to show their race."""
    with duckdb.connect() as conn:
        assert _threads(conn) != 1
