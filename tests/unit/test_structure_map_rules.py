"""The IM's structure map rules (GH-26): one refused, three warned.

SDMX 3.1 Section 2, chapters 9 and 10. A date map names a frequency dimension
or a target frequency, one of them (§9.1.4.2): the 3.0 and 3.1 XSDs make the
two a required choice and SDMX-JSON a `oneOf`, so breaking it is refused. The
other three no wire schema enforces, so breaking one builds the map with an
`ImRuleWarning` (#481): a structure map maps two structures or two structure
usages (§9.1.2.1), a component map of many sources or targets names a
representation map (§9.1.2.1), and a mapping's values match its map's sources
and targets (§10.1.2.1). The census cites each test here by name.
"""

import warnings

import pytest

import sdmxlib as sl
from sdmxlib.model.errors import ImRuleWarning, ValidationError
from sdmxlib.model.mapping import (
    ComponentMap,
    DatePatternMap,
    EpochMap,
    EpochPeriod,
    MapDataType,
    RepresentationMap,
    RepresentationMapping,
    StructureMap,
)
from sdmxlib.model.ref import Ref

pytestmark = pytest.mark.unit

_DSD = sl.DataStructure.urn_for(agency="AG", id="DSD", version="1.0")
_OTHER_DSD = sl.DataStructure.urn_for(agency="AG", id="DSD_2", version="1.0")
_FLOW = sl.Dataflow.urn_for(agency="AG", id="DF", version="1.0")
_OTHER_FLOW = sl.Dataflow.urn_for(agency="AG", id="DF_2", version="1.0")
_RM = RepresentationMap(
    id="RM",
    maintainer="AG",
    name={"en": "rm"},
    sources=[MapDataType("String"), MapDataType("String")],
    targets=[MapDataType("String")],
    mappings=[RepresentationMapping(sources=["A", "B"], targets=["AB"])],
)


def _map(**fields: object) -> StructureMap:
    source = fields.pop("source", _DSD)
    target = fields.pop("target", _OTHER_DSD)
    maps = fields.pop("maps", ())
    epoch_maps = fields.pop("epoch_maps", ())
    date_pattern_maps = fields.pop("date_pattern_maps", ())
    assert not fields, fields
    return StructureMap(
        id="SM",
        maintainer="AG",
        name={"en": "sm"},
        source=Ref[object].unresolved(source) if isinstance(source, sl.SdmxUrn) else None,
        target=Ref[object].unresolved(target) if isinstance(target, sl.SdmxUrn) else None,
        maps=maps if isinstance(maps, tuple) else (),
        epoch_maps=epoch_maps if isinstance(epoch_maps, tuple) else (),
        date_pattern_maps=date_pattern_maps if isinstance(date_pattern_maps, tuple) else (),
    )


def _warnings_of(build: object) -> list[str]:
    """The messages of the one `ImRuleWarning` that building warns with."""
    assert callable(build)
    with pytest.warns(ImRuleWarning) as caught:
        build()
    (warning,) = caught
    assert isinstance(warning.message, ImRuleWarning)
    return [issue.message for issue in warning.message.issues]


def _builds_quietly(build: object) -> None:
    assert callable(build)
    with warnings.catch_warnings():
        warnings.simplefilter("error", ImRuleWarning)
        build()


# ── §9.1.4.2: a date map's frequency, refused ────────────────────────────────


def _epoch(**frequency: object) -> EpochMap:
    dimension = frequency.get("frequency_dimension")
    target = frequency.get("target_frequency_id")
    mapped = frequency.get("mapped_frequencies", ())
    return EpochMap(
        id="EPOCH",
        component_maps=[("TIME_PERIOD", "TIME_PERIOD")],
        base_period="1970-01-01",
        epoch_period=EpochPeriod.DAY,
        frequency_dimension=dimension if isinstance(dimension, str) else None,
        target_frequency_id=target if isinstance(target, str) else None,
        mapped_frequencies=mapped if isinstance(mapped, tuple) else (),
    )


@pytest.mark.parametrize(
    ("frequency", "message"),
    [
        ({"frequency_dimension": "FREQ", "target_frequency_id": "M"}, "names a frequency dimension and a target"),
        ({}, "names neither a frequency dimension nor a target frequency"),
        ({"target_frequency_id": "M", "mapped_frequencies": ("FFM",)}, "maps frequencies without a frequency dim"),
    ],
    ids=["both", "neither", "mapped-without-dimension"],
)
def test_a_date_map_names_one_frequency_source(frequency: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        _map(epoch_maps=(_epoch(**frequency),))


def test_a_date_pattern_map_is_checked_alike() -> None:
    pattern = DatePatternMap(
        id="PATTERN",
        component_maps=[("TIME_PERIOD", "TIME_PERIOD")],
        source_pattern="yyyyMM",
        locale="en",
        frequency_dimension="FREQ",
        target_frequency_id="M",
    )
    with pytest.raises(ValidationError, match=r"date_pattern_maps\[PATTERN\]"):
        _map(date_pattern_maps=(pattern,))


@pytest.mark.parametrize(
    "frequency",
    [{"frequency_dimension": "FREQ", "mapped_frequencies": ("FFM",)}, {"target_frequency_id": "M"}],
    ids=["by-dimension", "by-target"],
)
def test_either_frequency_source_builds(frequency: dict[str, object]) -> None:
    _builds_quietly(lambda: _map(epoch_maps=(_epoch(**frequency),)))


# ── §9.1.2.1: source and target kinds, warned ────────────────────────────────


def test_a_map_of_a_structure_to_a_structure_usage_warns() -> None:
    (message,) = _warnings_of(lambda: _map(source=_DSD, target=_FLOW))
    assert message == "maps a structure to a structure usage; the IM maps two structures or two structure usages"


@pytest.mark.parametrize(("source", "target"), [(_DSD, _OTHER_DSD), (_FLOW, _OTHER_FLOW)], ids=["dsds", "flows"])
def test_a_map_of_two_of_a_kind_builds_quietly(source: sl.SdmxUrn[object], target: sl.SdmxUrn[object]) -> None:
    _builds_quietly(lambda: _map(source=source, target=target))


# ── §9.1.2.1: a many-valued component map's representation map, warned ──────


def test_a_many_to_one_component_map_without_a_representation_map_warns() -> None:
    component_map = ComponentMap(sources=["A", "B"], targets=["AB"])
    (message,) = _warnings_of(lambda: _map(maps=(component_map,)))
    assert message.startswith("maps 2 sources to 1 targets with no representation map")


@pytest.mark.parametrize(
    "component_map",
    [
        ComponentMap(sources=["A"], targets=["A"]),
        ComponentMap(sources=["A", "B"], targets=["AB"], representation_map=Ref.to(_RM)),
    ],
    ids=["one-to-one", "with-representation-map"],
)
def test_a_component_map_that_says_how_its_values_map_builds_quietly(component_map: ComponentMap) -> None:
    _builds_quietly(lambda: _map(maps=(component_map,)))


# ── §10.1.2.1: a mapping's arity, warned ─────────────────────────────────────


def _representation_map(*mappings: RepresentationMapping) -> RepresentationMap:
    return RepresentationMap(
        id="RM",
        maintainer="AG",
        name={"en": "rm"},
        sources=[MapDataType("String"), MapDataType("String")],
        targets=[MapDataType("String")],
        mappings=list(mappings),
    )


@pytest.mark.parametrize(
    "mapping",
    [
        RepresentationMapping(sources=["A"], targets=["AB"]),
        RepresentationMapping(sources=["A", "B"], targets=["X", "Y"]),
    ],
    ids=["too-few-sources", "too-many-targets"],
)
def test_a_mapping_of_the_wrong_arity_warns(mapping: RepresentationMapping) -> None:
    (message,) = _warnings_of(lambda: _representation_map(mapping))
    assert message.endswith("the map has 2 sources and 1 targets; the values match them in order")


@pytest.mark.parametrize(
    "mapping",
    [RepresentationMapping(sources=["A", "B"], targets=["AB"]), RepresentationMapping(sources=["A", "B"])],
    ids=["one-per-side", "no-target"],
)
def test_a_mapping_of_the_maps_arity_builds_quietly(mapping: RepresentationMapping) -> None:
    """`TargetValue` is optional in `ValueMappingType`, so a mapping to nothing is no arity break."""
    _builds_quietly(lambda: _representation_map(mapping))


@pytest.mark.parametrize("version", ["3.0", "3.1", "json"])
def test_a_warned_map_still_round_trips(version: str) -> None:
    """Warned, not refused: the 3.x formats carry a mixed pair and a many-valued map without one."""
    with pytest.warns(ImRuleWarning):
        structure_map = _map(source=_DSD, target=_FLOW, maps=(ComponentMap(sources=["A", "B"], targets=["AB"]),))
    with pytest.warns(ImRuleWarning):
        back = sl.parse(sl.serialize(sl.StructureMessage(structure_maps=[structure_map]), version=version))
    assert isinstance(back, sl.StructureMessage)
    assert back.structure_maps[0] == structure_map
