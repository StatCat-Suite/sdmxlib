"""SDMX-ML 2.1's ``<RepresentationMapping>``, held (#422).

A 2.1 component map says how its values map in one of three ways. A
``<CodelistMap>`` names a codelist map of the same structure set, and is that
map's URN in `ComponentMap.representation_map` — the 3.x shape. A
``<ValueMap>`` lists value pairs inline, which 3.x keeps only in a maintainable
map: the reader builds one, marked `BUILT_VALUE_MAP_TYPE`, and a 2.1 write
puts it back inline. ``<ToTextFormat>`` and ``<ToValueType>`` have no 3.x form
and are held as themselves.

`tests/unit/test_field_coverage.py` proves the maximal fixture's three
component maps survive every format and the store; this file holds what that
cannot say — the wire shape, the built map's identity, the refusals and the
gaps. The maximal message carries what 2.1 refuses on purpose, so it is
written here by the writers themselves, as the loss tests write it, and a
refusal is looked for among the structure map's issues alone.
"""

import attrs
import pytest
from lxml import etree

import sdmxlib as sl
from sdmxlib.formats import serialize
from sdmxlib.formats.gaps import unrepresentable
from sdmxlib.formats.sdmx_json import reader as json_reader
from sdmxlib.formats.sdmx_json import writer as json_writer
from sdmxlib.formats.sdmx_ml21 import reader as ml21_reader
from sdmxlib.formats.sdmx_ml21 import writer as ml21_writer
from sdmxlib.formats.sdmx_ml30 import reader as ml30_reader
from sdmxlib.formats.sdmx_ml30 import writer as ml30_writer
from sdmxlib.formats.sdmx_ml31 import reader as ml31_reader
from sdmxlib.formats.sdmx_ml31 import writer as ml31_writer
from sdmxlib.model.mapping import BUILT_VALUE_MAP_TYPE, is_built_value_map
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.validation import ValidationError
from tests.support import maximal

pytestmark = pytest.mark.unit

_STR = "{http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure}"
_ROUND_TRIPS = {
    "3.0": (ml30_writer.write, ml30_reader.read),
    "3.1": (ml31_writer.write, ml31_reader.read),
    "json": (json_writer.write, json_reader.read),
}


def _mappings(xml: bytes) -> list[etree._Element | None]:
    """Each written component map's ``<RepresentationMapping>``, in order."""
    doc = etree.fromstring(xml)
    return [cm.find(f"{_STR}RepresentationMapping") for cm in doc.iter(f"{_STR}ComponentMap")]


def _via_21(msg: StructureMessage | None = None) -> StructureMessage:
    return ml21_reader.read(ml21_writer.write(maximal.message() if msg is None else msg))


def test_sdmx_ml_2_1_writes_each_form_inside_its_component_map() -> None:
    codelist_map, value_map, text_format = _mappings(ml21_writer.write(maximal.message()))
    assert codelist_map is not None
    assert [el.get("id") for el in codelist_map.iterfind(f"{_STR}CodelistMap/Ref")] == ["RM"]
    assert value_map is not None
    assert [(el.get("source"), el.get("target")) for el in value_map.iter(f"{_STR}ValueMapping")] == [("1", "M")]
    assert text_format is not None
    assert text_format.find(f"{_STR}ToTextFormat") is not None
    assert text_format.findtext(f"{_STR}ToValueType") == "Name"


def test_a_built_value_map_is_written_inline_and_never_as_an_artefact() -> None:
    doc = etree.fromstring(ml21_writer.write(maximal.message()))
    assert [el.get("id") for el in doc.iter(f"{_STR}CodelistMap") if el.get("id")] == ["RM"]


def test_sdmx_ml_2_1_reads_each_form() -> None:
    original = maximal.message()
    (sm,) = _via_21().structure_maps
    codelist_map, value_map, text_format = sm.maps
    assert codelist_map.representation_map is not None
    assert codelist_map.representation_map.urn == sl.artefact_urn(original.representation_maps[0])
    assert value_map.representation_map is not None
    assert value_map.representation_map.urn == sl.artefact_urn(original.representation_maps[1])
    assert (text_format.to_text_format, text_format.to_value_type) == (
        original.structure_maps[0].maps[2].to_text_format,
        sl.ToValueType.NAME,
    )


def test_a_value_map_is_read_as_a_built_data_type_map() -> None:
    built = next(m for m in _via_21().representation_maps if m.id == "SM_VALUE_MAP_2@SS")
    assert BUILT_VALUE_MAP_TYPE in built.annotations.types
    assert built.sources == (sl.MapDataType("String"),)
    assert built.targets == (sl.MapDataType("String"),)
    assert built.name == maximal.message().structure_maps[0].name, "the structure map's name, no language invented"
    assert is_built_value_map(built)


def test_a_built_value_map_is_not_grouped_in_its_structure_set() -> None:
    """The set holds the structure map; the pairs are the structure map's."""
    back = _via_21()
    grouped = {str(ref.urn) for s in back.structure_sets for ref in s.maps}
    assert not {str(sl.artefact_urn(m)) for m in back.representation_maps if is_built_value_map(m)} & grouped


@pytest.mark.parametrize("version", sorted(_ROUND_TRIPS))
def test_a_built_value_map_is_a_data_type_map_in_3x_and_inline_again_in_2_1(version: str) -> None:
    write, read = _ROUND_TRIPS[version]
    via = read(write(maximal.message()))
    assert is_built_value_map(next(m for m in via.representation_maps if m.id == "SM_VALUE_MAP_2@SS"))
    value_map = _mappings(ml21_writer.write(via))[1]
    assert value_map is not None
    assert value_map.find(f"{_STR}ValueMap") is not None


def test_a_data_type_map_without_the_mark_is_still_refused_in_2_1() -> None:
    """Only a map the 2.1 reader built goes inline; any other still has no 2.1 form (#242)."""
    msg = maximal.message()
    unmarked = attrs.evolve(msg.representation_maps[1], annotations=sl.Annotations())
    issues = _issues(attrs.evolve(msg, representation_maps=[msg.representation_maps[0], unmarked]))
    assert any(path.startswith("RepresentationMap:SM_VALUE_MAP_2@SS.sources") for path in issues)


def _issues(msg: StructureMessage) -> list[str]:
    """The paths of every refusal a 2.1 write of `msg` meets."""
    with pytest.raises(ValidationError) as caught:
        serialize(msg, version="2.1")
    return [issue.path for issue in caught.value.issues]


def _with_maps(*comp_maps: sl.ComponentMap) -> StructureMessage:
    msg = maximal.message()
    (sm,) = msg.structure_maps
    return attrs.evolve(msg, structure_maps=[attrs.evolve(sm, maps=list(comp_maps))])


def _ours(paths: list[str]) -> list[str]:
    return [path for path in paths if path.startswith("StructureMap:SM@SS.maps")]


def test_the_maximal_structure_map_is_writable_in_2_1() -> None:
    """Discrimination for the refusals below: the maximal message writes, structure map and all.

    It used to be refused for other artefacts, so this filtered the refusals
    for the structure map's own; since #421 nothing in it is refused.
    """
    serialize(maximal.message(), version="2.1")


@pytest.mark.parametrize(
    "comp_map",
    [
        pytest.param(
            sl.ComponentMap(sources=["DIM"], targets=["DIM"], to_text_format=sl.Facet(type="String")),
            id="format-alone",
        ),
        pytest.param(
            sl.ComponentMap(sources=["DIM"], targets=["DIM"], to_value_type=sl.ToValueType.VALUE),
            id="value-type-alone",
        ),
    ],
)
def test_sdmx_ml_2_1_refuses_half_a_text_format_mapping(comp_map: sl.ComponentMap) -> None:
    assert _ours(_issues(_with_maps(comp_map))) == ["StructureMap:SM@SS.maps[0].to_value_type"]


def test_sdmx_ml_2_1_refuses_a_text_format_beside_a_map() -> None:
    comp_map = sl.ComponentMap(
        sources=["DIM"],
        targets=["DIM"],
        representation_map=sl.Ref.to(maximal.message().representation_maps[0]),
        to_text_format=sl.Facet(type="String"),
        to_value_type=sl.ToValueType.VALUE,
    )
    assert _ours(_issues(_with_maps(comp_map))) == ["StructureMap:SM@SS.maps[0].to_text_format"]


@pytest.mark.parametrize("version", ["3.0", "3.1", "json"])
def test_3x_drops_a_text_format_mapping_and_says_so(version: str) -> None:
    (sm,) = maximal.message().structure_maps
    fields = {gap.field for gap in unrepresentable(sm, version)}
    assert {"maps[].to_text_format", "maps[].to_value_type"} <= fields


def test_2_1_names_a_map_of_its_own_agency_and_version_and_no_other() -> None:
    (sm,) = maximal.message().structure_maps
    assert "maps[].representation_map" not in {gap.field for gap in unrepresentable(sm, "2.1")}
    elsewhere = sl.Ref[object].unresolved(sl.RepresentationMap.urn_for(agency="OTHER", id="RM", version="1.0"))
    moved = attrs.evolve(sm, maps=[sl.ComponentMap(sources=["DIM"], targets=["DIM"], representation_map=elsewhere)])
    assert "maps[].representation_map" in {gap.field for gap in unrepresentable(moved, "2.1")}
    assert _mappings(ml21_writer.write(_with_maps(*moved.maps))) == [None], "dropped, as declared"


def test_the_store_keeps_a_text_format_mapping_and_a_built_value_map() -> None:
    msg = maximal.message()
    (sm,) = msg.structure_maps
    built = msg.representation_maps[1]
    with sl.LocalRegistry(":memory:") as reg:
        reg.add(built)
        reg.add(sm)
        back_sm = reg.get(sl.StructureMap.parse_urn(str(sl.artefact_urn(sm))))
        back_built = reg.get(sl.RepresentationMap.parse_urn(str(sl.artefact_urn(built))))
    assert back_sm is not None
    assert back_built is not None
    assert back_sm.maps[2] == sm.maps[2]
    assert [m.representation_map.urn for m in back_sm.maps[:2] if m.representation_map is not None] == [
        sl.artefact_urn(m) for m in msg.representation_maps
    ]
    assert back_built == built
