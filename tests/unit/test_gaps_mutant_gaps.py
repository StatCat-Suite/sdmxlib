"""What mutation found `formats/gaps.py`'s tests could not see (#458).

Each test here kills mutants that survived the whole unit suite. They are
grouped by what was missing rather than by function, and the triage of the
rest — equivalent, and why — is in `docs/design/mutation-evidence.md`.

Two shapes recur. A predicate exercised only where it *fires* on its first
candidate: `test_field_coverage`'s maximal hierarchy puts a version on its
first top-level node, so the walk below that node was never taken. And a
message whose text no test reads: a reason naming "SDMX-ML None" passed.
"""

import pytest

import sdmxlib as sl
from sdmxlib.formats.gaps import (
    TARGETS,
    availability_label,
    declared_gaps,
    describe,
    dropped,
    unrepresentable,
    without_dropped,
)
from sdmxlib.model.annotations import Annotation, AnnotationURL
from sdmxlib.model.category import Categorisation
from sdmxlib.model.constraint import (
    AvailabilityConstraint,
    ConstraintAttachment,
    ConstraintType,
    CubeRegion,
    DataConstraint,
    KeyValue,
)
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure, Dimension
from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy
from sdmxlib.model.mapping import ComponentMap, RepresentationMap, StructureMap, StructureSet
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.provision import ProvisionAgreement
from sdmxlib.model.ref import Ref, artefact_urn
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.model.valuelist import ValueList
from tests.support.factories import concept_ref

pytestmark = pytest.mark.unit

DRAFT = "1.0.0-draft"


def _urn(artefact: sl.MaintainableArtefact) -> str:
    return str(artefact_urn(artefact))


def _fields(artefact: object, target: str) -> list[str]:
    return [gap.field for gap in unrepresentable(artefact, target)]


# ── a walk past the first candidate ───────────────────────────────────────


def _code_ref(code_id: str) -> Ref[sl.Code]:
    return Ref.unresolved(sl.Code.parse_urn(f"urn:sdmx:org.sdmx.infomodel.codelist.Code=AG:CL(1.0).{code_id}"))


def _hierarchy(*, child_version: str | None, top_version: str | None = None) -> Hierarchy:
    leaf = HierarchicalCode(id="LEAF", code=_code_ref("B"), version=child_version)
    top = HierarchicalCode(id="TOP", code=_code_ref("A"), version=top_version, children=[leaf])
    return Hierarchy(id="H", maintainer="AG", version="1.0", name={"en": "H"}, tree=[top])


def test_a_node_version_below_the_top_level_is_a_3x_gap() -> None:
    """The maximal hierarchy versions its first node, so the descent was never taken."""
    assert "tree[].version" in _fields(_hierarchy(child_version="2.0"), "3.0")


def test_a_hierarchy_without_node_versions_loses_none() -> None:
    assert "tree[].version" not in _fields(_hierarchy(child_version=None), "3.0")


# ── what SDMX-ML 3.1 keeps ────────────────────────────────────────────────


def _flow_ref(flow_id: str = "DF") -> Ref[object]:
    return Ref[object].unresolved(Dataflow.urn_for(agency="AG", id=flow_id, version="1.0"))


_REGION = CubeRegion(keys=(KeyValue(dimension_id="FREQ", values=frozenset({"A"})),))
_AVAILABILITY = AvailabilityConstraint(attachment=ConstraintAttachment(dataflows=(_flow_ref(),)), region=_REGION)


def test_sdmx_ml_3_1_leaves_no_availability_constraint_out() -> None:
    """Existing tests round-trip one through 3.1 — which keeps it whatever `dropped` says."""
    assert dropped(StructureMessage(availability_constraints=[_AVAILABILITY]), "3.1") == ()


def test_an_availability_constraint_left_out_says_exactly_why() -> None:
    (drop,) = dropped(StructureMessage(availability_constraints=[_AVAILABILITY]), "3.0")
    label = availability_label(_AVAILABILITY)
    assert (drop.urn, drop.cause) == (label, label)
    assert drop.reason == "SDMX-ML 3.0 has no AvailabilityConstraint, which only SDMX-ML 3.1 has"


def test_an_availability_constraint_with_no_single_target_is_named_so() -> None:
    spread = AvailabilityConstraint(
        attachment=ConstraintAttachment(dataflows=(_flow_ref("A"), _flow_ref("B"))), region=_REGION
    )
    assert availability_label(spread) == "AvailabilityConstraint(no single target)"


@pytest.mark.parametrize(("target", "kept"), [("3.1", [_AVAILABILITY]), ("3.0", [])])
def test_only_3_1_s_message_keeps_its_availability_constraints(target: str, kept: list[object]) -> None:
    """With something else left out too, so the message is rebuilt rather than returned as it was."""
    structure_set = StructureSet(id="SS", maintainer="AG", version="1.0", name={"en": "SS"})
    msg = StructureMessage(structure_sets=[structure_set], availability_constraints=[_AVAILABILITY])
    written = without_dropped(msg, target)
    assert written is not msg
    assert (list(written.structure_sets), list(written.availability_constraints)) == ([], kept)


def _constraint(role: ConstraintType, *, flows: int = 1) -> DataConstraint:
    return DataConstraint(
        id="DC",
        maintainer="AG",
        name={"en": "DC"},
        constraint_type=role,
        attachment=ConstraintAttachment(dataflows=tuple(_flow_ref(f"DF{i}") for i in range(flows))),
        regions=(_REGION,),
    )


def test_only_an_actual_constraint_that_fits_is_3_1_s_availability() -> None:
    """The 3.1 `sdmx_class` row: Actual *and* fitting, not either alone."""
    assert "sdmx_class" in _fields(_constraint(ConstraintType.ACTUAL), "3.1")
    assert "sdmx_class" not in _fields(_constraint(ConstraintType.ALLOWED), "3.1")
    assert _constraint(ConstraintType.ACTUAL, flows=2).availability_misfits(), "the case needs a misfit"
    assert "sdmx_class" not in _fields(_constraint(ConstraintType.ACTUAL, flows=2), "3.1")


# ── what follows from a drop ──────────────────────────────────────────────


def _codelist(version: str = "1.0") -> sl.Codelist:
    return sl.Codelist(
        id="CL", maintainer="AG", version=version, name={"en": "CL"}, codes=[sl.Code(id="A", name={"en": "a"})]
    )


def _dsd(*representations: SdmxUrn[sl.Codelist] | SdmxUrn[ValueList]) -> DataStructure:
    return DataStructure(
        id="DSD",
        maintainer="AG",
        version="1.0",
        name={"en": "DSD"},
        dimensions=[
            Dimension(id=f"D{i}", representation=rep, concept=concept_ref("X")) for i, rep in enumerate(representations)
        ],
    )


def test_a_3x_write_does_not_leave_out_what_names_a_draft() -> None:
    """Naming a draft is 2.1's limit only — even once something else is left out of 3.0."""
    dsd = _dsd(sl.Codelist.urn_for(agency="AG", id="CL", version=DRAFT))
    structure_set = StructureSet(id="SS", maintainer="AG", version="1.0", name={"en": "SS"})
    msg = StructureMessage(data_structures=[dsd], codelists=[_codelist(DRAFT)], structure_sets=[structure_set])
    assert [drop.urn for drop in dropped(msg, "3.0")] == [_urn(structure_set)]


def test_a_reference_to_a_value_list_outside_the_message_is_still_unnameable_in_2_1() -> None:
    """With the value list in the message, `_follow` would leave the DSD out anyway."""
    vl = ValueList.urn_for(agency="AG", id="VL", version="1.0")
    dsd = _dsd(vl)
    (drop,) = dropped(StructureMessage(data_structures=[dsd]), "2.1")
    assert (drop.urn, drop.cause) == (_urn(dsd), str(vl))
    assert drop.reason == f"it references {vl}, a ValueList SDMX-ML 2.1 cannot name"


def test_a_chain_that_needs_a_second_pass_is_followed_to_its_end() -> None:
    """Categorisations precede provision agreements in message order.

    So the first pass leaves out the agreement (it names a flow that names the
    draft) only after it has already passed the categorisation pointing at it.
    """
    dsd = DataStructure(id="DSD", maintainer="AG", version=DRAFT, name={"en": "DSD"})
    flow = Dataflow(
        id="DF",
        maintainer="AG",
        version="1.0",
        name={"en": "DF"},
        structure=Ref[DataStructure].unresolved(DataStructure.urn_for(agency="AG", id="DSD", version=DRAFT)),
    )
    agreement = ProvisionAgreement(
        id="PA", maintainer="AG", version="1.0", name={"en": "PA"}, dataflow=Ref[Dataflow].unresolved(flow.urn)
    )
    categorisation = Categorisation(
        id="CAT",
        maintainer="AG",
        version="1.0",
        name={"en": "CAT"},
        source=Ref[object].unresolved(agreement.urn),
        target=Ref[object].unresolved(
            SdmxUrn.parse("urn:sdmx:org.sdmx.infomodel.categoryscheme.Category=AG:CS(1.0).X")
        ),
    )
    msg = StructureMessage(
        data_structures=[dsd], dataflows=[flow], provision_agreements=[agreement], categorisations=[categorisation]
    )
    assert [m.urn for m in msg.all_artefacts()].index(categorisation.urn) < [m.urn for m in msg.all_artefacts()].index(
        agreement.urn
    ), "the case only discriminates in this order"

    drops = {drop.urn: drop for drop in dropped(msg, "2.1")}
    assert set(drops) == {_urn(dsd), _urn(flow), _urn(agreement), _urn(categorisation)}
    assert drops[_urn(categorisation)].cause == _urn(dsd)
    assert drops[_urn(categorisation)].reason == f"it references {_urn(agreement)}, which SDMX-ML 2.1 output leaves out"


def test_a_class_left_out_says_which_class() -> None:
    value_list = ValueList(id="VL", maintainer="AG", version="1.0", name={"en": "VL"})
    (drop,) = dropped(StructureMessage(value_lists=[value_list]), "2.1")
    assert drop.reason == "SDMX-ML 2.1 has no ValueList"


def test_a_draft_named_through_an_item_no_scheme_owns_is_its_own_cause() -> None:
    """`_cause` falls back to the reference itself when no scheme class holds the item."""
    item = SdmxUrn.parse(f"urn:sdmx:org.sdmx.infomodel.codelist.ValueItem=AG:VL({DRAFT}).X")
    categorisation = Categorisation(
        id="CAT", maintainer="AG", version="1.0", name={"en": "CAT"}, source=Ref[object].unresolved(item)
    )
    (drop,) = dropped(StructureMessage(categorisations=[categorisation]), "2.1")
    assert drop.cause == str(item)


# ── a structure map 2.1 cannot name ───────────────────────────────────────


def test_a_component_map_without_a_value_map_does_not_end_the_search() -> None:
    """A map with nothing to name is skipped, not taken as the answer for the rest."""
    elsewhere = Ref[object].unresolved(RepresentationMap.urn_for(agency="OTHER", id="RM", version="1.0"))
    structure_map = StructureMap(
        id="SM",
        maintainer="AG",
        version="1.0",
        name={"en": "SM"},
        maps=[
            ComponentMap(sources=["D"], targets=["D"]),
            ComponentMap(sources=["D"], targets=["D"], representation_map=elsewhere),
        ],
    )
    assert "maps[].representation_map" in _fields(structure_map, "2.1")


# ── annotation URLs 2.1 can keep ──────────────────────────────────────────


def _annotated(*urls: AnnotationURL) -> sl.Codelist:
    return sl.Codelist(
        id="CL", maintainer="AG", version="1.0", name={"en": "CL"}, annotations=[Annotation(id="A", urls=urls)]
    )


def test_one_unlocalised_url_is_what_2_1_carries() -> None:
    assert "annotations[].urls" not in _fields(_annotated(AnnotationURL(url="https://a.example")), "2.1")


def test_a_second_url_is_beyond_2_1() -> None:
    urls = (AnnotationURL(url="https://a.example"), AnnotationURL(url="https://b.example"))
    assert "annotations[].urls" in _fields(_annotated(*urls), "2.1")


# ── what the register says, exactly ───────────────────────────────────────


def test_declared_gaps_for_a_type_are_that_type_s_rows() -> None:
    rows = declared_gaps(Hierarchy)
    assert rows
    assert {type_name for _target, type_name, _field, _reason in rows} == {"Hierarchy"}


@pytest.mark.parametrize(
    ("target", "prose"),
    [
        ("2.1", "SDMX-ML 2.1"),
        ("3.0", "SDMX-ML 3.0"),
        ("3.1", "SDMX-ML 3.1"),
        ("json", "SDMX-JSON"),
        ("storage", "the DuckDB store"),
    ],
)
def test_each_target_reads_as_its_name(target: str, prose: str) -> None:
    assert describe(target) == prose


def test_the_prose_covers_every_target() -> None:
    assert len({describe(target) for target in TARGETS}) == len(TARGETS)


@pytest.mark.parametrize("ask", [unrepresentable, dropped])
def test_an_unknown_target_names_the_known_ones(ask: object) -> None:
    assert callable(ask)
    with pytest.raises(ValueError, match=r"expected one of 2\.1, 3\.0, 3\.1, json, storage$"):
        ask(StructureMessage() if ask is dropped else _codelist(), "4.0")
