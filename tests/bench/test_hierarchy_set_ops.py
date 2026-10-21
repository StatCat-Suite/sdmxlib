"""The four hierarchy shapes a census consumer actually hits (#348).

Filed against an older release, where these were 1.38 s, 0.39 s, ~40 s and
**~220 s** on a 266,966-node hierarchy — because the API materialised every
row as a `HierarchicalCode` for callers that only ever read `.id`. Re-measured
on a comparable synthetic hierarchy they are 48 ms, 52 ms, 51 ms and 233 ms.

These are guards, not measurements for their own sake. Each win comes from the
work staying in SQL, and each is one refactor away from silently going back:
an `.ids()` that grew an object build, or an `ancestor_at` that stopped
pushing down, would still return the right answer. So the assertions are on
**wall-clock ceilings**, which `docs/design/caching.md` normally argues against
— the exception it names is exactly this, a regression that is invisible in the
result and only shows in time.

The ceilings are deliberately loose — roughly 20x the measured value — so they
fire on *a change of algorithm*, not on a slow CI box. The number that matters
is the ratio to the 220 s report, not the millisecond.
"""

from __future__ import annotations

import time

import pytest

import sdmxlib as sl
from sdmxlib.model.hierarchy import Level
from tests.support.factories import code_urn

pytestmark = pytest.mark.bench

_PR = Level(id="PR", name={"en": "Province"})
_CMA = Level(id="CMA", name={"en": "Census metropolitan area"})
_DA = Level(id="DA", name={"en": "Dissemination area"})

#: Sized to the report's `H_CP_GEO_POPCTR` (266,966 nodes). Big enough that a
#: per-row Python build is unmissable and small enough to persist in seconds.
_N_CMA, _PER_CMA = 264, 1000


def _hierarchy() -> sl.Hierarchy:
    provinces = []
    for p in range(4):
        cmas = [
            sl.HierarchicalCode(
                id=f"CMA{p}_{c}",
                code=code_urn("CL", f"CMA{p}_{c}"),
                level=_CMA,
                children=[
                    sl.HierarchicalCode(id=f"DA{p}_{c}_{i}", code=code_urn("CL", f"DA{p}_{c}_{i}"), level=_DA)
                    for i in range(_PER_CMA)
                ],
            )
            for c in range(_N_CMA // 4)
        ]
        provinces.append(sl.HierarchicalCode(id=f"PR{p}", code=code_urn("CL", f"PR{p}"), level=_PR, children=cmas))
    return sl.Hierarchy(
        id="H_GEO",
        maintainer="STC",
        version="1.0",
        name={"en": "Statistical geography"},
        has_formal_levels=True,
        levels=[_PR, _CMA, _DA],
        tree=[sl.HierarchicalCode(id="CAN", code=code_urn("CL", "CAN"), children=provinces)],
    )


@pytest.fixture(scope="module")
def lazy_hierarchy():
    """A `LazyHierarchy` over ~264k persisted nodes.

    Module-scoped: the persist dominates and is not what is being measured.
    """
    hierarchy = _hierarchy()
    with sl.LocalRegistry(":memory:") as registry:
        registry.add(hierarchy)
        yield registry.get(hierarchy.urn)


def _fastest_ms(work, runs: int = 3) -> float:
    """Best of `runs`, in milliseconds. Best, not mean: a slow run is noise
    from the machine, and the question is whether the *plan* changed."""
    timings = []
    for _ in range(runs):
        started = time.perf_counter()
        work()
        timings.append((time.perf_counter() - started) * 1000)
    return min(timings)


def test_descendants_of_a_subtree_stay_in_sql(lazy_hierarchy) -> None:
    """Reported 1.38 s; ~48 ms once `.ids()` stopped building nodes."""
    elapsed = _fastest_ms(lambda: lazy_hierarchy.narrower_transitive("CMA0_0").ids())
    assert elapsed < 1000, f"descendants took {elapsed:.0f} ms — #348 measured 48 ms, and 1.38 s before the fix"


def test_counting_a_subtree_builds_nothing(lazy_hierarchy) -> None:
    """`.count()` must be a `COUNT(*)`. If it ever collects first, this is the
    only place that would notice — the answer is identical either way."""
    subtree = lazy_hierarchy.narrower_transitive("CMA0_0")
    assert subtree.count() == _PER_CMA
    assert _fastest_ms(subtree.count) < 1000


@pytest.mark.parametrize("other", ["PR0", "PR1"], ids=["overlapping", "disjoint"])
def test_intersecting_two_subtrees_stays_in_sql(lazy_hierarchy, other: str) -> None:
    """The disjoint case is the one that was ~40 s: an empty result cost the
    most, because both sides were materialised before anything was compared."""
    left = lazy_hierarchy.narrower_transitive("CMA0_0")
    elapsed = _fastest_ms(lambda: left.intersect(lazy_hierarchy.narrower_transitive(other)).count())
    assert elapsed < 1000, f"intersect took {elapsed:.0f} ms — #348 measured 52 ms, and up to 40 s before"


def test_the_ancestor_at_level_map_is_one_query(lazy_hierarchy) -> None:
    """**The 220-second row.** A whole-hierarchy node → ancestor-at-Level map,
    which the consumer had to assemble by iterating every node.

    Also asserts the map is *complete*: a pushed-down version that quietly
    dropped rows would be fast and wrong, and fast-and-wrong is the failure
    mode a timing ceiling cannot see on its own.
    """
    rollup = lazy_hierarchy.ancestor_at("CMA")
    elapsed = _fastest_ms(rollup.to_dict)

    mapped = rollup.to_dict()
    assert len(mapped) == _N_CMA * _PER_CMA + _N_CMA, "every DA and every CMA maps to a CMA"
    assert mapped["DA0_0_0"] == "CMA0_0"
    assert mapped["CMA0_0"] == "CMA0_0", "a node at the level maps to itself"
    assert "CAN" not in mapped, "a node above the level is absent, not mapped to null"
    assert elapsed < 5000, f"the rollup took {elapsed:.0f} ms — #348 measured 233 ms, and ~220 s before"


def test_the_rollup_can_be_had_as_a_frame(lazy_hierarchy) -> None:
    """#348's memory point: rows that stay in the engine are not Python heap,
    so they sit inside DuckDB's `memory_limit` rather than beside it."""
    frame = lazy_hierarchy.ancestor_at("CMA").collect()
    assert frame.height == _N_CMA * _PER_CMA + _N_CMA
    assert frame.columns == ["node_id", "rollup_id"]
