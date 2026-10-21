"""A draft is left out of SDMX-ML 2.1 output, with what references it (#420, #424).

2.1's version grammar has no form for an extension, so `1.0.0-draft` cannot be
written. Stripping the suffix would make a draft look final and could collide
with a real `1.0.0`, so the artefact is left out whole, and so is anything in
the message that would otherwise point at it. `sl.dropped(msg, "2.1")` names
each, with the draft that caused it.
"""

import functools
import re

import pytest
from lxml import etree

import sdmxlib as sl
from sdmxlib.formats.gaps import without_dropped
from sdmxlib.formats.sdmx_ml21 import reader as ml21_reader
from sdmxlib.formats.sdmx_ml21 import writer as ml21_writer
from sdmxlib.formats.sdmx_ml30 import reader as ml30_reader
from sdmxlib.model.concept import Concept
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure, Dimension
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.provision import ProvisionAgreement
from sdmxlib.model.ref import Ref, artefact_urn
from sdmxlib.model.urn import SdmxUrn
from tests.support.xsd import assert_valid_sdmx_ml, compile_structure_schema

pytestmark = pytest.mark.unit

DRAFT = "1.0.0-draft"


@functools.cache
def _schema() -> etree.XMLSchema:
    return compile_structure_schema("2.1")


def _codelist(id_: str = "CL", version: str = "1.0") -> sl.Codelist:
    return sl.Codelist(
        id=id_, maintainer="AG", version=version, name={"en": id_}, codes=[sl.Code(id="A", name={"en": "a"})]
    )


def _dsd(version: str = DRAFT) -> DataStructure:
    return DataStructure(id="DSD", maintainer="AG", version=version, name={"en": "DSD"})


def _flow(dsd_version: str = DRAFT, *, id_: str = "DF") -> Dataflow:
    urn = DataStructure.urn_for(agency="AG", id="DSD", version=dsd_version)
    return Dataflow(
        id=id_, maintainer="AG", version="1.0", name={"en": id_}, structure=Ref[DataStructure].unresolved(urn)
    )


def _agreement(flow_id: str = "DF") -> ProvisionAgreement:
    urn = Dataflow.urn_for(agency="AG", id=flow_id, version="1.0")
    return ProvisionAgreement(
        id="PA", maintainer="AG", version="1.0", name={"en": "PA"}, dataflow=Ref[Dataflow].unresolved(urn)
    )


def _urn(artefact: sl.MaintainableArtefact) -> str:
    return str(artefact_urn(artefact))


# ── what is left out ──────────────────────────────────────────────────────


def test_a_draft_is_left_out_and_the_rest_is_written() -> None:
    draft, kept = _codelist("CL_DRAFT", DRAFT), _codelist()
    msg = StructureMessage(codelists=[kept, draft])

    (drop,) = sl.dropped(msg, "2.1")
    assert (drop.urn, drop.cause) == (_urn(draft), _urn(draft))
    assert "'1.0.0-draft' is a draft" in drop.reason

    xml = sl.serialize(msg, version="2.1")
    assert_valid_sdmx_ml(xml, _schema())
    assert ml21_reader.read(xml).codelists.ids() == ["CL"]


def test_an_artefact_referencing_a_draft_is_left_out_naming_it() -> None:
    msg = StructureMessage(data_structures=[_dsd()], dataflows=[_flow()])
    dsd, flow = sl.dropped(msg, "2.1")
    assert flow.urn == _urn(msg.dataflows[0])
    assert flow.cause == dsd.urn == _urn(msg.data_structures[0])
    assert f"it references {dsd.urn}" in flow.reason


def test_a_reference_to_a_draft_outside_the_message_is_left_out_too() -> None:
    """The reference itself has no 2.1 form, whether or not its target is here."""
    (drop,) = sl.dropped(StructureMessage(dataflows=[_flow()]), "2.1")
    assert drop.cause == str(DataStructure.urn_for(agency="AG", id="DSD", version=DRAFT))


def test_the_cascade_follows_the_message_to_its_end() -> None:
    """PA → DF → DSD(draft): the agreement would point at a flow 2.1 left out."""
    msg = StructureMessage(data_structures=[_dsd()], dataflows=[_flow()], provision_agreements=[_agreement()])
    by_urn = {drop.urn: drop for drop in sl.dropped(msg, "2.1")}
    agreement = by_urn[_urn(msg.provision_agreements[0])]
    assert agreement.cause == _urn(msg.data_structures[0]), "the draft, not the flow between"
    assert f"it references {_urn(msg.dataflows[0])}" in agreement.reason
    assert len(by_urn) == 3


def test_an_item_reference_into_a_draft_scheme_names_the_scheme() -> None:
    """A DSD's concept lives in a draft scheme: the DSD goes, and its flow after it."""
    concept = SdmxUrn.make(
        Concept, package="conceptscheme", artefact_class="Concept", agency="AG", id="CS", version=DRAFT, item_id="FREQ"
    )
    dsd = DataStructure(
        id="DSD",
        maintainer="AG",
        version="1.0",
        name={"en": "DSD"},
        dimensions=[Dimension(id="FREQ", concept=Ref[Concept].unresolved(concept))],
    )
    msg = StructureMessage(data_structures=[dsd], dataflows=[_flow("1.0")])
    dsd_drop, flow_drop = sl.dropped(msg, "2.1")
    assert dsd_drop.cause == "urn:sdmx:org.sdmx.infomodel.conceptscheme.ConceptScheme=AG:CS(1.0.0-draft)"
    assert flow_drop.cause == dsd_drop.cause


def test_a_reference_to_an_artefact_not_in_the_message_is_left_alone() -> None:
    """Whether DF(1.0) exists is not this write's to say: only a left-out one cascades."""
    msg = StructureMessage(provision_agreements=[_agreement()], codelists=[_codelist("CL_DRAFT", DRAFT)])
    assert [drop.urn for drop in sl.dropped(msg, "2.1")] == [_urn(msg.codelists[0])]


def test_dropped_lists_in_message_order() -> None:
    msg = StructureMessage(codelists=[_codelist("CL_B", DRAFT), _codelist(), _codelist("CL_A", DRAFT)])
    assert [drop.urn for drop in sl.dropped(msg, "2.1")] == [_urn(msg.codelists[0]), _urn(msg.codelists[2])]


# ── only 2.1, and only drafts ─────────────────────────────────────────────


@pytest.mark.parametrize("target", ["3.0", "3.1", "json", "storage"])
def test_no_other_target_leaves_anything_out(target: str) -> None:
    assert sl.dropped(StructureMessage(data_structures=[_dsd()], dataflows=[_flow()]), target) == ()


def test_a_draft_still_writes_to_3_0() -> None:
    xml = sl.serialize(StructureMessage(codelists=[_codelist("CL", DRAFT)]), version="3.0")
    assert ml30_reader.read(xml).codelists["CL"].version == DRAFT


def test_an_unknown_target_is_refused() -> None:
    with pytest.raises(ValueError, match=re.escape("unknown target '4.0'")):
        sl.dropped(StructureMessage(), "4.0")


def test_a_message_without_drafts_is_written_as_given() -> None:
    msg = StructureMessage(codelists=[_codelist()], data_structures=[_dsd("1.0")], dataflows=[_flow("1.0")])
    assert sl.dropped(msg, "2.1") == ()
    assert without_dropped(msg, "2.1") is msg


def test_a_wildcard_reference_is_still_refused_not_left_out() -> None:
    """Not a draft: late binding has no 2.1 form either, and #422 decides it."""
    with pytest.raises(sl.ValidationError, match=re.escape("Dataflow:DF.structure")):
        sl.serialize(StructureMessage(dataflows=[_flow("1.0+.0")]), version="2.1")


# ── every way of writing 2.1 agrees ───────────────────────────────────────


def test_the_2_1_writer_called_directly_leaves_drafts_out_too() -> None:
    msg = StructureMessage(codelists=[_codelist(), _codelist("CL_DRAFT", DRAFT)])
    xml = ml21_writer.write(msg)
    assert_valid_sdmx_ml(xml, _schema())
    assert ml21_reader.read(xml).codelists.ids() == ["CL"]


def test_an_unvalidated_serialize_leaves_drafts_out_too() -> None:
    msg = StructureMessage(codelists=[_codelist(), _codelist("CL_DRAFT", DRAFT)])
    assert ml21_reader.read(sl.serialize(msg, version="2.1", validate=False)).codelists.ids() == ["CL"]


def test_the_caller_s_message_is_untouched() -> None:
    msg = StructureMessage(codelists=[_codelist(), _codelist("CL_DRAFT", DRAFT)])
    sl.serialize(msg, version="2.1")
    assert msg.codelists.ids() == ["CL", "CL_DRAFT"]


# ── asked of one artefact ─────────────────────────────────────────────────


def test_unrepresentable_reports_a_draft_and_a_reference_to_one() -> None:
    assert [gap.field for gap in sl.unrepresentable(_dsd(), "2.1")] == ["version"]
    assert [gap.field for gap in sl.unrepresentable(_flow(), "2.1")] == ["references"]
    assert sl.unrepresentable(_flow("1.0"), "2.1") == ()
    assert sl.unrepresentable(_dsd(), "3.0") == ()
