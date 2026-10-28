"""The SDMX-ML readers resolve no external entity and refuse entity expansion (GH-241).

Both come from lxml's defaults, which every reader uses: lxml 5 and later
resolves internal entities only, and libxml2 caps how far entities may
amplify a document. Nothing in sdmxlib sets them, so these tests are what
holds them: a reader given its own `XMLParser`, or an lxml release that
changes a default, fails here.
"""

from pathlib import Path

import pytest

import sdmxlib as sl

pytestmark = pytest.mark.unit

_MESSAGE = {
    "2.1": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message",
    "3.0": "http://www.sdmx.org/resources/sdmxml/schemas/v3_0/message",
    "3.1": "http://www.sdmx.org/resources/sdmxml/schemas/v3_1/message",
}


def _document(version: str, doctype: str, value: str) -> bytes:
    return (
        f'<?xml version="1.0"?><!DOCTYPE m [{doctype}]>'
        f'<mes:Structure xmlns:mes="{_MESSAGE[version]}"><mes:Header><mes:ID>{value}</mes:ID>'
        '<mes:Test>false</mes:Test><mes:Prepared>2026-01-01T00:00:00</mes:Prepared><mes:Sender id="S"/>'
        "</mes:Header></mes:Structure>"
    ).encode()


@pytest.mark.parametrize("version", sorted(_MESSAGE))
def test_an_external_entity_is_not_read(tmp_path: Path, version: str) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("secret-marker")
    document = _document(version, f'<!ENTITY x SYSTEM "{secret.as_uri()}">', "&x;")

    with pytest.raises(sl.SdmxParseError, match="Entity 'x' not defined") as caught:
        sl.parse(document, version=version, strict=False)
    assert "secret-marker" not in str(caught.value)


@pytest.mark.parametrize("version", sorted(_MESSAGE))
def test_entity_expansion_is_refused(version: str) -> None:
    """Ten levels of tenfold entities, 10^10 characters if expanded."""
    levels = ['<!ENTITY e0 "aaaaaaaaaa">'] + [f'<!ENTITY e{n} "{f"&e{n - 1};" * 10}">' for n in range(1, 10)]
    document = _document(version, "".join(levels), "&e9;")

    with pytest.raises(sl.SdmxParseError, match="amplification"):
        sl.parse(document, version=version, strict=False)


def test_an_internal_entity_still_reads() -> None:
    """Discrimination: the expansion refusal is about size, so a small entity reads."""
    document = _document("3.0", '<!ENTITY x "IREF1">', "&x;")
    message = sl.parse(document, version="3.0", strict=False)
    assert isinstance(message, sl.StructureMessage)
    assert message.header is not None
    assert message.header.id == "IREF1"
