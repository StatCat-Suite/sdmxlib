"""An attribute's measure relationship names a measure, or is unset (#497).

The IM relates an attribute "to one or more Measures", and the lack of a
relationship "defaults to a relationship to all Measures" (SDMX 3.1, Section 2,
lines 1402-1408). An empty `related_measures` was stored as given and written
as an empty `MeasureRelationship` — schema-invalid in SDMX-ML 3.0, 3.1 and
SDMX-JSON — with nothing refusing it first. It is refused as it is set now;
`None` keeps meaning every measure.
"""

import json
import re

import attrs
import pytest

import sdmxlib as sl
from sdmxlib.formats import parse, serialize
from sdmxlib.model.errors import ValidationError
from tests.support.factories import concept_ref
from tests.support.roundtrip import as_structure

pytestmark = pytest.mark.unit

_EMPTY = r"Attribute\.related_measures — related_measures is empty"


def _dsd(related_measures: tuple[str, ...] | None = ("OBS_VALUE",)) -> sl.DataStructure:
    return sl.DataStructure(
        id="DSD",
        maintainer="AG",
        name={"en": "d"},
        dimensions=[sl.Dimension(id="FREQ", concept=concept_ref("FREQ"))],
        attributes=[
            sl.Attribute(
                id="A",
                concept=concept_ref("A"),
                attachment=sl.AttachmentLevel.OBSERVATION,
                related_measures=related_measures,
            )
        ],
        measures=[sl.Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )


def test_an_empty_relationship_is_refused_as_it_is_set():
    with pytest.raises(ValidationError, match=_EMPTY):
        sl.Attribute(attachment=sl.AttachmentLevel.DATASET, id="A", related_measures=())


def test_evolve_is_refused_as_construction_is():
    attribute = sl.Attribute(attachment=sl.AttachmentLevel.DATASET, id="A", related_measures=("OBS_VALUE",))
    with pytest.raises(ValidationError, match=_EMPTY):
        attrs.evolve(attribute, related_measures=())


@pytest.mark.parametrize("related", [None, ("OBS_VALUE",)], ids=["unset: every measure", "one measure"])
def test_what_the_im_can_say_still_builds(related: tuple[str, ...] | None):
    assert _dsd(related).attributes["A"].related_measures == related


def test_sdmx_json_reads_an_empty_relationship_as_refused():
    doc = json.loads(serialize(sl.StructureMessage(data_structures=[_dsd()]), version="json"))
    components = doc["data"]["dataStructures"][0]["dataStructureComponents"]
    components["attributeList"]["attributes"][0]["measureRelationship"] = []
    msg = as_structure(parse(json.dumps(doc).encode(), strict=False))
    assert msg.data_structures.ids() == []
    (refusal,) = msg.refused
    assert "related_measures is empty" in refusal.reason


def test_sdmx_ml_30_reads_an_empty_relationship_as_refused():
    """A `<Measure>` with no text reads as no measure, so the relationship is empty."""
    written = serialize(sl.StructureMessage(data_structures=[_dsd()]), version="3.0")
    emptied, n = re.subn(rb"(<str:Measure>)OBS_VALUE(</str:Measure>)", rb"\1\2", written)
    assert n == 1, "the case lost its subject"
    msg = as_structure(parse(emptied, strict=False))
    assert msg.data_structures.ids() == []
    (refusal,) = msg.refused
    assert "related_measures is empty" in refusal.reason
