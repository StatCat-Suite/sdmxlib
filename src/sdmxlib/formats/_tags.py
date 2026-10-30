"""Language tags as the readers meet them (GH-280).

`normalize_tag` raises `ValueError`, which no reader catches: `build_each`
refuses an artefact for a `SdmxConformanceError` and lets anything else
through as a defect. So the readers normalise every tag here, and a tag that
is not well-formed, or two texts of one tag, contradict the document as any
other schema violation does and refuse the artefact that carries them.

SDMX-ML's ``xml:lang`` is an ``xs:language``, a token type, so a schema
validator collapses its whitespace before checking it: ``xml:lang=" en "`` is
``en``. `xml_tag` does the same. An absent ``xml:lang`` is English, as every
SDMX-ML schema declares (`SCHEMA_DEFAULT_LANG`).

SDMX-JSON allows a lone ``name`` with no ``names`` map, which states no
language. When the message's ``meta.contentLanguages`` lists exactly one, every
text in the message is in it, and `current_message_language` gives it; otherwise the
reader refuses the text (`formats.sdmx_json._dto.ingest_istr`).
"""

from collections.abc import Generator, Iterable
from contextlib import contextmanager
from contextvars import ContextVar

from lxml import etree

from sdmxlib.model.istring import InternationalString, normalize_tag
from sdmxlib.model.validation import SdmxConformanceError

XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"

#: The language of a `TextType` or `XHTMLType` element with no ``xml:lang``.
#: Not a library default: every SDMX-ML schema declares the attribute
#: ``default="en"``, so the element *is* English. The readers dropped it
#: instead, and an artefact named only that way lost its name unreported (#422).
SCHEMA_DEFAULT_LANG = "en"

#: The characters XML Schema's whitespace collapse removes.
_XML_SPACE = " \t\r\n"


def tag(raw: str, where: str) -> str:
    """`raw` normalised, or a refusal naming `where`.

    Raises:
        SdmxConformanceError: If `raw` is not a well-formed language tag.
    """
    try:
        return normalize_tag(raw)
    except ValueError as exc:
        raise SdmxConformanceError(where, str(exc)) from None


def xml_tag(el: etree._Element, where: str) -> str:
    """`el`'s ``xml:lang``, collapsed and normalised; English when absent.

    Raises:
        SdmxConformanceError: If the attribute is present and empty, or not a
            well-formed tag.
    """
    raw = el.get(XML_LANG)
    return SCHEMA_DEFAULT_LANG if raw is None else tag(raw.strip(_XML_SPACE), where)


def optional_xml_tag(el: etree._Element, where: str) -> str | None:
    """`el`'s ``xml:lang``, collapsed and normalised; None when absent.

    For an annotation URL and a constraint's value, whose language is
    optional and has no schema default.

    Raises:
        SdmxConformanceError: If the attribute is present and empty, or not a
            well-formed tag.
    """
    raw = el.get(XML_LANG)
    return None if raw is None else tag(raw.strip(_XML_SPACE), where)


def texts(pairs: Iterable[tuple[str, str]], where: str) -> InternationalString:
    """An `InternationalString` from `(tag, text)` pairs, each tag normalised.

    Raises:
        SdmxConformanceError: If a tag is not well-formed, or two normalise to
            one tag: the document gives two texts for one language, and sdmxlib
            never chooses between them.
    """
    data: dict[str, str] = {}
    for raw, text in pairs:
        lang = tag(raw, where)
        if lang in data:
            raise SdmxConformanceError(where, f"two texts for language {lang!r}")
        data[lang] = text
    return InternationalString._from_normalized(data)


def xml_texts(elements: Iterable[etree._Element], where: str) -> InternationalString:
    """An `InternationalString` from SDMX-ML text elements, as `texts` builds one.

    An empty element is read as the empty text it is (#452).

    Raises:
        SdmxConformanceError: As `texts`, and for an empty ``xml:lang``.
    """
    data: dict[str, str] = {}
    for el in elements:
        lang = xml_tag(el, where)
        if lang in data:
            raise SdmxConformanceError(where, f"two texts for language {lang!r}")
        data[lang] = el.text or ""
    return InternationalString._from_normalized(data)


#: The one language a SDMX-JSON message declares in ``meta.contentLanguages``,
#: while it is read. A context variable, as `_common.hierarchy_containers`'s
#: is: the readers are shared, and reads may overlap.
_MESSAGE_LANGUAGE: ContextVar[str | None] = ContextVar("sdmxlib_message_language", default=None)


@contextmanager
def message_language(content_languages: list[str] | None) -> Generator[None]:
    """Make the message's one declared language, if it declares one, `current_message_language` inside.

    Raises:
        SdmxConformanceError: If an entry of ``contentLanguages`` is not a
            well-formed tag. Raised outside any artefact, so it fails the read.
    """
    langs = {tag(each, "contentLanguages") for each in content_languages or ()}
    token = _MESSAGE_LANGUAGE.set(next(iter(langs)) if len(langs) == 1 else None)
    try:
        yield
    finally:
        _MESSAGE_LANGUAGE.reset(token)


def current_message_language() -> str | None:
    """The language every text of the SDMX-JSON message being read is in, when it declares exactly one."""
    return _MESSAGE_LANGUAGE.get()
