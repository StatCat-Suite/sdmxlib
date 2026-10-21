"""XSD-validation helpers for SDMX-ML writer tests.

The vendored XSDs live under ``tests/schemas/sdmx_ml{21,30,31}/``. The flat
per-version layout lets lxml's `etree.XMLSchema` resolve each
``<xs:include schemaLocation="..."/>`` relative to the parsed file's own
directory, with no catalog manipulation.

Schema compilation is the slow part (~100 ms per version). Tests should pull
the compiled `etree.XMLSchema` via the session-scoped fixtures in
``tests/unit/conftest.py``; this module exposes the compile primitive plus a
validation helper that raises a readable `AssertionError` with the full
error log.
"""

from pathlib import Path
from typing import Literal

from lxml import etree

SchemaVersion = Literal["2.1", "3.0", "3.1"]

_SCHEMAS_DIR = Path(__file__).resolve().parent.parent / "schemas"
_ENTRY = "SDMXMessage.xsd"
_VERSION_DIR = {"2.1": "sdmx_ml21", "3.0": "sdmx_ml30", "3.1": "sdmx_ml31"}


def compile_structure_schema(version: SchemaVersion) -> etree.XMLSchema:
    """Compile the SDMX-ML schema set for *version*.

    Returns:
        A compiled `etree.XMLSchema` rooted at the version's
        ``SDMXMessage.xsd``. Imported sibling schemas are resolved
        relative to that file's directory.

    Raises:
        FileNotFoundError: If the vendored schemas for *version* are
            missing — points the caller at the README.
        etree.XMLSchemaParseError: If the upstream XSDs themselves
            fail to compile (would mean a bad refresh).
    """
    entry = _SCHEMAS_DIR / _VERSION_DIR[version] / _ENTRY
    if not entry.exists():
        msg = f"SDMX-ML {version} schemas not found at {entry}. See tests/schemas/README.md for how to refresh them."
        raise FileNotFoundError(msg)
    return etree.XMLSchema(etree.parse(entry))


def assert_valid_sdmx_ml(xml: bytes, schema: etree.XMLSchema) -> None:
    """Validate *xml* against *schema*; raise AssertionError on failure.

    The lxml error log is walked into a multi-line message — each entry
    carries the failing line/column and a one-line diagnostic. This reads
    better in pytest output than the raw ``XMLSchemaError.__str__`` lxml
    raises by default (which truncates after the first error).
    """
    doc = etree.fromstring(xml)
    if schema.validate(doc):
        return
    errors = [f"  line {e.line}, col {e.column}: {e.message}" for e in schema.error_log]
    detail = "\n".join(errors) if errors else "  (no detail from schema.error_log)"
    msg = f"XML failed SDMX-ML XSD validation:\n{detail}"
    raise AssertionError(msg)
