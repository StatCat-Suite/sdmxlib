"""Tests against canonical SDMX-TWG metadata-message samples.

The fixture is the constructed-sample.json from
https://github.com/sdmx-twg/sdmx-json (metadata-message/samples). It
exercises shapes our hand-written tests don't necessarily cover:

- ``name`` as a bare string (single-language shorthand) instead of a dict
- A populated three-set message with diverging structures
- Nested attributes (CONTACT → ADDRESS → POSTAL_CODE) where the leaf is
  an integer scalar
- A real Metadataflow URN as the structure ref
"""

from pathlib import Path

import pytest

import sdmxlib as sl

pytestmark = pytest.mark.unit

_SAMPLE = Path(__file__).parent.parent / "fixtures" / "sdmx_metadata" / "sdmx_twg_constructed_sample.json"


def test_parses_as_metadata_message() -> None:
    msg = sl.parse(_SAMPLE.read_bytes())
    assert isinstance(msg, sl.MetadataMessage)
    assert len(msg.metadata_sets) == 3


def test_contact_set_targets_a_category_via_metadataflow() -> None:
    msg = sl.parse(_SAMPLE.read_bytes())
    assert isinstance(msg, sl.MetadataMessage)
    ms = msg.metadata_sets["METADATASET1"]
    assert ms.structure.urn.artefact_class == "Metadataflow"
    assert ms.structure.urn.id == "ESMS"
    assert len(ms.targets) == 1
    assert ms.targets[0].urn.artefact_class == "Category"


def test_contact_set_recursive_attribute_tree() -> None:
    msg = sl.parse(_SAMPLE.read_bytes())
    assert isinstance(msg, sl.MetadataMessage)
    contact = msg.metadata_sets["METADATASET1"].attributes["CONTACT"]
    assert {c.id for c in contact.children} == {
        "ORGANISATION",
        "ORGANISATION_UNIT",
        "ADDRESS",
        "EMAIL",
    }
    address = contact.children["ADDRESS"]
    assert {c.id for c in address.children} == {"STREET", "CITY", "POSTAL_CODE", "COUNTRY"}
    # Integer scalars round-trip as int (the JSON spec allows numeric values)
    assert address.children["POSTAL_CODE"].value == 2721
    assert isinstance(address.children["POSTAL_CODE"].value, int)


def test_meta_update_set_carries_string_scalars() -> None:
    msg = sl.parse(_SAMPLE.read_bytes())
    assert isinstance(msg, sl.MetadataMessage)
    update = msg.metadata_sets["META_UPDATE"]
    assert {a.id for a in update.attributes} == {"CERTIFIED", "POSTED", "UPDATED"}
    for attr in update.attributes:
        assert isinstance(attr.value, str)


def test_stat_pres_set_carries_xhtml_via_localised_text() -> None:
    """SDMX-JSON encodes XHTML as localisedText whose value happens to be HTML."""
    msg = sl.parse(_SAMPLE.read_bytes())
    assert isinstance(msg, sl.MetadataMessage)
    desc = msg.metadata_sets["STAT_PRES"].attributes["DATA_DESCR"]
    assert isinstance(desc.value, sl.InternationalString)
    body = desc.value["en"].text
    # The sample wraps content in an XHTML <div>; verify the literal markup
    # round-trips as text content (per the spec's localisedText shape).
    assert "<div" in body
    assert "xhtml" in body


def test_round_trip_through_writer_preserves_structure() -> None:
    """Parse, re-emit, parse again — the second pass should match the first."""
    original = sl.parse(_SAMPLE.read_bytes())
    assert isinstance(original, sl.MetadataMessage)
    re_emitted = sl.to_json(original)
    second = sl.parse(re_emitted)
    assert isinstance(second, sl.MetadataMessage)
    assert len(second.metadata_sets) == len(original.metadata_sets)
    for a, b in zip(original.metadata_sets, second.metadata_sets, strict=True):
        assert a.id == b.id
        assert {t.urn for t in a.targets} == {t.urn for t in b.targets}
        assert [attr.id for attr in a.attributes] == [attr.id for attr in b.attributes]
