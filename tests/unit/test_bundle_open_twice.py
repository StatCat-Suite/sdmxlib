"""One bundle open twice in one process (found by GH-69's bundle tier).

DuckDB shares a database instance between connections to the same file in a
process, so a second registry over a bundle saw the first's ``lake`` and its
ATTACH failed: "database with name "lake" already exists". A server checking
or profiling a bundle it serves, or two fixtures over one bundle, hit it.
"""

from pathlib import Path

import pytest

import sdmxlib as sl
from tests.support.bundles import FLOW, make_bundle, publish

pytestmark = pytest.mark.unit


def test_a_bundle_opened_twice_reads_observations_through_both(tmp_path: Path) -> None:
    root = publish(make_bundle(tmp_path / "b", 10))
    with sl.LocalRegistry.bundle(root) as first:
        assert first.obs(FLOW.urn).count() == 10
        with sl.LocalRegistry.bundle(root) as second:
            assert second.obs(FLOW.urn).count() == 10
        assert first.obs(FLOW.urn).count() == 10, "closing the second leaves the first's lake attached"


def test_a_different_catalog_under_the_same_alias_is_refused(tmp_path: Path) -> None:
    """Skipping the ATTACH is right only when it is the same catalog; another
    would be read in place of this registry's own."""
    root = publish(make_bundle(tmp_path / "b", 10))
    other = make_bundle(tmp_path / "other", 3)
    with sl.LocalRegistry.bundle(root) as first:
        assert first.obs(FLOW.urn).count() == 10
        second = sl.LocalRegistry.from_connection(first.connection)
        second._attach = {"lake": (f"'ducklake:{other / 'lake.ducklake'}'", f"(DATA_PATH '{other / 'parquet'}/')")}
        with pytest.raises(ValueError, match=r"'lake' is attached to .* this registry wants"):
            second.obs(FLOW.urn).count()
