"""Tests for model/convert.py — to_dict and from_dict."""

import pytest

from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.convert import from_dict, to_dict
from sdmxlib.model.datastructure import (
    AttachmentLevel,
    Attribute,
    DataStructure,
    Dimension,
    Measure,
)
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.ref import Ref
from sdmxlib.model.representation import Facet
from tests.support.factories import concept_ref

pytestmark = pytest.mark.unit


def _concept(concept_id: str) -> str:
    """A component's concept as `to_dict` writes one: its URN (every component needs one, #492)."""
    return str(concept_ref(concept_id).urn)


_CL_URN = "urn:sdmx:org.sdmx.infomodel.codelist.Codelist=SDMX:CL_FREQ(1.0)"
_CS_URN = "urn:sdmx:org.sdmx.infomodel.conceptscheme.Concept=SDMX:CS_COMMON(1.0).FREQ"
_DSD_URN = "urn:sdmx:org.sdmx.infomodel.datastructure.DataStructure=ESTAT:CPI(1.0)"
_DF_URN = "urn:sdmx:org.sdmx.infomodel.datastructure.Dataflow=ESTAT:DF_CPI(1.0)"


# ── to_dict ───────────────────────────────────────────────────────────────────


def test_to_dict_codelist_scalars():
    cl = Codelist(name={"en": "CL_FREQ"}, id="CL_FREQ", maintainer="SDMX", version="1.0")
    d = to_dict(cl)
    assert d["id"] == "CL_FREQ"
    assert d["agency_id"] == "SDMX"
    assert d["version"] == "1.0"


def test_to_dict_istring_name():
    cl = Codelist(id="CL_FREQ", maintainer="SDMX", name={"en": "Frequency", "fr": "Fréquence"})
    d = to_dict(cl)
    assert d["name"] == {"en": "Frequency", "fr": "Fréquence"}


def test_to_dict_empty_istring_omitted():
    """A description is optional, so it can be empty; a name cannot (#492)."""
    cl = Codelist(name={"en": "CL_FREQ"}, id="CL_FREQ", maintainer="SDMX")
    d = to_dict(cl)
    assert "description" not in d or d.get("description") is None or d["description"] == {}


def test_to_dict_codes_list():
    cl = Codelist(
        name={"en": "CL_FREQ"},
        id="CL_FREQ",
        maintainer="SDMX",
        codes=[Code(id="A", name={"en": "Annual"}), Code(name={"en": "Q"}, id="Q")],
    )
    d = to_dict(cl)
    assert isinstance(d["codes"], list)
    assert d["codes"][0]["id"] == "A"
    assert d["codes"][0]["name"] == {"en": "Annual"}
    assert d["codes"][1]["id"] == "Q"


def test_to_dict_code_parent_id():
    code = Code(name={"en": "M"}, id="M", parent_id="Q")
    d = to_dict(code)
    assert d["parent_id"] == "Q"


def test_to_dict_enumeration_representation():
    urn = Codelist.parse_urn(_CL_URN)
    dim = Dimension(id="FREQ", representation=Ref[Codelist].unresolved(urn), concept=concept_ref("FREQ"))
    d = to_dict(dim)
    assert d["representation"] == {"kind": "Enumeration", "urn": _CL_URN}


def test_to_dict_facet_representation():
    dim = Dimension(id="X", representation=Facet(type="String", max_length=50), concept=concept_ref("X"))
    d = to_dict(dim)
    representation = d["representation"]
    assert isinstance(representation, dict)
    assert representation["kind"] == "Facet"
    assert representation["type"] == "String"
    assert representation["max_length"] == 50


def test_to_dict_attachment_level():
    attr = Attribute(id="CONF_STATUS", attachment=AttachmentLevel.OBSERVATION, concept=concept_ref("CONF_STATUS"))
    d = to_dict(attr)
    assert d["attachment"] == "observation"


def test_to_dict_dsd_nested():
    dsd = DataStructure(
        name={"en": "CPI"},
        id="CPI",
        maintainer="ESTAT",
        dimensions=[
            Dimension(id="FREQ", concept=concept_ref("FREQ")),
            Dimension(id="REF_AREA", concept=concept_ref("REF_AREA")),
        ],
        measures=[Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )
    d = to_dict(dsd)
    # `to_dict` is a generic serializer, so every nested value is `object`.
    dimensions = d["dimensions"]
    measures = d["measures"]
    assert isinstance(dimensions, list)
    assert isinstance(measures, list)
    assert len(dimensions) == 2
    assert dimensions[0]["id"] == "FREQ"
    assert measures[0]["id"] == "OBS_VALUE"


def test_to_dict_none_fields_omitted():
    dim = Dimension(id="FREQ")
    d = to_dict(dim)
    assert "representation" not in d
    assert "concept" not in d


# ── from_dict ─────────────────────────────────────────────────────────────────


def test_from_dict_codelist_scalars():
    cl = from_dict(Codelist, {"id": "CL_FREQ", "agency_id": "SDMX", "version": "2.0", "name": {"en": "f"}})
    assert cl.id == "CL_FREQ"
    assert cl.agency_id == "SDMX"
    assert cl.version == "2.0"


def test_from_dict_codelist_name_coerced():
    cl = from_dict(Codelist, {"id": "CL_FREQ", "agency_id": "SDMX", "name": {"en": "Frequency"}})
    assert isinstance(cl.name, InternationalString)
    assert cl.name["en"].text == "Frequency"


def test_from_dict_codes_recursive():
    data = {
        "id": "CL_FREQ",
        "agency_id": "SDMX",
        "name": {"en": "Frequency"},
        "codes": [
            {"id": "A", "name": {"en": "Annual"}},
            {"id": "Q", "name": {"en": "Quarterly"}},
        ],
    }
    cl = from_dict(Codelist, data)
    assert cl.codes["A"].name["en"].text == "Annual"
    assert cl.codes["Q"].id == "Q"


def test_from_dict_representation_enumeration():
    data = {
        "id": "FREQ",
        "representation": {"kind": "Enumeration", "urn": _CL_URN},
    }
    dim = from_dict(Dimension, data)
    assert isinstance(dim.representation, Ref)
    assert str(dim.representation.urn) == _CL_URN


def test_from_dict_representation_facet():
    data = {
        "id": "X",
        "representation": {"kind": "Facet", "type": "String", "max_length": 100},
    }
    dim = from_dict(Dimension, data)
    assert isinstance(dim.representation, Facet)
    assert dim.representation.type == "String"
    assert dim.representation.max_length == 100


def test_from_dict_attachment_level():
    data = {"id": "CONF_STATUS", "attachment": "observation"}
    attr = from_dict(Attribute, data)
    assert attr.attachment is AttachmentLevel.OBSERVATION


def test_from_dict_dsd_with_nested_dimensions():
    data = {
        "id": "CPI",
        "agency_id": "ESTAT",
        "name": {"en": "CPI"},
        "dimensions": [
            {"id": "FREQ", "concept": _concept("FREQ"), "representation": {"kind": "Enumeration", "urn": _CL_URN}},
            {"id": "REF_AREA", "concept": _concept("REF_AREA")},
        ],
        "measures": [{"id": "OBS_VALUE", "concept": _concept("OBS_VALUE")}],
    }
    dsd = from_dict(DataStructure, data)
    assert isinstance(dsd.dimensions["FREQ"].representation, Ref)
    assert not dsd.dimensions["FREQ"].representation.resolved
    assert dsd.measures["OBS_VALUE"].id == "OBS_VALUE"


def test_from_dict_unknown_keys_ignored():
    data = {"id": "CL_FREQ", "agency_id": "SDMX", "name": {"en": "f"}, "unknown_field": "ignored"}
    cl = from_dict(Codelist, data)
    assert cl.id == "CL_FREQ"


def test_from_dict_raises_for_non_dataclass():
    with pytest.raises(TypeError):
        from_dict(str, {"id": "x"})


# ── Round-trip ────────────────────────────────────────────────────────────────


def test_round_trip_codelist():
    cl = Codelist(
        id="CL_FREQ",
        maintainer="SDMX",
        name={"en": "Frequency"},
        codes=[Code(id="A", name={"en": "Annual"}), Code(name={"en": "Q"}, id="Q")],
    )
    d = to_dict(cl)
    cl2 = from_dict(Codelist, d)
    assert cl2.id == cl.id
    assert cl2.name["en"].text == "Frequency"
    assert cl2.codes["A"].name["en"].text == "Annual"


def test_round_trip_dimension_with_ref():
    urn = Codelist.parse_urn(_CL_URN)
    dim = Dimension(id="FREQ", representation=Ref[Codelist].unresolved(urn), concept=concept_ref("FREQ"))
    d = to_dict(dim)
    dim2 = from_dict(Dimension, d)
    assert dim2.id == "FREQ"
    assert isinstance(dim2.representation, Ref)
    assert str(dim2.representation.urn) == _CL_URN


def test_round_trip_dsd():
    dsd = DataStructure(
        id="CPI",
        maintainer="ESTAT",
        name={"en": "Consumer Price Index"},
        dimensions=[
            Dimension(id="FREQ", concept=concept_ref("FREQ")),
            Dimension(id="REF_AREA", concept=concept_ref("REF_AREA")),
        ],
        attributes=[
            Attribute(id="CONF_STATUS", attachment=AttachmentLevel.OBSERVATION, concept=concept_ref("CONF_STATUS"))
        ],
        measures=[Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )
    d = to_dict(dsd)
    dsd2 = from_dict(DataStructure, d)
    assert dsd2.id == "CPI"
    assert dsd2.name["en"].text == "Consumer Price Index"
    assert dsd2.dimensions["FREQ"].id == "FREQ"
    assert dsd2.dimensions.ids() == ["FREQ", "REF_AREA"]
    assert dsd2.attributes["CONF_STATUS"].attachment is AttachmentLevel.OBSERVATION


def test_round_trip_attribute_related_dimensions():
    """to_dict handles list[str] fields like Attribute.related_dimensions."""
    dsd = DataStructure(
        id="DSD_TEST",
        maintainer="STC",
        name={"en": "Test"},
        dimensions=[Dimension(id="DIM1", concept=concept_ref("DIM1"))],
        measures=[Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
        attributes=[
            Attribute(
                id="UNIT_MEASURE",
                attachment=AttachmentLevel.SERIES,
                related_dimensions=["DIM1"],
                representation=Facet(type="String"),
                concept=concept_ref("UNIT_MEASURE"),
            )
        ],
    )
    d = to_dict(dsd)
    attributes = d["attributes"]
    assert isinstance(attributes, list)
    assert attributes[0]["related_dimensions"] == ["DIM1"]

    dsd2 = from_dict(DataStructure, d)
    assert dsd2.attributes["UNIT_MEASURE"].related_dimensions == ["DIM1"]


def test_from_dict_rebuilds_a_time_dimension() -> None:
    """`time_dimension` is the one component field `from_dict` reconstructs by
    name rather than by position, and its branch was reached only from a
    documentation driver until the building tutorial started running from the
    page itself."""
    dsd = from_dict(
        DataStructure,
        {
            "id": "DSD_T",
            "agency_id": "AG",
            "version": "1.0",
            "name": {"en": "t"},
            "dimensions": [{"id": "FREQ", "concept": _concept("FREQ")}],
            "time_dimension": {"id": "TIME_PERIOD", "concept": _concept("TIME_PERIOD")},
            "measures": [{"id": "OBS_VALUE", "concept": _concept("OBS_VALUE")}],
        },
    )
    assert dsd.time_dimension is not None
    assert dsd.time_dimension.id == "TIME_PERIOD"
