"""A value outside an SDMX enumeration is a parse error naming the attribute (GH-241).

Fuzzing found ``resolvePeriod="enGOfPeriod"`` escaping `sl.parse` as a bare
``ValueError`` from the 3.1 reader. Each reader site that turns document text
into an enum goes through `formats._coerce.to_enum`; these cover the date-map
attributes in every format that carries them, and the header's
``DataSetAction``.
"""

import pytest

import sdmxlib as sl
from tests.support.repo import ROOT

pytestmark = pytest.mark.unit

_GOLDEN = ROOT / "tests" / "golden" / "render"


@pytest.mark.parametrize(
    ("name", "version", "old", "attribute"),
    [
        ("structure-3.0.xml", "3.0", 'resolvePeriod="endOfPeriod"', "resolvePeriod"),
        ("structure-3.0.xml", "3.0", 'epochPeriod="second"', "epochPeriod"),
        ("structure-3.1.xml", "3.1", 'resolvePeriod="endOfPeriod"', "resolvePeriod"),
        ("structure-3.1.xml", "3.1", 'epochPeriod="second"', "epochPeriod"),
        ("structure-2.0.json", "json", '"resolvePeriod": "endOfPeriod"', "resolvePeriod"),
        ("structure-2.0.json", "json", '"epochPeriod": "second"', "epochPeriod"),
    ],
)
def test_a_value_outside_the_enumeration_is_a_parse_error(name: str, version: str, old: str, attribute: str) -> None:
    text = (_GOLDEN / name).read_text()
    assert text.count(old) == 1
    bogus = text.replace(old, old.replace(old.rsplit('"', 2)[1], "enGOfPeriod"))

    with pytest.raises(sl.SdmxParseError, match=rf"{attribute} must be one of .*got 'enGOfPeriod'"):
        sl.parse(bogus.encode(), version=version)


def test_the_unchanged_documents_read() -> None:
    """Discrimination: the documents above read once the value is valid."""
    for name, version in (("structure-3.0.xml", "3.0"), ("structure-3.1.xml", "3.1"), ("structure-2.0.json", "json")):
        sl.parse((_GOLDEN / name).read_bytes(), version=version)


def _with_action(action: str) -> bytes:
    """The golden metadata message with a real header carrying `action`."""
    document = (_GOLDEN / "metadata-3.0.xml").read_text()
    assert document.count("<mes:ID>SDMX_MSG</mes:ID>") == 1
    document = document.replace("<mes:ID>SDMX_MSG</mes:ID>", "<mes:ID>MSG1</mes:ID>")
    header_end = f"<mes:DataSetAction>{action}</mes:DataSetAction></mes:Header>"
    return document.replace("</mes:Header>", header_end, 1).encode()


def test_a_bad_data_set_action_leaves_the_header_unread() -> None:
    """The header reader records a header it cannot build as a loss, so nothing escapes."""
    valid = sl.parse(_with_action("Information"), version="3.0")
    assert isinstance(valid, sl.MetadataMessage)
    assert valid.header is not None
    assert valid.header.data_set_action is sl.ActionType.INFORMATION

    bogus = sl.parse(_with_action("Bogus"), version="3.0", strict=False)
    assert isinstance(bogus, sl.MetadataMessage)
    assert bogus.header is None
