"""`synthetic_bundle` and `profile_bundle` (GH-69).

A synthetic bundle must be what its shape says, publish as a conforming
bundle, and come out the same from the same seed. Determinism is asserted
together with its discrimination check: a different seed must give a
different bundle, or "same seed, same bundle" could hold because the seed is
ignored.
"""

from collections.abc import Callable
from pathlib import Path

import attrs
import polars as pl
import pytest

import sdmxlib as sl
from sdmxlib.bundle import check_bundle, read_manifest
from sdmxlib.testing import Dim, HierarchyShape, Shape, profile_bundle, synthetic_bundle

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


def _keys(root: Path) -> pl.DataFrame:
    """The rows of a two-dimension bundle, sorted."""
    with sl.LocalRegistry.bundle(root) as reg:
        return reg.obs(_FLOW).frame().sort("DIM_1", "DIM_2", "TIME_PERIOD")


def _synthetic(root: Path, shape: Shape, *, seed: int) -> Path:
    """The publication `synthetic_bundle` writes under `root`."""
    return root / synthetic_bundle(root, shape, seed=seed).token


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _synthetic(tmp_path_factory.mktemp("synthetic") / "b", _SHAPE, seed=3)


def test_it_publishes_a_bundle_that_checks_clean(built: Path) -> None:
    report = check_bundle(built, at="publish")
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
    same = _synthetic(tmp_path / "same", _SHAPE, seed=3)
    other = _synthetic(tmp_path / "other", _SHAPE, seed=4)
    assert _rows(same).equals(_rows(built))
    assert _codes(same, 1) == _codes(built, 1)
    assert not _rows(other).equals(_rows(built)), "the seed chooses the keys"
    assert _codes(other, 1) != _codes(built, 1), "the seed chooses the hierarchy"


def test_a_profile_reads_the_shape_back(built: Path) -> None:
    profile = profile_bundle(built, _FLOW)
    assert [(d.codes, d.depth) for d in profile.dimensions] == [(400, 4), (7, 1), (12, 2)]
    low, high = profile.dimensions[0].fanout
    assert 1 <= low <= high <= 5, "a truncated last subtree may hold fewer than the least"
    assert (profile.observations, profile.periods, profile.languages, profile.attributes) == (5_000, 6, ("en", "fr"), 2)
    assert 0.15 < profile.annotated < 0.35, "a quarter of the codes, drawn per code"


def test_a_profile_builds_a_stand_in_of_the_same_shape(tmp_path: Path, built: Path) -> None:
    profile = profile_bundle(built, _FLOW)
    twin = profile_bundle(_synthetic(tmp_path / "twin", profile, seed=11), _FLOW)
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


# ── hierarchies (GH-99) ───────────────────────────────────────────────────

_HIERARCHIES = (
    HierarchyShape(coverage=1.0, depth=3, fanout=(2, 6)),
    HierarchyShape(coverage=0.4, depth=2, fanout=(5, 20), multi_parent=0.25),
    HierarchyShape(coverage=0.01, depth=1),
)
_WITH = Shape(
    dimensions=(Dim(codes=500, depth=3, fanout=(2, 9), hierarchies=_HIERARCHIES), Dim(codes=7)),
    observations=2_000,
    periods=4,
    languages=("en", "fr"),
)


@pytest.fixture(scope="module")
def hierarchical(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _synthetic(tmp_path_factory.mktemp("synthetic") / "h", _WITH, seed=5)


def _nodes(root: Path, k: int) -> list[tuple[str, str | None, int, str]]:
    """Hierarchy `k` of dimension 1: each node's code id, parent path, depth and path."""
    with sl.LocalRegistry.bundle(root) as reg:
        rows = reg.connection.execute(
            """
            SELECT split_part(code_urn, '.', -1), parent_path, depth, path FROM sdmx.hierarchy_node
            WHERE hierarchy_urn LIKE ? ORDER BY path
            """,
            [f"%H_DIM_1_{k}(%"],
        ).fetchall()
    return [(str(code), parent, int(depth), str(path)) for code, parent, depth, path in rows]


def test_a_bundle_with_hierarchies_checks_clean(hierarchical: Path) -> None:
    report = check_bundle(hierarchical, at="publish")
    assert report.ok, "\n".join(map(str, report.findings))


def test_each_hierarchy_places_its_share_of_the_codes(hierarchical: Path) -> None:
    for k, spec in enumerate(_HIERARCHIES, start=1):
        codes = {code for code, _, _, _ in _nodes(hierarchical, k)}
        assert len(codes) == max(1, round(spec.coverage * 500)), f"hierarchy {k}"
        assert codes <= {code for code, _, _ in _codes(hierarchical, 1)}, "codes of the dimension's codelist"


def test_a_smaller_hierarchy_is_a_subset_of_a_larger_one(hierarchical: Path) -> None:
    placed = [{code for code, _, _, _ in _nodes(hierarchical, k)} for k in (1, 2, 3)]
    assert placed[2] < placed[1] < placed[0]


def test_each_hierarchy_keeps_to_its_depth_and_fanout(hierarchical: Path) -> None:
    for k, spec in enumerate(_HIERARCHIES, start=1):
        nodes = _nodes(hierarchical, k)
        assert max(depth for _, _, depth, _ in nodes) + 1 <= spec.depth, f"hierarchy {k}"
        first = [parent for _, parent, _, _ in nodes if parent is not None]
        if spec.depth > 1:
            assert first, f"hierarchy {k} is a tree, not a flat list"
        if spec.multi_parent == 0:
            counts = {p: first.count(p) for p in set(first)}
            assert max(counts.values(), default=0) <= spec.fanout[1], f"hierarchy {k}"


def test_a_share_of_the_codes_sits_under_two_parents(hierarchical: Path) -> None:
    nodes = _nodes(hierarchical, 2)
    parents: dict[str, set[str | None]] = {}
    for code, parent, _, _ in nodes:
        parents.setdefault(code, set()).add(parent)
    twice = [code for code, under in parents.items() if len(under) == 2]
    non_roots = sum(1 for _, parent, _, _ in nodes if parent is not None) - len(twice)
    assert 0.15 < len(twice) / non_roots < 0.35, "a quarter of the non-root codes, drawn per code"
    assert all(len(under) == 1 for code, under in parents.items() if code not in twice)
    once = [code for code, _, _, _ in _nodes(hierarchical, 1)]
    assert len(once) == len(set(once)), "multi_parent=0 places each code once"


def test_each_hierarchy_is_associated_with_its_dimension(hierarchical: Path) -> None:
    with sl.LocalRegistry.bundle(hierarchical) as reg:
        rows = reg.connection.execute(
            """
            SELECT split_part(urn, '=', 2), split_part(linked_hierarchy_urn, '=', 2), linked_object_item_id
            FROM sdmx.hierarchy_association ORDER BY urn
            """
        ).fetchall()
    assert rows == [(f"SYNTHETIC:HA_DIM_1_{k}(1.0)", f"SYNTHETIC:H_DIM_1_{k}(1.0)", "DIM_1") for k in (1, 2, 3)]


def test_hierarchies_come_out_the_same_from_the_same_seed(tmp_path: Path, hierarchical: Path) -> None:
    same = _synthetic(tmp_path / "same", _WITH, seed=5)
    other = _synthetic(tmp_path / "other", _WITH, seed=6)
    assert [_nodes(same, k) for k in (1, 2, 3)] == [_nodes(hierarchical, k) for k in (1, 2, 3)]
    assert _nodes(other, 2) != _nodes(hierarchical, 2), "the seed chooses the hierarchies"


def test_adding_hierarchies_leaves_the_codes_and_observations_as_they_were(tmp_path: Path) -> None:
    """Hierarchies draw from a stream of their own, so a shape gains them without its bundle moving."""
    plain = Shape(dimensions=(Dim(codes=500, depth=3, fanout=(2, 9)), Dim(codes=7)), observations=2_000, periods=4)
    hierarchical = attrs.evolve(
        plain, dimensions=(attrs.evolve(plain.dimensions[0], hierarchies=_HIERARCHIES), plain.dimensions[1])
    )
    without, with_ = _synthetic(tmp_path / "plain", plain, seed=5), _synthetic(tmp_path / "with", hierarchical, seed=5)
    assert _codes(with_, 1) == _codes(without, 1)
    assert _keys(with_).equals(_keys(without))
    assert _nodes(with_, 1), "discrimination: the second bundle has hierarchies"


def test_the_manifest_records_the_hierarchies_only_when_there_are_some(hierarchical: Path, built: Path) -> None:
    recorded = read_manifest(hierarchical).product["hierarchies"]
    assert recorded == [[1, h.coverage, h.depth, list(h.fanout), h.multi_parent] for h in _HIERARCHIES]
    assert "hierarchies" not in read_manifest(built).product


@pytest.mark.parametrize(
    ("make", "message"),
    [
        (lambda: HierarchyShape(coverage=0.0), r"coverage must be in \(0, 1\], got 0.0"),
        (lambda: HierarchyShape(coverage=1.5), r"coverage must be in \(0, 1\], got 1.5"),
        (lambda: HierarchyShape(multi_parent=1.0), r"multi_parent must be in \[0, 1\), got 1.0"),
        (lambda: HierarchyShape(depth=0), "depth must be at least 1, got 0"),
    ],
)
def test_a_hierarchy_shape_out_of_range_is_refused(make: Callable[[], HierarchyShape], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        make()
