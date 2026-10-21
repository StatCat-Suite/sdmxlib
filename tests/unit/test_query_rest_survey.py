"""Would a REST backend serve the questions people actually ask? (§10)

The measurement taken before building an executor, kept as a test so the
answer cannot rot. The corpus is #276's inventory of the 76 hand-written
sites — spine lookups, the `dsd_component` fan-out, concept-driven discovery,
codes, annotations, reference metadata — expressed as `sl.q` queries and run
through the planner against `SDMX_REST`. No HTTP and no executor: the
capability declaration plus the planner is the whole apparatus.

Kept rather than discarded for two reasons. It pins the **verdict per shape**,
so a change that quietly makes the headline query unservable fails here rather
than in a design review months later. And writing it found a real defect —
`shapes_of` flattened a `Quantified`'s parts into the outer shape set, mixing
root-relative and child-relative paths — which no unit test could see, because
nothing else did path analysis over a shape set.

The one shape worth knowing by name is `dataflows whose DSD uses concept X`:
#276's headline, N+1 requests evaluated naively, two under the §9.1 rewrite,
and `REFUSED` unless the DSD's agency is stated — because a dataflow's agency
does not imply its DSD's.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from sdmxlib.query._expr import AnyCol, Cols, Pred, RowKind
from sdmxlib.query._plan import Plan, scanned
from sdmxlib.query._pushdown import DUCKDB, SDMX_REST, PushdownError, plan_program, root_key, shapes_of
from sdmxlib.query.accessors import q

pytestmark = pytest.mark.unit


def classify[R: RowKind](plan: Plan[R]) -> str:
    """What the planner does with this shape, in one word.

    Calls the real planner. An earlier cut of this file carried its own
    prototype of the rewrite search — that was how the rewrite got designed,
    and keeping it once `_pushdown` grew the real one would have been two
    implementations free to drift, with the test agreeing with itself while
    the library did something else.
    """
    try:
        program = plan_program(plan, SDMX_REST)
    except PushdownError:
        return "REFUSED"
    if len(program.pushes) > 1:
        return "REWRITE"
    return "PUSHED" if not program.residuals else "BOUNDED-LOCAL"


def _case[R: RowKind, A](name: str, root: Cols[R, A], predicates: list[Pred[R]]) -> tuple[str, Callable[[], str]]:
    """One corpus shape: its name, and how to get the planner's verdict on it.

    The verdict is deferred into a closure so that each shape is typed where
    its root and predicates meet — the corpus mixes row kinds, so a list of
    bare `(root, predicates)` pairs could only be typed by erasing them.
    """
    return name, lambda: classify(Plan(root=scanned(root), predicates=tuple(predicates)))


CORPUS: list[tuple[str, Callable[[], str]]] = [
    # ── spine questions: the bulk of the 76 (#276) ────────────────────────
    _case("one dataflow by agency+id", q.dataflow, [q.dataflow.agency == "ESTAT", q.dataflow.id == "CPI"]),
    _case("an agency's dataflows", q.dataflow, [q.dataflow.agency == "ESTAT"]),
    _case("an agency's codelists", q.codelist, [q.codelist.agency == "ESTAT"]),
    _case("every dataflow, unfiltered", q.dataflow, []),
    _case("dataflows by name search", q.dataflow, [q.dataflow.name["en"].str.contains("price")]),
    _case(
        "an agency's dataflows by name",
        q.dataflow,
        [q.dataflow.agency == "ESTAT", q.dataflow.name["en"].str.contains("price")],
    ),
    _case("dataflows by version", q.dataflow, [q.dataflow.agency == "ESTAT", q.dataflow.version == "1.0"]),
    _case("final dataflows only", q.dataflow, [q.dataflow.agency == "ESTAT", q.dataflow.is_final == True]),  # noqa: E712
    # ── the fan-out shapes: dsd_component, 21 sites ───────────────────────
    _case("dataflows whose DSD is X", q.dataflow, [q.dataflow.dsd.id == "CPI_DSD"]),
    _case(
        "dataflows whose DSD uses concept REF_AREA",
        q.dataflow,
        [q.dataflow.dsd.dimensions.any(q.component.concept.id == "REF_AREA")],
    ),
    _case(
        "  ... same, with the DSD's agency stated",
        q.dataflow,
        [
            q.dataflow.dsd.agency == "ESTAT",
            q.dataflow.dsd.dimensions.any(q.component.concept.id == "REF_AREA"),
        ],
    ),
    _case(
        "  ... and the dataflow's own agency",
        q.dataflow,
        [
            q.dataflow.agency == "ESTAT",
            q.dataflow.dsd.agency == "ESTAT",
            q.dataflow.dsd.dimensions.any(q.component.concept.id == "REF_AREA"),
        ],
    ),
    _case("dimensions of a DSD", q.component, [q.component.dsd.id == "CPI_DSD"]),
    _case("components using a codelist", q.component, [q.component.codelist.id == "CL_FREQ"]),
    # ── concept-driven discovery: 8 sites ─────────────────────────────────
    _case("concepts of a scheme", q.concept, [q.concept.scheme.id == "CS"]),
    _case("concepts by name", q.concept, [q.concept.name["en"].str.contains("area")]),
    # ── codes ─────────────────────────────────────────────────────────────
    _case("codes of a codelist", q.code, [q.code.codelist.id == "CL_FREQ"]),
    _case(
        "codes of an agency's codelist",
        q.code,
        [q.code.codelist.agency == "ESTAT", q.code.codelist.id == "CL_FREQ"],
    ),
    _case("codes by label", q.code, [q.code.name["en"].str.contains("annual")]),
    # ── the REST-native spelling of the item-rooted questions ─────────────
    # Same information, rooted at the owner. This is what a caller writes once
    # they know REST returns items inside their artefact — the codes arrive
    # with the codelist, the components with the DSD (§11's prefetch).
    _case(
        "that codelist, rooted at the owner",
        q.codelist,
        [q.codelist.agency == "ESTAT", q.codelist.id == "CL_FREQ"],
    ),
    _case(
        "that DSD, rooted at the owner",
        q.dsd,
        [q.dsd.agency == "ESTAT", q.dsd.id == "CPI_DSD"],
    ),
    # ── annotations / labels: 12 sites ────────────────────────────────────
    _case("dataflows with an annotation type", q.dataflow, [q.dataflow.annotations.any(q.annotation.type == "NOTE")]),
    # ── reference metadata: 13 sites ──────────────────────────────────────
    _case("metadata sets for a flow", q.metadata_set, [q.metadata_set.agency == "ESTAT"]),
]


EXPECTED = {
    # ── spine questions: the bulk of the 76 ───────────────────────────────
    "one dataflow by agency+id": "PUSHED",
    "an agency's dataflows": "PUSHED",
    "an agency's codelists": "PUSHED",
    # Allowed deliberately: a caller asking for every dataflow said so, and
    # the cost is visible in the query they wrote. See §5's amendment.
    "every dataflow, unfiltered": "PUSHED",
    "dataflows by name search": "REFUSED",
    "an agency's dataflows by name": "BOUNDED-LOCAL",
    "dataflows by version": "PUSHED",
    "final dataflows only": "BOUNDED-LOCAL",
    # ── the fan-out shapes ────────────────────────────────────────────────
    # No agency anywhere, so neither scan is bounded — correctly refused.
    "dataflows whose DSD is X": "REFUSED",
    "dataflows whose DSD uses concept REF_AREA": "REFUSED",
    # Bounds the *DSD* scan and not the dataflow scan. §5's bound is per
    # scan, so this is still refused — which is the finding that the headline
    # query needs two agency filters, one per scan, not one.
    "  ... same, with the DSD's agency stated": "REFUSED",
    "  ... and the dataflow's own agency": "REWRITE",
    # ── item-rooted: refused, and now for the right reason ────────────────
    # REST has no endpoint for "all codes" or "all components" — items arrive
    # inside their parent. `Capability.scans` says so, so these now refuse
    # with "no endpoint for that relation; root at the owner" instead of the
    # earlier "add an agency", which was a fix that could not have helped.
    #
    # Closing that gap did **not** move the count, and the prediction that it
    # would was wrong. Serving an item-rooted query needs a third thing
    # neither `scans` nor `bounding` supplies: scan the owner and *explode*
    # its items into rows. That is a new step kind, not a declaration.
    "dimensions of a DSD": "REFUSED",
    "components using a codelist": "REFUSED",
    "concepts of a scheme": "REFUSED",
    "concepts by name": "REFUSED",
    "codes of a codelist": "REFUSED",
    "codes of an agency's codelist": "REFUSED",
    "codes by label": "REFUSED",
    # The same two questions, spelled the way REST can answer them.
    "that codelist, rooted at the owner": "PUSHED",
    "that DSD, rooted at the owner": "PUSHED",
    # ── annotations, reference metadata ───────────────────────────────────
    "dataflows with an annotation type": "REFUSED",
    "metadata sets for a flow": "PUSHED",
}


@pytest.mark.parametrize(("name", "verdict"), CORPUS, ids=[case[0] for case in CORPUS])
def test_each_shape_gets_the_verdict_the_survey_recorded(name: str, verdict: Callable[[], str]) -> None:
    """Per shape rather than in aggregate: a tally can stay at 13 while the
    thirteen change identity, and which shapes are servable is the finding."""
    assert verdict() == EXPECTED[name]


def test_how_many_shapes_are_servable() -> None:
    """The headline number, pinned rather than aspired to.

    It read 75 % against a prototype planner that checked only the *inner*
    scan of a rewrite. The real planner checks every scan, per §5, and the
    figure is 11 of 23. What stopped being servable is every query with no
    agency on one of its scans, which genuinely cannot be answered without
    fetching a registry.

    A floor rather than an equality, so adding a corpus entry does not fail
    the build for arithmetic reasons. A *drop* does, which is the direction
    that matters.
    """
    verdicts = [verdict() for _, verdict in CORPUS]
    servable = sum(v != "REFUSED" for v in verdicts)
    assert servable >= 11, dict(zip([c[0] for c in CORPUS], verdicts, strict=True))


def test_an_item_root_is_refused_with_a_fix_that_could_work() -> None:
    """The `scans` gap, closed — and it changed the message, not the count.

    Before, `q.code` looked like a scan that merely happened to be unbounded,
    so the refusal said "add an agency". Adding one would not have helped:
    there is no code endpoint at any agency. A refusal naming an impossible
    fix is worse than no refusal, because it sends the caller somewhere.
    """
    with pytest.raises(PushdownError, match="no endpoint for that relation"):
        plan_program(Plan(root=scanned(q.code), predicates=(q.code.codelist.agency == "ESTAT",)), SDMX_REST)


def test_a_maintainable_root_is_scannable_and_an_item_root_is_not() -> None:
    """`root_key` splits the two, and the split is the REST resource list.

    A maintainable's `__constraint__` carries its artefact type, which *is*
    the resource in `/structure/{resource}/…`. An item relation has no
    constraint and no resource, and the table name standing in is what lets a
    declaration say "I have no endpoint for these".
    """
    assert root_key(q.dataflow) == "Dataflow"
    assert root_key(q.code) == "code"
    assert root_key(q.dataflow) in SDMX_REST.scans
    assert root_key(q.code) not in SDMX_REST.scans
    # Derived from the accessors, so a new artefact type gains an endpoint
    # without anyone remembering to list it.
    assert {"Codelist", "DataStructure", "ConceptScheme", "Metadataflow"} <= SDMX_REST.scans


def test_the_bound_is_per_root() -> None:
    """One flat set could only ever mean "agency on the root" (§10).

    DuckDB is exempt by `total`; a root the declaration does not name is
    bounded by nothing rather than vacuously bounded.
    """
    assert SDMX_REST.bounding[root_key(q.dataflow)]
    assert not SDMX_REST.bounding.get(root_key(q.code))


def test_the_headline_query_costs_two_requests_not_n_plus_one() -> None:
    """#276's most common question, and the one with no expression today.

    Naively it is one request per dataflow to resolve its DSD — 801 round
    trips at 800 dataflows. The rewrite makes it two, independent of N. That
    is the payoff the rest of this design exists to unlock.
    """
    predicates = [
        # *Two* agency filters, one per scan. §5's bound is per scan, so the
        # dataflow scan needs its own — see the module docstring.
        q.dataflow.agency == "ESTAT",
        q.dataflow.dsd.agency == "ESTAT",
        q.dataflow.dsd.dimensions.any(q.component.concept.id == "REF_AREA"),
    ]
    program = plan_program(Plan(root=scanned(q.dataflow), predicates=tuple(predicates)), SDMX_REST)
    # Two requests, and *two* is the claim — one per scan, independent of how
    # many dataflows or DSDs exist.
    assert len(program.pushes) == 2
    scans = [type(push.plan.root).__name__ for push in program.pushes]
    assert scans == ["_Dsd", "_Dataflow"], scans
    # One inner scan for both dsd predicates, not one per predicate: the
    # agency filter is what bounds the scan the quantifier filters, and
    # neither is plannable alone.
    assert len(program.pushes[0].plan.predicates) == 1
    (membership,) = [step.member_of for step in program.residuals if step.member_of]
    assert membership.column.column == "dsd_urn"


def test_the_refusal_names_the_missing_agency() -> None:
    """A refusal a caller cannot act on is just a failure."""
    with pytest.raises(PushdownError, match="agency_id"):
        plan_program(
            Plan(root=scanned(q.dataflow), predicates=(q.dataflow.name["en"].str.contains("price"),)), SDMX_REST
        )


# ── projection (§11) ──────────────────────────────────────────────────────
#
# §9.4 guessed that `select()` / `group_by()` would be unavailable over a REST
# root, because the endpoint returns artefacts rather than rows. The recorded
# ECB responses say otherwise, and these pin the corrected answer.

PROJECTIONS: list[tuple[str, list[AnyCol], bool]] = [
    ("id and name — on the artefact itself", [q.dataflow.id, q.dataflow.name["en"]], True),
    ("the DSD's id — one hop, prefetchable", [q.dataflow.id, q.dataflow.dsd.id], True),
    ("group by the DSD's agency", [q.dataflow.dsd.agency], True),
    # No `references=` value brings a DSD's components back *as a relation to
    # project from* on a dataflow scan; the prefix is not declared, so the
    # projection is unavailable rather than quietly expensive.
    ("a component's concept — two hops", [q.dataflow.dsd.dimensions.concept.id], False),
]


@pytest.mark.parametrize(("name", "columns", "available"), PROJECTIONS, ids=[c[0] for c in PROJECTIONS])
def test_a_projection_is_available_when_one_request_supplies_it(
    name: str, columns: list[AnyCol], available: bool
) -> None:
    """Projection never pushes — no registry computes `count(*)` for us — so
    the question is whether one request carries the values, not who does the
    arithmetic."""
    assert SDMX_REST.can_project({tuple(column.path) for column in columns}) is available


def test_projecting_across_an_edge_is_one_request_not_n_plus_one() -> None:
    """The measurement behind §11, stated as the claim it supports.

    `/dataflow/ECB?detail=full` is 38.6 KB and 102 dataflows with no
    structures; adding `references=datastructure` returns those 102 *and* the
    81 DSDs they reference, in one response. The alternative is 82 requests.
    So a projection naming `dsd.id` costs one request, and `Select` needs no
    plan of its own — it needs to contribute its prefixes to the fetch.
    """
    assert ("dataflow", "dsd") in SDMX_REST.prefetch
    assert SDMX_REST.can_project({(), ("dataflow", "dsd")})


def test_duckdb_projects_anything_without_declaring_prefetch() -> None:
    """It has joins; `references=` is a REST notion and does not apply."""
    assert DUCKDB.prefetch == frozenset()
    assert DUCKDB.can_project({("dataflow", "dsd", "dimensions", "concept")})


def test_prefetch_is_not_pushes() -> None:
    """A prefetched prefix yields *values* and still cannot be *filtered* on.

    Collapsing the two would let the planner believe a `references=` fetch had
    narrowed the result set, which it never does — the filter is still ours.
    """
    crossed = shapes_of(q.dataflow.dsd.name["en"].str.contains("price"))
    assert SDMX_REST.can_project({("dataflow", "dsd")})
    assert not SDMX_REST.accepts(("dataflow", "dsd"), crossed)
