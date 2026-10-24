"""One bundle open twice in one process (found by GH-69's bundle tier).

Each bundle attaches under a prefix of its own in its engine, so two
registries over one publication in one engine share its attachment, and a
catalog of another publication under an alias already taken is refused
rather than read in place of the registry's own.
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
        lake = (f"'ducklake:{other / 'lake.ducklake'}'", f"(DATA_PATH '{other / 'parquet'}/')")
        second._attach = {first.lake_alias: lake}
        with pytest.raises(ValueError, match=r"__lake' is attached to .* this registry wants"):
            second.obs(FLOW.urn).count()
