"""Differential properties for the **new** query compiler, over `sdmx.code`.

`tests/support/oracle.py` is a pure-Python evaluator written to disagree with
`model/code_query.py`. It shares no code with that module *or* with this one —
it reads `Code` objects and re-derives ancestry from `parent_id` — so it is an
independent specification of what these predicates mean, and it can hold the
new compiler to the same account.

That is why this is written before the migration. `query/_compile.py` is the
module that will replace four DSLs; it has example tests and no generative
ones, while the module it replaces has ~1,500 lines of differential property.
Deleting the covered thing first and porting the tests afterwards would run
the migration through the gap.

**What does not port, and why that is not a hole.** The oracle models five
recursive predicates — `has_ancestor`, `is_ancestor_of`, `has_no_narrower`,
`any_ancestor_matches`, `any_descendant_matches`. The new language has none of
them by design: recursion stays a named domain verb on the artefact, which is
the corollary keeping every path finite and the compiler free of recursive
CTEs. They are asserted absent below rather than left unmentioned.

`order_by(q.code.position)` reproduces the old DSL's default order, which is
what `oracle.evaluate` returns — ids in codelist order.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from sdmxlib.local.registry import LocalRegistry
from sdmxlib.model.codelist import Codelist
from sdmxlib.query import _expr as qx
from sdmxlib.query.accessors import CodeRow, q
from tests.support import oracle
from tests.support.narrow import present
from tests.support.strategies import annotated_forest_codelists, forest_codelists

pytestmark = pytest.mark.unit

_STRING_OPS = ["contains", "starts_with", "ends_with"]

#: Each example persists a codelist into a fresh in-memory DuckDB, so these are
#: slower than a pure-model property. Scaled from the profile so
#: `HYPOTHESIS_PROFILE=deep` still explores hard.
_EXAMPLES = max(5, present(settings.default).max_examples // 5)


def _ids(reg: LocalRegistry, *predicates: qx.Pred[CodeRow]) -> list[str]:
    """Matching code ids, in codelist order — what `oracle.evaluate` returns."""
    return reg.query(q.code).filter(*predicates).order_by(q.code.position).ids()


def _match[R: qx.RowKind](ref: qx._StrOps[R], op: str, value: str) -> qx.Pred[R]:
    """Call whichever of the three string ops *op* names."""
    return {"contains": ref.contains, "starts_with": ref.starts_with, "ends_with": ref.ends_with}[op](value)


def _fragment(data: st.DataObject, value: str) -> str:
    return data.draw(st.sampled_from([value, value[:1], value[-1:], "zz"]), label="fragment")


# ── plain columns ─────────────────────────────────────────────────────────


@settings(max_examples=_EXAMPLES, deadline=None)
@given(cl=forest_codelists(), data=st.data())
def test_id_predicates_agree_with_the_oracle(cl: Codelist, data: st.DataObject) -> None:
    target = data.draw(st.sampled_from([c.id for c in cl.codes]))
    fragment = _fragment(data, target)
    op = data.draw(st.sampled_from(_STRING_OPS), label="op")
    codes = list(cl.codes)

    with LocalRegistry(":memory:") as reg:
        reg.add(cl)
        assert _ids(reg, q.code.id == target) == oracle.evaluate(oracle.id_eq(target), codes)
        assert _ids(reg, q.code.id != target) == oracle.evaluate(oracle.id_eq(target, negate=True), codes)
        assert _ids(reg, _match(q.code.id.str, op, fragment)) == oracle.evaluate(oracle.id_match(op, fragment), codes)


@settings(max_examples=_EXAMPLES, deadline=None)
@given(cl=forest_codelists(), data=st.data())
def test_uri_predicates_agree_with_the_oracle(cl: Codelist, data: st.DataObject) -> None:
    """`uri` is a path since #444. A code without one is NULL, so `!=` drops it as `parent.id` does a root."""
    codes = list(cl.codes)
    target = data.draw(st.sampled_from([c.uri for c in codes if c.uri is not None] or ["https://example.org/a"]))
    op = data.draw(st.sampled_from(_STRING_OPS), label="op")
    fragment = _fragment(data, target)

    with LocalRegistry(":memory:") as reg:
        reg.add(cl)
        assert _ids(reg, q.code.uri == target) == oracle.evaluate(oracle.uri_eq(target), codes)
        assert _ids(reg, q.code.uri != target) == oracle.evaluate(oracle.uri_eq(target, negate=True), codes)
        assert _ids(reg, q.code.uri.is_null()) == oracle.evaluate(oracle.uri_is_null(), codes)
        assert _ids(reg, q.code.uri.is_not_null()) == oracle.evaluate(oracle.uri_is_null(negate=True), codes)
        assert _ids(reg, _match(q.code.uri.str, op, fragment)) == oracle.evaluate(oracle.uri_match(op, fragment), codes)


@settings(max_examples=_EXAMPLES, deadline=None)
@given(cl=forest_codelists(), data=st.data())
def test_parent_id_predicates_agree_with_the_oracle(cl: Codelist, data: st.DataObject) -> None:
    """`parent_id != x` is the interesting one: a root's `parent_id` is NULL and
    `NULL != ?` is NULL rather than true, so roots drop out of a comparison a
    set-complement reading would keep."""
    codes = list(cl.codes)
    target = data.draw(st.sampled_from([c.id for c in codes]))
    op = data.draw(st.sampled_from(_STRING_OPS), label="op")
    fragment = _fragment(data, target)

    with LocalRegistry(":memory:") as reg:
        reg.add(cl)
        assert _ids(reg, q.code.parent.id.is_null()) == oracle.evaluate(oracle.parent_is_null(), codes)
        assert _ids(reg, q.code.parent.id.is_not_null()) == oracle.evaluate(oracle.parent_is_null(negate=True), codes)
        assert _ids(reg, q.code.parent.id == target) == oracle.evaluate(oracle.parent_eq(target), codes)
        assert _ids(reg, q.code.parent.id != target) == oracle.evaluate(oracle.parent_eq(target, negate=True), codes)
        assert _ids(reg, _match(q.code.parent.id.str, op, fragment)) == oracle.evaluate(
            oracle.parent_match(op, fragment), codes
        )


@settings(max_examples=_EXAMPLES, deadline=None)
@given(cl=forest_codelists(), wanted=st.integers(min_value=0, max_value=4))
def test_depth_agrees_with_a_fresh_traversal(cl: Codelist, wanted: int) -> None:
    """The compiler reads the baked `depth` column; the oracle walks
    `parent_id`. A disagreement means either the SQL or the bake is wrong —
    the invariant `docs/design/caching.md` implies and nothing else enforces."""
    codes = list(cl.codes)
    with LocalRegistry(":memory:") as reg:
        reg.add(cl)
        assert _ids(reg, q.code.depth == wanted) == oracle.evaluate(oracle.depth_eq(wanted), codes)
        assert _ids(reg, q.code.depth != wanted) == oracle.evaluate(oracle.depth_eq(wanted, negate=True), codes)


@settings(max_examples=_EXAMPLES, deadline=None)
@given(cl=forest_codelists(), data=st.data())
def test_ancestry_agrees_with_a_fresh_traversal(cl: Codelist, data: st.DataObject) -> None:
    """`ancestor_ids.has(x)` against an oracle that walks `parent_id`.

    The bake and the query are checked at once: the column is written by
    `storage.writers` and the oracle never reads it, so the two agreeing is
    the invariant `docs/design/caching.md` implies for baked data.
    """
    codes = list(cl.codes)
    target = data.draw(st.sampled_from([c.id for c in codes]))
    with LocalRegistry(":memory:") as reg:
        reg.add(cl)
        assert _ids(reg, q.code.ancestors.id == target) == oracle.evaluate(oracle.has_ancestor(target), codes)


@settings(max_examples=_EXAMPLES, deadline=None)
@given(cl=forest_codelists(), data=st.data())
def test_ancestry_with_a_depth_bound_agrees(cl: Codelist, data: st.DataObject) -> None:
    """What `narrower_transitive(depth=)` did, as an ordinary conjunction."""
    codes = list(cl.codes)
    target = data.draw(st.sampled_from([c.id for c in codes]))
    bound = data.draw(st.integers(min_value=0, max_value=4), label="depth")
    with LocalRegistry(":memory:") as reg:
        reg.add(cl)
        assert _ids(reg, (q.code.ancestors.id == target) & (q.code.depth <= bound)) == oracle.evaluate(
            oracle.and_(oracle.has_ancestor(target), lambda c, cs: oracle.depth(c.id, cs) <= bound), codes
        )


@settings(max_examples=_EXAMPLES, deadline=None)
@given(cl=forest_codelists(), data=st.data())
def test_is_in_agrees_with_the_oracle(cl: Codelist, data: st.DataObject) -> None:
    ids = [c.id for c in cl.codes]
    wanted = data.draw(st.lists(st.sampled_from(ids), max_size=4, unique=True), label="wanted")
    codes = list(cl.codes)
    with LocalRegistry(":memory:") as reg:
        reg.add(cl)
        assert _ids(reg, q.code.id.is_in(wanted)) == oracle.evaluate(lambda c, _codes: c.id in set(wanted), codes)


# ── labels, and the whole folding ladder ──────────────────────────────────


@settings(max_examples=_EXAMPLES, deadline=None)
@given(cl=forest_codelists(), data=st.data())
def test_name_predicates_agree_with_the_oracle(cl: Codelist, data: st.DataObject) -> None:
    """`name` is an inline `MAP` here, so this also checks the subscript."""
    codes = list(cl.codes)
    langs = sorted({lang for c in codes for lang in c.name})
    if not langs:
        return
    lang = data.draw(st.sampled_from(langs), label="lang")
    values = [found.text for c in codes if (found := c.name.get(lang)) is not None and found.text]
    if not values:
        return
    target = data.draw(st.sampled_from(values), label="target")
    assert target is not None
    fragment = _fragment(data, target)
    op = data.draw(st.sampled_from(_STRING_OPS), label="op")

    with LocalRegistry(":memory:") as reg:
        reg.add(cl)
        assert _ids(reg, q.code.name[lang] == target) == oracle.evaluate(oracle.name_eq(lang, target), codes)
        assert _ids(reg, q.code.name[lang] != target) == oracle.evaluate(
            oracle.name_eq(lang, target, negate=True), codes
        )
        assert _ids(reg, _match(q.code.name[lang].str, op, fragment)) == oracle.evaluate(
            oracle.name_match(lang, op, fragment), codes
        )
        assert _ids(reg, _match(q.code.name[lang].str.unaccented, op, fragment)) == oracle.evaluate(
            oracle.name_folded_match(lang, op, fragment, punctuation=False), codes
        )
        assert _ids(reg, _match(q.code.name[lang].str.folded, op, fragment)) == oracle.evaluate(
            oracle.name_folded_match(lang, op, fragment, punctuation=True), codes
        )


@settings(max_examples=_EXAMPLES, deadline=None)
@given(cl=forest_codelists(), data=st.data())
def test_caseless_carries_equality_and_the_floor_does_not(cl: Codelist, data: st.DataObject) -> None:
    """#222, stated as a property rather than as examples: `==` is
    case-sensitive and `.str.caseless ==` is not."""
    codes = list(cl.codes)
    target = data.draw(st.sampled_from([c.id for c in codes]))
    with LocalRegistry(":memory:") as reg:
        reg.add(cl)
        # `caseless_eq` is the oracle's value-level rule; lifting it to a
        # predicate here rather than adding a builder keeps the folding logic
        # in one place — which is the half that could disagree.
        assert _ids(reg, q.code.id.str.caseless == target) == oracle.evaluate(
            lambda c, _codes: oracle.caseless_eq(c.id, target, negate=False), codes
        )
        assert _ids(reg, q.code.id.str.caseless != target) == oracle.evaluate(
            lambda c, _codes: oracle.caseless_eq(c.id, target, negate=True), codes
        )


# ── annotations, which are a fan-out here ─────────────────────────────────


@settings(max_examples=_EXAMPLES, deadline=None)
@given(cl=annotated_forest_codelists(), data=st.data())
def test_annotation_presence_agrees_with_the_oracle(cl: Codelist, data: st.DataObject) -> None:
    """Dotting through the fan-out means *some* annotation, which is exactly
    what the oracle's `annotation_present` says."""
    codes = list(cl.codes)
    types = sorted({a.type for c in codes for a in c.annotations if a.type})
    if not types:
        return
    ann_type = data.draw(st.sampled_from(types), label="type")
    with LocalRegistry(":memory:") as reg:
        reg.add(cl)
        assert _ids(reg, q.code.annotations.type == ann_type) == oracle.evaluate(
            oracle.annotation_present(ann_type), codes
        )


@settings(max_examples=_EXAMPLES, deadline=None)
@given(cl=annotated_forest_codelists(), data=st.data())
def test_the_binder_agrees_with_the_oracle(cl: Codelist, data: st.DataObject) -> None:
    """`.any()` groups predicates onto **one** annotation, where two separate
    filters may match two different ones. The oracle says the same thing with
    a single `any(...)` over the annotation list, so a compiler that emitted
    two `EXISTS` here would disagree on any code carrying two annotations.
    """
    codes = list(cl.codes)
    pairs = sorted({(a.type, a.title) for c in codes for a in c.annotations if a.type and a.title})
    if not pairs:
        return
    ann_type, title = data.draw(st.sampled_from(pairs), label="pair")
    with LocalRegistry(":memory:") as reg:
        reg.add(cl)
        same = q.code.annotations.any(q.annotation.type == ann_type, q.annotation.title == title)
        assert _ids(reg, same) == oracle.evaluate(oracle.annotation_eq(ann_type, title), codes)


# ── boolean algebra ───────────────────────────────────────────────────────


@settings(max_examples=_EXAMPLES, deadline=None)
@given(cl=forest_codelists(), data=st.data())
def test_and_or_and_not_agree_with_the_oracle(cl: Codelist, data: st.DataObject) -> None:
    codes = list(cl.codes)
    target = data.draw(st.sampled_from([c.id for c in codes]))
    wanted_depth = data.draw(st.integers(min_value=0, max_value=3))

    left, left_o = q.code.id == target, oracle.id_eq(target)
    right, right_o = q.code.depth == wanted_depth, oracle.depth_eq(wanted_depth)

    with LocalRegistry(":memory:") as reg:
        reg.add(cl)
        assert _ids(reg, left & right) == oracle.evaluate(oracle.and_(left_o, right_o), codes)
        assert _ids(reg, left | right) == oracle.evaluate(oracle.or_(left_o, right_o), codes)
        assert _ids(reg, ~left) == oracle.evaluate(oracle.not_(left_o), codes)
        # Two filters are one conjunction — the law `filter` promises.
        assert _ids(reg, left, right) == _ids(reg, left & right)


@settings(max_examples=_EXAMPLES, deadline=None)
@given(cl=forest_codelists(), data=st.data())
def test_a_predicate_and_its_negation_partition_the_rows(cl: Codelist, data: st.DataObject) -> None:
    """Over a NOT NULL column, so three-valued logic does not apply — which is
    what makes this a partition rather than merely a subset pair."""
    codes = list(cl.codes)
    target = data.draw(st.sampled_from([c.id for c in codes]))
    with LocalRegistry(":memory:") as reg:
        reg.add(cl)
        matched = set(_ids(reg, q.code.id == target))
        rest = set(_ids(reg, q.code.id != target))
        assert not matched & rest
        assert matched | rest == {c.id for c in codes}


# ── the guards that keep the above honest ─────────────────────────────────


def test_every_predicate_kind_is_covered() -> None:
    """Counted off the module, so an eleventh predicate added without a
    property fails here rather than going quietly untested."""
    kinds = {
        cls.__name__
        for cls in vars(qx).values()
        if isinstance(cls, type) and issubclass(cls, qx.Pred) and cls is not qx.Pred
    }
    covered = {
        "Compare",  # id / parent_id / name / depth equality, and the caseless rung
        "Match",  # the three string ops at every fold
        "InSet",  # test_is_in_agrees_with_the_oracle
        "IsNull",  # test_parent_id_predicates_agree_with_the_oracle
        "BoolOp",  # test_and_or_and_not_agree_with_the_oracle
        "Not",  # test_and_or_and_not_agree_with_the_oracle
        "Quantified",  # test_the_binder_agrees_with_the_oracle
        "ArrayHas",  # test_ancestry_agrees_with_a_fresh_traversal
        # Not differential: no oracle models a second query or foreign SQL, and
        # writing one would mean restating the compiler. Covered by example in
        # test_query_sets.py and test_query_raw.py.
        "RowIn",
        "InRelation",
        # Also by example, in test_query_agg_filter.py — and there against the
        # verb it replaces, `LazyCodelist.leaves()`, which is a differential in
        # everything but name. The oracle counts codes, not a code's children,
        # so modelling this would mean extending it rather than reusing it.
        "AggCompare",
    }
    assert kinds == covered, f"untested predicate kinds: {kinds - covered}"


def test_the_recursive_predicates_are_absent_by_design() -> None:
    """The oracle models five the new language does not have.

    Recursion stays a named domain verb on the artefact — the corollary that
    keeps every path finite and the compiler free of recursive CTEs. Asserted
    so the gap reads as a decision rather than as missing coverage, and so
    adding one silently fails here.
    """
    for name in ("is_ancestor_of", "has_no_narrower", "any_ancestor_matches"):
        assert hasattr(oracle, name), "the oracle still models it"
    for absent in ("is_ancestor_of", "descendants_of", "narrower_transitive", "leaves"):
        assert not hasattr(q.code, absent), f"q.code grew {absent}"
    # `has_ancestor` and `any_ancestor_matches` are the exceptions, and they
    # are a *relationship* rather than verbs: the closure is baked at write
    # time, so ancestry is an ordinary fan-out.
    assert hasattr(q.code, "ancestors")
    assert hasattr(q.code, "descendants")


def test_the_dsl_path_is_sql_backed() -> None:
    """Guard against the differential tests silently degenerating.

    Every property above compares a compiled query against the oracle. If
    `reg.query(q.code)` ever stopped compiling — a fallback to Python
    filtering, say — they would all still pass and check nothing.
    """
    with LocalRegistry(":memory:") as reg:
        compiled = reg.query(q.code).filter(q.code.id == "X").to_sql()
        assert "FROM sdmx.code" in compiled.sql
        assert "WHERE" in compiled.sql
        assert "X" in compiled.params
