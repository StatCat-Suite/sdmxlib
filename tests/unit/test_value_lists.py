"""SDMX 3.x's ValueList is modelled, not refused (#422).

A `ValueList` is a maintainable of plain values — a codelist without hierarchy
or item identity — and a component's representation may enumerate by one
wherever it may by a codelist. The censuses prove every format and the store
carry each field (the maximal fixture holds one, and a concept it enumerates);
these pin the component accessors, the wire spellings, SDMX-JSON 2.0's
misspelt class, what 2.1 leaves out, and the store.
"""

import functools
import json
from collections.abc import Callable

import pytest
from lxml import etree

import sdmxlib as sl
from sdmxlib.formats.gaps import dropped, unrepresentable
from sdmxlib.formats.sdmx_json import reader as json_reader
from sdmxlib.formats.sdmx_json import writer as json_writer
from sdmxlib.formats.sdmx_ml21 import reader as ml21_reader
from sdmxlib.formats.sdmx_ml21 import writer as ml21_writer
from sdmxlib.formats.sdmx_ml30 import reader as ml30_reader
from sdmxlib.formats.sdmx_ml30 import writer as ml30_writer
from sdmxlib.formats.sdmx_ml31 import reader as ml31_reader
from sdmxlib.formats.sdmx_ml31 import writer as ml31_writer
from sdmxlib.model.codelist import CodelistLike, EnumerationLike
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import Ref
from sdmxlib.model.validation import ValidationError
from sdmxlib.model.valuelist import ValueItem, ValueList
from tests.support.factories import concept_ref
from tests.support.json_schema import assert_valid_sdmx_json, compile_json_schema
from tests.support.xsd import SchemaVersion, assert_valid_sdmx_ml, compile_structure_schema

pytestmark = pytest.mark.unit

_V3: dict[SchemaVersion, tuple[Callable[[StructureMessage], bytes], Callable[[bytes], StructureMessage]]] = {
    "3.0": (ml30_writer.write, ml30_reader.read),
    "3.1": (ml31_writer.write, ml31_reader.read),
}


@functools.cache
def _xsd(version: SchemaVersion) -> etree.XMLSchema:
    return compile_structure_schema(version)


def _units() -> ValueList:
    return ValueList(
        id="VL_UNIT",
        maintainer="ESTAT",
        version="1.0.0",
        name={"en": "Units"},
        items=[
            ValueItem(id="kg", name={"en": "Kilogram"}, description={"en": "SI mass"}),
            # A value is any string, which is what the class is for.
            ValueItem(id="m 2"),
            ValueItem(id="%", name={"en": "Percent"}),
        ],
    )


def _message() -> StructureMessage:
    units = _units()
    freq = sl.Codelist(
        id="CL_FREQ", maintainer="ESTAT", version="1.0.0", name={"en": "f"}, codes=[sl.Code(id="A", name={"en": "a"})]
    )
    concepts = sl.ConceptScheme(
        id="CS",
        maintainer="ESTAT",
        version="1.0.0",
        name={"en": "cs"},
        concepts=[
            sl.Concept(id="FREQ", name={"en": "f"}),
            sl.Concept(id="UNIT", name={"en": "u"}, core_representation=Ref.to(units)),
            sl.Concept(id="OBS_VALUE", name={"en": "o"}),
        ],
    )
    dsd = sl.DataStructure(
        id="DSD",
        maintainer="ESTAT",
        version="1.0.0",
        name={"en": "d"},
        dimensions=[sl.Dimension(id="FREQ", concept=concepts.ref_to("FREQ"), representation=Ref.to(freq))],
        attributes=[
            sl.Attribute(
                id="UNIT",
                concept=concepts.ref_to("UNIT"),
                representation=Ref.to(units),
                attachment=sl.AttachmentLevel.DATASET,
            )
        ],
        measures=[sl.Measure(id="OBS_VALUE", concept=concepts.ref_to("OBS_VALUE"))],
    )
    rmap = sl.RepresentationMap(
        id="RM",
        maintainer="ESTAT",
        version="1.0.0",
        name={"en": "rm"},
        sources=(Ref.to(units),),
        targets=(Ref.to(freq),),
    )
    return StructureMessage(
        codelists=[freq],
        value_lists=[units],
        concept_schemes=[concepts],
        data_structures=[dsd],
        representation_maps=[rmap],
    )


# ── the model ─────────────────────────────────────────────────────────────


def test_a_value_list_has_its_own_urn_class() -> None:
    assert str(_units().urn) == "urn:sdmx:org.sdmx.infomodel.codelist.ValueList=ESTAT:VL_UNIT(1.0.0)"


def test_a_value_list_is_an_enumeration_but_not_a_codelist() -> None:
    """It answers ids, labels and a size — not `Code` objects, which it does not have."""
    units = _units()
    assert isinstance(units, EnumerationLike)
    assert not isinstance(units, CodelistLike)
    assert units.code_ids() == ["kg", "m 2", "%"]
    assert units.code_map(lang="en") == {"kg": "Kilogram", "m 2": "m 2", "%": "Percent"}
    assert len(units) == 3


def test_a_representation_takes_a_value_list_its_urn_or_a_ref() -> None:
    units = _units()
    for given in (units, units.urn, Ref.to(units)):
        attribute = sl.Attribute(
            attachment=sl.AttachmentLevel.DATASET, id="UNIT", representation=given, concept=concept_ref("UNIT")
        )
        assert isinstance(attribute.representation, Ref)
        assert attribute.representation.urn == units.urn


def test_a_value_list_component_answers_the_enumeration_accessors() -> None:
    unit = _message().data_structures["DSD"].attributes["UNIT"]
    assert unit.is_enumerated
    assert unit.code_ids() == ["kg", "m 2", "%"]
    assert unit.resolved_enumeration is unit.value_list
    assert unit.value_list.id == "VL_UNIT"


def test_a_value_list_component_is_not_a_codelist_one() -> None:
    unit = _message().data_structures["DSD"].attributes["UNIT"]
    assert unit.resolved_codelist is None
    assert unit.codes is None
    with pytest.raises(TypeError, match="enumerated by a ValueList; read it with `value_list`"):
        _ = unit.codelist


def test_a_codelist_component_has_no_value_list() -> None:
    freq = _message().data_structures["DSD"].dimensions["FREQ"]
    assert freq.codelist.id == "CL_FREQ"
    with pytest.raises(TypeError, match="enumerated by a codelist; read it with `codelist`"):
        _ = freq.value_list


def test_the_dsd_label_map_includes_a_value_list_component() -> None:
    dsd = _message().data_structures["DSD"]
    assert dsd.code_map(lang="en")["UNIT"] == {"kg": "Kilogram", "m 2": "m 2", "%": "Percent"}
    # `all_codelists` is what it says: codelists only.
    assert set(dsd.all_codelists) == {"FREQ"}


# ── SDMX-ML 3.x ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("version", sorted(_V3))
def test_sdmx_ml_3x_round_trips_a_value_list_and_its_references(version: SchemaVersion) -> None:
    write, read = _V3[version]
    written = write(_message())
    assert_valid_sdmx_ml(written, _xsd(version))
    back = read(written)
    assert list(back.value_lists) == [_units()]
    unit = back.data_structures["DSD"].attributes["UNIT"]
    assert unit.value_list == _units(), "the reference resolves against the message"
    core = back.concept_schemes["CS"].concepts["UNIT"].core_representation
    assert isinstance(core, Ref)
    assert core.urn == _units().urn
    (source,) = back.representation_maps["RM"].sources
    assert isinstance(source, Ref)
    assert str(source.urn) == str(_units().urn)
    assert back.refused == ()
    assert back.lost == ()


@pytest.mark.parametrize("version", sorted(_V3))
def test_sdmx_ml_3x_spells_a_value_list_as_the_xsd_does(version: SchemaVersion) -> None:
    write, _ = _V3[version]
    root = etree.fromstring(write(StructureMessage(value_lists=[_units()])))
    str_ns = next(v for v in root.nsmap.values() if v.endswith("/structure"))
    items = root.findall(f".//{{{str_ns}}}ValueLists/{{{str_ns}}}ValueList/{{{str_ns}}}ValueItem")
    assert [item.get("id") for item in items] == ["kg", "m 2", "%"]


# ── SDMX-JSON ──────────────────────────────────────────────────────────────


def test_sdmx_json_round_trips_a_value_list_and_its_references() -> None:
    written = json_writer.write(_message())
    assert_valid_sdmx_json(written, compile_json_schema("structure", "2.0"))
    assert_valid_sdmx_json(written, compile_json_schema("structure", "2.1"))
    back = json_reader.read(written)
    assert list(back.value_lists) == [_units()]
    assert back.data_structures["DSD"].attributes["UNIT"].value_list == _units()
    assert back.refused == ()


def test_sdmx_json_writes_no_self_link_for_a_value_list() -> None:
    """SDMX-JSON 2.0's generic `urn` pattern misspells the class `Valuelist`."""
    body = json.loads(json_writer.write(StructureMessage(value_lists=[_units()])))
    (entry,) = body["data"]["valueLists"]
    assert "links" not in entry
    assert [item["id"] for item in entry["valueItems"]] == ["kg", "m 2", "%"]


def test_sdmx_json_refuses_a_value_list_where_no_enumeration_is() -> None:
    """Only an enumeration's own pattern spells `ValueList` in 2.0; elsewhere it is unwritable."""
    categorisation = sl.Categorisation(
        id="CAT",
        maintainer="ESTAT",
        name={"en": "c"},
        source=Ref.to(_units()),
        target=Ref[object].unresolved(
            sl.SdmxUrn.parse("urn:sdmx:org.sdmx.infomodel.categoryscheme.Category=ESTAT:CS(1.0).C")
        ),
    )
    with pytest.raises(ValidationError, match=r"codelist\.ValueList is not a class SDMX-JSON can reference"):
        sl.serialize(StructureMessage(categorisations=[categorisation]), version="json")


# ── SDMX-ML 2.1 ────────────────────────────────────────────────────────────


def test_sdmx_ml_2_1_leaves_out_a_value_list_and_what_names_it() -> None:
    left_out = {gap.urn.split("=", 1)[1]: gap.cause.split("=", 1)[1] for gap in dropped(_message(), "2.1")}
    assert left_out == {
        "ESTAT:VL_UNIT(1.0.0)": "ESTAT:VL_UNIT(1.0.0)",
        "ESTAT:CS(1.0.0)": "ESTAT:VL_UNIT(1.0.0)",
        "ESTAT:DSD(1.0.0)": "ESTAT:VL_UNIT(1.0.0)",
        "ESTAT:RM(1.0.0)": "ESTAT:VL_UNIT(1.0.0)",
    }
    back = ml21_reader.read(ml21_writer.write(_message()))
    assert [cl.id for cl in back.codelists] == ["CL_FREQ"], "the codelist names nothing it lacks"
    assert not back.data_structures
    assert not back.concept_schemes


def test_sdmx_ml_2_1_declares_why() -> None:
    assert [gap.field for gap in unrepresentable(_units(), "2.1")] == ["sdmx_class"]
    dsd = _message().data_structures["DSD"]
    assert "references" in [gap.field for gap in unrepresentable(dsd, "2.1")]
    assert unrepresentable(_units(), "3.0") == ()


# ── the store ──────────────────────────────────────────────────────────────


def test_the_store_gives_back_a_value_list_and_the_references_to_it() -> None:
    annotated = ValueList(
        id="VL_A",
        maintainer="ESTAT",
        version="1.0.0",
        name={"en": "a"},
        items=[ValueItem(id="a b", annotations=sl.Annotations([sl.Annotation(type="T", title="note")]))],
    )
    with sl.LocalRegistry(":memory:") as registry:
        for artefact in [*_message().all_artefacts(), annotated]:
            registry.add(artefact)
        assert registry.get(_units().urn) == _units()
        assert registry.get(annotated.urn) == annotated, "a value's annotations, keyed by list and value"
        dsd = registry.get(sl.DataStructure.urn_for(agency="ESTAT", id="DSD", version="1.0.0"))
        assert isinstance(dsd, sl.DataStructure)
        representation = dsd.attributes["UNIT"].representation
        assert isinstance(representation, Ref)
        assert representation.urn.artefact_class == "ValueList"
        assert dsd.attributes["UNIT"].code_ids() == ["kg", "m 2", "%"]


def test_a_value_list_re_added_replaces_its_values() -> None:
    shorter = ValueList(id="VL_UNIT", maintainer="ESTAT", version="1.0.0", name={"en": "u"}, items=[ValueItem(id="g")])
    with sl.LocalRegistry(":memory:") as registry:
        registry.add(_units())
        registry.add(shorter)
        assert registry.get(shorter.urn) == shorter


@pytest.mark.parametrize("version", ["3.0", "3.1", "json", "2.1"])
def test_serialize_validates_a_message_with_a_value_list(version: str) -> None:
    """Through `serialize`, whose validation refuses an unknown reference class.

    SDMX-JSON lets a value list stand only where an enumeration is, and 2.1
    leaves it out with its referrers before it validates — so a valid message
    writes cleanly to every target, which calling a writer directly skips.
    """
    back = sl.parse(sl.serialize(_message(), version=version))
    assert isinstance(back, StructureMessage)
    assert [vl.id for vl in back.value_lists] == ([] if version == "2.1" else ["VL_UNIT"])
