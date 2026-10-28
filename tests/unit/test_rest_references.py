"""`resolve="all"` and `resolve="parents"`: the artefacts that refer to one (GH-221).

A ``descendants`` fetch, what `resolve=True` sends, follows an artefact's own
references and never returns the constraints on a dataflow or the hierarchy
associations linked to it. A bundle's structural fingerprint covers those, so
building one needs ``references=all`` or ``parents``.

The session records that direction apart from depth. Depth alone would let an
earlier ``descendants`` fetch, which is as deep as an ``all`` one, answer an
``all`` request from cache, and the referring artefacts would never be asked
for.

Stubbed at the transport, as `test_rest_partial.py` is: the point is which
request goes out. Request-count tests are pinned only, since a `latest` URN is a
pointer and re-asks every time (#338); the wire tests take both URN kinds.
"""

from __future__ import annotations

import httpx2
import pytest

import sdmxlib as sl
from tests.unit.test_rest_partial import URNS, _message

pytestmark = pytest.mark.unit

_PINNED = URNS["pinned"]


def _recording():
    """A provider answering every request with the DSD, recording each ``(references, detail)``."""
    seen: list[tuple[str, str]] = []

    def _handler(request: httpx2.Request) -> httpx2.Response:
        seen.append((request.url.params.get("references", ""), request.url.params.get("detail", "")))
        return httpx2.Response(200, content=_message(["A"]), headers={"content-type": "application/xml"})

    return _handler, seen


def _registry(handler):
    return sl.RestRegistry("https://x.test/s", id="AG", transport=httpx2.MockTransport(handler))


@pytest.mark.parametrize("urn", URNS.values(), ids=URNS.keys())
@pytest.mark.parametrize(
    ("resolve", "wire"),
    [
        ("all", ("all", "referencepartial")),
        ("parents", ("parents", "full")),
        (True, ("descendants", "referencepartial")),
        ([sl.Codelist], ("codelist", "referencepartial")),
    ],
    ids=["all", "parents", "true", "types"],
)
def test_each_resolve_value_reaches_the_wire(urn, resolve, wire) -> None:
    """`"parents"` follows none of the artefact's own references, so it asks for the artefact in full."""
    handler, seen = _recording()
    with _registry(handler) as reg:
        reg.get(urn, resolve=resolve)

    assert seen == [wire]


def test_a_descendants_fetch_does_not_answer_an_all_request() -> None:
    """The defect the direction record exists for: equal depth, different direction."""
    handler, seen = _recording()
    with _registry(handler) as reg:
        reg.get(_PINNED, resolve=True)
        reg.get(_PINNED, resolve="all")
        reg.get(_PINNED, resolve="all")

    assert [references for references, _ in seen] == ["descendants", "all"]


def test_a_descendants_fetch_does_not_answer_a_parents_request() -> None:
    handler, seen = _recording()
    with _registry(handler) as reg:
        reg.get(_PINNED, resolve=True)
        reg.get(_PINNED, resolve="parents")

    assert [references for references, _ in seen] == ["descendants", "parents"]


def test_a_parents_fetch_does_not_answer_a_descendants_request() -> None:
    """``parents`` learns what refers to the artefact and nothing it refers to."""
    handler, seen = _recording()
    with _registry(handler) as reg:
        reg.get(_PINNED, resolve="parents")
        reg.get(_PINNED, resolve=True)

    assert [references for references, _ in seen] == ["parents", "descendants"]


@pytest.mark.parametrize("later", [True, "parents", "all"], ids=["true", "parents", "all"])
def test_an_all_fetch_answers_every_narrower_request(later) -> None:
    """``all`` is both directions, so each narrower request after it is a cache hit."""
    handler, seen = _recording()
    with _registry(handler) as reg:
        reg.get(_PINNED, resolve="all")
        reg.get(_PINNED, resolve=later)

    assert [references for references, _ in seen] == ["all"]


def test_a_federation_forwards_the_direction_to_a_rest_source() -> None:
    handler, seen = _recording()
    with _registry(handler) as reg:
        sl.FederatedRegistry(routing={"AG": reg}).get(_PINNED, resolve="all")

    assert [references for references, _ in seen] == ["all"]
