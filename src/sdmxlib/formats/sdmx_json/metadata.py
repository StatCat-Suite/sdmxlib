"""SDMX-JSON 2.0 metadata-message reader and writer (typed msgspec DTOs).

Handles the GenericMetadata envelope keyed on
``$schema = https://json.sdmx.org/2.1/sdmx-json-metadata-schema.json``.

This is a different message type from the Structure message — it carries
populated MetadataSets, not definitions. The data object has exactly one
top-level field: ``metadataSets``.

Part of the msgspec migration (milestone: SDMX-JSON dict+orjson → msgspec).
The wire shape is modelled as typed ``msgspec.Struct`` DTOs used both
directions: ``to_*`` converters (attrs model → DTO) on write, typed
``msgspec.json.decode(..., type=...)`` + ``from_*`` on read. Reuses the shared ``_dto``
foundation (``Meta``/``Sender``/``ENCODER``, the ``name``/``names`` istring
split, the camel-rename convention).

Per the official spec, ``ReportedAttribute.value`` is a JSON union:
``integer | number | boolean | string | localisedText | null``. We store
scalars as Python primitives and the localisedText shape (a ``{lang: text}``
object) as ``InternationalString``. The MSD declares whether a string scalar
is a code id, a date, etc. — the wire format does not.
"""

from datetime import datetime
from typing import cast

import attrs
import msgspec

from sdmxlib.formats._coerce import to_datetime
from sdmxlib.formats._tags import message_language, texts
from sdmxlib.model.collections import ArtefactList, ItemList
from sdmxlib.model.errors import SdmxParseError
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.message import MetadataMessage
from sdmxlib.model.metadata_provision import MetadataProvisionAgreement
from sdmxlib.model.metadataflow import Metadataflow
from sdmxlib.model.metadataset import (
    MetadataSet,
    ReportedAttribute,
    ReportedAttributeValue,
)
from sdmxlib.model.organisation import Agency
from sdmxlib.model.ref import Ref
from sdmxlib.model.urn import SdmxUrn

from ._dto import ENCODER, Link, WireStruct, _camel_rename, emit_istr, ingest_istr
from ._header import MetaJson, from_metadata_meta, to_meta
from ._losses import undeclared
from ._structure_dto import read_links, read_uri, uri_links

METADATA_SCHEMA_URL = "https://json.sdmx.org/2.1/sdmx-json-metadata-schema.json"

#: A populated reported value on the wire: ``attributeType.value`` anyOf
#: ``integer | number | boolean | string | localisedText | null``. msgspec
#: decodes each JSON type to the matching member (bool/int/float/str stay
#: distinct; a ``{lang: text}`` object → ``dict[str, str]``).
type ValueJson = bool | int | float | str | dict[str, str] | None


# ── Wire Structs ──────────────────────────────────────────────────────────


#: Read back from the model rather than restated, so the two cannot drift —
#: the wire default and the model default must be the same string or a
#: version-less payload round-trips to a different version than it arrived as.
_DEFAULT_SET_VERSION: str = attrs.fields(MetadataSet).version.default


class ReportedAttributeJson(WireStruct, rename=_camel_rename):
    """One populated ``attributeType`` — id + value + recursive children."""

    id: str
    value: ValueJson = None
    attributes: list[ReportedAttributeJson] | None = None


class MetadataSetJson(WireStruct, rename=_camel_rename):
    """A populated MetadataSet on the wire (``data.metadataSets[*]``)."""

    # id/agencyID are always emitted — REQUIRED so omit_defaults can never drop
    # them (see _dto conventions).
    id: str
    agency_id: str
    #: Always set on write (a non-None value → always emitted); typed optional
    #: for read tolerance — real payloads (e.g. the TWG sample) omit it, and
    #: ingest falls back to the model default, which is semver. It cannot fall
    #: back to "1.0": the metadata schema pins VersionType to x.y.z, so the
    #: reader would turn a valid version-less payload into output its own
    #: schema rejects (#234).
    version: str | None = None
    name: str | dict[str, str] | None = None
    names: dict[str, str] | None = None
    description: str | None = None
    descriptions: dict[str, str] | None = None
    valid_from: str | None = None
    valid_to: str | None = None
    #: A maintainable's external reference (#422); `None` when false, so the
    #: default is never emitted.
    is_external_reference: bool | None = None
    service_url: str | None = None
    structure_url: str | None = None
    #: Structure ref: exactly one is emitted — the two conformant SDMX 3.0
    #: options. ``metadataflow`` for a Metadataflow ref,
    #: ``metadataProvisionAgreement`` for an MPA ref.
    metadataflow: str | None = None
    metadata_provision_agreement: str | None = None
    targets: list[str] = msgspec.field(default_factory=list)
    attributes: list[ReportedAttributeJson] = msgspec.field(default_factory=list)
    #: The set's ``@uri`` under ``rel="describedby"`` and its other links, as
    #: a structure artefact carries them (#422).
    links: list[Link] | None = None


class MetadataDataJson(WireStruct, rename=_camel_rename):
    """The ``data`` object of an SDMX-JSON 2.0 metadata message."""

    metadata_sets: list[MetadataSetJson] = msgspec.field(default_factory=list)


class MetadataMessageJson(WireStruct):
    """The outer envelope of an SDMX-JSON 2.0 metadata message.

    ``data`` is optional so a message with no ``data`` key — or an explicit
    ``"data": null`` — decodes to an empty message rather than raising,
    matching the reader's previous ``doc.get("data") or {}``.
    """

    meta: MetaJson | None = None
    data: MetadataDataJson | None = None


# ── Write (attrs model → DTO) ───────────────────────────────────────────────


def _emit_value(v: ReportedAttributeValue | None) -> ValueJson:
    """Model value → wire union (InternationalString → ``{lang: text}`` dict)."""
    if isinstance(v, InternationalString):
        return v.as_dict(copy=False)
    return v


def _to_reported_attribute(attr: ReportedAttribute) -> ReportedAttributeJson:
    return ReportedAttributeJson(
        id=attr.id,
        value=_emit_value(attr.value),
        attributes=[_to_reported_attribute(c) for c in attr.children] or None,
    )


def _to_metadata_set(ms: MetadataSet) -> MetadataSetJson:
    name, names = emit_istr(ms.name)
    desc, descs = emit_istr(ms.description)
    is_flow = ms.structure.urn.artefact_class == "Metadataflow"
    urn = str(ms.structure.urn)
    return MetadataSetJson(
        id=ms.id,
        agency_id=ms.agency_id,
        version=ms.version,
        name=name,
        names=names,
        description=desc,
        descriptions=descs,
        valid_from=ms.valid_from.isoformat() if ms.valid_from is not None else None,
        valid_to=ms.valid_to.isoformat() if ms.valid_to is not None else None,
        is_external_reference=ms.is_external_reference or None,
        service_url=ms.service_url,
        structure_url=ms.structure_url,
        metadataflow=urn if is_flow else None,
        metadata_provision_agreement=None if is_flow else urn,
        targets=[str(t.urn) for t in ms.targets],
        attributes=[_to_reported_attribute(a) for a in ms.attributes],
        links=uri_links(ms),
    )


# ── Read (DTO → attrs model) ────────────────────────────────────────────────


def _ingest_value(raw: ValueJson) -> ReportedAttributeValue | None:
    """Wire union → model value (``{lang: text}`` dict → InternationalString)."""
    if isinstance(raw, dict):
        return texts(((k, v) for k, v in raw.items() if isinstance(v, str)), "value")
    return raw


def _ingest_meta_name(
    name: str | dict[str, str] | None, names: dict[str, str] | None, field: str
) -> InternationalString:
    """MetadataSet name → InternationalString.

    Prefers the ``names`` map, and tolerates a dict under ``name`` (our own
    legacy output before the name/names split). A bare string with no map is
    refused, as `ingest_istr` refuses it: it states no language.
    """
    if not isinstance(name, dict):
        return ingest_istr(name, names, field)
    if names:
        return ingest_istr(None, names, field)
    return texts(((k, v) for k, v in name.items() if isinstance(v, str)), field)


def _from_reported_attribute(attr: ReportedAttributeJson) -> ReportedAttribute:
    return ReportedAttribute(
        id=attr.id,
        value=_ingest_value(attr.value),
        children=ItemList([_from_reported_attribute(c) for c in attr.attributes or []]),
    )


def _structure_ref(ms: MetadataSetJson) -> Ref[Metadataflow] | Ref[MetadataProvisionAgreement]:
    urn_str = ms.metadataflow or ms.metadata_provision_agreement
    if not urn_str:
        msg = "MetadataSet JSON has neither metadataflow nor metadataProvisionAgreement ref"
        raise SdmxParseError(msg)
    parsed = SdmxUrn.parse(urn_str)
    if parsed.artefact_class == "MetadataProvisionAgreement":
        return Ref[MetadataProvisionAgreement].unresolved(cast("SdmxUrn[MetadataProvisionAgreement]", parsed))
    return Ref[Metadataflow].unresolved(cast("SdmxUrn[Metadataflow]", parsed))


def _parse_dt(s: str | None) -> datetime | None:
    return to_datetime(s, what="a validity timestamp") if s else None


def _from_metadata_set(ms: MetadataSetJson) -> MetadataSet:
    return MetadataSet(
        id=ms.id,
        maintainer=Agency(id=ms.agency_id),
        structure=_structure_ref(ms),
        version=ms.version or _DEFAULT_SET_VERSION,
        name=_ingest_meta_name(ms.name, ms.names, "name"),
        description=_ingest_meta_name(ms.description, ms.descriptions, "description"),
        # Writer emits validFrom/validTo; parse them back so they round-trip (#159).
        valid_from=_parse_dt(ms.valid_from),
        valid_to=_parse_dt(ms.valid_to),
        is_external_reference=bool(ms.is_external_reference),
        service_url=ms.service_url,
        structure_url=ms.structure_url,
        targets=[Ref[object].unresolved(SdmxUrn.parse(t)) for t in ms.targets],
        attributes=ItemList([_from_reported_attribute(a) for a in ms.attributes]),
        uri=read_uri(ms.links),
        links=read_links(ms.links),
    )


# ── Entry points ────────────────────────────────────────────────────────────


def read(data: bytes) -> MetadataMessage:
    """Parse SDMX-JSON 2.0 metadata-message bytes into a MetadataMessage.

    The caller is expected to have determined this is a metadata message —
    use the top-level ``sdmxlib.parse`` to auto-discriminate by ``$schema``.
    The header's loss, and each metadata set's keys no struct declares, are
    recorded in `MetadataMessage.lost`.
    """
    doc = msgspec.json.decode(data, type=MetadataMessageJson)
    dtos = (doc.data or MetadataDataJson()).metadata_sets
    with message_language(None if doc.meta is None else doc.meta.content_languages):
        sets = [_from_metadata_set(ms) for ms in dtos]
        header, header_lost = from_metadata_meta(doc.meta)
    lost = [] if header_lost is None else [header_lost]
    for built, raw, dto in zip(sets, _raw_metadata_sets(data), dtos, strict=False):
        lost.extend(undeclared(str(built.urn), raw, dto))
    return MetadataMessage(metadata_sets=ArtefactList(sets), header=header, lost=lost)


def _raw_metadata_sets(data: bytes) -> list[object]:
    """The document's ``data.metadataSets``, decoded untyped, for `undeclared` to walk beside the structs."""
    raw = msgspec.json.decode(data, type=object)
    body = raw.get("data") if isinstance(raw, dict) else None
    sets = body.get("metadataSets") if isinstance(body, dict) else None
    return sets if isinstance(sets, list) else []


def write(msg: MetadataMessage, *, pretty_print: bool = False) -> bytes:
    """Serialise a MetadataMessage to SDMX-JSON 2.0 metadata-message bytes."""
    doc = {
        "$schema": METADATA_SCHEMA_URL,
        "meta": to_meta(msg.header, schema=None),
        "data": {"metadataSets": [_to_metadata_set(ms) for ms in msg.metadata_sets]},
    }
    encoded = ENCODER.encode(doc)
    return msgspec.json.format(encoded, indent=2) if pretty_print else encoded
