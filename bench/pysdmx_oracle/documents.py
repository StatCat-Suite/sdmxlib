"""The SDMX-ML 3.1 documents the pysdmx oracle reads, built from the fixtures by our own writer (#457).

Two: the maximal fixture, written by the 3.1 writer (which drops what 3.1
cannot hold, as the element censuses do), and the 3.1 shape.
"""

from collections.abc import Callable

from lxml import etree

from bench.fmr.documents import apply
from bench.pysdmx_oracle.ledger import ADJUSTMENTS, Adjustment, Document
from sdmxlib.formats.sdmx_ml31 import writer as ml31_writer
from sdmxlib.model.message import StructureMessage
from tests.support import maximal, shapes

DOCUMENTS: tuple[Document, ...] = ("maximal", "shape")

_FIXTURES: dict[Document, Callable[[], StructureMessage]] = {
    "maximal": maximal.message,
    "shape": shapes.message_31,
}


def written(document: Document) -> bytes:
    """The document as our writer emits it, before any adjustment."""
    return ml31_writer.write(_FIXTURES[document]())


def adjustments(document: Document) -> tuple[Adjustment, ...]:
    """The ledger's adjustments to one document, in ledger order."""
    return tuple(a for a in ADJUSTMENTS if a.document == document)


def adjusted(document: Document, *, without: Adjustment | None = None) -> bytes:
    """The document pysdmx is given: written, then every adjustment but `without` applied."""
    root = etree.fromstring(written(document))
    for adjustment in adjustments(document):
        if adjustment is not without:
            assert apply(root, adjustment.xpath), adjustment.xpath
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")
