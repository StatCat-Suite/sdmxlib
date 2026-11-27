"""`sl.migration`'s structure rules: what the 3.x format cannot carry (GH-399)."""

import collections

import attrs
import pytest

import sdmxlib as sl
from sdmxlib.migration import Plan, Target
from sdmxlib.migration._plan import QUESTIONS
from tests.support import repo
from tests.support.roundtrip import structure_schema
from tests.support.xsd import assert_valid_sdmx_ml

pytestmark = pytest.mark.unit

_FIXTURES = repo.ROOT / "tests" / "fixtures" / "sdmx_ml21"
_TARGET = Target(version="3.0")
_POLICY = {"version.non-final": "draft", "format.loss": "accept", "version.external": "final"}


def _read(name: str) -> sl.StructureMessage:
    msg = sl.read(_FIXTURES / name)
    assert isinstance(msg, sl.StructureMessage)
    return msg


def _structure(plan: Plan) -> collections.Counter[str]:
    """The plan's findings from the structure rules, by rule."""
    found = (*plan.changes, *plan.questions, *plan.blocked)
    return collections.Counter(f.rule for f in found if not f.rule.startswith("version."))


def _classes(msg: sl.StructureMessage) -> collections.Counter[str]:
    return collections.Counter(type(a).__name__ for a in msg.all_artefacts())


# ── Classes the target lacks or converts ───────────────────────────────────


def test_a_class_the_target_lacks_is_left_out_and_asks_nothing_else():
    plan = sl.migration.plan(_read("complete.xml"), target=_TARGET)
    (dropped,) = [c for c in plan.changes if c.rule == "format.dropped"]
    assert "StructureSet=TEST:SS_TEST(1.0)" in dropped.urn
    assert [f for f in (*plan.changes, *plan.questions) if f.urn == dropped.urn] == [dropped]
    migrated = plan.apply(_answers(plan))
    assert "StructureSet" not in _classes(migrated)


def _answers(plan: Plan) -> dict[str, str]:
    """The policy's answer to each open question."""
    return {q.key: _POLICY[q.rule] for q in plan.open}


def test_a_converted_class_is_reported_and_kept():
    plan = sl.migration.plan(_read("minimal.xml"), target=_TARGET, policy=_POLICY)
    (converted,) = [c for c in plan.changes if c.rule == "format.converted"]
    assert "AttachmentConstraint" in converted.urn
    assert converted.edits == ()
    assert _classes(plan.apply())["AttachmentConstraint"] == 1


# ── Field losses ───────────────────────────────────────────────────────────


def test_a_field_the_target_cannot_carry_is_a_question_recommending_accept():
    plan = sl.migration.plan(_read("complete.xml"), target=_TARGET)
    (loss,) = [q for q in plan.questions if q.rule == "format.loss"]
    assert "reference_period (SDMX 3.0 removed ReferencePeriod" in loss.question
    assert loss.recommended == "accept"


@pytest.mark.parametrize(("choice", "held"), [("accept", 1), ("omit", 0)])
def test_the_loss_answer_keeps_or_omits_the_artefact(choice: str, held: int):
    plan = sl.migration.plan(_read("complete.xml"), target=_TARGET, policy={"version.non-final": "draft"})
    (loss,) = [q for q in plan.questions if q.rule == "format.loss"]
    migrated = plan.apply({loss.key: choice})
    assert len([c for c in migrated.constraints if loss.urn.endswith(f"{c.id}(1.0)")]) == held


# ── The MSD remodel ────────────────────────────────────────────────────────


def _with_flow(msg: sl.StructureMessage, msd: sl.SdmxUrn[object]) -> sl.StructureMessage:
    flow = sl.Metadataflow(
        id="MDF",
        maintainer="TEST",
        name={"en": "mdf"},
        structure=sl.Ref.unresolved(sl.MetadataStructure.parse_urn(str(msd))),
    )
    return attrs.evolve(msg, metadataflows=[flow])


def test_a_metadataflow_takes_the_targets_its_2_1_msd_implies():
    msg = _read("with_metadata_structure.xml")
    msg = _with_flow(msg, sl.SdmxUrn.parse(str(sl.artefact_urn(msg.metadata_structures[0]))))
    plan = sl.migration.plan(msg, target=_TARGET, policy=_POLICY)
    assert _structure(plan) == {"metadataflow.targets": 1, "msd.target-layer": 1}
    (targets,) = [c for c in plan.changes if c.rule == "metadataflow.targets"]
    assert targets.after == "urn:sdmx:org.sdmx.infomodel.datastructure.Dataflow=*:*(*)"

    migrated = plan.apply()
    assert migrated.metadataflows[0].targets == ("urn:sdmx:org.sdmx.infomodel.datastructure.Dataflow=*:*(*)",)
    assert_valid_sdmx_ml(sl.to_xml(migrated, version="3.0"), structure_schema("3.0"))


def test_a_metadataflow_whose_targets_cannot_be_derived_is_blocked():
    gone = sl.MetadataStructure.urn_for(agency="TEST", id="MSD_GONE", version="1.0")
    plan = sl.migration.plan(_with_flow(_read("with_metadata_structure.xml"), gone), target=_TARGET)
    (blocked,) = [b for b in plan.blocked if b.rule == "metadataflow.targets"]
    assert "is neither resolved nor in the message" in blocked.reason


# ── MeasureDimension ───────────────────────────────────────────────────────


def _with_measure_dimension(msg: sl.StructureMessage) -> sl.StructureMessage:
    (dsd,) = msg.data_structures
    concepts = sl.ConceptScheme.urn_for(agency="TEST", id="CS_MEASURES", version="1.0")
    concept = sl.Concept.parse_urn("urn:sdmx:org.sdmx.infomodel.conceptscheme.Concept=TEST:CS_MEASURES(1.0).MEASURE")
    measure = sl.MeasureDimension(
        id="MEASURE",
        concept=sl.Ref[sl.Concept].unresolved(concept),
        representation=sl.Ref[sl.ConceptScheme].unresolved(concepts),
    )
    return attrs.evolve(msg, data_structures=[attrs.evolve(dsd, measure_dimension=measure)])


def test_a_measure_dimension_whose_concept_scheme_is_missing_is_blocked():
    plan = sl.migration.plan(_with_measure_dimension(_read("complete.xml")), target=_TARGET)
    (blocked,) = [b for b in plan.blocked if b.rule == "structure.measure-dimension"]
    assert "SDMX 3.x has no MeasureDimension, and DSD_TEST has MEASURE" in blocked.reason


# ── Every rule ─────────────────────────────────────────────────────────────


def test_every_structure_rule_fires():
    """Exact, so a rule added without a case here, or one that stops firing, fails."""
    msd_msg = _read("with_metadata_structure.xml")
    msd = sl.SdmxUrn.parse(str(sl.artefact_urn(msd_msg.metadata_structures[0])))
    minimal = _with_measure_dimension(_read("minimal.xml"))
    msg = attrs.evolve(
        minimal,
        metadata_structures=msd_msg.metadata_structures,
        metadataflows=_with_flow(msd_msg, msd).metadataflows,
        header=minimal.header,
    )
    assert _structure(sl.migration.plan(msg, target=_TARGET)) == {
        "format.dropped": 1,
        "format.converted": 1,
        "format.loss": 1,
        "metadataflow.targets": 1,
        "msd.target-layer": 1,
        "structure.measure-dimension": 1,
    }


def test_the_loss_question_takes_the_declared_options():
    plan = sl.migration.plan(_read("complete.xml"), target=_TARGET)
    (loss,) = [q for q in plan.questions if q.rule == "format.loss"]
    assert tuple(o.id for o in loss.options) == QUESTIONS["format.loss"]
