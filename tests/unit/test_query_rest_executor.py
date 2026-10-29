"""Fetch, materialise, ask locally — and the differential that proves it closed.

§13's claim is that a backend which computes nothing can still answer a query:
fetch what the plan names, write it into a store, and run the *original* query
against that. The risk is not the fetching. It is that a fetch one relation
short does not fail — the query runs against a store missing a table and
returns nothing, with no error anywhere.

**So the gate is a differential against a store that holds everything.** The
reference registry is loaded directly with every artefact; the test registry is
loaded only with what the planner chose to request. If the closure is right the
two answer identically; if it asks for too little, the reference finds rows the
fetched one does not.

That is only a real test because `FakeRestService` computes `references=` from
the SDMX meaning — walking `Ref` fields — rather than by asking `_closure` what
it wanted. A fake that served whatever was asked for could never disagree.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import sdmxlib as sl
from sdmxlib.model.concept import Concept, ConceptScheme
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure, Dimension
from sdmxlib.model.organisation import Agency
from sdmxlib.query._execute import fetch_into
from sdmxlib.query._plan import Plan, scanned
from sdmxlib.query._pushdown import PushdownError
from sdmxlib.query.accessors import q
from tests.support.factories import concept_ref
from tests.support.fake_rest import FakeRestService

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sdmxlib.model.ref import Ref
    from sdmxlib.query._expr import Agg, Col, Pred
    from sdmxlib.query.accessors import DataflowRow

pytestmark = pytest.mark.unit

ESTAT = Agency(id="ESTAT")


def _catalogue() -> list[ConceptScheme | DataStructure | Dataflow]:
    """Two DSDs over a shared concept scheme, three dataflows across them.

    Shaped so every query below has a non-empty *and* a non-total answer. A
    fetch that returned everything and one that returned nothing would both
    agree with a broken closure otherwise.
    """
    scheme = ConceptScheme(
        id="CS",
        maintainer=ESTAT,
        name={"en": "CS"},
        concepts=[
            Concept(id="REF_AREA", name={"en": "Reference area"}),
            Concept(id="FREQ", name={"en": "Frequency"}),
        ],
    )

    # `Ref`, not the bare `Concept` object: a component built from the item
    # itself is silently dropped by the writer (#279), and the query would then
    # answer nothing for a reason that has nothing to do with the fetch.
    def concept(item_id: str) -> Ref[Concept]:
        return concept_ref(item_id, agency="ESTAT", scheme_id="CS", version="1.0")

    with_area = DataStructure(
        id="WITH_AREA",
        maintainer=ESTAT,
        name={"en": "has a reference area"},
        dimensions=[
            Dimension(id="REF_AREA", concept=concept("REF_AREA")),
            Dimension(id="FREQ", concept=concept_ref("FREQ")),
        ],
    )
    no_area = DataStructure(
        id="NO_AREA",
        maintainer=ESTAT,
        name={"en": "frequency only"},
        dimensions=[Dimension(id="FREQ", concept=concept("FREQ"))],
    )
    flows = [
        Dataflow(id=name, maintainer=ESTAT, name={"en": name}, structure=dsd)
        for name, dsd in [("FLOW_A", with_area), ("FLOW_B", with_area), ("FLOW_C", no_area)]
    ]
    return [scheme, with_area, no_area, *flows]


@pytest.fixture
def reference() -> Iterator[sl.LocalRegistry]:
    """A store holding the whole catalogue — what a perfect fetch would give."""
    with sl.LocalRegistry(":memory:") as registry:
        for artefact in _catalogue():
            registry.add(artefact)
        yield registry


@pytest.fixture
def service() -> FakeRestService:
    return FakeRestService(_catalogue())


def _ids(registry: sl.LocalRegistry, *predicates: Pred[DataflowRow]) -> list[str]:
    rows = registry.query(q.dataflow).filter(*predicates).select(q.dataflow.id).collect()
    return sorted(str(value) for value in rows.to_series())


# ── the differential ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name",
    ["by the DSD's id", "by a concept the DSD uses", "by the flow's own id"],
)
def test_a_fetched_store_answers_what_a_complete_one_does(
    reference: sl.LocalRegistry, service: FakeRestService, name: str
) -> None:
    """The gate. Fetch only what the plan asks for, and get the same answer.

    A closure that asks too little shows up here as the reference finding rows
    the fetched store does not — which is the *only* signal, since both stores
    answer without error either way.
    """
    predicates: tuple[Pred[DataflowRow], ...] = {
        "by the DSD's id": (
            q.dataflow.agency == "ESTAT",
            q.dataflow.dsd.agency == "ESTAT",
            q.dataflow.dsd.id == "WITH_AREA",
        ),
        "by a concept the DSD uses": (
            q.dataflow.agency == "ESTAT",
            q.dataflow.dsd.agency == "ESTAT",
            q.dataflow.dsd.dimensions.any(q.component.concept.id == "REF_AREA"),
        ),
        "by the flow's own id": (q.dataflow.agency == "ESTAT", q.dataflow.id == "FLOW_A"),
    }[name]

    expected = _ids(reference, *predicates)
    assert 0 < len(expected) < 3, f"{name} does not discriminate: {expected}"

    with sl.LocalRegistry(":memory:") as fetched:
        fetch_into(fetched, Plan(root=scanned(q.dataflow), predicates=predicates), service)
        assert _ids(fetched, *predicates) == expected


def test_a_short_fetch_would_have_been_caught(service: FakeRestService) -> None:
    """The differential's teeth, demonstrated by defeating the closure.

    Asking for `references=none` where the closure asks for `children` returns
    the DSDs without their concept scheme. The query then answers **nothing**
    rather than failing — which is why the test above compares against a
    complete store instead of asserting the fetch looks reasonable.
    """
    predicates = (
        q.dataflow.agency == "ESTAT",
        q.dataflow.dsd.agency == "ESTAT",
        q.dataflow.dsd.dimensions.any(q.component.concept.id == "REF_AREA"),
    )
    with sl.LocalRegistry(":memory:") as short:
        for message in (
            service.get_all("datastructure", "ESTAT", references="none"),
            service.get_all("dataflow", "ESTAT", references="none"),
        ):
            for field in ("data_structures", "dataflows", "concept_schemes"):
                for artefact in getattr(message, field, ()):
                    short.add(artefact)
        assert _ids(short, *predicates) == [], "the short fetch should answer nothing, silently"


# ── what was asked ────────────────────────────────────────────────────────


def test_the_headline_query_issues_two_requests(service: FakeRestService) -> None:
    """Two, independent of how many dataflows exist — §13's cost claim."""
    predicates = (
        q.dataflow.agency == "ESTAT",
        q.dataflow.dsd.agency == "ESTAT",
        q.dataflow.dsd.dimensions.any(q.component.concept.id == "REF_AREA"),
    )
    with sl.LocalRegistry(":memory:") as fetched:
        requests = fetch_into(fetched, Plan(root=scanned(q.dataflow), predicates=predicates), service)
    assert len(requests) == 2
    assert [call[0] for call in service.calls] == ["datastructure", "dataflow"]
    assert [call[4] for call in service.calls] == ["children", "none"]


def test_nothing_is_fetched_when_the_planner_refuses(service: FakeRestService) -> None:
    """The refusal comes before the network, not after it.

    A backend that fetched and *then* discovered it should not have would have
    already paid the cost the rule exists to avoid.
    """
    with sl.LocalRegistry(":memory:") as fetched, pytest.raises(PushdownError):
        fetch_into(
            fetched,
            Plan(root=scanned(q.dataflow), predicates=(q.dataflow.name["en"].str.contains("FLOW"),)),
            service,
        )
    assert service.calls == []


# ── the user-facing entry ─────────────────────────────────────────────────


def _rest_with(service: FakeRestService) -> sl.RestRegistry:
    """A `RestRegistry` answering from `service` instead of over HTTP.

    Substituting the client is the seam `_execute.StructureSource` exists for:
    the executor depends on the *shape* of the structure endpoint, so a
    service that is not HTTP is a legitimate implementation rather than a mock.
    """
    registry = sl.RestRegistry("https://example.invalid/", id="TEST")
    registry._client = service  # pyrefly: ignore[bad-assignment]
    return registry


def test_the_rest_registry_answers_the_same_query_the_local_one_does(
    reference: sl.LocalRegistry, service: FakeRestService
) -> None:
    """One language, two backends — the claim #306 asserted and %17 owed."""
    predicates = (
        q.dataflow.agency == "ESTAT",
        q.dataflow.dsd.agency == "ESTAT",
        q.dataflow.dsd.dimensions.any(q.component.concept.id == "REF_AREA"),
    )
    rest = _rest_with(service)
    try:
        assert sorted(rest.query(q.dataflow).filter(*predicates).ids()) == _ids(reference, *predicates)
    finally:
        rest.close()


def test_two_terminals_on_the_same_question_fetch_once(service: FakeRestService) -> None:
    """Keyed on `canonical_key`, so it is the *question* that is cached.

    `filter(a).filter(b)` and `filter(b).filter(a)` mean the same thing and
    must share a fetch — which is what #270 built that key for, and the reason
    a plain object identity check would not do.
    """
    predicates = (q.dataflow.agency == "ESTAT", q.dataflow.dsd.agency == "ESTAT")
    rest = _rest_with(service)
    try:
        rest.query(q.dataflow).filter(*predicates).ids()
        after_first = len(service.calls)
        rest.query(q.dataflow).filter(*predicates).count()
        rest.query(q.dataflow).filter(*reversed(predicates)).ids()
        assert len(service.calls) == after_first
    finally:
        rest.close()


def test_nothing_is_fetched_until_a_terminal(service: FakeRestService) -> None:
    """Strict laziness is what makes the planner possible, not tidiness.

    `reg.query(q.dataflow)` names no agency. Fetching there would fetch every
    dataflow in the registry — the query is not a question until a terminal
    asks it.
    """
    rest = _rest_with(service)
    try:
        pending = rest.query(q.dataflow).filter(q.dataflow.agency == "ESTAT")
        assert service.calls == []
        pending.ids()
        assert service.calls != []
    finally:
        rest.close()


def test_projection_and_aggregation_work_over_rest(service: FakeRestService) -> None:
    """§11's claim, run rather than asserted about `can_project`.

    Everything backing §11 until now was a boolean: `can_project()` said a
    projection *would* be servable. This is the projection actually running
    over a fetched store — one request, then `select`, `unique` and `group_by`
    computed locally, which is what "the backend computes nothing" means in
    practice.

    It composes without anything special because every terminal goes through
    `Query._cursor()`, so a `Select` triggers the same lazy fetch a row
    terminal does.

    **Two fetches, not one, since §5.14.** The first two projections name plain
    root columns and are served by `detail=allstubs`; the third aggregates over
    a to-many path, which a stub cannot answer, so it fetches `full` for
    itself. The plan is the same question each time — the *detail* is not, and
    it is part of the fetch key precisely so a stub request cannot leave a
    later artefact query reading rows whose items never arrived.

    That is a real trade: a caller who mixes a projection with an aggregate
    over one plan pays a round trip that asking `full` throughout would not.
    Taken because the payload difference runs the other way and is much
    larger — a `full` scan of a registry's codelists carries every code of
    every codelist, where the stub listing carries none.
    """
    rest = _rest_with(service)
    try:
        base = rest.query(q.dataflow).filter(q.dataflow.agency == "ESTAT")
        assert sorted(base.select(q.dataflow.id).collect().to_series().to_list()) == ["FLOW_A", "FLOW_B", "FLOW_C"]
        assert base.select(q.dataflow.agency).unique().collect().to_series().to_list() == ["ESTAT"]
        assert base.group_by(q.dataflow.agency).len().collect().to_dicts() == [{"agency": "ESTAT", "len": 3}]
        assert [call[-1] for call in service.calls] == ["none", "none"], "no references either way"
        assert len(service.calls) == 2, "allstubs for the two projections, full for the aggregate"
    finally:
        rest.close()


# ── a projection reaches what it reads (GH-271) ──────────────────────────


def _projected(
    registry: sl.RestRegistry | sl.LocalRegistry, **items: Col[DataflowRow] | Agg[DataflowRow]
) -> list[dict[str, object]]:
    query = registry.query(q.dataflow).filter(q.dataflow.agency == "ESTAT")
    frame = query.select(f=q.dataflow.id, **items).collect()
    return sorted(frame.to_dicts(), key=lambda row: str(row["f"]))


def test_a_projection_across_an_edge_answers_what_a_complete_store_does(
    reference: sl.LocalRegistry, service: FakeRestService
) -> None:
    """The plan filters only on the dataflow, so its closure alone asks for
    `references=none`; the projection reads `dsd.id`, and the fetch must reach
    it or every row reads null."""
    rest = _rest_with(service)
    try:
        assert _projected(rest, d=q.dataflow.dsd.id) == _projected(reference, d=q.dataflow.dsd.id)
        assert [call[-1] for call in service.calls] == ["children"]
    finally:
        rest.close()


def test_a_projection_after_a_plain_fetch_of_the_plan_fetches_again(service: FakeRestService) -> None:
    """The fetch key carries `references=`: a row terminal's `none` fetch of the
    same plan must not satisfy a projection that needs the edge."""
    rest = _rest_with(service)
    try:
        plain = rest.query(q.dataflow).filter(q.dataflow.agency == "ESTAT")
        assert sorted(plain.ids()) == ["FLOW_A", "FLOW_B", "FLOW_C"]
        rows = _projected(rest, d=q.dataflow.dsd.id)
        assert [row["d"] for row in rows] == ["WITH_AREA", "WITH_AREA", "NO_AREA"]
        assert [call[-1] for call in service.calls] == ["none", "children"]
    finally:
        rest.close()


def test_a_projection_two_edges_out_asks_for_descendants(reference: sl.LocalRegistry, service: FakeRestService) -> None:
    """`dsd.dimensions.concept` crosses dataflow → DSD → concept scheme."""
    concepts = q.dataflow.dsd.dimensions.concept.id.list()
    rest = _rest_with(service)
    try:
        assert _projected(rest, c=concepts) == _projected(reference, c=concepts)
        assert [call[-1] for call in service.calls] == ["descendants"]
    finally:
        rest.close()
