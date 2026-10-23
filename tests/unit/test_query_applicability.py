"""`q.hierarchy_applicability` — which hierarchy applies, resolved once (#372).

The rule is SDMX's: a `HierarchyAssociation` naming this dataflow as its
context is *scoped* and beats an *unscoped* one, which is the default for any
flow whose DSD carries that component. Stating it in the DDL rather than in
Python is what makes the path language, `LocalRegistry.applicable_hierarchies`
and a downstream consumer read the same answer — the divergence #372 opened
with was two implementations disagreeing about ambiguity.

Every fixture here is built to *discriminate*. One flow with one association
would confirm any implementation, including one that ignores the correlation
entirely and returns every association it can find.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import sdmxlib as sl
from sdmxlib.model.datastructure import DataStructure, Dimension
from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy, HierarchyAssociation
from sdmxlib.model.ref import Ref
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.query.accessors import q
from tests.support.factories import concept_ref

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit


_NAME = {"en": "x"}
_A = q.hierarchy_applicability


def _dim_urn(dsd_id: str, component_id: str) -> SdmxUrn[Dimension]:
    return SdmxUrn.make(
        Dimension,
        package="datastructure",
        artefact_class="Dimension",
        agency="SC",
        id=dsd_id,
        version="1.0",
        item_id=component_id,
    )


def _code_ref(code_id: str) -> Ref[sl.Code]:
    return Ref[sl.Code].unresolved(
        SdmxUrn.make(
            sl.Code,
            package="codelist",
            artefact_class="Code",
            agency="SC",
            id="CL_GEO",
            version="1.0",
            item_id=code_id,
        )
    )


@pytest.fixture
def reg() -> Iterator[sl.LocalRegistry]:
    """Two flows on one DSD, two hierarchies, one scoped and one unscoped.

    The shape each assertion needs:

    * `CPI_FLOW` has a scoped association *and* inherits the unscoped one, so
      precedence is exercised rather than assumed.
    * `TRADE_FLOW` has only the unscoped one, so the default branch is real.
    * `TRADE` is a second DSD with the same `REF_AREA` component id and no
      association at all — an implementation matching on the id alone would
      answer for it.
    """
    with sl.LocalRegistry(":memory:") as registry:
        codelist = sl.Codelist(
            id="CL_GEO",
            maintainer="SC",
            version="1.0",
            name=_NAME,
            codes=[sl.Code(id=c, name=_NAME) for c in ("CA", "ON", "TOR")],
        )
        cpi_dsd = DataStructure(
            id="CPI",
            maintainer="SC",
            version="1.0",
            name=_NAME,
            dimensions=[
                Dimension(id="REF_AREA", concept=concept_ref("REF_AREA"), representation=Ref.to(codelist)),
                Dimension(id="FREQ", concept=concept_ref("FREQ")),
            ],
        )
        other_dsd = DataStructure(
            id="TRADE",
            maintainer="SC",
            version="1.0",
            name=_NAME,
            dimensions=[Dimension(id="REF_AREA", concept=concept_ref("REF_AREA"), representation=Ref.to(codelist))],
        )
        cpi_flow = sl.Dataflow(id="CPI_FLOW", maintainer="SC", version="1.0", name=_NAME, structure=Ref.to(cpi_dsd))
        trade_flow = sl.Dataflow(id="TRADE_FLOW", maintainer="SC", version="1.0", name=_NAME, structure=Ref.to(cpi_dsd))
        standard = Hierarchy(
            id="GEO_STD",
            maintainer="SC",
            version="1.0",
            name=_NAME,
            tree=[
                HierarchicalCode(
                    id="CA",
                    code=_code_ref("CA"),
                    children=[HierarchicalCode(id="ON", code=_code_ref("ON"))],
                )
            ],
        )
        cpi_specific = Hierarchy(
            id="GEO_CPI",
            maintainer="SC",
            version="1.0",
            name=_NAME,
            tree=[HierarchicalCode(id="CA", code=_code_ref("CA"))],
        )
        registry.add_all(
            [
                codelist,
                cpi_dsd,
                other_dsd,
                cpi_flow,
                trade_flow,
                standard,
                cpi_specific,
                HierarchyAssociation(
                    id="HA_STD",
                    maintainer="SC",
                    version="1.0",
                    name=_NAME,
                    linked_hierarchy=Ref.to(standard),
                    linked_object=Ref[object].unresolved(_dim_urn("CPI", "REF_AREA")),
                ),
                HierarchyAssociation(
                    id="HA_CPI",
                    maintainer="SC",
                    version="1.0",
                    name=_NAME,
                    linked_hierarchy=Ref.to(cpi_specific),
                    linked_object=Ref[object].unresolved(_dim_urn("CPI", "REF_AREA")),
                    context_object=Ref.to(cpi_flow),
                ),
            ]
        )
        yield registry


def _rows(reg: sl.LocalRegistry) -> list[dict[str, object]]:
    frame = reg.query(_A).select(
        flow=_A.dataflow.id,
        comp=_A.component.id,
        hier=_A.hierarchy.id,
        scoped=_A.scoped,
        rank=_A.rank,
    )
    return sorted(frame.collect().to_dicts(), key=lambda r: (str(r["flow"]), int(str(r["rank"]))))


def test_a_scoped_association_outranks_an_unscoped_one(reg: sl.LocalRegistry) -> None:
    """Both apply to CPI_FLOW; the one naming it wins, and the loser stays."""
    cpi = [r for r in _rows(reg) if r["flow"] == "CPI_FLOW"]
    assert cpi == [
        {"flow": "CPI_FLOW", "comp": "REF_AREA", "hier": "GEO_CPI", "scoped": True, "rank": 1},
        {"flow": "CPI_FLOW", "comp": "REF_AREA", "hier": "GEO_STD", "scoped": False, "rank": 2},
    ]


def test_an_unscoped_association_applies_to_every_flow_on_the_dsd(reg: sl.LocalRegistry) -> None:
    """TRADE_FLOW was never named by any association and still resolves."""
    trade = [r for r in _rows(reg) if r["flow"] == "TRADE_FLOW"]
    assert trade == [{"flow": "TRADE_FLOW", "comp": "REF_AREA", "hier": "GEO_STD", "scoped": False, "rank": 1}]


def test_a_component_with_no_association_is_absent(reg: sl.LocalRegistry) -> None:
    """`FREQ` shares a DSD with `REF_AREA` and has no hierarchy of its own."""
    assert not [r for r in _rows(reg) if r["comp"] == "FREQ"]


def test_a_different_dsd_sharing_a_component_id_is_not_swept_in(reg: sl.LocalRegistry) -> None:
    """`TRADE` also has `REF_AREA`, and no flow of its own points at it.

    The association is keyed `(dsd_urn, component_id)`. Matching on the id
    alone would answer here, and every other assertion in this file would
    still pass.
    """
    dsds = {str(r["flow"]) for r in _rows(reg)}
    assert dsds == {"CPI_FLOW", "TRADE_FLOW"}


def test_the_winner_is_one_row_per_flow(reg: sl.LocalRegistry) -> None:
    """What a consumer joins on: `rank == 1`, no COALESCE at the call site."""
    best = reg.query(_A).filter(_A.rank == 1).select(flow=_A.dataflow.id, hier=_A.hierarchy.id)
    assert sorted(best.collect().to_dicts(), key=lambda r: str(r["flow"])) == [
        {"flow": "CPI_FLOW", "hier": "GEO_CPI"},
        {"flow": "TRADE_FLOW", "hier": "GEO_STD"},
    ]


def test_the_tiebreak_is_unique_on_data_built_to_break_it(reg: sl.LocalRegistry) -> None:
    """The proof `test_query_order.VIEW_TIEBREAKS` points at.

    A view declares no constraints, so the ordering guard cannot read this
    key off the DDL. It holds by construction — one dataflow row joined to one
    component row joined to one association row — and the fixture is the
    adversarial case for it: two flows sharing a DSD, two associations on one
    component, and a second DSD repeating the component id. If the key were
    short a column, these rows would collide.
    """
    key = type(_A).__key__
    assert key == ("dataflow_urn", "dsd_urn", "component_id", "association_urn")

    columns = ", ".join(f'"{column}"' for column in key)
    total, distinct = reg.connection.execute(
        f"SELECT count(*), count(DISTINCT ({columns})) FROM sdmx.hierarchy_applicability"
    ).fetchone() or (0, 0)
    assert total > 1, "the fixture stopped discriminating"
    assert total == distinct


def test_the_relation_agrees_with_the_python_resolver(reg: sl.LocalRegistry) -> None:
    """The differential check stage 2 exists for.

    `applicable_hierarchies` walks objects; the view walks rows. They are
    different implementations of one rule, and #372 exists because two such
    implementations had already drifted apart on ambiguity.
    """
    for flow_id in ("CPI_FLOW", "TRADE_FLOW"):
        flow = reg.get(sl.Dataflow.urn_for(agency="SC", id=flow_id, version="1.0"))
        assert flow is not None
        walked = [str(ref.urn) for ref in reg.applicable_hierarchies(flow, "REF_AREA")]
        queried = [
            str(row["hier"])
            for row in reg.query(_A)
            .filter(_A.dataflow.id == flow_id)
            .filter(_A.component.id == "REF_AREA")
            .select(hier=_A.hierarchy.urn, rank=_A.rank)
            .collect()
            .sort("rank")
            .to_dicts()
        ]
        assert walked == queried, flow_id


# ── the fetch, over REST ──────────────────────────────────────────────────


def test_a_derived_root_fetches_its_source_relations() -> None:
    """One scan, three requests — the expansion `__derived_from__` names.

    A view has no endpoint. Before this the planner refused the root outright,
    with a message advising the caller to "root the query at the owner", which
    is advice for an *item* relation and nonsense for a derived one. There is
    a fetch here; it is simply not one request.
    """
    from sdmxlib.query._plan import Plan, scanned
    from sdmxlib.query._pushdown import SDMX_REST, plan_program
    from sdmxlib.query._rest import requests_for

    plan = Plan(root=scanned(_A), predicates=(_A.agency == "SC",))
    requests = requests_for(plan_program(plan, SDMX_REST))

    assert [request.resource for request in requests] == ["hierarchyassociation", "dataflow", "datastructure"]
    assert {request.agency for request in requests} == {"SC"}
    # `references=none`: the relations are being fetched outright rather than
    # reached through one another, and `detail=full` because the view reads
    # plain columns off all three — a stub carries identification only.
    assert {request.references for request in requests} == {"none"}
    assert {request.detail for request in requests} == {"full"}


def test_a_derived_scan_still_has_to_be_bounded() -> None:
    """The agency is what bounds all three requests, so it is not optional.

    Without it the expansion would fetch every association, every dataflow and
    every DSD a service holds — the §5 refusal, and the reason the view
    projects `agency_id` at all rather than making a caller reach through the
    dataflow hop for it.
    """
    from sdmxlib.query._plan import Plan, scanned
    from sdmxlib.query._pushdown import SDMX_REST, PushdownError, plan_program

    plan = Plan(root=scanned(_A), predicates=(_A.component.id == "REF_AREA",))
    with pytest.raises(PushdownError, match="agency_id"):
        plan_program(plan, SDMX_REST)


def test_the_local_backend_scans_it_directly() -> None:
    """DuckDB is `total`, so nothing about the expansion applies to it."""
    from sdmxlib.query._plan import Plan, scanned
    from sdmxlib.query._pushdown import DUCKDB, plan_program

    program = plan_program(Plan(root=scanned(_A)), DUCKDB)
    assert len(program.steps) == 1
