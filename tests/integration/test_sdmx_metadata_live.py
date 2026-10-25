"""Live integration tests for the reference-metadata pipeline.

Probes real, publicly accessible SDMX 3.0 endpoints. As of 2026-04-28 no
provider serves a populated MetadataSet payload (see issue #45), so the
live coverage is:

- The Global Registry serves the IAEG-SDGs SDG MSD (a real-world MSD with
  ~50 attributes). We parse it and verify round-trip.
- IMF serves a properly-shaped but empty MetadataMessage envelope. We
  parse it and verify the empty-array path.

Cassettes under tests/integration/cassettes/ replay these on subsequent
runs — no network needed after the initial recording.

To re-record: ``just record-cassettes`` (or ``uv run pytest -m live
--record-mode=all``).
"""

import httpx2
import pytest

import sdmxlib as sl

pytestmark = pytest.mark.integration

_GLOBAL_REGISTRY = sl.Provider.SDMX_GLOBAL.url
_IMF_3 = "https://api.imf.org/external/sdmx/3.0"

_ACCEPT_STRUCTURE_30 = "application/vnd.sdmx.structure+xml;version=3.0.0"
_ACCEPT_METADATA_JSON = "application/vnd.sdmx.metadata+json;version=2.0.0"


def _fetch(url: str, accept: str) -> bytes:
    # httpx2 (vs urllib) so the request uses certifi's CA bundle — IMF's chain
    # isn't in the uv-managed Python's bundled trust store.
    with httpx2.Client(timeout=30, follow_redirects=True) as client:
        response = client.get(url, headers={"Accept": accept})
        response.raise_for_status()
        return response.content


# ── Global Registry: real IAEG-SDGs SDG MSD ────────────────────────────


@pytest.mark.vcr
def test_sdg_msd_parses_from_global_registry() -> None:
    """Parse a real-world MSD with ~50 metadata attributes."""
    url = f"{_GLOBAL_REGISTRY}/structure/metadatastructure/IAEG-SDGs/SDG_MSD/1.0"
    data = _fetch(url, _ACCEPT_STRUCTURE_30)
    msg = sl.parse(data)

    assert isinstance(msg, sl.StructureMessage)
    assert len(msg.metadata_structures) == 1
    msd = msg.metadata_structures["SDG_MSD"]
    assert msd.agency_id == "IAEG-SDGs"
    assert msd.version == "1.0"
    # The published MSD has ~50 attributes; we don't pin the exact count
    # because the maintainer may add or remove attributes over time.
    assert len(msd.attributes) >= 30


@pytest.mark.vcr
def test_sdg_msd_concept_refs_resolve() -> None:
    """Each MetadataAttribute carries a Concept ref pointing into SDG_META_CONCEPTS."""
    url = f"{_GLOBAL_REGISTRY}/structure/metadatastructure/IAEG-SDGs/SDG_MSD/1.0"
    data = _fetch(url, _ACCEPT_STRUCTURE_30)
    msg = sl.parse(data)
    assert isinstance(msg, sl.StructureMessage)
    msd = msg.metadata_structures["SDG_MSD"]
    for attr in msd.attributes:
        assert attr.concept is not None, f"{attr.id} missing concept identity"
        assert attr.concept.urn.id == "SDG_META_CONCEPTS"
        assert attr.concept.urn.item_id == attr.id


@pytest.mark.vcr
def test_sdg_msd_round_trips_through_xml_writer() -> None:
    """Real-world MSD round-trips losslessly through our XML writer + reader."""
    url = f"{_GLOBAL_REGISTRY}/structure/metadatastructure/IAEG-SDGs/SDG_MSD/1.0"
    data = _fetch(url, _ACCEPT_STRUCTURE_30)
    original = sl.parse(data)
    assert isinstance(original, sl.StructureMessage)

    re_emitted = sl.to_xml(original, version="3.0")
    second = sl.parse(re_emitted)
    assert isinstance(second, sl.StructureMessage)
    assert len(second.metadata_structures) == 1
    msd_a = original.metadata_structures["SDG_MSD"]
    msd_b = second.metadata_structures["SDG_MSD"]
    assert [a.id for a in msd_a.attributes] == [a.id for a in msd_b.attributes]


# ── IMF: empty MetadataMessage envelope ────────────────────────────────


@pytest.mark.vcr
def test_imf_empty_metadata_envelope_parses() -> None:
    """IMF serves SDMX-JSON metadata envelopes; the IFS dataflow is empty.

    This validates the metadata-schema $schema dispatch path and the
    empty-metadataSets array handling. When IMF starts serving populated
    payloads (see issue #45) this test should keep passing — only the
    assertion needs to widen.
    """
    url = f"{_IMF_3}/metadata/structure/dataflow/IMF/IFS/1.0.0"
    data = _fetch(url, _ACCEPT_METADATA_JSON)
    msg = sl.parse(data)

    assert isinstance(msg, sl.MetadataMessage)
    # As of 2026-04-28 IMF returns no populated sets for IFS.
    # If this assertion ever fires, see issue #45 — we now have real data
    # to validate against.
    assert len(msg.metadata_sets) == 0
