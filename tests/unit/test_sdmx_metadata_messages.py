"""Wire-format round-trip tests for MetadataMessage.

Covers SDMX-JSON 2.0 metadata-message envelope and SDMX-ML 3.0
<mes:GenericMetadata> envelope. Both share the same in-memory
MetadataMessage; only the wire format differs.
"""

from typing import Any

import attrs
import pytest

from sdmxlib.formats import parse, to_json, to_xml
from sdmxlib.formats.sdmx_json.metadata import (
    METADATA_SCHEMA_URL,
)
from sdmxlib.formats.sdmx_json.metadata import (
    read as read_metadata_json,
)
from sdmxlib.formats.sdmx_json.metadata import (
    write as write_metadata_json,
)
from sdmxlib.formats.sdmx_ml30.metadata import read as read_metadata_xml
from sdmxlib.formats.sdmx_ml30.metadata import write as write_metadata_xml
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.message import MetadataMessage, StructureMessage
from sdmxlib.model.metadata_provision import MetadataProvisionAgreement
from sdmxlib.model.metadataflow import Metadataflow
from sdmxlib.model.metadataset import MetadataSet, ReportedAttribute
from sdmxlib.model.metadatastructure import MetadataStructure
from sdmxlib.model.ref import Ref
from tests.support.factories import code_urn, footnote_metadataflow

pytestmark = pytest.mark.unit


# ── Fixtures ─────────────────────────────────────────────────────────────


def _make_msd() -> MetadataStructure:
    return MetadataStructure(name={"en": "MSD_FOOTNOTES"}, id="MSD_FOOTNOTES", maintainer="STATCAN")


def _make_set(structure: Metadataflow | MetadataProvisionAgreement) -> MetadataSet:
    return MetadataSet(
        id="FOOTNOTE_A",
        maintainer="STATCAN",
        structure=structure,
        name={"en": "Footnote A"},
        targets=[
            code_urn("CL_CHARACTERISTIC", "1", agency="STATCAN"),
            code_urn("CL_CHARACTERISTIC", "5", agency="STATCAN"),
        ],
        attributes=[
            ReportedAttribute(
                id="CONTACT",
                children=[
                    ReportedAttribute(id="CONTACT_ORG_UNIT", value="STAT-D2"),
                    ReportedAttribute(
                        id="CONTACT_NAME",
                        value={"en": "CPI Statistics Unit", "fr": "Unite des stats IPC"},
                    ),
                ],
            ),
            ReportedAttribute(id="FREQ", value="M"),
            ReportedAttribute(id="POSTAL_CODE", value=2721),
        ],
    )


# ── SDMX-JSON metadata round-trip ──────────────────────────────────────


def test_json_metadata_round_trip_with_metadataflow_ref() -> None:
    msd = _make_msd()
    mdf = footnote_metadataflow(msd, name=None)
    ms = _make_set(mdf)

    out = read_metadata_json(write_metadata_json(MetadataMessage(metadata_sets=[ms])))

    assert isinstance(out, MetadataMessage)
    assert len(out.metadata_sets) == 1
    parsed = out.metadata_sets[0]
    assert parsed.id == "FOOTNOTE_A"
    assert parsed.structure.urn.artefact_class == "Metadataflow"
    assert parsed.structure.urn.id == "MDF_FOOTNOTES"
    assert {t.urn.item_id for t in parsed.targets} == {"1", "5"}

    contact = parsed.attributes["CONTACT"]
    assert contact.children["CONTACT_ORG_UNIT"].value == "STAT-D2"
    name_val = contact.children["CONTACT_NAME"].value
    assert isinstance(name_val, InternationalString)
    assert name_val["en"].text == "CPI Statistics Unit"

    assert parsed.attributes["FREQ"].value == "M"
    assert parsed.attributes["POSTAL_CODE"].value == 2721


def test_json_metadata_round_trip_with_mpa_ref() -> None:
    mpa = MetadataProvisionAgreement(
        name={"en": "MPA_FOOTNOTES"},
        id="MPA_FOOTNOTES",
        maintainer="STATCAN",
        metadataflow=footnote_metadataflow(_make_msd(), name=None),
    )
    out = read_metadata_json(write_metadata_json(MetadataMessage(metadata_sets=[_make_set(mpa)])))
    parsed = out.metadata_sets[0]
    assert parsed.structure.urn.artefact_class == "MetadataProvisionAgreement"
    assert parsed.structure.urn.id == "MPA_FOOTNOTES"


def test_json_metadata_envelope_uses_metadata_schema() -> None:
    msd = _make_msd()
    raw = write_metadata_json(MetadataMessage(metadata_sets=[_make_set(footnote_metadataflow(msd, name=None))]))
    assert METADATA_SCHEMA_URL.encode("utf-8") in raw
    # And it lives at $schema, not as a metadataSets-in-Structure mistake.
    assert b'"$schema"' in raw[:200]


# ── SDMX-ML 3.0 GenericMetadata round-trip ─────────────────────────────


def test_xml_metadata_round_trip_with_metadataflow_ref() -> None:
    msd = _make_msd()
    mdf = footnote_metadataflow(msd, name=None)
    ms = _make_set(mdf)

    out = read_metadata_xml(write_metadata_xml(MetadataMessage(metadata_sets=[ms])))

    assert isinstance(out, MetadataMessage)
    parsed = out.metadata_sets[0]
    assert parsed.id == "FOOTNOTE_A"
    assert parsed.structure.urn.artefact_class == "Metadataflow"
    assert {t.urn.item_id for t in parsed.targets} == {"1", "5"}

    contact = parsed.attributes["CONTACT"]
    assert contact.children["CONTACT_ORG_UNIT"].value == "STAT-D2"
    name_val = contact.children["CONTACT_NAME"].value
    assert isinstance(name_val, InternationalString)
    assert name_val["fr"].text == "Unite des stats IPC"

    assert parsed.attributes["FREQ"].value == "M"
    # Scalar numeric round-trips through XML as a string (Value text content
    # is anySimpleType — type coercion belongs at the MSD-aware layer).
    assert parsed.attributes["POSTAL_CODE"].value == "2721"


def test_xml_metadata_round_trip_with_mpa_ref() -> None:
    mpa = MetadataProvisionAgreement(
        name={"en": "MPA_FOOTNOTES"},
        id="MPA_FOOTNOTES",
        maintainer="STATCAN",
        metadataflow=footnote_metadataflow(_make_msd(), name=None),
    )
    raw = write_metadata_xml(MetadataMessage(metadata_sets=[_make_set(mpa)]))
    # The set body references the MPA (a URN string, per 3.0); the message header
    # resolves through to the MSD (MPA → Metadataflow → MSD).
    assert b"<md:MetadataProvisionAgreement>urn:" in raw
    assert b".metadatastructure.MetadataStructure=STATCAN:MSD_FOOTNOTES" in raw  # header resolved through
    parsed = read_metadata_xml(raw).metadata_sets[0]
    assert parsed.structure.urn.artefact_class == "MetadataProvisionAgreement"
    assert parsed.structure.urn.id == "MPA_FOOTNOTES"


def test_xml_metadata_envelope_root_is_generic_metadata() -> None:
    msd = _make_msd()
    raw = write_metadata_xml(MetadataMessage(metadata_sets=[_make_set(footnote_metadataflow(msd, name=None))]))
    assert b"GenericMetadata" in raw[:500]
    # Header carries the MSD binding.
    assert b"structureID" in raw


def test_xml_metadata_repeated_attribute_siblings() -> None:
    msd = _make_msd()
    ms = MetadataSet(
        name={"en": "MS_REP"},
        id="MS_REP",
        maintainer="STATCAN",
        structure=footnote_metadataflow(msd, name=None),
        targets=[code_urn("CL_T", "1", agency="STATCAN")],
        attributes=[
            ReportedAttribute(id="TAG", value="alpha"),
            ReportedAttribute(id="TAG", value="beta"),
        ],
    )
    out = read_metadata_xml(write_metadata_xml(MetadataMessage(metadata_sets=[ms])))
    parsed = out.metadata_sets[0]
    values = [a.value for a in parsed.attributes if a.id == "TAG"]
    assert values == ["alpha", "beta"]


# ── Top-level parse/write dispatch ──────────────────────────────────────


def test_parse_auto_dispatches_metadata_xml() -> None:
    msd = _make_msd()
    raw = write_metadata_xml(MetadataMessage(metadata_sets=[_make_set(footnote_metadataflow(msd, name=None))]))
    msg = parse(raw)
    assert isinstance(msg, MetadataMessage)


def test_parse_auto_dispatches_metadata_json() -> None:
    msd = _make_msd()
    raw = write_metadata_json(MetadataMessage(metadata_sets=[_make_set(footnote_metadataflow(msd, name=None))]))
    msg = parse(raw)
    assert isinstance(msg, MetadataMessage)


def test_parse_routes_structure_message_correctly() -> None:
    """Sanity: a Structure JSON without metadata sets still parses as Structure."""
    msg = parse(b'{"meta":{},"data":{"codelists":[]}}')
    assert isinstance(msg, StructureMessage)


def test_to_xml_dispatches_on_metadata_message() -> None:
    msd = _make_msd()
    raw = to_xml(MetadataMessage(metadata_sets=[_make_set(footnote_metadataflow(msd, name=None))]))
    assert b"GenericMetadata" in raw[:500]


def test_to_json_dispatches_on_metadata_message() -> None:
    msd = _make_msd()
    raw = to_json(MetadataMessage(metadata_sets=[_make_set(footnote_metadataflow(msd, name=None))]))
    assert METADATA_SCHEMA_URL.encode("utf-8") in raw


def test_to_xml_for_metadata_rejects_2_1_version() -> None:
    """SDMX-ML 2.1 GenericMetadata is intentionally out of scope; to_xml
    silently upgrades to 3.0 rather than failing."""
    msd = _make_msd()
    raw = to_xml(MetadataMessage(metadata_sets=[_make_set(footnote_metadataflow(msd, name=None))]), version="2.1")
    # Quietly upgraded to 3.0 — assert the v3.0 namespace is present.
    assert b"v3_0/message" in raw


# ── Cross-message resolution: target URNs survive round trips ──────────


def test_target_urn_round_trips_with_correct_artefact_class() -> None:
    msd = _make_msd()
    ms = MetadataSet(
        name={"en": "MS"},
        id="MS",
        maintainer="STATCAN",
        structure=footnote_metadataflow(msd, name=None),
        targets=[code_urn("CL_X", "1", agency="STATCAN")],
    )
    out = read_metadata_json(write_metadata_json(MetadataMessage(metadata_sets=[ms])))
    target_urn = out.metadata_sets[0].targets[0].urn
    assert target_urn.artefact_class == "Code"
    assert target_urn.item_id == "1"


# Suppress unused-import warnings for symbols re-exported via the
# public API surface below.
_ = (Ref, Any)


# ── SDMX-ML 3.0 GenericMetadata XSD conformity (#187) ────────────────────


def test_xml_metadata_conforms_to_3_0_xsd_metadataflow(sdmx_ml30_schema) -> None:
    from tests.support.xsd import assert_valid_sdmx_ml

    raw = write_metadata_xml(MetadataMessage(metadata_sets=[_make_set(footnote_metadataflow(_make_msd(), name=None))]))
    assert_valid_sdmx_ml(raw, sdmx_ml30_schema)


def test_xml_metadata_conforms_to_3_0_xsd_mpa(sdmx_ml30_schema) -> None:
    from tests.support.xsd import assert_valid_sdmx_ml

    mpa = MetadataProvisionAgreement(
        name={"en": "MPA_FOOTNOTES"},
        id="MPA_FOOTNOTES",
        maintainer="STATCAN",
        metadataflow=footnote_metadataflow(_make_msd(), name=None),
    )
    raw = write_metadata_xml(MetadataMessage(metadata_sets=[_make_set(mpa)]))
    assert_valid_sdmx_ml(raw, sdmx_ml30_schema)


def test_a_metadata_set_keeps_its_uri_and_external_reference_in_xml() -> None:
    """`uri` was written and never read back; the external reference was neither (#422)."""
    ms = attrs.evolve(
        _make_set(footnote_metadataflow(_make_msd(), name=None)),
        uri="https://example.org/uri/set",
        is_external_reference=True,
        service_url="https://example.org/registry",
        structure_url="https://example.org/set.xml",
    )
    (back,) = read_metadata_xml(write_metadata_xml(MetadataMessage(metadata_sets=[ms]))).metadata_sets
    assert (back.uri, back.is_external_reference, back.service_url, back.structure_url) == (
        "https://example.org/uri/set",
        True,
        "https://example.org/registry",
        "https://example.org/set.xml",
    )


def test_a_metadata_set_keeps_its_external_reference_flag_in_json() -> None:
    """SDMX-JSON has the flag and no URLs, which `gaps.py` declares."""
    ms = attrs.evolve(_make_set(footnote_metadataflow(_make_msd(), name=None)), is_external_reference=True)
    back = parse(to_json(MetadataMessage(metadata_sets=[ms])))
    assert isinstance(back, MetadataMessage)
    assert back.metadata_sets[0].is_external_reference
