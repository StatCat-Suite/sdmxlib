"""Unit tests for SDMX-ML 2.1 reader."""

from pathlib import Path

import pytest

import sdmxlib as sl
from sdmxlib.formats import parse
from sdmxlib.formats.sdmx_ml21.reader import read
from sdmxlib.formats.sdmx_ml21.writer import write
from sdmxlib.model.codelist import Codelist
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import AttachmentLevel, MeasureDimension
from sdmxlib.model.ref import Ref
from sdmxlib.model.representation import Facet
from tests.support.factories import concept_ref
from tests.support.narrow import present
from tests.support.roundtrip import as_structure

pytestmark = pytest.mark.unit

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "sdmx_ml21"


def _minimal() -> bytes:
    return (_FIXTURES / "minimal.xml").read_bytes()


# ── as_structure(parse()) auto-detection ────────────────────────────────────────────────────


def test_parse_detects_21():
    msg = as_structure(parse(_minimal()))
    assert msg.codelists


def test_parse_explicit_version():
    msg = as_structure(parse(_minimal(), version="2.1"))
    assert msg.codelists


def test_parse_unsupported_version():
    with pytest.raises(ValueError, match="Unsupported"):
        as_structure(parse(_minimal(), version="4.0"))


def test_parse_undetectable():
    with pytest.raises(ValueError, match="Cannot detect"):
        as_structure(parse(b"<notsdmx/>"))


# ── Codelists ─────────────────────────────────────────────────────────────────


def test_codelists_parsed():
    msg = read(_minimal())
    assert len(msg.codelists) == 1
    cl = msg.codelists["CL_FREQ"]
    assert cl.id == "CL_FREQ"
    assert cl.agency_id == "SDMX"
    assert cl.version == "1.0"
    assert cl.legacy_is_final is True


def test_codelist_names_multilingual():
    msg = read(_minimal())
    cl = msg.codelists["CL_FREQ"]
    assert cl.name["en"] == "Frequency"
    assert cl.name["fr"] == "Fréquence"


def test_codelist_codes():
    msg = read(_minimal())
    cl = msg.codelists["CL_FREQ"]
    assert cl.codes.ids() == ["A", "Q", "M", "Q1"]
    assert cl.codes["A"].name["en"] == "Annual"
    assert cl.codes["Q1"].name["en"] == "Q1 sub-code"


def test_codelist_hierarchy():
    msg = read(_minimal())
    cl = msg.codelists["CL_FREQ"]
    assert cl.codes["Q1"].parent_id == "Q"
    assert cl.codes["A"].parent_id is None


def test_codelist_roots():
    msg = read(_minimal())
    cl = msg.codelists["CL_FREQ"]
    root_ids = cl.roots().ids()
    assert "Q1" not in root_ids
    assert "A" in root_ids


def test_codelist_narrower():
    msg = read(_minimal())
    cl = msg.codelists["CL_FREQ"]
    children = cl.narrower("Q")
    assert children.ids() == ["Q1"]


# ── ConceptSchemes ────────────────────────────────────────────────────────────


def test_concept_schemes_parsed():
    msg = read(_minimal())
    assert len(msg.concept_schemes) == 1
    cs = msg.concept_schemes["CROSS_DOMAIN_CONCEPTS"]
    assert cs.agency_id == "SDMX"
    assert cs.version == "1.0"


def test_concept_scheme_concepts():
    msg = read(_minimal())
    cs = msg.concept_schemes["CROSS_DOMAIN_CONCEPTS"]
    assert "FREQ" in cs.concepts


def test_concept_core_representation_parsed():
    msg = read(_minimal())
    cs = msg.concept_schemes["CROSS_DOMAIN_CONCEPTS"]
    freq = cs.concepts["FREQ"]
    assert isinstance(freq.core_representation, Ref)
    assert freq.core_representation.id == "CL_FREQ"


def test_concept_core_representation_roundtrip():
    msg = read(_minimal())
    rt = read(write(msg))
    freq = rt.concept_schemes["CROSS_DOMAIN_CONCEPTS"].concepts["FREQ"]
    assert isinstance(freq.core_representation, Ref)
    assert freq.core_representation.id == "CL_FREQ"


# ── DataStructures ────────────────────────────────────────────────────────────


def test_data_structure_parsed():
    msg = read(_minimal())
    assert len(msg.data_structures) == 1
    dsd = msg.data_structures["DSD_TEST"]
    assert dsd.id == "DSD_TEST"
    assert dsd.agency_id == "TEST"
    assert dsd.legacy_is_final is True


def test_dsd_name():
    msg = read(_minimal())
    dsd = msg.data_structures["DSD_TEST"]
    assert dsd.name["en"] == "Test Data Structure"


def test_dsd_dimensions():
    msg = read(_minimal())
    dsd = msg.data_structures["DSD_TEST"]
    assert dsd.dimensions.ids() == ["FREQ"]


def test_dsd_dimension_enumerated():
    msg = read(_minimal())
    dsd = msg.data_structures["DSD_TEST"]
    dim = dsd.dimensions["FREQ"]
    assert dim.is_enumerated
    assert isinstance(dim.representation, Ref)
    assert dim.representation.id == "CL_FREQ"


def test_dimension_inherits_core_representation():
    """A Dimension with no local representation inherits from concept's core."""
    msg = read(_minimal())
    # The minimal fixture's FREQ dimension has a local representation (CL_FREQ).
    # Build a new dimension that omits the local rep but wires the resolved concept.
    cs = msg.concept_schemes["CROSS_DOMAIN_CONCEPTS"]
    dim = sl.Dimension(id="FREQ", concept=cs.ref_to("FREQ"))
    assert isinstance(dim.representation, Ref)  # inherited from core
    assert dim.representation.id == "CL_FREQ"
    assert dim.is_enumerated


def test_dsd_dimension_codelist_resolved():
    msg = read(_minimal())
    dsd = msg.data_structures["DSD_TEST"]
    dim = dsd.dimensions["FREQ"]
    assert isinstance(dim.representation, Ref)
    assert dim.representation.resolved
    assert isinstance(dim.representation, Ref)
    cl = dim.codelist
    assert cl.id == "CL_FREQ"
    assert len(cl.codes) == 4


def test_dsd_dimension_concept_resolved():
    msg = read(_minimal())
    dsd = msg.data_structures["DSD_TEST"]
    dim = dsd.dimensions["FREQ"]
    assert dim.concept is not None
    assert dim.concept.resolved
    assert dim.concept().name["en"] == "Frequency"


def test_dsd_time_dimension():
    msg = read(_minimal())
    dsd = msg.data_structures["DSD_TEST"]
    assert dsd.time_dimension is not None
    assert dsd.time_dimension.id == "TIME_PERIOD"
    assert isinstance(dsd.time_dimension.representation, Facet)
    assert dsd.time_dimension.text_type == "ObservationalTimePeriod"


def test_dsd_time_dimension_concept_resolved():
    msg = read(_minimal())
    dsd = msg.data_structures["DSD_TEST"]
    td = dsd.time_dimension
    assert td is not None
    assert td.concept is not None
    assert td.concept.resolved
    assert td.concept().id == "TIME_PERIOD"


def test_dsd_attributes():
    msg = read(_minimal())
    dsd = msg.data_structures["DSD_TEST"]
    assert dsd.attributes.ids() == ["OBS_STATUS"]
    attr = dsd.attributes["OBS_STATUS"]
    assert attr.usage is sl.AttributeUsage.OPTIONAL
    assert attr.attachment == AttachmentLevel.OBSERVATION
    # And nothing more: 2.1's <PrimaryMeasure><Ref id="OBS_VALUE"/> restates a
    # constant the version already fixes, so it is not carried (#360).
    assert not hasattr(attr, "related_measure")


def test_dsd_attribute_text_format():
    msg = read(_minimal())
    dsd = msg.data_structures["DSD_TEST"]
    attr = dsd.attributes["OBS_STATUS"]
    assert isinstance(attr.representation, Facet)
    assert attr.representation.type == "String"
    assert attr.representation.max_length == 1


def test_dsd_measures():
    msg = read(_minimal())
    dsd = msg.data_structures["DSD_TEST"]
    assert dsd.measures.ids() == ["OBS_VALUE"]
    measure = dsd.measures["OBS_VALUE"]
    assert measure.concept is not None
    assert measure.concept.resolved
    assert measure.concept().name["en"] == "Observation Value"


def test_dsd_annotations():
    msg = read(_minimal())
    dsd = msg.data_structures["DSD_TEST"]
    assert "SOURCE" in dsd.annotations
    ann = dsd.annotations.first("SOURCE")
    assert ann is not None
    assert ann.text["en"] == "Test fixture"


def test_dsd_codes_shortcut():
    msg = read(_minimal())
    dsd = msg.data_structures["DSD_TEST"]
    codes = dsd.dimensions["FREQ"].codes
    assert codes is not None
    assert "A" in codes


def test_dsd_all_codelists():
    msg = read(_minimal())
    dsd = msg.data_structures["DSD_TEST"]
    clists = dsd.all_codelists
    assert "FREQ" in clists
    assert clists["FREQ"].id == "CL_FREQ"


def test_dsd_code_map():
    msg = read(_minimal())
    dsd = msg.data_structures["DSD_TEST"]
    code_map = dsd.code_map(lang="en")
    assert code_map["FREQ"]["A"] == "Annual"
    assert code_map["FREQ"]["Q"] == "Quarterly"


def test_dsd_column_labels():
    msg = read(_minimal())
    dsd = msg.data_structures["DSD_TEST"]
    labels = dsd.column_labels(lang="en")
    assert labels["FREQ"] == "Frequency"
    assert labels["OBS_VALUE"] == "Observation Value"


# ── MeasureDimension ──────────────────────────────────────────────────────────

_MEASURE_DIM_XML = b"""\
<?xml version="1.0" encoding="UTF-8"?>
<mes:Structure
  xmlns:mes="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message"
  xmlns:str="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure"
  xmlns:com="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common">
  <mes:Header>
    <mes:ID>TEST</mes:ID>
    <mes:Test>false</mes:Test>
    <mes:Prepared>2024-01-01T00:00:00</mes:Prepared>
    <mes:Sender id="TEST"/>
  </mes:Header>
  <mes:Structures>
    <str:OrganisationSchemes>
      <str:AgencyScheme agencyID="SDMX" id="AGENCIES" version="1.0">
        <com:Name xml:lang="en">Agencies</com:Name>
        <str:Agency id="TEST"><com:Name xml:lang="en">Test</com:Name></str:Agency>
      </str:AgencyScheme>
    </str:OrganisationSchemes>
    <str:Concepts>
      <str:ConceptScheme id="CS_INDICATOR" agencyID="TEST" version="1.0">
        <com:Name xml:lang="en">Indicator</com:Name>
        <str:Concept id="EMP"><com:Name xml:lang="en">Employment</com:Name></str:Concept>
        <str:Concept id="UNE"><com:Name xml:lang="en">Unemployment</com:Name></str:Concept>
      </str:ConceptScheme>
      <str:ConceptScheme id="CS_LFS" agencyID="TEST" version="1.0">
        <com:Name xml:lang="en">Labour force concepts</com:Name>
        <str:Concept id="REF_AREA"><com:Name xml:lang="en">Area</com:Name></str:Concept>
        <str:Concept id="INDICATOR"><com:Name xml:lang="en">Indicator</com:Name></str:Concept>
        <str:Concept id="TIME_PERIOD"><com:Name xml:lang="en">Time</com:Name></str:Concept>
        <str:Concept id="OBS_VALUE"><com:Name xml:lang="en">Value</com:Name></str:Concept>
      </str:ConceptScheme>
    </str:Concepts>
    <str:DataStructures>
      <str:DataStructure id="DSD_LFS" agencyID="TEST" version="1.0">
        <com:Name xml:lang="en">Labour Force Survey</com:Name>
        <str:DataStructureComponents>
          <str:DimensionList id="DimensionDescriptor">
            <str:Dimension id="REF_AREA" position="1">
              <str:ConceptIdentity>
                <Ref id="REF_AREA" maintainableParentID="CS_LFS" maintainableParentVersion="1.0" agencyID="TEST"/>
              </str:ConceptIdentity>
              <str:LocalRepresentation>
                <str:TextFormat textType="String"/>
              </str:LocalRepresentation>
            </str:Dimension>
            <str:MeasureDimension id="INDICATOR" position="2">
              <str:ConceptIdentity>
                <Ref id="INDICATOR" maintainableParentID="CS_LFS" maintainableParentVersion="1.0" agencyID="TEST"/>
              </str:ConceptIdentity>
              <str:LocalRepresentation>
                <str:Enumeration>
                  <Ref id="CS_INDICATOR" agencyID="TEST" version="1.0" package="conceptscheme" class="ConceptScheme"/>
                </str:Enumeration>
              </str:LocalRepresentation>
            </str:MeasureDimension>
            <str:TimeDimension id="TIME_PERIOD" position="3">
              <str:ConceptIdentity>
                <Ref id="TIME_PERIOD" maintainableParentID="CS_LFS" maintainableParentVersion="1.0" agencyID="TEST"/>
              </str:ConceptIdentity>
              <str:LocalRepresentation>
                <str:TextFormat textType="ObservationalTimePeriod"/>
              </str:LocalRepresentation>
            </str:TimeDimension>
          </str:DimensionList>
          <str:MeasureList id="MeasureDescriptor">
            <str:PrimaryMeasure id="OBS_VALUE">
              <str:ConceptIdentity>
                <Ref id="OBS_VALUE" maintainableParentID="CS_LFS" maintainableParentVersion="1.0" agencyID="TEST"/>
              </str:ConceptIdentity>
            </str:PrimaryMeasure>
          </str:MeasureList>
        </str:DataStructureComponents>
      </str:DataStructure>
    </str:DataStructures>
  </mes:Structures>
</mes:Structure>
"""


def test_measure_dimension_parsed():
    msg = read(_MEASURE_DIM_XML)
    dsd = msg.data_structures["DSD_LFS"]
    assert dsd.measure_dimension is not None
    assert isinstance(dsd.measure_dimension, MeasureDimension)
    assert dsd.measure_dimension.id == "INDICATOR"


def test_measure_dimension_enumerated():
    msg = read(_MEASURE_DIM_XML)
    dsd = msg.data_structures["DSD_LFS"]
    md = dsd.measure_dimension
    assert md is not None
    assert md.is_enumerated
    assert isinstance(md.representation, Ref)
    assert md.representation.resolved
    assert isinstance(md.representation, Ref)
    assert md.representation().id == "CS_INDICATOR"


def test_measure_dimension_concepts():
    """The measure dimension enumerates concepts, not codes."""
    msg = read(_MEASURE_DIM_XML)
    dsd = msg.data_structures["DSD_LFS"]
    md = dsd.measure_dimension
    assert md is not None
    concepts = md.concepts
    assert concepts is not None
    assert set(concepts.ids()) == {"EMP", "UNE"}
    assert md.concept_scheme.id == "CS_INDICATOR"


def test_measure_dimension_not_in_dimensions():
    """MeasureDimension is stored separately, not in the dimensions list."""
    msg = read(_MEASURE_DIM_XML)
    dsd = msg.data_structures["DSD_LFS"]
    assert "INDICATOR" not in dsd.dimensions.ids()


def test_measure_dimension_in_components():
    msg = read(_MEASURE_DIM_XML)
    dsd = msg.data_structures["DSD_LFS"]
    assert "INDICATOR" in dsd.components.ids()


def test_no_measure_dimension_when_absent():
    msg = read(_minimal())
    dsd = msg.data_structures["DSD_TEST"]
    assert dsd.measure_dimension is None


# ── Dataflows ─────────────────────────────────────────────────────────────────


def test_dataflows_parsed():
    msg = read(_minimal())
    assert len(msg.dataflows) == 1
    df = msg.dataflows["DF_TEST"]
    assert df.id == "DF_TEST"
    assert df.agency_id == "TEST"
    assert df.name["en"] == "Test Dataflow"


def test_dataflow_structure_resolved():
    msg = read(_minimal())
    df = msg.dataflows["DF_TEST"]
    assert df.structure is not None
    assert df.structure.resolved
    dsd = df.structure()
    assert dsd.id == "DSD_TEST"


# ── CategorySchemes ───────────────────────────────────────────────────────────


def test_category_schemes_parsed():
    msg = read(_minimal())
    assert len(msg.category_schemes) == 1
    cs = msg.category_schemes["CS_TEST"]
    assert cs.name["en"] == "Test Category Scheme"


def test_category_scheme_hierarchy():
    msg = read(_minimal())
    cs = msg.category_schemes["CS_TEST"]
    assert "CAT_A" in cs.categories
    cat_a = cs.categories["CAT_A"]
    assert "CAT_A1" in cat_a.categories
    assert cat_a.categories["CAT_A1"].name["en"] == "Category A1"


# ── Categorisations ───────────────────────────────────────────────────────────


def test_categorisations_parsed():
    msg = read(_minimal())
    assert len(msg.categorisations) == 1
    cat = msg.categorisations["CAT_DF_TEST"]
    assert cat.name["en"] == "DF_TEST categorisation"


def test_categorisation_source_resolved():
    msg = read(_minimal())
    cat = msg.categorisations["CAT_DF_TEST"]
    assert cat.source is not None
    assert cat.source.resolved
    resolved = cat.source()
    assert isinstance(resolved, Dataflow)
    assert resolved.id == "DF_TEST"


# ── Structure mapping (top-level RepresentationMap, etc.) ─────────────────────
# SDMX-ML 2.1 wraps maps inside <str:StructureSets>; sdmxlib unwraps each map
# into a top-level maintainable on the StructureMessage. The 2.1 fixture's
# single codelist map becomes a top-level RepresentationMap.


def test_representation_maps_parsed():
    msg = read(_minimal())
    assert len(msg.representation_maps) == 1
    rm = msg.representation_maps["TEST:MAP_FREQ@SS_TEST"]
    # Named for the set it is in (#421), as `<id>@<set>`.
    assert rm.id == "MAP_FREQ@SS_TEST"
    assert rm.agency_id == "TEST"
    assert rm.name["en"] == "Frequency map"


def test_representation_map_source_resolved():
    msg = read(_minimal())
    rm = msg.representation_maps["TEST:MAP_FREQ@SS_TEST"]
    (source,) = rm.sources
    assert isinstance(source, Ref)
    assert source.resolved
    resolved = source()
    assert isinstance(resolved, Codelist)
    assert resolved.id == "CL_FREQ"


def test_representation_map_value_maps():
    msg = read(_minimal())
    rm = msg.representation_maps["TEST:MAP_FREQ@SS_TEST"]
    assert len(rm.mappings) == 2
    assert rm.translate("A") == "A"
    assert rm.translate("Q") == "Q"
    assert rm.translate("M") is None


def test_representation_map_roundtrip_via_its_structure_set():
    """The 2.1 writer puts the map back in its set as ``MAP_FREQ``; the reader names it again."""
    msg = read(_minimal())
    rt = read(write(msg))
    assert rt.representation_maps.ids() == msg.representation_maps.ids()
    rt_rm = rt.representation_maps["TEST:MAP_FREQ@SS_TEST"]
    assert rt_rm.id == "MAP_FREQ@SS_TEST"
    assert rt_rm.mappings == msg.representation_maps["TEST:MAP_FREQ@SS_TEST"].mappings
    assert [(m.sources[0].value, m.targets) for m in rt_rm.mappings] == [("A", ("A",)), ("Q", ("Q",))]


# ── Constraints ──────────────────────────────────────────────────────────────


def test_constraints_parsed():
    msg = read(_minimal())
    assert len(msg.constraints) == 1
    cc = msg.constraints["CC_TEST"]
    assert cc.id == "CC_TEST"
    assert cc.agency_id == "TEST"
    assert cc.constraint_type is sl.ConstraintType.ALLOWED


@pytest.mark.parametrize("bad", ['type=""', 'type="Nonsense"'])
def test_a_bad_constraint_type_is_a_conformance_error(bad: str):
    """A reader's contract is that `as_structure(parse())` raises only `SdmxParseError`.

    `ConstraintType.parse` raises a bare `ValueError`, which is right for a
    caller constructing the model and wrong at a format boundary — so the
    reader converted it to `SdmxConformanceError`, the subclass meaning "this
    document contradicts the schema". Found by the reader fuzzer with
    `type=""`, which slips past the `or` guarding the 3.0 spelling because an
    empty string is falsy.
    """
    from sdmxlib.model.validation import SdmxConformanceError

    payload = _minimal().replace(b'type="Allowed"', bad.encode())
    assert payload != _minimal(), "fixture no longer carries the attribute this test mutates"
    # `parse`, not the module reader: a reader refuses the one constraint and
    # reads on, and it is `parse` that fails the call over it (#435).
    with pytest.raises(SdmxConformanceError, match="expected Allowed or Actual"):
        parse(payload)


def test_a_constraint_without_type_is_actual_as_the_schema_defaults():
    """2.1's ``@type`` is optional with ``default="Actual"``; it was read as Allowed (#475).

    The two mean opposite things, what data may be reported and what data
    exists, so the misreading was silent and wrong. No swept registry omits the
    attribute, which is why nothing caught it.
    """
    payload = _minimal().replace(b' type="Allowed"', b"")
    assert payload != _minimal(), "fixture no longer carries the attribute this test removes"
    assert read(payload).constraints["CC_TEST"].constraint_type is sl.ConstraintType.ACTUAL


def test_constraint_name():
    msg = read(_minimal())
    cc = msg.constraints["CC_TEST"]
    assert cc.name["en"] == "Test Constraint"


def test_constraint_attachment_resolved():
    msg = read(_minimal())
    cc = msg.constraints["CC_TEST"]
    assert len(cc.attachment.dataflows) == 1
    assert cc.attachment.dataflows[0].resolved
    resolved = cc.attachment.dataflows[0]()
    assert isinstance(resolved, Dataflow)
    assert resolved.id == "DF_TEST"


def test_constraint_cube_region():
    msg = read(_minimal())
    cc = msg.constraints["CC_TEST"]
    assert len(cc.regions) == 1
    region = cc.regions[0]
    assert region.include is True
    assert len(region.keys) == 1
    kv = region.keys[0]
    assert kv.dimension_id == "FREQ"
    assert kv.codes() == frozenset({"A", "Q"})


def test_constraint_allows():
    msg = read(_minimal())
    cc = msg.constraints["CC_TEST"]
    assert cc.allows({"FREQ": "A"})
    assert not cc.allows({"FREQ": "M"})


def test_constraint_roundtrip():
    msg = read(_minimal())
    rt = read(write(msg))
    assert rt.constraints.ids() == msg.constraints.ids()
    orig_cc = msg.constraints["CC_TEST"]
    rt_cc = rt.constraints["CC_TEST"]
    assert rt_cc.constraint_type == orig_cc.constraint_type
    assert len(rt_cc.regions) == len(orig_cc.regions)


# ── Empty message ─────────────────────────────────────────────────────────────


def test_empty_structures_block():
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<mes:Structure
  xmlns:mes="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message"
  xmlns:str="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure"
  xmlns:com="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common">
  <mes:Header><mes:ID>X</mes:ID><mes:Test>false</mes:Test>
    <mes:Prepared>2024-01-01T00:00:00</mes:Prepared>
    <mes:Sender id="X"/></mes:Header>
  <mes:Structures/>
</mes:Structure>"""
    msg = read(xml)
    assert not msg.codelists
    assert not msg.data_structures
    assert not msg.dataflows


def test_no_structures_element():
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<mes:Structure
  xmlns:mes="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message">
  <mes:Header><mes:ID>X</mes:ID><mes:Test>false</mes:Test>
    <mes:Prepared>2024-01-01T00:00:00</mes:Prepared>
    <mes:Sender id="X"/></mes:Header>
</mes:Structure>"""
    msg = read(xml)
    assert not msg.codelists


# ── ArtefactList lookup ───────────────────────────────────────────────────────


def test_artefact_list_by_urn():
    msg = read(_minimal())
    urn = Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")
    cl = msg.codelists[urn]
    assert cl.id == "CL_FREQ"


def test_artefact_list_by_qualified_key():
    msg = read(_minimal())
    dsd = msg.data_structures["TEST:DSD_TEST(1.0)"]
    assert dsd.id == "DSD_TEST"


# ── OrganisationSchemes ───────────────────────────────────────────────────────


def test_agency_schemes_parsed():
    msg = read(_minimal())
    assert len(msg.agency_schemes) == 1
    scheme = msg.agency_schemes[0]
    assert scheme.id == "AGENCIES"
    assert scheme.agency_id == "SDMX"


def test_agency_scheme_agencies():
    msg = read(_minimal())
    scheme = msg.agency_schemes[0]
    assert scheme.agencies.ids() == ["SDMX", "TEST"]
    assert scheme.agencies["SDMX"].name["en"] == "SDMX"
    assert scheme.agencies["TEST"].name["en"] == "Test Agency"


def test_agency_scheme_roundtrip():
    msg = read(_minimal())
    rt = read(write(msg))
    assert rt.agency_schemes.ids() == msg.agency_schemes.ids()
    orig_scheme = msg.agency_schemes[0]
    rt_scheme = rt.agency_schemes[0]
    assert rt_scheme.agencies.ids() == orig_scheme.agencies.ids()
    assert rt_scheme.agencies["SDMX"].name["en"] == orig_scheme.agencies["SDMX"].name["en"]


# ── HierarchicalCodelist (with_hierarchy fixture) ─────────────────────────────


def _with_hierarchy() -> bytes:
    return (_FIXTURES / "with_hierarchy.xml").read_bytes()


def test_hierarchy_parsed():
    msg = read(_with_hierarchy())
    assert msg.hierarchies.ids() == ["HCL_GEO"]


def test_hierarchy_tree_top_level():
    msg = read(_with_hierarchy())
    h = msg.hierarchies["HCL_GEO"]
    assert h.tree.ids() == ["H_CA"]


def test_hierarchy_flat_index_is_complete():
    """The flat ``codes`` walks the whole tree."""
    msg = read(_with_hierarchy())
    h = msg.hierarchies["HCL_GEO"]
    assert {n.id for n in h.codes} == {"H_CA", "H_ON", "H_TO", "H_QC", "H_MTL"}


def test_hierarchy_nested_navigation():
    msg = read(_with_hierarchy())
    h = msg.hierarchies["HCL_GEO"]
    ca = h.tree["H_CA"]
    assert {c.id for c in ca.children} == {"H_ON", "H_QC"}
    on = ca.children["H_ON"]
    assert on.children.ids() == ["H_TO"]


def test_hierarchy_code_ref_carries_item_urn():
    """Each HierarchicalCode points at a Code item in the referenced Codelist."""
    msg = read(_with_hierarchy())
    h = msg.hierarchies["HCL_GEO"]
    ca = h["H_CA"]
    assert ca.code is not None
    assert ca.code.urn.item_id == "CA"
    assert ca.code.urn.id == "CL_AREA"


def test_hierarchy_skos_navigation():
    """broader / narrower / depth_of work over the parsed tree."""
    msg = read(_with_hierarchy())
    h = msg.hierarchies["HCL_GEO"]
    assert h.depth_of("H_CA.H_ON.H_TO") == 2
    assert present(h.broader("H_CA.H_ON.H_TO")).id == "H_ON"
    assert set(h.narrower("H_CA").ids()) == {"H_ON", "H_QC"}


def test_hierarchy_roundtrip():
    """parse → write → re-parse preserves the tree structure and refs."""
    msg = read(_with_hierarchy())
    rt = read(write(msg))
    assert rt.hierarchies.ids() == msg.hierarchies.ids()
    rt_h = rt.hierarchies["HCL_GEO"]
    orig_h = msg.hierarchies["HCL_GEO"]
    assert rt_h.tree.ids() == orig_h.tree.ids()
    assert {n.id for n in rt_h.codes} == {n.id for n in orig_h.codes}
    # parent/child relationships preserved
    assert rt_h.broader_map() == orig_h.broader_map()
    # code refs preserved
    rt_to = rt_h["H_CA.H_ON.H_TO"]
    orig_to = orig_h["H_CA.H_ON.H_TO"]
    assert rt_to.code is not None
    assert orig_to.code is not None
    assert rt_to.code.urn.item_id == orig_to.code.urn.item_id


# ── New 3.1-shaped Hierarchy fields (close #105, #106, #110) ──────────────────


def test_hierarchy_has_formal_levels_parsed():
    """``@leveled="true"`` on the 2.1 ``<str:Hierarchy>`` element maps to
    ``Hierarchy.has_formal_levels`` (the 3.1-named field)."""
    msg = read(_with_hierarchy())
    assert msg.hierarchies["HCL_GEO"].has_formal_levels is True


def test_hierarchy_levels_flattened_from_xsd_chain():
    """v2.1 encodes the level depth as a nested ``<Level><Level>...`` chain;
    sdmxlib flattens this into ``Hierarchy.levels`` root-first."""
    msg = read(_with_hierarchy())
    levels = msg.hierarchies["HCL_GEO"].levels
    assert levels.ids() == ["COUNTRY", "PROVINCE", "CITY"]
    assert levels["COUNTRY"].name.get("en") == "Country"


def test_hierarchy_level_coding_format_parsed():
    """The COUNTRY level's CodingFormat (textType=Alpha) round-trips into
    ``Level.coding_format`` as a ``Facet``."""
    msg = read(_with_hierarchy())
    country = msg.hierarchies["HCL_GEO"].levels["COUNTRY"]
    assert country.coding_format is not None
    assert country.coding_format.type == "Alpha"


def test_hierarchical_code_validity_parsed():
    """HierarchicalCode @validFrom is per-node temporal validity,
    independent of the underlying Code's own validity."""
    msg = read(_with_hierarchy())
    ca = msg.hierarchies["HCL_GEO"]["H_CA"]
    assert ca.valid_from is not None
    assert ca.valid_from.year == 1867
    # H_TO has no validity declared
    assert msg.hierarchies["HCL_GEO"]["H_CA.H_ON.H_TO"].valid_from is None


def test_hierarchy_3_1_fields_roundtrip():
    """parse → write → re-parse preserves has_formal_levels, levels chain,
    Level.coding_format, and per-node validity."""
    msg = read(_with_hierarchy())
    rt = read(write(msg))
    rt_h = rt.hierarchies["HCL_GEO"]
    orig_h = msg.hierarchies["HCL_GEO"]
    assert rt_h.has_formal_levels == orig_h.has_formal_levels
    assert rt_h.levels.ids() == orig_h.levels.ids()
    assert rt_h.levels["COUNTRY"].coding_format is not None
    assert rt_h.levels["COUNTRY"].coding_format.type == present(orig_h.levels["COUNTRY"].coding_format).type
    assert rt_h["H_CA"].valid_from == orig_h["H_CA"].valid_from
    assert rt_h["H_CA.H_ON.H_TO"].valid_from is None


# ── ReferencePeriod + ReleaseCalendar (close #101) ────────────────────────────


def test_content_constraint_release_calendar_parsed():
    """2.1 ContentConstraint ReleaseCalendar parses periodicity / offset
    / tolerance as ISO-8601 duration strings."""
    msg = read(_minimal())
    cc = msg.constraints["CC_TEST"]
    assert cc.release_calendar is not None
    assert cc.release_calendar.periodicity == "P1M"
    assert cc.release_calendar.offset == "P15D"
    assert cc.release_calendar.tolerance == "P2D"


def test_content_constraint_reference_period_parsed():
    """2.1 ReferencePeriod's @startTime / @endTime are xs:dateTime."""
    from datetime import UTC, datetime

    msg = read(_minimal())
    cc = msg.constraints["CC_TEST"]
    assert cc.reference_period is not None
    # The fixture's startTime/endTime carry no explicit timezone, so the
    # parsed values are naive datetimes. Construct the expected values the
    # same way for a direct equality check.
    # Unqualified in the document, so tagged UTC (#440).
    assert cc.reference_period.start_period == datetime(2020, 1, 1, tzinfo=UTC)
    assert cc.reference_period.end_period == datetime(2023, 12, 31, 23, 59, 59, tzinfo=UTC)


def test_content_constraint_release_then_reference_round_trip():
    """The XSD sequence is ReleaseCalendar BEFORE ReferencePeriod; the
    writer was previously emitting them swapped. parse → write → re-parse
    + XSD validation now exercises the corrected order."""
    msg = read(_minimal())
    rt = read(write(msg))
    rt_cc = rt.constraints["CC_TEST"]
    orig_cc = msg.constraints["CC_TEST"]
    assert rt_cc.release_calendar == orig_cc.release_calendar
    assert rt_cc.reference_period == orig_cc.reference_period


# ── 2.1 cannot encode a measure-less DSD (#205 follow-up) ─────────────────────


def test_measureless_dsd_is_refused_by_the_21_writer():
    """`<MeasureList>` is mandatory in 2.1 and must hold a `<PrimaryMeasure>`.

    A DSD with no measures has no valid 2.1 encoding, so the writer refuses
    rather than emitting a document the schema rejects — and which sdmxlib's
    own reader now declines to parse.
    """
    from sdmxlib.formats.sdmx_ml21.writer import write as write21
    from sdmxlib.model.collections import ArtefactList, ItemList
    from sdmxlib.model.datastructure import DataStructure, Dimension
    from sdmxlib.model.message import StructureMessage

    dsd = DataStructure(
        id="NO_MEASURE",
        maintainer="T",
        version="1.0",
        name={"en": "d"},
        dimensions=ItemList([Dimension(id="FREQ", concept=concept_ref("FREQ"))]),
    )
    with pytest.raises(ValueError, match="no measures"):
        write21(StructureMessage(data_structures=ArtefactList([dsd])))


def test_dsd_with_a_measure_still_writes():
    from sdmxlib.formats.sdmx_ml21.writer import write as write21
    from sdmxlib.model.collections import ArtefactList, ItemList
    from sdmxlib.model.datastructure import DataStructure, Dimension, Measure
    from sdmxlib.model.message import StructureMessage

    dsd = DataStructure(
        id="WITH_MEASURE",
        maintainer="T",
        version="1.0",
        name={"en": "d"},
        dimensions=ItemList([Dimension(id="FREQ", concept=concept_ref("FREQ"))]),
        measures=ItemList([Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))]),
    )
    assert b"MeasureList" in write21(StructureMessage(data_structures=ArtefactList([dsd])))
