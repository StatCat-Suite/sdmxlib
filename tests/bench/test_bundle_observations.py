"""The data plane, against a real bundle (#391).

%19 profiled 1,140,560,738 observations and fixed two unconditional clauses.
Both fixes are guarded by unit tests on **SQL text**, which is right — the
defects were clauses, and a clause is what a test can read — but every number
in !453 and !454 came from a throwaway script. Nothing in the repo could
reproduce them, and nothing would notice a later change putting either clause
back on a path the synthetic fixture does not cover.

That fixture is the gap. `tests/bench/fixtures.py` builds three columns of a
few thousand rows in Python, and had **no sidecar at all** until #332 — while
#388 was found on a binding whose sidecar is a separate DuckLake table. The
ratios that motivated the work (2600x, 5.4x) do not exist at that size.

## What is asserted, and what is not

**Clauses, not the clock**, for the same reason the rest of this suite counts:
`"JOIN" not in sql` holds on any machine. What the bundle adds is that it holds
against a *real binding* — a real sidecar, a real partition column, a
`DataflowBinding` read from storage rather than constructed in the test.

Two wall assertions are the exception `tests/bench/test_hierarchy_set_ops.py`
argues for and `test_collect_stays_close_to_frame` now uses: a clause that
comes back is invisible in the result and shows only in the clock. They are
ratios against the same query without the clause, so they say "this costs what
it should" rather than "this laptop is fast". Measured on the census bundle:

| | with the clause | without | ratio | bound |
| --- | --- | --- | --- | --- |
| #388 sidecar join, `count()` over 1.14 B | 4306 ms | **1.1 ms** | 3814x | 5x |
| #389 key order, `collect()` of 4.9 M x 2 | 438 ms | **99 ms** | 4.4x | 2x |

## Which partition, and why not the biggest

`discover_observations` picks the largest partition **under**
`MAX_BENCH_PARTITION_ROWS`. Taking the biggest was the first cut and was
wrong: the census bundle's is `DisseminationArea` at 842,209,475 rows, and
materialising it a few times took this tier from two minutes to **35**. The
bound lands on `FederalElectoralDistrict` — 4,915,280 rows, close to the
4.26 M partition %19 measured on, and the whole tier runs in ~2 min.

## The bundle, read in place

`data_plane_bundle` is the published bundle `$SDMXLIB_BENCH_BUNDLE` names,
which `bundle()` opens read-only, so this tier exercises the public
constructor #387 fixed, as any consumer of a bundle does.
"""

import time
from collections.abc import Callable

import pytest

from sdmxlib.model.urn import SdmxUrn
from sdmxlib.query._compile import CompiledQuery
from sdmxlib.session import cursor_for
from tests.bench import _bundle

pytestmark = pytest.mark.bundle


def _timed(fn):
    start = time.perf_counter()
    fn()
    return time.perf_counter() - start


def _best(fn, rounds: int = 3) -> float:
    fn()
    return min(_timed(fn) for _ in range(rounds))


@pytest.fixture
def observations(data_plane_registry, obs_shapes):
    """The bound dataflow's observations, as a query.

    `SdmxUrn.parse` rather than the string: `obs()` denies `str` in
    its signature and fails with `'str' object has no attribute 'sdmx_package'`
    when handed one, which is the shape #370 is about.
    """
    return data_plane_registry.obs(SdmxUrn.parse(obs_shapes.dataflow_urn))


@pytest.fixture
def partitioned(observations, obs_shapes):
    """One partition of it — the shape both %19 fixes were measured on."""
    if obs_shapes.partition is None or obs_shapes.big_partition is None:
        pytest.skip("this bundle's binding declares no partition column")
    component = observations.plan.root.binding.component_of(obs_shapes.partition)
    return observations.filter(observations.col(component) == obs_shapes.big_partition)


# ── #388: the sidecar join is emitted only where something reads it ───────


def test_a_count_over_the_whole_flow_plans_no_join(observations, obs_shapes):
    """The clause #388 removed, on the binding it was found on.

    The unit test for this builds its sidecar in the same `:memory:` DuckDB.
    Here the sidecar is a separate DuckLake table reached across an ATTACH,
    which is where the 2460x came from: 2598 ms against 1 ms for a count over
    1.14 B rows, because the join scales with rows scanned and a count is
    supposed never to touch them.
    """
    if not obs_shapes.sidecar:
        pytest.skip("this bundle's binding has no series-attributes sidecar — #388's shape is absent")
    assert "JOIN" not in observations.to_sql(columns="count(*)", ordered=False).sql


def test_a_count_filtered_on_a_sidecar_column_keeps_the_join(observations, obs_shapes):
    """The other half, which is what makes the first assertion mean something.

    Pruning driven by the *terminal* rather than by what is referenced would
    pass the test above and silently drop this join, returning a count of every
    row in the flow.
    """
    if not obs_shapes.sidecar or obs_shapes.sidecar_component is None:
        pytest.skip("this bundle's binding has no non-key sidecar column to filter on")
    filtered = observations.filter(observations.col(obs_shapes.sidecar_component).is_not_null())
    assert "JOIN" in filtered.to_sql(columns="count(*)", ordered=False).sql


def test_pruning_the_join_is_what_makes_a_whole_flow_count_affordable(observations, obs_shapes):
    """One of two wall assertions, and a ratio rather than a ceiling.

    A join that comes back is invisible in the answer — the count is identical
    — so this is the exception the suite makes for a cost that shows only in
    the clock. Measured against the *same count with the join*, so it says the
    clause costs what it should rather than anything about the machine.
    """
    if not obs_shapes.sidecar or obs_shapes.sidecar_component is None:
        pytest.skip("no sidecar to prune")
    joined = observations.filter(observations.col(obs_shapes.sidecar_component).is_not_null())
    pruned_s = _best(observations.count)
    joined_s = _best(joined.count)
    assert pruned_s * 5 < joined_s, (
        f"pruning the sidecar join saved only {joined_s / pruned_s:.1f}x "
        f"({pruned_s:.3f}s pruned vs {joined_s:.3f}s joined) — on the census bundle it is ~2600x"
    )


# ── #389: the key order is emitted unless declined ────────────────────────


def test_the_default_still_orders(partitioned, observations):
    """The guard #389's own acceptance asks for by name: a default quietly
    becoming unordered is a silent change to what every caller receives."""
    projected = partitioned.select(observations.col(observations.components[0]))
    assert "ORDER BY" in projected.to_sql().sql


def test_order_by_none_drops_it(partitioned, observations):
    """`order_by(None)` on a real binding, whose key is every dimension the DSD
    declares rather than the two a synthetic fixture has."""
    projected = partitioned.order_by(None).select(observations.col(observations.components[0]))
    assert "ORDER BY" not in projected.to_sql().sql


def test_declining_the_order_returns_the_same_rows(partitioned):
    """The ordering is a permutation. Asserted because the fast path is the one
    a regression would take, and a count is cheap next to the frame."""
    assert partitioned.order_by(None).count() == partitioned.count()


def test_the_key_order_is_what_a_large_frame_pays_for(partitioned, observations, data_plane_registry):
    """The second wall assertion, and the reason #389 is not cosmetic.

    A ratio again, against the same projection with the clause declined — and
    timed on the SQL alone, because the clause is what it is about. `collect`
    adds a typing pass that costs the same either way: on DuckDB 1.5 the clause
    still dominated it (337 ms against 75 ms on the 4.26 M-row `CensusDivision`
    partition), but 2.0 sorts so much faster that the shared pass diluted the
    whole-`collect` ratio to 1.7x (321 ms against 185 ms) while the SQL alone
    stayed at 14x (147 ms against 10.5 ms, on 4.9 M-row
    `FederalElectoralDistrict`).
    """
    columns = [observations.col(c) for c in observations.components[:2]]
    handle = cursor_for(data_plane_registry.connection)

    def run(compiled: CompiledQuery) -> Callable[[], object]:
        return lambda: handle.sql(compiled.sql, params=list(compiled.params)).to_arrow_table()

    ordered_s = _best(run(partitioned.select(*columns).to_sql()))
    unordered_s = _best(run(partitioned.order_by(None).select(*columns).to_sql()))
    assert unordered_s * 2 < ordered_s, (
        f"declining the key order saved only {ordered_s / unordered_s:.1f}x "
        f"({unordered_s:.3f}s vs {ordered_s:.3f}s) — on the census bundle it is ~14x"
    )


# ── the bundle really is the thing the unit tests cannot be ───────────────


def test_the_binding_has_the_shape_these_benchmarks_need(obs_shapes):
    """A precondition, stated out loud rather than left to make the assertions
    above vacuous.

    `_bundle.discover_observations` asks the bundle instead of naming
    `DF_CP_2021`, so this reports what it found — and a bundle whose binding
    has no sidecar and no partitions skips the tier rather than passing it.
    """
    assert obs_shapes.dataflow_urn, "a bound dataflow"
    assert obs_shapes.big_partition_rows <= _bundle.MAX_BENCH_PARTITION_ROWS, (
        f"the benchmarks would materialise {obs_shapes.big_partition_rows:,} rows — "
        f"`discover_observations` is meant to pick the largest partition under "
        f"{_bundle.MAX_BENCH_PARTITION_ROWS:,}. Choosing the biggest instead took this "
        f"tier from two minutes to 35."
    )
    if not obs_shapes.sidecar and obs_shapes.partition is None:
        pytest.skip(
            "this bundle's binding has neither a sidecar nor a partition column, so neither %19 fix is observable on it"
        )
