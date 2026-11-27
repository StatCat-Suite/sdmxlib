"""The hierarchy rule: each hierarchy an HCL carried is bound by a HierarchyAssociation (GH-428)."""

import attrs
import pytest

import sdmxlib as sl
from sdmxlib.migration import Plan, Target
from tests.support import repo
from tests.support.roundtrip import structure_schema
from tests.support.xsd import assert_valid_sdmx_ml

pytestmark = pytest.mark.unit

_TARGET = Target(version="3.0")
_POLICY = {"version.non-final": "final"}


def _hcl_message() -> sl.StructureMessage:
    """with_hierarchy.xml: HCL_GEO holding one hierarchy over TEST:CL_AREA(1.0)."""
    msg = sl.read(repo.ROOT / "tests" / "fixtures" / "sdmx_ml21" / "with_hierarchy.xml")
    assert isinstance(msg, sl.StructureMessage)
    return msg


def _concepts() -> sl.ConceptScheme:
    return sl.ConceptScheme(
        id="CS",
        maintainer="TEST",
        version="1.0",
        name={"en": "cs"},
        concepts=[sl.Concept(id=c, name={"en": c}) for c in ("REF_AREA", "COUNTERPART")],
    )


def _dsd(id_: str, *dimension_ids: str, version: str = "1.0") -> sl.DataStructure:
    (codelist,) = _hcl_message().codelists
    return sl.DataStructure(
        id=id_,
        maintainer="TEST",
        version=version,
        name={"en": id_},
        dimensions=[
            sl.Dimension(id=d, concept=_concepts().ref_to(d), representation=sl.Ref.to(codelist)) for d in dimension_ids
        ],
    )


def _with(*structures: sl.DataStructure) -> sl.StructureMessage:
    msg = _hcl_message()
    return attrs.evolve(msg, header=msg.header, concept_schemes=[_concepts()], data_structures=list(structures))


def _findings(plan: Plan) -> list[sl.migration.Change | sl.migration.Question]:
    return [f for f in (*plan.changes, *plan.questions) if f.rule == "hierarchy.association"]


def test_one_dimension_is_bound_by_a_change_and_a_consumer_finds_the_hierarchy():
    plan = sl.migration.plan(_with(_dsd("DSD", "REF_AREA")), target=_TARGET, policy=_POLICY)
    (change,) = _findings(plan)
    assert isinstance(change, sl.migration.Change)
    assert change.after == "urn:sdmx:org.sdmx.infomodel.codelist.HierarchyAssociation=TEST:HCL_GEO@DSD@REF_AREA(1.0)"
    migrated = plan.apply()
    (association,) = migrated.hierarchy_associations
    (hierarchy,) = migrated.hierarchies
    # The references followed the version moves to 1.0.0.
    assert association.linked_hierarchy is not None
    assert association.linked_hierarchy.urn == hierarchy.urn
    assert str(association.linked_object.urn if association.linked_object else None).endswith(
        "Dimension=TEST:DSD(1.0.0).REF_AREA"
    )
    (dsd,) = migrated.data_structures
    flow = sl.Dataflow(id="DF", maintainer="TEST", version="1.0.0", name={"en": "df"}, structure=sl.Ref.to(dsd))
    registry = sl.InMemoryRegistry()
    registry.add_all([*migrated.all_artefacts(), flow])
    found = registry.unique_hierarchy(flow, "REF_AREA")
    assert found is not None
    assert found.urn == hierarchy.urn
    assert_valid_sdmx_ml(sl.to_xml(migrated, version="3.0"), structure_schema("3.0"))


def test_several_dimensions_are_a_question_bound_to_each_or_none():
    msg = _with(_dsd("DSD", "REF_AREA", "COUNTERPART"), _dsd("DSD", "REF_AREA", version="1.1"))
    plan = sl.migration.plan(msg, target=_TARGET, policy=_POLICY)
    (question,) = _findings(plan)
    assert isinstance(question, sl.migration.Question)
    assert question.recommended == "each"
    each = plan.apply({question.key: "each"})
    assert sorted(str(a.urn).rsplit("=", 1)[1] for a in each.hierarchy_associations) == [
        "TEST:HCL_GEO@DSD@COUNTERPART(1.0.0)",
        "TEST:HCL_GEO@DSD@REF_AREA(1.0.0)",
        "TEST:HCL_GEO@DSD@REF_AREA(1.1.0)",
    ]
    assert list(plan.apply({question.key: "none"}).hierarchy_associations) == []


def test_no_dimension_leaves_it_unbound_with_a_change_saying_so():
    plan = sl.migration.plan(_hcl_message(), target=_TARGET, policy=_POLICY)
    (change,) = _findings(plan)
    assert isinstance(change, sl.migration.Change)
    assert change.edits == ()
    assert "no dimension in the message is enumerated by" in change.reason
    assert list(plan.apply().hierarchy_associations) == []


def test_a_hierarchy_already_linked_asks_nothing():
    msg = _with(_dsd("DSD", "REF_AREA"))
    (hierarchy,) = msg.hierarchies
    linked = sl.HierarchyAssociation(
        id="HA", maintainer="TEST", version="1.0.0", name={"en": "ha"}, linked_hierarchy=sl.Ref.to(hierarchy)
    )
    held = attrs.evolve(msg, header=msg.header, hierarchy_associations=[linked])
    assert _findings(sl.migration.plan(held, target=_TARGET, policy=_POLICY)) == []


def test_a_hierarchy_no_hcl_carried_asks_nothing():
    """Discrimination: the rule is about the 2.1 containers, not every hierarchy."""
    msg = _with(_dsd("DSD", "REF_AREA"))
    bare = attrs.evolve(msg, header=msg.header, hierarchical_codelists=[])
    assert _findings(sl.migration.plan(bare, target=_TARGET, policy=_POLICY)) == []


def test_the_association_of_a_draft_dsd_is_a_draft():
    plan = sl.migration.plan(_with(_dsd("DSD", "REF_AREA")), target=_TARGET, policy={"version.non-final": "draft"})
    (association,) = plan.apply().hierarchy_associations
    (dsd,) = plan.apply().data_structures
    assert (association.version, dsd.version) == ("1.0.0-draft", "1.0.0-draft")
