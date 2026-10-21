"""Integration tests for LocalRegistry with real SDMX data.

Uses VCR cassettes to replay a REST fetch, then ingests into a
DuckDB-backed LocalRegistry and verifies round-trip fidelity including
lazy codelist access, Ref resolution, and code maps.

To re-record: pytest -m integration --record-mode=all
"""

import pytest

import sdmxlib as sl
from sdmxlib.local import LazyCodelist, LocalRegistry
from sdmxlib.model.ref import Ref
from tests.support.narrow import present

pytestmark = pytest.mark.integration

_ECB = sl.Provider.ECB


@pytest.mark.vcr
def test_ingest_and_get_dsd():
    """Fetch a DSD from REST, ingest into LocalRegistry, get it back."""
    with sl.RestRegistry(_ECB) as remote:
        dsd = remote.get(sl.DataStructure.urn_for(agency="ECB", id="ECB_EXR1"), resolve=True)

        with LocalRegistry(":memory:") as local:
            local.ingest(remote.registry)

            urn = sl.DataStructure.urn_for(
                agency="ECB",
                id="ECB_EXR1",
                version=present(dsd).version,
            )
            loaded = local.get(urn)

    assert loaded is not None
    assert loaded.id == "ECB_EXR1"
    assert len(loaded.dimensions) == len(present(dsd).dimensions)


@pytest.mark.vcr
def test_ingest_lazy_codelist_round_trip():
    """Ingested codelists come back as LazyCodelist with working code access."""
    with sl.RestRegistry(_ECB) as remote:
        remote.get(sl.DataStructure.urn_for(agency="ECB", id="ECB_EXR1"), resolve=True)

        with LocalRegistry(":memory:") as local:
            local.ingest(remote.registry)

            codelists = local.get_all(sl.Codelist)
            assert len(codelists) >= 1
            for cl in codelists:
                assert isinstance(cl, LazyCodelist)
                assert len(cl) >= 1


@pytest.mark.vcr
def test_ingest_ref_resolution():
    """After ingest, DSD Refs resolve to LazyCodelists from DuckDB."""
    with sl.RestRegistry(_ECB) as remote:
        dsd = remote.get(sl.DataStructure.urn_for(agency="ECB", id="ECB_EXR1"), resolve=True)

        with LocalRegistry(":memory:") as local:
            local.ingest(remote.registry)

            urn = sl.DataStructure.urn_for(
                agency="ECB",
                id="ECB_EXR1",
                version=present(dsd).version,
            )
            loaded = local.get(urn)

            for dim in present(loaded).dimensions:
                if isinstance(dim.representation, Ref) and dim.representation.resolved:
                    cl = dim.representation()
                    assert isinstance(cl, LazyCodelist)
                    assert len(cl) >= 1


@pytest.mark.vcr
def test_ingest_code_map():
    """code_map() returns labels after ingest without creating Code objects."""
    with sl.RestRegistry(_ECB) as remote:
        dsd = remote.get(sl.DataStructure.urn_for(agency="ECB", id="ECB_EXR1"), resolve=True)

        with LocalRegistry(":memory:") as local:
            local.ingest(remote.registry)

            urn = sl.DataStructure.urn_for(
                agency="ECB",
                id="ECB_EXR1",
                version=present(dsd).version,
            )
            cm = local.dsd_code_map(urn, lang="en")

    assert len(cm) >= 1
    for codes in cm.values():
        assert isinstance(codes, dict)
        assert len(codes) >= 1


@pytest.mark.vcr
def test_ingest_dsd_traversal():
    """Full DSD traversal works after ingest — code_map, column_labels."""
    with sl.RestRegistry(_ECB) as remote:
        dsd = remote.get(sl.DataStructure.urn_for(agency="ECB", id="ECB_EXR1"), resolve=True)

        with LocalRegistry(":memory:") as local:
            local.ingest(remote.registry)

            urn = sl.DataStructure.urn_for(
                agency="ECB",
                id="ECB_EXR1",
                version=present(dsd).version,
            )
            loaded = local.get(urn)

            cm = present(loaded).code_map(lang="en")
            labels = present(loaded).column_labels(lang="en")

    assert len(cm) >= 1
    assert len(labels) >= 1
