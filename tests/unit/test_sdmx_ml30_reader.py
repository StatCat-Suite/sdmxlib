"""Unit tests for SDMX-ML 3.0 reader."""

from pathlib import Path

import pytest

from sdmxlib.formats import parse
from sdmxlib.formats.sdmx_ml30.reader import read as read30
from sdmxlib.formats.sdmx_ml30.writer import write as write30
from sdmxlib.model.collections import ItemList
from sdmxlib.model.constraint import ConstraintType
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import AttachmentLevel, AttributeUsage
from sdmxlib.model.hierarchy import Hierarchy
from sdmxlib.model.mapping import MapDataType, SourceValue
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import Ref
from tests.support.narrow import present
from tests.support.roundtrip import as_structure, load_xml

pytestmark = pytest.mark.unit

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "sdmx_ml30"


# ── auto-detection ────────────────────────────────────────────────────────────


def test_parse_detects_30():
    msg = load_xml("3.0", "minimal")
    assert msg is not None


def test_parse_version_hint():
    data = (_FIXTURES / "minimal.xml").read_bytes()
    msg = as_structure(parse(data, version="3.0"))
    assert msg.codelists.ids() == ["CL_FREQ"]


# ── codelists ─────────────────────────────────────────────────────────────────


def test_codelist_ids():
    msg = load_xml("3.0", "minimal")
    assert msg.codelists.ids() == ["CL_FREQ"]


def test_codelist_agency():
    msg = load_xml("3.0", "minimal")
    assert msg.codelists["CL_FREQ"].agency_id == "SDMX"


def test_codelist_version():
    msg = load_xml("3.0", "minimal")
    assert msg.codelists["CL_FREQ"].version == "1.0"


def test_codelist_is_final_defaults_false_in_3_0():
    """SDMX 3.0 removed isFinal from every maintainable; defaults to False."""
    msg = load_xml("3.0", "minimal")
    assert msg.codelists["CL_FREQ"].is_final is False


def test_code_ids():
    msg = load_xml("3.0", "minimal")
    assert msg.codelists["CL_FREQ"].codes.ids() == ["A", "Q", "M", "Q1"]


def test_code_name():
    msg = load_xml("3.0", "minimal")
    assert msg.codelists["CL_FREQ"].codes["A"].name.get("en") == "Annual"


def test_code_multilingual():
    msg = load_xml("3.0", "minimal")
    code_a = msg.codelists["CL_FREQ"].codes["A"]
    assert code_a.name.get("en") == "Annual"
    assert code_a.name.get("fr") == "Annuel"


def test_code_parent_id():
    msg = load_xml("3.0", "minimal")
    assert msg.codelists["CL_FREQ"].codes["Q1"].parent_id == "Q"
    assert msg.codelists["CL_FREQ"].codes["A"].parent_id is None


# ── concept schemes ───────────────────────────────────────────────────────────


def test_concept_scheme_ids():
    msg = load_xml("3.0", "minimal")
    assert msg.concept_schemes.ids() == ["CROSS_DOMAIN_CONCEPTS"]


def test_concept_ids():
    msg = load_xml("3.0", "minimal")
    cs = msg.concept_schemes["CROSS_DOMAIN_CONCEPTS"]
    assert cs.concepts.ids() == ["FREQ", "TIME_PERIOD", "OBS_VALUE", "OBS_STATUS"]


# ── data structures ───────────────────────────────────────────────────────────


def test_dsd_ids():
    msg = load_xml("3.0", "minimal")
    assert msg.data_structures.ids() == ["DSD_TEST"]


def test_dimension_ids():
    msg = load_xml("3.0", "minimal")
    dsd = msg.data_structures["DSD_TEST"]
    assert dsd.dimensions.ids() == ["FREQ"]


def test_dimension_order():
    msg = load_xml("3.0", "minimal")
    dsd = msg.data_structures["DSD_TEST"]
    assert dsd.dimensions.ids() == ["FREQ"]


def test_time_dimension():
    msg = load_xml("3.0", "minimal")
    dsd = msg.data_structures["DSD_TEST"]
    assert dsd.time_dimension is not None
    assert dsd.time_dimension.id == "TIME_PERIOD"


def test_attribute_ids():
    msg = load_xml("3.0", "minimal")
    dsd = msg.data_structures["DSD_TEST"]
    assert dsd.attributes.ids() == ["OBS_STATUS"]


def test_attribute_usage_conditional():
    """SDMX 3.0: usage= attribute (not assignmentStatus=)."""
    msg = load_xml("3.0", "minimal")
    dsd = msg.data_structures["DSD_TEST"]
    assert dsd.attributes["OBS_STATUS"].usage is AttributeUsage.OPTIONAL


def test_attribute_attachment_obs():
    """SDMX 3.0: ObservationValue element signals observation-level attachment."""
    msg = load_xml("3.0", "minimal")
    dsd = msg.data_structures["DSD_TEST"]
    assert dsd.attributes["OBS_STATUS"].attachment == AttachmentLevel.OBSERVATION


def test_measure_ids():
    """SDMX 3.0: MeasureList uses <str:Measure> (not PrimaryMeasure)."""
    msg = load_xml("3.0", "minimal")
    dsd = msg.data_structures["DSD_TEST"]
    assert dsd.measures.ids() == ["OBS_VALUE"]


# ── dataflows ─────────────────────────────────────────────────────────────────


def test_dataflow_ids():
    msg = load_xml("3.0", "minimal")
    assert msg.dataflows.ids() == ["DF_TEST"]


def test_dataflow_structure_resolved():
    msg = load_xml("3.0", "minimal")
    df = msg.dataflows["DF_TEST"]
    assert df.structure is not None
    assert df.structure.resolved


# ── category schemes & categorisations ───────────────────────────────────────


def test_category_scheme_ids():
    msg = load_xml("3.0", "minimal")
    assert msg.category_schemes.ids() == ["CS_TEST"]


def test_category_ids():
    msg = load_xml("3.0", "minimal")
    cs = msg.category_schemes["CS_TEST"]
    assert cs.categories.ids() == ["CAT_A"]


def test_nested_category_ids():
    msg = load_xml("3.0", "minimal")
    cs = msg.category_schemes["CS_TEST"]
    assert cs.categories["CAT_A"].categories.ids() == ["CAT_A1"]


def test_categorisation_ids():
    msg = load_xml("3.0", "minimal")
    assert msg.categorisations.ids() == ["CAT_DF_TEST"]


# ── with_groups fixture ───────────────────────────────────────────────────────


def test_groups_with_groups():
    msg = load_xml("3.0", "with_groups")
    dsd = msg.data_structures["DSD_FX"]
    assert dsd.groups is not None
    assert dsd.groups.ids() == ["SiblingGroup"]


def test_group_dimension_refs():
    """SDMX 3.0 GroupDimension uses dimensionRef attribute."""
    msg = load_xml("3.0", "with_groups")
    dsd = msg.data_structures["DSD_FX"]
    assert dsd.groups is not None
    group = dsd.groups["SiblingGroup"]
    assert group.dimension_ids == ["FREQ", "CURRENCY", "CURRENCY_DENOM"]


def test_multiple_measures():
    """SDMX 3.0 allows multiple measures in MeasureList."""
    msg = load_xml("3.0", "with_groups")
    dsd = msg.data_structures["DSD_FX"]
    assert dsd.measures.ids() == ["OBS_VALUE", "OBS_VALUE_ALT"]


def test_multiple_dims():
    msg = load_xml("3.0", "with_groups")
    dsd = msg.data_structures["DSD_FX"]
    assert dsd.dimensions.ids() == ["FREQ", "CURRENCY", "CURRENCY_DENOM"]


def test_dim_order_preserved():
    msg = load_xml("3.0", "with_groups")
    dsd = msg.data_structures["DSD_FX"]
    assert len(list(dsd.dimensions)) > 0


def test_multiple_codelists():
    msg = load_xml("3.0", "with_groups")
    assert set(msg.codelists.ids()) == {"CL_FREQ", "CL_CURRENCY"}


def test_series_attribute_related_dims():
    """CONF_STATUS attribute is related to FREQ and CURRENCY dimensions."""
    msg = load_xml("3.0", "with_groups")
    dsd = msg.data_structures["DSD_FX"]
    conf = dsd.attributes["CONF_STATUS"]
    assert conf.attachment == AttachmentLevel.SERIES
    assert conf.related_dimensions is not None
    assert set(conf.related_dimensions) == {"FREQ", "CURRENCY"}


def test_attribute_usage_mandatory():
    msg = load_xml("3.0", "with_groups")
    dsd = msg.data_structures["DSD_FX"]
    assert dsd.attributes["CONF_STATUS"].usage is AttributeUsage.MANDATORY


# ── empty / missing sections ──────────────────────────────────────────────────


def test_empty_message_returns_structure_message():
    data = b"""<?xml version="1.0" encoding="UTF-8"?>
<mes:Structure xmlns:mes="http://www.sdmx.org/resources/sdmxml/schemas/v3_0/message">
  <mes:Structures/>
</mes:Structure>"""
    msg = as_structure(parse(data))
    assert isinstance(msg, StructureMessage)
    assert msg.codelists.ids() == []
    assert msg.data_structures.ids() == []


def test_no_groups_returns_none():
    msg = load_xml("3.0", "minimal")
    dsd = msg.data_structures["TEST:DSD_TEST"]
    assert dsd.groups is None or dsd.groups == ItemList()


# ── OrganisationSchemes ───────────────────────────────────────────────────────


def test_agency_schemes_parsed():
    msg = load_xml("3.0", "minimal")
    assert len(msg.agency_schemes) == 1
    scheme = msg.agency_schemes[0]
    assert scheme.id == "AGENCIES"
    assert scheme.agency_id == "SDMX"


def test_agency_scheme_agencies():
    msg = load_xml("3.0", "minimal")
    scheme = msg.agency_schemes[0]
    assert scheme.agencies.ids() == ["SDMX", "TEST"]
    assert scheme.agencies["SDMX"].name["en"] == "SDMX"


def test_agency_contact_parsed():
    msg = load_xml("3.0", "minimal")
    sdmx_agency = msg.agency_schemes[0].agencies["SDMX"]
    assert len(sdmx_agency.contacts) == 1
    contact = sdmx_agency.contacts[0]
    assert contact.name["en"] == "SDMX Support"
    assert "support@sdmx.org" in contact.emails


def test_agency_scheme_roundtrip():
    msg = load_xml("3.0", "minimal")
    rt = read30(write30(msg))
    assert rt.agency_schemes.ids() == msg.agency_schemes.ids()
    orig_scheme = msg.agency_schemes[0]
    rt_scheme = rt.agency_schemes[0]
    assert rt_scheme.agencies.ids() == orig_scheme.agencies.ids()
    sdmx_rt = rt_scheme.agencies["SDMX"]
    sdmx_orig = orig_scheme.agencies["SDMX"]
    assert len(sdmx_rt.contacts) == len(sdmx_orig.contacts)
    assert sdmx_rt.contacts[0].emails == sdmx_orig.contacts[0].emails


# ── Constraints ───────────────────────────────────────────────────────────────


def test_constraints_parsed():
    msg = load_xml("3.0", "minimal")
    assert len(msg.constraints) == 1
    cc = msg.constraints["CC_TEST"]
    assert cc.id == "CC_TEST"
    assert cc.constraint_type is ConstraintType.ALLOWED


def test_constraint_attachment_resolved():
    msg = load_xml("3.0", "minimal")
    cc = msg.constraints["CC_TEST"]
    assert len(cc.attachment.dataflows) == 1
    assert cc.attachment.dataflows[0].resolved
    resolved = cc.attachment.dataflows[0]()
    assert isinstance(resolved, Dataflow)
    assert resolved.id == "DF_TEST"


def test_constraint_cube_region():
    msg = load_xml("3.0", "minimal")
    cc = msg.constraints["CC_TEST"]
    assert len(cc.regions) == 1
    kv = cc.regions[0].keys[0]
    assert kv.dimension_id == "FREQ"
    assert kv.codes() == frozenset({"A", "Q"})


def test_constraint_roundtrip():
    msg = load_xml("3.0", "minimal")
    rt = read30(write30(msg))
    assert rt.constraints.ids() == msg.constraints.ids()
    rt_cc = rt.constraints["CC_TEST"]
    assert rt_cc.constraint_type is ConstraintType.ALLOWED
    assert len(rt_cc.regions) == 1


# ── StructureSets ─────────────────────────────────────────────────────────────
# SDMX 3.0 removed the unified <str:StructureSets> container. The individual
# map kinds (CategorySchemeMaps, CodelistMaps, ConceptSchemeMaps, etc.) get
# their own top-level containers. The minimal 3.0 fixture therefore no longer
# carries a StructureSet; the structure-set coverage lives in the 2.1 tests.


# ── MetadataStructures + Metadataflows (with_metadata fixture) ───────────────


def _load_meta() -> StructureMessage:
    return as_structure(parse((_FIXTURES / "with_metadata.xml").read_bytes()))


def test_metadata_structure_parsed():
    msg = _load_meta()
    assert msg.metadata_structures.ids() == ["MSD_TEST"]


def test_metadata_attribute_tree_recursion():
    """Top-level attribute carries two children — round-trip preserves the tree."""
    msd = _load_meta().metadata_structures["MSD_TEST"]
    assert msd.attributes.ids() == ["CONTACT", "FOOTNOTE_TEXT"]
    contact = msd.attributes["CONTACT"]
    assert contact.is_presentational is True
    assert contact.children.ids() == ["CONTACT_NAME", "CONTACT_LANG"]


def test_metadata_attribute_min_max_occurs():
    """Coded child has minOccurs=0 / maxOccurs=unbounded."""
    msd = _load_meta().metadata_structures["MSD_TEST"]
    lang = msd.attributes["CONTACT"].children["CONTACT_LANG"]
    assert lang.min_occurs == 0
    assert lang.max_occurs is None  # unbounded


def test_metadata_attribute_codelist_representation():
    msd = _load_meta().metadata_structures["MSD_TEST"]
    lang = msd.attributes["CONTACT"].children["CONTACT_LANG"]
    assert lang.representation is not None
    assert isinstance(lang.representation, Ref)
    assert lang.representation.urn.id == "CL_LANG"


def test_metadata_attribute_facet_representation():
    """CONTACT_NAME has TextFormat representation (Facet, not Ref)."""
    msd = _load_meta().metadata_structures["MSD_TEST"]
    name = msd.attributes["CONTACT"].children["CONTACT_NAME"]
    assert name.representation is not None
    # Facet exposes a `type` field for the text type
    assert getattr(name.representation, "type", None) == "String"


def test_metadataflow_parsed_with_structure_ref():
    msg = _load_meta()
    assert msg.metadataflows.ids() == ["MDF_TEST"]
    mdf = msg.metadataflows["MDF_TEST"]
    assert mdf.structure is not None
    assert mdf.structure.resolved
    assert mdf.structure().id == "MSD_TEST"


def test_metadataflow_targets_round_trip():
    """Target URN list (incl. WildcardUrnType wildcards) survives read."""
    mdf = _load_meta().metadataflows["MDF_TEST"]
    assert mdf.targets == ("urn:sdmx:org.sdmx.infomodel.datastructure.Dataflow=TEST:CPI(*)",)


def test_metadata_round_trip():
    """Parse → write → re-parse preserves the MSD tree and Metadataflow targets."""
    msg = _load_meta()
    rt = read30(write30(msg))
    assert rt.metadata_structures.ids() == msg.metadata_structures.ids()
    orig_msd = msg.metadata_structures["MSD_TEST"]
    rt_msd = rt.metadata_structures["MSD_TEST"]
    assert rt_msd.attributes.ids() == orig_msd.attributes.ids()
    # Recursion preserved
    assert rt_msd.attributes["CONTACT"].children.ids() == orig_msd.attributes["CONTACT"].children.ids()
    # min/max occurs preserved
    assert rt_msd.attributes["CONTACT"].children["CONTACT_LANG"].max_occurs is None
    # Metadataflow targets preserved
    assert rt.metadataflows["MDF_TEST"].targets == msg.metadataflows["MDF_TEST"].targets


# ── CodelistExtension (close #109) ────────────────────────────────────────────


def _load_extension() -> StructureMessage:
    return as_structure(parse((_FIXTURES / "with_codelist_extension.xml").read_bytes()))


def test_codelist_extension_parsed():
    msg = _load_extension()
    composed = msg.codelists["CL_AREA_ALL"]
    assert len(composed.extensions) == 2


def test_codelist_extension_inclusive_selection():
    """First extension cherry-picks CA from CL_AREA_BASE."""
    from sdmxlib.model.codelist import InclusionMode

    msg = _load_extension()
    ext = msg.codelists["CL_AREA_ALL"].extensions[0]
    assert ext.codelist.urn.id == "CL_AREA_BASE"
    assert ext.prefix is None
    assert ext.selection is not None
    assert ext.selection.mode is InclusionMode.INCLUDE
    assert [m.value for m in ext.selection.members] == ["CA"]


def test_codelist_extension_exclusive_with_prefix_and_cascade():
    """Second extension excludes DE_OLD from CL_AREA_ARCH, prefixed ARCH_,
    with cascade ``excluderoot``."""
    from sdmxlib.model.codelist import CascadeSelection, InclusionMode

    msg = _load_extension()
    ext = msg.codelists["CL_AREA_ALL"].extensions[1]
    assert ext.codelist.urn.id == "CL_AREA_ARCH"
    assert ext.prefix == "ARCH_"
    assert ext.selection is not None
    assert ext.selection.mode is InclusionMode.EXCLUDE
    member = ext.selection.members[0]
    assert member.value == "DE_OLD"
    assert member.cascade is CascadeSelection.EXCLUDE_ROOT


def test_codelist_extension_roundtrip():
    """Parse → write → re-parse preserves the extension list, modes,
    cascade, and prefix."""
    msg = _load_extension()
    rt = read30(write30(msg))
    orig = msg.codelists["CL_AREA_ALL"].extensions
    rt_exts = rt.codelists["CL_AREA_ALL"].extensions
    assert len(rt_exts) == len(orig)
    for orig_ext, rt_ext in zip(orig, rt_exts, strict=True):
        assert rt_ext.codelist.urn.id == orig_ext.codelist.urn.id
        assert rt_ext.prefix == orig_ext.prefix
        if orig_ext.selection is None:
            assert rt_ext.selection is None
        else:
            assert present(rt_ext.selection).mode is orig_ext.selection.mode
            orig_members = [(m.value, m.cascade) for m in orig_ext.selection.members]
            rt_members = [(m.value, m.cascade) for m in present(rt_ext.selection).members]
            assert rt_members == orig_members


def test_codelist_without_extensions_defaults_empty_tuple():
    """Codelists in older fixtures with no <CodelistExtension> children
    have an empty tuple, not None."""
    msg = load_xml("3.0", "minimal")
    assert msg.codelists["CL_FREQ"].extensions == ()


# ── RepresentationMap value mapping variants (close #107) ─────────────────────


def _load_repmap_variants() -> StructureMessage:
    return as_structure(parse((_FIXTURES / "with_representation_map_variants.xml").read_bytes()))


def test_repmap_variants_four_mappings():
    msg = _load_repmap_variants()
    rm = msg.representation_maps["RM_VARIANTS"]
    assert len(rm.mappings) == 4


def test_repmap_literal_variant_no_validity():
    rm = _load_repmap_variants().representation_maps["RM_VARIANTS"]
    m = rm.mappings[0]
    assert m.sources == (SourceValue("A"),)
    assert m.targets == ("ANNUAL",)
    assert m.valid_from is None
    assert m.valid_to is None


def test_repmap_literal_variant_with_validity():
    """SDMX-ML ValueMappingType validity attrs are xs:date — we parse as
    datetime.date, not datetime.datetime."""
    from datetime import date

    rm = _load_repmap_variants().representation_maps["RM_VARIANTS"]
    m = rm.mappings[1]
    assert m.sources[0].is_literal
    assert m.valid_from == date(2020, 1, 1)
    assert m.valid_to == date(2023, 12, 31)


def test_repmap_regex_variant():
    rm = _load_repmap_variants().representation_maps["RM_VARIANTS"]
    m = rm.mappings[2]
    assert m.sources == (SourceValue("^M-[0-9]+$", is_regex=True),)
    assert m.targets == ("MONTHLY",)


def test_repmap_index_variant():
    rm = _load_repmap_variants().representation_maps["RM_VARIANTS"]
    m = rm.mappings[3]
    assert m.sources == (SourceValue("WK", start_index=2, end_index=3),)


def test_repmap_translate_only_handles_literal():
    """The convenience ``translate`` method ignores non-literal variants
    by design — regex / index semantics must be handled by the caller."""
    rm = _load_repmap_variants().representation_maps["RM_VARIANTS"]
    assert rm.translate("A") == "ANNUAL"
    assert rm.translate("Q") == "QUARTERLY"
    # The regex variant matches "M-12" but ``translate`` only handles literals.
    assert rm.translate("M-12") is None


def test_repmap_variants_roundtrip():
    """All four mapping kinds + per-mapping validity survive parse → write
    → re-parse, each source value's flags included."""
    orig = _load_repmap_variants()
    rt = read30(write30(orig))
    assert rt.representation_maps["RM_VARIANTS"].mappings == orig.representation_maps["RM_VARIANTS"].mappings


_N_TO_M = """<?xml version="1.0" encoding="UTF-8"?>
<mes:Structure xmlns:mes="http://www.sdmx.org/resources/sdmxml/schemas/v3_0/message"
               xmlns:str="http://www.sdmx.org/resources/sdmxml/schemas/v3_0/structure"
               xmlns:com="http://www.sdmx.org/resources/sdmxml/schemas/v3_0/common">
  <mes:Header><mes:ID>N_TO_M</mes:ID><mes:Test>true</mes:Test>
    <mes:Prepared>2026-01-01T00:00:00</mes:Prepared><mes:Sender id="T"/></mes:Header>
  <mes:Structures>
    <str:RepresentationMaps>
      <str:RepresentationMap id="RM" agencyID="T" version="1.0">
        <com:Name xml:lang="en">rm</com:Name>
        <str:SourceCodelist>urn:sdmx:org.sdmx.infomodel.codelist.Codelist=T:CL_A(1.0)</str:SourceCodelist>
        <str:SourceDataType>String</str:SourceDataType>
        <str:TargetDataType>Integer</str:TargetDataType>
        <str:TargetCodelist>urn:sdmx:org.sdmx.infomodel.codelist.Codelist=T:CL_B(1.0)</str:TargetCodelist>
        <str:RepresentationMapping>
          <str:SourceValue>A</str:SourceValue>
          <str:SourceValue isRegEx="true">^x</str:SourceValue>
          <str:TargetValue>1</str:TargetValue>
          <str:TargetValue>B</str:TargetValue>
        </str:RepresentationMapping>
        <str:RepresentationMapping>
          <str:SourceValue startIndex="1" endIndex="2">QQ</str:SourceValue>
          <str:SourceValue>Z</str:SourceValue>
        </str:RepresentationMapping>
      </str:RepresentationMap>
    </str:RepresentationMaps>
    <str:StructureMaps>
      <str:StructureMap id="SM" agencyID="T" version="1.0">
        <com:Name xml:lang="en">sm</com:Name>
        <str:Source>urn:sdmx:org.sdmx.infomodel.datastructure.DataStructure=T:A(1.0)</str:Source>
        <str:Target>urn:sdmx:org.sdmx.infomodel.datastructure.DataStructure=T:B(1.0)</str:Target>
        <str:ComponentMap>
          <str:Source>FREQ</str:Source>
          <str:Source>REF_AREA</str:Source>
          <str:Target>KEY</str:Target>
          <str:RepresentationMap>urn:sdmx:org.sdmx.infomodel.structuremapping.RepresentationMap=T:RM(1.0)</str:RepresentationMap>
        </str:ComponentMap>
      </str:StructureMap>
    </str:StructureMaps>
  </mes:Structures>
</mes:Structure>
"""


def test_an_n_to_m_representation_map_is_read_whole():
    """Every endpoint, in document order, and every value of every mapping (#422).

    Both were read as their first until #422 — with nothing reporting it — and
    a mapping with no target value was skipped outright.
    """
    rm = as_structure(parse(_N_TO_M.encode())).representation_maps["RM"]
    assert [e.urn.id if isinstance(e, Ref) else e for e in rm.sources] == ["CL_A", MapDataType("String")]
    assert [e.urn.id if isinstance(e, Ref) else e for e in rm.targets] == [MapDataType("Integer"), "CL_B"]
    assert rm.mappings[0].sources == (SourceValue("A"), SourceValue("^x", is_regex=True))
    assert rm.mappings[0].targets == ("1", "B")
    assert rm.mappings[1].sources == (SourceValue("QQ", start_index=1, end_index=2), SourceValue("Z"))
    assert rm.mappings[1].targets == ()


def test_an_n_to_m_component_map_is_read_whole():
    sm = as_structure(parse(_N_TO_M.encode())).structure_maps["SM"]
    assert sm.maps[0].sources == ("FREQ", "REF_AREA")
    assert sm.maps[0].targets == ("KEY",)


def test_n_to_m_maps_round_trip():
    orig = as_structure(parse(_N_TO_M.encode()))
    back = read30(write30(orig))
    assert back.representation_maps["RM"].mappings == orig.representation_maps["RM"].mappings
    assert [str(e.urn) if isinstance(e, Ref) else e for e in back.representation_maps["RM"].sources] == [
        str(e.urn) if isinstance(e, Ref) else e for e in orig.representation_maps["RM"].sources
    ]
    assert back.structure_maps["SM"].maps == orig.structure_maps["SM"].maps


# ── RepresentationMap with primitive dataType endpoint (#131) ─────────────────


def test_repmap_datatype_endpoint_parses():
    """Reader recognises ``<str:TargetDataType>`` and surfaces it as
    ``MapDataType`` rather than dropping it."""
    rm = _load_repmap_variants().representation_maps["RM_DATATYPE"]
    (source,) = rm.sources
    assert isinstance(source, Ref)
    assert rm.targets == (MapDataType("Integer"),)


def test_repmap_datatype_endpoint_roundtrips():
    """parse → write → re-parse preserves the ``MapDataType`` discriminator."""
    orig = _load_repmap_variants()
    rt = read30(write30(orig))
    assert rt.representation_maps["RM_DATATYPE"].targets == (MapDataType("Integer"),)


# ── HierarchyAssociation (close #108) ─────────────────────────────────────────


def _load_hassoc() -> StructureMessage:
    return as_structure(parse((_FIXTURES / "with_hierarchy_association.xml").read_bytes()))


def test_hierarchy_association_parsed():
    msg = _load_hassoc()
    assert msg.hierarchy_associations.ids() == ["HA_GEO_REF_AREA"]


def test_hierarchy_association_links():
    """All three URN-text refs resolve to the right kind of artefact."""
    msg = _load_hassoc()
    ha = msg.hierarchy_associations["HA_GEO_REF_AREA"]
    assert ha.linked_hierarchy is not None
    assert ha.linked_hierarchy.urn.id == "H_GEO"
    assert ha.linked_object is not None
    assert ha.linked_object.urn.artefact_class == "Dimension"
    assert ha.linked_object.urn.item_id == "REF_AREA"
    assert ha.context_object is not None
    assert ha.context_object.urn.artefact_class == "Dataflow"
    assert ha.context_object.urn.id == "DF_GEO"


def test_hierarchy_association_linked_hierarchy_resolves_in_same_message():
    """The 3.0 reader indexes Hierarchies before HierarchyAssociations so
    LinkedHierarchy refs resolve."""
    msg = _load_hassoc()
    ha = msg.hierarchy_associations["HA_GEO_REF_AREA"]
    assert ha.linked_hierarchy is not None
    assert ha.linked_hierarchy.resolved
    resolved = ha.linked_hierarchy()
    assert isinstance(resolved, Hierarchy)
    assert resolved.id == "H_GEO"


def test_hierarchy_association_optional_context_object_absent():
    """If <ContextObject> is missing, the field is None."""
    from sdmxlib.model.hierarchy import HierarchyAssociation

    ha = HierarchyAssociation(name={"en": "HA_TEST"}, id="HA_TEST", maintainer="T")
    assert ha.context_object is None


def test_hierarchy_association_roundtrip():
    """parse → write → re-parse preserves the three refs and the
    container is emitted in the right order."""
    msg = _load_hassoc()
    rt = read30(write30(msg))
    assert rt.hierarchy_associations.ids() == msg.hierarchy_associations.ids()
    orig_ha = msg.hierarchy_associations["HA_GEO_REF_AREA"]
    rt_ha = rt.hierarchy_associations["HA_GEO_REF_AREA"]
    assert str(present(rt_ha.linked_hierarchy).urn) == str(present(orig_ha.linked_hierarchy).urn)
    assert str(present(rt_ha.linked_object).urn) == str(present(orig_ha.linked_object).urn)
    assert str(present(rt_ha.context_object).urn) == str(present(orig_ha.context_object).urn)


# ── Multiple Hierarchies + HierarchyAssociations (#135) ──────────────────────


def _load_multi() -> StructureMessage:
    return as_structure(parse((_FIXTURES / "with_multiple_hierarchies.xml").read_bytes()))


def test_two_hierarchies_over_one_codelist_parsed():
    """Both Hierarchy artefacts land in the message, both bound to CL_AREA codes."""
    msg = _load_multi()
    assert set(msg.hierarchies.ids()) == {"H_GEO_ADMIN", "H_GEO_STAT"}
    admin = msg.hierarchies["H_GEO_ADMIN"]
    stat = msg.hierarchies["H_GEO_STAT"]
    # Same codes, different parent graphs (#135).
    assert admin.descendants("CA") == ["ON", "TOR", "QC"]
    assert stat.descendants("CA") == ["ON", "QC", "TOR"]
    # ON has a descendant under admin (TOR) but not under statistical.
    assert admin.descendants("ON") == ["TOR"]
    assert stat.descendants("ON") == []


def test_two_hierarchy_associations_parsed():
    """One scoped, one unscoped — distinguishable by their context_object."""
    msg = _load_multi()
    ids = set(msg.hierarchy_associations.ids())
    assert ids == {"HA_REF_AREA_ADMIN", "HA_REF_AREA_STAT_GLOBAL"}
    scoped = msg.hierarchy_associations["HA_REF_AREA_ADMIN"]
    unscoped = msg.hierarchy_associations["HA_REF_AREA_STAT_GLOBAL"]
    assert scoped.context_object is not None
    assert scoped.context_object.urn.id == "DF_GEO"
    assert unscoped.context_object is None


def test_unique_hierarchy_picks_dataflow_scoped_admin():
    """End-to-end: the scoped HA for DF_GEO wins over the unscoped one,
    so reg.unique_hierarchy(DF_GEO, REF_AREA) resolves to the admin graph."""
    from sdmxlib.model.in_memory_registry import InMemoryRegistry

    msg = _load_multi()
    reg = InMemoryRegistry()
    reg.add_all([*msg.codelists, *msg.dataflows, *msg.hierarchies, *msg.hierarchy_associations])
    # Also add the DSD so the constructed dim URN resolves against a real
    # DataStructure agency/id/version.
    reg.add_all(msg.data_structures)
    df = msg.dataflows["DF_GEO"]
    h = reg.unique_hierarchy(df, "REF_AREA")
    assert h is not None
    assert h.urn.id == "H_GEO_ADMIN"


def test_multi_hierarchy_fixture_roundtrips():
    """parse → write → re-parse preserves both hierarchies and both associations."""
    msg = _load_multi()
    rt = read30(write30(msg))
    assert set(rt.hierarchies.ids()) == set(msg.hierarchies.ids())
    assert set(rt.hierarchy_associations.ids()) == set(msg.hierarchy_associations.ids())
    rt_admin = rt.hierarchies["H_GEO_ADMIN"]
    rt_stat = rt.hierarchies["H_GEO_STAT"]
    assert rt_admin.descendants("ON") == ["TOR"]
    assert rt_stat.descendants("ON") == []


# ── Annotations on non-DSD maintainables (close #96) ──────────────────────────


def _load_complete() -> StructureMessage:
    return as_structure(parse((_FIXTURES / "complete.xml").read_bytes()))


def test_codelist_annotation_parsed_with_all_fields():
    """The Codelist annotation exercises every attr / element on AnnotationType:
    @id, AnnotationTitle, AnnotationType, AnnotationURL, multi-lingual AnnotationText."""
    cl = _load_complete().codelists["CL_FREQ"]
    assert len(cl.annotations) == 1
    ann = cl.annotations[0]
    assert ann.id == "cl-src"
    assert ann.title == "Source frequency codelist"
    assert ann.type == "SOURCE"
    assert [u.url for u in ann.urls] == ["https://example.org/CL_FREQ"]
    assert ann.text.get("en") == "Curated by SDMX"
    assert ann.text.get("fr") == "Conservé par SDMX"


def test_concept_scheme_annotation_minimal():
    """A type-only annotation parses with all other fields None."""
    cs = _load_complete().concept_schemes["CROSS_DOMAIN_CONCEPTS"]
    assert "DEPRECATED" in cs.annotations


def test_dataflow_annotation_parsed():
    df = _load_complete().dataflows["DF_TEST"]
    assert present(df.annotations.first("STATUS")).text.get("en") == "Production-ready"


def test_categorisation_annotation_parsed():
    cat = _load_complete().categorisations["CAT_DF_TEST"]
    assert "SOURCE" in cat.annotations


def test_provision_agreement_annotation_parsed():
    pa = _load_complete().provision_agreements["PA_TEST"]
    assert [u.url for u in present(pa.annotations.first("CONTACT")).urls] == ["mailto:owner@example.org"]


def test_data_constraint_annotation_parsed():
    cc = _load_complete().constraints["CC_TEST"]
    assert present(cc.annotations.first("NOTE")).text.get("en") == ("Restricted dimensions for Q1 reporting")


def test_annotations_round_trip_on_all_non_dsd_maintainables():
    """parse → write → re-parse preserves the annotations block on every
    maintainable that previously lost them silently on write."""
    msg = _load_complete()
    rt = read30(write30(msg))
    # Codelist (with the full attribute set)
    rt_cl = rt.codelists["CL_FREQ"]
    orig_cl = msg.codelists["CL_FREQ"]
    assert rt_cl.annotations == orig_cl.annotations
    # Other artefacts: just compare the annotations
    for slot in (
        "concept_schemes",
        "dataflows",
        "categorisations",
        "provision_agreements",
        "constraints",
    ):
        orig_list = getattr(msg, slot)
        rt_list = getattr(rt, slot)
        for orig_artefact, rt_artefact in zip(orig_list, rt_list, strict=True):
            assert rt_artefact.annotations == orig_artefact.annotations, (
                f"Annotations lost on round-trip for {slot}[{orig_artefact.id}]"
            )


# ── Attribute attachment levels: Group + Dataset (close #98) ──────────────────


def test_attribute_group_attachment_parsed():
    """v3.0 <Group>id</Group> is parsed into related_group with the
    GroupDimension descriptor's id (not just AttachmentLevel.GROUP marker)."""
    msg = as_structure(parse((_FIXTURES / "with_groups.xml").read_bytes()))
    dsd = msg.data_structures["DSD_FX"]
    attr = dsd.attributes["DECIMALS"]
    assert attr.attachment is AttachmentLevel.GROUP
    assert attr.related_group == "SiblingGroup"


def test_attribute_dataset_attachment_parsed():
    """v3.0 <Dataflow/> in AttributeRelationship maps to AttachmentLevel.DATASET
    (the 3.0 spec dropped 2.1's <None/> form and merged dataset-level into
    'varies per dataflow')."""
    msg = as_structure(parse((_FIXTURES / "with_groups.xml").read_bytes()))
    dsd = msg.data_structures["DSD_FX"]
    attr = dsd.attributes["UNIT_MULT"]
    assert attr.attachment is AttachmentLevel.DATASET
    assert attr.related_dimensions is None
    assert attr.related_group is None


def test_attribute_attachment_levels_round_trip():
    """parse → write → re-parse preserves the attachment level on each of
    the four shapes (observation, series, group with id, dataset/dataflow)."""
    msg = as_structure(parse((_FIXTURES / "with_groups.xml").read_bytes()))
    rt = read30(write30(msg))
    orig_dsd = msg.data_structures["DSD_FX"]
    rt_dsd = rt.data_structures["DSD_FX"]
    for attr_id in ("OBS_STATUS", "CONF_STATUS", "DECIMALS", "UNIT_MULT"):
        assert rt_dsd.attributes[attr_id].attachment == orig_dsd.attributes[attr_id].attachment
    # Group id round-trips
    assert rt_dsd.attributes["DECIMALS"].related_group == "SiblingGroup"
    # Multi-dim series attachment list round-trips
    assert rt_dsd.attributes["CONF_STATUS"].related_dimensions == ["FREQ", "CURRENCY"]


# ── Contact multi-field (close #99) ───────────────────────────────────────────


def test_contact_all_fields_parsed():
    """The press-office Contact in complete.xml exercises every ContactType
    field the XSD allows: Name (multi-lang), Department, Role, Telephone (x2),
    Fax, X400, URI, Email (x2)."""
    msg = _load_complete()
    agency = msg.agency_schemes[0].agencies["SDMX"]
    assert len(agency.contacts) == 2
    press = agency.contacts[1]
    assert press.name["en"] == "SDMX Press Office"
    assert press.name["fr"] == "Bureau de presse SDMX"
    assert press.department["en"] == "Communications"
    assert press.role["en"] == "Media liaison"
    assert press.phones == ("+33 1 23 45 67 89", "+33 1 98 76 54 32")
    assert press.faxes == ("+33 1 11 22 33 44",)
    assert press.x400s == ("C=FR;A=ATLAS;P=SDMX;O=PRESS;S=OFFICE",)
    assert press.uris == ("https://sdmx.org/press",)
    assert press.emails == ("press@sdmx.org", "media@sdmx.org")


def test_contact_round_trip_preserves_every_field():
    """parse → write → re-parse preserves every Contact attribute."""
    msg = _load_complete()
    rt = read30(write30(msg))
    orig = msg.agency_schemes[0].agencies["SDMX"].contacts[1]
    rt_press = rt.agency_schemes[0].agencies["SDMX"].contacts[1]
    assert rt_press == orig


# ── Multi-language tag round-trip (close #100) ────────────────────────────────


def test_codelist_name_carries_non_en_fr_tags():
    """CL_FREQ.Name carries en + fr + de + es; all four survive parse."""
    cl = _load_complete().codelists["CL_FREQ"]
    assert set(cl.name.langs) >= {"en", "fr", "de", "es"}
    assert cl.name["de"] == "Häufigkeit"
    assert cl.name["es"] == "Frecuencia"


def test_region_coded_language_tags_are_read_lowercase():
    """Region-coded tags (pt-BR, zh-Hans, es-419) are stored lowercase (GH-280),
    and found under any case."""
    code = _load_complete().codelists["CL_FREQ"].codes["A"]
    assert code.name["pt-BR"] == "Anual"
    assert code.name["zh-Hans"] == "每年"
    assert code.name["es-419"] == "Anual"
    assert code.name.langs[3:] == ["pt-br", "zh-hans", "es-419"]


def test_region_coded_tags_round_trip_verbatim():
    """parse → write → re-parse preserves every region/script-coded tag
    on Name elements without folding case or dropping hyphenated tags."""
    msg = _load_complete()
    rt = read30(write30(msg))
    orig_a = msg.codelists["CL_FREQ"].codes["A"]
    rt_a = rt.codelists["CL_FREQ"].codes["A"]
    assert rt_a.name == orig_a.name
    # Spot-check the discriminating tags.
    assert set(rt_a.name.langs) == set(orig_a.name.langs)
    for tag in ("pt-BR", "zh-Hans", "es-419"):
        assert rt_a.name[tag] == orig_a.name[tag]


# ── ReleaseCalendar round-trip (close #101) ───────────────────────────────────


def test_data_constraint_release_calendar_parsed():
    """3.0 DataConstraint ReleaseCalendar parses the three ISO-8601 duration
    children (Periodicity / Offset / Tolerance)."""
    msg = load_xml("3.0", "minimal")
    cc = msg.constraints["CC_TEST"]
    assert cc.release_calendar is not None
    assert cc.release_calendar.periodicity == "P1M"
    assert cc.release_calendar.offset == "P15D"
    assert cc.release_calendar.tolerance == "P2D"


def test_data_constraint_release_calendar_round_trip():
    """parse → write → re-parse preserves the ReleaseCalendar block and
    emits it BEFORE CubeRegion (per the 3.0 XSD sequence)."""
    msg = load_xml("3.0", "minimal")
    rt = read30(write30(msg))
    rt_rc = rt.constraints["CC_TEST"].release_calendar
    assert rt_rc == msg.constraints["CC_TEST"].release_calendar


def test_data_constraint_has_no_reference_period_in_3_0():
    """SDMX 3.0 dropped ReferencePeriod from DataConstraint; the 3.0
    writer must not emit it even if the model carries one (e.g. when
    rehydrated from a 2.1 source)."""
    from datetime import UTC, datetime

    from sdmxlib.model.constraint import ReferencePeriod

    msg = load_xml("3.0", "minimal")
    # Inject a ReferencePeriod into the model — verify the 3.0 writer
    # silently drops it.
    cc = msg.constraints["CC_TEST"]
    object.__setattr__(
        cc,
        "reference_period",
        ReferencePeriod(
            start_period=datetime(2020, 1, 1, tzinfo=UTC),
            end_period=datetime(2023, 12, 31, tzinfo=UTC),
        ),
    )
    rt = read30(write30(msg))
    assert rt.constraints["CC_TEST"].reference_period is None


# ── Constraint coverage gaps: role=Actual + CubeRegion @include=false (#97) ───


def test_data_constraint_role_actual_parsed():
    """The Allowed/Actual role discriminates two semantically different
    constraints — Allowed describes what *can* be reported; Actual
    describes what *is* reported."""
    from sdmxlib.model.constraint import ConstraintType

    msg = _load_complete()
    actual = msg.constraints["CC_ACTUAL"]
    assert actual.constraint_type is ConstraintType.ACTUAL
    # The first constraint stays Allowed — we have one of each role now.
    assert msg.constraints["CC_TEST"].constraint_type is ConstraintType.ALLOWED


def test_data_constraint_actual_round_trip():
    """parse → write → re-parse preserves the role attribute on the
    second-role constraint."""
    from sdmxlib.model.constraint import ConstraintType

    msg = _load_complete()
    rt = read30(write30(msg))
    assert rt.constraints["CC_ACTUAL"].constraint_type is ConstraintType.ACTUAL


def test_cube_region_include_false_parsed():
    """The XSD allows up to two CubeRegions per DataConstraint and uses
    the include flag to discriminate. Our model carries this as the
    boolean ``CubeRegion.include``."""
    msg = _load_complete()
    cc = msg.constraints["CC_TEST"]
    assert len(cc.regions) == 2
    assert cc.regions[0].include is True
    assert cc.regions[1].include is False


def test_cube_region_include_false_round_trip():
    msg = _load_complete()
    rt = read30(write30(msg))
    rt_regions = rt.constraints["CC_TEST"].regions
    assert [r.include for r in rt_regions] == [True, False]
    # The exclude region's KeyValue payload survives too.
    assert rt_regions[1].keys[0].dimension_id == "REF_AREA"
    assert rt_regions[1].keys[0].codes() == frozenset({"XX"})


# ── URN-text ref resolution is type-guarded (#200) ────────────────────────────


_WRONG_TYPED_ENDPOINT = b"""<?xml version="1.0" encoding="UTF-8"?>
<mes:Structure
  xmlns:mes="http://www.sdmx.org/resources/sdmxml/schemas/v3_0/message"
  xmlns:str="http://www.sdmx.org/resources/sdmxml/schemas/v3_0/structure"
  xmlns:com="http://www.sdmx.org/resources/sdmxml/schemas/v3_0/common">
  <mes:Header>
    <mes:ID>WRONG_TYPED_ENDPOINT</mes:ID>
    <mes:Test>false</mes:Test>
    <mes:Prepared>2024-01-01T00:00:00</mes:Prepared>
    <mes:Sender id="TEST"/>
  </mes:Header>
  <mes:Structures>
    <str:ConceptSchemes>
      <str:ConceptScheme agencyID="TEST" id="CS_X" version="1.0">
        <com:Name xml:lang="en">Not a codelist</com:Name>
        <str:Concept id="C1"><com:Name xml:lang="en">C1</com:Name></str:Concept>
      </str:ConceptScheme>
    </str:ConceptSchemes>
    <str:RepresentationMaps>
      <str:RepresentationMap agencyID="TEST" id="RM_WRONG" version="1.0">
        <com:Name xml:lang="en">SourceCodelist pointing at a ConceptScheme</com:Name>
        <str:SourceCodelist>urn:sdmx:org.sdmx.infomodel.conceptscheme.ConceptScheme=TEST:CS_X(1.0)</str:SourceCodelist>
        <str:RepresentationMapping>
          <str:SourceValue>A</str:SourceValue>
          <str:TargetValue>B</str:TargetValue>
        </str:RepresentationMapping>
      </str:RepresentationMap>
    </str:RepresentationMaps>
  </mes:Structures>
</mes:Structure>
"""


def test_urn_text_ref_with_wrong_class_is_rejected():
    """A ``SourceCodelist`` naming a ConceptScheme fails the parse.

    No schema-valid message can produce it — the XSD types that element
    ``AnyCodelistReferenceType`` — and reifying it would put a ConceptScheme
    into a slot the model types for a Codelist. The reader refuses rather than
    admitting a state the model cannot represent.
    """
    from sdmxlib.model.validation import SdmxConformanceError

    # `parse`, not the module reader: a reader refuses the one map and reads
    # on, and it is `parse` that fails the call over it (#435).
    with pytest.raises(SdmxConformanceError, match="requires a Codelist reference"):
        parse(_WRONG_TYPED_ENDPOINT)


def test_urn_text_ref_still_resolves_a_right_typed_artefact():
    """The check does not cost resolution when the URN names the right class."""
    from sdmxlib.model.ref import Ref

    rm = _load_repmap_variants().representation_maps["RM_VARIANTS"]
    (source,) = rm.sources
    assert isinstance(source, Ref)
    assert source.resolved is True
    assert source.urn.id == "CL_SRC"
