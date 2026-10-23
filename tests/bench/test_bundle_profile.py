"""`profile_bundle` on the real bundle (GH-69): counts it reads agree with the manifest's.

The profile is what a stress bundle that mimics this one is built from, so it
has to read the real bundle's shape, not a plausible one. Its observation
count is checked against the manifest's (DuckLake's count, through another
path), and each dimension's code count against the store.
"""

import pytest

import sdmxlib as sl
from sdmxlib.bundle import read_manifest
from sdmxlib.testing import profile_bundle

pytestmark = [pytest.mark.bench, pytest.mark.bundle]


def test_the_profile_agrees_with_the_manifest_and_the_store(data_plane_bundle, data_plane_registry):
    (binding,) = read_manifest(data_plane_bundle).bindings
    flow = sl.Dataflow.parse_urn(binding.dataflow)
    profile = profile_bundle(data_plane_bundle, flow)
    assert profile.observations == binding.observations
    dsd = data_plane_registry.dsd(flow)
    enumerated = [d for d in dsd.dimensions if isinstance(d.representation, sl.Ref)]
    counts = [
        data_plane_registry.connection.execute(
            "SELECT count(*) FROM sdmx.code WHERE codelist_urn = ?", [str(d.representation.urn)]
        ).fetchone()[0]
        for d in enumerated
    ]
    assert [d.codes for d in profile.dimensions] == [c for c in counts if c]
    assert profile.languages
