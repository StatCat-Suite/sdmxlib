"""No XML-incompatible character may enter the domain model (#221).

SDMX string types *are* XSD types, so a value XML cannot represent has no valid
SDMX-ML form in any version. JSON carries such characters happily, which is how
the two readers came to disagree: SDMX-ML input was rejected by the parser while
the same content arrived intact through SDMX-JSON, producing an artefact that
could never be written back to XML — signalled only by a raw ``ValueError`` out
of lxml, with no field path.

The properties here are stated over the *whole* forbidden range rather than a
handful of sentinel characters, because the interesting failures were never the
obvious ones. `test_forbidden_set_matches_lxml` is the anchor: it derives the
ban set from what lxml actually accepts, so our constant and the XML spec
cannot drift apart. It also caught a real mistake while this was written —
DEL (U+007F) and NEL (U+0085) are *legal* in XML, and a "not printable" rule
would have rejected valid documents.
"""

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st
from lxml import etree

from sdmxlib.formats import parse
from sdmxlib.formats.sdmx_json import writer as json_writer
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.collections import ItemList
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.validation import (
    _XML_FORBIDDEN,
    SdmxConformanceError,
    ValidationError,
    _first_xml_incompatible,
)
from tests.support.strategies import names
from tests.support.unchecked import unchecked

pytestmark = pytest.mark.unit

#: Every character XML forbids, as a Hypothesis strategy.
forbidden_chars = st.sampled_from(sorted(_XML_FORBIDDEN))

#: The subset that can actually appear in a wire document. Lone surrogates are
#: not encodable as UTF-8, so no SDMX-JSON payload can carry one and no reader
#: can ever see one — they reach the model only by direct construction, which
#: makes `validate` their sole boundary. Discovered by these very properties:
#: `json.dumps` and the SDMX-JSON writer both raise UnicodeEncodeError on them.
wire_forbidden_chars = st.sampled_from(sorted(c for c in _XML_FORBIDDEN if not 0xD800 <= ord(c) <= 0xDFFF))

#: Characters below U+0100 that XML permits — the boundary cases live here.
allowed_low_chars = st.sampled_from([chr(c) for c in range(0x100) if chr(c) not in _XML_FORBIDDEN])


def _codelist_named(text: str) -> Codelist:
    return Codelist(
        id="CL",
        maintainer="TEST",
        version="1.0",
        name=InternationalString(en=text),
        codes=ItemList([Code(id="X", name=InternationalString(en="x"))]),
    )


# ── The anchor: our constant must match lxml, not our memory of the spec ─────


@pytest.mark.parametrize("codepoint", [*range(0x100), 0xD800, 0xDFFF, 0xFFFE, 0xFFFF, 0x10000])
def test_forbidden_set_matches_lxml(codepoint: int):
    """`_XML_FORBIDDEN` agrees with what lxml will actually serialise.

    Derived from behaviour rather than transcribed from the XML spec, so the
    two cannot drift. Rejecting more than XML does would refuse valid SDMX.
    """
    char = chr(codepoint)
    element = etree.Element("a")
    try:
        element.text = char
        etree.tostring(element)
        lxml_accepts = True
    except ValueError:
        lxml_accepts = False
    assert (char not in _XML_FORBIDDEN) == lxml_accepts, f"U+{codepoint:04X} disagreement"


# ── validate() rejects them, wherever they appear ────────────────────────────


@given(bad=forbidden_chars, prefix=st.text(alphabet="abc", max_size=4), suffix=st.text(alphabet="abc", max_size=4))
def test_validate_rejects_any_forbidden_character_in_a_name(bad: str, prefix: str, suffix: str):
    """Any forbidden character anywhere in a name is a validation error."""
    with pytest.raises(ValidationError) as exc:
        _codelist_named(f"{prefix}{bad}{suffix}")
    assert f"U+{ord(bad):04X}" in str(exc.value)


@given(text=names())
def test_validate_accepts_every_xml_safe_name(text: InternationalString):
    """The converse: no name the strategies generate may be rejected.

    Guards against the check overshooting — the failure mode that would make
    valid SDMX unloadable.
    """
    _codelist_named(next(iter(text.as_dict().values())))


@given(char=allowed_low_chars)
def test_validate_accepts_xml_legal_control_adjacent_characters(char: str):
    """Tab, LF, CR, DEL and NEL are legal in XML and must stay accepted."""
    _codelist_named(f"a{char}b")


# ── The read boundary rejects them, so the two readers agree ─────────────────


@given(bad=wire_forbidden_chars)
def test_json_reader_rejects_forbidden_characters(bad: str):
    """SDMX-JSON must not admit what SDMX-ML cannot carry.

    The parity that was missing: lxml refuses these on parse, so before this
    the same logical message was loadable as JSON and not as XML.
    """
    blob = json.dumps(
        {
            "data": {
                "codelists": [
                    {
                        "id": "CL",
                        "agencyID": "TEST",
                        "version": "1.0",
                        "names": {"en": f"bad{bad}name"},
                        "codes": [{"id": "X", "names": {"en": "x"}}],
                    }
                ]
            }
        }
    ).encode()
    # `parse`, not the module reader: a reader refuses the one codelist and
    # reads on, and it is `parse` that fails the call over it (#435).
    with pytest.raises(SdmxConformanceError):
        parse(blob)


@given(bad=wire_forbidden_chars)
def test_such_an_artefact_cannot_be_written_as_json_and_read_back(bad: str):
    """The round-trip that used to succeed and produce un-serialisable data.

    Writing still works — the writer is not the boundary — but reading the
    result back now fails loudly instead of yielding an artefact with no valid
    SDMX-ML form. Such a codelist is refused as its name is set (#492), so it is
    built unchecked: this is about what the reader does with what got past.
    """
    with unchecked():
        codelist = _codelist_named(f"bad{bad}name")
    written = json_writer.write(StructureMessage(codelists=[codelist]))
    with pytest.raises(SdmxConformanceError):
        parse(written)


# ── The helper itself ────────────────────────────────────────────────────────


@given(bad=forbidden_chars, text=st.text(alphabet="abc", max_size=6))
def test_first_xml_incompatible_reports_the_first_offender(bad: str, text: str):
    """The helper names the offending character, for the error message."""
    assert _first_xml_incompatible(text) is None
    assert _first_xml_incompatible(text + bad) == bad
