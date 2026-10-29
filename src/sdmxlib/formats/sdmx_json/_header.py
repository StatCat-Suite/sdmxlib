"""SDMX-JSON's ``meta`` object as a message's `Header` (#480).

``meta`` holds a message's id, test flag, preparation time, sender, receivers
and names. It has no source, no sender time zone, and none of a metadata
header's four; `formats.gaps` declares each. ``contentLanguages`` and
``links`` are the message's own wire facts, not header content, and are not
read.

A message without a header is written with the placeholders SDMX-ML uses, so
the same message always serialises to the same bytes, and read back as no
header at all.
"""

from sdmxlib.formats._coerce import to_datetime
from sdmxlib.formats.sdmx_json._dto import WireStruct, _camel_rename, emit_istr, ingest_istr
from sdmxlib.formats.sdmx_json._structure_dto import ContactJson, to_contact
from sdmxlib.formats.sdmx_json._structure_ingest import from_contact
from sdmxlib.formats.sdmx_ml._header import MESSAGE
from sdmxlib.model.errors import ReadGap, ValidationError
from sdmxlib.model.header import Header, MetadataHeader, Party, Sender
from sdmxlib.model.istring import InternationalString

#: What a header-less message is written with, as SDMX-ML writes it but with
#: the zone every SDMX-JSON schema requires on ``prepared``.
_PLACEHOLDER_ID = "SDMX_MSG"
_PLACEHOLDER_PREPARED = "2000-01-01T00:00:00Z"
_PLACEHOLDER_SENDER = "sdmxlib"


class PartyJson(WireStruct, rename=_camel_rename):
    """A ``meta.sender`` or ``meta.receivers[]`` entry; its id is optional only so a sloppy one decodes."""

    id: str | None = None
    name: str | None = None
    names: dict[str, str] | None = None
    contacts: list[ContactJson] | None = None


class MetaJson(WireStruct, rename=_camel_rename):
    """The ``meta`` object, every field optional so a sloppy one never fails the document's decode.

    `schema` comes first because readers recognise a message by it, and some
    look only at the start of the document: pysdmx reads the first 350
    characters (#465).
    """

    schema: str | None = None
    id: str | None = None
    test: bool | None = None
    prepared: str | None = None
    sender: PartyJson | None = None
    receivers: list[PartyJson] | None = None
    name: str | None = None
    names: dict[str, str] | None = None


def to_meta(header: Header | None, *, schema: str | None) -> MetaJson:
    """`header` as ``meta``, or the placeholders when it is None; `schema` leads it when given."""
    if header is None:
        return MetaJson(
            schema=schema,
            id=_PLACEHOLDER_ID,
            prepared=_PLACEHOLDER_PREPARED,
            sender=PartyJson(id=_PLACEHOLDER_SENDER),
        )
    name, names = emit_istr(header.name)
    return MetaJson(
        schema=schema,
        id=header.id,
        test=header.test or None,
        prepared=header.prepared.isoformat(),
        sender=_party(header.sender),
        receivers=[_party(receiver) for receiver in header.receivers] or None,
        name=name,
        names=names,
    )


def _party(party: Party) -> PartyJson:
    name, names = emit_istr(party.name)
    return PartyJson(
        id=party.id, name=name, names=names, contacts=[to_contact(contact) for contact in party.contacts] or None
    )


def from_meta(meta: MetaJson | None) -> tuple[Header | None, ReadGap | None]:
    """``meta`` as a `Header`, and the loss when it cannot be built.

    None, and no loss, when there is none or it holds only the placeholders.
    A ``meta`` missing a key its schema requires (an id, ``prepared``, a
    party's id), or holding what the model cannot (a ``prepared`` that is no
    date-time), is None and a loss, as SDMX-ML's header is: it fails nothing,
    and is not dropped silently.
    """
    if meta is None or _is_placeholder(meta):
        return None, None
    parties = [meta.sender, *(meta.receivers or ())]
    if meta.id is None or meta.prepared is None or any(party is None or party.id is None for party in parties):
        return None, _lost("it lacks an id, prepared, or a party's id, which the schema requires")
    try:
        header = Header(
            id=meta.id,
            test=bool(meta.test),
            prepared=to_datetime(meta.prepared, what="meta.prepared"),
            sender=_sender(meta.sender) if meta.sender is not None else Sender(id=""),
            receivers=tuple(_read_party(receiver) for receiver in meta.receivers or ()),
            name=InternationalString(**ingest_istr(meta.name, meta.names)),
        )
    except (ValidationError, ValueError) as exc:
        return None, _lost(str(exc))
    return header, None


def from_metadata_meta(meta: MetaJson | None) -> tuple[MetadataHeader | None, ReadGap | None]:
    """`from_meta` for a reference-metadata message, whose four more fields SDMX-JSON has no place for."""
    header, lost = from_meta(meta)
    if header is None:
        return None, lost
    return MetadataHeader(
        id=header.id,
        test=header.test,
        prepared=header.prepared,
        sender=header.sender,
        receivers=header.receivers,
        name=header.name,
    ), None


def _lost(reason: str) -> ReadGap:
    return ReadGap(urn=MESSAGE, construct="meta", count=1, reason=f"read as no header: {reason}")


def _is_placeholder(meta: MetaJson) -> bool:
    return (
        meta.id == _PLACEHOLDER_ID
        and meta.prepared == _PLACEHOLDER_PREPARED
        and meta.sender == PartyJson(id=_PLACEHOLDER_SENDER)
        and not (meta.test or meta.receivers or meta.name or meta.names)
    )


def _read_party(party: PartyJson) -> Party:
    return Party(
        id=party.id or "",
        name=InternationalString(**ingest_istr(party.name, party.names)),
        contacts=tuple(from_contact(contact) for contact in party.contacts or ()),
    )


def _sender(party: PartyJson) -> Sender:
    read = _read_party(party)
    return Sender(id=read.id, name=read.name, contacts=read.contacts)
