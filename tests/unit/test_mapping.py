"""Tests for sdmxlib.model.mapping — 3.0-native structural mapping types."""

from collections.abc import Callable
from typing import cast

import pytest

from sdmxlib.model.mapping import (
    CategorySchemeMap,
    ComponentMap,
    ConceptSchemeMap,
    ItemMap,
    RepresentationMap,
    RepresentationMapping,
    SourceValue,
    StructureMap,
)

pytestmark = pytest.mark.unit


# ── RepresentationMap ─────────────────────────────────────────────────────────


def test_representation_map_defaults():
    m = RepresentationMap(name={"en": "RM_TEST"}, id="RM_TEST", maintainer="SDMX")
    assert m.sources == ()
    assert m.targets == ()
    assert m.mappings == ()


def test_representation_map_translate_hit():
    m = RepresentationMap(
        name={"en": "RM_TEST"},
        id="RM_TEST",
        maintainer="SDMX",
        mappings=(
            RepresentationMapping(sources=("A",), targets=("ANNUAL",)),
            RepresentationMapping(sources=("Q",), targets=("QUARTERLY",)),
        ),
    )
    assert m.translate("A") == "ANNUAL"
    assert m.translate("Q") == "QUARTERLY"


def test_representation_map_translate_miss():
    m = RepresentationMap(
        name={"en": "RM_TEST"},
        id="RM_TEST",
        maintainer="SDMX",
        mappings=(RepresentationMapping(sources=("A",), targets=("ANNUAL",)),),
    )
    assert m.translate("X") is None


def test_representation_map_translate_reads_only_a_literal_one_to_one():
    """An N:M or pattern mapping has no single answer, so `translate` skips it."""
    m = RepresentationMap(
        name={"en": "RM_TEST"},
        id="RM_TEST",
        maintainer="SDMX",
        mappings=(
            RepresentationMapping(sources=("A", "B"), targets=("AB",)),
            RepresentationMapping(sources=(SourceValue("A", is_regex=True),), targets=("RX",)),
            RepresentationMapping(sources=("A",), targets=("X", "Y")),
            RepresentationMapping(sources=("A",)),
        ),
    )
    assert m.translate("A") is None


def test_a_source_value_is_literal_without_flags():
    assert SourceValue("A").is_literal
    assert not SourceValue("A", is_regex=True).is_literal
    assert not SourceValue("A", start_index=1, end_index=2).is_literal


def test_a_plain_string_source_is_a_literal_source_value():
    assert RepresentationMapping(sources=("A",)).sources == (SourceValue("A"),)


def _untyped(text: str) -> tuple[str, ...]:
    """A bare string, as an unchecked caller would pass it where a tuple belongs."""
    unchecked: object = text
    return cast("tuple[str, ...]", unchecked)


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(lambda: ComponentMap(sources=_untyped("FREQ"), targets=("F",)), id="component-sources"),
        pytest.param(lambda: ComponentMap(sources=("FREQ",), targets=_untyped("F")), id="component-targets"),
        pytest.param(lambda: RepresentationMapping(sources=_untyped("AB")), id="mapping-sources"),
        pytest.param(lambda: RepresentationMapping(sources=("A",), targets=_untyped("B")), id="mapping-targets"),
    ],
)
def test_a_bare_string_is_refused_where_a_tuple_is_expected(build: Callable[[], object]):
    """A `str` iterates as its characters — `sources="AB"` would read as two values (#261).

    pyrefly's `string-as-iterable` refuses the call statically; this is the
    runtime half, for a caller no checker reads.
    """
    with pytest.raises(TypeError, match="got the string"):
        build()


def test_representation_map_translate_empty():
    assert RepresentationMap(name={"en": "RM_TEST"}, id="RM_TEST", maintainer="SDMX").translate("A") is None


def test_representation_map_name_converter():
    m = RepresentationMap(id="RM_TEST", maintainer="SDMX", name={"en": "Map"})
    assert m.name["en"] == "Map"


def test_representation_map_urn_classvar():
    assert RepresentationMap.sdmx_class == "RepresentationMap"
    assert RepresentationMap.msg_field == "representation_maps"


def test_representation_map_urn_factory():
    urn = RepresentationMap.urn_for(agency="SDMX", id="RM_TEST", version="1.0")
    assert "RepresentationMap" in str(urn)
    assert "RM_TEST" in str(urn)


# ── ConceptSchemeMap / CategorySchemeMap ──────────────────────────────────────


def test_concept_scheme_map_holds_item_maps():
    csm = ConceptSchemeMap(
        name={"en": "CS2CS"},
        id="CS2CS",
        maintainer="SDMX",
        maps=(ItemMap(source_id="FREQ", target_id="FREQUENCY"),),
    )
    assert csm.maps[0].source_id == "FREQ"
    assert csm.maps[0].target_id == "FREQUENCY"


def test_category_scheme_map_holds_item_maps():
    csm = CategorySchemeMap(
        name={"en": "CAT2CAT"},
        id="CAT2CAT",
        maintainer="SDMX",
        maps=(ItemMap(source_id="X", target_id="Y"),),
    )
    assert csm.maps == (ItemMap(source_id="X", target_id="Y"),)


def test_concept_scheme_map_is_maintainable():
    assert ConceptSchemeMap.sdmx_class == "ConceptSchemeMap"
    assert ConceptSchemeMap.msg_field == "concept_scheme_maps"


def test_category_scheme_map_is_maintainable():
    assert CategorySchemeMap.sdmx_class == "CategorySchemeMap"
    assert CategorySchemeMap.msg_field == "category_scheme_maps"


# ── StructureMap ──────────────────────────────────────────────────────────────


def test_structure_map_holds_component_maps():
    sm = StructureMap(
        name={"en": "DSD2DSD"},
        id="DSD2DSD",
        maintainer="SDMX",
        maps=(ComponentMap(sources=("FREQ",), targets=("FREQUENCY",)),),
    )
    assert len(sm.maps) == 1
    assert sm.maps[0].sources == ("FREQ",)


def test_structure_map_is_maintainable():
    assert StructureMap.sdmx_class == "StructureMap"
    assert StructureMap.msg_field == "structure_maps"


def test_structure_map_empty_component_list():
    """StructureMap also covers the 2.1 DataflowMap case, which has no component maps."""
    sm = StructureMap(name={"en": "DF2DF"}, id="DF2DF", maintainer="SDMX")
    assert sm.maps == ()
