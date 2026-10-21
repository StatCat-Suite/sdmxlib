"""The ``<mes:Header>`` of every SDMX-ML version, read and written (#480).

`StructureHeaderType` and `GenericMetadataHeaderType` share their elements up
to ``Name`` and from ``Source``; the metadata header puts its ``Structure``
elements and four more between. The writer takes the ``Structure`` elements
as a callback, since only the metadata writer knows what they name.

A message without a header is written with fixed placeholders (`PLACEHOLDER`),
so the same message always serialises to the same bytes, and read back as no
header at all, so it round-trips.
"""

from collections.abc import Callable
from datetime import datetime

from lxml import etree
from typing_extensions import TypedDict

from sdmxlib.formats._coerce import to_datetime
from sdmxlib.model.errors import ReadGap, SdmxParseError, ValidationError
from sdmxlib.model.header import ActionType, Header, MetadataHeader, Party, Sender
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.organisation import Contact, MetadataProvider
from sdmxlib.model.ref import Ref

XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"

#: What a header-less message is written with: ``ID``, ``Test``, ``Prepared``
#: and the sender's id, which `StructureHeaderType` requires.
PLACEHOLDER = ("SDMX_MSG", "false", "2000-01-01T00:00:00", "sdmxlib")

#: What a header's `ReadGap` names, in place of an artefact's URN: the message itself.
MESSAGE = "(message)"

#: The ``ContactType`` channels, in schema order, and the field each fills.
_CHANNELS = (("Telephone", "phones"), ("Fax", "faxes"), ("X400", "x400s"), ("URI", "uris"), ("Email", "emails"))


# ── Writing ────────────────────────────────────────────────────────────────


def write_header(
    root: etree._Element,
    header: Header | None,
    *,
    com: str,
    mes: str,
    structures: Callable[[etree._Element], None] | None = None,
) -> None:
    """Append `header` to `root` as ``<mes:Header>``, or the placeholders when it is None.

    Args:
        root: The message element.
        header: What to write; a `MetadataHeader` writes its four elements too.
        com: The common namespace URI.
        mes: The message namespace URI.
        structures: Appends a metadata header's ``Structure`` elements, which
            come after ``Name``.
    """
    el = etree.SubElement(root, f"{{{mes}}}Header")

    def text(tag: str, value: str) -> None:
        etree.SubElement(el, f"{{{mes}}}{tag}").text = value

    if header is None:
        message_id, test, prepared, sender_id = PLACEHOLDER
        text("ID", message_id)
        text("Test", test)
        text("Prepared", prepared)
        etree.SubElement(el, f"{{{mes}}}Sender").set("id", sender_id)
        if structures is not None:
            structures(el)
        return
    text("ID", header.id)
    text("Test", "true" if header.test else "false")
    text("Prepared", header.prepared.isoformat())
    _write_party(el, "Sender", header.sender, com=com, mes=mes)
    for receiver in header.receivers:
        _write_party(el, "Receiver", receiver, com=com, mes=mes)
    _write_texts(el, f"{{{com}}}Name", header.name)
    if structures is not None:
        structures(el)
    if isinstance(header, MetadataHeader):
        _write_metadata_fields(el, header, mes=mes)
    _write_texts(el, f"{{{mes}}}Source", header.source)


def _write_metadata_fields(el: etree._Element, header: MetadataHeader, *, mes: str) -> None:
    """The four elements `GenericMetadataHeaderType` adds, in schema order, after ``Structure``."""

    def text(tag: str, value: str) -> None:
        etree.SubElement(el, f"{{{mes}}}{tag}").text = value

    if header.metadata_provider is not None:
        text("MetadataProvider", str(header.metadata_provider.urn))
    if header.data_set_action is not None:
        text("DataSetAction", header.data_set_action.value)
    for data_set_id in header.data_set_ids:
        text("DataSetID", data_set_id)
    if header.extracted is not None:
        text("Extracted", header.extracted.isoformat())


def _write_texts(parent: etree._Element, tag: str, texts: InternationalString) -> None:
    for lang, value in texts.items():
        child = etree.SubElement(parent, tag)
        child.set(XML_LANG, lang)
        child.text = value


def _write_party(parent: etree._Element, tag: str, party: Party, *, com: str, mes: str) -> None:
    el = etree.SubElement(parent, f"{{{mes}}}{tag}")
    el.set("id", party.id)
    _write_texts(el, f"{{{com}}}Name", party.name)
    for contact in party.contacts:
        contact_el = etree.SubElement(el, f"{{{mes}}}Contact")
        _write_texts(contact_el, f"{{{com}}}Name", contact.name)
        _write_texts(contact_el, f"{{{mes}}}Department", contact.department)
        _write_texts(contact_el, f"{{{mes}}}Role", contact.role)
        for tag_name, field_name in _CHANNELS:
            channel: tuple[str, ...] = getattr(contact, field_name)
            for value in channel:
                etree.SubElement(contact_el, f"{{{mes}}}{tag_name}").text = value
    if isinstance(party, Sender) and party.timezone is not None:
        etree.SubElement(el, f"{{{mes}}}Timezone").text = party.timezone


# ── Reading ────────────────────────────────────────────────────────────────


def read_header(root: etree._Element, *, com: str) -> tuple[Header | None, ReadGap | None]:
    """`root`'s ``<mes:Header>`` as a `Header`, and the loss when it cannot be built.

    None, and no loss, when it has none or holds only the placeholders. One
    the model cannot hold (a party with no id, a ``Prepared`` that is no
    date) is None and a loss: the artefacts are what a read is for, and the
    header used to be ignored, so it fails nothing, but it is not dropped
    silently either. The message namespace is `root`'s own.
    """
    el = _header_element(root)
    if el is None or _is_placeholder(el):
        return None, None
    return _built(lambda: Header(**_common(el, com=com)))


def read_metadata_header(root: etree._Element, *, com: str) -> tuple[MetadataHeader | None, ReadGap | None]:
    """`read_header` for a reference-metadata message, with its four more elements."""
    el = _header_element(root)
    if el is None or _is_placeholder(el):
        return None, None
    return _built(lambda: _metadata_header(root, el, com=com))


def _built[H: Header](build: Callable[[], H]) -> tuple[H | None, ReadGap | None]:
    try:
        return build(), None
    except (SdmxParseError, ValidationError, ValueError) as exc:
        return None, ReadGap(urn=MESSAGE, construct="mes:Header", count=1, reason=f"read as no header: {exc}")


def _metadata_header(root: etree._Element, el: etree._Element, *, com: str) -> MetadataHeader:
    mes = etree.QName(root).namespace
    provider = el.findtext(f"{{{mes}}}MetadataProvider")
    action = el.findtext(f"{{{mes}}}DataSetAction")
    extracted = el.findtext(f"{{{mes}}}Extracted")
    return MetadataHeader(
        **_common(el, com=com),
        metadata_provider=None
        if provider is None
        else Ref[MetadataProvider].unresolved(MetadataProvider.parse_urn(provider.strip())),
        data_set_action=None if action is None else ActionType(action.strip()),
        data_set_ids=tuple((each.text or "").strip() for each in el.findall(f"{{{mes}}}DataSetID")),
        extracted=None if extracted is None else to_datetime(extracted.strip(), what="Header/Extracted"),
    )


def _header_element(root: etree._Element) -> etree._Element | None:
    mes = etree.QName(root).namespace
    return root.find(f"{{{mes}}}Header")


def _is_placeholder(el: etree._Element) -> bool:
    """Whether `el` holds exactly what `write_header` writes for no header, and nothing else.

    A metadata header's ``Structure`` elements are left out: the writer derives
    them from the sets, header or none.
    """
    children = [child for child in el if etree.QName(child).localname != "Structure"]
    if [etree.QName(child).localname for child in children] != ["ID", "Test", "Prepared", "Sender"]:
        return False
    sender = children[3]
    said = ((children[0].text or "").strip(), (children[1].text or "").strip(), (children[2].text or "").strip())
    return (*said, sender.get("id")) == PLACEHOLDER and len(sender) == 0 and set(sender.attrib) == {"id"}


class _HeaderFields(TypedDict, closed=True):
    """What `StructureHeaderType` and `GenericMetadataHeaderType` share."""

    id: str
    test: bool
    prepared: datetime
    sender: Sender
    receivers: tuple[Party, ...]
    name: InternationalString
    source: InternationalString


def _common(el: etree._Element, *, com: str) -> _HeaderFields:
    mes = etree.QName(el).namespace or ""
    prepared = (el.findtext(f"{{{mes}}}Prepared") or "").strip()
    sender = el.find(f"{{{mes}}}Sender")
    return {
        "id": (el.findtext(f"{{{mes}}}ID") or "").strip(),
        "test": (el.findtext(f"{{{mes}}}Test") or "false").strip() in {"true", "1"},
        "prepared": to_datetime(prepared, what="Header/Prepared"),
        "sender": _read_sender(sender, com=com, mes=mes) if sender is not None else Sender(id=""),
        "receivers": tuple(_read_party(each, com=com, mes=mes) for each in el.findall(f"{{{mes}}}Receiver")),
        "name": _read_texts(el, f"{{{com}}}Name"),
        "source": _read_texts(el, f"{{{mes}}}Source"),
    }


def _read_texts(parent: etree._Element, tag: str) -> InternationalString:
    texts = {child.get(XML_LANG, "en"): child.text or "" for child in parent.findall(tag)}
    return InternationalString(**texts)


def _read_contacts(el: etree._Element, *, com: str, mes: str) -> tuple[Contact, ...]:
    def channel(contact_el: etree._Element, tag: str) -> tuple[str, ...]:
        return tuple(each.text for each in contact_el.findall(f"{{{mes}}}{tag}") if each.text)

    return tuple(
        Contact(
            name=_read_texts(contact_el, f"{{{com}}}Name"),
            department=_read_texts(contact_el, f"{{{mes}}}Department"),
            role=_read_texts(contact_el, f"{{{mes}}}Role"),
            phones=channel(contact_el, "Telephone"),
            faxes=channel(contact_el, "Fax"),
            x400s=channel(contact_el, "X400"),
            uris=channel(contact_el, "URI"),
            emails=channel(contact_el, "Email"),
        )
        for contact_el in el.findall(f"{{{mes}}}Contact")
    )


def _read_party(el: etree._Element, *, com: str, mes: str) -> Party:
    return Party(
        id=el.get("id", ""),
        name=_read_texts(el, f"{{{com}}}Name"),
        contacts=_read_contacts(el, com=com, mes=mes),
    )


def _read_sender(el: etree._Element, *, com: str, mes: str) -> Sender:
    timezone = el.findtext(f"{{{mes}}}Timezone")
    return Sender(
        id=el.get("id", ""),
        name=_read_texts(el, f"{{{com}}}Name"),
        contacts=_read_contacts(el, com=com, mes=mes),
        timezone=None if timezone is None else timezone.strip(),
    )
