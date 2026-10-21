"""Generative coverage for the *other* query DSL — `item()`, `Query[T]`, the SQL seam (#251).

%10's Hypothesis work treated "the query DSL" as the codelist filter DSL. There
is a second one, and it is the one a user meets first — it is what CLAUDE.md's
"Common patterns" shows:

    dsd.dimensions.filter(sl.item("is_enumerated"))
    reg.query(sl.Dataflow, agency="ESTAT").browse()

`api/query.py`, `api/filters.py`, `model/expr.py` and `sql.py` had **no**
property tests between them.

## Two findings that shaped what is written here

**`ItemPredicate` has only `~`.** No `&`, no `|`. So the composition laws #251
anticipated — and the De Morgan laws #260 explicitly deferred to this issue —
have nothing to apply to. Double negation does; that is asserted below. This is
the same shape as #260's own finding about the query DSLs, and it means the
codebase has *no* boolean-algebra surface anywhere: negation is a per-field flag
or a single unary operator, never a combinator tree.

**`Query[T]` mutated.** `filter` appended to the receiver and returned `self`,
so branching off a base query did not branch — both predicates landed on one
object, and every other query class in the library is a frozen record that
evolves. Filed as #291, pinned here, and **closed by deletion** in #306: the
refinement surface ran *after* the fetch, so it could never be pushed to a
provider and only made a full catalogue download look bounded. What is left is
three fetch modes, and the properties below say what they still owe each
other.

## The uncovered lines, checked before writing

#251 asked whether they were error paths or dead code. They are neither: the
gaps in `expr.py` and `filters.py` are `__invert__` / `__hash__` / `__repr__`,
and the gaps in `api/query.py` are `DataQuery`'s fetch/pivot/stream methods —
the *data* path, which needs a live fetcher and is not `Query[T]` at all.

Result: `expr.py` and `api/filters.py` reach 100 %, and `sql.py`'s one real
uncovered line — the schema remap, since retired with `CompileContext` — was
a property. What remains is explained rather than chased:

- `sql.py` × 6 — Protocol method bodies (``...``). Unreachable by
  construction; covering them would mean instantiating a Protocol.
- `api/query.py` × 10 — `DataQuery.fetch` / `pivot` / `pivot_to` / `fetch_to`.
  These need a live fetcher or streamer and belong with the data-plane suite,
  not with `Query[T]`; #251's brief is the collection query.
"""

from dataclasses import dataclass

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from sdmxlib.model.collections import ItemList
from sdmxlib.model.expr import ItemPredicate, item

pytestmark = pytest.mark.unit


@dataclass
class _Item:
    """A stand-in with one attribute of each kind `item()` can reach."""

    id: str
    depth: int
    flag: bool
    parent: str | None


_IDS = ["A", "B", "C", "AB"]


@st.composite
def item_lists(draw: st.DrawFn) -> ItemList[_Item]:
    n = draw(st.integers(min_value=0, max_value=6), label="n")
    return ItemList(
        [
            _Item(
                id=draw(st.sampled_from(_IDS), label=f"id{i}") + str(i),
                depth=draw(st.integers(min_value=0, max_value=3), label=f"depth{i}"),
                flag=draw(st.booleans(), label=f"flag{i}"),
                parent=draw(st.sampled_from([None, "A", "B"]), label=f"parent{i}"),
            )
            for i in range(n)
        ]
    )


# ── item() against the lambda it replaces ────────────────────────────────────


@settings(deadline=None)
@given(items=item_lists(), value=st.sampled_from(_IDS), depth=st.integers(min_value=0, max_value=3))
def test_item_expressions_agree_with_the_equivalent_lambda(items, value: str, depth: int):
    """The differential test that needs no oracle — the lambda *is* the oracle.

    `item("x") == v` exists only to be a more readable spelling of
    `lambda i: i.x == v`. If the two ever disagree the expression form is
    simply wrong, and every operator it offers is checked here rather than the
    one or two a hand-written test would have picked.
    """
    assert items.filter(item("flag")).ids() == items.filter(lambda i: bool(i.flag)).ids()
    assert items.filter(item("id") == value).ids() == items.filter(lambda i: i.id == value).ids()
    assert items.filter(item("id") != value).ids() == items.filter(lambda i: i.id != value).ids()
    assert items.filter(item("depth") == depth).ids() == items.filter(lambda i: i.depth == depth).ids()
    assert items.filter(item("parent").is_null()).ids() == items.filter(lambda i: i.parent is None).ids()
    # A one-element `is_in` is an equality — #260's law 2, restated for the
    # in-memory DSL. Spelling the oracle as `==` rather than `in [value]` makes
    # that the assertion rather than a coincidence.
    assert items.filter(item("id").is_in([value])).ids() == items.filter(lambda i: i.id == value).ids()
    assert items.filter(item("id").is_in([])).ids() == []
    assert items.filter(~item("flag")).ids() == items.filter(lambda i: not i.flag).ids()


@settings(deadline=None)
@given(items=item_lists(), value=st.sampled_from(_IDS))
def test_double_negation_is_the_identity(items, value: str):
    """``~~p`` selects what ``p`` selects.

    The only algebraic law this DSL admits: `ItemPredicate` has `__invert__`
    and nothing else, so there is no `&` or `|` for De Morgan to distribute
    over. #260 deferred those laws here on the assumption the combinators
    existed; they do not, in either DSL.
    """
    for predicate in (item("flag"), item("id") == value, item("parent").is_null()):
        assert items.filter(~~predicate).ids() == items.filter(predicate).ids()


@settings(deadline=None)
@given(items=item_lists(), value=st.sampled_from(_IDS))
def test_a_predicate_and_its_negation_partition_the_list(items, value: str):
    """``p`` and ``~p`` are a disjoint cover — for *expressions*, not just lambdas.

    `test_properties_core` asserts `partition` is a disjoint cover for lambda
    predicates. This asserts the same of the expression form, which is a
    different code path: `ItemExpr.__invert__` builds a new closure over the
    attribute rather than wrapping the caller's callable.
    """
    for predicate in (item("flag"), item("id") == value, item("parent").is_null()):
        yes = set(items.filter(predicate).ids())
        no = set(items.filter(~predicate).ids())
        assert yes | no == set(items.ids())
        assert yes & no == set()


@settings(deadline=None)
@given(items=item_lists())
def test_partition_agrees_with_filter_for_expressions(items):
    """``partition(p)`` is ``(filter(p), filter(~p))`` — stated for expressions."""
    matched, rest = items.partition(item("flag"))
    assert matched.ids() == items.filter(item("flag")).ids()
    assert rest.ids() == items.filter(~item("flag")).ids()


# ── Identity dunders, which nothing exercised ────────────────────────────────


def test_item_expressions_hash_and_repr_by_attribute() -> None:
    """`ItemExpr.__hash__` / `__repr__` — two of `expr.py`'s uncovered lines.

    They matter for a reason beyond coverage: `__eq__` on `ItemExpr` is
    overloaded to *build a predicate*, so the usual "equal objects hash equal"
    reasoning does not apply and the hash keys on the attribute name alone.
    Worth pinning, because that is a surprising contract to rediscover.
    """
    assert hash(item("id")) == hash(item("id"))
    assert hash(item("id")) != hash(item("depth"))
    assert repr(item("id")) == "item('id')"


def test_item_predicate_repr_names_its_function() -> None:
    """`ItemPredicate.__repr__`, the last uncovered line in `expr.py`."""
    assert "ItemPredicate(" in repr(ItemPredicate(lambda _x: True))


# ── Query[T] ─────────────────────────────────────────────────────────────────


@st.composite
def item_lists_plain(draw: st.DrawFn) -> list[_Item]:
    return list(draw(item_lists()))


# `CompileContext` went with the `Predicate` seam (#306), and its three
# properties with it: alias uniqueness, per-pass counter restart, and the
# schema remap. The first two are now `_Namespace`, checked in
# `tests/unit/test_sql.py` — including against a real nested compile, which
# the property could not do because it had no compiler to run. The schema
# remap had no caller then and has none now; the path language names
# `sdmx.<table>` directly.
