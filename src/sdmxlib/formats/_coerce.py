"""Convert document text to Python scalars, failing with the declared error.

Every value a reader converts came out of a payload, so a value that will not
convert means the payload is malformed — not that the caller made a mistake.
Python's builtins disagree: ``int("")``, ``float("x")`` and
``datetime.fromisoformat("")`` all raise a bare ``ValueError``, and before
#247 each of those escaped `sdmxlib.formats.parse` as itself.

Fuzzing found them one family at a time — ``minOccurs=""``, then a
``maxLength`` that was not a numeral, then a ``startTime`` of ``""`` — which
is the argument for a single module rather than a `try` at each site: the next
reader to convert something gets the right behaviour by importing from here.

Shared by the XML and SDMX-JSON readers, hence `sdmxlib.formats` rather than
either package.
"""

from datetime import date, datetime
from enum import Enum

from sdmxlib.model.errors import SdmxParseError


def to_enum[E: Enum](enum: type[E], raw: str, *, what: str) -> E:
    """Look up an enum member by value, or raise `SdmxParseError`.

    ``SomeEnum(value)`` raises a bare ``ValueError`` whose message names the
    Python class rather than the SDMX attribute, which is neither catchable as
    a parse failure nor useful to whoever has to fix the document.
    """
    try:
        return enum(raw)
    except ValueError as exc:
        allowed = ", ".join(repr(m.value) for m in enum)
        msg = f"{what} must be one of {allowed}, got {raw!r}"
        raise SdmxParseError(msg) from exc


def to_int(raw: str, *, what: str) -> int:
    """Parse an integer, or raise `SdmxParseError` naming *what* failed."""
    try:
        return int(raw)
    except ValueError as exc:
        msg = f"{what} must be an integer, got {raw!r}"
        raise SdmxParseError(msg) from exc


def to_float(raw: str, *, what: str) -> float:
    """Parse a number, or raise `SdmxParseError` naming *what* failed."""
    try:
        return float(raw)
    except ValueError as exc:
        msg = f"{what} must be a number, got {raw!r}"
        raise SdmxParseError(msg) from exc


def to_datetime(raw: str, *, what: str) -> datetime:
    """Parse an ISO 8601 timestamp, or raise `SdmxParseError`."""
    try:
        return datetime.fromisoformat(raw)
    except ValueError as exc:
        msg = f"{what} must be an ISO 8601 date-time, got {raw!r}"
        raise SdmxParseError(msg) from exc


def to_date(raw: str, *, what: str) -> date:
    """Parse an ISO 8601 date, or raise `SdmxParseError`."""
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        msg = f"{what} must be an ISO 8601 date, got {raw!r}"
        raise SdmxParseError(msg) from exc
