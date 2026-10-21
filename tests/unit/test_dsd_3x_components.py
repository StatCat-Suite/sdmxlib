"""SDMX 3.0's additions to a DSD's components are held, not dropped (#422).

The field census (`test_field_coverage`) and the store census prove each field
survives every format that can carry it, and `gaps.py` declares the 2.1 losses.
These pin the parts a census cannot see: the spellings (`"unbounded"`,
`optional="true"`, `areDimensionsOptional`), the position rule, and validation.
"""

import functools
import json
import re

import pytest
from attrs import evolve
from lxml import etree

import sdmxlib as sl
from sdmxlib.formats._positions import check_positions
from sdmxlib.formats.sdmx_json import reader as json_reader
from sdmxlib.formats.sdmx_json import writer as json_writer
from sdmxlib.formats.sdmx_ml30 import reader as ml30_reader
from sdmxlib.formats.sdmx_ml30 import writer as ml30_writer
from sdmxlib.model.concept import Concept
from sdmxlib.model.datastructure import (
    AttachmentLevel,
    Attribute,
    DataStructure,
    Dimension,
    Measure,
    MetadataAttributeUsage,
)
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.metadatastructure import MetadataStructure
from sdmxlib.model.ref import Ref
from sdmxlib.model.representation import Facet
from sdmxlib.model.urn import SdmxUrn
from tests.support.factories import concept_ref
from tests.support.json_schema import assert_valid_sdmx_json, compile_json_schema
from tests.support.xsd import assert_valid_sdmx_ml, compile_structure_schema

pytestmark = pytest.mark.unit

_STR30 = "{http://www.sdmx.org/resources/sdmxml/schemas/v3_0/structure}"
_TEXT = Facet(type="String")


def _concept(item: str) -> Ref[Concept]:
    urn = SdmxUrn.make(
        Concept, package="conceptscheme", artefact_class="Concept", agency="AG", id="CS", version="1.0", item_id=item
    )
    return Ref[Concept].unresolved(urn)


@functools.cache
def _xsd() -> etree.XMLSchema:
    return compile_structure_schema("3.0")


def _dsd() -> DataStructure:
    return DataStructure(
        id="DSD",
        maintainer="AG",
        version="1.0",
        name={"en": "DSD"},
        dimensions=[
            Dimension(id="A", concept=_concept("A"), representation=_TEXT),
            Dimension(id="B", concept=_concept("B"), representation=_TEXT),
        ],
        attributes=[
            Attribute(
                id="ATTR",
                concept=_concept("ATTR"),
                attachment=AttachmentLevel.SERIES,
                related_dimensions=["A", "B"],
                optional_dimensions={"B"},
                related_measures=("M2",),
                representation=_TEXT,
                min_occurs=0,
                max_occurs=None,
            )
        ],
        measures=[
            Measure(id="M1", concept=_concept("M1"), representation=_TEXT, max_occurs=4),
            Measure(id="M2", concept=_concept("M2"), representation=_TEXT),
        ],
        metadata=MetadataStructure.urn_for(agency="AG", id="MSD", version="1.0"),
        metadata_attributes=[
            MetadataAttributeUsage(
                id="NOTE", attachment=AttachmentLevel.SERIES, related_dimensions=["A"], optional_dimensions={"A"}
            )
        ],
    )


def _xml(dsd: DataStructure) -> bytes:
    return ml30_writer.write(StructureMessage(data_structures=[dsd]))


_MSD_URN = "urn:sdmx:org.sdmx.infomodel.metadatastructure.MetadataStructure=AG:MSD(1.0)"


def _json(dsd: DataStructure) -> dict[str, object]:
    loaded = json.loads(json_writer.write(StructureMessage(data_structures=[dsd])))
    assert isinstance(loaded, dict)
    return loaded


def _at(value: object, *path: str | int) -> object:
    """`value[path[0]][path[1]]…`, narrowing at each step."""
    for key in path:
        if isinstance(key, int):
            assert isinstance(value, list)
            value = value[key]
        else:
            assert isinstance(value, dict)
            value = value[key]
    return value


def _dsd_json(body: dict[str, object]) -> object:
    return _at(body, "data", "dataStructures", 0)


# ── the spellings ─────────────────────────────────────────────────────────


def test_sdmx_ml_spells_each_addition_as_the_schema_does() -> None:
    xml = _xml(_dsd())
    assert_valid_sdmx_ml(xml, _xsd())
    doc = etree.fromstring(xml)
    attr = next(doc.iter(f"{_STR30}Attribute"))
    representation = attr.find(f"{_STR30}LocalRepresentation")
    assert representation is not None
    assert (representation.get("minOccurs"), representation.get("maxOccurs")) == ("0", "unbounded")
    assert [d.get("optional") for d in attr.iter(f"{_STR30}Dimension")] == [None, "true"]
    assert [m.text for m in attr.iter(f"{_STR30}Measure")] == ["M2"]
    usage = next(doc.iter(f"{_STR30}MetadataAttributeUsage"))
    assert usage.get("urn") is None, "the 3.0 XSD's urn pattern for it is an erratum"
    assert usage.findtext(f"{_STR30}MetadataAttributeReference") == "NOTE"
    assert doc.findtext(f".//{_STR30}DataStructure/{_STR30}Metadata") == _MSD_URN


def test_sdmx_ml_reads_back_what_it_wrote() -> None:
    dsd = _dsd()
    back = ml30_reader.read(_xml(dsd)).data_structures[0]
    assert back.attributes == dsd.attributes
    assert back.measures == dsd.measures
    assert back.metadata_attributes == dsd.metadata_attributes
    assert back.metadata is not None
    assert back.metadata.urn == MetadataStructure.urn_for(agency="AG", id="MSD", version="1.0")


def test_sdmx_json_spells_each_addition_as_the_schema_does() -> None:
    body = _json(_dsd())
    assert_valid_sdmx_json(json.dumps(body).encode(), compile_json_schema("structure"))
    dsd = _dsd_json(body)
    attr = _at(dsd, "dataStructureComponents", "attributeList", "attributes", 0)
    assert _at(attr, "localRepresentation", "minOccurs") == 0
    assert _at(attr, "localRepresentation", "maxOccurs") == "unbounded"
    assert _at(attr, "attributeRelationship") == {"dimensions": ["A", "B"], "areDimensionsOptional": [False, True]}
    assert _at(attr, "measureRelationship") == ["M2"]
    usage = _at(dsd, "dataStructureComponents", "attributeList", "metadataAttributeUsages", 0)
    assert _at(usage, "metadataAttributeReference") == "NOTE"
    assert _at(dsd, "metadata") == _MSD_URN


def test_sdmx_json_counts_positions_from_zero() -> None:
    """ "Starting at 0", the schema says; the writer counted from 1 until #422."""
    dims = _at(_dsd_json(_json(_dsd())), "dataStructureComponents", "dimensionList", "dimensions")
    assert isinstance(dims, list)
    assert [_at(d, "position") for d in dims] == [0, 1]


def test_sdmx_json_reads_back_what_it_wrote() -> None:
    dsd = _dsd()
    back = json_reader.read(json_writer.write(StructureMessage(data_structures=[dsd]))).data_structures[0]
    assert back.attributes == dsd.attributes
    assert back.measures == dsd.measures
    assert back.metadata_attributes == dsd.metadata_attributes


def test_no_measure_relationship_means_all_measures() -> None:
    back = ml30_reader.read(_xml(evolve(_dsd(), attributes=[evolve(_dsd().attributes[0], related_measures=None)])))
    assert back.data_structures[0].attributes[0].related_measures is None


# ── position: derived, and refused when it disagrees ──────────────────────


def _with_positions(*positions: str) -> bytes:
    doc = etree.fromstring(_xml(_dsd()))
    dimensions = doc.findall(f".//{_STR30}DimensionList/{_STR30}Dimension")
    for el, position in zip(dimensions, positions, strict=True):
        el.set("position", position)
    return etree.tostring(doc)


@pytest.mark.parametrize(
    "positions",
    [("1", "2"), ("0", "1"), ("1", "3"), ("0", "2")],
    ids=["from 1", "from 0", "a gap", "a gap from 0"],
)
def test_a_position_consistent_with_document_order_is_read(positions: tuple[str, str]) -> None:
    back = ml30_reader.read(_with_positions(*positions))
    assert back.refused == ()
    assert back.data_structures[0].dimensions.ids() == ["A", "B"]


@pytest.mark.parametrize(
    "positions",
    [("2", "1"), ("5", "3"), ("1", "1"), ("1", "x")],
    ids=["swapped", "decreasing with a gap", "repeated", "not a number"],
)
def test_a_position_that_disagrees_refuses_the_dsd(positions: tuple[str, str]) -> None:
    back = ml30_reader.read(_with_positions(*positions))
    (refusal,) = back.refused
    assert "position=" in refusal.reason
    assert not back.data_structures


def test_a_json_position_that_disagrees_refuses_the_dsd() -> None:
    body = _json(_dsd())
    first, second = (_at(_dsd_json(body), "dataStructureComponents", "dimensionList", "dimensions", i) for i in (0, 1))
    assert isinstance(first, dict)
    assert isinstance(second, dict)
    first["position"], second["position"] = 1, 0
    back = json_reader.read(json.dumps(body).encode())
    (refusal,) = back.refused
    assert (
        "dimension 'B' comes after 'A' in the dimension list but says position=0, not more than its 1" in refusal.reason
    )


def test_eurostats_gapped_positions_are_read() -> None:
    """`ESTAT:DSB_ICTEG01(7.0)` as published: 1, 2, 3, 5, 6 — a dimension removed, order kept (#449)."""
    check_positions([("freq", "1"), ("indic_is", "2"), ("unit", "3"), ("geo", "5"), ("ind_type", "6")], element="x")


# ── 2.1 says what it cannot carry ─────────────────────────────────────────


def test_unrepresentable_names_each_2_1_loss() -> None:
    assert sorted(gap.field for gap in sl.unrepresentable(_dsd(), "2.1")) == [
        "attributes[].max_occurs",
        "attributes[].min_occurs",
        "attributes[].optional_dimensions",
        "attributes[].related_measures",
        "measures[].max_occurs",
        "metadata",
        "metadata_attributes",
    ]


def test_a_dsd_without_the_additions_loses_nothing_to_2_1() -> None:
    plain = evolve(
        _dsd(),
        attributes=[
            evolve(_dsd().attributes[0], optional_dimensions=(), related_measures=None, min_occurs=1, max_occurs=1)
        ],
        measures=[
            Measure(id="M1", representation=_TEXT, concept=concept_ref("M1")),
            Measure(id="M2", representation=_TEXT, concept=concept_ref("M2")),
        ],
        metadata=None,
        metadata_attributes=[],
    )
    assert [gap.field for gap in sl.unrepresentable(plain, "2.1") if gap.field != "version"] == []


# ── validation ────────────────────────────────────────────────────────────


_ATTR = _dsd().attributes[0]


@pytest.mark.parametrize(
    ("attribute", "path"),
    [
        pytest.param(evolve(_ATTR, min_occurs=3, max_occurs=2), "attributes[ATTR].max_occurs", id="min above max"),
        pytest.param(evolve(_ATTR, max_occurs=0), "attributes[ATTR].max_occurs", id="max zero"),
        pytest.param(evolve(_ATTR, related_measures=("NOPE",)), "attributes[ATTR].related_measures", id="no measure"),
        pytest.param(
            evolve(_ATTR, optional_dimensions={"C"}), "attributes[ATTR].optional_dimensions", id="unrelated optional"
        ),
    ],
)
def test_an_addition_that_says_nothing_coherent_is_refused(attribute: Attribute, path: str) -> None:
    with pytest.raises(sl.ValidationError, match=re.escape(path)):
        evolve(_dsd(), attributes=[attribute])


def test_the_example_is_valid() -> None:
    """The discrimination check for the cases above: it builds."""
    _dsd()
