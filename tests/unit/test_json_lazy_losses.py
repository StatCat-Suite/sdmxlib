"""An SDMX-JSON read finds its losses when they are asked for (#461).

Finding them walks every key of every artefact beside the struct that
decoded it, which was 40% of the read (22 of 54 ms on BIS's codelists). So
the walk runs on the first read of `StructureMessage.lost`, and not at all
for a caller who never asks. The guards count walks rather than time them.
"""

import json

import attrs
import pytest

from sdmxlib import formats
from sdmxlib.formats.sdmx_json import reader
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.validation import LossyReadError

pytestmark = pytest.mark.unit


def _with_an_undeclared_key() -> bytes:
    """A JSON codelist whose one code carries a key no struct declares."""
    codelist = Codelist(
        id="CL", maintainer="AG", version="1.0", name={"en": "Codes"}, codes=[Code(id="A", name={"en": "A"})]
    )
    document = json.loads(formats.serialize(StructureMessage(codelists=[codelist]), version="json"))
    document["data"]["codelists"][0]["codes"][0]["colour"] = "red"
    return json.dumps(document).encode()


def _parse(document: bytes) -> StructureMessage:
    message = formats.parse(document)
    assert isinstance(message, StructureMessage)
    return message


@pytest.fixture
def walks(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The URN of each artefact the loss walk visits, as it visits it."""
    visited: list[str] = []
    original = reader.undeclared

    def counting(urn: str, raw: object, dto: object) -> object:
        visited.append(urn)
        return original(urn, raw, dto)

    monkeypatch.setattr(reader, "undeclared", counting)
    return visited


def test_a_read_does_not_walk_until_lost_is_read(walks: list[str]) -> None:
    message = _parse(_with_an_undeclared_key())
    assert walks == [], "the read walked the document for losses nobody asked for"
    assert [gap.construct for gap in message.lost] == ["CodeJson.colour"]
    assert len(walks) == 1, "reading `lost` must walk once, which is also what shows the count above can fail"


def test_lost_is_worked_out_once(walks: list[str]) -> None:
    message = _parse(_with_an_undeclared_key())
    first = message.lost
    assert message.lost is first
    assert len(walks) == 1


def test_lossless_still_refuses_a_lossy_read() -> None:
    with pytest.raises(LossyReadError, match="colour"):
        formats.parse(_with_an_undeclared_key(), lossless=True)


def test_a_copy_keeps_the_losses_still_to_find() -> None:
    """`attrs.evolve` carries them, which the refusal pass relies on."""
    copy = attrs.evolve(_parse(_with_an_undeclared_key()), refused=())
    assert [gap.construct for gap in copy.lost] == ["CodeJson.colour"]


def test_equality_does_not_depend_on_whether_lost_was_read() -> None:
    asked, not_asked = _parse(_with_an_undeclared_key()), _parse(_with_an_undeclared_key())
    _ = asked.lost
    assert asked == not_asked


def _clean() -> bytes:
    """A JSON codelist with nothing a struct skips, so `lossless=True` reads it."""
    codelist = Codelist(
        id="CL", maintainer="AG", version="1.0", name={"en": "Codes"}, codes=[Code(id="A", name={"en": "A"})]
    )
    return formats.serialize(StructureMessage(codelists=[codelist]), version="json")


def test_reading_lost_releases_the_document() -> None:
    """Until `lost` is read the message holds what finds the losses, and the document with it (#473)."""
    message = _parse(_with_an_undeclared_key())
    assert callable(message._lost), "the premise: losses are still to find"
    _ = message.lost
    assert not callable(message._lost), "reading lost must let the document go"


def test_a_lossless_parse_keeps_nothing_to_release() -> None:
    """`parse(..., lossless=True)` finds the losses during the read, so nothing is held after it (#473)."""
    message = formats.parse(_clean(), lossless=True)
    assert isinstance(message, StructureMessage)
    assert not callable(message._lost)
