"""Read-path benchmarks against a real SDMX bundle (#379, milestone %18).

Every other benchmark in this suite runs on a fixture `fixtures.py` builds in
Python, capped at 10k items and carrying no annotations. Milestone %18 was
measured on a 2.6 GB bundle — 1,876,557 hierarchy nodes, 582,977 codes,
585,675 annotations — and none of its rows are reachable from a synthetic
fixture. This module is where those rows live.

Skipped unless a bundle is available; see `_bundle` for how one is found and
why a *wrong* path raises instead of skipping.

## What is asserted, and what is only measured

The wall benchmarks reproduce %18's table so a change of algorithm is visible
in `just bench-compare`. They assert nothing: on a 2.6 GB bundle a wall
ceiling measures the machine at least as much as the code.

The **assertions** are counts, per CLAUDE.md — the defects in %18 are all
*work done*, which is identical on a busy laptop and an idle one. Each was
written against known-broken code and watched to fail there first; a guard
that has only ever passed is not known to be a guard.

They carry `xfail(strict=True)` rather than being left red, following
`tests/unit/test_roundtrip_property_guard.py`. Strict is the whole mechanism:
when #377 or #378 lands, its assertion starts passing, pytest turns that XPASS
into a **failure**, and the marker has to come off in the same commit. A
red-until-fixed recipe would say the same thing once and then be ignored —
which is the failure mode this tier exists to prevent, wearing its other face.

| assertion | issue | measured on `main` |
| --- | --- | --- |
| a small hierarchy's eager read does not build its whole codelist | #378 | ~~579,951 `Code`s~~ — **fixed** |
| `collect()` builds no `Level` per row | #377 | ~~28 built, 5 declared~~ — **fixed** |
| `collect()` and `materialize()` agree on a node's level | #377 | ~~28 of 28 disagree~~ — **fixed** |
| `collect()` stays close to `frame()` | %18 criterion | +15.33, +35.00 calls/row |
| `frame()` builds nothing per row | #401 | 838, 843 calls total |

The `collect()` row has been corrected twice. It began as `within 5x of
ids()`, which was unreachable by construction rather than by being ambitious
(#382), and it then spent a while as a wall-clock ratio against `frame()` —
the one assertion in this file that was *not* a count. That flaked in the
gate: 22.7x against a 16x bound with 12.9x measured, green alone and green on
two further full-tier runs (#401). Calls per row say the same thing and came
back identical across three runs, so every assertion here is now work done,
with no exception left to argue for. The wall figures still exist, in
`test_query_codes` and `test_query_hierarchy_nodes`, which report rather than
assert.

A third cost — the empty `InternationalString` built per annotation, 52% of
`q.code…collect()` — has no assertion here yet. Its honest bound is
`2 * codes + annotations_carrying_text`, which is worth stating in the same
commit that makes it true rather than guessing at it now.
"""

import cProfile
import gc
import tracemalloc

import pytest

import sdmxlib as sl
from sdmxlib.local import LocalRegistry
from sdmxlib.model.codelist import Code
from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy, Level
from sdmxlib.query.accessors import q
from sdmxlib.storage.lazy import LazyHierarchy
from tests.bench import _bundle
from tests.bench._bundle import Shapes

pytestmark = pytest.mark.bundle


@pytest.fixture
def registry(bundle_conn):
    """A registry with the cross-call resolver cache off.

    `resolve_cache_items=0` because these benchmarks measure *a read*, and a
    session-scoped registry would serve the second test from the first test's
    cache. `docs/design/performance.md` §Layer 3 is a real and wanted
    optimisation; it just makes a benchmark of the layers beneath it
    meaningless.
    """
    return sl.LocalRegistry.from_connection(bundle_conn, resolve_cache_items=0)


def _bench_once(benchmark, fn):
    """Run *fn* under pytest-benchmark without the default calibration.

    These operations take seconds and allocate over a gigabyte. The default
    calibration would run them thousands of times looking for a stable
    microsecond, and the memory would not survive it.
    """
    return benchmark.pedantic(fn, rounds=3, iterations=1, warmup_rounds=0)


# ── the four shapes of %18's table ────────────────────────────────────────────


def test_eager_read_of_the_largest_codelist(benchmark, registry: LocalRegistry, shapes: Shapes):
    """%18: 4.00 s / +1.9 GB for 579,951 codes.

    48% `_collect_refs` (#376), 28% annotation builders (#377).
    """
    benchmark.extra_info["codes"] = shapes.codelist_codes
    result = _bench_once(benchmark, lambda: registry.get(shapes.codelist, eager=True))
    assert result is not None


def test_eager_read_of_a_small_hierarchy(benchmark, registry: LocalRegistry, shapes: Shapes):
    """%18: 3.32 s / +1.28 GB for a **28-node** hierarchy (#378).

    The size in `extra_info` is the point of the benchmark, not decoration:
    this hierarchy is three orders of magnitude smaller than the one below and
    costs a third as much, because both pay the same codelist.
    """
    benchmark.extra_info["nodes"] = shapes.small_hierarchy_nodes
    benchmark.extra_info["codes_in_referenced_codelist"] = shapes.codelist_codes
    result = _bench_once(benchmark, lambda: registry.get(shapes.small_hierarchy_over_codelist, eager=True))
    assert result is not None


def test_materialize_the_largest_hierarchy(benchmark, registry: LocalRegistry, shapes: Shapes):
    """%18: 3.19 s / +1.1 GB for 499,138 nodes (#154).

    Deliberately re-fetches the `LazyHierarchy` each round. `materialize()` is
    not memoised — the warm call measured identical to the cold one, which is
    #154's item 3 — so this would read the same either way today. It is
    written this way so that it still measures *materialisation* if that
    changes.
    """
    benchmark.extra_info["nodes"] = shapes.big_hierarchy_nodes

    def read() -> Hierarchy:
        lazy = registry.get(shapes.big_hierarchy)
        assert isinstance(lazy, LazyHierarchy)
        return lazy.materialize()

    result = _bench_once(benchmark, read)
    assert result is not None


@pytest.mark.parametrize("terminal", ["ids", "collect"])
def test_query_codes(benchmark, registry: LocalRegistry, shapes: Shapes, terminal):
    """%18: `.ids()` 96 ms vs `.collect()` 3.63 s over 579,951 codes.

    Parametrised rather than split so `just bench-compare` shows the pair
    adjacent — the ratio is the number that matters and it is the milestone's
    success criterion.
    """
    benchmark.extra_info["codes"] = shapes.codelist_codes
    plan = registry.query(q.code).filter(q.code.codelist.urn == str(shapes.codelist))
    result = _bench_once(benchmark, getattr(plan, terminal))
    assert len(result) == shapes.codelist_codes


@pytest.mark.parametrize("terminal", ["ids", "collect"])
def test_query_hierarchy_nodes(benchmark, registry: LocalRegistry, shapes: Shapes, terminal):
    """%18: `.ids()` 95 ms vs `.collect()` 4.41 s over 499,138 nodes."""
    benchmark.extra_info["nodes"] = shapes.big_hierarchy_nodes
    plan = registry.query(q.hcode).filter(q.hcode.hierarchy.urn == str(shapes.big_hierarchy))
    result = _bench_once(benchmark, getattr(plan, terminal))
    assert len(result) == shapes.big_hierarchy_nodes


# ── the assertions ────────────────────────────────────────────────────────────


def test_eager_hierarchy_read_does_not_build_its_whole_codelist(registry: LocalRegistry, shapes: Shapes):
    """An eager read costs its own size, not its codelist's (#378).

    On the 2021 Census Profile bundle a **28-node** hierarchy builds **579,951**
    `Code` objects, because resolving its nodes' `Ref[Code]` loads the parent
    scheme in full. `docs/design/performance.md` §Layer 2 is working as
    designed — the scheme is loaded once rather than once per ref (#72) — but
    once is still every code in it.

    The bound is the node count rather than a constant: a node may place a
    code, so one `Code` per node is the most an honest resolution needs. Some
    slack is allowed for codes reached as parents of those.

    Passed as of the #378 fix — an eager read now fetches the item URNs it
    needs rather than their whole parent scheme. Its `xfail` marker came off in
    that commit, which is what strict xfail is for.
    """
    bound = max(shapes.small_hierarchy_nodes * 2, 100)
    # A precondition, not an assertion. Under `xfail` a failed assert is an
    # *expected* failure, so a bundle lacking #378's shape would report a
    # green xfail while testing nothing. Skipping says so out loud instead.
    if shapes.codelist_codes <= bound:
        pytest.skip(
            f"this bundle's smallest hierarchy ({shapes.small_hierarchy_nodes} nodes) is not "
            f"small relative to its codelist ({shapes.codelist_codes} codes) — #378's shape is absent"
        )

    with _bundle.counting(Code) as built:
        registry.get(shapes.small_hierarchy_over_codelist, eager=True)

    assert built[0] <= bound, (
        f"eager read of a {shapes.small_hierarchy_nodes}-node hierarchy built {built[0]} Code "
        f"objects — the codelist it references has {shapes.codelist_codes} (#378)"
    )


def test_collect_builds_no_level_per_row(registry: LocalRegistry, shapes: Shapes):
    """`collect()` looks a level up; it does not rebuild one per row (#377).

    `storage/resolve.py` builds `Level(id=…)` for every `hierarchy_node` row it
    turns into a `HierarchicalCode` — 499,138 of them on the largest hierarchy
    here, 28% of the collect. `ee10df9` removed exactly this from
    `LazyHierarchy.materialize()`; the twin in the sibling builder was never
    touched.

    It is also **lossy**, which is the half a timing test would miss: the
    `Level` built here carries only an id, so the same node reached through
    `collect()` has a different level than through `materialize()`. That
    disagreement is asserted separately below.

    Run against the *small* hierarchy: the defect is per-row, so it shows at 28
    rows as clearly as at 499,138, and this way the guard costs milliseconds.

    Passed as of the #377 fix; its `xfail` marker came off in that commit,
    which is what strict xfail is for.
    """
    hierarchy = registry.get(shapes.small_hierarchy_over_codelist)
    assert isinstance(hierarchy, LazyHierarchy)
    declared = hierarchy.levels
    plan = registry.query(q.hcode).filter(q.hcode.hierarchy.urn == str(shapes.small_hierarchy_over_codelist))

    with _bundle.counting(Level) as built:
        nodes = plan.collect()

    if not nodes:
        pytest.skip("no nodes collected — the assertion below would be vacuous")
    assert built[0] <= len(declared), (
        f"collect() of {len(nodes)} nodes built {built[0]} Level objects; the hierarchy declares {len(declared)} (#377)"
    )


def test_collect_and_materialize_agree_on_a_nodes_level(registry: LocalRegistry, shapes: Shapes):
    """The same node has the same level by either route (#377).

    The cost half of `test_collect_builds_no_level_per_row` could be fixed by
    caching the wrong `Level`. This says what the object has to *be*: whatever
    `materialize()` gives for the same node.
    """
    hierarchy = registry.get(shapes.small_hierarchy_over_codelist)
    assert isinstance(hierarchy, LazyHierarchy)
    # `Hierarchy.codes` is the flat index over every node, not just the roots.
    materialised = {n.id: n for n in hierarchy.materialize().codes}
    collected = (
        registry.query(q.hcode).filter(q.hcode.hierarchy.urn == str(shapes.small_hierarchy_over_codelist)).collect()
    )

    if not collected:
        pytest.skip("no nodes collected — the assertion below would be vacuous")
    # `Query.collect()` is declared `list[object]` — the row type is erased at
    # the terminal — so the element type is asserted here rather than assumed.
    nodes = [node for node in collected if isinstance(node, HierarchicalCode)]
    assert len(nodes) == len(collected), "collect() over q.hcode yields HierarchicalCode"
    disagreements = [
        (node.id, node.level, materialised[node.id].level)
        for node in nodes
        if node.id in materialised and node.level != materialised[node.id].level
    ]
    assert not disagreements, (
        f"collect() and materialize() disagree on {len(disagreements)} nodes' level: {disagreements[:3]}"
    )


#: What one item of an eagerly-read artefact may cost, in the two units that
#: do not describe the laptop.
#:
#: %18 stated this as **1 s and 300 MB per single-artefact read**, and that was
#: unreachable by construction for the two artefacts it was aimed at. 300 MB
#: over `H_CP_GEO_FED`'s 499,138 nodes is 630 bytes for a `HierarchicalCode`
#: plus the `Code` it resolves, two `InternationalString`s, an `Annotations`,
#: an `ItemList` and a `Level`; measured is 1,909. And 1 s is 2.0 us/node
#: *including* a ref resolution, where `materialize()` — the same construction
#: with none — is 4.2 us/node, so it was missed by 2x at zero resolution cost.
#: A total that scales with the artefact is a statement about the bundle's
#: largest artefact, not about anything the library can fix; the per-item
#: figure is what every %18 change actually moved.
#:
#: **Calls, not seconds**, for the reason `d94ed3b` gives: an A/B on this
#: laptop disagreed in *direction* between hierarchies while another build ran.
#: Bytes are memory rather than time and do not have that problem.
#:
#: Measured on the census bundle after #376, #377, #378, #383, #384, #385 and the
#: parent-scheme-URN cache:
#:
#: | artefact | items | calls/item | bytes/item |
#: | --- | --- | --- | --- |
#: | `CL_GEOGRAPHY` | 579,951 | 29.1 | 731 |
#: | `H_CP_GEO_FED` | 499,138 | 120.2 | 1,909 |
#: | `H_CP_GEO_ADMINISTRATIVE` | 63,404 | 124.7 | 2,161 |
#:
#: A hierarchy node costs **4x** the calls and **2.8x** the bytes of a code,
#: because it pays for its own construction *and* for the `Code` its `Ref`
#: resolves to. The bounds bind there and leave the codelist far under, which
#: is the same shape #386's weigher has: bound where the cost is.
#:
#: #492 has the store check what it builds, as `add` does: `H_CP_GEO_FED` went
#: from 144.31 to 155.30 calls a node (+1.2% on the clock, A/B x5; RSS
#: unchanged). The rules walk the nodes twice; they cost eighteen calls a node
#: until a node's path was built only for a node with an issue to name.
#: Stage 2 of #492 took it to 158.31 (each `Ref` bound as it is built) and then
#: 159.30: a frozen node is built after its children, read deepest first as
#: `LazyHierarchy.materialize` reads them, one dict pop a node. The clock went
#: the other way, 6.187 s against 6.355 s (A/B x5): each node is built once,
#: rather than built and then given its children.
EAGER_CALLS_PER_ITEM = 160
EAGER_BYTES_PER_ITEM = 2_600


@pytest.mark.parametrize(
    ("shape_attr", "size_attr"),
    [
        ("codelist", "codelist_codes"),
        ("big_hierarchy", "big_hierarchy_nodes"),
    ],
)
def test_an_eager_read_stays_within_its_per_item_budget(registry: LocalRegistry, shapes: Shapes, shape_attr, size_attr):
    """%18's first success criterion, restated in units a fix can move.

    Two measurements of one read rather than one, because they fail for
    different reasons: calls catch a per-item build creeping back in, bytes
    catch an object growing a field nobody meant to retain half a million
    times.

    The read is done twice — once under `cProfile`, once under `tracemalloc` —
    since each instruments the other into uselessness. `tracemalloc` inflated
    an eager `H_CP_GEO_FED` from 7.97 s to 22.15 s while this was being
    written, which is exactly why neither number here is a clock.
    """
    urn = getattr(shapes, shape_attr)
    items = getattr(shapes, size_attr)
    if items < 1_000:
        pytest.skip(f"{shape_attr} has {items} items — a per-item figure over a handful is noise")

    profiler = cProfile.Profile()
    profiler.enable()
    try:
        read = registry.get(urn, eager=True)
    finally:
        profiler.disable()  # see `_calls`: a leaked profiler fails every later one
    # Summed off `getstats()` rather than read from `pstats.Stats(...)`: the
    # two are equal by construction — `get_top_level_stats` accumulates the
    # same per-entry call count — and `Stats` assigns `total_calls` in `init()`
    # rather than declaring it, so typeshed has no such attribute (#347).
    calls = sum(e.callcount for e in profiler.getstats())
    assert read is not None
    del read

    tracemalloc.start()
    try:
        gc.collect()
        before, _ = tracemalloc.get_traced_memory()
        held = registry.get(urn, eager=True)
        gc.collect()
        after, _ = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert held is not None
    del held

    per_call = calls / items
    per_byte = (after - before) / items
    assert per_call <= EAGER_CALLS_PER_ITEM, (
        f"{shape_attr}: {per_call:.1f} calls per item over {items:,} items, above the {EAGER_CALLS_PER_ITEM} bound"
    )
    assert per_byte <= EAGER_BYTES_PER_ITEM, (
        f"{shape_attr}: {per_byte:.0f} bytes retained per item over {items:,} items, "
        f"above the {EAGER_BYTES_PER_ITEM} bound"
    )


#: Per-row construction budgets: how many Python calls `collect()` makes that
#: `frame()` does not, divided by rows. Two per case, because the pair answers
#: two questions — what a caller pays for objects instead of a frame, and the
#: construction tax alone, since `annotations=False` leaves *one* query on each
#: side reading the same columns (#381).
#:
#: Per-accessor, unlike the wall-clock ratio these replaced, because the two
#: differ by more than any single bound could hold tightly: a `Code` is cheaper
#: to build than a `HierarchicalCode`, and `q.hcode`'s two figures coincide
#: because those nodes carry hardly any annotations while half of `q.code`'s
#: collect is the annotation read.
#:
#: Measured on the census bundle, identical across three runs — 15.33 / 26.14 and
#: 35.00 / 35.00. The bounds sat ~30 % above, which is wide enough to survive a
#: bundle whose rows differ and narrow enough that a regained per-row build
#: cannot hide: #377's `Level`-per-row was several calls a row on its own.
#:
#: `hcode` has since moved, and is recorded rather than re-derived: 43.00 / 43.24
#: on the %20 collecting branch, then **45.00 / 45.00** with #440 — exactly two
#: calls a row, `as_utc` on `valid_from` and `valid_to`, the attrs converter that
#: holds every model datetime tagged UTC. A converter runs on every construction,
#: `None` included, so the two are the cost of the rule rather than of this path
#: (which had also skipped `from_storage`, and returned naive values). Its bound
#: keeps the two-call margin it had, which still catches a regained per-row build.
#:
#: Both moved by **exactly one call a row** with #422's links — 17.14 / 33.34
#: and 46.02 / 46.02 — the `to_links` converter, which runs on every code and
#: node like `as_utc` and returns at once when there are none. It cost three
#: until the empty case returned before any call and the readers stopped
#: asking an empty map for each row. Every bound rose by the same one, so each
#: keeps the margin it had.
#:
#: `hcode` then fell to **35.02 / 35.02** with #419: a hierarchical code has no
#: name or description of its own, so the two text maps built for every node
#: went — eleven calls a row. Its bound came down with it, keeping its margin,
#: so the drop is held rather than spent.
#:
#: Both rose by **exactly two calls a row** with #492 — 19.14 / 35.33 and
#: 36.02 / 36.02 — the id check, now run as each code and node is built: the
#: field validator, and its one regex match on a valid id (the message is only
#: built for a bad one). It was four until the valid case returned straight
#: after the match. Every bound rose by the same two, as with #422.
#:
#: `code` then rose twice more, to **25.06 / 41.25**. The bundle itself first:
#: its 498,786 dissemination blocks were named ``""``, which #492's name check
#: refuses, and were named from their id in place, in ``en`` and ``fr`` like
#: the dissemination areas beside them — 20.00 / 36.19 on the code before, the
#: one `sys.intern` for each added language. Then the name check, as each code
#: is built: the field validator, the map and its values, and one regex match
#: per language on a plain name, five calls a row. It was thirteen as a
#: generator over three string checks. The `code` bounds rose by the same
#: 5.86; `hcode` did not move, since a hierarchical code has no name.
#: Read as ``(construction only, with the annotation read)`` — the same order
#: as the docstring table below, so the pair cannot be transposed by eye.
COLLECT_CALLS_PER_ROW = {
    "code": (28.86, 42.86),
    "hcode": (39.0, 39.0),
}

#: What `frame()` may spend in total, whatever the row count. It builds no
#: object per row, which is the entire reason it is the baseline above; 838 and
#: 843 calls for 579,951 and 499,143 rows is that property stated as a number.
FRAME_TOTAL_CALLS = 5_000


def _calls(fn):
    """`(calls, rows)` for one terminal, counted rather than timed.

    Summed off `getstats()` for the reason
    `test_an_eager_read_stays_within_its_per_item_budget` gives: `pstats.Stats`
    assigns `total_calls` in `init()` rather than declaring it, so typeshed has
    no such attribute (#347).

    Disabled in a ``finally``: on 3.14 a profiler holds a `sys.monitoring`
    tool slot, so one left enabled by a terminal that raised made every later
    profile fail with "tool 2 is already in use", burying the first error.
    """
    profiler = cProfile.Profile()
    profiler.enable()
    try:
        result = fn()
    finally:
        profiler.disable()
    return sum(e.callcount for e in profiler.getstats()), len(result)


@pytest.mark.parametrize(
    ("accessor", "shape_attr", "urn_path"),
    [
        ("code", "codelist", "codelist"),
        ("hcode", "big_hierarchy", "hierarchy"),
    ],
)
def test_collect_stays_close_to_frame(registry: LocalRegistry, shapes: Shapes, accessor, shape_attr, urn_path):
    """The object-construction tax, as calls per row against `frame()` (#382, #401).

    **Against `frame()` and not `ids()`, which was the first correction.** This
    stood as `collect() <= ids() * 5`, and %18 stated the same 5x as a success
    criterion. It was unreachable by construction rather than by being
    ambitious: `ids()` fetches one `VARCHAR` column while `collect()` fetches
    every one — two `MAP(VARCHAR, VARCHAR)` labels included — and then runs a
    second query for annotations. A `collect()` with a free `Code.__init__`
    would still have been an order of magnitude off, so the ratio measured how
    much *more* `collect()` reads, dressed up as what it costs to build objects
    from it. `frame()` reads the same columns, so the fetch sits on both sides
    and what is left is the building.

    **A count and not a clock, which was the second.** It stood as a wall-clock
    ratio, min-of-three, on the argument that a ratio is stable across machines
    while a ceiling on a 2.6 GB bundle is a statement about the laptop. True of
    a *slower* machine, false of contention within one run: the two sides are
    timed at different moments and the tier does two minutes of heavy DuckDB
    work around them, so a stall in one moves the ratio. It failed `just ci`
    once at 22.7x against a 16x bound with 12.9x measured, then passed alone
    and in two further full-tier runs (#401). A flaky assertion in a gate is
    worse than a missing one, because the answer to a red that goes green on a
    re-run is to stop reading it.

    Calls per row say the same thing and do not move: 15.33 and 35.00, byte
    for byte across three runs, on a machine doing anything else. That is the
    shape every other assertion in this file already has.

    Measured on the census bundle after #377, #380, #381 and #349:

    | | `frame()` | `collect()` | `collect(annotations=False)` |
    | --- | --- | --- | --- |
    | `q.code` 579,951 | 838 calls | +26.14/row | +15.33/row |
    | `q.hcode` 499,143 | 843 calls | +35.00/row | +35.00/row |

    The wall figures those replaced — 158 ms against 2372 ms and 1196 ms;
    176 ms against 2311 ms and 2264 ms — are not lost: `test_query_codes` and
    `test_query_hierarchy_nodes` still report them under `just bench-compare`,
    where a number that varies with the machine belongs.

    What a call count cannot see is work that gets slower without getting more
    numerous — a regex compiled per row inside one existing call. Those two
    benchmarks are where that would show, and they report rather than assert
    for the reason this docstring has just spent a paragraph on.
    """
    root = getattr(q, accessor)
    plan = registry.query(root).filter(getattr(root, urn_path).urn == str(getattr(shapes, shape_attr)))
    construction_only, with_annotations = COLLECT_CALLS_PER_ROW[accessor]

    frame_calls, _ = _calls(plan.frame)
    collect_calls, rows = _calls(plan.collect)
    built_calls, _ = _calls(lambda: plan.collect(annotations=False))

    assert rows, f"{accessor}: the filter matched no rows, so every budget below is vacuous"
    per_row = (collect_calls - frame_calls) / rows
    built_per_row = (built_calls - frame_calls) / rows

    assert per_row <= with_annotations, (
        f"{accessor}: collect() makes {per_row:.2f} calls per row that frame() does not "
        f"({collect_calls:,} against {frame_calls:,} over {rows:,} rows), above the "
        f"{with_annotations} bound"
    )
    assert built_per_row <= construction_only, (
        f"{accessor}: collect(annotations=False) makes {built_per_row:.2f} calls per row "
        f"that frame() does not ({built_calls:,} against {frame_calls:,} over {rows:,} rows), "
        f"above the {construction_only} bound — this one is construction alone, so a regression here "
        f"is a per-row build rather than a heavier read"
    )


@pytest.mark.parametrize(
    ("accessor", "shape_attr", "urn_path"),
    [
        ("code", "codelist", "codelist"),
        ("hcode", "big_hierarchy", "hierarchy"),
    ],
)
def test_frame_builds_nothing_per_row(registry: LocalRegistry, shapes: Shapes, accessor, shape_attr, urn_path):
    """What licenses `frame()` as the baseline above.

    The test above subtracts `frame()`'s calls from `collect()`'s and divides
    by rows, which is only a construction tax if `frame()` itself does no
    per-row work. It does not — 838 calls for 579,951 rows — but that is a
    property of the polars path rather than a guarantee, and if it ever
    regained one the subtraction would quietly start cancelling the very cost
    it is there to measure.

    An absolute bound rather than a per-row one, because the claim is exactly
    that the number does not scale: 5,000 is six times what it costs today and
    still a hundredfold below one call per row.
    """
    root = getattr(q, accessor)
    plan = registry.query(root).filter(getattr(root, urn_path).urn == str(getattr(shapes, shape_attr)))

    frame_calls, rows = _calls(plan.frame)

    assert rows > 100_000, f"{accessor}: {rows:,} rows is too few for this to mean anything"
    assert frame_calls <= FRAME_TOTAL_CALLS, (
        f"{accessor}: frame() made {frame_calls:,} calls over {rows:,} rows "
        f"({frame_calls / rows:.3f} per row), above the flat {FRAME_TOTAL_CALLS} bound — "
        f"it has regained per-row work, which makes it useless as the baseline in "
        f"test_collect_stays_close_to_frame"
    )
