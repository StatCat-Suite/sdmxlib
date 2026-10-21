"""2.1's StructureSet and 3.x's top-level maps, converted both ways (#421).

2.1 nests every map in a StructureSet; 3.0 made each map maintainable and
removed the set. Read from 2.1, a map is named ``<map>@<set>``, the rule a
hierarchy follows in its HierarchicalCodelist, and takes its set's validity and
external reference. 3.x and SDMX-JSON leave the set out and write its maps.
"""

from datetime import UTC, datetime

import attrs
import pytest

from sdmxlib.formats import parse, serialize
from sdmxlib.formats.gaps import dropped, unrepresentable
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.mapping import RepresentationMap, RepresentationMapping, StructureSet
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import Ref, artefact_urn
from sdmxlib.model.validation import ValidationError
from tests.support.roundtrip import as_structure, structure_schema
from tests.support.xsd import assert_valid_sdmx_ml

pytestmark = pytest.mark.unit

_CL = Codelist(id="CL", maintainer="AG", name={"en": "cl"}, codes=[Code(id="A", name={"en": "a"})])


@attrs.frozen
class _Held:
    """What a 2.1 set carries for each map in it."""

    valid_from: datetime | None = None
    valid_to: datetime | None = None
    is_external_reference: bool = False
    service_url: str | None = None
    structure_url: str | None = None


#: A set carrying none of them, as a map built without them needs.
_NOTHING_HELD = _Held()


def _map(map_id: str, held: _Held = _NOTHING_HELD, name: str | None = None) -> RepresentationMap:
    return RepresentationMap(
        id=map_id,
        maintainer="AG",
        name={"en": name or map_id},
        sources=[Ref.to(_CL)],
        targets=[Ref.to(_CL)],
        mappings=[RepresentationMapping(sources=["A"], targets=["A"])],
        valid_from=held.valid_from,
        valid_to=held.valid_to,
        is_external_reference=held.is_external_reference,
        service_url=held.service_url,
        structure_url=held.structure_url,
    )


def _set(set_id: str, *maps: RepresentationMap, held: _Held = _NOTHING_HELD) -> StructureSet:
    return StructureSet(
        id=set_id,
        maintainer="AG",
        name={"en": set_id},
        maps=tuple(Ref[object].unresolved(m.urn) for m in maps),
        valid_from=held.valid_from,
        valid_to=held.valid_to,
        is_external_reference=held.is_external_reference,
        service_url=held.service_url,
        structure_url=held.structure_url,
    )


def _back(msg: StructureMessage, version: str = "2.1") -> StructureMessage:
    written = serialize(msg, version=version)
    if version != "json":
        assert_valid_sdmx_ml(written, structure_schema(version))
    return as_structure(parse(written))


def test_maps_of_one_inner_id_in_two_sets_stay_two() -> None:
    """Each written as ``CLM`` in its set, each read back as its own map."""
    first, second = _map("CLM@SS_A"), _map("CLM@SS_B", name="second")
    msg = StructureMessage(
        codelists=[_CL],
        representation_maps=[first, second],
        structure_sets=[_set("SS_A", first), _set("SS_B", second)],
    )
    written = serialize(msg, version="2.1")
    assert written.count(b'<str:CodelistMap id="CLM">') == 2
    back = _back(msg)
    assert (list(back.representation_maps), list(back.structure_sets)) == (
        list(msg.representation_maps),
        list(msg.structure_sets),
    )


def test_a_map_takes_its_sets_validity_and_external_reference() -> None:
    held = _Held(valid_from=datetime(2020, 1, 1, tzinfo=UTC), is_external_reference=True, service_url="https://x.org/r")
    member = _map("CLM@SS", held)
    structure_set = _set("SS", member, held=held)
    back = _back(StructureMessage(codelists=[_CL], representation_maps=[member], structure_sets=[structure_set]))
    assert list(back.representation_maps) == [member]


def test_a_standalone_map_comes_back_from_2_1_as_itself() -> None:
    """Its own validity and external reference survive, in a wrapper carrying them."""
    plain = _map("CLM")
    dated = _map("CLM_DATED", _Held(valid_to=datetime(2030, 1, 1, tzinfo=UTC), structure_url="https://x.org/s"))
    msg = StructureMessage(codelists=[_CL], representation_maps=[plain, dated])
    written = serialize(msg, version="2.1")
    # One wrapper per validity and external reference, so each is the map's own.
    assert written.count(b'<str:StructureSet id="SS_SYNTHETIC"') == 1
    assert written.count(b'<str:StructureSet id="SS_SYNTHETIC_2"') == 1
    back = _back(msg)
    assert (list(back.representation_maps), list(back.structure_sets)) == ([plain, dated], [])
    assert unrepresentable(dated, "2.1") == ()


@pytest.mark.parametrize(
    ("member", "named"),
    [
        pytest.param(_map("CLM"), "id", id="id"),
        pytest.param(_map("CLM@SS", _Held(valid_from=datetime(2020, 1, 1, tzinfo=UTC))), "valid_from", id="validity"),
        pytest.param(_map("CLM@SS", _Held(service_url="https://x.org/r")), "service_url", id="external-reference"),
    ],
)
def test_2_1_refuses_a_map_its_set_would_give_back_changed(member: RepresentationMap, named: str) -> None:
    agrees = _map("CLM@SS")
    msg = StructureMessage(codelists=[_CL], representation_maps=[agrees], structure_sets=[_set("SS", agrees)])
    assert serialize(msg, version="2.1")
    msg = StructureMessage(codelists=[_CL], representation_maps=[member], structure_sets=[_set("SS", member)])
    with pytest.raises(ValidationError, match=f"container's {named}, not its own"):
        serialize(msg, version="2.1")


@pytest.mark.parametrize("version", ["3.0", "3.1", "json"])
def test_3x_writes_the_maps_and_leaves_the_set_out(version: str) -> None:
    member = _map("CLM@SS")
    structure_set = _set("SS", member)
    msg = StructureMessage(codelists=[_CL], representation_maps=[member], structure_sets=[structure_set])
    (gap,) = [gap for gap in unrepresentable(structure_set, version) if gap.field == "sdmx_class"]
    assert "its maps are written without it" in gap.reason
    assert [drop.urn for drop in dropped(msg, version)] == [str(artefact_urn(structure_set))]
    back = _back(msg, version)
    assert (list(back.representation_maps), list(back.structure_sets)) == ([member], [])
