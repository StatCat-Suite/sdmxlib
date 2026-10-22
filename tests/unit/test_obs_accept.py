"""An observation fetch asks each provider for CSV the way it answers (GH-50).

Both fetches sent a bare ``Accept: text/csv``, which Eurostat refuses with
406. Checked live on 2026-10-22, no one header serves every provider:

    Accept sent               ECB    ESTAT  BIS     OECD
    text/csv                  CSV    406    SDMX-ML CSV
    SDMX-CSV 1.0 type         406    CSV    CSV     CSV
    both, text/csv q=0.9      XML    XML    CSV     -

So the header is the provider's (`ProviderInfo.csv_media_type`), else the
SDMX-CSV the API version defines. And a 200 that is not CSV, as BIS sends for
``text/csv``, is refused rather than handed to DuckDB's CSV reader.

These drive the real `httpx.Client` through a `MockTransport`, as
`test_rest_download.py` does: the header and the check are the feature.
"""

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

import sdmxlib as sl
from sdmxlib.api.client import SdmxClient
from sdmxlib.api.providers import ProviderInfo

pytestmark = pytest.mark.unit

_CSV = b"DATAFLOW,FREQ,OBS_VALUE\nAG:F(1.0),A,1.5\n"
_XML = b'<?xml version="1.0"?><message:StructureSpecificData/>'


def _answering(
    content_type: str | None, body: bytes = _CSV
) -> tuple[Callable[[httpx.Request], httpx.Response], list[httpx.Request]]:
    seen: list[httpx.Request] = []
    headers = {} if content_type is None else {"content-type": content_type}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=body, headers=headers)

    return handler, seen


def _accept_sent(client: SdmxClient, seen: list[httpx.Request]) -> str:
    client.get_data_at("data/AG,F,1.0/all", {})
    return seen[-1].headers["accept"]


@pytest.mark.parametrize(
    ("api", "accept"),
    [
        ("2.1", "application/vnd.sdmx.data+csv;version=1.0.0"),
        ("3.0", "application/vnd.sdmx.data+csv;version=2.0.0"),
    ],
)
def test_a_bare_url_asks_for_the_sdmx_csv_its_api_defines(api: str, accept: str) -> None:
    handler, seen = _answering("application/vnd.sdmx.data+csv")
    with SdmxClient("https://example.test", api=api, transport=httpx.MockTransport(handler)) as client:
        assert _accept_sent(client, seen) == accept


@pytest.mark.parametrize(
    ("provider", "accept"),
    [
        (sl.Provider.ECB, "text/csv"),
        (sl.Provider.ESTAT, "application/vnd.sdmx.data+csv;version=1.0.0"),
        (sl.Provider.BIS, "application/vnd.sdmx.data+csv;version=1.0.0"),
        (sl.Provider.SDMX_GLOBAL, "application/vnd.sdmx.data+csv;version=2.0.0"),
    ],
)
def test_a_provider_asks_as_it_answers(provider: ProviderInfo, accept: str) -> None:
    handler, seen = _answering("text/csv")
    with sl.RestRegistry(provider, transport=httpx.MockTransport(handler)) as reg:
        assert _accept_sent(reg._client, seen) == accept


def test_an_explicit_media_type_wins_over_the_providers() -> None:
    handler, seen = _answering("text/csv")
    with sl.RestRegistry(
        sl.Provider.ECB, csv_media_type="application/vnd.sdmx.data+csv", transport=httpx.MockTransport(handler)
    ) as reg:
        assert _accept_sent(reg._client, seen) == "application/vnd.sdmx.data+csv"


@pytest.mark.parametrize(
    "content_type",
    ["text/csv", "text/csv; charset=utf-8", "application/vnd.sdmx.data+csv; version=2.0.0; charset=utf-8", None],
)
def test_any_csv_is_read(content_type: str | None, tmp_path: Path) -> None:
    handler, _ = _answering(content_type)
    with SdmxClient("https://example.test", transport=httpx.MockTransport(handler)) as client:
        assert client.get_data_at("data/AG,F,1.0/all", {}) == _CSV
        client.stream_data_at("data/AG,F,1.0/all", {}, to=tmp_path / "data.csv")
    assert (tmp_path / "data.csv").read_bytes() == _CSV


@pytest.mark.parametrize(
    "content_type", ["application/xml;charset=UTF-8", "application/vnd.sdmx.genericdata+xml;version=2.1"]
)
def test_a_response_that_is_not_csv_is_refused(content_type: str, tmp_path: Path) -> None:
    """BIS answers ``text/csv`` with SDMX-ML and a 200; ECB and ESTAT answer
    an ``Accept`` listing two types the same way. Neither reaches DuckDB."""
    handler, _ = _answering(content_type, body=_XML)
    target = tmp_path / "data.csv"
    with SdmxClient("https://example.test", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="not CSV"):
            client.get_data_at("data/AG,F,1.0/all", {})
        with pytest.raises(ValueError, match="not CSV"):
            client.stream_data_at("data/AG,F,1.0/all", {}, to=target)
    assert not target.exists(), "nothing is written before the check"
