"""Unit tests for sl.Provider — built-in endpoint map."""

import pytest

import sdmxlib as sl
from sdmxlib.api.providers import Provider, ProviderInfo

pytestmark = pytest.mark.unit


def test_attribute_access_returns_provider_info():
    assert isinstance(sl.Provider.ECB, ProviderInfo)
    assert sl.Provider.ECB.url == "https://data-api.ecb.europa.eu/service"


def test_subscript_access_returns_same_object():
    assert sl.Provider["ECB"] is sl.Provider.ECB


def test_all_builtins_accessible_via_subscript():
    for key in ("ECB", "BIS", "ESTAT", "IMF", "OECD", "SDMX_GLOBAL"):
        info = sl.Provider[key]
        assert isinstance(info, ProviderInfo)
        assert info.url.startswith("https://")


def test_unknown_key_raises_key_error():
    with pytest.raises(KeyError, match="NOPE"):
        _ = Provider["NOPE"]


def test_key_error_message_lists_available():
    with pytest.raises(KeyError, match="ECB"):
        _ = Provider["NOPE"]


def test_provider_is_sl_provider():
    assert sl.Provider is Provider


def test_provider_api_versions():
    assert Provider.ECB.api == "2.1"
    assert Provider.BIS.api == "2.1"
    assert Provider.SDMX_GLOBAL.api == "3.0"
