"""Unit tests for SDMX-ML 3.1 writer.

3.1 has only a small surface of divergences from 3.0 (Categorisation @version
prohibited, DataConstraint @role and <ReleaseCalendar> removed); the bulk of
the read/write logic is inherited from the 3.0 module. These tests focus on
proving the divergences are honoured and XSD-valid round-trips work for the
common-case fixture.
"""

from pathlib import Path

import pytest
from lxml import etree

from sdmxlib.formats import parse, serialize
from sdmxlib.formats.sdmx_ml31.reader import read as read31
from sdmxlib.formats.sdmx_ml31.writer import write
from tests.support.roundtrip import as_structure, load_xml, roundtrip_xml, structure_schema
from tests.support.xsd import assert_valid_sdmx_ml

pytestmark = pytest.mark.unit

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "sdmx_ml31"

_FIXTURE_NAMES = [
    "minimal",
    "complete",
    "with_categories",
    "with_codelist_extension",
    "with_groups",
    "with_hierarchy_association",
    "with_metadata",
    "with_representation_map_variants",
    "with_structure_map",
]


# ── output format smoke ───────────────────────────────────────────────────────


def test_write_returns_bytes():
    assert isinstance(write(load_xml("3.1", "minimal")), bytes)


def test_write_pretty_print():
    assert b"\n" in write(load_xml("3.1", "minimal"), pretty_print=True)


def test_serialize_entrypoint_routes_to_3_1():
    """serialize(version=\"3.1\") routes to the 3.1 writer; output validates."""
    out = serialize(load_xml("3.1", "minimal"), version="3.1")
    assert_valid_sdmx_ml(out, structure_schema("3.1"))


# ── input fixture XSD-validity ────────────────────────────────────────────────


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_input_fixture_is_xsd_valid(fixture: str, sdmx_ml31_schema: etree.XMLSchema) -> None:
    """Hand-written 3.1 fixtures must themselves validate against the v3.1.0 XSD."""
    assert_valid_sdmx_ml((_FIXTURES / f"{fixture}.xml").read_bytes(), sdmx_ml31_schema)


# ── 3.1 divergences from 3.0 (close #103, #104) ───────────────────────────────


def test_categorisation_emitted_without_version_attribute():
    """Per the 3.1 Categorisation XSD, @version is prohibited (#103). Even
    when the model carries version=\"1.0\" (the default), the 3.1 writer
    must not emit a version attribute."""
    out = write(load_xml("3.1", "minimal"))
    root = etree.fromstring(out)
    ns = {"str": "http://www.sdmx.org/resources/sdmxml/schemas/v3_1/structure"}
    [cat_el] = root.findall(".//str:Categorisation", ns)
    assert "version" not in cat_el.attrib


def test_data_constraint_emitted_without_role_attribute():
    """Per the 3.1 DataConstraint XSD, @role was removed (#104). Even when
    the model holds constraint_type=ConstraintType.ALLOWED, the 3.1 writer
    must not emit a role attribute."""
    out = write(load_xml("3.1", "minimal"))
    root = etree.fromstring(out)
    ns = {"str": "http://www.sdmx.org/resources/sdmxml/schemas/v3_1/structure"}
    [cc_el] = root.findall(".//str:DataConstraint", ns)
    assert "role" not in cc_el.attrib


def test_data_constraint_does_not_emit_release_calendar():
    """3.1 dropped <ReleaseCalendar> from the DataConstraint sequence — the
    semantic moved to the new AvailabilityConstraint artefact. Even when
    the model carries a populated release_calendar (e.g. from a 3.0 source)
    the 3.1 writer must not emit the element."""
    from sdmxlib.model.constraint import ReleaseCalendar

    msg = load_xml("3.1", "minimal")
    object.__setattr__(
        msg.constraints["CC_TEST"],
        "release_calendar",
        ReleaseCalendar(periodicity="P1M", offset="P15D", tolerance="P2D"),
    )
    out = write(msg)
    assert b"ReleaseCalendar" not in out
    # And the result still XSD-validates.
    assert_valid_sdmx_ml(out, structure_schema("3.1"))


# ── round-trip (parse → write → XSD-validate → re-parse) ──────────────────────


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_roundtrip_codelist_ids(fixture: str):
    orig, rt = roundtrip_xml("3.1", fixture)
    assert rt.codelists.ids() == orig.codelists.ids()


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_roundtrip_constraint_ids(fixture: str):
    orig, rt = roundtrip_xml("3.1", fixture)
    assert rt.constraints.ids() == orig.constraints.ids()


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_roundtrip_categorisation_ids(fixture: str):
    orig, rt = roundtrip_xml("3.1", fixture)
    assert rt.categorisations.ids() == orig.categorisations.ids()


def test_parse_auto_detects_3_1_namespace():
    """as_structure(parse()) with no version hint routes a v3_1-namespaced message to
    the 3.1 reader (separate from the 3.0 path)."""
    data = (_FIXTURES / "minimal.xml").read_bytes()
    msg = as_structure(parse(data))
    # Read directly through the 3.1 reader, compare slot ids.
    direct = read31(data)
    assert msg.codelists.ids() == direct.codelists.ids()
