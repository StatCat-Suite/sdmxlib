"""Unreachable raises and names itself; absent returns `None` (§5.8).

The two are different facts and the caller's response differs — retry a dead
source, fall back for a missing artefact — so a library that reports them
identically has made the distinction unrecoverable.

Before this, a dead provider surfaced as a bare
``ConnectError: connection refused``: no source, no URL, nothing to act on.

The line that matters most is `TransportError` rather than `HTTPError`. The
latter includes `HTTPStatusError`, so catching it would relabel a 404 — a
perfectly good answer meaning "not here" — as an unreachable source, which is
the conflation this removes.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

import sdmxlib as sl

pytestmark = pytest.mark.unit

_URN = sl.Codelist.urn_for(agency="AG", id="CL_X", version="1.0")
_DEVNULL = Path("/dev/null")


def _registry(handler):
    return sl.RestRegistry("https://x.test/s", id="AG", transport=httpx.MockTransport(handler))


def _refusing(exc: Exception):
    def _handler(_request: httpx.Request) -> httpx.Response:
        raise exc

    return _handler


# ── unreachable raises, and says enough to act on ─────────────────────────


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("connection refused"),
        httpx.ReadTimeout("timed out"),
        httpx.ConnectTimeout("timed out"),
        httpx.PoolTimeout("pool exhausted"),
        OSError("network is down"),
    ],
)
def test_a_transport_failure_becomes_registry_unavailable(exc: Exception) -> None:
    """Every way a request fails to get an answer, not only refusal."""
    with _registry(_refusing(exc)) as reg, pytest.raises(sl.RegistryUnavailable):
        reg.get(_URN)


def test_the_error_names_the_source_and_what_was_tried() -> None:
    """§5.8 asks for both. A bare `ConnectError` gives neither, and in a
    federation "which one?" is the first question a caller has."""
    with _registry(_refusing(httpx.ConnectError("refused"))) as reg, pytest.raises(sl.RegistryUnavailable) as caught:
        reg.get(_URN)

    message = str(caught.value)
    assert "https://x.test/s" in message, "the source"
    assert "CL_X" in message, "and what was being read"
    assert caught.value.tried is not None


def test_the_underlying_error_is_preserved() -> None:
    """Wrapping must not cost the diagnosis — the transport error says *why*."""
    cause = httpx.ConnectError("refused")
    with _registry(_refusing(cause)) as reg, pytest.raises(sl.RegistryUnavailable) as caught:
        reg.get(_URN)
    assert caught.value.cause is cause
    assert caught.value.__cause__ is cause


# ── absent is still absent ────────────────────────────────────────────────


def test_a_404_is_absent_not_unreachable() -> None:
    """The discriminating case, and the reason the catch is `TransportError`
    rather than `HTTPError`.

    SDMX-REST assigns 404 the meaning *"no results found"*, so it is the
    commonest way a provider says "not here" — and §5.8 reserves `None` for
    exactly that. It used to raise `HTTPStatusError`, which left `None`
    reachable only when a provider answered 200 with an empty message.
    """
    with _registry(lambda _r: httpx.Response(404, text="not found")) as reg:
        assert reg.get(_URN) is None


def test_a_404_and_a_dead_source_are_distinguishable() -> None:
    """The whole point of §5.8, in one test: the two facts a caller must act
    on differently must not arrive looking the same."""
    with _registry(lambda _r: httpx.Response(404)) as absent:
        assert absent.get(_URN) is None
    with _registry(_refusing(httpx.ConnectError("refused"))) as dead, pytest.raises(sl.RegistryUnavailable):
        dead.get(_URN)


def test_a_500_is_not_an_unreachable_source() -> None:
    """A server that answers at all was reached. Its error is its own, and
    relabelling it would tell the caller to retry a different thing."""
    with _registry(lambda _r: httpx.Response(500, text="boom")) as reg, pytest.raises(httpx.HTTPStatusError):
        reg.get(_URN)


# ── the streaming paths, where status and transport share a block ─────────


def test_a_server_error_on_a_streaming_path_is_not_relabelled() -> None:
    """Found by a surviving mutant, not by review.

    On `get_artefact` the status check runs *after* `_reaching` closes, so
    swapping `TransportError` for `HTTPError` there changes nothing and the
    mutant lived. On the streaming paths it does not: `stream()` opens the
    connection inside `_reaching`, so `_raise_for_status` runs in the same
    block — and the broader catch would report a 500 as an unreachable
    source, telling the caller to retry a server that answered.
    """
    with (
        _registry(lambda _r: httpx.Response(500, text="boom")) as reg,
        pytest.raises(httpx.HTTPStatusError),
    ):
        reg._client.stream_data_at("data/dataflow/AG/DF/1.0/all", {}, to=_DEVNULL)


def test_a_transport_failure_on_a_streaming_path_is_still_wrapped() -> None:
    """The other half, so the fix is not "streaming never wraps"."""
    with _registry(_refusing(httpx.ConnectError("refused"))) as reg, pytest.raises(sl.RegistryUnavailable):
        reg._client.stream_data_at("data/dataflow/AG/DF/1.0/all", {}, to=_DEVNULL)
