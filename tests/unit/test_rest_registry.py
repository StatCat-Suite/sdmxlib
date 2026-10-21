"""Unit tests for RestRegistry — no HTTP."""

from http.cookiejar import CookieJar

import httpx
import pytest

import sdmxlib as sl
from sdmxlib.api.client import SdmxClient
from sdmxlib.api.providers import Provider, ProviderInfo
from sdmxlib.model.in_memory_registry import InMemoryRegistry
from tests.support.narrow import present

pytestmark = pytest.mark.unit

_ENDPOINT = "https://data-api.ecb.europa.eu/service"


def test_rest_registry_has_registry_property():
    with sl.RestRegistry(_ENDPOINT) as reg:
        assert isinstance(reg.registry, InMemoryRegistry)


def test_rest_registry_registry_starts_empty():
    with sl.RestRegistry(_ENDPOINT) as reg:
        assert len(reg.registry) == 0


def test_rest_registry_registry_is_same_instance():
    """registry property returns the same object on every call."""
    with sl.RestRegistry(_ENDPOINT) as reg:
        assert reg.registry is reg.registry


# ── Provider entries ──────────────────────────────────────────────────────────


def test_provider_ecb_is_provider_info():
    assert isinstance(Provider.ECB, ProviderInfo)


def test_provider_ecb_url():
    assert Provider.ECB.url == "https://data-api.ecb.europa.eu/service"


def test_provider_ecb_api_version():
    assert Provider.ECB.api == "2.1"


def test_provider_sdmx_global_api_version():
    assert Provider.SDMX_GLOBAL.api == "3.0"


def test_provider_subscript_access():
    assert Provider["ECB"] is Provider.ECB


def test_provider_subscript_missing():
    with pytest.raises(KeyError, match="UNKNOWN"):
        Provider["UNKNOWN"]


# ── RestRegistry accepts ProviderInfo ─────────────────────────────────────────


def test_rest_registry_accepts_provider_info():
    with sl.RestRegistry(Provider.ECB) as reg:
        assert isinstance(reg.registry, InMemoryRegistry)


def test_rest_registry_provider_info_overrides_api():
    """When a ProviderInfo is passed, its api field is used (not the default)."""
    # SDMX_GLOBAL has api="3.0"; construct and verify no error raised
    with sl.RestRegistry(Provider.SDMX_GLOBAL) as reg:
        assert isinstance(reg.registry, InMemoryRegistry)


# ── id + store ───────────────────────────────────────────────────────


def test_provider_carries_id():
    assert Provider.ECB.id == "ECB"
    assert Provider.SDMX_GLOBAL.id == "SDMX_GLOBAL"


def test_rest_registry_id_picked_up_from_provider():
    with sl.RestRegistry(Provider.ECB) as reg:
        assert reg.id == "ECB"


def test_rest_registry_explicit_id_wins_over_provider():
    with sl.RestRegistry(Provider.ECB, id="MY_ECB") as reg:
        assert reg.id == "MY_ECB"


def test_rest_registry_id_none_for_raw_url():
    with sl.RestRegistry(_ENDPOINT) as reg:
        assert reg.id is None


def test_store_requires_id():
    """A bare URL endpoint with store must supply id."""
    from sdmxlib.local import LocalRegistry

    with LocalRegistry(":memory:") as local, pytest.raises(ValueError, match="id"):
        sl.RestRegistry(_ENDPOINT, store=local).close()


def test_local_cache_serves_pinned_fetch_without_http(monkeypatch: pytest.MonkeyPatch):
    """A pinned URN already in the local store is served without HTTP.

    The plan goal: 'warmed federated paths match local-only latency.'
    """
    from sdmxlib.local import LocalRegistry
    from sdmxlib.model.codelist import Code, Codelist
    from sdmxlib.model.istring import InternationalString
    from sdmxlib.model.message import StructureMessage

    cl = Codelist(
        id="CL_FREQ",
        maintainer="ECB",
        version="1.0",
        name=InternationalString(en="Frequency"),
        codes=[Code(id="A", name=InternationalString(en="Annual"))],
    )

    call_count = {"n": 0}

    def _fake_get_artefact(self: SdmxClient, *args: object, **kwargs: object) -> StructureMessage:
        call_count["n"] += 1
        return StructureMessage(codelists=[cl])

    # The `get` path fetches through `get_artefact_if_changed`, which may
    # answer NOT_MODIFIED where the unconditional sibling cannot (#339).
    monkeypatch.setattr(SdmxClient, "get_artefact_if_changed", _fake_get_artefact)

    with LocalRegistry(":memory:") as local:
        # First fetch: HTTP, materialised through.
        with sl.RestRegistry(Provider.ECB, store=local) as reg:
            reg.get(Codelist.urn_for(agency="ECB", id="CL_FREQ", version="1.0"))
        assert call_count["n"] == 1

        # Second RestRegistry instance pointing to the same local store —
        # cold Session but warm local cache. Fetch must NOT hit HTTP.
        with sl.RestRegistry(Provider.ECB, store=local) as reg:
            result = reg.get(Codelist.urn_for(agency="ECB", id="CL_FREQ", version="1.0"))
        assert call_count["n"] == 1  # no extra HTTP call
        assert result is not None
        assert result.id == "CL_FREQ"


def test_local_cache_skipped_for_latest_urn(monkeypatch: pytest.MonkeyPatch):
    """A 'latest' URN re-fetches even when cached locally — version is under-specified."""
    from sdmxlib.local import LocalRegistry
    from sdmxlib.model.codelist import Code, Codelist
    from sdmxlib.model.istring import InternationalString
    from sdmxlib.model.message import StructureMessage

    cl = Codelist(
        id="CL_FREQ",
        maintainer="ECB",
        version="1.0",
        name=InternationalString(en="Frequency"),
        codes=[Code(id="A", name=InternationalString(en="Annual"))],
    )

    call_count = {"n": 0}

    def _fake_get_artefact(self: SdmxClient, *args: object, **kwargs: object) -> StructureMessage:
        call_count["n"] += 1
        return StructureMessage(codelists=[cl])

    # The `get` path fetches through `get_artefact_if_changed`, which may
    # answer NOT_MODIFIED where the unconditional sibling cannot (#339).
    monkeypatch.setattr(SdmxClient, "get_artefact_if_changed", _fake_get_artefact)

    with LocalRegistry(":memory:") as local:
        with sl.RestRegistry(Provider.ECB, store=local) as reg:
            reg.get(Codelist.urn_for(agency="ECB", id="CL_FREQ", version="1.0"))
        assert call_count["n"] == 1

        # Same content but URN says "latest" — must hit HTTP again.
        with sl.RestRegistry(Provider.ECB, store=local) as reg:
            reg.get(Codelist.urn_for(agency="ECB", id="CL_FREQ", version="latest"))
        assert call_count["n"] == 2


def test_store_writes_through_with_source_tag(monkeypatch: pytest.MonkeyPatch):
    """A successful fetch is written into the local store with source_registry=id."""
    from sdmxlib.local import LocalRegistry
    from sdmxlib.model.codelist import Code, Codelist
    from sdmxlib.model.istring import InternationalString
    from sdmxlib.model.message import StructureMessage

    cl = Codelist(
        id="CL_FREQ",
        maintainer="ECB",
        version="1.0",
        name=InternationalString(en="Frequency"),
        codes=[Code(id="A", name=InternationalString(en="Annual"))],
    )
    fake_msg = StructureMessage(codelists=[cl])

    def _fake_get_artefact(self: SdmxClient, *args: object, **kwargs: object) -> StructureMessage:
        return fake_msg

    # The `get` path fetches through `get_artefact_if_changed`, which may
    # answer NOT_MODIFIED where the unconditional sibling cannot (#339).
    monkeypatch.setattr(SdmxClient, "get_artefact_if_changed", _fake_get_artefact)

    with LocalRegistry(":memory:") as local, sl.RestRegistry(Provider.ECB, store=local) as reg:
        reg.get(Codelist.urn_for(agency="ECB", id="CL_FREQ", version="1.0"))
        # Artefact appears in the local store with the source tag
        cur = local._cursor
        row = present(cur).execute("SELECT source_registry FROM artefact WHERE artefact_type = 'Codelist'").fetchone()
        assert row == ("ECB",)


# ── SdmxClient URL building ───────────────────────────────────────────────────


def test_client_v21_resource_url():
    client = SdmxClient("https://example.com/service", api="2.1")
    url = client._resource_url("DataStructure", "ECB", "ECB_EXR1", "latest")
    assert url == "https://example.com/service/datastructure/ECB/ECB_EXR1/latest"
    client.close()


def test_client_v30_resource_url_latest():
    client = SdmxClient("https://example.com/sdmx/v2", api="3.0")
    url = client._resource_url("DataStructure", "ECB", "ECB_EXR1", "latest")
    assert url == "https://example.com/sdmx/v2/structure/datastructure/ECB/ECB_EXR1/~"
    client.close()


def test_client_v30_resource_url_explicit_version():
    client = SdmxClient("https://example.com/sdmx/v2", api="3.0")
    url = client._resource_url("Codelist", "BIS", "CL_FREQ", "1.0")
    assert url == "https://example.com/sdmx/v2/structure/codelist/BIS/CL_FREQ/1.0"
    client.close()


def test_client_v21_collection_url():
    client = SdmxClient("https://example.com/service", api="2.1")
    url = client._collection_url("Dataflow", "ECB")
    assert url == "https://example.com/service/dataflow/ECB"
    client.close()


def test_client_v21_collection_url_no_agency():
    client = SdmxClient("https://example.com/service", api="2.1")
    url = client._collection_url("Dataflow", None)
    assert url == "https://example.com/service/dataflow/all"
    client.close()


def test_client_v30_collection_url_no_agency():
    client = SdmxClient("https://example.com/sdmx/v2", api="3.0")
    url = client._collection_url("Dataflow", None)
    assert url == "https://example.com/sdmx/v2/structure/dataflow/*"
    client.close()


# ── Forwarded httpx options ───────────────────────────────────────────────────
#
# HttpxOptions must not narrow below httpx's own parameter types: sdmxlib never
# reads these values, and httpx normalises them at construction, so every
# spelling httpx accepts has to reach it unchanged. Written as separate literal
# call sites rather than a parametrize list, which would erase the argument
# types to `object` and need a suppression per call. As literals they also
# type-check against the declared shape under `basedpyright tests/...` — though
# note both checkers are scoped to `src` in the default gate, so the runtime
# assertions are what CI actually enforces.


def test_client_forwards_every_params_spelling():
    expected = httpx.QueryParams({"detail": "full"})
    clients = [
        SdmxClient(_ENDPOINT, params={"detail": "full"}),
        SdmxClient(_ENDPOINT, params=[("detail", "full")]),
        SdmxClient(_ENDPOINT, params=(("detail", "full"),)),
        SdmxClient(_ENDPOINT, params="detail=full"),
        SdmxClient(_ENDPOINT, params=b"detail=full"),
        SdmxClient(_ENDPOINT, params=httpx.QueryParams({"detail": "full"})),
    ]
    for client in clients:
        assert client._client.params == expected
        client.close()


def test_client_forwards_non_str_param_values():
    """httpx accepts int/bool/None and sequences; the shape must not reject them."""
    client = SdmxClient(_ENDPOINT, params={"limit": 100, "refs": ["parents", "children"]})
    assert client._client.params == httpx.QueryParams({"limit": 100, "refs": ["parents", "children"]})
    client.close()


def test_client_forwards_every_cookies_spelling():
    clients = [
        SdmxClient(_ENDPOINT, cookies={"session": "abc"}),
        SdmxClient(_ENDPOINT, cookies=[("session", "abc")]),
        SdmxClient(_ENDPOINT, cookies=CookieJar()),
        SdmxClient(_ENDPOINT, cookies=httpx.Cookies({"session": "abc"})),
    ]
    for client in clients:
        assert isinstance(client._client.cookies, httpx.Cookies)
        client.close()
