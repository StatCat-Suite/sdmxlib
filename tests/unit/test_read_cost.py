"""What a read costs per item, guarded by counts rather than clocks (%22).

Milestone 20 made reads 30-130% slower, measured against `main` before it
(#461, #463). Each fix here removes work done once per item, so each guard
asserts that the work no longer grows with the item count: the same calls
for a 1-code codelist as for a 200-code one. A clock would pass on a fast
machine and fail on a loaded one; a count does neither.
"""

from collections.abc import Callable, Iterable
from typing import override

import pytest
from lxml import etree

from sdmxlib import formats
from sdmxlib.formats.sdmx_ml21 import namespaces as ml21
from sdmxlib.formats.sdmx_ml21 import reader as ml21_reader
from sdmxlib.formats.sdmx_ml30 import namespaces as ml30
from sdmxlib.formats.sdmx_ml30 import reader as ml30_reader
from sdmxlib.model import _rules, validation
from sdmxlib.model.annotations import Annotations
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.message import StructureMessage

pytestmark = pytest.mark.unit


def _codelist_document(codes: int, version: str) -> bytes:
    codelist = Codelist(
        id="CL",
        maintainer="AG",
        version="1.0",
        name={"en": "Codes"},
        codes=[Code(id=f"C{i}", name={"en": f"Code {i}"}) for i in range(codes)],
    )
    return formats.serialize(StructureMessage(codelists=[codelist]), version=version)


def _calls_while_parsing(monkeypatch: pytest.MonkeyPatch, document: bytes, target: object, name: str) -> int:
    """How many times `target.name` is called while `document` is parsed."""
    original: Callable[..., object] = getattr(target, name)
    calls = 0

    def counting(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(target, name, counting)
        formats.parse(document)
    return calls


@pytest.mark.parametrize("version", ["2.1", "3.0", "3.1"])
def test_reading_a_code_builds_no_qname(monkeypatch: pytest.MonkeyPatch, version: str) -> None:
    """A code's kind is its tag, compared as a string; no `etree.QName` per code (#463).

    Building one per code to read its local name cost 2.9 ms of the 95 ms BIS
    codelist read.
    """
    one = _calls_while_parsing(monkeypatch, _codelist_document(1, version), etree, "QName")
    many = _calls_while_parsing(monkeypatch, _codelist_document(200, version), etree, "QName")
    assert many == one, f"etree.QName calls grow with the code count: {one} for 1 code, {many} for 200"


def test_the_qname_guard_can_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    """The count above is capable of growing: a per-code QName is seen."""
    helpers = ml21_reader._helpers
    original = helpers._read_code

    def with_qname(code_el: etree._Element) -> Code:
        _ = etree.QName(code_el).localname
        return original(code_el)

    monkeypatch.setattr(helpers, "_read_code", with_qname)
    one = _calls_while_parsing(monkeypatch, _codelist_document(1, "2.1"), etree, "QName")
    many = _calls_while_parsing(monkeypatch, _codelist_document(200, "2.1"), etree, "QName")
    assert many == one + 199


def _code_with_a_link(com: str, str_: str) -> etree._Element:
    """A `<Code>` holding a `<com:Link>` child in *com*'s namespace."""
    code = etree.Element(f"{{{str_}}}Code", id="A")
    etree.SubElement(code, f"{{{com}}}Link", rel="self", url="https://example.org/A")
    return code


def test_the_2_1_reader_never_looks_for_links() -> None:
    """SDMX-ML 2.1 has no `<com:Link>`, so its reader does not search for one (#463).

    The search ran once per item and cost 7.8 ms of the 95 ms BIS codelist
    read, always finding nothing. The element here does hold a link, so a
    reader that looked would return it.
    """
    assert ml21_reader._helpers.read_links(_code_with_a_link(ml21.COM, ml21.STR)) == ()


def test_a_3x_reader_does_find_links() -> None:
    """The discrimination check: the same shape in 3.0's namespaces gives its link back."""
    helpers = ml30_reader._READER._helpers
    links = helpers.read_links(_code_with_a_link(ml30.COM, ml30.STR))
    assert [link.url for link in links] == ["https://example.org/A"]


# ── SDMX-JSON reads (#466) ──────────────────────────────────────────────────


def _json_codelist(codes: int, *, name: str = "Code") -> bytes:
    codelist = Codelist(
        id="CL",
        maintainer="AG",
        version="1.0",
        name={"en": "Codes"},
        codes=[Code(id=f"C{i}", name={"en": f"{name} {i}"}) for i in range(codes)],
    )
    return formats.serialize(StructureMessage(codelists=[codelist]), version="json", validate=False)


class _CountingForbidden(frozenset[str]):
    """`_rules.XML_FORBIDDEN`, counting the scans that reach it."""

    scans = 0

    @override
    def isdisjoint(self, other: Iterable[object], /) -> bool:
        type(self).scans += 1
        return super().isdisjoint(other)


def _scans_while_parsing(monkeypatch: pytest.MonkeyPatch, document: bytes) -> int:
    _CountingForbidden.scans = 0
    with monkeypatch.context() as patch:
        patch.setattr(_rules, "XML_FORBIDDEN", _CountingForbidden(_rules.XML_FORBIDDEN))
        formats.parse(document)
    return _CountingForbidden.scans


def test_printable_ascii_names_skip_the_character_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    """A printable-ASCII name cannot hold an XML-forbidden character, so it is not scanned (#466).

    The scan ran twice per name on a JSON read, ~22 ms a pass on UNSD's 68,191
    codes, and once on an SDMX-ML read.
    """
    assert _scans_while_parsing(monkeypatch, _json_codelist(200)) == 0


def test_a_name_outside_printable_ascii_is_still_scanned(monkeypatch: pytest.MonkeyPatch) -> None:
    """The discrimination check: the count above can grow."""
    assert _scans_while_parsing(monkeypatch, _json_codelist(200, name="Café")) > 0


@pytest.mark.parametrize(
    "text",
    [
        "plain",
        "tab\there",
        "line\nbreak",
        "cr\rhere",
        "del\x7f",
        "nel\x85",
        "Café",
        "nul\x00",
        "bell\x07",
        "lone\ud800",
        "￾",
        "",
    ],
)
def test_the_fast_path_answers_as_the_scan_does(text: str) -> None:
    by_scan = next((c for c in text if c in validation._XML_FORBIDDEN), None)
    assert validation._first_xml_incompatible(text) == by_scan


def test_reading_codes_without_annotations_builds_no_annotations_per_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every annotation-less item shares one empty `Annotations` (#466)."""
    one = _calls_while_parsing(monkeypatch, _json_codelist(1), Annotations, "__init__")
    many = _calls_while_parsing(monkeypatch, _json_codelist(200), Annotations, "__init__")
    assert many == one, f"Annotations built per code: {one} for 1 code, {many} for 200"
