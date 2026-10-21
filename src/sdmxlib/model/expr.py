"""ItemExpr, ItemPredicate, item() — expression-based filtering for ItemList.

Provides a Polars-style expression API for ItemList.filter():

    dims.filter(item("id") == "FREQ")
    dims.filter(item("is_enumerated"))
    dims.filter(~item("is_enumerated"))
    dims.filter(item("id").is_in(["FREQ", "GEO"]))
    dims.filter(item("codelist").is_null())

Both lambda and expression forms are accepted by ItemList.filter():

    dims.filter(lambda d: d.id == "FREQ")     # lambda
    dims.filter(item("id") == "FREQ")          # expression
"""

from collections.abc import Callable, Collection
from typing import Any

#: Anything `ItemList.filter()` accepts: a lambda, an `ItemPredicate`, or a bare
#: `ItemExpr` used as a truthy check. `&` and `|` take this rather than
#: `ItemPredicate` so ``(item("id") == "FREQ") & item("is_enumerated")`` works —
#: the right-hand side there is an `ItemExpr`, and requiring the narrower type
#: would reject the most natural spelling of the most common conjunction.
ItemFilter = Callable[[object], bool]


class ItemPredicate:
    """A composable predicate returned by ItemExpr operations.

    Supports ``&``, ``|`` and ``~``. The operands may be any filter
    `ItemList.filter()` would accept — another predicate, a bare `ItemExpr`, or
    a plain lambda:

        ~item("is_enumerated")                          # not enumerated
        (item("id") == "FREQ") & item("is_enumerated")  # both
        (item("id") == "FREQ") | (item("id") == "TIME")  # either

    These are the same three operators the SQL-backed DSLs grew in #262, spelled
    the same way. The implementation has nothing in common — this side composes
    Python callables and there is no NULL, so unlike the SQL predicates these do
    obey excluded middle: ``p | ~p`` holds for every item.
    """

    __slots__ = ("_fn",)

    def __init__(self, fn: Callable[[object], bool]) -> None:
        self._fn = fn

    def __call__(self, x: object) -> bool:
        """Evaluate the predicate against *x*."""
        return self._fn(x)

    def __and__(self, other: ItemFilter) -> ItemPredicate:
        left = self._fn
        return ItemPredicate(lambda x: left(x) and other(x))

    def __or__(self, other: ItemFilter) -> ItemPredicate:
        left = self._fn
        return ItemPredicate(lambda x: left(x) or other(x))

    def __invert__(self) -> ItemPredicate:
        fn = self._fn
        return ItemPredicate(lambda x: not fn(x))

    def __repr__(self) -> str:
        return f"ItemPredicate({self._fn!r})"


class ItemExpr:
    """Expression builder for a single item attribute.

    Created via ``item("attr_name")``. Calling the expression directly
    performs a truthy check; comparison operators and methods return
    composable ``ItemPredicate`` objects.

    Example:
        item("is_enumerated")            # truthy check
        ~item("is_enumerated")           # negated truthy
        item("id") == "FREQ"             # equality
        item("id") != "FREQ"             # inequality
        item("id").is_in(["FREQ", "GEO"])  # membership
        item("codelist").is_null()       # None check
    """

    __slots__ = ("_attr",)

    def __init__(self, attr: str) -> None:
        self._attr = attr

    # ── Direct use as predicate ───────────────────────────────────────────

    def __call__(self, x: object) -> bool:
        """Truthy check — allows using ItemExpr directly as a filter predicate."""
        return bool(getattr(x, self._attr))

    def __invert__(self) -> ItemPredicate:
        """Negated truthy check."""
        attr = self._attr
        return ItemPredicate(lambda x: not bool(getattr(x, attr)))

    def __and__(self, other: ItemFilter) -> ItemPredicate:
        """Truthy check ANDed with *other* — ``item("is_enumerated") & p``."""
        return ItemPredicate(self) & other

    def __or__(self, other: ItemFilter) -> ItemPredicate:
        """Truthy check ORed with *other*."""
        return ItemPredicate(self) | other

    # ── Comparison operators ──────────────────────────────────────────────

    def __eq__(self, other: object) -> ItemPredicate:  # pyrefly: ignore[bad-override]
        attr = self._attr

        def equal(x: object) -> bool:
            return bool(getattr(x, attr) == other)

        return ItemPredicate(equal)

    def __ne__(self, other: object) -> ItemPredicate:  # pyrefly: ignore[bad-override]
        attr = self._attr

        def unequal(x: object) -> bool:
            return bool(getattr(x, attr) != other)

        return ItemPredicate(unequal)

    # ── Methods ───────────────────────────────────────────────────────────

    def is_in(self, values: Collection[Any]) -> ItemPredicate:
        """True if the attribute value is in *values*."""
        attr = self._attr
        value_set = frozenset(values)
        return ItemPredicate(lambda x: getattr(x, attr) in value_set)

    def is_null(self) -> ItemPredicate:
        """True if the attribute value is None."""
        attr = self._attr
        return ItemPredicate(lambda x: getattr(x, attr) is None)

    # ── Identity ─────────────────────────────────────────────────────────

    def __hash__(self) -> int:
        return hash(self._attr)

    def __repr__(self) -> str:
        return f"item({self._attr!r})"


def item(attr: str) -> ItemExpr:
    """Build a filter expression for an item attribute.

    Example:
        dims.filter(item("id") == "FREQ")
        dims.filter(item("is_enumerated"))
        dims.filter(~item("is_enumerated"))
        dims.filter(item("id").is_in(["FREQ", "GEO"]))
        dims.filter(item("codelist").is_null())
    """
    return ItemExpr(attr)
