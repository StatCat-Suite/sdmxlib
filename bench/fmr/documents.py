"""The documents the FMR oracle sends, built from the fixtures by our own writers (#457).

Two per version: the maximal fixture, written by the version's writer (which
drops what the version cannot hold, as the element censuses do), and the
version's shape. SDMX-ML 3.1 is not among them: FMR 12.4.2 reads no 3.1, and
the recorder records that instead.
"""

import re
from collections.abc import Callable, Iterable

from lxml import etree

from bench.fmr.ledger import ADJUSTMENTS, Adjustment, Document, Version
from sdmxlib.formats.sdmx_ml21 import writer as ml21_writer
from sdmxlib.formats.sdmx_ml30 import writer as ml30_writer
from sdmxlib.model.message import StructureMessage
from tests.support import maximal, shapes

VERSIONS: tuple[Version, ...] = ("2.1", "3.0")
DOCUMENTS: tuple[Document, ...] = ("maximal", "shape")

_WRITERS: dict[Version, Callable[[StructureMessage], bytes]] = {"2.1": ml21_writer.write, "3.0": ml30_writer.write}
_FIXTURES: dict[tuple[Version, Document], Callable[[], StructureMessage]] = {
    ("2.1", "maximal"): maximal.message,
    ("3.0", "maximal"): maximal.message,
    ("2.1", "shape"): shapes.message_21,
    ("3.0", "shape"): shapes.message,
}


def written(version: Version, document: Document) -> bytes:
    """The document as our writer emits it, before any adjustment."""
    return _WRITERS[version](_FIXTURES[version, document]())


def adjustments(version: Version, document: Document) -> tuple[Adjustment, ...]:
    """The ledger's adjustments to one document, in ledger order."""
    return tuple(a for a in ADJUSTMENTS if (a.version, a.document) == (version, document))


def apply(root: etree._Element, xpath: str) -> int:
    """Remove the elements and attributes `xpath` names from `root`; how many it matched."""
    hits = root.xpath(xpath)
    assert isinstance(hits, list)
    for hit in hits:
        if isinstance(hit, etree._ElementUnicodeResult) and hit.is_attribute:
            owner, name = hit.getparent(), hit.attrname
            assert isinstance(owner, etree._Element)
            assert name is not None
            del owner.attrib[name]
        elif isinstance(hit, etree._Element):
            parent = hit.getparent()
            assert parent is not None
            parent.remove(hit)
    return len(hits)


def adjusted(version: Version, document: Document, *, without: Adjustment | None = None) -> bytes:
    """The document FMR is sent: written, then every adjustment but `without` applied."""
    root = etree.fromstring(written(version, document))
    for adjustment in adjustments(version, document):
        if adjustment is not without:
            apply(root, adjustment.xpath)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")


_ML30 = "http://www.sdmx.org/resources/sdmxml/schemas/v3_0"


def setup(documents: Iterable[bytes]) -> bytes:
    """What FMR must hold before the documents resolve: their maintainers, and the role concepts they name.

    FMR resolves every maintainer against ``SDMX:AGENCIES`` and a concept role
    against the concept it names, which the model writes into
    ``SDMX:CROSS_DOMAIN_CONCEPTS``. Neither is in a fresh FMR, so both are
    derived from the documents and loaded first, the SDMX agency kept.
    """
    agencies: set[str] = set()
    roles: set[str] = set()
    for document in documents:
        root = etree.fromstring(document)
        agencies |= {str(a) for a in root.xpath("//@agencyID") if str(a) != "SDMX"}
        text = document.decode()
        roles |= set(re.findall(r"CROSS_DOMAIN_CONCEPTS\(1\.0\)\.([\w-]+)", text))
        roles |= set(re.findall(r'maintainableParentID="CROSS_DOMAIN_CONCEPTS"[^>]*\bid="([\w-]+)"', text))
    mes, str_, com = f"{{{_ML30}/message}}", f"{{{_ML30}/structure}}", f"{{{_ML30}/common}}"
    lang = "{http://www.w3.org/XML/1998/namespace}lang"
    root = etree.Element(
        f"{mes}Structure", nsmap={"mes": f"{_ML30}/message", "str": f"{_ML30}/structure", "com": f"{_ML30}/common"}
    )
    header = etree.SubElement(root, f"{mes}Header")
    for name, text in (("ID", "FMR_ORACLE_SETUP"), ("Test", "true"), ("Prepared", "2026-01-01T00:00:00")):
        etree.SubElement(header, f"{mes}{name}").text = text
    etree.SubElement(header, f"{mes}Sender", id="SDMXLIB")
    structures = etree.SubElement(root, f"{mes}Structures")

    def scheme(container: str, tag: str, item: str, scheme_id: str, ids: Iterable[str], name: str) -> None:
        parent = etree.SubElement(
            etree.SubElement(structures, f"{str_}{container}"),
            f"{str_}{tag}",
            agencyID="SDMX",
            id=scheme_id,
            version="1.0",
        )
        etree.SubElement(parent, f"{com}Name", {lang: "en"}).text = name
        for ident in sorted(ids):
            child = etree.SubElement(parent, f"{str_}{item}", id=ident)
            etree.SubElement(child, f"{com}Name", {lang: "en"}).text = ident

    scheme("AgencySchemes", "AgencyScheme", "Agency", "AGENCIES", {"SDMX"} | agencies, "SDMX Agency Scheme")
    scheme(
        "ConceptSchemes",
        "ConceptScheme",
        "Concept",
        "CROSS_DOMAIN_CONCEPTS",
        roles,
        "The role concepts the sdmxlib fixtures name",
    )
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")
