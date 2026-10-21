"""Unit tests for SDMX-ML 2.1 writer."""

from pathlib import Path

import pytest
from lxml import etree

from sdmxlib.formats import parse, serialize
from sdmxlib.formats.sdmx_ml21.writer import write
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.collections import ArtefactList, ItemList
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.message import StructureMessage
from tests.support.roundtrip import as_structure, load_xml, roundtrip_xml
from tests.support.xsd import assert_valid_sdmx_ml

pytestmark = pytest.mark.unit

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "sdmx_ml21"

# Three real documents covering different shapes:
#   minimal         — hand-crafted: 1 CL, 1 DSD (1 dim, 1 attr), 1 dataflow
#   ecb_data_structure — ECB EXR1: 11 CLs, 1 DSD (5 dims, 24 attrs, 1 group), 7 dataflows
#   ecb_dataflow    — ECB EXR1 dataflow-only response: 11 CLs, 1 DSD, 1 dataflow
#   with_hierarchy  — flat Codelist + HierarchicalCodelist with a 3-level
#                     nested HierarchicalCode tree (Canada → provinces → cities)
_FIXTURE_NAMES = ["minimal", "ecb_data_structure", "ecb_dataflow", "with_hierarchy"]


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_input_fixture_is_xsd_valid(fixture: str, sdmx_ml21_schema: etree.XMLSchema) -> None:
    """The input fixtures must themselves be SDMX-ML 2.1 XSD-conformant."""
    assert_valid_sdmx_ml((_FIXTURES / f"{fixture}.xml").read_bytes(), sdmx_ml21_schema)


# ── output format (smoke tests on minimal) ────────────────────────────────────


def test_write_returns_bytes():
    assert isinstance(write(load_xml("2.1", "minimal")), bytes)


def test_write_is_valid_xml():
    root = etree.fromstring(write(load_xml("2.1", "minimal")))
    assert root is not None


def test_write_has_structure_root():
    root = etree.fromstring(write(load_xml("2.1", "minimal")))
    assert "Structure" in str(root.tag)


def test_write_pretty_print():
    # Not `b"\n" in`: the declaration ends a line of its own either way (#458).
    plain, pretty = write(load_xml("2.1", "minimal")), write(load_xml("2.1", "minimal"), pretty_print=True)
    assert pretty.count(b"\n") > plain.count(b"\n") == 1


def test_serialize_entrypoint():
    assert isinstance(serialize(load_xml("2.1", "minimal")), bytes)


def test_serialize_unsupported_version():
    with pytest.raises(ValueError, match="Unsupported"):
        serialize(load_xml("2.1", "minimal"), version="4.0")


# ── round-trip: parse → write → re-parse (all three fixtures) ─────────────────


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_roundtrip_codelist_ids(fixture: str):
    orig, rt = roundtrip_xml("2.1", fixture)
    assert rt.codelists.ids() == orig.codelists.ids()


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_roundtrip_codelist_agency(fixture: str):
    orig, rt = roundtrip_xml("2.1", fixture)
    for o, r in zip(orig.codelists, rt.codelists, strict=True):
        assert r.agency_id == o.agency_id


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_roundtrip_codelist_version(fixture: str):
    orig, rt = roundtrip_xml("2.1", fixture)
    for o, r in zip(orig.codelists, rt.codelists, strict=True):
        assert r.version == o.version


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_roundtrip_code_ids(fixture: str):
    orig, rt = roundtrip_xml("2.1", fixture)
    for o, r in zip(orig.codelists, rt.codelists, strict=True):
        assert r.codes.ids() == o.codes.ids()


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_roundtrip_code_names(fixture: str):
    orig, rt = roundtrip_xml("2.1", fixture)
    for o_cl, r_cl in zip(orig.codelists, rt.codelists, strict=True):
        for o_code, r_code in zip(o_cl.codes, r_cl.codes, strict=True):
            assert r_code.name.get("en") == o_code.name.get("en")


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_roundtrip_concept_scheme_ids(fixture: str):
    orig, rt = roundtrip_xml("2.1", fixture)
    assert rt.concept_schemes.ids() == orig.concept_schemes.ids()


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_roundtrip_concept_ids(fixture: str):
    orig, rt = roundtrip_xml("2.1", fixture)
    for o, r in zip(orig.concept_schemes, rt.concept_schemes, strict=True):
        assert r.concepts.ids() == o.concepts.ids()


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_roundtrip_data_structure_ids(fixture: str):
    orig, rt = roundtrip_xml("2.1", fixture)
    assert rt.data_structures.ids() == orig.data_structures.ids()


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_roundtrip_dimension_ids(fixture: str):
    orig, rt = roundtrip_xml("2.1", fixture)
    for o, r in zip(orig.data_structures, rt.data_structures, strict=True):
        assert r.dimensions.ids() == o.dimensions.ids()


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_roundtrip_dimension_order(fixture: str):
    orig, rt = roundtrip_xml("2.1", fixture)
    for o_dsd, r_dsd in zip(orig.data_structures, rt.data_structures, strict=True):
        assert r_dsd.dimensions.ids() == o_dsd.dimensions.ids()


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_roundtrip_time_dimension(fixture: str):
    orig, rt = roundtrip_xml("2.1", fixture)
    for o, r in zip(orig.data_structures, rt.data_structures, strict=True):
        if o.time_dimension is not None:
            assert r.time_dimension is not None
            assert r.time_dimension.id == o.time_dimension.id


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_roundtrip_attribute_ids(fixture: str):
    orig, rt = roundtrip_xml("2.1", fixture)
    for o, r in zip(orig.data_structures, rt.data_structures, strict=True):
        assert r.attributes.ids() == o.attributes.ids()


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_roundtrip_attribute_usage(fixture: str):
    orig, rt = roundtrip_xml("2.1", fixture)
    for o_dsd, r_dsd in zip(orig.data_structures, rt.data_structures, strict=True):
        for o_attr, r_attr in zip(o_dsd.attributes, r_dsd.attributes, strict=True):
            assert r_attr.usage == o_attr.usage


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_roundtrip_measure_ids(fixture: str):
    orig, rt = roundtrip_xml("2.1", fixture)
    for o, r in zip(orig.data_structures, rt.data_structures, strict=True):
        assert r.measures.ids() == o.measures.ids()


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_roundtrip_dataflow_ids(fixture: str):
    orig, rt = roundtrip_xml("2.1", fixture)
    assert rt.dataflows.ids() == orig.dataflows.ids()


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_roundtrip_groups(fixture: str):
    orig, rt = roundtrip_xml("2.1", fixture)
    for o, r in zip(orig.data_structures, rt.data_structures, strict=True):
        o_groups = o.groups or ItemList()
        r_groups = r.groups or ItemList()
        assert r_groups.ids() == o_groups.ids()
        for o_g, r_g in zip(o_groups, r_groups, strict=True):
            assert r_g.dimension_ids == o_g.dimension_ids


# ── Constraints ───────────────────────────────────────────────────────────────


def test_ecb_constraints_roundtrip():
    """ECB fixture has 1 DataConstraint — verify count survives round-trip."""
    orig, rt = roundtrip_xml("2.1", "ecb_dataflow")
    assert len(rt.constraints) == len(orig.constraints)


def test_attachment_constraint_parsed_from_minimal():
    """2.1-only AttachmentConstraint in minimal.xml parses with the right shape."""
    msg = load_xml("2.1", "minimal")
    assert len(msg.attachment_constraints) == 1
    ac = msg.attachment_constraints["AC_TEST"]
    assert ac.agency_id == "TEST"
    assert len(ac.attachment.dataflows) == 1
    (key_set,) = ac.data_key_sets
    assert key_set.is_included
    assert [key.values for key in key_set.keys] == [(("FREQ", "A"),)]


def test_attachment_constraint_roundtrip():
    """Parse → write (XSD-validated) → re-parse preserves AttachmentConstraint."""
    orig, rt = roundtrip_xml("2.1", "minimal")
    assert rt.attachment_constraints.ids() == orig.attachment_constraints.ids()
    assert rt.attachment_constraints["AC_TEST"].data_key_sets == orig.attachment_constraints["AC_TEST"].data_key_sets


def test_attachment_constraint_dropped_by_3_0_writer():
    """3.0 has no AttachmentConstraint XSD type — writer silently drops the slot."""
    from sdmxlib.formats.sdmx_ml30.writer import write as write30

    msg = load_xml("2.1", "minimal")
    assert len(msg.attachment_constraints) == 1
    out = write30(msg)
    assert b"AttachmentConstraint" not in out


def test_attachment_constraint_dropped_by_3_1_writer():
    """3.1 inherits the 3.0 drop — AttachmentConstraint has no 3.1 equivalent."""
    from sdmxlib.formats.sdmx_ml31.writer import write as write31

    msg = load_xml("2.1", "minimal")
    assert len(msg.attachment_constraints) == 1
    out = write31(msg)
    assert b"AttachmentConstraint" not in out


def test_ecb_structure_maps_roundtrip():
    """ECB data structure fixture's maps survive round-trip as top-level lists."""
    orig = load_xml("2.1", "ecb_data_structure")
    rt = as_structure(parse(write(orig)))
    assert len(rt.representation_maps) == len(orig.representation_maps)
    assert len(rt.structure_maps) == len(orig.structure_maps)
    assert len(rt.concept_scheme_maps) == len(orig.concept_scheme_maps)
    assert len(rt.category_scheme_maps) == len(orig.category_scheme_maps)


# ── empty message ─────────────────────────────────────────────────────────────


def test_write_empty_message():
    assert etree.fromstring(write(StructureMessage())) is not None


def test_empty_message_roundtrip():
    rt = as_structure(parse(write(StructureMessage())))
    assert rt.codelists.ids() == []
    assert rt.data_structures.ids() == []


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
    assert rt.codelists["SDMX:CL_FREQ"].codes.ids() == ["A", "Q", "M"]


def test_codelist_multilingual_roundtrip():
    rt = as_structure(parse(write(_codelist_msg())))
    code_a = rt.codelists["SDMX:CL_FREQ"].codes["A"]
    assert code_a.name.get("en") == "Annual"
    assert code_a.name.get("fr") == "Annuel"


def test_codelist_version_roundtrip():
    rt = as_structure(parse(write(_codelist_msg())))
    assert rt.codelists["SDMX:CL_FREQ"].version == "2.0"


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
    rt_cl = as_structure(parse(write(msg))).codelists["TEST:CL_TEST"]
    assert rt_cl.codes["A1"].parent_id == "A"
    assert rt_cl.codes["A2"].parent_id == "A"
    assert rt_cl.codes["A"].parent_id is None
