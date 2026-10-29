"""A `Push` as an SDMX REST request — and the `references=` it must carry.

The URL assembly is the dull half and is asserted as whole strings, because a
segment in the wrong order is exactly the bug a field-by-field assertion reads
straight past.

The interesting half is `references=`. A scan must fetch enough to answer
everything evaluated against *its* rows, which is more than it pushes: the
predicates the backend could not take come back as local steps over the same
rows, and they reach the deepest paths precisely because they are the ones it
could not answer. Getting that wrong produces a request that looks reasonable,
returns 2 MB, and answers nothing.
"""

from __future__ import annotations

import pytest

from sdmxlib.query._compile import projection_paths
from sdmxlib.query._expr import Cols, Pred, RowKind
from sdmxlib.query._plan import Plan, scanned
from sdmxlib.query._pushdown import SDMX_REST, plan_program
from sdmxlib.query._rest import Request, requests_for
from sdmxlib.query.accessors import q

pytestmark = pytest.mark.unit


def _requests[R: RowKind, A](root: Cols[R, A], *predicates: Pred[R]) -> tuple[Request, ...]:
    plan = Plan(root=scanned(root), predicates=predicates)
    return requests_for(plan_program(plan, SDMX_REST))


def _urls(requests: tuple[Request, ...]) -> list[str]:
    return [f"/{r.path}?" + "&".join(f"{k}={v}" for k, v in r.params.items()) for r in requests]


# ── assembly ──────────────────────────────────────────────────────────────


def test_one_artefact_by_agency_and_id() -> None:
    assert _urls(_requests(q.dataflow, q.dataflow.agency == "ESTAT", q.dataflow.id == "CPI")) == [
        "/structure/dataflow/ESTAT/CPI/latest?detail=full"
    ]


def test_an_unconstrained_segment_is_all_and_an_unconstrained_version_is_latest() -> None:
    """The two wildcards are not interchangeable.

    `all` means every id; `latest` means the newest version rather than every
    version. Using `all` for the version would silently widen the result set —
    a query asking for a dataflow would get its whole history.
    """
    (request,) = _requests(q.codelist, q.codelist.agency == "ESTAT")
    assert request.resource_id == "all"
    assert request.version == "latest"


def test_the_resource_is_the_artefact_type_lowercased() -> None:
    (request,) = _requests(q.dsd, q.dsd.agency == "ESTAT")
    assert request.resource == "datastructure"


def test_references_none_is_omitted_rather_than_sent() -> None:
    """It is the wire default, and sending it makes every URL noisier to read
    in a cassette diff."""
    (request,) = _requests(q.dataflow, q.dataflow.agency == "ESTAT")
    assert "references" not in request.params


# ── the references value, which is where the risk is ──────────────────────


def test_a_scan_fetches_for_the_local_work_that_follows_it() -> None:
    """The bug this file exists to pin.

    The §9.1 sub-scan pushes `dsd.agency` and keeps the concept predicate
    local, so its *plan* mentions no crossing at all. Computing `references=`
    from the plan alone gave `none` — the fetch that returns 81 DSDs, resolves
    zero concept ids, and answers nothing with no error.
    """
    requests = _requests(
        q.dataflow,
        q.dataflow.agency == "ESTAT",
        q.dataflow.dsd.agency == "ESTAT",
        q.dataflow.dsd.dimensions.any(q.component.concept.id == "REF_AREA"),
    )
    assert _urls(requests) == [
        "/structure/datastructure/ESTAT/all/latest?detail=full&references=children",
        "/structure/dataflow/ESTAT/all/latest?detail=full",
    ]


def test_the_deep_paths_ride_on_the_inner_scan_not_the_outer_one() -> None:
    """Why the rewrite is cheaper and not merely different (§13).

    The inner scan carries `references=children` over the DSDs; the outer scan
    needs nothing at all. Without the rewrite the same closure would land on
    the *dataflow* scan, dragging every dataflow's subtree.
    """
    inner, outer = _requests(
        q.dataflow,
        q.dataflow.agency == "ESTAT",
        q.dataflow.dsd.agency == "ESTAT",
        q.dataflow.dsd.dimensions.any(q.component.concept.id == "REF_AREA"),
    )
    assert inner.resource == "datastructure"
    assert inner.references == "children"
    assert outer.resource == "dataflow"
    assert outer.references == "none"


def test_a_local_filter_on_the_root_needs_no_references() -> None:
    """A name search is evaluated locally but reaches nothing across an edge."""
    (request,) = _requests(q.dataflow, q.dataflow.agency == "ESTAT", q.dataflow.name["en"].str.contains("price"))
    assert request.references == "none"


# ── the wire-level bound ──────────────────────────────────────────────────


def test_every_request_a_program_issues_is_bounded() -> None:
    """§5 as it appears on the wire, and a second opinion rather than a
    substitute: the planner already refused the unbounded scans, so an
    unbounded request here means the two disagree.
    """
    for predicates in (
        (q.dataflow.agency == "ESTAT",),
        (q.dataflow.agency == "ESTAT", q.dataflow.name["en"].str.contains("price")),
        (
            q.dataflow.agency == "ESTAT",
            q.dataflow.dsd.agency == "ESTAT",
            q.dataflow.dsd.dimensions.any(q.component.concept.id == "REF_AREA"),
        ),
    ):
        for request in _requests(q.dataflow, *predicates):
            assert request.is_bounded(), request.path


def test_the_request_count_is_a_property_of_the_plan() -> None:
    """Two requests for the headline query, whatever the registry holds.

    A program whose length grew with the number of rows returned would satisfy
    every other rule here and still be the N+1 problem.
    """
    assert (
        len(
            _requests(
                q.dataflow,
                q.dataflow.agency == "ESTAT",
                q.dataflow.dsd.agency == "ESTAT",
                q.dataflow.dsd.dimensions.any(q.component.concept.id == "REF_AREA"),
            )
        )
        == 2
    )


# ── detail= (#318) ────────────────────────────────────────────────────────


def test_a_listing_asks_for_stubs_only_when_it_is_a_projection() -> None:
    """The capability the retired `browse()` had, and the constraint on it.

    `codelist/ECB?detail=allstubs` is 88 KB for 244 codelists; one codelist at
    full detail (CL_CURRENCY) is 69 KB. Listing a whole catalogue and fetching
    a single artefact cost the same order, which is what made the stub worth
    having.

    But only a *projection* may ask for it. A row terminal resolves rows to
    artefacts out of the store, and a stub-populated store answers with a
    codelist carrying zero codes and no error — so `projection=None` means
    `full`, and that is the default.
    """
    program = plan_program(Plan(root=scanned(q.codelist), predicates=(q.codelist.agency == "ECB",)), SDMX_REST)
    (row_terminal,) = requests_for(program)
    assert row_terminal.detail == "full"

    (projected,) = requests_for(program, projection=[("artefact", "resource_id")])
    assert projected.detail == "allstubs"


def test_a_projection_reading_a_description_still_asks_for_full() -> None:
    """`description` is outside the stub surface even though `name` is inside
    it — measured, not assumed."""
    program = plan_program(
        Plan(root=scanned(q.codelist), predicates=(q.codelist.agency == "ECB",)),
        SDMX_REST,
    )
    projection = [("localized_text", "description_en")]
    assert requests_for(program, projection=projection)[0].detail == "full"
    assert requests_for(program, projection=[("localized_text", "name_en")])[0].detail == "allstubs"


def test_the_membership_reference_forces_full_on_the_outer_scan() -> None:
    """The bug this pins, and it is the planner's own doing.

    The §9.1 rewrite narrows the outer scan by `dataflow.dsd_urn`, which is a
    *reference* — and a stub carries identification only. Computed from the
    caller's predicates alone, the outer scan came back `allstubs`, every
    `dsd_urn` was null, and the membership matched nothing: the rewrite
    answering emptily, without an error.

    A `Membership` is not in `step.predicates` because the planner produced
    it, which is exactly why a check walking only what the caller wrote could
    not see it.
    """
    inner, outer = _requests(
        q.dataflow,
        q.dataflow.agency == "ESTAT",
        q.dataflow.dsd.agency == "ESTAT",
        q.dataflow.dsd.dimensions.any(q.component.concept.id == "REF_AREA"),
    )
    assert inner.resource == "datastructure"
    assert inner.detail == "full", "the inner scan reads components"
    assert outer.resource == "dataflow"
    assert outer.detail == "full", "the outer scan must carry dsd_urn for the membership"


def test_the_membership_reference_defeats_a_stub_even_for_a_projection() -> None:
    """The reference is not in `step.predicates` — the *planner* produced it —
    so a check walking only what the caller wrote could not see it, and the
    outer scan came back as stubs with every `dsd_urn` null."""
    program = plan_program(
        Plan(
            root=scanned(q.dataflow),
            predicates=(
                q.dataflow.agency == "ESTAT",
                q.dataflow.dsd.agency == "ESTAT",
                q.dataflow.dsd.dimensions.any(q.component.concept.id == "REF_AREA"),
            ),
        ),
        SDMX_REST,
    )
    _inner, outer = requests_for(program, projection=[("artefact", "resource_id")])
    assert outer.detail == "full"


def test_projected_paths_join_the_root_scan_only() -> None:
    """GH-271: a `Select`'s paths are over the root scan's rows, which
    `plan_program` emits last, after any §9.1 sub-scan."""
    plain = plan_program(Plan(root=scanned(q.dataflow), predicates=(q.dataflow.agency == "ESTAT",)), SDMX_REST)
    assert requests_for(plain)[0].references == "none"
    one_edge = projection_paths([("d", q.dataflow.dsd.id)])
    (projected,) = requests_for(plain, projected_paths=one_edge)
    assert projected.references == "children"

    rewritten = plan_program(
        Plan(
            root=scanned(q.dataflow),
            predicates=(q.dataflow.agency == "ESTAT", q.dataflow.dsd.agency == "ESTAT"),
        ),
        SDMX_REST,
    )
    baseline = requests_for(rewritten)
    two_edges = projection_paths([("c", q.dataflow.dsd.dimensions.concept.id.list())])
    with_paths = requests_for(rewritten, projected_paths=two_edges)
    assert len(with_paths) == len(baseline) == 2
    assert with_paths[0] == baseline[0], "the sub-scan is not what the projection reads"
    assert with_paths[1].references == "descendants"
