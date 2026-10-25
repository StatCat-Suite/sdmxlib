"""How long a `latest` pointer may go un-re-asked (#339).

#338 settled the rule — a pinned URN caches forever, `latest` is a pointer and
never a cache key — and left the window to this. The window is the provider's
call, with a caller override.

Every provider shape here is one recorded in `tests/integration/cassettes`, so
the cases are what real services actually send rather than what the RFC allows:

| provider             | structures                                       | revalidation  |
| -------------------- | ------------------------------------------------ | ------------- |
| ECB, IMF             | `no-cache, no-store, max-age=0, must-revalidate`  | none          |
| OECD                 | `public, max-age=86400`                           | Last-Modified |
| SDMX Global Registry | *(absent)*                                        | ETag          |

The override is a **floor**: it can rescue a provider whose `no-store` is
appliance boilerplate — ECB sends it on *codelists* — and can never shorten
what a generous one offered.
"""

from __future__ import annotations

import httpx2
import pytest

import sdmxlib as sl
from tests.support.narrow import present

pytestmark = pytest.mark.unit

_LATEST = sl.Codelist.urn_for(agency="AG", id="CL_X", version="latest")
_PINNED = sl.Codelist.urn_for(agency="AG", id="CL_X", version="1.0")


def _body(version: str = "1.0", name: str = "x") -> bytes:
    return (
        '<?xml version="1.0"?><mes:Structure '
        'xmlns:mes="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message" '
        'xmlns:str="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure" '
        'xmlns:com="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common">'
        "<mes:Header><mes:ID>x</mes:ID><mes:Test>false</mes:Test>"
        "<mes:Prepared>2026-01-01T00:00:00</mes:Prepared><mes:Sender id='S'/></mes:Header>"
        "<mes:Structures><str:Codelists>"
        f'<str:Codelist id="CL_X" agencyID="AG" version="{version}" isFinal="false">'
        f'<com:Name xml:lang="en">{name}</com:Name>'
        '<str:Code id="A"><com:Name xml:lang="en">a</com:Name></str:Code>'
        "</str:Codelist></str:Codelists></mes:Structures></mes:Structure>"
    ).encode()


class _Provider:
    """Serves `headers`, and answers 304 to a matching validator."""

    def __init__(self, headers: dict[str, str], *, etag: str | None = None) -> None:
        self.headers = headers
        self.etag = etag
        self.seen: list[httpx2.Headers] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.seen.append(request.headers)
        if self.etag and request.headers.get("if-none-match") == self.etag:
            return httpx2.Response(304, headers=self.headers)
        return httpx2.Response(200, content=_body(), headers={"content-type": "application/xml", **self.headers})

    @property
    def requests(self) -> int:
        return len(self.seen)

    @property
    def conditional(self) -> int:
        return sum(1 for headers in self.seen if "if-none-match" in headers or "if-modified-since" in headers)


def _registry(provider: _Provider, **kwargs):
    return sl.RestRegistry("https://x.test/s", id="AG", transport=httpx2.MockTransport(provider), **kwargs)


# ── the provider decides ──────────────────────────────────────────────────


def test_a_provider_that_says_nothing_is_re_asked_every_time() -> None:
    """#338's rule, unchanged where no header moves it. The library has no
    basis for inventing a window, so it does not."""
    provider = _Provider({})
    with _registry(provider) as reg:
        for _ in range(3):
            reg.get(_LATEST)
    assert provider.requests == 3


def test_a_max_age_window_is_honoured() -> None:
    """OECD publishes `max-age=86400` on structures, which is a deliberate
    statement and the case this feature exists to use."""
    provider = _Provider({"cache-control": "public, max-age=86400"})
    with _registry(provider) as reg:
        answers = [reg.get(_LATEST) for _ in range(3)]
    assert provider.requests == 1
    assert all(answer is not None for answer in answers)
    assert len({id(answer) for answer in answers}) == 1, "and the same artefact each time"


def test_no_store_is_obeyed() -> None:
    """ECB and IMF send this on codelists. Obeyed by default, whatever we
    suspect about why they send it."""
    provider = _Provider({"cache-control": "no-cache, no-store, max-age=0, must-revalidate"})
    with _registry(provider) as reg:
        for _ in range(3):
            reg.get(_LATEST)
    assert provider.requests == 3


def test_an_etag_makes_revalidation_cheap() -> None:
    """The Global Registry sends an ETag and no `max-age`. Still one request
    per ask — but a 304, which carries no body."""
    provider = _Provider({"etag": '"v1"'}, etag='"v1"')
    with _registry(provider) as reg:
        for _ in range(3):
            reg.get(_LATEST)
    assert provider.requests == 3
    assert provider.conditional == 2, "the first has nothing to revalidate against"


def test_a_last_modified_is_used_when_there_is_no_etag() -> None:
    """OECD's validator. Weaker than an ETag — one-second granularity — but
    sending it costs nothing and not sending it costs a body."""
    provider = _Provider({"last-modified": "Wed, 21 Oct 2026 07:28:00 GMT"})
    with _registry(provider) as reg:
        reg.get(_LATEST)
        reg.get(_LATEST)
    assert "if-modified-since" in provider.seen[-1]


# ── the caller overrides ──────────────────────────────────────────────────


def test_the_override_rescues_a_no_store_provider() -> None:
    """The reason the override is part of the design rather than an
    afterthought: ECB sends `no-store` on a *codelist*, and stamps
    `isFinal="false"` on all 2420 artefacts recorded. Obeying that literally
    optimises for a statement nobody meant to make."""
    provider = _Provider({"cache-control": "no-cache, no-store, max-age=0"})
    with _registry(provider, latest_ttl=300) as reg:
        for _ in range(3):
            reg.get(_LATEST)
    assert provider.requests == 1


def test_the_override_is_a_floor_not_a_ceiling() -> None:
    """A caller asking for 1 second cannot shorten a provider's day. Wanting
    *less* is spelled with a pinned URN, which is not a pointer at all."""
    provider = _Provider({"cache-control": "public, max-age=86400"})
    with _registry(provider, latest_ttl=1) as reg:
        for _ in range(3):
            reg.get(_LATEST)
    assert provider.requests == 1


# ── what must not change ──────────────────────────────────────────────────


def test_a_pinned_urn_never_takes_the_freshness_path() -> None:
    """A pinned version is immutable, so there is no pointer to confirm — and
    no header may shorten it. Answered from the session, no conditional
    request, whatever the provider said."""
    provider = _Provider({"cache-control": "no-cache, no-store, max-age=0"})
    with _registry(provider) as reg:
        for _ in range(3):
            reg.get(_PINNED)
    assert provider.requests == 1, "cached by identity, not by freshness"
    assert provider.conditional == 0


def test_a_moved_pointer_is_seen_once_the_window_expires() -> None:
    """The freshness window bounds staleness; it does not remove it. With no
    window granted, a new version is visible immediately — which is #338's
    guarantee, still holding."""
    served = ["1.0", "2.0"]
    calls = {"n": 0}

    def _handler(_request: httpx2.Request) -> httpx2.Response:
        version = served[min(calls["n"], 1)]
        calls["n"] += 1
        return httpx2.Response(200, content=_body(version), headers={"content-type": "application/xml"})

    with sl.RestRegistry("https://x.test/s", id="AG", transport=httpx2.MockTransport(_handler)) as reg:
        # A local first: pyrefly 1.2.0 does not solve `present(reg.get(...))` when
        # the inner call is itself generic (`T | None`) — see #425's repro.
        first = reg.get(_LATEST)
        assert present(first).version == "1.0"
        second = reg.get(_LATEST)
        assert present(second).version == "2.0"


def test_a_query_fetch_is_never_conditional() -> None:
    """The query path must not take this route. A `NOT_MODIFIED` there would
    write no artefacts, leaving #340's scope membership empty and the query
    answering nothing — so `get_artefact` stays unconditional and only the
    `get` path opts in."""
    provider = _Provider({"cache-control": "public, max-age=86400"})
    with _registry(provider) as reg:
        assert reg.query(sl.q.codelist).ids() == ["CL_X"]
        assert reg.query(sl.q.codelist).filter(sl.q.codelist.agency == "AG").ids() == ["CL_X"]
    assert provider.conditional == 0


# ── isolating the two rules `reg.get` cannot show ─────────────────────────


def test_no_store_beats_a_max_age_that_contradicts_it() -> None:
    """Isolates the flag from the number.

    Both survived a mutant that dropped `no_store` entirely, because every
    real provider that sends it also sends `max-age=0` — so the suite could
    not tell which one was doing the work. This sends a contradiction no
    provider would, precisely so it can.
    """
    provider = _Provider({"cache-control": "no-store, max-age=86400"})
    with _registry(provider) as reg:
        for _ in range(3):
            reg.get(_LATEST)
    assert provider.requests == 3, "no-store wins over the max-age beside it"


def test_the_override_still_rescues_that_contradiction() -> None:
    """And the floor applies to the flag, not only to a small number."""
    provider = _Provider({"cache-control": "no-store, max-age=0"})
    with _registry(provider, latest_ttl=600) as reg:
        for _ in range(3):
            reg.get(_LATEST)
    assert provider.requests == 1


def test_a_pinned_url_is_never_given_a_freshness_entry() -> None:
    """Asserted on the client, because `reg.get` cannot show it.

    A pinned URN is answered from the session on every call after the first,
    so the client is reached once and no conditional request is ever made
    whatever the rule says — a mutant sending pinned URLs down the freshness
    path passed the registry-level test untouched.

    The commitment is that a pinned version *has no pointer to confirm*, so
    nothing about it is recorded here at all.
    """
    provider = _Provider({"cache-control": "public, max-age=86400", "etag": '"v1"'})
    with _registry(provider) as reg:
        reg._client.get_artefact_if_changed("codelist", "AG", "CL_X", "1.0")
        assert reg._client._freshness == {}, "a pinned URL has no freshness to remember"

        reg._client.get_artefact_if_changed("codelist", "AG", "CL_X", "latest")
        assert len(reg._client._freshness) == 1, "and a pointer does"


def test_the_freshness_store_is_bounded() -> None:
    """Per `docs/design/caching.md`. Eviction costs a request, never an answer
    — so the bound can be small and the failure mode is only slowness."""
    provider = _Provider({"cache-control": "public, max-age=86400"})
    with _registry(provider, freshness_size=2) as reg:
        for index in range(5):
            reg._client.get_artefact_if_changed("codelist", "AG", f"CL_{index}", "latest")
        assert len(reg._client._freshness) == 2
