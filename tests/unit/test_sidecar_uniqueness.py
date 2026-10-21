"""A sidecar that is not unique on its join keys is refused, not joined (#412).

A series-attributes sidecar is joined as `LEFT JOIN … USING (<keys>)`, and the
keys are *derived* — whatever columns the two tables share — not declared. If
the sidecar holds more than one row per key, every query that reaches a sidecar
column multiplies its rows by that factor: `count()`, `collect()` and every
aggregate come back wrong, and nothing errors. #412 measured a filter on a
sidecar column counting 6 rows out of 3.

So `SchemaInspector.plan` refuses such a sidecar, naming the table, the keys and
both counts. Every consumer goes through it — `obs()` and anyone
splicing `plan.from_clause` into their own SQL — so the guard is in one place.

The check costs a scan of the sidecar (~5 ms on a census bundle's 619-row
geography table), so it runs once per table and key set and is cached beside
the inspector's `DESCRIBE` cache, with the same lifecycle: `invalidate(table)`
clears both. The count of checks run is asserted, not the time they take.
"""

from __future__ import annotations

from datetime import UTC, datetime

import duckdb
import pytest

from sdmxlib.local import LocalRegistry
from sdmxlib.local.data_store import DuckDBDataStore
from sdmxlib.local.sql import SchemaInspector, SidecarNotUniqueError
from sdmxlib.model.binding import DataflowBinding
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.ref import Ref

pytestmark = pytest.mark.unit

SIDECAR = "memory.data.series"


def _binding() -> DataflowBinding:
    return DataflowBinding(
        dataflow=Ref[Dataflow].unresolved(Dataflow.urn_for(agency="AG", id="DF", version="1.0")),
        database_role="memory",
        table_location="data.obs",
        series_attributes_location="data.series",
        dimension_column_map={"FREQ": "FREQ", "REF_AREA": "GEO", "TIME_PERIOD": "TIME_PERIOD"},
        measure_column_map={"OBS_VALUE": "OBS_VALUE"},
        attribute_column_map={"GEO_LEVEL": "GEO_LEVEL"},
        structural_version="AG:DSD(1.0)",
        source_registry="local",
        materialized_at=datetime(2026, 1, 1, tzinfo=UTC),
        bound_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _tables(conn: duckdb.DuckDBPyConnection, sidecar_rows: str) -> None:
    """Three series × two periods of observations, and the given sidecar."""
    conn.execute("CREATE SCHEMA IF NOT EXISTS data")
    conn.execute("""
        CREATE OR REPLACE TABLE data.obs AS
        SELECT 'A' AS FREQ, area AS GEO, period AS TIME_PERIOD, 1.5 AS OBS_VALUE
        FROM (SELECT unnest(['CA', 'US', 'MX']) AS area), (SELECT unnest(['2019', '2020']) AS period)
    """)
    conn.execute("CREATE OR REPLACE TABLE data.series (FREQ TEXT, GEO TEXT, GEO_LEVEL TEXT, VINTAGE TEXT)")
    conn.execute(f"INSERT INTO data.series VALUES {sidecar_rows}")


UNIQUE = "('A', 'CA', 'Country', 'v1'), ('A', 'US', 'Country', 'v1'), ('A', 'MX', 'Country', 'v1')"

#: #412's shape: two vintages per series, and `VINTAGE` is not on the fact
#: table, so the derived keys are `(FREQ, GEO)` and each matches two rows.
TWO_PER_SERIES = UNIQUE + ", ('A', 'CA', 'Country', 'v2'), ('A', 'US', 'Country', 'v2'), ('A', 'MX', 'Country', 'v2')"


def test_the_join_really_fans_out_without_the_guard() -> None:
    """The premise, measured on the raw join: six rows where the truth is three.

    Without this the refusal below could be guarding a fixture that never
    multiplied anything.
    """
    with LocalRegistry(":memory:") as registry:
        conn = registry.connection.cursor()
        _tables(conn, TWO_PER_SERIES)
        row = conn.execute(
            "SELECT count(*) FROM data.obs LEFT JOIN data.series USING (FREQ, GEO) "
            "WHERE GEO_LEVEL = 'Country' AND TIME_PERIOD = '2019'"
        ).fetchone()
        assert row == (6,), "3 observations, each joined to 2 sidecar rows"


def test_observations_refuses_a_sidecar_that_is_not_unique_on_its_keys() -> None:
    with LocalRegistry(":memory:") as registry:
        _tables(registry.connection.cursor(), TWO_PER_SERIES)
        store = DuckDBDataStore.from_registry(registry)
        with pytest.raises(SidecarNotUniqueError) as caught:
            store.obs(_binding())
    message = str(caught.value)
    assert SIDECAR in message
    assert "FREQ, GEO" in message, "names the derived keys"
    assert "6 rows" in message, "the row count"
    assert "3 distinct" in message, "and the distinct-key count"


def test_plan_refuses_it_for_custom_sql_too() -> None:
    """`plan.from_clause` is spliced into callers' own SQL; they get the same guard."""
    with LocalRegistry(":memory:") as registry:
        _tables(registry.connection.cursor(), TWO_PER_SERIES)
        store = DuckDBDataStore.from_registry(registry)
        with pytest.raises(SidecarNotUniqueError):
            store.inspect().plan(_binding())


def test_it_is_still_a_value_error() -> None:
    """Callers catching `plan`'s existing refusal (no shared columns) catch this too."""
    assert issubclass(SidecarNotUniqueError, ValueError)


def test_a_unique_sidecar_is_accepted_and_joins() -> None:
    with LocalRegistry(":memory:") as registry:
        _tables(registry.connection.cursor(), UNIQUE)
        store = DuckDBDataStore.from_registry(registry)
        query = store.obs(_binding())
        assert query.filter(query.col("GEO_LEVEL") == "Country").count() == 6


def test_duplicates_under_a_null_key_are_accepted() -> None:
    """`USING` never matches NULL, so repeated NULL-keyed rows cannot multiply anything."""
    with LocalRegistry(":memory:") as registry:
        _tables(registry.connection.cursor(), UNIQUE + ", ('A', NULL, 'Other', 'v1'), ('A', NULL, 'Other', 'v2')")
        store = DuckDBDataStore.from_registry(registry)
        query = store.obs(_binding())
        assert query.filter(query.col("GEO_LEVEL") == "Country").count() == 6


class _CountingConn:
    """Forwards to a real connection, counting the uniqueness checks it is asked to run."""

    def __init__(self, real: duckdb.DuckDBPyConnection) -> None:
        self._real = real
        self.checks = 0

    def execute(self, sql: str, *args: object, **kwargs: object) -> object:
        if "count(DISTINCT" in sql:
            self.checks += 1
        return self._real.execute(sql, *args, **kwargs)


def test_the_check_runs_once_per_table_and_again_after_invalidate(monkeypatch: pytest.MonkeyPatch) -> None:
    with LocalRegistry(":memory:") as registry:
        _tables(registry.connection.cursor(), UNIQUE)
        inspector = SchemaInspector(registry.connection)
        conn = _CountingConn(registry.connection)
        monkeypatch.setattr(inspector, "_conn", conn)
        for _ in range(5):
            inspector.plan(_binding())
        assert conn.checks == 1, "cached across plans"

        inspector.invalidate(SIDECAR)
        inspector.plan(_binding())
        assert conn.checks == 2, "a rewritten table is checked again"


def test_fixing_the_table_and_invalidating_clears_the_refusal() -> None:
    """The refusal is not cached as a verdict on the name: the producer's fix takes."""
    with LocalRegistry(":memory:") as registry:
        cur = registry.connection.cursor()
        _tables(cur, TWO_PER_SERIES)
        store = DuckDBDataStore.from_registry(registry)
        with pytest.raises(SidecarNotUniqueError):
            store.inspect().plan(_binding())
        cur.execute("DELETE FROM data.series WHERE VINTAGE = 'v2'")
        store.inspect().invalidate(SIDECAR)
        assert store.inspect().plan(_binding()).sidecar == SIDECAR
