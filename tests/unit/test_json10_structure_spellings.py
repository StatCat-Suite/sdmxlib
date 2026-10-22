"""SDMX-JSON 1.0's spellings, read as 2.0's (GH-30).

FMR 12.4.2 serves a DSD in SDMX-JSON 1.0 with 1.0's vocabulary, which the
reader, knowing only 2.0's, refused whole:

- an attribute relationship ``{"none": {}}`` (2.1's ``<str:None/>``, a
  dataset-level attribute) or ``{"primaryMeasure": id}`` (observation-level);
- a representation's ``textFormat`` holding ``textType``, where 2.0 has
  ``format`` holding ``dataType``.

`tests/integration/test_fmr_oracle.py` reads the recorded documents; these pin
each spelling alone.
"""

import json
from pathlib import Path

import pytest

import sdmxlib as sl
from sdmxlib.model.datastructure import AttachmentLevel, DataStructure

pytestmark = pytest.mark.unit

_MINIMAL = Path(__file__).parents[1] / "fixtures" / "sdmx_json" / "minimal.json"
_RELATIONSHIP = '"attributeRelationship":{"observation":{}}'
_MEASURE = '"measures":[{"id":"OBS_VALUE",'


def _dsd(old: str, new: str) -> DataStructure:
    """The fixture's one DSD, with `old` (which it holds once) spelled `new`."""
    text = json.dumps(json.loads(_MINIMAL.read_text()), separators=(",", ":"))
    assert text.count(old) == 1
    message = sl.parse(text.replace(old, new).encode())
    assert isinstance(message, sl.StructureMessage)
    (dsd,) = message.data_structures
    return dsd


def _attachment(relationship: str) -> AttachmentLevel:
    return _dsd(_RELATIONSHIP, f'"attributeRelationship":{relationship}').attributes[0].attachment


@pytest.mark.parametrize(
    ("relationship", "level"),
    [
        ('{"none":{}}', AttachmentLevel.DATASET),
        ('{"dataflow":{}}', AttachmentLevel.DATASET),
        ('{"primaryMeasure":"OBS_VALUE"}', AttachmentLevel.OBSERVATION),
        ('{"observation":{}}', AttachmentLevel.OBSERVATION),
    ],
)
def test_1_0_relationships_read_as_their_2_0_counterparts(relationship: str, level: AttachmentLevel) -> None:
    assert _attachment(relationship) is level


def test_two_relationships_at_once_are_still_refused() -> None:
    with pytest.raises(sl.SdmxConformanceError, match="needs exactly one of"):
        _attachment('{"none":{},"observation":{}}')


def test_a_1_0_text_format_reads_as_a_2_0_format() -> None:
    representation = '"localRepresentation":{"textFormat":{"textType":"Double","decimals":2}},'
    facet = _dsd(_MEASURE, _MEASURE + representation).measures[0].representation
    assert isinstance(facet, sl.Facet)
    assert (facet.type, facet.decimals) == (sl.DataType.Double, 2)
