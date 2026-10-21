"""Differential properties for the query compiler, over `sdmx.hierarchy_node`.

The companion to `test_properties_query_code.py`, and the reason
`tests/support/hierarchy_oracle.py` survived §3. `model/hierarchy_query.py`
was retired with `model/code_query.py`, and the 1,307-line property file that
held it to account went with it — but the *oracle* is not a test of that
module. It is a pure-Python specification of what these predicates mean,
written to share no code with any compiler, so it holds the new one to the
same account for the cost of a port.

Deleting the oracle along with its old caller would have been the quiet loss
in this milestone: the compiler that replaced four DSLs would then have
differential coverage over codes and none over placements, and nothing would
have said so.

**Why this is not a copy of the code properties.** The two relations differ
where it matters to a compiler: a placement is keyed `(hierarchy_urn, path)`
— its id may repeat in another branch (#439) — its self-reference is
`parent_path`, and it
carries `level_id` and an optional `code_urn` that codes have no analogue for.
Every one of those is a distinct edge or column declaration, and a wrong one
compiles cleanly and answers wrongly.

**What does not port.** The five recursive predicates the oracle models —
`has_ancestor`, `is_ancestor_of`, `has_no_narrower`, `any_ancestor_matches`,
`any_descendant_matches`. The path language has none by design: recursion
stays a named domain verb, which is what keeps every path finite. They are
asserted absent rather than left unmentioned, exactly as in the code file.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from sdmxlib.local.registry import LocalRegistry
from sdmxlib.model.hierarchy import Hierarchy
from sdmxlib.query._expr import Pred
from sdmxlib.query.accessors import HCodeRow, q
from tests.support import hierarchy_oracle as oracle
from tests.support.narrow import present
from tests.support.strategies import forest_hierarchies

pytestmark = pytest.mark.unit

_EXAMPLES = max(5, present(settings.default).max_examples // 5)


def _rows(hierarchy: Hierarchy) -> list[oracle.Row]:
    return oracle.flatten(hierarchy)


def _ids(reg: LocalRegistry, *predicates: Pred[HCodeRow]) -> list[str]:
    """Matching placement ids, sorted — `oracle.evaluate` returns a list and
    `sdmx.hierarchy_node` stores no declaration order to align it with."""
    return sorted(reg.query(q.hcode).filter(*predicates).ids())


def _expected(pred: oracle.Pred, rows: list[oracle.Row]) -> list[str]:
    return sorted(oracle.evaluate(pred, rows))


def _target(data: st.DataObject, rows: list[oracle.Row]) -> str:
    return data.draw(st.sampled_from([r.id for r in rows] + ["zz"]), label="target")


# ── the columns a placement has of its own ────────────────────────────────


@given(hierarchy=forest_hierarchies(), data=st.data())
@settings(max_examples=_EXAMPLES, deadline=None)
def test_id_predicates_agree_with_the_oracle(hierarchy: Hierarchy, data: st.DataObject) -> None:
    rows = _rows(hierarchy)
    target = _target(data, rows)
    with LocalRegistry(":memory:") as reg:
        reg.add(hierarchy)
        assert _ids(reg, q.hcode.id == target) == _expected(oracle.id_eq(target), rows)
        assert _ids(reg, q.hcode.id != target) == _expected(oracle.id_eq(target, negate=True), rows)


@given(hierarchy=forest_hierarchies(), data=st.data())
@settings(max_examples=_EXAMPLES, deadline=None)
def test_uri_predicates_agree_with_the_oracle(hierarchy: Hierarchy, data: st.DataObject) -> None:
    """`uri` is a path since #444; a node without one is NULL, and `!=` drops it."""
    rows = _rows(hierarchy)
    target = data.draw(st.sampled_from([r.node.uri for r in rows if r.node.uri is not None] + ["zz"]), label="uri")
    op = data.draw(st.sampled_from(["contains", "starts_with", "ends_with"]), label="op")
    fragment = data.draw(st.sampled_from([target, target[:1], target[-1:]]), label="fragment")
    match = {"contains": q.hcode.uri.str.contains, "starts_with": q.hcode.uri.str.starts_with}
    match["ends_with"] = q.hcode.uri.str.ends_with
    with LocalRegistry(":memory:") as reg:
        reg.add(hierarchy)
        assert _ids(reg, q.hcode.uri == target) == _expected(oracle.uri_eq(target), rows)
        assert _ids(reg, q.hcode.uri != target) == _expected(oracle.uri_eq(target, negate=True), rows)
        assert _ids(reg, q.hcode.uri.is_null()) == _expected(oracle.uri_is_null(), rows)
        assert _ids(reg, match[op](fragment)) == _expected(oracle.uri_match(op, fragment), rows)


@given(hierarchy=forest_hierarchies(), data=st.data())
@settings(max_examples=_EXAMPLES, deadline=None)
def test_parent_predicates_agree_with_the_oracle(hierarchy: Hierarchy, data: st.DataObject) -> None:
    """`parent.id` is the trimmed form of `parent_node_id` — the hop and the
    stored reference are one query, so this checks the column through the
    only spelling the language offers."""
    rows = _rows(hierarchy)
    target = _target(data, rows)
    with LocalRegistry(":memory:") as reg:
        reg.add(hierarchy)
        assert _ids(reg, q.hcode.parent.id == target) == _expected(oracle.parent_eq(target), rows)
        assert _ids(reg, q.hcode.parent.id.is_null()) == _expected(oracle.parent_is_null(), rows)
        assert _ids(reg, q.hcode.parent.id.is_not_null()) == _expected(oracle.parent_is_null(negate=True), rows)


@given(hierarchy=forest_hierarchies(), data=st.data())
@settings(max_examples=_EXAMPLES, deadline=None)
def test_depth_agrees_with_a_fresh_traversal(hierarchy: Hierarchy, data: st.DataObject) -> None:
    """The oracle re-derives depth by walking `parent_id`; the column is baked
    by the writer. So this checks the bake as well as the predicate — the
    invariant `docs/design/caching.md` implies and nothing else enforces.
    """
    rows = _rows(hierarchy)
    wanted = data.draw(st.integers(min_value=0, max_value=4), label="depth")
    with LocalRegistry(":memory:") as reg:
        reg.add(hierarchy)
        assert _ids(reg, q.hcode.depth == wanted) == _expected(oracle.depth_eq(wanted), rows)


@given(hierarchy=forest_hierarchies(), data=st.data())
@settings(max_examples=_EXAMPLES, deadline=None)
def test_level_agrees_with_the_oracle(hierarchy: Hierarchy, data: st.DataObject) -> None:
    """A column codes have no analogue for, and one where the accessor changed
    in this milestone: `level_id` became the `level` edge, and `level.id`
    trims back to the same column."""
    rows = _rows(hierarchy)
    levels = [level for level in {oracle._level_id(r) for r in rows} if level is not None]
    target = data.draw(st.sampled_from([*levels, "zz"]) if levels else st.just("zz"), label="level")
    with LocalRegistry(":memory:") as reg:
        reg.add(hierarchy)
        assert _ids(reg, q.hcode.level.id == target) == _expected(oracle.level_eq(target), rows)


# ── ancestry, which is a stored fact rather than recursion ────────────────


@given(hierarchy=forest_hierarchies(), data=st.data())
@settings(max_examples=_EXAMPLES, deadline=None)
def test_ancestry_agrees_with_a_fresh_traversal(hierarchy: Hierarchy, data: st.DataObject) -> None:
    """`ancestors` reads the baked `ancestor_ids`; the oracle walks the tree.

    The two agreeing is what makes the bake trustworthy, and it is the only
    check on it — a wrong bake compiles and answers wrongly.
    """
    rows = _rows(hierarchy)
    target = _target(data, rows)
    with LocalRegistry(":memory:") as reg:
        reg.add(hierarchy)
        found = _ids(reg, q.hcode.ancestors.id == target)
        expected = sorted(r.id for r in rows if target in oracle.ancestor_ids(r.path, rows))
        assert found == expected
        # The edge itself joins on path (#439): the same walk, by path.
        path = data.draw(st.sampled_from([r.path for r in rows]), label="path")
        by_path = _ids(reg, q.hcode.ancestors.nested_id == path)
        assert by_path == sorted(r.id for r in rows if path in oracle.ancestors(r.path, rows))


@given(hierarchy=forest_hierarchies(), data=st.data())
@settings(max_examples=_EXAMPLES, deadline=None)
def test_descendants_are_the_same_edge_read_backwards(hierarchy: Hierarchy, data: st.DataObject) -> None:
    rows = _rows(hierarchy)
    target = _target(data, rows)
    with LocalRegistry(":memory:") as reg:
        reg.add(hierarchy)
        found = _ids(reg, q.hcode.descendants.id == target)
        # Every node with *some* descendant whose id is the target — a leaf
        # id may sit in several branches (#439), so this is a union.
        expected = sorted(
            r.id for r in rows if any(d.id == target and r.path in oracle.ancestors(d.path, rows) for d in rows)
        )
        assert found == expected


@given(hierarchy=forest_hierarchies(), data=st.data())
@settings(max_examples=_EXAMPLES, deadline=None)
def test_leaves_are_the_placements_with_no_children(hierarchy: Hierarchy, data: st.DataObject) -> None:
    rows = _rows(hierarchy)
    with LocalRegistry(":memory:") as reg:
        reg.add(hierarchy)
        found = _ids(reg, q.hcode.children.count() == 0)
        parents = {r.parent_id for r in rows if r.parent_id is not None}
        assert found == sorted(r.id for r in rows if r.id not in parents)


# ── the boolean algebra ───────────────────────────────────────────────────


@given(hierarchy=forest_hierarchies(), data=st.data())
@settings(max_examples=_EXAMPLES, deadline=None)
def test_and_or_and_not_agree_with_the_oracle(hierarchy: Hierarchy, data: st.DataObject) -> None:
    rows = _rows(hierarchy)
    first, second = _target(data, rows), _target(data, rows)
    with LocalRegistry(":memory:") as reg:
        reg.add(hierarchy)
        left, right = q.hcode.id == first, q.hcode.parent.id == second
        o_left, o_right = oracle.id_eq(first), oracle.parent_eq(second)
        assert _ids(reg, left & right) == _expected(oracle.and_(o_left, o_right), rows)
        assert _ids(reg, left | right) == _expected(oracle.or_(o_left, o_right), rows)
        assert _ids(reg, ~left) == _expected(oracle.not_(o_left), rows)


# ── the guard that keeps the above honest ─────────────────────────────────


def test_the_recursive_predicates_are_absent_by_design() -> None:
    """The oracle models five the path language does not have.

    Stated rather than left as a gap: recursion is a named domain verb on the
    artefact (`h.narrower_transitive`), which is what keeps every path finite
    and the compiler free of recursive CTEs. The verbs themselves are covered
    in `test_query_hierarchy.py`, against the same baked columns.
    """
    modelled = {"has_ancestor", "is_ancestor_of", "has_no_narrower", "any_ancestor_matches", "any_descendant_matches"}
    assert modelled <= set(vars(oracle)), "the oracle stopped modelling these — the claim below is stale"
    for name in ("has_ancestor", "is_ancestor_of", "has_no_narrower"):
        assert not hasattr(q.hcode, name)
