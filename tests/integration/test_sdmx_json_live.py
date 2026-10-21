"""Integration tests for the SDMX-JSON reader against a live endpoint.

Uses the SDMX Global Registry which supports SDMX-JSON 2.0 via the
Accept: application/vnd.sdmx.structure+json;version=2.0.0 header.

Cassettes are recorded under tests/integration/cassettes/ and replayed
on subsequent runs — no network needed after the initial recording.

To re-record: just record-cassettes
"""

import urllib.request

import pytest

import sdmxlib as sl
from sdmxlib.formats import parse
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import Ref

pytestmark = pytest.mark.integration

_GLOBAL_REGISTRY = sl.Provider.SDMX_GLOBAL.url
_ACCEPT_JSON = "application/vnd.sdmx.structure+json;version=2.0.0"


@pytest.fixture(scope="module")
def vcr_config():
    return {
        "match_on": ["method", "scheme", "host", "port", "path", "query", "accept"],
    }


def _fetch_json(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"Accept": _ACCEPT_JSON})  # noqa: S310
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        body: bytes = resp.read()
        return body


# ── Codelist ──────────────────────────────────────────────────────────────────


@pytest.mark.vcr
def test_sdmx_json_codelist_parsed():
    """Parse a codelist from the Global Registry as SDMX-JSON."""
    url = f"{_GLOBAL_REGISTRY}/structure/codelist/SDMX/CL_FREQ/latest"
    msg = parse(_fetch_json(url))
    # `parse` returns either message kind; this endpoint returns the
    # structural one, and saying so narrows it as well as asserting it.
    assert isinstance(msg, StructureMessage)
    assert len(msg.codelists) >= 1
    cl = msg.codelists["CL_FREQ"]
    assert cl.agency_id == "SDMX"
    assert len(cl.codes) >= 1


@pytest.mark.vcr
def test_sdmx_json_codelist_code_names():
    """Verify each code has a name in at least one language."""
    url = f"{_GLOBAL_REGISTRY}/structure/codelist/SDMX/CL_FREQ/latest"
    msg = parse(_fetch_json(url))
    # `parse` returns either message kind; this endpoint returns the
    # structural one, and saying so narrows it as well as asserting it.
    assert isinstance(msg, StructureMessage)
    cl = msg.codelists["CL_FREQ"]
    for code in cl.codes:
        assert len(code.name) >= 1


# ── Data structure ────────────────────────────────────────────────────────────


@pytest.mark.vcr
def test_sdmx_json_dsd_parsed():
    """Parse a DSD with references from the Global Registry as SDMX-JSON."""
    url = f"{_GLOBAL_REGISTRY}/structure/datastructure/ESTAT/CPI/latest?references=all"
    msg = parse(_fetch_json(url))
    # `parse` returns either message kind; this endpoint returns the
    # structural one, and saying so narrows it as well as asserting it.
    assert isinstance(msg, StructureMessage)
    assert len(msg.data_structures) >= 1
    dsd = msg.data_structures["CPI"]
    assert dsd.agency_id == "ESTAT"
    assert len(dsd.dimensions) >= 1


@pytest.mark.vcr
def test_sdmx_json_dsd_has_measure():
    """DSD has at least one measure."""
    url = f"{_GLOBAL_REGISTRY}/structure/datastructure/ESTAT/CPI/latest?references=all"
    msg = parse(_fetch_json(url))
    # `parse` returns either message kind; this endpoint returns the
    # structural one, and saying so narrows it as well as asserting it.
    assert isinstance(msg, StructureMessage)
    dsd = msg.data_structures["CPI"]
    assert len(dsd.measures) >= 1


@pytest.mark.vcr
def test_sdmx_json_dsd_enumerated_dims():
    """Enumerated dimensions have resolved codelists."""
    url = f"{_GLOBAL_REGISTRY}/structure/datastructure/ESTAT/CPI/latest?references=all"
    msg = parse(_fetch_json(url))
    # `parse` returns either message kind; this endpoint returns the
    # structural one, and saying so narrows it as well as asserting it.
    assert isinstance(msg, StructureMessage)
    dsd = msg.data_structures["CPI"]
    enumerated = dsd.enumerated_dimensions
    if not enumerated:
        pytest.skip("No enumerated dimensions in ESTAT:CPI")
    for dim in enumerated:
        assert isinstance(dim.representation, Ref)
        assert dim.representation.resolved
