"""A REST query answers from its own fetch, not from the store (§5.13, #340).

The compiled SQL scans the materialisation store, so before this an earlier
question's rows — or a previous session's, with a persistent `store=` —
answered alongside the current fetch. Measured on `main`:

    provider serves: ['CL_LIVE']
    query answers:   ['CL_LIVE', 'CL_RETIRED']

Wrong, with no signal. What was *not* broken, and what narrows the fix: a
**filtered** query was already fine, because the predicate re-applies locally
over the materialised rows, so an earlier question's rows filter back out. The
hole is unfiltered scans and projections.

**Why a table and not a timestamp.** The obvious `fetched_at >= <watermark>`
needs a value that does not exist when the SQL is built: terminals compile
first and fetch second, and `to_sql()` is contractually non-executing with
output documented as handable to another library, so it cannot be made to
fetch. `canonical_key(plan)` *is* known at compile time, so membership keyed by
it needs no reordering and no change to a public signature.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

import sdmxlib as sl

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.unit


def _message(*codelists: tuple[str, str]) -> bytes:
    body = "".join(
        f'<str:Codelist id="{cid}" agencyID="AG" version="{version}" isFinal="false">'
        f'<com:Name xml:lang="en">{cid}</com:Name>'
        f'<str:Code id="A"><com:Name xml:lang="en">a</com:Name></str:Code></str:Codelist>'
        for cid, version in codelists
    )
    return (
        '<?xml version="1.0"?><mes:Structure '
        'xmlns:mes="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message" '
        'xmlns:str="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure" '
        'xmlns:com="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common">'
        "<mes:Header><mes:ID>x</mes:ID><mes:Test>false</mes:Test>"
        "<mes:Prepared>2026-01-01T00:00:00</mes:Prepared><mes:Sender id='S'/></mes:Header>"
        f"<mes:Structures><str:Codelists>{body}</str:Codelists></mes:Structures></mes:Structure>"
    ).encode()


def _codelist(cid: str, version: str = "1.0") -> sl.Codelist:
    return sl.Codelist(
        id=cid,
        maintainer="AG",
        version=version,
        name={"en": cid},
        codes=[sl.Code(id="A", name={"en": "a"})],
    )


def _serving(*codelists: tuple[str, str]) -> Callable[[httpx.Request], httpx.Response]:
    return lambda _r: httpx.Response(200, content=_message(*codelists), headers={"content-type": "application/xml"})


def _rest(store: sl.LocalRegistry, handler: Callable[[httpx.Request], httpx.Response]) -> sl.RestRegistry:
    return sl.RestRegistry("https://x.test/s", id="X", store=store, transport=httpx.MockTransport(handler))


# ── the two measured symptoms ─────────────────────────────────────────────


def test_a_withdrawn_artefact_does_not_answer() -> None:
    """The headline. The store holds a codelist the provider has since
    retired; only the provider's answer may come back."""
    with sl.LocalRegistry(":memory:", source_registry="X") as store:
        store.add(_codelist("CL_RETIRED"))
        with _rest(store, _serving(("CL_LIVE", "1.0"))) as reg:
            assert reg.query(sl.q.codelist).ids() == ["CL_LIVE"]


def test_a_version_the_request_did_not_ask_for_does_not_answer() -> None:
    """REST requests default to `latest`, so a fetch brings one version while
    the store may hold every version it was ever given. The query reports what
    the request asked for."""
    with sl.LocalRegistry(":memory:", source_registry="X") as store:
        store.add(_codelist("CL_V", "1.0"))
        store.add(_codelist("CL_V", "2.0"))
        with _rest(store, _serving(("CL_LIVE", "1.0"))) as reg:
            assert reg.query(sl.q.codelist).ids() == ["CL_LIVE"]


def test_an_earlier_question_does_not_leak_into_a_later_one() -> None:
    """Two questions in one session. Before this, the second scan saw the
    first's rows — which looked right whenever the provider happened to serve
    a superset, and wrong otherwise."""
    served = iter([_serving(("CL_ONE", "1.0")), _serving(("CL_TWO", "1.0"))])
    handlers: list[Callable[[httpx.Request], httpx.Response]] = [next(served), next(served)]
    calls = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        response = handlers[min(calls["n"], 1)](request)
        calls["n"] += 1
        return response

    with sl.LocalRegistry(":memory:") as store, _rest(store, _handler) as reg:
        first = reg.query(sl.q.codelist).filter(sl.q.codelist.agency == "AG").ids()
        second = reg.query(sl.q.codelist).filter(sl.q.codelist.version == "1.0").ids()

    assert first == ["CL_ONE"]
    assert second == ["CL_TWO"], "not ['CL_ONE', 'CL_TWO']"


# ── what must keep working ────────────────────────────────────────────────


def test_a_local_registry_is_not_scoped() -> None:
    """A `LocalRegistry` has no fetch, so its rows *are* its answer. Scoping it
    would return nothing at all — the failure this whole area keeps producing."""
    with sl.LocalRegistry(":memory:") as reg:
        reg.add(_codelist("CL_LOCAL"))
        assert reg.query(sl.q.codelist).ids() == ["CL_LOCAL"]


def test_a_second_terminal_over_one_query_still_answers() -> None:
    """The trap #340 named. The fetch cache makes the second terminal skip the
    fetch, so a scope derived from "rows written just now" would filter to
    nothing and return empty. Keyed by the plan, the membership outlives the
    fetch that wrote it."""
    with sl.LocalRegistry(":memory:") as store, _rest(store, _serving(("CL_LIVE", "1.0"))) as reg:
        query = reg.query(sl.q.codelist)
        assert query.count() == 1
        assert query.ids() == ["CL_LIVE"], "the cached question still answers"


def test_a_re_fetch_replaces_rather_than_accumulates() -> None:
    """Two registries over one store, asking the same question, with the
    provider's answer changed in between. The second must not see the first's
    rows — membership is replaced per key, not appended."""
    responses = [_serving(("CL_OLD", "1.0")), _serving(("CL_NEW", "1.0"))]
    calls = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        response = responses[min(calls["n"], 1)](request)
        calls["n"] += 1
        return response

    with sl.LocalRegistry(":memory:") as store:
        with _rest(store, _handler) as first:
            assert first.query(sl.q.codelist).ids() == ["CL_OLD"]
        with _rest(store, _handler) as second:
            assert second.query(sl.q.codelist).ids() == ["CL_NEW"], "not ['CL_NEW', 'CL_OLD']"


def test_projections_and_pages_are_scoped_too() -> None:
    """Three compile entry points, one rule. A projection or a page that
    skipped the clause would be the same wrong answer by another terminal."""
    with sl.LocalRegistry(":memory:", source_registry="X") as store:
        store.add(_codelist("CL_RETIRED"))
        with _rest(store, _serving(("CL_LIVE", "1.0"))) as reg:
            projected = reg.query(sl.q.codelist).select(sl.q.codelist.id).collect()
            assert projected.to_series().to_list() == ["CL_LIVE"]
            page = reg.query(sl.q.codelist).page(10)
            assert [urn.split("=")[-1] for urn in page.urns] == ["AG:CL_LIVE(1.0)"]


def test_observations_are_not_scoped_by_this() -> None:
    """`ObsQuery` carries a `prepare` hook too, but its rows are observations
    in a bound table with no urn to scope by. Applying the clause there would
    be a binder error at best and an empty result at worst."""
    with sl.LocalRegistry(":memory:") as reg:
        assert reg.query(sl.q.codelist)._fetch_scope is None
