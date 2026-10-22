"""`parse` raises on an SDMX error message instead of reading it as empty (GH-31).

The FMR recordings are what a real registry sent for a query with no answer:
SDMX-ML 2.1's ``<message:Error>``, and an ``{"errors": [...]}`` document for
both SDMX-JSON versions. Each used to come back as an empty `StructureMessage`,
nothing refused and nothing lost.
"""

from pathlib import Path

import pytest

import sdmxlib as sl

pytestmark = pytest.mark.unit

_RECORDED = Path(__file__).parents[1] / "integration" / "fmr" / "12.4.2" / "2.1-shape" / "back"


@pytest.mark.parametrize(
    ("name", "errors"),
    [
        ("AG_MD-ml21.xml", (("100", "No Results Found"),)),
        ("AG_MD-json20.json", (("404", "No Results Found"),)),
        ("AG_MD-json10.json", (("404", "No Results Found"),)),
    ],
)
def test_a_recorded_error_message_raises_with_its_codes(name: str, errors: tuple[tuple[str, str], ...]) -> None:
    with pytest.raises(sl.SdmxErrorMessage) as caught:
        sl.parse((_RECORDED / name).read_bytes())
    assert caught.value.errors == errors
    assert "No Results Found" in str(caught.value)


def test_an_sdmx_ml_30_error_reports_every_error_and_the_first_text_of_each() -> None:
    raw = b"""<?xml version="1.0" encoding="UTF-8"?>
<mes:Error xmlns:mes="http://www.sdmx.org/resources/sdmxml/schemas/v3_0/message"
           xmlns:com="http://www.sdmx.org/resources/sdmxml/schemas/v3_0/common">
  <!-- a comment is not an error -->
  <mes:ErrorMessage code="510">
    <com:Text xml:lang="en">Too many results</com:Text>
    <com:Text xml:lang="fr">Trop</com:Text>
  </mes:ErrorMessage>
  <mes:ErrorMessage code="500"/>
</mes:Error>"""
    with pytest.raises(sl.SdmxErrorMessage) as caught:
        sl.parse(raw)
    assert caught.value.errors == (("510", "Too many results"), ("500", ""))


def test_a_json_error_takes_its_title_then_its_detail() -> None:
    raw = b'{"errors": [{"code": 404.0, "title": "Not found", "detail": "longer"}, {"code": 500, "detail": "Oops"}]}'
    with pytest.raises(sl.SdmxErrorMessage) as caught:
        sl.parse(raw)
    assert caught.value.errors == (("404", "Not found"), ("500", "Oops"))


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(b'{"errors": [{"code": 500, "title": "partial"}], "data": {}}', id="errors beside data"),
        pytest.param(b'{"errors": [], "meta": {}}', id="no errors"),
    ],
)
def test_a_json_message_that_answers_is_read_whatever_errors_it_carries(raw: bytes) -> None:
    assert isinstance(sl.parse(raw), sl.StructureMessage)


def test_it_is_caught_as_a_parse_error() -> None:
    with pytest.raises(sl.SdmxParseError):
        sl.parse((_RECORDED / "AG_MD-ml21.xml").read_bytes())
