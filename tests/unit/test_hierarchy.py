"""Tests for Hierarchy and HierarchicalCode domain objects."""

import pytest

from sdmxlib.model.codelist import Code
from sdmxlib.model.collections import ItemList
from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy, Level
from sdmxlib.model.ref import Ref
from sdmxlib.model.validation import ValidationError
from tests.support.factories import code_ref, code_urn

pytestmark = pytest.mark.unit


def _make_hierarchy() -> Hierarchy:
    """Build a simple geographic hierarchy for testing."""
    return Hierarchy(
        id="H_GEO",
        maintainer="TEST",
        version="1.0",
        name={"en": "Geographic Hierarchy"},
        tree=[
            HierarchicalCode(
                id="CANADA",
                code=Ref[Code].unresolved(code_urn("CL_GEO", "CA", agency="TEST")),
                children=[
                    HierarchicalCode(
                        id="ON",
                        code=Ref[Code].unresolved(code_urn("CL_GEO", "ON", agency="TEST")),
                        children=[
                            HierarchicalCode(
                                id="OTTAWA",
                                code=Ref[Code].unresolved(code_urn("CL_GEO", "OTTAWA", agency="TEST")),
                            ),
                            HierarchicalCode(
                                id="TORONTO",
                                code=Ref[Code].unresolved(code_urn("CL_GEO", "TORONTO", agency="TEST")),
                            ),
                        ],
                    ),
                    HierarchicalCode(
                        id="QC",
                        code=Ref[Code].unresolved(code_urn("CL_GEO", "QC", agency="TEST")),
                    ),
                ],
            ),
        ],
    )


# ── Construction ─────────────────────────────────────────────────────────────


def test_basic_construction():
    h = _make_hierarchy()
    assert h.id == "H_GEO"
    assert h.agency_id == "TEST"
    assert h.name.get("en") == "Geographic Hierarchy"


def test_tree_roots():
    h = _make_hierarchy()
    assert len(h.tree) == 1
    assert h.tree["CANADA"].id == "CANADA"


def test_tree_children():
    h = _make_hierarchy()
    canada = h.tree["CANADA"]
    assert len(canada.children) == 2
    assert canada.children["ON"].id == "ON"
    assert canada.children["QC"].id == "QC"


def test_nested_children():
    h = _make_hierarchy()
    ontario = h.tree["CANADA"].children["ON"]
    assert len(ontario.children) == 2
    assert ontario.children["OTTAWA"].id == "OTTAWA"


# ── Flat index ───────────────────────────────────────────────────────────────


def test_flat_index_all_nodes():
    h = _make_hierarchy()
    assert len(h.codes) == 5  # CANADA, ON, QC, OTTAWA, TORONTO


def test_lookup_is_by_path():
    h = _make_hierarchy()
    assert h["CANADA.ON.OTTAWA"].id == "OTTAWA"
    assert h["CANADA.QC"].id == "QC"
    assert h.get("CANADA.ON.NOPE") is None


def test_a_bare_id_names_a_root_only():
    """pysdmx's rule: a nested node is reached by its path, never its leaf (#439)."""
    h = _make_hierarchy()
    assert "CANADA" in h
    assert "CANADA.ON.OTTAWA" in h
    assert "OTTAWA" not in h
    with pytest.raises(KeyError):
        h["OTTAWA"]


def test_by_id_finds_every_node_with_the_leaf():
    h = Hierarchy(
        name={"en": "H"},
        id="H",
        maintainer="T",
        tree=[
            HierarchicalCode(id="MR", children=[HierarchicalCode(id="X", code=code_ref("X"))], code=code_ref("MR")),
            HierarchicalCode(
                id="TYPO_METRO", children=[HierarchicalCode(id="MR", code=code_ref("MR"))], code=code_ref("TYPO_METRO")
            ),
        ],
    )
    assert [h.path_of(n) for n in h.by_id("MR")] == ["MR", "TYPO_METRO.MR"]
    assert list(h.by_id("NOPE")) == []


def test_root_not_in_tree_by_leaf():
    h = _make_hierarchy()
    assert "OTTAWA" not in h.tree
    assert "CANADA" in h.tree


# ── Code refs ────────────────────────────────────────────────────────────────


def test_code_ref_is_ref():
    h = _make_hierarchy()
    node = h["CANADA"]
    assert isinstance(node.code, Ref)
    assert not node.code.resolved


# ── ItemList coercion ────────────────────────────────────────────────────────


def test_list_coercion_on_tree():
    h = Hierarchy(name={"en": "H"}, id="H", maintainer="T", tree=[HierarchicalCode(id="A", code=code_ref("A"))])
    assert isinstance(h.tree, ItemList)
    assert len(h.tree) == 1


def test_list_coercion_on_children():
    node = HierarchicalCode(id="A", children=[HierarchicalCode(id="B", code=code_ref("B"))], code=code_ref("A"))
    assert isinstance(node.children, ItemList)
    assert len(node.children) == 1


# ── Empty hierarchy ──────────────────────────────────────────────────────────


def test_empty_hierarchy():
    h = Hierarchy(name={"en": "H_EMPTY"}, id="H_EMPTY", maintainer="T")
    assert len(h.tree) == 0
    assert len(h.codes) == 0


# ── Validation ───────────────────────────────────────────────────────────────


def test_validate_valid_hierarchy():
    _make_hierarchy()


def test_validate_bad_id():
    with pytest.raises(ValidationError) as exc:
        Hierarchy(name={"en": "bad id"}, id="bad id", maintainer="TEST")
    assert [i.path for i in exc.value.issues] == ["Hierarchy.id"]


# ── Level (SDMX 3.1) ───────────────────────────────────────────────────────


def test_level_construction():
    lv = Level(id="SECTOR", name={"en": "Sector"})
    assert lv.id == "SECTOR"
    assert lv.name.get("en") == "Sector"


def test_hierarchy_with_levels():
    levels = [
        Level(id="SECTOR", name={"en": "Sector"}),
        Level(id="SUBSEC", name={"en": "Subsector"}),
    ]
    h = Hierarchy(name={"en": "H_NAICS"}, id="H_NAICS", maintainer="TEST", levels=levels)
    assert len(h.levels) == 2
    assert h.levels["SECTOR"].id == "SECTOR"
    assert h.levels["SUBSEC"].name.get("en") == "Subsector"


def test_has_formal_levels_default_false():
    """A hierarchy carrying levels is not automatically *formally* leveled.

    SDMX 3.1's ``hasFormalLevels`` distinguishes a hierarchy where every
    code lives in a named level from one where the level metadata is
    descriptive only. Defaults to False so old fixtures parse unchanged.
    """
    h = Hierarchy(
        name={"en": "H"},
        id="H",
        maintainer="T",
        levels=[Level(id="L1")],
        tree=[HierarchicalCode(id="A", code=code_ref("A"))],
    )
    assert h.has_formal_levels is False


def test_has_formal_levels_true_when_set():
    h = Hierarchy(
        name={"en": "H"},
        id="H",
        maintainer="T",
        has_formal_levels=True,
        levels=[Level(id="L1")],
        tree=[HierarchicalCode(id="A", code=code_ref("A"))],
    )
    assert h.has_formal_levels is True


def test_has_formal_levels_independent_of_levels_list():
    """A formally-leveled hierarchy can have an empty levels list (degenerate)
    and a value-based hierarchy can carry descriptive levels."""
    formal_no_levels = Hierarchy(name={"en": "H1"}, id="H1", maintainer="T", has_formal_levels=True)
    value_with_levels = Hierarchy(name={"en": "H2"}, id="H2", maintainer="T", levels=[Level(id="L1")])
    assert formal_no_levels.has_formal_levels is True
    assert value_with_levels.has_formal_levels is False


def test_hierarchical_code_level_ref():
    lv = Level(id="SECTOR", name={"en": "Sector"})
    node = HierarchicalCode(
        id="11",
        level=lv,
        code=code_ref("11"),
    )
    assert node.level is not None
    assert node.level.id == "SECTOR"


# ── SKOS / tree navigation ──────────────────────────────────────────────────


def test_hierarchy_roots_matches_tree():
    h = _make_hierarchy()
    assert [n.id for n in h.roots()] == [n.id for n in h.tree]


def test_hierarchy_leaves():
    h = _make_hierarchy()
    leaf_ids = {n.id for n in h.leaves()}
    assert leaf_ids == {"OTTAWA", "TORONTO", "QC"}


def test_hierarchy_narrower():
    h = _make_hierarchy()
    assert [n.id for n in h.narrower("CANADA")] == ["ON", "QC"]
    assert [n.id for n in h.narrower("CANADA.ON")] == ["OTTAWA", "TORONTO"]
    assert [n.id for n in h.narrower("CANADA.ON.OTTAWA")] == []
    with pytest.raises(KeyError, match=r"'ON' is a node id, not a path .* 'CANADA.ON'"):
        h.narrower("ON")  # a nested node's bare id is the #439 stale call, refused (#514)


def test_hierarchy_broader():
    h = _make_hierarchy()
    assert h.broader("CANADA") is None
    p = h.broader("CANADA.ON")
    assert p is not None
    assert p.id == "CANADA"
    p = h.broader("CANADA.ON.OTTAWA")
    assert p is not None
    assert p.id == "ON"


def test_hierarchy_broader_transitive_root_first():
    h = _make_hierarchy()
    assert [n.id for n in h.broader_transitive("CANADA.ON.OTTAWA")] == ["CANADA", "ON"]
    assert [n.id for n in h.broader_transitive("CANADA")] == []
    # bounded
    assert [n.id for n in h.broader_transitive("CANADA.ON.OTTAWA", depth=1)] == ["ON"]


def test_hierarchy_narrower_transitive():
    h = _make_hierarchy()
    descendants = {n.id for n in h.narrower_transitive("CANADA")}
    assert descendants == {"ON", "OTTAWA", "TORONTO", "QC"}
    # bounded
    assert {n.id for n in h.narrower_transitive("CANADA", depth=1)} == {"ON", "QC"}


def test_hierarchy_transitive_walks_can_include_the_node_itself():
    """`LazyHierarchy` has taken `include_self` all along and the eager
    `Hierarchy` did not, which made the hierarchies page's "self-inclusion is
    explicit everywhere" untrue. The node keeps the walk's own ordering:
    `broader_transitive` is root-first, so self lands last.
    """
    h = _make_hierarchy()
    assert [n.id for n in h.broader_transitive("CANADA.ON.OTTAWA", include_self=True)] == ["CANADA", "ON", "OTTAWA"]
    assert next(iter(h.narrower_transitive("CANADA.ON", include_self=True))).id == "ON"
    assert {n.id for n in h.narrower_transitive("CANADA.ON", include_self=True)} == {
        "ON",
        *(n.id for n in h.narrower_transitive("CANADA.ON")),
    }


def test_hierarchy_include_self_on_an_unknown_node_adds_nothing():
    h = _make_hierarchy()
    assert list(h.narrower_transitive("NOPE", include_self=True)) == []
    assert list(h.broader_transitive("NOPE", include_self=True)) == []


def test_hierarchy_depth_of():
    h = _make_hierarchy()
    assert h.depth_of("CANADA") == 0
    assert h.depth_of("CANADA.ON") == 1
    assert h.depth_of("CANADA.ON.OTTAWA") == 2


def test_hierarchy_depth_of_unknown_raises():
    h = _make_hierarchy()
    with pytest.raises(KeyError):
        h.depth_of("NOPE")


# ── Bulk projections ────────────────────────────────────────────────────────


def test_hierarchy_depths():
    h = _make_hierarchy()
    assert h.depths() == {
        "CANADA": 0,
        "CANADA.ON": 1,
        "CANADA.QC": 1,
        "CANADA.ON.OTTAWA": 2,
        "CANADA.ON.TORONTO": 2,
    }


def test_hierarchy_broader_map():
    h = _make_hierarchy()
    assert h.broader_map() == {
        "CANADA": None,
        "CANADA.ON": "CANADA",
        "CANADA.QC": "CANADA",
        "CANADA.ON.OTTAWA": "CANADA.ON",
        "CANADA.ON.TORONTO": "CANADA.ON",
    }


def test_hierarchy_narrower_map():
    h = _make_hierarchy()
    nm = h.narrower_map()
    assert nm["CANADA"] == ("CANADA.ON", "CANADA.QC")
    assert nm["CANADA.ON"] == ("CANADA.ON.OTTAWA", "CANADA.ON.TORONTO")
    assert nm["CANADA.ON.OTTAWA"] == ()


def test_hierarchy_ancestors_map_root_first():
    h = _make_hierarchy()
    am = h.ancestors_map()
    assert am["CANADA"] == ()
    assert am["CANADA.ON"] == ("CANADA",)
    assert am["CANADA.ON.OTTAWA"] == ("CANADA", "CANADA.ON")


def test_hierarchy_descendants_map():
    h = _make_hierarchy()
    dm = h.descendants_map()
    assert dm["CANADA"] == ("CANADA.ON", "CANADA.ON.OTTAWA", "CANADA.ON.TORONTO", "CANADA.QC")
    assert dm["CANADA.ON"] == ("CANADA.ON.OTTAWA", "CANADA.ON.TORONTO")
    assert dm["CANADA.ON.OTTAWA"] == ()


def test_hierarchy_inherit_along_broader():
    h = _make_hierarchy()
    out = h.inherit_along_broader({"CANADA": "country", "CANADA.ON": "province"})
    assert out == {
        "CANADA": "country",
        "CANADA.ON": "province",
        "CANADA.QC": "country",
        "CANADA.ON.OTTAWA": "province",
        "CANADA.ON.TORONTO": "province",
    }


def test_hierarchical_code_level_default_none():
    node = HierarchicalCode(id="A", code=code_ref("A"))
    assert node.level is None


# ── descendants(code_id) ────────────────────────────────────────────────────


def test_descendants_returns_code_ids_not_node_ids():
    """`descendants` speaks code-ids — the fixture's node_id ('CANADA') differs from
    the underlying code id ('CA'), so this test would fail if we matched on node.id."""
    h = _make_hierarchy()
    assert h.descendants("CA") == ["ON", "OTTAWA", "TORONTO", "QC"]


def test_descendants_intermediate_node():
    h = _make_hierarchy()
    assert sorted(h.descendants("ON")) == ["OTTAWA", "TORONTO"]


def test_descendants_leaf_returns_empty():
    h = _make_hierarchy()
    assert h.descendants("OTTAWA") == []


def test_descendants_unknown_code_returns_empty():
    h = _make_hierarchy()
    assert h.descendants("NOPE") == []


def test_descendants_falls_back_to_node_id_when_code_ref_missing():
    """A node without a `code` ref is matched by its `id` instead."""
    h = Hierarchy(
        name={"en": "H_BARE"},
        id="H_BARE",
        maintainer="TEST",
        version="1.0",
        tree=[
            HierarchicalCode(
                id="A",
                children=[HierarchicalCode(id="B", code=code_ref("B")), HierarchicalCode(id="C", code=code_ref("C"))],
                code=code_ref("A"),
            ),
        ],
    )
    assert h.descendants("A") == ["B", "C"]
