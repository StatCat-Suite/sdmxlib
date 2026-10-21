"""A single 3.x measure is written to SDMX-ML 2.1 under the pinned id (#421).

2.1's `PrimaryMeasure` id is fixed to ``OBS_VALUE``; 3.x lets a measure be
named for what it measures, and IMF serves six DSDs that do. The 2.1 writer
renames the one measure rather than refusing the DSD, keeping its concept and
representation, and `formats.gaps` declares the id as what is lost. Several
measures still have no 2.1 form and are refused.
"""

import attrs
import pytest
from lxml import etree

from sdmxlib.formats import parse, serialize
from sdmxlib.formats.gaps import unrepresentable
from sdmxlib.model.collections import ArtefactList, ItemList
from sdmxlib.model.datastructure import DataStructure
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.validation import ValidationError
from tests.support.roundtrip import as_structure, load_xml
from tests.support.xsd import assert_valid_sdmx_ml

pytestmark = pytest.mark.unit


def _with_measures(*ids: str) -> tuple[StructureMessage, DataStructure]:
    msg = load_xml("3.0", "minimal")
    dsd = msg.data_structures[0]
    (measure,) = dsd.measures
    renamed = attrs.evolve(dsd, measures=ItemList([attrs.evolve(measure, id=i) for i in ids]))
    return attrs.evolve(msg, data_structures=ArtefactList([renamed])), renamed


def test_a_single_measure_is_written_as_obs_value(sdmx_ml21_schema: etree.XMLSchema) -> None:
    msg, dsd = _with_measures("VALUE")
    written = serialize(msg, version="2.1")
    assert_valid_sdmx_ml(written, sdmx_ml21_schema)
    (back,) = as_structure(parse(written)).data_structures[0].measures
    (measure,) = dsd.measures
    assert back.id == "OBS_VALUE"
    assert back.concept == measure.concept
    assert back.representation == measure.representation


def _measure_gaps(dsd: DataStructure) -> list[tuple[str, str]]:
    """The DSD's 2.1 gaps on its measure's id. Its attribute, multilingual by 3.0's default, has its own (#503)."""
    return [(gap.target, gap.field) for gap in unrepresentable(dsd, "2.1") if gap.field.startswith("measures[].id")]


def test_the_rename_is_declared() -> None:
    _, dsd = _with_measures("VALUE")
    assert _measure_gaps(dsd) == [("2.1", "measures[].id")]


def test_obs_value_is_no_gap() -> None:
    _, dsd = _with_measures("OBS_VALUE")
    assert _measure_gaps(dsd) == []


def test_several_measures_are_still_refused() -> None:
    msg, _ = _with_measures("VALUE", "LOWER")
    with pytest.raises(ValidationError, match="exactly one PrimaryMeasure"):
        serialize(msg, version="2.1")
