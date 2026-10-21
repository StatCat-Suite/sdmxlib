"""Tests for the custom-SQL seam — SchemaInspector, QueryPlan, DESCRIBE cache (#163)."""

import threading
from datetime import UTC, datetime

import attrs
import duckdb
import polars as pl
import pytest

import sdmxlib as sl
import sdmxlib.local.sql
from sdmxlib.local import LocalRegistry
from sdmxlib.local.data_store import DuckDBDataStore
from sdmxlib.local.sql import QueryPlan, SchemaInspector
from sdmxlib.model.binding import DataflowBinding
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.ref import Ref
from sdmxlib.session import cursor_for
from sdmxlib.sql import SqlRelation

pytestmark = pytest.mark.unit


# ── Fixture builders ────────────────────────────────────────────────────


def _build_tables(local: LocalRegistry) -> None:
    """Main fact table + a series-attributes sidecar sharing the key columns."""
    cur = local.connection.cursor()
    cur.execute("CREATE SCHEMA IF NOT EXISTS data")
    cur.execute("""
        CREATE OR REPLACE TABLE data.observations (
            FREQ TEXT,
            REF_AREA TEXT,
            OBS_VALUE DOUBLE
        )
    """)
    cur.execute("INSERT INTO data.observations VALUES ('A', 'CA', 100.0), ('A', 'US', 200.0)")
    # Sidecar keyed on (FREQ, REF_AREA); adds GEO_NAME not present on the fact table.
    cur.execute("""
        CREATE OR REPLACE TABLE data.series_attrs (
            FREQ TEXT,
            REF_AREA TEXT,
            GEO_NAME TEXT
        )
    """)
    cur.execute("INSERT INTO data.series_attrs VALUES ('A', 'CA', 'Canada'), ('A', 'US', 'United States')")


def _binding(*, sidecar: str | None = None) -> DataflowBinding:
    flow_urn = Dataflow.urn_for(agency="TEST", id="DF_TEST", version="1.0")
    return DataflowBinding(
        dataflow=Ref[Dataflow].unresolved(flow_urn),
        database_role="memory",
        table_location="data.observations",
        dimension_column_map={"FREQ": "FREQ", "REF_AREA": "REF_AREA"},
        measure_column_map={"OBS_VALUE": "OBS_VALUE"},
        attribute_column_map={},
        structural_version="TEST:DSD_TEST(1.0)",
        source_registry="local",
        materialized_at=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
        bound_at=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
        series_attributes_location=sidecar,
    )


class _CountingHandle:
    """Forwards to a thread's real handle, counting ``DESCRIBE`` calls into its spy."""

    def __init__(self, real: duckdb.DuckDBPyConnection, spy: _CountingCursors) -> None:
        self._real = real
        self._spy = spy

    def execute(self, sql: str, *args: object, **kwargs: object) -> object:
        if sql.lstrip().upper().startswith("DESCRIBE"):
            self._spy.describe_count += 1
        return self._real.execute(sql, *args, **kwargs)


class _CountingCursors:
    """Stands in for `cursor_for` where the inspector calls it.

    The inspector runs on each thread's own handle, never on its base
    connection (#75), so the count is taken there — every thread's handle
    counting into this one spy.
    """

    def __init__(self) -> None:
        self.describe_count = 0

    def __call__(self, base: duckdb.DuckDBPyConnection) -> _CountingHandle:
        return _CountingHandle(cursor_for(base), self)


MAIN = "memory.data.observations"
SIDECAR = "memory.data.series_attrs"


# ── Reflection ───────────────────────────────────────────────────────────


def test_columns_returns_table_columns():
    with LocalRegistry(":memory:") as local:
        _build_tables(local)
        insp = DuckDBDataStore.from_registry(local).inspect()
        assert insp.columns(MAIN) == ("FREQ", "REF_AREA", "OBS_VALUE")


def test_shared_columns_in_main_order():
    with LocalRegistry(":memory:") as local:
        _build_tables(local)
        insp = DuckDBDataStore.from_registry(local).inspect()
        # Intersection in MAIN order — FREQ, REF_AREA (OBS_VALUE/GEO_NAME excluded).
        assert insp.shared_columns(MAIN, SIDECAR) == ("FREQ", "REF_AREA")


# ── Query planning ───────────────────────────────────────────────────────


def test_plan_no_sidecar():
    with LocalRegistry(":memory:") as local:
        _build_tables(local)
        insp = DuckDBDataStore.from_registry(local).inspect()
        plan = insp.plan(_binding())
        assert plan.sidecar is None
        assert plan.join_keys == ()
        assert plan.from_clause == MAIN


def test_plan_sidecar_join():
    with LocalRegistry(":memory:") as local:
        _build_tables(local)
        insp = DuckDBDataStore.from_registry(local).inspect()
        plan = insp.plan(_binding(sidecar="data.series_attrs"))
        assert plan.main == MAIN
        assert plan.sidecar == SIDECAR
        assert plan.join_keys == ("FREQ", "REF_AREA")
        assert plan.from_clause == f"{MAIN} LEFT JOIN {SIDECAR} USING (FREQ, REF_AREA)"


def test_plan_no_shared_columns_raises():
    with LocalRegistry(":memory:") as local:
        cur = local.connection.cursor()
        cur.execute("CREATE SCHEMA IF NOT EXISTS data")
        cur.execute("CREATE OR REPLACE TABLE data.observations (FREQ TEXT, OBS_VALUE DOUBLE)")
        cur.execute("CREATE OR REPLACE TABLE data.orphan (WIDGET TEXT)")
        insp = DuckDBDataStore.from_registry(local).inspect()
        with pytest.raises(ValueError, match="shares no columns"):
            insp.plan(_binding(sidecar="data.orphan"))


# ── QueryPlan value object ───────────────────────────────────────────────


def test_queryplan_from_clause_defaults():
    assert QueryPlan(main="a.b", sidecar=None, join_keys=()).from_clause == "a.b"
    joined = QueryPlan(main="a.b", sidecar="a.c", join_keys=("K1", "K2"))
    assert joined.from_clause == "a.b LEFT JOIN a.c USING (K1, K2)"


# ── DESCRIBE cache (Ask 2 — removes the consumer monkeypatch) ─────────────


def test_describe_is_cached_across_queries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each table is DESCRIBEd at most once across many plans — the cache guard.

    Calls `plan` directly. It used to go through `_build_query`, which is gone
    with the SQL-fragment API (#329), and the binding here has a sidecar —
    which the typed root refuses — so there is no query to route it through
    any more. Testing the cache at its own level is the better shape anyway:
    the guard is about `SchemaInspector`, not about whoever calls it.
    """
    with LocalRegistry(":memory:") as local:
        _build_tables(local)
        store = DuckDBDataStore.from_registry(local)
        insp = store.inspect()
        counting = _CountingCursors()
        monkeypatch.setattr(sdmxlib.local.sql, "cursor_for", counting)
        binding = _binding(sidecar="data.series_attrs")
        for _ in range(5):
            insp.plan(binding)
        # MAIN + SIDECAR, one DESCRIBE each — not 2 per call.
        assert counting.describe_count == 2


def test_columns_is_single_flight_under_concurrency(monkeypatch: pytest.MonkeyPatch) -> None:
    """Concurrent cold-cache callers for one table DESCRIBE it exactly once."""
    with LocalRegistry(":memory:") as local:
        _build_tables(local)
        insp = DuckDBDataStore.from_registry(local).inspect()
        counting = _CountingCursors()
        monkeypatch.setattr(sdmxlib.local.sql, "cursor_for", counting)
        start = threading.Barrier(8)

        def hammer() -> None:
            start.wait()  # release all threads at once for maximum contention
            insp.columns(MAIN)

        threads = [threading.Thread(target=hammer) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # Single-flight: one build wins, the rest see the hot cache.
        assert counting.describe_count == 1


def test_store_shares_one_inspector():
    with LocalRegistry(":memory:") as local:
        _build_tables(local)
        store = DuckDBDataStore.from_registry(local)
        assert store.inspect() is store.inspect()


def test_invalidate_reflects_schema_change():
    with LocalRegistry(":memory:") as local:
        _build_tables(local)
        insp = DuckDBDataStore.from_registry(local).inspect()
        assert insp.columns(MAIN) == ("FREQ", "REF_AREA", "OBS_VALUE")
        local.connection.cursor().execute("ALTER TABLE data.observations ADD COLUMN NOTE TEXT")
        # Still cached — stale on purpose until invalidated.
        assert insp.columns(MAIN) == ("FREQ", "REF_AREA", "OBS_VALUE")
        insp.invalidate(MAIN)
        assert insp.columns(MAIN) == ("FREQ", "REF_AREA", "OBS_VALUE", "NOTE")


def test_write_observations_invalidates_cache():
    """write_observations clears the cache so a rewritten schema is re-reflected."""
    with LocalRegistry(":memory:") as local:
        _build_tables(local)
        store = DuckDBDataStore.from_registry(local, attach={"lake": "':memory:'"})
        insp = store.inspect()
        _ = insp.columns(MAIN)  # populate cache
        assert insp._columns_cache
        store.write_observations(
            schema="s",
            table="t",
            source=pl.DataFrame({"FREQ": ["A"], "OBS_VALUE": [1.0]}),
        )
        assert insp._columns_cache == {}


# ── End-to-end: the seam produces working custom SQL ─────────────────────


def test_plan_from_clause_drives_custom_sql():
    with LocalRegistry(":memory:") as local:
        _build_tables(local)
        store = DuckDBDataStore.from_registry(local)
        plan = store.inspect().plan(_binding(sidecar="data.series_attrs"))
        # `from_clause` is sdmxlib-derived, and the value is parameterised —
        # the distinction CLAUDE.md's "SQL style" draws, and the reason this
        # interpolation is fine. (Carried a line-level S608 suppression until
        # tests/ became exempt for that rule wholesale.)
        sql = f"SELECT REF_AREA, GEO_NAME FROM {plan.from_clause} WHERE FREQ = ? ORDER BY REF_AREA"
        rows = store.connection.execute(sql, ["A"]).fetchall()
        assert rows == [("CA", "Canada"), ("US", "United States")]


def test_top_level_exports():
    assert sl.SchemaInspector is SchemaInspector
    assert sl.QueryPlan is QueryPlan


# ── The empty OR-set, end to end (#256) ─────────────────────────────────


@pytest.mark.parametrize(
    ("dimensions", "expected"),
    [
        ([None, None], ["CA", "US"]),
        ([None, ["CA"]], ["CA"]),
        ([None, []], []),
        ([[], None], []),
    ],
)
def test_empty_component_filter_returns_no_rows(
    dimensions: list[list[str] | SqlRelation | None],
    expected: list[str],
) -> None:
    """An empty OR-set selects nothing, through the real query path.

    ``[]`` used to compile to ``IN ()``, which DuckDB rejects outright — a
    parser error reachable from the public `ComponentFilter` constructor while
    `sdmxlib.rest` sat at 100% line coverage, because nothing executed the
    fragments it emitted (#256).

    Exercised through the real query path rather than by composing a SELECT in
    the test, so what runs is the production compilation of binding, filter and
    binds — the thing that has to survive an empty set, not a hand-rolled
    approximation of it. That path was `DuckDBDataStore.query` and its
    `to_where_clauses` concatenation; it is now `filter_components`, which
    routes the same `[]` through `is_in([])`. The reading is unchanged and the
    compiler is not, which is exactly why this test moved rather than went.
    """
    cf = sl.ComponentFilter(dimension_ids=["FREQ", "REF_AREA"], dimensions=dimensions)
    with LocalRegistry(":memory:") as local:
        _build_tables(local)
        store = DuckDBDataStore.from_registry(local)
        obs = store.obs(_binding())
        df = obs.filter_components(cf).order_by(obs.col("REF_AREA")).frame()

    assert df["REF_AREA"].to_list() == expected


def test_empty_attribute_filter_returns_no_rows() -> None:
    """Same for the attribute path, which has its own emit branch.

    The attribute lives on the sidecar, so this also proves the contradiction
    survives the LEFT JOIN rather than being optimised into it.

    **This is #332's acceptance test**, and it predates the feature: it was
    written against the SQL-fragment API because the attribute path had its own
    emit branch, and it uses a sidecar binding deliberately. Running the same
    assertions through `obs()` is what "the typed path can read a
    sidecar" means.
    """
    binding = attrs.evolve(
        _binding(sidecar="data.series_attrs"),
        attribute_column_map={"GEO_NAME": "GEO_NAME"},
    )
    cf = sl.ComponentFilter(
        dimension_ids=["FREQ", "REF_AREA"],
        dimensions=[None, None],
        attributes={"GEO_NAME": []},
    )
    with LocalRegistry(":memory:") as local:
        _build_tables(local)
        store = DuckDBDataStore.from_registry(local)
        empty = store.obs(binding).filter_components(cf).frame()
        populated = (
            store.obs(binding)
            .filter_components(
                sl.ComponentFilter(
                    dimension_ids=["FREQ", "REF_AREA"],
                    dimensions=[None, None],
                    attributes={"GEO_NAME": ["Canada"]},
                )
            )
            .frame()
        )

    assert empty.height == 0
    assert populated["REF_AREA"].to_list() == ["CA"], "the non-empty case must still match"
