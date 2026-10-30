"""SDMX-ML 3.0 GenericMetadata reader and writer.

Handles ``<mes:GenericMetadata>`` envelopes carrying populated MetadataSets.
This is a separate message type from the Structure envelope (handled by
sibling reader.py / writer.py).

Per the official SDMX 3.0 schemas (all reference types are *simple URN
strings*, not ``<Ref>`` containers — a key difference from 2.1):

- The MSD URN binding lives in the message ``<mes:Header>`` as a
  ``<mes:Structure structureID="…">`` block (``GenericMetadataStructureType``
  — only ``structureID``, no data-message ``namespace`` /
  ``dimensionAtObservation``) whose ``<com:Structure>`` child carries the MSD
  URN as text.
- The ``MetadataSet`` element is in the *message* namespace
  (``<mes:MetadataSet>``); its children are in the metadata-generic namespace.
  It carries one ``<md:Metadataflow>URN</md:Metadataflow>`` or
  ``<md:MetadataProvisionAgreement>URN</md:MetadataProvisionAgreement>`` (the
  two SDMX 3.0 structure-ref options), one or more
  ``<md:Target>URN</md:Target>`` elements, and one or more
  ``<md:Attribute id="…">`` elements.
- ``<md:Attribute>`` is recursive (children are nested ``<md:Attribute>``
  elements); the value is one of:
  - ``<md:Value>scalar</md:Value>`` (text content; coded ids look the same as
    other scalars on the wire — the MSD declares the type)
  - ``<com:Text xml:lang="…">`` siblings for multilingual free text
  - ``<com:StructuredText xml:lang="…">`` for XHTML
- Repetition of an attribute is via sibling ``<md:Attribute>`` elements
  with the same id, not a values list on a single attribute.
"""

from datetime import datetime
from typing import cast

from lxml import etree

from sdmxlib.formats._coerce import to_datetime
from sdmxlib.formats._tags import texts, xml_tag, xml_texts
from sdmxlib.formats.sdmx_ml._common import flag
from sdmxlib.formats.sdmx_ml._header import read_metadata_header, write_header
from sdmxlib.model.collections import ArtefactList, ItemList
from sdmxlib.model.errors import SdmxParseError
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.link import Link
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

from .namespaces import COM, MD, MES, XML_LANG

_NSMAP: dict[str, str] = {"mes": MES, "md": MD, "com": COM}


# ── Tag helpers ───────────────────────────────────────────────────────────


def _m(tag: str) -> str:
    return f"{{{MES}}}{tag}"


def _md(tag: str) -> str:
    return f"{{{MD}}}{tag}"


def _c(tag: str) -> str:
    return f"{{{COM}}}{tag}"


# ── Writer ────────────────────────────────────────────────────────────────


def _write_istring_children(
    parent: etree._Element,
    s: InternationalString,
    *,
    tag: str,
    namespace: str,
) -> None:
    for lang, text in s._backing().items():
        el = etree.SubElement(parent, f"{{{namespace}}}{tag}")
        el.set(XML_LANG, lang)
        el.text = text


def _write_attribute_value(parent: etree._Element, value: ReportedAttributeValue | None) -> None:
    if value is None:
        return
    if isinstance(value, InternationalString):
        # Multilingual: one <com:Text> per locale.
        _write_istring_children(parent, value, tag="Text", namespace=COM)
    else:
        v_el = etree.SubElement(parent, _md("Value"))
        v_el.text = str(value)


def _write_attribute(parent: etree._Element, attr: ReportedAttribute) -> None:
    # No `uri` here: `AttributeType` extends `AnnotableType`, not
    # `IdentifiableType`, so a reported attribute has no such attribute to
    # write. The model field exists because every other identifiable does
    # carry one — see the recorded gap in `KNOWN_GAPS` (#238).
    el = etree.SubElement(parent, _md("Attribute"))
    el.set("id", attr.id)
    _write_attribute_value(el, attr.value)
    for child in attr.children:
        _write_attribute(el, child)


def _write_maintainable_attrs(el: etree._Element, ms: MetadataSet) -> None:
    """A metadata set's `MaintainableType` attributes, each only where it is set."""
    el.set("agencyID", ms.agency_id)
    el.set("id", ms.id)
    el.set("version", ms.version)
    optional = {
        "uri": ms.uri,
        "validFrom": ms.valid_from.isoformat() if ms.valid_from is not None else None,
        "validTo": ms.valid_to.isoformat() if ms.valid_to is not None else None,
        "isExternalReference": "true" if ms.is_external_reference else None,
        "serviceURL": ms.service_url,
        "structureURL": ms.structure_url,
    }
    for attribute, value in optional.items():
        if value:
            el.set(attribute, value)


def _write_metadata_set(parent: etree._Element, ms: MetadataSet) -> None:
    # The MetadataSet *element* is in the message namespace (declared on
    # GenericMetadataType); its children are in the metadata-generic namespace.
    el = etree.SubElement(parent, _m("MetadataSet"))
    _write_maintainable_attrs(el, ms)
    # `MetadataSetBaseType`'s links come before its name (#422).
    for link in ms.links:
        link_el = etree.SubElement(el, f"{{{COM}}}Link")
        link_el.set("rel", link.rel)
        link_el.set("url", link.url)
        if link.urn is not None:
            link_el.set("urn", link.urn)
        if link.type is not None:
            link_el.set("type", link.type)

    if ms.name:
        _write_istring_children(el, ms.name, tag="Name", namespace=COM)
    if ms.description:
        _write_istring_children(el, ms.description, tag="Description", namespace=COM)

    # Structure binding: <md:Metadataflow> OR <md:MetadataProvisionAgreement> —
    # the two structure-ref options the SDMX 3.0 model permits. The 3.0 reference
    # types are simple URN strings (not <Ref> containers).
    is_mpa = ms.structure.urn.artefact_class == "MetadataProvisionAgreement"
    structure_tag = "MetadataProvisionAgreement" if is_mpa else "Metadataflow"
    etree.SubElement(el, _md(structure_tag)).text = str(ms.structure.urn)

    for target in ms.targets:
        target_el = etree.SubElement(el, _md("Target"))
        target_el.text = str(target.urn)

    for attr in ms.attributes:
        _write_attribute(el, attr)


#: Artefact class of a header URN -> the ``<com:…>`` element that may carry it.
#: ``GenericMetadataStructureType`` is a *choice* of three, each restricted to
#: one artefact kind by a pattern facet, so the element name is not free: a
#: metadataflow URN inside ``<com:Structure>`` fails validation because that
#: slot is typed ``MetadataStructureReferenceType`` (#234).
_HEADER_ELEMENT = {
    "MetadataStructure": "Structure",
    "Metadataflow": "StructureUsage",
    "ProvisionAgreement": "ProvisionAgreement",
}


def _resolve_msd_urn(structure: Ref[Metadataflow] | Ref[MetadataProvisionAgreement]) -> SdmxUrn[object]:
    """Best-effort resolution of a MetadataSet structure ref to its MSD URN.

    The 3.0 message header prefers the MSD; a set references a Metadataflow or
    a MetadataProvisionAgreement. Walk the in-memory graph when resolved
    (MPA → Metadataflow → MSD), else fall back to the ref's own URN — which the
    caller must then place in the element matching *that* URN's class, not in
    the MSD slot.
    """
    urn = structure.urn
    cls = urn.artefact_class
    if cls == "Metadataflow" and structure.resolved:
        mdf = cast("Metadataflow", structure())
        if mdf.structure is not None:
            return mdf.structure.urn
    elif cls == "MetadataProvisionAgreement" and structure.resolved:
        mpa = cast("MetadataProvisionAgreement", structure())
        if mpa.metadataflow is not None and mpa.metadataflow.resolved:
            mdf = mpa.metadataflow()
            if mdf.structure is not None:
                return mdf.structure.urn
    return urn


def _write_header(root: etree._Element, msg: MetadataMessage) -> None:
    write_header(root, msg.header, com=COM, mes=MES, structures=lambda header: _write_structures(header, msg))


def _write_structures(header: etree._Element, msg: MetadataMessage) -> None:
    """The header's ``<mes:Structure>`` elements, which name the structure each set's ``@structureRef`` means.

    One <mes:Structure> per distinct MSD referenced in the message. The
    header wants the MSD URN; the set's structure ref points at a Metadataflow
    or a MetadataProvisionAgreement, so resolve through to the MSD when the
    in-memory graph allows (MPA → Metadataflow → MSD), else fall back to the
    ref's own URN.
    """
    seen: dict[str, SdmxUrn[object]] = {}
    for ms in msg.metadata_sets:
        seen_urn = _resolve_msd_urn(ms.structure)
        seen.setdefault(str(seen_urn), seen_urn)

    for idx, urn in enumerate(seen.values(), start=1):
        # GenericMetadataStructureType: only ``structureID`` is allowed here (no
        # data-message ``namespace`` / ``dimensionAtObservation``). Its child is
        # a *choice* of three URN-string refs, one per artefact kind, so the
        # element is picked from the URN's class rather than fixed. Writing an
        # unresolved metadataflow into ``<com:Structure>`` emitted a document
        # every 3.0 consumer rejects (#234); `check_metadata_serialisable`
        # refuses the kinds with no header slot at all.
        struct_el = etree.SubElement(header, _m("Structure"))
        struct_el.set("structureID", f"MDS{idx}")
        child = _HEADER_ELEMENT.get(urn.artefact_class, "Structure")
        etree.SubElement(struct_el, _c(child)).text = str(urn)


def write(msg: MetadataMessage, *, pretty_print: bool = False) -> bytes:
    """Serialise a MetadataMessage to SDMX-ML 3.0 GenericMetadata bytes.

    Args:
        msg: The MetadataMessage to serialise.
        pretty_print: If True, indent the XML output.

    Returns:
        UTF-8 encoded XML bytes of a ``<mes:GenericMetadata>`` document.
    """
    root = etree.Element(_m("GenericMetadata"), nsmap=_NSMAP)
    _write_header(root, msg)
    for ms in msg.metadata_sets:
        _write_metadata_set(root, ms)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=pretty_print)


# ── Reader ────────────────────────────────────────────────────────────────


def _read_istring(parent: etree._Element, *, tag: str, namespace: str = COM) -> InternationalString:
    return xml_texts((child for child in parent.findall(f"{{{namespace}}}{tag}") if child.text), tag)


def _read_attribute_value(el: etree._Element) -> ReportedAttributeValue | None:
    # Multilingual <com:Text> children take precedence.
    text_children = el.findall(_c("Text"))
    if text_children:
        return xml_texts((child for child in text_children if child.text), "Text")
    # XHTML <com:StructuredText> — collected as InternationalString too;
    # the inner XHTML markup is preserved as the text content of each locale.
    structured_children = el.findall(_c("StructuredText"))
    if structured_children:
        langs: list[tuple[str, str]] = []
        for child in structured_children:
            lang = xml_tag(child, "StructuredText")
            # Reconstitute inner content (may include XHTML markup).
            inner = etree.tostring(child, encoding="unicode", with_tail=False)
            # Strip the outer <StructuredText> wrapper to keep just the markup.
            opening_close = inner.find(">")
            closing_open = inner.rfind("<")
            content = inner[opening_close + 1 : closing_open] if opening_close != -1 and closing_open != -1 else ""
            langs.append((lang, content))
        return texts(langs, "StructuredText")
    # Scalar <md:Value>.
    v_el = el.find(_md("Value"))
    if v_el is not None and v_el.text is not None:
        return v_el.text
    return None


def _read_attribute(el: etree._Element) -> ReportedAttribute:
    children = [_read_attribute(c) for c in el.findall(_md("Attribute"))]
    return ReportedAttribute(
        id=el.get("id", ""),
        value=_read_attribute_value(el),
        children=ItemList(children),
    )


def _read_metadata_set(el: etree._Element) -> MetadataSet:
    # Either <md:Metadataflow> or <md:MetadataProvisionAgreement> child (the two
    # conformant SDMX 3.0 structure-ref options).
    # 3.0 reference types are simple URN strings (element text), not <Ref>.
    structure_urn: SdmxUrn[object] | None = None
    for tag in ("Metadataflow", "MetadataProvisionAgreement"):
        struct_el = el.find(_md(tag))
        if struct_el is not None and struct_el.text and struct_el.text.strip():
            structure_urn = SdmxUrn.parse(struct_el.text.strip())
            break
    if structure_urn is None:
        msg = "MetadataSet missing Metadataflow or MetadataProvisionAgreement ref"
        raise SdmxParseError(msg)

    structure_ref: Ref[Metadataflow] | Ref[MetadataProvisionAgreement] = (
        Ref[MetadataProvisionAgreement].unresolved(cast("SdmxUrn[MetadataProvisionAgreement]", structure_urn))
        if structure_urn.artefact_class == "MetadataProvisionAgreement"
        else Ref[Metadataflow].unresolved(cast("SdmxUrn[Metadataflow]", structure_urn))
    )

    targets: list[Ref[object]] = [
        Ref[object].unresolved(SdmxUrn.parse(target_el.text.strip()))
        for target_el in el.findall(_md("Target"))
        if target_el.text
    ]

    attributes = [_read_attribute(a) for a in el.findall(_md("Attribute"))]

    def _dt(attr: str) -> datetime | None:
        val = el.get(attr)
        return to_datetime(val, what=f"@{attr}") if val else None

    return MetadataSet(
        id=el.get("id", ""),
        maintainer=Agency(id=el.get("agencyID", "")),
        structure=structure_ref,
        version=el.get("version", "1.0"),
        name=_read_istring(el, tag="Name"),
        description=_read_istring(el, tag="Description"),
        # Writer emits validFrom/validTo; parse them back so they round-trip (#159).
        valid_from=_dt("validFrom"),
        valid_to=_dt("validTo"),
        # Written all along and never read back (#422).
        uri=el.get("uri"),
        links=tuple(
            Link(
                rel=link_el.get("rel", ""), url=link_el.get("url", ""), urn=link_el.get("urn"), type=link_el.get("type")
            )
            for link_el in el.findall(f"{{{COM}}}Link")
        ),
        is_external_reference=flag(el, "isExternalReference"),
        service_url=el.get("serviceURL"),
        structure_url=el.get("structureURL"),
        targets=targets,
        attributes=ItemList(attributes),
    )


def read(data: bytes) -> MetadataMessage:
    """Parse SDMX-ML 3.0 ``<mes:GenericMetadata>`` bytes into a MetadataMessage."""
    root = etree.fromstring(data)
    sets = [_read_metadata_set(el) for el in root.findall(_m("MetadataSet"))]
    header, lost = read_metadata_header(root, com=COM)
    return MetadataMessage(metadata_sets=ArtefactList(sets), header=header, lost=() if lost is None else (lost,))
