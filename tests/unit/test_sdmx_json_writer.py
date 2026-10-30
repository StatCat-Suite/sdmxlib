"""Unit tests for SDMX-JSON writer — round-trip, XML→JSON, JSON→XML."""

import json
from pathlib import Path

import pytest

from sdmxlib.formats import parse, serialize
from sdmxlib.formats.sdmx_json.reader import read as read_json
from sdmxlib.formats.sdmx_json.writer import write as write_json
from sdmxlib.formats.sdmx_ml21.reader import read as read_xml21
from sdmxlib.model.collections import ItemList
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import Ref
from tests.support.roundtrip import as_structure, roundtrip_json

pytestmark = pytest.mark.unit

_JSON_FIXTURES = Path(__file__).parent.parent / "fixtures" / "sdmx_json"
_XML21_FIXTURES = Path(__file__).parent.parent / "fixtures" / "sdmx_ml21"
_XML30_FIXTURES = Path(__file__).parent.parent / "fixtures" / "sdmx_ml30"

_JSON_FIXTURE_NAMES = ["minimal", "with_groups", "with_categories"]

# `with_groups` carries two measures (OBS_VALUE, OBS_VALUE_ALT). SDMX-ML 2.1
# has exactly one PrimaryMeasure, so that DSD is valid SDMX 3.0 with no 2.1
# representation and `serialize(..., version="2.1")` now refuses it. The 2.1
# writer used to emit one <PrimaryMeasure> per measure, producing a document
# that failed schema validation — see #224.
_JSON_TO_XML21_FIXTURE_NAMES = ["minimal", "with_categories"]
_XML21_FIXTURE_NAMES = ["minimal", "ecb_data_structure", "ecb_dataflow"]
_XML30_FIXTURE_NAMES = ["minimal", "with_groups", "with_categories"]


def _load_json(name: str) -> StructureMessage:
    return read_json((_JSON_FIXTURES / f"{name}.json").read_bytes())


def _load_xml21(name: str) -> StructureMessage:
    return as_structure(parse((_XML21_FIXTURES / f"{name}.xml").read_bytes()))


def _load_xml30(name: str) -> StructureMessage:
    return as_structure(parse((_XML30_FIXTURES / f"{name}.xml").read_bytes()))


# ── output format (smoke) ─────────────────────────────────────────────────────


def test_write_returns_bytes():
    assert isinstance(write_json(_load_json("minimal")), bytes)


def test_write_is_valid_json():
    doc = json.loads(write_json(_load_json("minimal")))
    assert "data" in doc
    assert "meta" in doc


def test_write_pretty_print():
    assert b"\n" in write_json(_load_json("minimal"), pretty_print=True)


def test_serialize_entrypoint():
    assert isinstance(serialize(_load_json("minimal"), version="json"), bytes)


def test_serialize_unsupported_version():
    with pytest.raises(ValueError, match="Unsupported"):
        serialize(_load_json("minimal"), version="4.0")


def test_empty_message():
    out = write_json(StructureMessage())
    rt = read_json(out)
    assert rt.codelists.ids() == []
    assert rt.data_structures.ids() == []


# ── JSON round-trip: parse → write → re-parse ────────────────────────────────


@pytest.mark.parametrize("fixture", _JSON_FIXTURE_NAMES)
def test_roundtrip_codelist_ids(fixture: str):
    orig = _load_json(fixture)
    rt = roundtrip_json(orig)
    assert rt.codelists.ids() == orig.codelists.ids()


@pytest.mark.parametrize("fixture", _JSON_FIXTURE_NAMES)
def test_roundtrip_codelist_agency(fixture: str):
    orig = _load_json(fixture)
    rt = roundtrip_json(orig)
    for o, r in zip(orig.codelists, rt.codelists, strict=True):
        assert r.agency_id == o.agency_id


@pytest.mark.parametrize("fixture", _JSON_FIXTURE_NAMES)
def test_roundtrip_codelist_version(fixture: str):
    orig = _load_json(fixture)
    rt = roundtrip_json(orig)
    for o, r in zip(orig.codelists, rt.codelists, strict=True):
        assert r.version == o.version


@pytest.mark.parametrize("fixture", _JSON_FIXTURE_NAMES)
def test_roundtrip_code_ids(fixture: str):
    orig = _load_json(fixture)
    rt = roundtrip_json(orig)
    for o, r in zip(orig.codelists, rt.codelists, strict=True):
        assert r.codes.ids() == o.codes.ids()


@pytest.mark.parametrize("fixture", _JSON_FIXTURE_NAMES)
def test_roundtrip_code_names(fixture: str):
    orig = _load_json(fixture)
    rt = roundtrip_json(orig)
    for o_cl, r_cl in zip(orig.codelists, rt.codelists, strict=True):
        for o_code, r_code in zip(o_cl.codes, r_cl.codes, strict=True):
            assert r_code.name.get("en") == o_code.name.get("en")


@pytest.mark.parametrize("fixture", _JSON_FIXTURE_NAMES)
def test_roundtrip_concept_scheme_ids(fixture: str):
    orig = _load_json(fixture)
    rt = roundtrip_json(orig)
    assert rt.concept_schemes.ids() == orig.concept_schemes.ids()


@pytest.mark.parametrize("fixture", _JSON_FIXTURE_NAMES)
def test_roundtrip_concept_ids(fixture: str):
    orig = _load_json(fixture)
    rt = roundtrip_json(orig)
    for o, r in zip(orig.concept_schemes, rt.concept_schemes, strict=True):
        assert r.concepts.ids() == o.concepts.ids()


@pytest.mark.parametrize("fixture", _JSON_FIXTURE_NAMES)
def test_roundtrip_dsd_ids(fixture: str):
    orig = _load_json(fixture)
    rt = roundtrip_json(orig)
    assert rt.data_structures.ids() == orig.data_structures.ids()


@pytest.mark.parametrize("fixture", _JSON_FIXTURE_NAMES)
def test_roundtrip_dimension_ids(fixture: str):
    orig = _load_json(fixture)
    rt = roundtrip_json(orig)
    for o, r in zip(orig.data_structures, rt.data_structures, strict=True):
        assert r.dimensions.ids() == o.dimensions.ids()


@pytest.mark.parametrize("fixture", _JSON_FIXTURE_NAMES)
def test_roundtrip_dimension_order(fixture: str):
    orig = _load_json(fixture)
    rt = roundtrip_json(orig)
    for o_dsd, r_dsd in zip(orig.data_structures, rt.data_structures, strict=True):
        assert r_dsd.dimensions.ids() == o_dsd.dimensions.ids()


@pytest.mark.parametrize("fixture", _JSON_FIXTURE_NAMES)
def test_roundtrip_time_dimension(fixture: str):
    orig = _load_json(fixture)
    rt = roundtrip_json(orig)
    for o, r in zip(orig.data_structures, rt.data_structures, strict=True):
        if o.time_dimension is not None:
            assert r.time_dimension is not None
            assert r.time_dimension.id == o.time_dimension.id


@pytest.mark.parametrize("fixture", _JSON_FIXTURE_NAMES)
def test_roundtrip_attribute_ids(fixture: str):
    orig = _load_json(fixture)
    rt = roundtrip_json(orig)
    for o, r in zip(orig.data_structures, rt.data_structures, strict=True):
        assert r.attributes.ids() == o.attributes.ids()


@pytest.mark.parametrize("fixture", _JSON_FIXTURE_NAMES)
def test_roundtrip_attribute_usage(fixture: str):
    orig = _load_json(fixture)
    rt = roundtrip_json(orig)
    for o_dsd, r_dsd in zip(orig.data_structures, rt.data_structures, strict=True):
        for o_attr, r_attr in zip(o_dsd.attributes, r_dsd.attributes, strict=True):
            assert r_attr.usage == o_attr.usage


@pytest.mark.parametrize("fixture", _JSON_FIXTURE_NAMES)
def test_roundtrip_attribute_attachment(fixture: str):
    orig = _load_json(fixture)
    rt = roundtrip_json(orig)
    for o_dsd, r_dsd in zip(orig.data_structures, rt.data_structures, strict=True):
        for o_attr, r_attr in zip(o_dsd.attributes, r_dsd.attributes, strict=True):
            assert r_attr.attachment == o_attr.attachment


@pytest.mark.parametrize("fixture", _JSON_FIXTURE_NAMES)
def test_roundtrip_measure_ids(fixture: str):
    orig = _load_json(fixture)
    rt = roundtrip_json(orig)
    for o, r in zip(orig.data_structures, rt.data_structures, strict=True):
        assert r.measures.ids() == o.measures.ids()


@pytest.mark.parametrize("fixture", _JSON_FIXTURE_NAMES)
def test_roundtrip_dataflow_ids(fixture: str):
    orig = _load_json(fixture)
    rt = roundtrip_json(orig)
    assert rt.dataflows.ids() == orig.dataflows.ids()


@pytest.mark.parametrize("fixture", _JSON_FIXTURE_NAMES)
def test_roundtrip_dataflow_structure_resolved(fixture: str):
    orig = _load_json(fixture)
    rt = roundtrip_json(orig)
    for o, r in zip(orig.dataflows, rt.dataflows, strict=True):
        if o.structure is not None:
            assert r.structure is not None
            assert r.structure.resolved == o.structure.resolved


def test_roundtrip_groups():
    orig = _load_json("with_groups")
    rt = roundtrip_json(orig)
    for o, r in zip(orig.data_structures, rt.data_structures, strict=True):
        o_groups = o.groups or ItemList()
        r_groups = r.groups or ItemList()
        assert r_groups.ids() == o_groups.ids()
        for o_g, r_g in zip(o_groups, r_groups, strict=True):
            assert r_g.dimension_ids == o_g.dimension_ids


def test_roundtrip_category_schemes():
    orig = _load_json("with_categories")
    rt = roundtrip_json(orig)
    assert rt.category_schemes.ids() == orig.category_schemes.ids()


def test_roundtrip_nested_categories():
    orig = _load_json("with_categories")
    rt = roundtrip_json(orig)
    o_cs = orig.category_schemes["TEST:DATAFLOWS"]
    r_cs = rt.category_schemes["TEST:DATAFLOWS"]
    assert r_cs.categories.ids() == o_cs.categories.ids()
    assert r_cs.categories["MACRO"].categories.ids() == o_cs.categories["MACRO"].categories.ids()


def test_roundtrip_categorisations():
    orig = _load_json("with_categories")
    rt = roundtrip_json(orig)
    assert rt.categorisations.ids() == orig.categorisations.ids()


def test_is_final_does_not_survive_json_round_trip():
    """SDMX-JSON 2.1 doesn't carry ``isFinal`` (#128); the field drops on
    round-trip and the reader defaults it to False."""
    orig = _load_json("minimal")
    rt = roundtrip_json(orig)
    assert rt.codelists["SDMX:CL_FREQ"].is_final is False


def test_roundtrip_enumerated_dimension_resolved():
    rt = roundtrip_json(_load_json("minimal"))
    dim = rt.data_structures[0].dimensions["FREQ"]
    assert isinstance(dim.representation, Ref)
    assert dim.representation.resolved
    assert isinstance(dim.representation, Ref)
    assert dim.representation().id == "CL_FREQ"


def test_roundtrip_multilingual_names():
    orig = _load_json("with_categories")
    rt = roundtrip_json(orig)
    cl = rt.codelists["SDMX:CL_AREA"]
    eu = cl.codes["EU"]
    assert eu.name["en"].text == "European Union"
    assert eu.name["fr"].text == "Union européenne"


# ── XML 2.1 → JSON → re-parse ─────────────────────────────────────────────────


@pytest.mark.parametrize("fixture", _XML21_FIXTURE_NAMES)
def test_xml21_to_json_codelist_ids(fixture: str):
    orig = _load_xml21(fixture)
    rt = read_json(write_json(orig))
    assert rt.codelists.ids() == orig.codelists.ids()


@pytest.mark.parametrize("fixture", _XML21_FIXTURE_NAMES)
def test_xml21_to_json_code_ids(fixture: str):
    orig = _load_xml21(fixture)
    rt = read_json(write_json(orig))
    for o, r in zip(orig.codelists, rt.codelists, strict=True):
        assert r.codes.ids() == o.codes.ids()


@pytest.mark.parametrize("fixture", _XML21_FIXTURE_NAMES)
def test_xml21_to_json_dsd_ids(fixture: str):
    orig = _load_xml21(fixture)
    rt = read_json(write_json(orig))
    assert rt.data_structures.ids() == orig.data_structures.ids()


@pytest.mark.parametrize("fixture", _XML21_FIXTURE_NAMES)
def test_xml21_to_json_dimension_ids(fixture: str):
    orig = _load_xml21(fixture)
    rt = read_json(write_json(orig))
    for o, r in zip(orig.data_structures, rt.data_structures, strict=True):
        assert r.dimensions.ids() == o.dimensions.ids()


@pytest.mark.parametrize("fixture", _XML21_FIXTURE_NAMES)
def test_xml21_to_json_dataflow_ids(fixture: str):
    orig = _load_xml21(fixture)
    rt = read_json(write_json(orig))
    assert rt.dataflows.ids() == orig.dataflows.ids()


# ── XML 3.0 → JSON → re-parse ─────────────────────────────────────────────────


@pytest.mark.parametrize("fixture", _XML30_FIXTURE_NAMES)
def test_xml30_to_json_codelist_ids(fixture: str):
    orig = _load_xml30(fixture)
    rt = read_json(write_json(orig))
    assert rt.codelists.ids() == orig.codelists.ids()


@pytest.mark.parametrize("fixture", _XML30_FIXTURE_NAMES)
def test_xml30_to_json_dsd_ids(fixture: str):
    orig = _load_xml30(fixture)
    rt = read_json(write_json(orig))
    assert rt.data_structures.ids() == orig.data_structures.ids()


@pytest.mark.parametrize("fixture", _XML30_FIXTURE_NAMES)
def test_xml30_to_json_dimension_ids(fixture: str):
    orig = _load_xml30(fixture)
    rt = read_json(write_json(orig))
    for o, r in zip(orig.data_structures, rt.data_structures, strict=True):
        assert r.dimensions.ids() == o.dimensions.ids()


@pytest.mark.parametrize("fixture", _XML30_FIXTURE_NAMES)
def test_xml30_to_json_dataflow_ids(fixture: str):
    orig = _load_xml30(fixture)
    rt = read_json(write_json(orig))
    assert rt.dataflows.ids() == orig.dataflows.ids()


# ── JSON → XML 2.1 → re-parse ────────────────────────────────────────────────


@pytest.mark.parametrize("fixture", _JSON_TO_XML21_FIXTURE_NAMES)
def test_json_to_xml21_codelist_ids(fixture: str):
    orig = _load_json(fixture)
    rt = as_structure(parse(serialize(orig, version="2.1")))
    assert rt.codelists.ids() == orig.codelists.ids()


@pytest.mark.parametrize("fixture", _JSON_TO_XML21_FIXTURE_NAMES)
def test_json_to_xml21_code_ids(fixture: str):
    orig = _load_json(fixture)
    rt = as_structure(parse(serialize(orig, version="2.1")))
    for o, r in zip(orig.codelists, rt.codelists, strict=True):
        assert r.codes.ids() == o.codes.ids()


@pytest.mark.parametrize("fixture", _JSON_TO_XML21_FIXTURE_NAMES)
def test_json_to_xml21_dsd_ids(fixture: str):
    orig = _load_json(fixture)
    rt = as_structure(parse(serialize(orig, version="2.1")))
    assert rt.data_structures.ids() == orig.data_structures.ids()


@pytest.mark.parametrize("fixture", _JSON_TO_XML21_FIXTURE_NAMES)
def test_json_to_xml21_dimension_ids(fixture: str):
    orig = _load_json(fixture)
    rt = as_structure(parse(serialize(orig, version="2.1")))
    for o, r in zip(orig.data_structures, rt.data_structures, strict=True):
        assert r.dimensions.ids() == o.dimensions.ids()


@pytest.mark.parametrize("fixture", _JSON_TO_XML21_FIXTURE_NAMES)
def test_json_to_xml21_dataflow_ids(fixture: str):
    orig = _load_json(fixture)
    rt = as_structure(parse(serialize(orig, version="2.1")))
    assert rt.dataflows.ids() == orig.dataflows.ids()


# ── JSON → XML 3.0 → re-parse ────────────────────────────────────────────────


@pytest.mark.parametrize("fixture", _JSON_FIXTURE_NAMES)
def test_json_to_xml30_codelist_ids(fixture: str):
    orig = _load_json(fixture)
    rt = as_structure(parse(serialize(orig, version="3.0")))
    assert rt.codelists.ids() == orig.codelists.ids()


@pytest.mark.parametrize("fixture", _JSON_FIXTURE_NAMES)
def test_json_to_xml30_dsd_ids(fixture: str):
    orig = _load_json(fixture)
    rt = as_structure(parse(serialize(orig, version="3.0")))
    assert rt.data_structures.ids() == orig.data_structures.ids()


@pytest.mark.parametrize("fixture", _JSON_FIXTURE_NAMES)
def test_json_to_xml30_dimension_ids(fixture: str):
    orig = _load_json(fixture)
    rt = as_structure(parse(serialize(orig, version="3.0")))
    for o, r in zip(orig.data_structures, rt.data_structures, strict=True):
        assert r.dimensions.ids() == o.dimensions.ids()


@pytest.mark.parametrize("fixture", _JSON_FIXTURE_NAMES)
def test_json_to_xml30_dataflow_ids(fixture: str):
    orig = _load_json(fixture)
    rt = as_structure(parse(serialize(orig, version="3.0")))
    assert rt.dataflows.ids() == orig.dataflows.ids()


# ── OrganisationSchemes ───────────────────────────────────────────────────────


def test_agency_scheme_json_roundtrip():
    orig = read_xml21((_XML21_FIXTURES / "minimal.xml").read_bytes())
    rt = read_json(write_json(orig))
    assert rt.agency_schemes.ids() == orig.agency_schemes.ids()
    orig_scheme = orig.agency_schemes[0]
    rt_scheme = rt.agency_schemes[0]
    assert rt_scheme.agencies.ids() == orig_scheme.agencies.ids()
    assert rt_scheme.agencies["SDMX"].name["en"] == orig_scheme.agencies["SDMX"].name["en"]


def test_constraint_json_roundtrip():
    """Data constraints survive, and 2.1 attachment constraints convert.

    SDMX-JSON follows the 3.0 model, which unified 2.1's two constraint
    artefacts into one. So an `AttachmentConstraint` is written as the
    equivalent `DataConstraint` rather than dropped — this test previously
    asserted only ``["CC_TEST"]`` came back, which encoded the drop (#244).
    """
    orig = read_xml21((_XML21_FIXTURES / "minimal.xml").read_bytes())
    rt = read_json(write_json(orig))
    converted = [ac.id for ac in orig.attachment_constraints]
    assert rt.constraints.ids() == [*orig.constraints.ids(), *converted]
    assert converted, "fixture no longer carries an attachment constraint; pick another"
    orig_cc = orig.constraints["CC_TEST"]
    rt_cc = rt.constraints["CC_TEST"]
    assert rt_cc.constraint_type == orig_cc.constraint_type
    assert len(rt_cc.regions) == len(orig_cc.regions)
    assert rt_cc.regions[0].keys[0].values == orig_cc.regions[0].keys[0].values


def test_representation_map_json_roundtrip():
    """The JSON layer keeps the 2.1-style ``structureSets`` wrapper but our
    model carries maps top-level; round-trip exercises the synthesize-on-write,
    flatten-on-read path."""
    orig = read_xml21((_XML21_FIXTURES / "minimal.xml").read_bytes())
    rt = read_json(write_json(orig))
    assert rt.representation_maps.ids() == orig.representation_maps.ids()
    orig_rm = orig.representation_maps[0]
    rt_rm = rt.representation_maps[0]
    assert rt_rm.id == orig_rm.id
    assert rt_rm.mappings == orig_rm.mappings
