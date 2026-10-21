"""Fuzzing the readers: hostile bytes in, one declared exception out (#247).

Every other property in this suite asserts something about a *valid* document.
This one asserts the only thing that can be said about an invalid one:
`parse` either returns a message or raises `SdmxParseError`. Nothing else —
no `lxml.etree.XMLSyntaxError`, no `msgspec.DecodeError`, no
`UnicodeDecodeError`, no `AttributeError` from a reader that assumed an
element was there, and no hang.

That invariant is worth stating because the readers sit directly on the
untyped shell. Before #247, feeding 6600 mutated fixtures through `parse`
produced five distinct exception types, four of them third-party:

    4935  lxml.etree.XMLSyntaxError
     788  msgspec.DecodeError
     720  ValueError                    (ours, and the only documented one)
      45  msgspec.ValidationError
      15  SdmxConformanceError          (ours)
      13  UnicodeDecodeError

Handling a malformed provider response therefore meant importing lxml *and*
msgspec to name their exceptions. `SdmxParseError` is now the one type, with
the original preserved as ``__cause__``.

Hypothesis rather than a mutation loop over `random`, for two reasons that
matter more here than in the round-trip properties: a failure shrinks to a
minimal payload instead of arriving as a 40 KB blob, and the `ci` profile's
``derandomize`` makes it reproducible without a seed corpus on disk.
"""

from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings, target
from hypothesis import strategies as st

from sdmxlib.formats import parse, serialize
from sdmxlib.model.errors import SdmxParseError
from sdmxlib.model.message import StructureMessage
from tests.support.mutation import corpus, mutated_bytes, mutated_json, mutated_xml
from tests.support.narrow import present
from tests.support.strategies import codelists, data_structures

#: A mutated document may well break an IM rule; that warns as it is built
#: (#481), and the property is only that a read parses or raises.
pytestmark = [pytest.mark.unit, pytest.mark.filterwarnings("ignore::sdmxlib.ImRuleWarning")]

#: Mutation is cheap — no XSD validation, no round-trip — so these run the
#: full profile rather than the reduced count the schema-validating properties
#: use. `HYPOTHESIS_PROFILE=deep just test-properties` scales them 20x.
_EXAMPLES = present(settings.default).max_examples

_XML_CORPUS = corpus(".xml")
_JSON_CORPUS = corpus(".json")

#: Filesystem-dependent collection is worth asserting: an empty corpus would
#: make every property below vacuously pass.
assert len(_XML_CORPUS) >= 10, f"only {len(_XML_CORPUS)} XML fixtures found; discovery looks broken"
assert len(_JSON_CORPUS) >= 3, f"only {len(_JSON_CORPUS)} JSON fixtures found; discovery looks broken"

_FUZZ = settings(
    max_examples=_EXAMPLES,
    deadline=None,
    # Mutation draws from a fixed corpus and does real parsing work; both trip
    # the timing heuristics without indicating anything wrong.
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)


def _assert_parse_contract(name: str, payload: bytes) -> None:
    """`parse` returns a message or raises `SdmxParseError`. Nothing else.

    Bare ``except Exception`` is the point of the test rather than a lapse:
    the property is about which types escape, so it has to catch everything to
    find out. `pytest.raises` cannot express "either of these two outcomes".
    """
    try:
        message = parse(payload)
    except SdmxParseError:
        return
    except Exception as exc:  # noqa: BLE001 — see docstring
        pytest.fail(
            f"{name} mutated to {len(payload)} bytes raised "
            f"{type(exc).__module__}.{type(exc).__name__}: {exc}\n"
            f"parse() may only raise SdmxParseError — wrap it at the boundary "
            f"in sdmxlib.formats.parse, or fix the reader if this is a defect"
        )
    else:
        # A mutation the reader accepted got *further* than one it rejected, so
        # steer the search that way — this is how the fuzzer stops spending its
        # budget on payloads that die in the XML parser.
        target(float(len(message.all_artefacts())), label="artefacts recovered")


@_FUZZ
@given(case=mutated_bytes(_XML_CORPUS))
def test_byte_mutated_xml_parses_or_raises_sdmx_parse_error(case: tuple[str, bytes]):
    """Truncated, flipped, spliced and injected SDMX-ML."""
    _assert_parse_contract(*case)


@_FUZZ
@given(case=mutated_bytes(_JSON_CORPUS))
def test_byte_mutated_json_parses_or_raises_sdmx_parse_error(case: tuple[str, bytes]):
    """The same, for SDMX-JSON — msgspec's failures are a different family."""
    _assert_parse_contract(*case)


@_FUZZ
@given(case=mutated_xml(_XML_CORPUS))
def test_structurally_mutated_xml_parses_or_raises_sdmx_parse_error(case: tuple[str, bytes]):
    """Well-formed XML that is no longer valid SDMX.

    Where byte mutation gets 39 % of its payloads past the XML parser, this
    gets 93 % — and three times as many of them deep enough to recover ten or
    more artefacts. The document is missing a required attribute, or naming an
    element that does not exist, or carrying a URN that parses to nothing, and
    the reader has to decide what to do about it. See `tests.support.mutation`
    for the full measurement.
    """
    _assert_parse_contract(*case)


@_FUZZ
@given(case=mutated_json(_JSON_CORPUS))
def test_structurally_mutated_json_parses_or_raises_sdmx_parse_error(case: tuple[str, bytes]):
    """Valid JSON whose values have the wrong types for the typed DTOs."""
    _assert_parse_contract(*case)


@_FUZZ
@given(
    artefact=st.one_of(codelists(), data_structures()),
    version=st.sampled_from(["2.1", "3.0", "3.1", "json"]),
    data=st.data(),
)
def test_mutated_writer_output_parses_or_raises_sdmx_parse_error(
    artefact: object,
    version: str,
    data: st.DataObject,
):
    """Mutate what *we* emit, not only what is checked in.

    The corpus is a couple of dozen files written by hand. The documents this
    library actually produces are shaped by the writers, so fuzzing only the
    fixtures leaves the emitted shapes untested — including every construct a
    generated artefact reaches that no fixture happens to contain.
    """
    slot = "codelists" if type(artefact).__name__ == "Codelist" else "data_structures"
    # The slot is chosen at runtime, so no static type describes this splat —
    # one annotated local, per CLAUDE.md's rule for a genuinely dynamic step.
    by_field: dict[str, Any] = {slot: [artefact]}
    written = serialize(StructureMessage(**by_field), version=version)
    seed = [(f"generated-{version}", written)]
    _name, payload = data.draw(mutated_bytes(seed))
    _assert_parse_contract(f"generated {version}", payload)


# ── Hostile inputs, pinned ────────────────────────────────────────────────────
#
# Not fuzzing: fixed payloads for the classic parser attacks. They are here
# because the probe that opened #247 found sdmxlib already immune to all of
# them — and immune *by inheritance*, from lxml's defaults and msgspec's typed
# decode, with nothing in the suite saying so. One `etree.XMLParser(...)`
# constructed with `resolve_entities=True` or `huge_tree=True`, for a reason
# that looked good at the time, would undo it silently.
#
# The assertion is deliberately weak — "refused somehow" rather than a specific
# message — because the exact wording belongs to lxml and will change. What
# must not change is that the payload does not expand, does not read the disk,
# and does not recurse.

_NS_21 = (
    b'xmlns:mes="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message" '
    b'xmlns:str="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure" '
    b'xmlns:com="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common"'
)

#: Ten nested entities, each expanding the last tenfold — the "billion laughs"
#: denial of service. lxml caps the amplification factor.
_BILLION_LAUGHS = (
    b'<?xml version="1.0"?>\n<!DOCTYPE mes:Structure [\n'
    b' <!ENTITY a "aaaaaaaaaa">\n <!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">\n'
    b' <!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">\n <!ENTITY d "&c;&c;&c;&c;&c;&c;&c;&c;&c;&c;">\n'
    b' <!ENTITY e "&d;&d;&d;&d;&d;&d;&d;&d;&d;&d;">\n <!ENTITY f "&e;&e;&e;&e;&e;&e;&e;&e;&e;&e;">\n'
    b"]>\n<mes:Structure " + _NS_21 + b"><mes:Header><mes:ID>&f;</mes:ID></mes:Header></mes:Structure>"
)

#: An external entity pointing at a local file — XXE. lxml does not resolve
#: external entities unless asked, so the reference stays undefined.
_XXE = (
    b'<?xml version="1.0"?>\n'
    b'<!DOCTYPE mes:Structure [ <!ENTITY xxe SYSTEM "file:///etc/passwd"> ]>\n'
    b"<mes:Structure " + _NS_21 + b"><mes:Header><mes:ID>&xxe;</mes:ID></mes:Header></mes:Structure>"
)

#: 50 000 nested elements. lxml refuses beyond depth 256 without `huge_tree`,
#: which is what keeps this off the C stack.
_DEEP_XML = (
    b"<mes:Structure " + _NS_21 + b"><mes:Structures><str:Codelists>"
    b"<str:Codelist>" * 50_000 + b"</str:Codelist>" * 50_000 + b"</str:Codelists></mes:Structures></mes:Structure>"
)

#: 5 000 nested arrays where the schema declares an object.
_DEEP_JSON = (
    b'{"$schema":"https://raw.githubusercontent.com/sdmx-twg/sdmx-json/master/'
    b'structure-message/tools/schemas/2.0.0/sdmx-json-structure-schema.json","data":'
    + b"[" * 5_000
    + b"]" * 5_000
    + b"}"
)

#: 100 000 attributes on one element — no parser limit applies, so this one
#: asserts we *finish* rather than that we refuse.
_MANY_ATTRIBUTES = (
    b"<mes:Structure "
    + _NS_21
    + b" "
    + b" ".join(f'a{i}="{i}"'.encode() for i in range(100_000))
    + b"><mes:Structures/></mes:Structure>"
)


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        ("billion laughs", _BILLION_LAUGHS),
        ("XXE file:///etc/passwd", _XXE),
        ("50k nested elements", _DEEP_XML),
        ("5k nested JSON arrays", _DEEP_JSON),
    ],
    ids=["billion_laughs", "xxe", "deep_xml", "deep_json"],
)
def test_hostile_payloads_are_refused(name: str, payload: bytes):
    """Each classic parser attack is refused, and refused as a parse error."""
    with pytest.raises(SdmxParseError):
        parse(payload)


def test_xxe_does_not_read_the_filesystem():
    """The XXE payload must not leak file contents even if it somehow parses.

    Separate from the refusal above because the two failure modes are
    different: `parse` raising is the current behaviour, but a future parser
    change could make it *succeed* with `/etc/passwd` interpolated into the
    header, which the refusal test would then report as a regression without
    saying what was actually at stake.
    """
    try:
        message = parse(_XXE)
    except SdmxParseError:
        return
    assert "root:" not in str(message), "external entity was resolved — XXE"


def test_many_attributes_terminates():
    """100 000 attributes on one element is slow, not fatal."""
    try:
        parse(_MANY_ATTRIBUTES)
    except SdmxParseError:
        return  # refusing is fine; hanging or dying is not
