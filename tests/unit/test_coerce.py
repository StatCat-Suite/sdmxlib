"""`formats/_coerce` — the conversions exist to replace `ValueError`.

Every line this file covers was previously uncovered, and they were all the
`except` branches: the happy paths run on every parse, so the module read as
53% covered while the *entire reason it exists* was untested. Its docstring
says fuzzing found the cases one family at a time — `minOccurs=""`, a
non-numeric `maxLength`, a `startTime` of `""` — so the risk is not
hypothetical.

What matters is the *type*: `SdmxParseError` is what `sdmxlib.formats.parse`
promises, and a bare `ValueError` escaping it is uncatchable as a parse
failure. Asserting the type alone would pass on `raise SdmxParseError("")`,
so each case also checks the message names both the field and the bad value —
without them the error cannot tell anyone which part of the document to fix.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import Enum

import pytest

from sdmxlib.formats._coerce import to_date, to_datetime, to_enum, to_float, to_int
from sdmxlib.model.errors import SdmxParseError

pytestmark = pytest.mark.unit


class _Colour(Enum):
    RED = "red"
    BLUE = "blue"


# ── the happy paths, so a converter that always raised would fail here ───────


def test_each_converter_returns_the_parsed_value() -> None:
    assert to_enum(_Colour, "red", what="colour") is _Colour.RED
    assert to_int("42", what="minOccurs") == 42
    assert to_float("1.5", what="maxValue") == 1.5
    assert to_datetime("2026-01-01T00:00:00+00:00", what="startTime") == datetime(2026, 1, 1, tzinfo=UTC)
    assert to_date("2026-01-01", what="validFrom") == date(2026, 1, 1)


# ── the reason the module exists ─────────────────────────────────────────────

#: `""` is in every row because it is the shape fuzzing actually found, and
#: the one Python's builtins are worst at: `int("")` and
#: `datetime.fromisoformat("")` both raise with a message naming neither the
#: field nor the document.
_BAD = [
    pytest.param(lambda raw: to_enum(_Colour, raw, what="colour"), "", "colour", id="enum-empty"),
    pytest.param(lambda raw: to_enum(_Colour, raw, what="colour"), "green", "colour", id="enum-unknown"),
    pytest.param(lambda raw: to_int(raw, what="minOccurs"), "", "minOccurs", id="int-empty"),
    pytest.param(lambda raw: to_int(raw, what="maxLength"), "many", "maxLength", id="int-word"),
    pytest.param(lambda raw: to_int(raw, what="minOccurs"), "1.5", "minOccurs", id="int-decimal"),
    pytest.param(lambda raw: to_float(raw, what="maxValue"), "", "maxValue", id="float-empty"),
    pytest.param(lambda raw: to_float(raw, what="maxValue"), "x", "maxValue", id="float-word"),
    pytest.param(lambda raw: to_datetime(raw, what="startTime"), "", "startTime", id="datetime-empty"),
    pytest.param(lambda raw: to_datetime(raw, what="startTime"), "not-a-date", "startTime", id="datetime-word"),
    pytest.param(lambda raw: to_datetime(raw, what="startTime"), "2026-13-01", "startTime", id="datetime-month-13"),
    pytest.param(lambda raw: to_date(raw, what="validFrom"), "", "validFrom", id="date-empty"),
    pytest.param(lambda raw: to_date(raw, what="validFrom"), "2026-02-30", "validFrom", id="date-feb-30"),
]


@pytest.mark.parametrize(("convert", "raw", "what"), _BAD)
def test_a_malformed_value_raises_sdmx_parse_error(convert, raw: str, what: str) -> None:
    """Not `ValueError`. A value that will not convert came out of a payload,
    so the payload is malformed — a distinction `sdmxlib.formats.parse`
    promises and its callers catch on."""
    with pytest.raises(SdmxParseError):
        convert(raw)


@pytest.mark.parametrize(("convert", "raw", "what"), _BAD)
def test_the_error_names_the_field_and_the_value(convert, raw: str, what: str) -> None:
    """Both, because either alone leaves the reader searching: the field
    without the value does not say which occurrence, and the value without the
    field does not say which attribute."""
    with pytest.raises(SdmxParseError) as caught:
        convert(raw)

    message = str(caught.value)
    assert what in message, f"the field name is missing from {message!r}"
    assert repr(raw) in message, f"the offending value is missing from {message!r}"


def test_an_enum_error_lists_what_was_allowed() -> None:
    """The one case where the caller cannot guess the fix from the field name."""
    with pytest.raises(SdmxParseError) as caught:
        to_enum(_Colour, "green", what="colour")

    assert "'red'" in str(caught.value)
    assert "'blue'" in str(caught.value)


@pytest.mark.parametrize(("convert", "raw", "what"), _BAD)
def test_the_original_valueerror_is_kept_as_the_cause(convert, raw: str, what: str) -> None:
    """`raise ... from exc` throughout. Losing it would drop the only record
    of what Python actually objected to."""
    with pytest.raises(SdmxParseError) as caught:
        convert(raw)

    assert isinstance(caught.value.__cause__, ValueError)
