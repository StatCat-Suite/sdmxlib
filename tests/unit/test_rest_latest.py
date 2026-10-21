"""What a `latest` URN means across the chain (#338, design §5.16).

The decision, recorded here because the code cannot state it on its own:

**A pinned URN names an immutable artefact; `latest` names a mutable pointer.**
Retention is governed by SDMX version semantics, not by HTTP cache headers — a
provider's ``no-store`` does not shorten the life of ``CL_X(1.0)``, because that
version cannot change. `latest` is never itself a cache key: it is asked of the
authority, and what comes back is interned under the *pinned* URN it resolved
to.

Every test here publishes a **new version between two calls**, which is the
only arrangement that can tell a fresh answer from a lucky one. A cache that
never expires passes any test where the answer does not change.

Not covered here, deliberately: the freshness *window* for the pointer.
Provider ``Cache-Control`` plus a caller override is the agreed rule for that
and lands separately — see #339. Until it does, the pointer is re-asked every
time, which is the correct end of the trade to be on while the window does not
exist.
"""

from __future__ import annotations

import httpx
import pytest

import sdmxlib as sl
from tests.support.narrow import present

pytestmark = pytest.mark.unit

_LATEST = sl.Codelist.urn_for(agency="AG", id="CL_X", version="latest")
_V1 = sl.Codelist.urn_for(agency="AG", id="CL_X", version="1.0")
_V2 = sl.Codelist.urn_for(agency="AG", id="CL_X", version="2.0")


def _body(version: str, name: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<mes:Structure xmlns:mes="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message"'
        ' xmlns:str="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure"'
        ' xmlns:com="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common">'
        "<mes:Header><mes:ID>x</mes:ID><mes:Test>false</mes:Test>"
        "<mes:Prepared>2026-01-01T00:00:00</mes:Prepared><mes:Sender id='S'/></mes:Header>"
        "<mes:Structures><str:Codelists>"
        f'<str:Codelist id="CL_X" agencyID="AG" version="{version}" isFinal="false">'
        f'<com:Name xml:lang="en">{name}</com:Name>'
        '<str:Code id="A"><com:Name xml:lang="en">a</com:Name></str:Code>'
        "</str:Codelist></str:Codelists></mes:Structures></mes:Structure>"
    ).encode()


def _publishing(*editions: tuple[str, str], no_store: bool = False):
    """A provider that serves each edition in turn, then repeats the last.

    `no_store` sends ECB's real header — ``no-cache, no-store, max-age=0,
    must-revalidate`` — so a test can show it neither adds nor removes
    caching, because retention here is decided by the URN's version.
    """
    served: list[str] = []

    def _handler(_request: httpx.Request) -> httpx.Response:
        version, name = editions[min(len(served), len(editions) - 1)]
        served.append(version)
        headers = {"content-type": "application/xml"}
        if no_store:
            headers["cache-control"] = "no-cache, no-store, max-age=0, must-revalidate"
        return httpx.Response(200, content=_body(version, name), headers=headers)

    return _handler, served


def _registry(handler):
    return sl.RestRegistry("https://x.test/s", id="AG", transport=httpx.MockTransport(handler))


# ── the pointer is re-asked ───────────────────────────────────────────────


def test_latest_asked_twice_sees_a_new_version() -> None:
    """The defect, as a guard.

    Before #338 this made **one** request and returned ``1.0`` twice — an
    in-process index answered the second call from a pointer nobody had
    re-resolved. The provider had published 2.0 in between.
    """
    handler, served = _publishing(("1.0", "first"), ("2.0", "second"))
    with _registry(handler) as reg:
        first = reg.get(_LATEST)
        second = reg.get(_LATEST)

    assert served == ["1.0", "2.0"], "the pointer was re-asked"
    assert present(first).version == "1.0"
    assert present(second).version == "2.0", "and the new version is what came back"


def test_a_resolved_latest_is_re_asked_too() -> None:
    """`resolve=True` took a different index (`_resolved_latest`) and had the
    same defect. Two indices, one rule."""
    handler, served = _publishing(("1.0", "first"), ("2.0", "second"))
    with _registry(handler) as reg:
        first = reg.get(_LATEST, resolve=True)
        second = reg.get(_LATEST, resolve=True)

    assert present(first).version == "1.0"
    assert present(second).version == "2.0"
    assert served[0] == "1.0"


# ── the pinned artefact is kept ───────────────────────────────────────────


def test_a_latest_fetch_interns_under_the_pinned_urn() -> None:
    """The other half of the rule, and what stops "never cache `latest`" from
    meaning "never cache anything". The payload carries its real version, so
    resolving the pointer costs no extra round-trip."""
    handler, served = _publishing(("1.0", "first"))
    with _registry(handler) as reg:
        from_latest = reg.get(_LATEST)
        from_pinned = reg.get(_V1)

    assert len(served) == 1, "the pinned read was free"
    assert from_pinned is from_latest, "and it is the same object"


def test_a_pinned_urn_is_kept_even_when_the_provider_says_no_store() -> None:
    """ECB's actual header on codelists, verbatim.

    A pinned version is immutable under SDMX's versioning rules, so
    ``no-store`` cannot shorten its life here. RFC 9111 constrains caches
    standing between a client and an origin; this is an application holding an
    artefact it was given, under an identity the standard says cannot be
    reused for different content.
    """
    handler, served = _publishing(("1.0", "first"), no_store=True)
    with _registry(handler) as reg:
        reg.get(_V1)
        reg.get(_V1)
        reg.get(_V1)

    assert len(served) == 1, "no-store does not make an immutable version volatile"


def test_two_pinned_versions_are_both_kept() -> None:
    """`latest` moving does not evict what it used to point at — the older
    version is still a valid identity and still answerable."""
    handler, served = _publishing(("1.0", "first"), ("2.0", "second"))
    with _registry(handler) as reg:
        reg.get(_LATEST)
        reg.get(_LATEST)
        before = len(served)
        one = reg.get(_V1)
        two = reg.get(_V2)

    assert len(served) == before, "both were already held"
    assert (present(one).version, present(two).version) == ("1.0", "2.0")


# ── the two layers now agree ──────────────────────────────────────────────


def test_the_session_and_the_local_store_apply_one_rule(tmp_path) -> None:
    """#338's actual complaint: `_fetch_one` consulted two caches with
    opposite `latest` policies, and the one consulted first always won.

    Asserted through behaviour rather than by reading the two branches — with
    a `store` in play, a second registry over the same store must
    still see a newly published version.
    """
    handler, served = _publishing(("1.0", "first"), ("2.0", "second"))
    with sl.LocalRegistry(tmp_path / "cache.duckdb") as local, _registry(handler) as reg:
        reg._store = local
        first = reg.get(_LATEST)
        second = reg.get(_LATEST)
        pinned_again = reg.get(_V1)

    assert (present(first).version, present(second).version) == ("1.0", "2.0"), "neither layer served a stale pointer"
    assert present(pinned_again).version == "1.0", "and the pinned one is still served from cache"
    assert served == ["1.0", "2.0"], "exactly two requests: one per `latest` ask, none for the pinned"
