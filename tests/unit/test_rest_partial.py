"""`partial=False` must reach the wire, not be answered from a partial cache.

Found while removing the `latest` cache (#338). The integration test that
claimed to check this — `test_resolve_partial_false_has_more_codes_than_partial`
— had **never** exercised it: its cassette holds a single
``detail=referencepartial`` interaction, because the `partial=False` call was
answered from the `_resolved_latest` index and the ``detail=full`` request was
never issued, even at record time. The assertion `full >= partial` then compared
an object with itself.

That is worse than staleness. A caller who writes `partial=False` is saying *"I
need the untrimmed codelists"*; being handed the trimmed ones silently is a
wrong answer to an explicit question.

Stubbed at the transport so both responses are ours: the point is which request
goes out and which artefact comes back, neither of which needs a real provider.

**Every test here is parametrised over a pinned URN and a `latest` one**, and
that is #344: the original file used `latest` throughout, and three of the four
tests below failed against a pinned URN when finally asked. `latest` re-asks
every time because it is a pointer (#338), so it happened to issue the
`detail=full` request on the way past — for reasons that had nothing to do with
`partial`. The pinned path was answered from `_resolved_map`, which recorded
*that* a URN had been resolved and not *how completely*.

A guard that only exercises the case which passes for an unrelated reason is
the shape worth watching for. Both URN kinds, always.
"""

from __future__ import annotations

import httpx
import pytest

import sdmxlib as sl
from sdmxlib.model.ref import Ref
from tests.support.narrow import present

pytestmark = pytest.mark.unit

#: Both, on every test. `latest` is a pointer and pinned is an identity, and
#: they reach the session cache by different paths.
URNS = {
    "pinned": sl.DataStructure.urn_for(agency="AG", id="DSD_X", version="1.0"),
    "latest": sl.DataStructure.urn_for(agency="AG", id="DSD_X"),
}

_TRIMMED = ["A"]
_UNTRIMMED = ["A", "Q", "M"]


def _message(codes: list[str]) -> bytes:
    code_xml = "".join(f'<str:Code id="{c}"><com:Name xml:lang="en">{c}</com:Name></str:Code>' for c in codes)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<mes:Structure xmlns:mes="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message"'
        ' xmlns:str="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure"'
        ' xmlns:com="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common">'
        "<mes:Header><mes:ID>x</mes:ID><mes:Test>false</mes:Test>"
        "<mes:Prepared>2026-01-01T00:00:00</mes:Prepared><mes:Sender id='S'/></mes:Header>"
        "<mes:Structures>"
        "<str:Codelists>"
        f'<str:Codelist id="CL_FREQ" agencyID="AG" version="1.0" isFinal="false">'
        '<com:Name xml:lang="en">freq</com:Name>'
        f"{code_xml}"
        "</str:Codelist></str:Codelists>"
        "<str:DataStructures>"
        '<str:DataStructure id="DSD_X" agencyID="AG" version="1.0" isFinal="false">'
        '<com:Name xml:lang="en">dsd</com:Name>'
        "<str:DataStructureComponents><str:DimensionList id='DimensionDescriptor'>"
        '<str:Dimension id="FREQ" position="1"><str:ConceptIdentity>'
        "<Ref package='conceptscheme' class='Concept' maintainableParentID='CS' "
        "maintainableParentVersion='1.0' agencyID='AG' id='FREQ'/>"
        "</str:ConceptIdentity><str:LocalRepresentation><str:Enumeration>"
        "<Ref package='codelist' id='CL_FREQ' agencyID='AG' version='1.0' class='Codelist'/>"
        "</str:Enumeration></str:LocalRepresentation></str:Dimension>"
        "</str:DimensionList></str:DataStructureComponents>"
        "</str:DataStructure></str:DataStructures>"
        "</mes:Structures></mes:Structure>"
    ).encode()


def _serving_by_detail():
    """A provider that trims codes unless ``detail=full`` is asked for.

    Which is what `detail` means: ``referencepartial`` returns only the codes
    the structure actually uses.
    """
    seen: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        detail = request.url.params.get("detail", "")
        seen.append(detail)
        codes = _UNTRIMMED if detail == "full" else _TRIMMED
        return httpx.Response(200, content=_message(codes), headers={"content-type": "application/xml"})

    return _handler, seen


def _registry(handler):
    return sl.RestRegistry("https://x.test/s", id="AG", transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("urn", URNS.values(), ids=URNS.keys())
def test_partial_false_issues_a_full_request_after_a_partial_one(urn) -> None:
    """The request that was never made.

    Before #338 the second call was served from the `latest` index and no
    ``detail=full`` request went out — provably, since the recorded cassette
    for the integration test contains only the partial one.
    """
    handler, seen = _serving_by_detail()
    with _registry(handler) as reg:
        reg.get(urn, resolve=True)
        reg.get(urn, resolve=True, partial=False)

    assert "full" in seen, f"partial=False never reached the wire; details asked: {seen}"


@pytest.mark.parametrize("urn", URNS.values(), ids=URNS.keys())
def test_partial_false_returns_the_untrimmed_codelist(urn) -> None:
    """The claim the integration test meant to make, now actually made.

    `full >= partial` was the original assertion and it is too weak to fail:
    when both sides are the same object it holds trivially. Asserted as exact
    membership instead, so a cached partial answer cannot satisfy it.

    Note the trimmed codes are read *before* the second call — see
    `test_a_fuller_fetch_upgrades_the_interned_codelist` for why that is not
    incidental.
    """
    handler, _ = _serving_by_detail()
    with _registry(handler) as reg:
        trimmed = reg.get(urn, resolve=True)
        assert isinstance(present(trimmed).dimensions["FREQ"].representation, Ref)
        trimmed_codes = [c.id for c in present(trimmed).dimensions["FREQ"].representation().codes]
        untrimmed = reg.get(urn, resolve=True, partial=False)
        assert isinstance(present(untrimmed).dimensions["FREQ"].representation, Ref)
        untrimmed_codes = [c.id for c in present(untrimmed).dimensions["FREQ"].representation().codes]

    assert trimmed_codes == _TRIMMED
    assert untrimmed_codes == _UNTRIMMED


@pytest.mark.parametrize("urn", URNS.values(), ids=URNS.keys())
def test_a_fuller_fetch_upgrades_the_interned_codelist(urn) -> None:
    """Surprising, and deliberate: a reference finds its target in the registry
    on each access (#492), so after a later fetch carrying more of
    `CL_FREQ(1.0)`, the DSD handed out earlier finds the fuller codelist.

    A caller still holding the first DSD sees its codelist grow. That is not a
    stale-cache bug — it is the same pinned artefact, more completely known —
    but it does mean *"read the codes, then re-fetch, then compare"* measures
    nothing. Written down because it cost a test-design mistake to find. It
    used to happen by filling in the earlier object; nothing is written now.
    """
    handler, _ = _serving_by_detail()
    with _registry(handler) as reg:
        first = reg.get(urn, resolve=True)
        assert isinstance(present(first).dimensions["FREQ"].representation, Ref)
        before = [c.id for c in present(first).dimensions["FREQ"].representation().codes]
        reg.get(urn, resolve=True, partial=False)
        assert isinstance(present(first).dimensions["FREQ"].representation, Ref)
        after = [c.id for c in present(first).dimensions["FREQ"].representation().codes]

    assert before == _TRIMMED
    assert after == _UNTRIMMED, "the DSD handed out earlier did not find the fuller codelist"


@pytest.mark.parametrize("urn", URNS.values(), ids=URNS.keys())
def test_a_partial_request_after_a_full_one_is_still_answered(urn) -> None:
    """The other order, so the fix is not "always re-fetch when partial differs
    in one direction only".

    Only that it is *answered*, because the two URN kinds answer differently and both
    are right: pinned hits the cache and hands back the untrimmed codelist it
    already holds, `latest` re-asks as a pointer must and gets the trimmed one
    it asked for. The request counts are pinned separately below.
    """
    handler, seen = _serving_by_detail()
    with _registry(handler) as reg:
        reg.get(urn, resolve=True, partial=False)
        second = reg.get(urn, resolve=True)

    assert seen.count("full") >= 1
    assert second is not None
    assert isinstance(second.dimensions["FREQ"].representation, Ref)
    assert second.dimensions["FREQ"].representation().codes.ids(), "answered with a real codelist"


def test_a_pointer_re_asks_for_the_partial_detail_it_was_given() -> None:
    """The `latest` half of the pair above.

    A pointer is never served from the session (#338), so `partial=True` after
    `partial=False` goes back out — and comes back trimmed, which is what was
    asked for. Not a downgrade of the earlier answer: it is a *later* answer to
    a *different* question.
    """
    handler, seen = _serving_by_detail()
    with _registry(handler) as reg:
        reg.get(URNS["latest"], resolve=True, partial=False)
        trimmed = reg.get(URNS["latest"], resolve=True)

    assert seen == ["full", "referencepartial"], f"details asked: {seen}"
    assert trimmed is not None
    assert isinstance(trimmed.dimensions["FREQ"].representation, Ref)
    assert [c.id for c in trimmed.dimensions["FREQ"].codelist.codes] == _TRIMMED


def test_a_partial_request_after_a_full_one_costs_no_request() -> None:
    """Completeness is *ordered*, and this is the direction that follows.

    `FULL` satisfies a request for `PARTIAL`, so the second call is a hit.
    Before #344 this happened by accident — the boolean said "resolved" and
    both directions hit, which is also why the bug existed. Now it is the
    ordering doing it, and worth pinning: a fix that keyed the cache on
    `partial` instead would re-fetch here for nothing.

    Pinned only. A `latest` URN is a pointer and re-asks whatever is held
    (#338), so it cannot show a cache hit and is not making this promise.
    """
    handler, seen = _serving_by_detail()
    with _registry(handler) as reg:
        reg.get(URNS["pinned"], resolve=True, partial=False)
        reg.get(URNS["pinned"], resolve=True)

    assert seen == ["full"], f"the partial request should have been a cache hit; asked: {seen}"
