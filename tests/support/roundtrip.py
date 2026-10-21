"""Shared fixture loading and round-trip harness for the format tests.

Before this module, `_load` / `_schema` / `_roundtrip` were each defined
independently in five to eight test files (21 definitions, 14 of them
distinct). The differences were almost entirely accidental — a docstring here,
a hard-coded version there — but nothing made that visible, so each new format
test copied whichever neighbour it was written next to.

The XSD-validating round-trip is the piece worth having in one place. It is
what catches a writer emitting non-conformant XML that our own reader happily
tolerates, and a test file that quietly omitted the validation step would look
exactly like one that included it.

Test-only; not packaged with sdmxlib.
"""

import functools
from collections.abc import Callable
from pathlib import Path

from lxml import etree

from sdmxlib.formats import parse
from sdmxlib.formats.sdmx_json.reader import read as json_read
from sdmxlib.formats.sdmx_json.writer import write as json_write
from sdmxlib.formats.sdmx_ml21.writer import write as ml21_write
from sdmxlib.formats.sdmx_ml30.writer import write as ml30_write
from sdmxlib.formats.sdmx_ml31.writer import write as ml31_write
from sdmxlib.model.message import MetadataMessage, StructureMessage
from tests.support.xsd import SchemaVersion, assert_valid_sdmx_ml, compile_structure_schema

FIXTURES = Path(__file__).parent.parent / "fixtures"
"""Root of the checked-in wire fixtures."""

_FIXTURE_DIR: dict[SchemaVersion, str] = {"2.1": "sdmx_ml21", "3.0": "sdmx_ml30", "3.1": "sdmx_ml31"}

_WRITE: dict[SchemaVersion, Callable[[StructureMessage], bytes]] = {
    "2.1": ml21_write,
    "3.0": ml30_write,
    "3.1": ml31_write,
}


def as_structure(msg: StructureMessage | MetadataMessage) -> StructureMessage:
    """Narrow `parse`'s auto-detected result to a structure message.

    ``parse`` discriminates on the wire envelope and can hand back either kind.
    The fixtures here are all structure messages, so this asserts what the
    caller already assumes instead of leaving the union to be ignored — the
    per-module helpers simply annotated ``-> StructureMessage`` and moved on,
    which nothing caught because neither checker is pointed at ``tests/``.

    Public since #347 pointed basedpyright at ``tests/unit`` and turned that
    last sentence into 97 findings across 18 files. One narrowing with one
    message beats 97 inline asserts, and it is the same one the fixtures in
    this module were already using.
    """
    assert isinstance(msg, StructureMessage), f"expected a StructureMessage, got {type(msg).__name__}"
    return msg


@functools.cache
def structure_schema(version: SchemaVersion) -> etree.XMLSchema:
    """The compiled SDMX-ML structure XSD for *version*.

    Cached for the session rather than per module. Compiling costs 6-12 ms —
    three of the previous per-module caches claimed "~100 ms" in their
    docstrings, which was wrong by an order of magnitude, so the consolidation
    is about having one definition rather than about speed.

    Args:
        version: SDMX-ML schema version.

    Returns:
        The compiled schema.
    """
    return compile_structure_schema(version)


def load_xml(version: SchemaVersion, name: str) -> StructureMessage:
    """Parse the named SDMX-ML fixture for *version*.

    Args:
        version: ``"2.1"``, ``"3.0"`` or ``"3.1"`` — selects the fixture
            directory.
        name: Fixture stem, without the ``.xml`` suffix.

    Returns:
        The parsed message.
    """
    return as_structure(parse((FIXTURES / _FIXTURE_DIR[version] / f"{name}.xml").read_bytes()))


def load_json(name: str) -> StructureMessage:
    """Parse the named SDMX-JSON structure fixture.

    Args:
        name: Fixture stem, without the ``.json`` suffix.

    Returns:
        The parsed message.
    """
    return json_read((FIXTURES / "sdmx_json" / f"{name}.json").read_bytes())


def roundtrip_xml(version: SchemaVersion, name: str) -> tuple[StructureMessage, StructureMessage]:
    """Parse a fixture, write it, validate against the XSD, and re-parse.

    The validation step is the point. A pure round-trip cannot catch a writer
    that emits non-conformant XML which our own reader tolerates — the two
    sides share the same blind spot. Validating the intermediate document
    against the official schema does not.

    Args:
        version: SDMX-ML version; selects both the fixture directory and the
            writer.
        name: Fixture stem.

    Returns:
        ``(original, re-parsed)`` for the caller to compare.
    """
    orig = load_xml(version, name)
    written = _WRITE[version](orig)
    assert_valid_sdmx_ml(written, structure_schema(version))
    return orig, as_structure(parse(written))


def roundtrip_json(msg: StructureMessage) -> StructureMessage:
    """Write *msg* as SDMX-JSON and read it back.

    Args:
        msg: The message to round-trip.

    Returns:
        The re-parsed message.
    """
    return json_read(json_write(msg))
