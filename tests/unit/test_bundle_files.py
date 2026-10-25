"""`bundle_files`: what a copy of a publication's tiers needs, for a tool sdmxlib does not run (GH-182).

The list is what rclone or azcopy is handed, so the properties that matter
are the ones a copy driven by it has: it holds every listed file and no other,
in two steps it is a publication `check_bundle` passes, and the manifest is
never in the list, since those tools copy in parallel.
"""

import shutil
from pathlib import Path

import msgspec
import pytest

from sdmxlib.bundle import MANIFEST_NAME, ManifestError, Tier, bundle_files, check_bundle, check_on_open, read_manifest
from sdmxlib.testing import Dim, Shape, synthetic_bundle

pytestmark = pytest.mark.unit

_SHAPE = Shape(dimensions=(Dim(codes=20, depth=2, fanout=(2, 4)), Dim(codes=5)), observations=300, periods=4)


@pytest.fixture(scope="module")
def published(tmp_path_factory: pytest.TempPathFactory) -> Path:
    bundle = tmp_path_factory.mktemp("files") / "census"
    return bundle / synthetic_bundle(bundle, _SHAPE, seed=7).token


def _expected(publication: Path, tiers: set[Tier]) -> list[str]:
    """The listing, derived from the manifest without `bundle_files`'s path logic."""
    manifest = read_manifest(publication)
    token = publication.name
    data = sorted(f"data/{f.path}" for f in manifest.files if f.root == "data" and f.tier in tiers)
    own = sorted(f"{token}/{f.path}" for f in manifest.files if f.root != "data" and f.tier in tiers)
    return data + own


@pytest.mark.parametrize("tiers", [(1, 2, 3), (1, 2), (3,), (1,)])
def test_it_lists_the_files_of_the_tiers_asked_for_data_first(published: Path, tiers: tuple[Tier, ...]) -> None:
    listed = bundle_files(read_manifest(published), tiers=tiers)
    assert listed == _expected(published, set(tiers))


def test_the_bundle_has_files_in_every_listed_kind(published: Path) -> None:
    """The listing tests are vacuous unless there are both data and publication files."""
    listed = bundle_files(read_manifest(published))
    # A floor: how many data files DuckLake writes for a table is its own choice.
    assert sum(p.startswith("data/") for p in listed) >= 1
    assert sum(p.startswith(f"{published.name}/") for p in listed) == 2  # meta.duckdb and lake.ducklake


def test_the_manifest_is_never_listed(published: Path) -> None:
    assert f"{published.name}/{MANIFEST_NAME}" not in bundle_files(read_manifest(published))


def _copy(listed: list[str], source: Path, target: Path) -> None:
    """What rclone does with ``--files-from``: each path, source to target, in reverse to show order is moot."""
    for path in reversed(listed):
        (target / path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / path, target / path)


def test_a_copy_of_every_tier_in_two_steps_checks_clean(published: Path, tmp_path: Path) -> None:
    bundle, target = published.parent, tmp_path / "census"
    _copy(bundle_files(read_manifest(published)), bundle, target)
    assert not (target / published.name / MANIFEST_NAME).exists(), "after the first step it is unpublished"
    _copy([f"{published.name}/{MANIFEST_NAME}"], bundle, target)
    report = check_bundle(target / published.name, at="receive")
    assert report.ok, "\n".join(map(str, report.findings))


def test_a_server_copy_of_tiers_1_and_2_reads_tier_3_from_the_store(published: Path, tmp_path: Path) -> None:
    bundle, target = published.parent, tmp_path / "census"
    _copy([*bundle_files(read_manifest(published), tiers=(1, 2)), f"{published.name}/{MANIFEST_NAME}"], bundle, target)
    assert not (target / "data").exists()
    report = check_on_open(target / published.name, stamps=tmp_path / "stamps", remote=f"file://{published}/")
    assert report.ok, "\n".join(map(str, report.findings))


def test_a_data_path_outside_the_bundle_directory_is_refused(published: Path) -> None:
    escaping = msgspec.structs.replace(read_manifest(published), data_path="../../elsewhere/")
    with pytest.raises(ManifestError, match="leads outside the bundle directory"):
        bundle_files(escaping)
