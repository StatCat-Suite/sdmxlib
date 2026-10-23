"""`synthetic_bundle` and `profile_bundle` (GH-69).

A synthetic bundle must be what its shape says, publish as a conforming
bundle, and come out the same from the same seed. Determinism is asserted
together with its discrimination check: a different seed must give a
different bundle, or "same seed, same bundle" could hold because the seed is
ignored.
"""

from pathlib import Path

import polars as pl
import pytest

import sdmxlib as sl
from sdmxlib.bundle import check_bundle
from sdmxlib.testing import Dim, Shape, profile_bundle, synthetic_bundle

pytestmark = pytest.mark.unit

_FLOW = sl.Dataflow.urn_for(agency="SYNTHETIC", id="DF_SYNTHETIC", version="1.0")
_SHAPE = Shape(
    dimensions=(Dim(codes=400, depth=4, fanout=(2, 5)), Dim(codes=7), Dim(codes=12, depth=2, fanout=(3, 3))),
    observations=5_000,
    periods=6,
    languages=("en", "fr"),
    attributes=2,
    annotated=0.25,
)


def _rows(root: Path) -> pl.DataFrame:
    with sl.LocalRegistry.bundle(root) as reg:
        return reg.obs(_FLOW).frame().sort("DIM_1", "DIM_2", "DIM_3", "TIME_PERIOD")


def _codes(root: Path, n: int) -> list[tuple[str, str | None, int]]:
    with sl.LocalRegistry.bundle(root) as reg:
        rows = reg.connection.execute(
            "SELECT code_id, parent_id, depth FROM sdmx.code WHERE codelist_urn LIKE ? ORDER BY code_id",
            [f"%CL_DIM_{n}(%"],
        ).fetchall()
    return [(code_id, parent, depth) for code_id, parent, depth in rows]


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("synthetic") / "b"
    synthetic_bundle(root, _SHAPE, seed=3)
    return root


def test_it_publishes_a_bundle_that_checks_clean(built: Path) -> None:
    report = check_bundle(built)
    assert report.ok, "\n".join(map(str, report.findings))


def test_it_is_the_shape_it_was_asked_for(built: Path) -> None:
    rows = _rows(built)
    assert rows.height == _SHAPE.observations
    assert rows.select("DIM_1", "DIM_2", "DIM_3", "TIME_PERIOD").n_unique() == rows.height, "no key twice"
    assert rows.get_column("TIME_PERIOD").n_unique() == _SHAPE.periods
    assert [len(_codes(built, n)) for n in (1, 2, 3)] == [400, 7, 12]
    assert {c for c in rows.columns if c.startswith("ATTR_")} == {"ATTR_1", "ATTR_2"}


def test_a_hierarchy_keeps_to_its_depth_and_fanout(built: Path) -> None:
    codes = _codes(built, 1)
    assert max(depth for _, _, depth in codes) + 1 == 4
    children: dict[str, int] = {}
    for _, parent, _ in codes:
        if parent is not None:
            children[parent] = children.get(parent, 0) + 1
    assert children, "a hierarchy, not a flat list"
    assert max(children.values()) <= 5
    assert all(depth == 0 for _, parent, depth in _codes(built, 2) if parent is None), "a flat dimension is flat"
    assert all(parent is None for _, parent, _ in _codes(built, 2))


def test_the_same_seed_gives_the_same_bundle_and_another_seed_does_not(tmp_path: Path, built: Path) -> None:
    synthetic_bundle(tmp_path / "same", _SHAPE, seed=3)
    synthetic_bundle(tmp_path / "other", _SHAPE, seed=4)
    assert _rows(tmp_path / "same").equals(_rows(built))
    assert _codes(tmp_path / "same", 1) == _codes(built, 1)
    assert not _rows(tmp_path / "other").equals(_rows(built)), "the seed chooses the keys"
    assert _codes(tmp_path / "other", 1) != _codes(built, 1), "the seed chooses the hierarchy"


def test_a_profile_reads_the_shape_back(built: Path) -> None:
    profile = profile_bundle(built, _FLOW)
    assert [(d.codes, d.depth) for d in profile.dimensions] == [(400, 4), (7, 1), (12, 2)]
    low, high = profile.dimensions[0].fanout
    assert 1 <= low <= high <= 5, "a truncated last subtree may hold fewer than the least"
    assert (profile.observations, profile.periods, profile.languages, profile.attributes) == (5_000, 6, ("en", "fr"), 2)
    assert 0.15 < profile.annotated < 0.35, "a quarter of the codes, drawn per code"


def test_a_profile_builds_a_stand_in_of_the_same_shape(tmp_path: Path, built: Path) -> None:
    profile = profile_bundle(built, _FLOW)
    synthetic_bundle(tmp_path / "twin", profile, seed=11)
    twin = profile_bundle(tmp_path / "twin", _FLOW)
    assert [(d.codes, d.depth) for d in twin.dimensions] == [(d.codes, d.depth) for d in profile.dimensions]
    assert (twin.observations, twin.periods, twin.languages, twin.attributes) == (
        profile.observations,
        profile.periods,
        profile.languages,
        profile.attributes,
    )


def test_more_observations_than_keys_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"6 observations do not fit a key space of 4 \(2 x 2\)"):
        synthetic_bundle(tmp_path / "b", Shape(dimensions=(Dim(codes=2),), observations=6, periods=2), seed=0)


def test_a_directory_that_is_not_empty_is_refused(tmp_path: Path) -> None:
    (tmp_path / "x").write_text("x")
    with pytest.raises(FileExistsError, match="not empty"):
        synthetic_bundle(tmp_path, _SHAPE, seed=0)
