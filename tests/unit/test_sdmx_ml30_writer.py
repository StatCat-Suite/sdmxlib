"""Unit tests for SDMX-ML 3.0 writer."""

from pathlib import Path

import attrs
import pytest
from lxml import etree

from sdmxlib.formats import parse, serialize
from sdmxlib.formats.sdmx_ml30.writer import write
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.collections import ArtefactList, ItemList
from sdmxlib.model.datastructure import AttributeUsage
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.message import StructureMessage
from tests.support.roundtrip import as_structure, load_xml, roundtrip_xml
from tests.support.xsd import assert_valid_sdmx_ml

pytestmark = pytest.mark.unit

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "sdmx_ml30"

# Three fixtures covering different shapes:
#   minimal         — 1 CL, 1 CS, 1 DSD (1 dim + time + 1 attr + 1 measure), 1 dataflow,
#                     category scheme + categorisation
#   with_groups     — 2 CLs, 1 CS, 1 DSD (3 dims + 2 attrs + 2 measures + group), 1 dataflow
#   with_categories — 2 CLs, 1 CS, 1 DSD (2 dims + time + 2 attrs + 1 measure),
#                     2 dataflows, 1 cat scheme (nested), 2 categorisations
#   complete        — one of every modelled 3.0 artefact (used by
#                     test_roundtrip_complete; also schema-validated here)
#   with_metadata   — MSD with nested MetadataAttribute tree (codelist +
#                     facet representation) and Metadataflow with Target
_FIXTURE_NAMES = [
    "minimal",
    "with_groups",
    "with_categories",
    "complete",
    "with_metadata",
    "with_codelist_extension",
    "with_representation_map_variants",
    "with_hierarchy_association",
    "with_multiple_hierarchies",
]


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_input_fixture_is_xsd_valid(fixture: str, sdmx_ml30_schema: etree.XMLSchema) -> None:
    """The hand-written input fixtures must themselves be XSD-conformant.

    The reader is intentionally permissive, so a fixture bug can hide here
    indefinitely. Validating the fixtures directly surfaces those.
    """
    assert_valid_sdmx_ml((_FIXTURES / f"{fixture}.xml").read_bytes(), sdmx_ml30_schema)


# ── output format (smoke tests on minimal) ────────────────────────────────────


def test_write_returns_bytes():
    assert isinstance(write(load_xml("3.0", "minimal")), bytes)


def test_write_is_valid_xml():
    root = etree.fromstring(write(load_xml("3.0", "minimal")))
    assert root is not None


def test_write_has_structure_root():
    root = etree.fromstring(write(load_xml("3.0", "minimal")))
    assert "Structure" in str(root.tag)


def test_write_uses_v30_namespace():
    data = write(load_xml("3.0", "minimal"))
    assert b"v3_0" in data


def test_write_pretty_print():
    assert b"\n" in write(load_xml("3.0", "minimal"), pretty_print=True)


def test_serialize_entrypoint():
    assert isinstance(serialize(load_xml("3.0", "minimal"), version="3.0"), bytes)


def test_serialize_unsupported_version():
    with pytest.raises(ValueError, match="Unsupported"):
        serialize(load_xml("3.0", "minimal"), version="4.0")


# ── 3.0-specific XML output checks ───────────────────────────────────────────


def test_write_uses_measure_not_primary_measure():
    """Output must use <str:Measure>, not <str:PrimaryMeasure>."""
    data = write(load_xml("3.0", "minimal"))
    assert b"Measure " in data or b"Measure>" in data
    assert b"PrimaryMeasure" not in data


def test_write_uses_observation_not_primary_measure_in_relationship():
    """AttributeRelationship/Observation per v3.0.0 XSD (not 2.1's PrimaryMeasure).

    An earlier transition called this <ObservationValue/>; the v3.0.0 schema
    uses the shorter <Observation/>.
    """
    data = write(load_xml("3.0", "minimal"))
    assert b"<str:Observation/>" in data
    assert b"PrimaryMeasure" not in data


def test_write_uses_usage_not_assignment_status():
    """Attribute element must carry usage=, not assignmentStatus=; optional, its default, not at all (#503)."""
    msg = load_xml("3.0", "minimal")
    dsd = msg.data_structures[0]
    (first, *rest) = dsd.attributes
    mandatory = attrs.evolve(first, usage=AttributeUsage.MANDATORY)
    data = write(attrs.evolve(msg, data_structures=[attrs.evolve(dsd, attributes=[mandatory, *rest])]))
    assert data.count(b"usage=") == 1
    assert b'usage="mandatory"' in data
    assert b"assignmentStatus" not in data


def test_write_group_uses_dimension_reference():
    """GroupDimension must use <DimensionReference>id</...> per v3.0.0 XSD.

    An earlier cleanup-phase comment claimed `dimensionRef=` was the 3.0
    shape; the actual schema requires the same nested DimensionReference
    element as 2.1, just with the id as text content (not a wrapped Ref).
    """
    data = write(load_xml("3.0", "with_groups"))
    assert b"<str:DimensionReference>" in data
    assert b"dimensionRef=" not in data


# ── round-trip: parse → write → re-parse (all three fixtures) ─────────────────


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_roundtrip_codelist_ids(fixture: str):
    orig, rt = roundtrip_xml("3.0", fixture)
    assert rt.codelists.ids() == orig.codelists.ids()


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_roundtrip_codelist_agency(fixture: str):
    orig, rt = roundtrip_xml("3.0", fixture)
    for o, r in zip(orig.codelists, rt.codelists, strict=True):
        assert r.agency_id == o.agency_id


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_roundtrip_codelist_version(fixture: str):
    orig, rt = roundtrip_xml("3.0", fixture)
    for o, r in zip(orig.codelists, rt.codelists, strict=True):
        assert r.version == o.version


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_roundtrip_code_ids(fixture: str):
    orig, rt = roundtrip_xml("3.0", fixture)
    for o, r in zip(orig.codelists, rt.codelists, strict=True):
        assert r.codes.ids() == o.codes.ids()


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_roundtrip_code_names(fixture: str):
    orig, rt = roundtrip_xml("3.0", fixture)
    for o_cl, r_cl in zip(orig.codelists, rt.codelists, strict=True):
        for o_code, r_code in zip(o_cl.codes, r_cl.codes, strict=True):
            assert r_code.name.get("en") == o_code.name.get("en")


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_roundtrip_concept_scheme_ids(fixture: str):
    orig, rt = roundtrip_xml("3.0", fixture)
    assert rt.concept_schemes.ids() == orig.concept_schemes.ids()


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_roundtrip_concept_ids(fixture: str):
    orig, rt = roundtrip_xml("3.0", fixture)
    for o, r in zip(orig.concept_schemes, rt.concept_schemes, strict=True):
        assert r.concepts.ids() == o.concepts.ids()


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_roundtrip_data_structure_ids(fixture: str):
    orig, rt = roundtrip_xml("3.0", fixture)
    assert rt.data_structures.ids() == orig.data_structures.ids()


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_roundtrip_dimension_ids(fixture: str):
    orig, rt = roundtrip_xml("3.0", fixture)
    for o, r in zip(orig.data_structures, rt.data_structures, strict=True):
        assert r.dimensions.ids() == o.dimensions.ids()


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_roundtrip_dimension_order(fixture: str):
    orig, rt = roundtrip_xml("3.0", fixture)
    for o_dsd, r_dsd in zip(orig.data_structures, rt.data_structures, strict=True):
        assert r_dsd.dimensions.ids() == o_dsd.dimensions.ids()


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_roundtrip_time_dimension(fixture: str):
    orig, rt = roundtrip_xml("3.0", fixture)
    for o, r in zip(orig.data_structures, rt.data_structures, strict=True):
        if o.time_dimension is not None:
            assert r.time_dimension is not None
            assert r.time_dimension.id == o.time_dimension.id


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_roundtrip_attribute_ids(fixture: str):
    orig, rt = roundtrip_xml("3.0", fixture)
    for o, r in zip(orig.data_structures, rt.data_structures, strict=True):
        assert r.attributes.ids() == o.attributes.ids()


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_roundtrip_attribute_usage(fixture: str):
    orig, rt = roundtrip_xml("3.0", fixture)
    for o_dsd, r_dsd in zip(orig.data_structures, rt.data_structures, strict=True):
        for o_attr, r_attr in zip(o_dsd.attributes, r_dsd.attributes, strict=True):
            assert r_attr.usage == o_attr.usage


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_roundtrip_attribute_attachment(fixture: str):
    orig, rt = roundtrip_xml("3.0", fixture)
    for o_dsd, r_dsd in zip(orig.data_structures, rt.data_structures, strict=True):
        for o_attr, r_attr in zip(o_dsd.attributes, r_dsd.attributes, strict=True):
            assert r_attr.attachment == o_attr.attachment


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_roundtrip_measure_ids(fixture: str):
    orig, rt = roundtrip_xml("3.0", fixture)
    for o, r in zip(orig.data_structures, rt.data_structures, strict=True):
        assert r.measures.ids() == o.measures.ids()


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_roundtrip_dataflow_ids(fixture: str):
    orig, rt = roundtrip_xml("3.0", fixture)
    assert rt.dataflows.ids() == orig.dataflows.ids()


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_roundtrip_groups(fixture: str):
    orig, rt = roundtrip_xml("3.0", fixture)
    for o, r in zip(orig.data_structures, rt.data_structures, strict=True):
        o_groups = o.groups or ItemList()
        r_groups = r.groups or ItemList()
        assert r_groups.ids() == o_groups.ids()
        for o_g, r_g in zip(o_groups, r_groups, strict=True):
            assert r_g.dimension_ids == o_g.dimension_ids


@pytest.mark.parametrize("fixture", ["minimal", "with_categories"])
def test_roundtrip_category_scheme_ids(fixture: str):
    orig, rt = roundtrip_xml("3.0", fixture)
    assert rt.category_schemes.ids() == orig.category_schemes.ids()


@pytest.mark.parametrize("fixture", ["minimal", "with_categories"])
def test_roundtrip_categorisation_ids(fixture: str):
    orig, rt = roundtrip_xml("3.0", fixture)
    assert rt.categorisations.ids() == orig.categorisations.ids()


# ── empty message ─────────────────────────────────────────────────────────────


def test_write_empty_message():
    assert etree.fromstring(write(StructureMessage())) is not None


def test_empty_message_roundtrip():
    rt = as_structure(parse(write(StructureMessage())))
    assert rt.codelists.ids() == []
    assert rt.data_structures.ids() == []


# ── multiple measures (SDMX 3.0 feature) ─────────────────────────────────────


def test_multiple_measures_roundtrip():
    orig, rt = roundtrip_xml("3.0", "with_groups")
    dsd_o = orig.data_structures["DSD_FX"]
    dsd_r = rt.data_structures["DSD_FX"]
    assert dsd_r.measures.ids() == dsd_o.measures.ids()
    assert len(dsd_r.measures) == 2


# ── codelist-only message ─────────────────────────────────────────────────────


def _codelist_msg() -> StructureMessage:
    codes = ItemList(
        [
            Code(id="A", name=InternationalString.of({"en": "Annual", "fr": "Annuel"})),
            Code(id="Q", name=InternationalString.of({"en": "Quarterly"})),
            Code(id="M", name=InternationalString.of({"en": "Monthly"})),
        ]
    )
    cl = Codelist(
        id="CL_FREQ",
        maintainer="SDMX",
        version="2.0",
        name=InternationalString.of({"en": "Frequency"}),
        codes=codes,
    )
    return StructureMessage(codelists=ArtefactList([cl]))


def test_codelist_only_roundtrip():
    rt = as_structure(parse(write(_codelist_msg())))
    assert rt.codelists["CL_FREQ"].codes.ids() == ["A", "Q", "M"]


def test_codelist_multilingual_roundtrip():
    rt = as_structure(parse(write(_codelist_msg())))
    code_a = rt.codelists["CL_FREQ"].codes["A"]
    assert code_a.name["en"].text == "Annual"
    assert code_a.name["fr"].text == "Annuel"


def test_codelist_version_roundtrip():
    rt = as_structure(parse(write(_codelist_msg())))
    assert rt.codelists["CL_FREQ"].version == "2.0"


# ── hierarchical codes ────────────────────────────────────────────────────────


def test_code_parent_roundtrip():
    codes = ItemList(
        [
            Code(id="A", name={"en": "A"}),
            Code(id="A1", name={"en": "A1"}, parent_id="A"),
            Code(id="A2", name={"en": "A2"}, parent_id="A"),
        ]
    )
    cl = Codelist(id="CL_TEST", maintainer="TEST", name={"en": "Test"}, codes=codes)
    msg = StructureMessage(codelists=ArtefactList([cl]))
    rt_cl = as_structure(parse(write(msg))).codelists["CL_TEST"]
    assert rt_cl.codes["A1"].parent_id == "A"
    assert rt_cl.codes["A2"].parent_id == "A"
    assert rt_cl.codes["A"].parent_id is None
