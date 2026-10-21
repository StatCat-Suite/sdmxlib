"""Integration tests against live SDMX REST endpoints.

Cassettes are recorded under tests/cassettes/ and replayed on subsequent
runs — no network needed after the initial recording.

To re-record: just record-cassettes
"""

import urllib.request

import pytest

import sdmxlib as sl
from sdmxlib.formats import parse
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import Ref

pytestmark = pytest.mark.integration

_ECB = sl.Provider.ECB.url

# ── Helpers ───────────────────────────────────────────────────────────────────


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"Accept": "application/xml"})  # noqa: S310
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        body: bytes = resp.read()
        return body


# ── ECB standalone artefact endpoints ────────────────────────────────────────


@pytest.mark.vcr
def test_ecb_standalone_codelist():
    """Parse a single codelist from the ECB endpoint."""
    url = f"{_ECB}/codelist/ECB/CL_CURRENCY"
    data = _fetch(url)
    msg = parse(data)
    # `parse` returns either message kind; this endpoint returns the
    # structural one, and saying so narrows it as well as asserting it.
    assert isinstance(msg, StructureMessage)
    assert len(msg.codelists) >= 1
    cl = msg.codelists["CL_CURRENCY"]
    assert cl.agency_id == "ECB"
    assert len(cl.codes) >= 1
    assert cl.codes[0].name.get("en") is not None


@pytest.mark.vcr
def test_ecb_standalone_codelist_hierarchy():
    """Verify codelist codes and roots work on a real codelist."""
    url = f"{_ECB}/codelist/ECB/CL_CURRENCY"
    data = _fetch(url)
    msg = parse(data)
    # `parse` returns either message kind; this endpoint returns the
    # structural one, and saying so narrows it as well as asserting it.
    assert isinstance(msg, StructureMessage)
    cl = msg.codelists["CL_CURRENCY"]
    roots = cl.roots()
    assert len(roots) >= 1
    for code in roots:
        assert code.parent_id is None


# ── ECB DSD and dataflow endpoints ────────────────────────────────────────────


@pytest.mark.vcr
def test_ecb_dsd_with_resolution():
    """Parse an ECB DSD and verify cross-reference resolution."""
    url = f"{_ECB}/datastructure/ECB/ECB_EXR1?references=all"
    data = _fetch(url)
    msg = parse(data)
    # `parse` returns either message kind; this endpoint returns the
    # structural one, and saying so narrows it as well as asserting it.
    assert isinstance(msg, StructureMessage)

    assert len(msg.data_structures) >= 1
    dsd = msg.data_structures["ECB_EXR1"]
    assert dsd.agency_id == "ECB"

    enumerated = dsd.enumerated_dimensions
    assert len(enumerated) >= 1

    for dim in enumerated:
        if isinstance(dim.representation, Ref) and dim.representation.resolved:
            codes = dim.codes
            assert codes is not None
            assert len(codes) >= 1
            break
    else:
        pytest.skip("No resolved codelists in ECB_EXR1 response")


@pytest.mark.vcr
def test_ecb_dataflow():
    """Parse ECB dataflows and verify structure ref resolution."""
    url = f"{_ECB}/dataflow/ECB?references=datastructure"
    data = _fetch(url)
    msg = parse(data)
    # `parse` returns either message kind; this endpoint returns the
    # structural one, and saying so narrows it as well as asserting it.
    assert isinstance(msg, StructureMessage)

    assert len(msg.dataflows) >= 1
    for df in msg.dataflows:
        if df.structure and df.structure.resolved:
            dsd = df.structure()
            assert dsd.id
            return

    pytest.skip("No resolved dataflow structures in ECB response")


@pytest.mark.vcr
def test_ecb_dsd_enumeration_types():
    """Verify enumerated dimensions have Ref[Codelist] representation."""
    url = f"{_ECB}/datastructure/ECB/ECB_EXR1?references=all"
    data = _fetch(url)
    msg = parse(data)
    # `parse` returns either message kind; this endpoint returns the
    # structural one, and saying so narrows it as well as asserting it.
    assert isinstance(msg, StructureMessage)
    dsd = msg.data_structures["ECB_EXR1"]

    for dim in dsd.enumerated_dimensions:
        assert isinstance(dim.representation, Ref)


@pytest.mark.vcr
def test_ecb_code_map():
    """Parse ECB DSD and build a code map."""
    url = f"{_ECB}/datastructure/ECB/ECB_EXR1?references=all"
    data = _fetch(url)
    msg = parse(data)
    # `parse` returns either message kind; this endpoint returns the
    # structural one, and saying so narrows it as well as asserting it.
    assert isinstance(msg, StructureMessage)
    dsd = msg.data_structures["ECB_EXR1"]

    code_map = dsd.code_map(lang="en")
    assert len(code_map) >= 1
    for codes in code_map.values():
        assert len(codes) >= 1
        break
