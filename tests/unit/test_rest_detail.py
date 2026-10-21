"""`detail=allstubs` reaches the wire, and cannot starve a later query (§5.14).

#318 derived when a stub answers a query and when it does not, and tested the
derivation — but `requests_for`'s `projection` argument defaulted to `None` and
the only production caller supplied none, so **`allstubs` never once fired**.
The derivation was tested and unreachable: coverage of a thing nobody ran.

The rule wired through is the one `request_for` already stated in a comment:
*only a projection knows it needs nothing more than the columns it names*. So
`Select` — which **is** the projected query — supplies its columns, and every
other terminal supplies `None`, meaning "artefacts", meaning `full`. A stub
carries no items, and resolving against one yields a codelist with zero codes
and no error.

The load-bearing test here is `test_a_narrow_fetch_does_not_satisfy_a_wide_
query`. The fetch cache keys on `canonical_key(plan)`, and the projection is
*not* part of the plan — so without the key carrying the derived detail, a
`select(id)` fetch would mark the plan done and a later `collect()` would skip
its own fetch and hand back hollow artefacts. Silent, and the worst shape
available.
"""

from __future__ import annotations

import httpx
import pytest

import sdmxlib as sl

pytestmark = pytest.mark.unit

_BODY = (
    b'<?xml version="1.0"?><mes:Structure '
    b'xmlns:mes="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message" '
    b'xmlns:str="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure" '
    b'xmlns:com="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common">'
    b"<mes:Header><mes:ID>x</mes:ID><mes:Test>false</mes:Test>"
    b"<mes:Prepared>2026-01-01T00:00:00</mes:Prepared><mes:Sender id='S'/></mes:Header>"
    b"<mes:Structures><str:Codelists>"
    b'<str:Codelist id="CL_X" agencyID="AG" version="1.0" isFinal="false">'
    b'<com:Name xml:lang="en">x</com:Name>'
    b'<str:Code id="A"><com:Name xml:lang="en">a</com:Name></str:Code>'
    b'<str:Code id="B"><com:Name xml:lang="en">b</com:Name></str:Code>'
    b"</str:Codelist></str:Codelists></mes:Structures></mes:Structure>"
)


@pytest.fixture
def reg():
    """A registry recording the `detail=` of every request it makes."""
    details: list[str | None] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        details.append(request.url.params.get("detail"))
        return httpx.Response(200, content=_BODY, headers={"content-type": "application/xml"})

    with sl.RestRegistry("https://x.test/s", id="AG", transport=httpx.MockTransport(_handler)) as registry:
        registry.details = details  # type: ignore[attr-defined]
        yield registry


# ── the derivation now fires ──────────────────────────────────────────────


def test_a_projection_asks_for_stubs(reg) -> None:
    """Unreachable before this: the argument existed and nothing passed it."""
    reg.query(sl.q.codelist).select(sl.q.codelist.id).collect()
    assert reg.details == ["allstubs"]


def test_a_projected_stream_asks_for_stubs(reg) -> None:
    """`Select`'s other executing path. Two call sites, one rule — a fix on
    only `collect` would leave `stream` fetching more than it reads."""
    list(reg.query(sl.q.codelist).select(sl.q.codelist.id).stream())
    assert reg.details == ["allstubs"]


# ── and only where a stub is enough ───────────────────────────────────────


def test_an_unprojected_collect_asks_for_full(reg) -> None:
    """`collect()` resolves rows to artefacts. A stub has no items, so this is
    the case that must never narrow — the failure is a hollow artefact and no
    error, not an exception."""
    codelists = reg.query(sl.q.codelist).collect()
    assert reg.details == ["full"]
    assert codelists[0].codes.count() == 2


def test_an_unprojected_terminal_asks_for_full(reg) -> None:
    """`ids()` reads one column and could in principle narrow, but it is not a
    projection and does not carry one. Left conservative deliberately: the
    saving is small and the failure mode is silence."""
    reg.query(sl.q.codelist).ids()
    assert reg.details == ["full"]


def test_a_projection_reaching_items_asks_for_full(reg) -> None:
    """A stub carries no items, so a projection that crosses into one cannot
    be served by it — `detail_for` forces `full` on any non-empty path.

    Rooted at the codelist and crossing to its codes, because `q.code` is not
    a REST root at all: items come back inside the artefact that owns them,
    and the planner refuses the scan outright.
    """
    reg.query(sl.q.codelist).select(codes=sl.q.codelist.codes.id.list()).collect()
    assert reg.details == ["full"]


# ── the cache key ─────────────────────────────────────────────────────────


def test_a_narrow_fetch_does_not_satisfy_a_wide_query(reg) -> None:
    """The trap the projection opens, and the reason it is in the fetch key.

    Both calls have the *same plan* — the projection lives on the `Select`, not
    the `Plan` — so a key of `canonical_key(plan)` alone would mark the
    question fetched after the stub request and let `collect()` resolve
    artefacts whose items never arrived.
    """
    reg.query(sl.q.codelist).select(sl.q.codelist.id).collect()
    codelists = reg.query(sl.q.codelist).collect()

    assert reg.details == ["allstubs", "full"], "the wide query fetched for itself"
    assert codelists[0].codes.count() == 2, "and its artefacts are whole"


def test_the_wide_fetch_is_not_repeated(reg) -> None:
    """The other direction: once `full` has been fetched, a narrower ask is
    already covered and must not fetch again."""
    reg.query(sl.q.codelist).collect()
    reg.query(sl.q.codelist).select(sl.q.codelist.id).collect()
    assert reg.details == ["full"], "a stub request after a full one is waste"


def test_two_projections_with_the_same_detail_share_one_fetch(reg) -> None:
    """Keyed on the *derived* detail, not the projection: two projections that
    both reduce to `allstubs` are one question on the wire."""
    reg.query(sl.q.codelist).select(sl.q.codelist.id).collect()
    reg.query(sl.q.codelist).select(sl.q.codelist.agency).collect()
    assert reg.details == ["allstubs"]
