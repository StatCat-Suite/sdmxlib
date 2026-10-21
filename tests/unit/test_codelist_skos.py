"""Unit tests for `Codelist` SKOS-style hierarchy methods.

Covers ``roots``, ``leaves``, ``narrower``, ``broader``,
``narrower_transitive``, ``broader_transitive``, ``depth_of`` — the
parent-id-driven navigation surface on ``Codelist``. Closes #124.

Fixture shape::

    A (root)
    ├── A1
    │   ├── A11 (leaf)
    │   └── A12 (leaf)
    └── A2 (leaf)
    B (root, also leaf)
"""

import pytest

from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.collections import ItemList
from sdmxlib.model.organisation import Agency
from tests.support.narrow import present
from tests.support.unchecked import unchecked

pytestmark = pytest.mark.unit


@pytest.fixture
def cl() -> Codelist:
    codes = [
        Code(name={"en": "A"}, id="A"),
        Code(name={"en": "A1"}, id="A1", parent_id="A"),
        Code(name={"en": "A11"}, id="A11", parent_id="A1"),
        Code(name={"en": "A12"}, id="A12", parent_id="A1"),
        Code(name={"en": "A2"}, id="A2", parent_id="A"),
        Code(name={"en": "B"}, id="B"),
    ]
    return Codelist(
        name={"en": "CL_TEST"}, id="CL_TEST", maintainer=Agency(id="TEST"), version="1.0", codes=ItemList(codes)
    )


# ── roots / leaves ───────────────────────────────────────────────────────────


def test_roots_returns_codes_with_no_parent(cl: Codelist) -> None:
    assert cl.roots().ids() == ["A", "B"]


def test_leaves_returns_codes_with_no_children(cl: Codelist) -> None:
    # A11, A12, A2 are leaves of the A subtree; B is a root that's also a leaf.
    assert sorted(cl.leaves().ids()) == ["A11", "A12", "A2", "B"]


def test_roots_empty_codelist() -> None:
    empty = Codelist(name={"en": "EMPTY"}, id="EMPTY", maintainer=Agency(id="TEST"))
    assert list(empty.roots()) == []


def test_leaves_empty_codelist() -> None:
    empty = Codelist(name={"en": "EMPTY"}, id="EMPTY", maintainer=Agency(id="TEST"))
    assert list(empty.leaves()) == []


# ── narrower (direct children) ───────────────────────────────────────────────


def test_narrower_returns_direct_children(cl: Codelist) -> None:
    assert sorted(cl.narrower("A").ids()) == ["A1", "A2"]


def test_narrower_of_leaf_is_empty(cl: Codelist) -> None:
    assert list(cl.narrower("A11")) == []


def test_narrower_of_missing_id_is_empty(cl: Codelist) -> None:
    assert list(cl.narrower("NOT_REAL")) == []


# ── broader (direct parent) ──────────────────────────────────────────────────


def test_broader_returns_direct_parent(cl: Codelist) -> None:
    parent = cl.broader("A11")
    assert parent is not None
    assert parent.id == "A1"


def test_broader_of_root_is_none(cl: Codelist) -> None:
    assert cl.broader("A") is None


def test_broader_of_missing_id_is_none(cl: Codelist) -> None:
    assert cl.broader("NOT_REAL") is None


# ── narrower_transitive (all descendants) ────────────────────────────────────


def test_narrower_transitive_returns_all_descendants(cl: Codelist) -> None:
    assert sorted(cl.narrower_transitive("A").ids()) == ["A1", "A11", "A12", "A2"]


def test_narrower_transitive_depth_1_is_direct_children(cl: Codelist) -> None:
    assert sorted(cl.narrower_transitive("A", depth=1).ids()) == ["A1", "A2"]


def test_narrower_transitive_depth_2(cl: Codelist) -> None:
    assert sorted(cl.narrower_transitive("A", depth=2).ids()) == ["A1", "A11", "A12", "A2"]


def test_narrower_transitive_of_leaf_is_empty(cl: Codelist) -> None:
    assert list(cl.narrower_transitive("A11")) == []


def test_narrower_transitive_of_missing_id_is_empty(cl: Codelist) -> None:
    assert list(cl.narrower_transitive("NOT_REAL")) == []


def test_narrower_transitive_cycle_is_safe() -> None:
    """A pathological self-cycle must terminate (the ``seen`` set guards us)."""
    codes = [
        Code(name={"en": "X"}, id="X", parent_id="Y"),
        Code(name={"en": "Y"}, id="Y", parent_id="X"),
    ]
    # Unchecked: a codelist with a cycle is refused as it is built (#492); navigation
    # must still survive one, built so to test it.
    with unchecked():
        cl = Codelist(name={"en": "CYCLE"}, id="CYCLE", maintainer=Agency(id="TEST"), codes=ItemList(codes))
    result = cl.narrower_transitive("X")
    # Both reachable from X via Y → X; cycle protection clamps to the unseen set.
    assert sorted(result.ids()) == ["Y"]


# ── broader_transitive (all ancestors) ───────────────────────────────────────


def test_broader_transitive_returns_all_ancestors(cl: Codelist) -> None:
    # A11 → A1 → A (root-first via parent chain → returned root-last)
    assert cl.broader_transitive("A11").ids() == ["A1", "A"]


def test_broader_transitive_depth_1_is_direct_parent(cl: Codelist) -> None:
    assert cl.broader_transitive("A11", depth=1).ids() == ["A1"]


def test_broader_transitive_of_root_is_empty(cl: Codelist) -> None:
    assert list(cl.broader_transitive("A")) == []


def test_broader_transitive_of_missing_id_is_empty(cl: Codelist) -> None:
    assert list(cl.broader_transitive("NOT_REAL")) == []


def test_broader_transitive_dangling_parent_id_stops_walking() -> None:
    """If a code names a parent_id that doesn't exist in the codelist, the
    ancestor walk stops cleanly rather than raising."""
    codes = [Code(name={"en": "X"}, id="X", parent_id="DANGLING")]
    # Unchecked: a codelist with a dangling parent is refused as it is built (#492); navigation
    # must still survive one, built so to test it.
    with unchecked():
        cl = Codelist(name={"en": "ORPHAN"}, id="ORPHAN", maintainer=Agency(id="TEST"), codes=ItemList(codes))
    assert list(cl.broader_transitive("X")) == []


# ── depth_of ─────────────────────────────────────────────────────────────────


def test_depth_of_root_is_zero(cl: Codelist) -> None:
    assert cl.depth_of("A") == 0
    assert cl.depth_of("B") == 0


def test_depth_of_first_level(cl: Codelist) -> None:
    assert cl.depth_of("A1") == 1
    assert cl.depth_of("A2") == 1


def test_depth_of_second_level(cl: Codelist) -> None:
    assert cl.depth_of("A11") == 2
    assert cl.depth_of("A12") == 2


def test_depth_of_missing_id_raises(cl: Codelist) -> None:
    with pytest.raises(KeyError, match="NOT_REAL"):
        cl.depth_of("NOT_REAL")


def test_depth_of_dangling_parent_stops_walking() -> None:
    """A code whose parent_id doesn't exist counts the steps it could
    take before the chain breaks — here, 0 because we never find the
    parent."""
    codes = [Code(name={"en": "X"}, id="X", parent_id="DANGLING")]
    # Unchecked: a codelist with a dangling parent is refused as it is built (#492); navigation
    # must still survive one, built so to test it.
    with unchecked():
        cl = Codelist(name={"en": "ORPHAN"}, id="ORPHAN", maintainer=Agency(id="TEST"), codes=ItemList(codes))
    # The implementation breaks out as soon as `parent` lookup returns None;
    # we never increment, so depth = 0.
    assert cl.depth_of("X") == 0


# ── include_self, matching the lazy walks ────────────────────────────────────


def test_narrower_transitive_can_include_the_node_itself(cl: Codelist) -> None:
    """`LazyCodelist` has taken `include_self` since the query language landed;
    the eager walk did not, so the hierarchies page's "self-inclusion is
    explicit everywhere" was false of half the surface. Found by executing
    that page's own example.
    """
    assert [c.id for c in cl.narrower_transitive("A1")] == ["A11", "A12"]
    assert [c.id for c in cl.narrower_transitive("A1", include_self=True)] == ["A1", "A11", "A12"]


def test_broader_transitive_can_include_the_node_itself(cl: Codelist) -> None:
    """Leaf-to-root, so the node leads."""
    assert [c.id for c in cl.broader_transitive("A11")] == ["A1", "A"]
    assert [c.id for c in cl.broader_transitive("A11", include_self=True)] == ["A11", "A1", "A"]


def test_include_self_on_an_unknown_id_adds_nothing(cl: Codelist) -> None:
    """A missing node has no self to include — and must not become a `None`
    entry in a list of `Code`."""
    assert list(cl.narrower_transitive("NOPE", include_self=True)) == []
    assert list(cl.broader_transitive("NOPE", include_self=True)) == []


def test_include_self_does_not_double_count_a_root(cl: Codelist) -> None:
    """`B` is both a root and a leaf, so it is where an off-by-one shows."""
    assert [c.id for c in cl.narrower_transitive("B", include_self=True)] == ["B"]
    assert [c.id for c in cl.broader_transitive("B", include_self=True)] == ["B"]


def test_include_self_agrees_with_the_lazy_walk() -> None:
    """The two implementations are separate code, and the page presents them
    as one API. Differentially checked rather than asserted twice."""
    import sdmxlib as sl

    codelist = sl.Codelist(
        id="CL_T",
        maintainer="AG",
        version="1.0",
        name={"en": "t"},
        codes=[
            sl.Code(id="A", name={"en": "a"}),
            sl.Code(id="A1", name={"en": "a1"}, parent_id="A"),
            sl.Code(id="A11", name={"en": "a11"}, parent_id="A1"),
        ],
    )
    with sl.LocalRegistry(":memory:") as local:
        local.add(codelist)
        lazy = local.get(codelist.urn)
        for include_self in (False, True):
            eager_down = [c.id for c in codelist.narrower_transitive("A", include_self=include_self)]
            assert sorted(eager_down) == sorted(present(lazy).narrower_transitive("A", include_self=include_self).ids())
            eager_up = [c.id for c in codelist.broader_transitive("A11", include_self=include_self)]
            assert sorted(eager_up) == sorted(present(lazy).broader_transitive("A11", include_self=include_self).ids())
