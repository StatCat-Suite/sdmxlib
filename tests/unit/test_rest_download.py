"""`RestRegistry.download` — byte-faithful passthrough, with the format asked for.

§6 step 8. Two things separate a download from `obs()`: nothing parses
the body, and the caller chooses the wire format. Both are only real if the
`Accept` header actually carries the choice and the response is checked against
it — a format argument that nothing verifies is decorative, which is the state
this replaced (`Accept` was hardcoded to ``text/csv``, so a download's path
extension was the only hint and SDMX-ML could not be requested at all).

These drive the **real** `httpx2.Client` through a `MockTransport`, rather than
stubbing `download_data`. The header construction and the `Content-Type` check
are the feature; a stub at that seam would test neither.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Generator
from contextlib import contextmanager

import httpx2
import pytest

import sdmxlib as sl

pytestmark = pytest.mark.unit

_FLOW = sl.Dataflow.urn_for(agency="ECB", id="EXR", version="1.0")
_CSV = b"DATAFLOW,FREQ,OBS_VALUE\nECB:EXR(1.0),M,1.2\n"


@contextmanager
def _registry(
    handler: Callable[[httpx2.Request], httpx2.Response],
) -> Generator[tuple[sl.RestRegistry, list[httpx2.Request]]]:
    """A registry whose HTTP is `handler`, and the requests it made.

    The list used to be stashed on the registry as an undeclared attribute,
    which needed a suppression to write and made every `seen` an attribute
    error (#347). Yielding the pair says the same thing, declared.
    """
    seen: list[httpx2.Request] = []

    def _record(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return handler(request)

    with sl.RestRegistry("https://example.test/service", id="ECB", transport=httpx2.MockTransport(_record)) as reg:
        yield reg, seen


def _serving(
    body: bytes = _CSV,
    content_type: str | None = "application/vnd.sdmx.data+csv;version=2.0.0",
) -> Callable[[httpx2.Request], httpx2.Response]:
    headers = {} if content_type is None else {"content-type": content_type}
    return lambda _request: httpx2.Response(200, content=body, headers=headers)


# ── the format reaches the wire ───────────────────────────────────────────


def test_the_format_is_sent_as_accept() -> None:
    """`Accept` used to be hardcoded; this asserts the argument sets it."""
    with _registry(_serving()) as (reg, seen):
        reg.download(_FLOW, fmt=sl.DataFormat.SDMX_CSV_2_0_0)
    assert seen[-1].headers["accept"] == "application/vnd.sdmx.data+csv;version=2.0.0"


def test_sdmx_ml_can_be_asked_for_at_all() -> None:
    """Unreachable before this step: the client only ever sent ``text/csv``."""
    xml = b"<mes:StructureSpecificData/>"
    with _registry(_serving(xml, "application/vnd.sdmx.data+xml;version=3.0.0")) as (reg, seen):
        assert reg.download(_FLOW, fmt=sl.DataFormat.SDMX_ML_3_0) == xml
    assert seen[-1].headers["accept"] == "application/vnd.sdmx.data+xml;version=3.0.0"


def test_the_body_is_returned_unparsed() -> None:
    """Byte-faithful: not a `Dataset`, not re-encoded, not column-reordered."""
    body = b"\xef\xbb\xbfDATAFLOW,FREQ\r\nECB:EXR(1.0),M\r\n"
    with _registry(_serving(body)) as (reg, _seen):
        assert reg.download(_FLOW, fmt=sl.DataFormat.SDMX_CSV_2_0_0) == body, "BOM and CRLFs intact"


def test_key_and_periods_reach_the_request() -> None:
    with _registry(_serving()) as (reg, seen):
        reg.download(_FLOW, fmt=sl.DataFormat.SDMX_CSV_2_0_0, key="M.USD", start_period="2021-01", end_period="2021-06")
    request = seen[-1]
    assert request.url.path.endswith("/data/ECB,EXR,1.0/M.USD")
    assert dict(request.url.params) == {"startPeriod": "2021-01", "endPeriod": "2021-06"}


def test_a_download_costs_exactly_one_request() -> None:
    """No structural round-trip: unlike `obs`, there is no key to
    widen and no columns to interpret, so the DSD is never needed. A second
    request here would mean the identity argument was being resolved."""
    with _registry(_serving()) as (reg, seen):
        reg.download(_FLOW, fmt=sl.DataFormat.SDMX_CSV_2_0_0)
    assert len(seen) == 1


# ── to a file ─────────────────────────────────────────────────────────────


def test_a_destination_gets_the_bytes_and_is_returned(tmp_path) -> None:
    out = tmp_path / "exr.csv"
    with _registry(_serving()) as (reg, _seen):
        assert reg.download(_FLOW, fmt=sl.DataFormat.SDMX_CSV_2_0_0, to=out) == out
    assert out.read_bytes() == _CSV


def test_a_destination_may_be_a_string(tmp_path) -> None:
    out = tmp_path / "exr.csv"
    with _registry(_serving()) as (reg, _seen):
        assert reg.download(_FLOW, fmt=sl.DataFormat.SDMX_CSV_2_0_0, to=str(out)) == out


# ── the two refusals ──────────────────────────────────────────────────────


def test_an_extension_contradicting_the_format_is_refused(tmp_path) -> None:
    """The format is not inferred from the path — but a `.xml` file holding
    CSV bytes misleads every later reader, so the disagreement is caught."""
    with _registry(_serving()) as (reg, seen), pytest.raises(ValueError, match=re.escape("ends in '.xml'")):
        reg.download(_FLOW, fmt=sl.DataFormat.SDMX_CSV_2_0_0, to=tmp_path / "exr.xml")
    assert seen == [], "and refused before the request, not after"


@pytest.mark.parametrize("name", ["exr", "exr.dat", "exr.csv.gz", "exr.2021"])
def test_an_unrecognised_extension_is_left_alone(tmp_path, name: str) -> None:
    """``.dat``, ``.gz`` and no extension say nothing about the contents.
    Refusing them would be inference wearing a refusal's clothes."""
    with _registry(_serving()) as (reg, _seen):
        assert reg.download(_FLOW, fmt=sl.DataFormat.SDMX_CSV_2_0_0, to=tmp_path / name).read_bytes() == _CSV


def test_a_provider_that_ignores_accept_is_caught() -> None:
    """The silent-wrong-answer case: ask for SDMX-CSV 2.0.0, get 1.0.0, and
    the columns differ. Nothing downstream would notice, because a download
    has no downstream — which is exactly why the check has to be here."""
    with (
        _registry(_serving(content_type="application/vnd.sdmx.data+csv;version=1.0.0")) as (reg, _seen),
        pytest.raises(ValueError, match="ignored `Accept`"),
    ):
        reg.download(_FLOW, fmt=sl.DataFormat.SDMX_CSV_2_0_0)


def test_a_different_base_format_is_caught() -> None:
    with (
        _registry(_serving(b"{}", "application/vnd.sdmx.data+json;version=2.0.0")) as (reg, _seen),
        pytest.raises(ValueError, match="ignored `Accept`"),
    ):
        reg.download(_FLOW, fmt=sl.DataFormat.SDMX_CSV_2_0_0)


def test_a_plain_text_csv_response_is_caught() -> None:
    """Providers that ignore `Accept` typically answer `text/csv`. Letting it
    pass would make the format argument decorative again."""
    with (
        _registry(_serving(content_type="text/csv")) as (reg, _seen),
        pytest.raises(ValueError, match="ignored `Accept`"),
    ):
        reg.download(_FLOW, fmt=sl.DataFormat.SDMX_CSV_2_0_0)


def test_the_check_also_guards_the_streamed_form(tmp_path) -> None:
    """Same refusal on the `to=` path, and *before* a file is written — a
    truncated wrong-format file on disk is the outcome worth avoiding."""
    out = tmp_path / "exr.csv"
    with (
        _registry(_serving(content_type="text/csv")) as (reg, _seen),
        pytest.raises(ValueError, match="ignored `Accept`"),
    ):
        reg.download(_FLOW, fmt=sl.DataFormat.SDMX_CSV_2_0_0, to=out)
    assert not out.exists()


# ── what the check tolerates ──────────────────────────────────────────────


def test_extra_parameters_are_ignored() -> None:
    """``charset`` and ``labels`` qualify the encoding, not the format."""
    served = "application/vnd.sdmx.data+csv;version=2.0.0;charset=utf-8;labels=both"
    with _registry(_serving(content_type=served)) as (reg, _seen):
        assert reg.download(_FLOW, fmt=sl.DataFormat.SDMX_CSV_2_0_0) == _CSV


def test_a_padded_version_agrees() -> None:
    """SDMX-ML 2.1's media type is registered as ``version=2.1``; a provider
    echoing ``2.1.0`` is agreeing, not disagreeing."""
    with _registry(_serving(b"<x/>", "application/vnd.sdmx.genericdata+xml;version=2.1.0")) as (reg, _seen):
        assert reg.download(_FLOW, fmt=sl.DataFormat.SDMX_ML_2_1_GENERIC) == b"<x/>"


def test_a_missing_content_type_passes() -> None:
    """Nothing to disagree with. Inventing a disagreement would refuse a body
    that may well be correct."""
    with _registry(_serving(content_type=None)) as (reg, _seen):
        assert reg.download(_FLOW, fmt=sl.DataFormat.SDMX_CSV_2_0_0) == _CSV


def test_case_does_not_matter() -> None:
    """Media types are case-insensitive per RFC 9110, and providers vary."""
    with _registry(_serving(content_type="APPLICATION/VND.SDMX.DATA+CSV;VERSION=2.0.0")) as (reg, _seen):
        assert reg.download(_FLOW, fmt=sl.DataFormat.SDMX_CSV_2_0_0) == _CSV
