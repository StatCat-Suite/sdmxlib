"""Integration tests for the SDMX-ML 3.0 reader against a live endpoint.

Uses the SDMX Global Registry (FMR instance) which supports SDMX 3.0 format
via the Accept: application/vnd.sdmx.structure+xml;version=3.0.0 header.

Cassettes are recorded under tests/integration/cassettes/ and replayed on
subsequent runs — no network needed after the initial recording.

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

# Content-type for SDMX-ML 3.0 structure responses
_ACCEPT_30 = "application/vnd.sdmx.structure+xml;version=3.0.0"


def _fetch_30(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"Accept": _ACCEPT_30})  # noqa: S310
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        body: bytes = resp.read()
        return body


# ── Codelist ──────────────────────────────────────────────────────────────────


@pytest.mark.vcr
def test_sdmx30_codelist_parsed():
    """Parse a single codelist from the Global Registry in SDMX 3.0 format."""
    url = f"{_GLOBAL_REGISTRY}/structure/codelist/SDMX/CL_FREQ/latest"
    data = _fetch_30(url)
    msg = parse(data)
    # `parse` returns either kind; this endpoint returns the structural one.
    assert isinstance(msg, StructureMessage)
    assert len(msg.codelists) >= 1
    cl = msg.codelists["CL_FREQ"]
    assert cl.agency_id == "SDMX"
    assert len(cl.codes) >= 1


@pytest.mark.vcr
def test_sdmx30_codelist_names():
    """Verify code names are present in at least one language."""
    url = f"{_GLOBAL_REGISTRY}/structure/codelist/SDMX/CL_FREQ/latest"
    data = _fetch_30(url)
    msg = parse(data)
    # `parse` returns either kind; this endpoint returns the structural one.
    assert isinstance(msg, StructureMessage)
    cl = msg.codelists["CL_FREQ"]
    for code in cl.codes:
        # At least one language present
        assert len(code.name) >= 1


# ── Data structure ────────────────────────────────────────────────────────────


@pytest.mark.vcr
def test_sdmx30_dsd_parsed():
    """Parse a DSD from the Global Registry in SDMX 3.0 format."""
    url = f"{_GLOBAL_REGISTRY}/structure/datastructure/ESTAT/CPI/latest?references=all"
    data = _fetch_30(url)
    msg = parse(data)
    # `parse` returns either kind; this endpoint returns the structural one.
    assert isinstance(msg, StructureMessage)
    assert len(msg.data_structures) >= 1
    dsd = msg.data_structures["CPI"]
    assert dsd.agency_id == "ESTAT"
    assert len(dsd.dimensions) >= 1


@pytest.mark.vcr
def test_sdmx30_dsd_measure_is_measure():
    """SDMX 3.0 DSDs use Measure elements (not PrimaryMeasure)."""
    url = f"{_GLOBAL_REGISTRY}/structure/datastructure/ESTAT/CPI/latest?references=all"
    data = _fetch_30(url)
    msg = parse(data)
    # `parse` returns either kind; this endpoint returns the structural one.
    assert isinstance(msg, StructureMessage)
    dsd = msg.data_structures["CPI"]
    assert len(dsd.measures) >= 1


@pytest.mark.vcr
def test_sdmx30_dsd_enumerated_dims():
    """Enumerated dimensions have Ref[Codelist] representation."""
    url = f"{_GLOBAL_REGISTRY}/structure/datastructure/ESTAT/CPI/latest?references=all"
    data = _fetch_30(url)
    msg = parse(data)
    # `parse` returns either kind; this endpoint returns the structural one.
    assert isinstance(msg, StructureMessage)
    dsd = msg.data_structures["CPI"]
    enumerated = dsd.enumerated_dimensions
    if not enumerated:
        pytest.skip("No enumerated dimensions in ESTAT:CPI")
    for dim in enumerated:
        assert isinstance(dim.representation, Ref)
