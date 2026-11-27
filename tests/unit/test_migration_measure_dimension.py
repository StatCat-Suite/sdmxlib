"""The MeasureDimension remodels: a dimension enumerated by a codelist, or one measure per concept (GH-429)."""

import attrs
import pytest

import sdmxlib as sl
from sdmxlib.migration import Plan, Question, Target
from tests.support.roundtrip import structure_schema
from tests.support.xsd import assert_valid_sdmx_ml

pytestmark = pytest.mark.unit

_TARGET = Target(version="3.0")
_POLICY = {"version.non-final": "final"}


def _concepts() -> sl.ConceptScheme:
    return sl.ConceptScheme(
        id="CS",
        maintainer="AG",
        version="1.0",
        name={"en": "concepts"},
        concepts=[sl.Concept(id=c, name={"en": c}) for c in ("GEO", "MEASURE", "UNIT", "OBS_VALUE")],
    )


def _measures() -> sl.ConceptScheme:
    return sl.ConceptScheme(
        id="CS_MEASURES",
        maintainer="AG",
        version="1.0",
        name={"en": "measures"},
        concepts=[sl.Concept(id=c, name={"en": c.lower()}) for c in ("CO2", "CH4")],
    )


def _codes() -> sl.Codelist:
    return sl.Codelist(
        id="CL_GEO", maintainer="AG", version="1.0", name={"en": "geo"}, codes=[sl.Code(id="CA", name={"en": "ca"})]
    )


def _message(*, with_scheme: bool = True) -> sl.StructureMessage:
    concepts = _concepts()
    dsd = sl.DataStructure(
        id="GHG",
        maintainer="AG",
        version="1.0",
        name={"en": "ghg"},
        dimensions=[sl.Dimension(id="GEO", concept=concepts.ref_to("GEO"), representation=sl.Ref.to(_codes()))],
        measure_dimension=sl.MeasureDimension(
            id="MEASURE", concept=concepts.ref_to("MEASURE"), representation=sl.Ref.to(_measures())
        ),
        attributes=[
            sl.Attribute(
                id="UNIT",
                concept=concepts.ref_to("UNIT"),
                attachment=sl.AttachmentLevel.SERIES,
                related_dimensions=["GEO", "MEASURE"],
            )
        ],
        measures=[
            sl.Measure(id="OBS_VALUE", concept=concepts.ref_to("OBS_VALUE"), representation=sl.Facet(type="Double"))
        ],
    )
    flow = sl.Dataflow(id="DF", maintainer="AG", version="1.0", name={"en": "df"}, structure=sl.Ref.to(dsd))
    constraint = sl.DataConstraint(
        id="CC",
        maintainer="AG",
        version="1.0",
        name={"en": "cc"},
        attachment=sl.ConstraintAttachment(dataflows=[sl.Ref.to(flow)]),
        regions=[
            sl.CubeRegion(
                keys=[
                    sl.KeyValue(dimension_id="GEO", values=["CA"]),
                    sl.KeyValue(dimension_id="MEASURE", values=["CO2"]),
                ]
            )
        ],
    )
    return sl.StructureMessage(
        concept_schemes=[concepts, *([_measures()] if with_scheme else [])],
        codelists=[_codes()],
        data_structures=[dsd],
        dataflows=[flow],
        constraints=[constraint],
    )


def _question(plan: Plan) -> Question:
    (question,) = [q for q in plan.questions if q.rule == "structure.measure-dimension"]
    return question


def test_the_measure_dimension_is_a_question_recommending_a_dimension():
    plan = sl.migration.plan(_message(), target=_TARGET, policy=_POLICY)
    question = _question(plan)
    assert question.recommended == "dimension"
    assert [o.id for o in question.options] == ["dimension", "measures"]
    assert [b for b in plan.blocked if b.rule == "structure.measure-dimension"] == []


def test_as_a_dimension_it_is_enumerated_by_a_codelist_of_its_concepts():
    plan = sl.migration.plan(_message(), target=_TARGET, policy=_POLICY)
    migrated = plan.apply({_question(plan).key: "dimension"})
    (dsd,) = migrated.data_structures
    assert dsd.measure_dimension is None
    assert dsd.dimensions.ids() == ["GEO", "MEASURE"]
    codelist = dsd.dimensions["MEASURE"].codelist
    assert (codelist.id, codelist.version, codelist.codes.ids()) == ("CL_CS_MEASURES", "1.0.0", ["CO2", "CH4"])
    assert codelist.codes["CO2"].name["en"].text == "co2"
    assert dsd.version == "1.0.0"
    (constraint,) = migrated.constraints
    assert [key.dimension_id for key in constraint.regions[0].keys] == ["GEO", "MEASURE"]
    assert_valid_sdmx_ml(sl.to_xml(migrated, version="3.0"), structure_schema("3.0"))


def test_as_measures_each_concept_is_a_measure_and_the_constraint_loses_its_key():
    plan = sl.migration.plan(_message(), target=_TARGET, policy=_POLICY)
    migrated = plan.apply({_question(plan).key: "measures"})
    (dsd,) = migrated.data_structures
    assert dsd.measure_dimension is None
    assert dsd.dimensions.ids() == ["GEO"]
    assert dsd.measures.ids() == ["CO2", "CH4"]
    assert dsd.measures["CO2"].representation == sl.Facet(type="Double")
    assert str(dsd.measures["CO2"].concept.urn if dsd.measures["CO2"].concept else None).endswith(
        "Concept=AG:CS_MEASURES(1.0.0).CO2"
    )
    assert dsd.attributes["UNIT"].related_dimensions == ["GEO"]
    (constraint,) = migrated.constraints
    assert [key.dimension_id for key in constraint.regions[0].keys] == ["GEO"]
    assert [c.id for c in migrated.codelists] == ["CL_GEO"]
    assert_valid_sdmx_ml(sl.to_xml(migrated, version="3.0"), structure_schema("3.0"))


def test_without_its_concept_scheme_it_is_blocked():
    plan = sl.migration.plan(_message(with_scheme=False), target=_TARGET, policy=_POLICY)
    (blocked,) = [b for b in plan.blocked if b.rule == "structure.measure-dimension"]
    assert "whose concept scheme is not in the message" in blocked.reason
    assert [q for q in plan.questions if q.rule == "structure.measure-dimension"] == []


def test_a_codelist_id_already_taken_is_blocked():
    msg = _message()
    taken = sl.Codelist(
        id="CL_CS_MEASURES",
        maintainer="AG",
        version="1.0.0",
        name={"en": "t"},
        codes=[sl.Code(id="X", name={"en": "x"})],
    )
    held = attrs.evolve(msg, header=msg.header, codelists=[*msg.codelists, taken])
    (blocked,) = [b for b in sl.migration.plan(held, target=_TARGET).blocked if b.rule == "structure.measure-dimension"]
    assert "which the message already holds at a version" in blocked.reason


def test_an_omitted_dsd_is_not_put_back_by_the_remodel():
    plan = sl.migration.plan(_message(), target=_TARGET)
    answers = {q.key: q.recommended or "omit" for q in plan.questions}
    dsd_key = next(q.key for q in plan.questions if q.rule == "version.non-final" and "DataStructure=" in q.urn)
    answers[dsd_key] = "omit"
    assert list(plan.apply(answers, partial=True).data_structures) == []
