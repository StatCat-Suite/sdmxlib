"""Text with no ``xml:lang`` is English, because every SDMX-ML schema says so (#422).

`TextType` declares ``<xs:attribute ref="xml:lang" default="en"/>`` in 2.1, 3.0
and 3.1, so ``<com:Name>Foo</com:Name>`` is a valid English name. The readers
kept only text that spelled its language, and dropped the rest unreported —
an artefact named that way lost its name.

This is the schema's default, applied where the document is parsed. It is not
a library default language: every API that takes `lang` still requires one.
"""

from collections.abc import Callable

import pytest
from lxml import etree

from sdmxlib.formats.sdmx_ml._common import XML_LANG
from sdmxlib.formats.sdmx_ml21 import reader as ml21_reader
from sdmxlib.formats.sdmx_ml21 import writer as ml21_writer
from sdmxlib.formats.sdmx_ml30 import metadata as ml30_metadata
from sdmxlib.formats.sdmx_ml30 import reader as ml30_reader
from sdmxlib.formats.sdmx_ml30 import writer as ml30_writer
from sdmxlib.formats.sdmx_ml31 import reader as ml31_reader
from sdmxlib.formats.sdmx_ml31 import writer as ml31_writer
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.message import MetadataMessage, StructureMessage
from sdmxlib.model.metadataset import MetadataSet, ReportedAttribute
from sdmxlib.model.metadatastructure import MetadataStructure
from tests.support import maximal
from tests.support.factories import footnote_metadataflow
from tests.support.field_census import differences
from tests.support.xsd import SchemaVersion, assert_valid_sdmx_ml, compile_structure_schema

pytestmark = pytest.mark.unit

_FORMATS: dict[SchemaVersion, tuple[Callable[[StructureMessage], bytes], Callable[[bytes], StructureMessage]]] = {
    "2.1": (ml21_writer.write, ml21_reader.read),
    "3.0": (ml30_writer.write, ml30_reader.read),
    "3.1": (ml31_writer.write, ml31_reader.read),
}

#: The `TextType` elements the maximal message writes. An `AnnotationURL`'s
#: language is optional with no default — absent means "not localised" — and a
#: cube region value's likewise, so neither is here.
_TEXT_TYPES = frozenset({"Name", "Description", "AnnotationText", "Department", "Role"})


@pytest.mark.parametrize("version", sorted(_FORMATS))
def test_text_without_a_language_is_read_as_english(version: SchemaVersion) -> None:
    write, read = _FORMATS[version]
    written = write(maximal.message())
    doc = etree.fromstring(written)
    unmarked = 0
    for el in doc.iter():
        if isinstance(el.tag, str) and etree.QName(el).localname in _TEXT_TYPES and el.get(XML_LANG) == "en":
            del el.attrib[XML_LANG]
            unmarked += 1
    seen = {etree.QName(el).localname for el in doc.iter() if isinstance(el.tag, str)}
    assert seen >= _TEXT_TYPES, f"the maximal message writes no {sorted(_TEXT_TYPES - seen)}: the test reaches nothing"
    assert unmarked
    unmarked_doc = etree.tostring(doc)
    assert_valid_sdmx_ml(unmarked_doc, compile_structure_schema(version))
    back = read(unmarked_doc)
    read_back = {str(artefact.urn): artefact for artefact in back.all_artefacts()}
    for original in read(written).all_artefacts():
        found = list(differences(original, read_back[str(original.urn)]))
        assert not found, f"{original.urn} lost {found}"


def test_a_reported_value_without_a_language_is_read_as_english() -> None:
    """The 3.0 metadata reader, whose `com:Text` and name are the same `TextType`."""
    flow = footnote_metadataflow(MetadataStructure(name={"en": "MSD"}, id="MSD", maintainer="AG"), name=None)
    metadata_set = MetadataSet(
        id="SET",
        maintainer="AG",
        structure=flow,
        name={"en": "set"},
        targets=[flow.urn],
        attributes=[ReportedAttribute(id="NOTE", value={"en": "a note"})],
    )
    doc = etree.fromstring(ml30_metadata.write(MetadataMessage(metadata_sets=[metadata_set])))
    for el in doc.iter():
        if isinstance(el.tag, str):
            el.attrib.pop(XML_LANG, None)
    (back,) = ml30_metadata.read(etree.tostring(doc)).metadata_sets
    assert back.name == InternationalString(en="set")
    assert back.attributes["NOTE"].value == InternationalString(en="a note")
