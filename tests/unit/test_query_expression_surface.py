"""Every operator on every expression type builds a node or raises. Never a bool.

Three defects of one shape shipped in this milestone, each found by accident
and fixed on its own:

- `pred | column` built a `BoolOp` from a column, and the trailing `== "B"`
  then compared that node to a string and yielded `False`.
- `Agg` had no `__eq__`, so `count() == 0` was an identity check.
- `Localised` had none either, so `name == "Canada"` — a forgotten language
  subscript — matched nothing.

The shape is always the same: **an expression that reads like a predicate and
is not one.** Python's defaults are what make it silent. `==` falls back to
identity and yields `False`; `bool()` falls back to "always true"; `in` falls
back to iterating `__getitem__`. Each produces a filter that compiles, runs,
and returns the wrong rows.

So this is a *derived* guard rather than three regression tests. It enumerates
the expression types off the module and asserts, for every operator Python
would otherwise answer for us, that the result is a DSL node or a `TypeError`
naming the fix. A new expression type is covered the day it is added, which is
the property the three separate fixes did not have.

polars sets the precedent for the truthiness half and for the message: `bool`
on an expression raises "the truth value of an Expr is ambiguous" rather than
defaulting to `True`.

**Why `&` / `|` / `~` and not `and` / `or` / `not`:** the keywords call
`__bool__` and cannot be overloaded, so `a and b` would evaluate `bool(a)` and
return `b` — silently dropping the first condition. Raising is what turns that
into an error. `filter(a, b)` ANDs without any operator, and is the spelling
the cookbook prefers.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sdmxlib.model.binding import DataflowBinding
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.ref import Ref
from sdmxlib.query import _expr as expr
from sdmxlib.query.accessors import q
from sdmxlib.query.observations import Observations

pytestmark = pytest.mark.unit


def _observations() -> Observations:
    """An observation root, which is how a caller reaches `Untyped`, `ComponentStr` and `ComponentNum`."""
    binding = DataflowBinding(
        dataflow=Ref[Dataflow].unresolved(Dataflow.urn_for(agency="AG", id="DF", version="1.0")),
        database_role="memory",
        table_location="data.obs",
        dimension_column_map={"REF_AREA": "GEO"},
        measure_column_map={"OBS_VALUE": "OBS_VALUE"},
        attribute_column_map={},
        structural_version="AG:DSD(1.0)",
        source_registry="local",
        materialized_at=datetime(2026, 1, 1, tzinfo=UTC),
        bound_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    return Observations(binding)


def _samples() -> dict[str, object]:
    """One live instance of every expression kind a caller can hold.

    Built through the public accessor rather than by constructing the classes,
    so an instance here is one a caller could actually obtain — a type only
    reachable internally is not part of this surface.
    """
    return {
        "Str": q.code.id,
        # A `Str` in every respect except ordering, which reads the sort key
        # baked beside it (#311) — so it is sampled like any other column and
        # must satisfy the same operator surface.
        "Temporal": q.dsd.valid_from,
        "Version": q.dsd.version,
        "Vocabulary": q.artefact.type,
        "Num": q.code.depth,
        # The only column class with no structural accessor: it exists for a
        # dataflow's observations, whose type nothing declares. Reached through
        # the public root rather than constructed, per the rule above — the
        # binding is the accessor here.
        "Untyped": _observations()["OBS_VALUE"],
        # `obs.text` / `obs.num`: a `Str` / `Num` projected under the component
        # id (#511). Nothing types these components, so neither refuses.
        "ComponentStr": _observations().text("REF_AREA"),
        "ComponentNum": _observations().num("OBS_VALUE"),
        "Bool": q.dsd.is_final,
        "Localised": q.code.name,
        "Label": q.code.name["en"],
        "SideLocalised": q.dsd.name,
        "SideLabel": q.dsd.name["en"],
        "Arr": q.code.ancestors.id,
        "NumExpr": q.code.depth + 1,
        "StrExpr": q.code.id + ":",
        "Agg": q.code.children.count(),
        "Win": q.code.id.rank().over(q.code.depth),
        "Pred": q.code.id == "A",
        "Quantified": q.code.children.any(q.code.id == "X"),
        "FanOut": q.code.children,
        "Cols": q.code,
    }


def test_the_sample_covers_every_public_expression_type() -> None:
    """Derived, so a new expression type joins this guard without being added.

    The list above is instances; this checks it against the *classes* the
    module exposes, so a type nobody remembered to sample fails here rather
    than going unguarded.
    """
    bases = (expr.AnyCol, expr.AnyPred, expr.AnyAgg, expr.AnyCols, expr.Localised, expr.SideLocalised)
    public = {
        name
        for name, value in vars(expr).items()
        if isinstance(value, type) and issubclass(value, bases) and not name.startswith("_")
    }
    # Abstract bases and the row-kind marker are not values a caller holds.
    abstract = {
        "AnyCol",
        "AnyPred",
        "AnyAgg",
        "AnyCols",
        "AnyCursor",
        # Exported vocabulary rather than a node: `having()` has to name what
        # it takes, and a caller reaches one only by comparing an aggregate.
        "AggPred",
        "Col",
        "Cols",
        "Pred",
        "FanOut",
        "RowKind",
        # `Computed` is the base; a caller only ever holds one of its two
        # concrete forms, and both are sampled.
        "Computed",
    }
    # Predicate *nodes* are reached through `Pred`, which is sampled; sampling
    # each would test the same inherited operators over and over.
    nodes = {
        "Compare",
        "IsNull",
        "Match",
        "ArrayHas",
        "InSet",
        "InRelation",
        "RowIn",
        "BoolOp",
        "Not",
        "AggCompare",
        "WinCompare",
        "WinPred",
        "Cursor",
        "OrderKey",
        "Descending",
    }
    sampled = set(_samples())
    missing = public - abstract - nodes - sampled
    assert not missing, f"expression types with no sample in this guard: {sorted(missing)}"


@pytest.mark.parametrize("name", sorted(_samples()))
def test_equality_builds_a_node_or_raises(name: str) -> None:
    """`==` and `!=` must never fall through to Python's identity check.

    That fallback is the quiet one: it yields `False`, so the caller gets a
    filter that runs and matches nothing rather than an error.
    """
    subject = _samples()[name]
    for op, apply in (("==", lambda s: s == 1), ("!=", lambda s: s != 1)):
        try:
            result = apply(subject)
        except TypeError:
            # Refusing is the other acceptable answer, and every refusal here
            # is checked for its message by the named cases below.
            continue
        assert not isinstance(result, bool), f"{name} {op} returned a bare {result!r}"
        # `Win` builds a `WinPred`, which is deliberately *not* an `AnyPred`:
        # that is what keeps a windowed test out of `filter()`. Still a node.
        assert isinstance(result, (expr.AnyPred, expr.WinPred)), f"{name} {op} returned {type(result).__name__}"


@pytest.mark.parametrize("name", sorted(_samples()))
def test_truthiness_is_refused(name: str) -> None:
    """`bool()` must raise, which is what makes `and` / `or` / `not` raise.

    They cannot be overloaded — `a and b` evaluates `bool(a)` and returns one
    operand — so without this a caller writing `and` silently loses a
    condition. polars refuses identically.
    """
    subject = _samples()[name]
    with pytest.raises(TypeError, match="ambiguous"):
        bool(subject)


@pytest.mark.parametrize("name", sorted(_samples()))
def test_the_python_keywords_raise(name: str) -> None:
    """The three the keywords route through, spelled as a caller would."""
    subject = _samples()[name]
    other = q.code.id == "Z"
    for apply in (lambda s: s and other, lambda s: s or other, lambda s: not s):
        with pytest.raises(TypeError, match="ambiguous"):
            apply(subject)


@pytest.mark.parametrize("name", sorted(_samples()))
def test_membership_and_iteration_are_refused(name: str) -> None:
    """`in` falls back to iterating `__getitem__`, which accepts any key on a
    localised column and so never terminates on a real miss."""
    subject = _samples()[name]
    with pytest.raises(TypeError):
        _ = "x" in subject  # pyrefly: ignore[not-iterable]
    with pytest.raises(TypeError):
        iter(subject)  # pyrefly: ignore[no-matching-overload]


# ── the three that shipped, kept as named cases ───────────────────────────


def test_a_forgotten_language_subscript() -> None:
    """`name` reads like a column and is not one until a language is chosen.
    The likeliest mistake on this surface, and it matched nothing."""
    with pytest.raises(TypeError, match=r'Subscript the language first: name\["en"\]'):
        _ = q.code.name == "Canada"


def test_a_forgotten_column() -> None:
    """`filter(q.code == "A")` — an accessor is a namespace, not a value."""
    with pytest.raises(TypeError, match="Compare one of its columns"):
        _ = q.code == "A"


def test_the_precedence_trap() -> None:
    """`&` and `|` bind tighter than `==`, so the unparenthesised form hands a
    column to `|`. Caught at the `|` now, before a bool can exist."""
    with pytest.raises(TypeError, match="parenthesise each comparison"):
        _ = (q.code.id == "A") | q.code.id == "B"


def test_an_aggregate_comparison() -> None:
    """`count() == 0` was an identity check yielding `False`."""
    assert isinstance(q.code.children.count() == 0, expr.AnyPred)


def test_predicate_equality_is_refused_rather_than_repaired() -> None:
    """It never worked. attrs generated `self.column == other.column and ...`,
    and the column arm *builds* a `Compare` — always truthy — so it fell
    through to the value: `(code.id == "A") == (code.name["en"] == "A")`
    reported True for two different columns.

    `canonical_key()` is the supported way to ask whether two queries are the
    same, and it is checked properly in `test_query_canonical_key.py`.
    """
    with pytest.raises(TypeError, match="canonical_key"):
        _ = (q.code.id == "A") == (q.code.name["en"] == "A")


# ── the operators that must keep working ──────────────────────────────────


def test_the_supported_combinators_still_build_nodes() -> None:
    """A guard that refuses too much is worse than the holes it closed."""
    first, second = q.code.id == "A", q.code.depth == 1
    assert isinstance(first & second, expr.AnyPred)
    assert isinstance(first | second, expr.AnyPred)
    assert isinstance(~first, expr.AnyPred)
    assert isinstance(q.code.name["en"] == "Canada", expr.AnyPred)
    assert isinstance(q.code.depth > 1, expr.AnyPred)
    assert isinstance(q.code.id.is_in(["A"]), expr.AnyPred)


def test_filter_ands_without_an_operator() -> None:
    """The spelling the cookbook prefers, and the reason `and` not working
    costs a caller nothing."""
    import sdmxlib as sl
    from sdmxlib.model.codelist import Code, Codelist
    from sdmxlib.model.organisation import Agency

    with sl.LocalRegistry(":memory:") as reg:
        reg.add(
            Codelist(
                id="CL",
                maintainer=Agency(id="AG"),
                name={"en": "n"},
                codes=[Code(id="A", name={"en": "a"}), Code(id="B", name={"en": "b"})],
            )
        )
        both = reg.query(q.code).filter(q.code.id == "A", q.code.name["en"] == "a")
        anded = reg.query(q.code).filter((q.code.id == "A") & (q.code.name["en"] == "a"))
        assert both.ids() == anded.ids() == ["A"]
