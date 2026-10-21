"""Smoke + forward-compat tests for the vendored SDMX-ML XSDs.

Confirms the schemas under ``tests/schemas/{sdmx_ml21,sdmx_ml30,sdmx_ml31}/``
compile cleanly via lxml. If these tests fail, the rest of the XSD-validation
machinery (writer round-trip validation, input-fixture validation) won't
work either, so failures here surface the schema-set problem before any
writer test points at it.

This module also documents known 3.0 → 3.1 divergences. We don't yet emit
3.1 output, but vendoring the 3.1 XSDs lets us check whether our 3.0
output also happens to be 3.1-valid (mostly yes), and pin the set of
schema changes upstream that we'll need to handle when we do add 3.1.
"""

from pathlib import Path

import pytest
from lxml import etree

pytestmark = pytest.mark.unit

_FIXTURES_30 = Path(__file__).parent.parent / "fixtures" / "sdmx_ml30"


# ── Compile smoke ─────────────────────────────────────────────────────────────


def test_sdmx_ml21_schema_compiles(sdmx_ml21_schema: etree.XMLSchema) -> None:
    assert isinstance(sdmx_ml21_schema, etree.XMLSchema)


def test_sdmx_ml30_schema_compiles(sdmx_ml30_schema: etree.XMLSchema) -> None:
    assert isinstance(sdmx_ml30_schema, etree.XMLSchema)


def test_sdmx_ml31_schema_compiles(sdmx_ml31_schema: etree.XMLSchema) -> None:
    """v3.1.0 schemas were vendored alongside 3.0 in MR !63."""
    assert isinstance(sdmx_ml31_schema, etree.XMLSchema)


# ── 3.0 → 3.1 forward-compat audit ────────────────────────────────────────────
# We don't have a 3.1 writer yet. Until we do, the value of having the 3.1
# XSDs vendored is twofold:
#   1. We can run our 3.0 fixtures through the 3.1 schema (after rewriting
#      namespaces) and see whether 3.1 changed anything that affects us.
#   2. When we DO add 3.1 emission, the divergence set documented here
#      tells the implementer exactly which fields changed.


def _to_3_1(xml: bytes) -> bytes:
    """Rewrite ``v3_0`` namespace declarations in *xml* to ``v3_1``."""
    return (
        xml.replace(b"v3_0/message", b"v3_1/message")
        .replace(b"v3_0/structure", b"v3_1/structure")
        .replace(b"v3_0/common", b"v3_1/common")
    )


def _validate_against_3_1(xml: bytes, schema: etree.XMLSchema) -> list[str]:
    """Return the list of validation error messages from running *xml*
    through the 3.1 XSD; empty list if valid."""
    doc = etree.fromstring(_to_3_1(xml))
    if schema.validate(doc):
        return []
    return [e.message for e in schema.error_log]


def test_with_groups_30_validates_against_31(sdmx_ml31_schema: etree.XMLSchema) -> None:
    """``with_groups.xml`` exercises DSD / group / measure shapes that
    transferred unchanged from 3.0 to 3.1."""
    xml = (_FIXTURES_30 / "with_groups.xml").read_bytes()
    assert _validate_against_3_1(xml, sdmx_ml31_schema) == []


def test_with_metadata_30_validates_against_31(sdmx_ml31_schema: etree.XMLSchema) -> None:
    """MSD + Metadataflow shapes from MR !61 transferred unchanged to 3.1."""
    xml = (_FIXTURES_30 / "with_metadata.xml").read_bytes()
    assert _validate_against_3_1(xml, sdmx_ml31_schema) == []


def test_minimal_30_diverges_from_31_on_known_attrs(sdmx_ml31_schema: etree.XMLSchema) -> None:
    """Pin the known 3.0 → 3.1 divergence set.

    Two attributes our 3.0 writer emits do not exist in the 3.1 schema:

      - ``Categorisation/@version`` — 3.1 dropped versioning on Categorisation
        (it follows AgencyScheme into the un-versioned sui-generis pattern).
      - ``DataConstraint/@role`` — 3.1 reworked DataConstraint; the
        ``role`` enum is no longer expressed as an attribute.

    A new error attribute in this list means upstream changed something
    else and the 3.1 implementer should audit before continuing. See
    follow-up issue #103 (3.1 Categorisation un-versioning) and #104
    (3.1 DataConstraint role removal).
    """
    xml = (_FIXTURES_30 / "minimal.xml").read_bytes()
    errors = _validate_against_3_1(xml, sdmx_ml31_schema)
    attrs = sorted(
        {
            msg.split("attribute '")[1].split("'")[0]
            for msg in errors
            if "attribute '" in msg and "is not allowed" in msg
        }
    )
    assert attrs == ["role", "version"], (
        "3.0 → 3.1 divergence set changed; the 3.1 implementer should audit "
        "the new errors and update this test plus the follow-up issues "
        "(#103, #104). Errors:\n  " + "\n  ".join(errors)
    )
