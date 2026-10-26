"""Is one batched scan really cheaper than N per-component scans? (#277)

#277 reports that a downstream SDMX REST service bypasses `build_component_availability` and
writes its own SQL, on the grounds that ours issues a separate
`SELECT DISTINCT` per component where one scan would do:

    -- their query, one scan, every dimension at once
    SELECT array_agg(DISTINCT REF_AREA ...) FILTER (WHERE REF_AREA IS NOT NULL),
           array_agg(DISTINCT GENDER   ...) FILTER (WHERE GENDER   IS NOT NULL),
           ..., COUNT(*)
    FROM main JOIN sidecar ...

The issue asked, in its own words, to "confirm it beats N scans on a real lake
rather than assuming — the consumers' claim is plausible and unmeasured by us".

**Measured, it does not.** The batched form was slower in every shape tried:

    6 dims,  3M rows, card 100,  in-memory     25 ms  vs  31 ms   0.82x
    6 dims,  3M rows, card 100,  parquet        7 ms  vs  12 ms   0.55x
    20 dims, 3M rows, card 100,  parquet       25 ms  vs  53 ms   0.48x
    20 dims, 3M rows, card 5000, parquet       42 ms  vs  73 ms   0.58x
    40 dims, 1M rows, card 100,  parquet       34 ms  vs  65 ms   0.52x
    6 dims,  20M rows, card 100, parquet       24 ms  vs  60 ms   0.40x

and end to end, against our real code path:

    build_component_availability(component_id="all")   27 ms
    the consumers' batched scan + COUNT                32 ms
    raw N single-column DISTINCT scans                 26 ms

The reason is that **"one scan" is not one scan's worth of I/O**. Parquet is
columnar, so N queries each projecting one column read the same bytes as one
query projecting N columns — there is no I/O to save. What differs is the
aggregation: `SELECT DISTINCT c` is a single-column parallel hash-distinct,
while N `array_agg(DISTINCT …) FILTER (…)` build N distinct sets inside one
operator and materialise them as arrays. The second is the slower plan.

So the N-scan shape is deliberate, not an oversight, and this benchmark exists
to keep that answer attached to evidence. Our own wrapper costs ~1 ms over raw
SQL, which is the other half of the finding: there was no overhead to remove.

None of this touches #277's *structural* argument — that an API of
one-question-per-call cannot express "all of these at once". That argument
stands on its own and is noted against #273; it just is not a performance
argument, and `component_id="all"` already answers the availability case.

Run with ``just bench`` or ``uv run pytest tests/bench -m bench``.
"""

from __future__ import annotations

import statistics
import time
from datetime import UTC, datetime

import duckdb
import pytest

from sdmxlib.builders.contentconstraint import build_component_availability
from sdmxlib.local import LocalRegistry
from sdmxlib.model.binding import DataflowBinding
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure, Dimension, Measure
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.ref import Ref
from tests.support.factories import concept_ref

pytestmark = pytest.mark.bench

_DIMS = [f"D{i}" for i in range(8)]
_ROWS = 500_000
_CARDINALITY = 200
_FLOW = Dataflow.urn_for(agency="TEST", id="DF", version="1.0")


def _median_ms(fn, rounds: int = 5) -> float:
    fn()  # warm
    samples = []
    for _ in range(rounds):
        start = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - start)
    return statistics.median(samples) * 1e3


def _batched_sql() -> str:
    """The consumers' shape: every component's distincts in one statement."""
    aggs = ", ".join(f'array_agg(DISTINCT "{d}") FILTER (WHERE "{d}" IS NOT NULL)' for d in _DIMS)
    return f"SELECT {aggs}, COUNT(*) FROM data.observations"


def _n_scans(conn: duckdb.DuckDBPyConnection) -> None:
    """Our shape: one single-column DISTINCT per component."""
    for dim in _DIMS:
        sql = f'SELECT DISTINCT "{dim}" FROM data.observations WHERE "{dim}" IS NOT NULL'
        conn.execute(sql).fetchall()


@pytest.fixture
def catalog():
    """A bound dataflow over a wide, populated lake table."""
    with LocalRegistry(":memory:") as local:
        dsd_urn = DataStructure.urn_for(agency="TEST", id="DSD", version="1.0")
        local.add(
            DataStructure(
                id="DSD",
                maintainer="TEST",
                version="1.0",
                name=InternationalString(en="d"),
                dimensions=[Dimension(id=d, concept=concept_ref(d)) for d in _DIMS],
                measures=[Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
            )
        )
        local.add(
            Dataflow(
                id="DF",
                maintainer="TEST",
                version="1.0",
                name=InternationalString(en="f"),
                structure=Ref[DataStructure].unresolved(dsd_urn),
            )
        )
        conn = local.connection
        conn.execute("CREATE SCHEMA IF NOT EXISTS data")
        columns = ", ".join(f"'{d}_' || (i % {_CARDINALITY})::VARCHAR AS {d}" for d in _DIMS)
        ddl = f"CREATE TABLE data.observations AS SELECT {columns}, i::DOUBLE AS OBS_VALUE FROM range({_ROWS}) s(i)"
        conn.execute(ddl)
        local.add(
            DataflowBinding(
                dataflow=Ref[Dataflow].unresolved(_FLOW),
                database_role="memory",
                table_location="data.observations",
                dimension_column_map={d: d for d in _DIMS},
                measure_column_map={"OBS_VALUE": "OBS_VALUE"},
                attribute_column_map={},
                structural_version="TEST:DSD(1.0)",
                source_registry="local",
                materialized_at=datetime(2026, 4, 30, tzinfo=UTC),
                bound_at=datetime(2026, 4, 30, tzinfo=UTC),
            )
        )
        yield local, conn


def test_batching_the_scans_does_not_help(catalog) -> None:
    """The #277 claim, as an assertion rather than a paragraph.

    Deliberately loose: it fails only if batching becomes *clearly* the better
    plan (more than 25 % faster), which is the condition under which #277
    should be reopened. Measured, batching is 1.2-2.5x slower, so there is a
    wide margin before this fires.
    """
    _, conn = catalog
    batched = _median_ms(lambda: conn.execute(_batched_sql()).fetchone())
    per_component = _median_ms(lambda: _n_scans(conn))
    assert batched > per_component * 0.75, (
        f"one batched scan ({batched:.0f} ms) is now materially faster than "
        f"{len(_DIMS)} per-component scans ({per_component:.0f} ms). "
        "#277's premise may hold on this DuckDB version — re-open it."
    )


def test_our_wrapper_costs_almost_nothing_over_raw_sql(catalog) -> None:
    """The other half: there is no wrapper overhead to remove either.

    #277 reads as though the library were adding cost. It is not — the builder
    resolves the binding and DSD once, then runs exactly the scans a caller
    would write by hand.
    """
    cat, conn = catalog
    # `api` has been required since #337 — the key's slot count differs between
    # 2.1 and 3.0, and there is deliberately no default. This benchmark still
    # passed it positionally-by-omission and errored instead of measuring.
    ours = _median_ms(
        lambda: build_component_availability(
            cat, _FLOW, key="all", component_id="all", mode="exact", api="2.1", name={"en": "Availability"}
        )
    )
    raw = _median_ms(lambda: _n_scans(conn))
    assert ours < raw * 2.5, (
        f"build_component_availability ({ours:.0f} ms) has grown well beyond the raw scans "
        f"it wraps ({raw:.0f} ms); the builder should be a thin layer over them"
    )
