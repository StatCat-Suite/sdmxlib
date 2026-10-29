"""Column references, paths and predicates.

An expression here is a *description*, not a step in a pipeline: building one
runs nothing and its meaning comes from the clause it is handed to. That is
polars' `Expr` layer, and it is the half of polars this design borrows — the
`LazyFrame` pipeline, where order matters throughout, is deliberately not
borrowed. See `docs/design/query-language.md`, "Have we become a pipeline?".

Two things are threaded through every type here.

**The row kind `R`** is the query's *root*, not the column's own relation. A
predicate is about a row of whatever the query selects from, reached through
joins, so `q.component.dsd.agency` is a `Str[ComponentRow]` even though
`agency_id` lives on `artefact`. Typed the other way round, a component query
would reject its own path.

**The path** is the sequence of declared edges walked to reach the column. It
is what the compiler turns into a join tree, and it is also a machine-checked
proof that the route exists — every dot is an edge that had to be declared.
"""

import builtins
import copy
import sys
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, date, datetime
from typing import Literal, NamedTuple, NoReturn, Self, TypeIs, cast, override

import attrs

from sdmxlib._time import to_storage
from sdmxlib.model._label_match import Fold
from sdmxlib.model.version import sort_key as version_sort_key
from sdmxlib.query._common import Descending, canonical_key
from sdmxlib.query._joins import STRUCTURAL_SCHEMA, KeyRank, PathError, RootJoin, trim
from sdmxlib.sql import SqlRelation


def _refuse_comparison(subject: str, got: object, fix: str) -> NoReturn:
    """The refusal every expression type gives for an operator it does not build.

    Python's default `==` is an identity check, so an expression type that does
    not define one yields `False` — a filter that compiles, runs and matches
    nothing. Three of those shipped in this milestone (`|` over a column,
    `Agg` without `__eq__`, and this family), which is why the surface now has
    a derived guard in `tests/unit/test_query_expression_surface.py` rather
    than three separate fixes.

    polars sets the precedent for the truthiness half — `bool(Expr)` raises
    "the truth value of an Expr is ambiguous" rather than defaulting to True.
    """
    msg = f"{subject} cannot be compared — got {type(got).__name__}. {fix}"
    raise TypeError(msg)


def _ambiguous_truth(subject: str) -> str:
    """`bool()` on an expression, which is always `True` and never meant.

    `if q.code.id == "A":` reads as a test and is a tautology. polars refuses
    the same way and for the same reason.
    """
    return (
        f"the truth value of {subject} is ambiguous — it describes a query, it does not run one. "
        f"`and` / `or` / `not` cannot be overloaded in Python, so they would silently drop an "
        f"operand: use `&`, `|` and `~`, and parenthesise each comparison. Several predicates "
        f"passed to filter() are ANDed, which needs no operator at all."
    )


class RowKind:
    """Marker for the relation a query is rooted at. Never instantiated.

    A pure phantom: it carries no behaviour and exists so that a predicate
    built for one root cannot be handed to a query over another. That check is
    the only reason the parameter exists, and it is worth the noise — without
    it, `reg.query(q.component).filter(q.codelist.id == "X")` type-checks and
    then emits a `WHERE` against a table absent from the `FROM`.
    """

    __slots__ = ()


# ── predicates ────────────────────────────────────────────────────────────


class AnyPred:
    """Non-generic base for predicates, so the compiler can name one.

    `Pred[R]` is invariant in its row kind — `__and__` takes another `Pred[R]`,
    a contravariant position — so `Pred[ComponentRow]` does not satisfy a
    `Pred[RowKind]` parameter. The compiler erases row kinds anyway: they exist
    to stop a predicate reaching the wrong query, and by compile time that has
    already been checked. So it takes this instead.
    """

    __slots__ = ()


class Pred[R: RowKind](AnyPred):
    """A boolean expression over one root's rows."""

    __slots__ = ()

    def __and__(self, other: Pred[R]) -> Pred[R]:
        _require_predicate("&", other)
        return _combine("and", self, other)

    def __or__(self, other: Pred[R]) -> Pred[R]:
        _require_predicate("|", other)
        return _combine("or", self, other)

    def __invert__(self) -> Pred[R]:
        # `~~p` is `p`. Not an optimisation — `NOT (NOT x)` is `x` even under
        # three-valued logic, since NULL negates to NULL — but it keeps the
        # tree readable and makes the canonical key of a double negation the
        # key of what it negates.
        if isinstance(self, Not):
            return self.part
        return Not(self)

    # Predicate equality is refused rather than fixed, because it never worked:
    # attrs generated `self.column == other.column and ...`, and the column arm
    # *builds a `Compare`* rather than comparing. Being always truthy, it fell
    # through to the value — so `(code.id == "A") == (code.name["en"] == "A")`
    # reported True for two different columns. Nothing relied on it; use
    # `canonical_key()` for query identity, which is what it is for.
    def __eq__(self, other: object) -> bool:
        _refuse_comparison(
            "a predicate",
            other,
            "Combine predicates with `&` (and), `|` (or) and `~` (not), or pass several to "
            "filter(), which ANDs them. To compare two *queries*, use canonical_key().",
        )

    def __ne__(self, other: object) -> bool:
        _refuse_comparison(
            "a predicate",
            other,
            "Combine predicates with `&` (and), `|` (or) and `~` (not), or pass several to "
            "filter(), which ANDs them. To compare two *queries*, use canonical_key().",
        )

    def __bool__(self) -> bool:
        msg = _ambiguous_truth("a predicate")
        raise TypeError(msg)

    # `__eq__` refuses rather than compares, so identity is the only sane hash.
    def __hash__(self) -> int:
        return id(self)


def _combine[R: RowKind](op: str, left: Pred[R], right: Pred[R]) -> Pred[R]:
    """Build `left op right`, flattening operands of the same kind.

    `a & b & c` is one three-part node rather than two nested two-part ones,
    so the emitted SQL reads `(a AND b AND c)`. A mixed tree does not flatten:
    an `&` inside a `|` has to stay a subtree or the query means something
    else.
    """
    parts: list[Pred[R]] = []
    for side in (left, right):
        if isinstance(side, BoolOp) and side.op == op:
            parts.extend(side.parts)
        else:
            parts.append(side)
    return BoolOp(op, tuple(parts))


def _require_predicate(op: str, other: object) -> None:
    """Refuse `pred & column`, which Python's precedence makes easy to write.

    `&` and `|` bind *tighter* than `==`, so

        (q.dsd.agency == "A") | q.dsd.agency == "B"

    parses as `((pred) | column) == "B"`. Without this the `|` would build a
    `BoolOp` from a column, and the trailing `==` would then compare that node
    to a string and quietly yield `False` — a filter that runs and returns
    nothing. The older DSLs guarded it and this one did not; found while
    porting the cookbook page that warns about it.
    """
    if isinstance(other, AnyPred):
        return
    msg = (
        f"{op} needs a predicate on both sides, got {type(other).__name__}. "
        f"Python binds {op} tighter than ==, so parenthesise each comparison: "
        f'(q.code.id == "A") {op} (q.code.id == "B").'
    )
    raise TypeError(msg)


def _refuse_boolean_operand(op: str, other: object) -> NoReturn:
    """Refuse `&` / `|` with a column on either side.

    A column is not a question about a row, so it is never a boolean operand.
    Reached from both arms — see the comment on `AnyCol.__ror__` for why the
    reflected one is the important half.
    """
    msg = (
        f"{op} needs a predicate on both sides, got a column and "
        f"{type(other).__name__}. Python binds {op} tighter than ==, so "
        f'parenthesise each comparison: (q.code.id == "A") {op} '
        f'(q.code.id == "B").'
    )
    raise TypeError(msg)


@attrs.frozen(eq=False)
class Compare[R: RowKind](Pred[R]):
    """A column against a literal — the leaf of most predicates.

    `fold` is `Fold.NONE` unless the comparison came through `.str.caseless`,
    which is the only rung carrying equality.
    """

    column: AnyCol
    op: str
    value: object
    fold: Fold = Fold.NONE


@attrs.frozen(eq=False)
class IsNull[R: RowKind](Pred[R]):
    column: Col[R]
    negate: bool = False


@attrs.frozen(eq=False)
class Match[R: RowKind](Pred[R]):
    """A string match at one folding strength.

    `fold` is `_label_match.Fold`, whose ladder is total: each rung ignores
    everything the one above it does. The SQL for every rung is built by
    `_label_match`, shared with the existing DSLs rather than reimplemented —
    #222 and #255 are what that module cost, and a second implementation would
    be a second chance to get three-valued matching and `LIKE` escaping wrong.
    """

    column: AnyCol
    op: str
    fold: Fold
    value: str


@attrs.frozen(eq=False)
class ArrayHas[R: RowKind](Pred[R]):
    """A list column contains a value — SQL's `? = ANY(col)`.

    The inverse of `InSet`, and worth keeping distinct: `InSet` asks whether a
    scalar column is among *caller-supplied* values, this asks whether a
    caller-supplied value is among a *stored list*.
    """

    column: AnyCol
    value: object


@attrs.frozen(eq=False)
class InSet[R: RowKind](Pred[R]):
    column: Col[R]
    values: tuple[object, ...]


@attrs.frozen(eq=False)
class InRelation[R: RowKind](Pred[R]):
    """`column IN (<subquery>)` against a foreign single-column relation.

    The composable half of the escape hatch, and the seam
    `sdmxlib.sql.SqlRelation` already described: one `IN (SELECT ...)` rather
    than a fetched list spliced back in as N binds. The subquery's tables must
    be visible on the consumer's connection, which they are whenever both sides
    share one — the `DuckDBDataStore` / `LocalRegistry` case.

    `source` is that requirement made checkable (§5.9). It is the token the
    relation reported via `sdmxlib.sql.HasSource`, or `None` when the relation
    could not say — carried on the predicate because `is_in` sees the relation
    and only `filter` sees the consumer.
    """

    column: AnyCol
    sql: str
    params: tuple[object, ...]
    source: object = None
    #: How to name that source in a refusal. See `Query.source_label`.
    source_label: str | None = None


@attrs.frozen(eq=False)
class RowIn[R: RowKind](Pred[R]):
    """The root's key is (or is not) among the rows of a compiled subquery.

    How the set operations are expressed. `intersect` is a semi-join and
    `difference` an anti-join — so both are *predicates on the left query*,
    which makes them refinements structurally rather than by promise: the
    result is the same `Query` with one more `WHERE`, and every clause
    downstream of it behaves exactly as it did.

    Compiled as `EXISTS` rather than `IN`. `NOT IN` against a subquery
    containing a NULL returns no rows at all, and `__key__` is only guaranteed
    *unique* — a unique index permits nulls. `NOT EXISTS` gives anti-join
    semantics either way.

    `columns` are physical column names on the root's own table, in the same
    order the subquery projects them.
    """

    columns: tuple[str, ...]
    sql: str
    params: tuple[object, ...]
    negate: bool = False


@attrs.frozen(eq=False)
class BoolOp[R: RowKind](Pred[R]):
    op: str
    parts: tuple[Pred[R], ...]


@attrs.frozen(eq=False)
class Not[R: RowKind](Pred[R]):
    part: Pred[R]


@attrs.frozen
class AnyCursor:
    """Non-generic base for cursors, for the reason `AnyPred` and `AnyAgg` are.

    `Cursor[R]` is invariant, so it does not satisfy a `Cursor[RowKind]`
    parameter. The row kind is checked at `after()`; the compiler only needs
    the values and their directions, and they live here so it can read them off
    whatever it was handed.
    """

    values: tuple[object, ...]
    ascending: tuple[bool, ...]


@attrs.frozen
class Cursor[R: RowKind](AnyCursor):
    """An opaque position in one query's order — where the next page starts.

    Carries the sort-key values of the last row of a page, in the order the
    `ORDER BY` emitted them, plus the direction of each. `after()` turns that
    into the lexicographic "strictly after this row" comparison, which is what
    makes keyset paging skip no rows and repeat none even while rows are being
    inserted underneath it — the failure `offset` has and cannot fix.

    Parameterised on the row kind so a cursor from one query cannot be handed
    to another. The directions travel with it because a cursor is only
    meaningful against the order that produced it: `after()` checks them and
    refuses a cursor whose shape no longer matches.

    Do not construct one; take it from `Query.page`.
    """


@attrs.frozen
class OrderKey:
    """One resolved `ORDER BY` term: a column and its direction.

    Non-generic, for the reason `AnyPred` is: the row kind is checked at the
    boundary — `order_by` takes `Col[R]`, so a key built for another root is
    rejected there — and by compile time it has done its job. A generic
    `OrderKey[R]` would be invariant and so would not satisfy an
    `OrderKey[RowKind]` parameter, which is the same wall `Pred[R]` hit.
    """

    column: AnyCol
    ascending: bool = True


@attrs.frozen
class AnyAgg:
    """Non-generic base for aggregates, for the reason `AnyPred` exists.

    `Agg[R]` is invariant, so `Agg[DsdRow]` does not satisfy an `Agg[RowKind]`
    parameter. The compiler erases row kinds anyway — by then `select` has
    already rejected an aggregate built for another root — so it takes this.

    The *data* lives here rather than being declared here and supplied by the
    subclass: an attrs field re-annotated in a subclass reads as an
    inconsistent override, and the compiler needs to read `kind` and `columns`
    off whatever it narrowed to.
    """

    kind: str
    columns: tuple[AnyCol, ...]
    #: Child-rooted predicates restricting *which* children are collected —
    #: SQL's `FILTER (WHERE ...)`, and the `where=` keyword every aggregate
    #: takes (#373). Empty means the whole fan-out, which is what an
    #: unrestricted aggregate has always meant.
    #:
    #: Evaluated **per candidate child**: each is inspected on its own and
    #: either collected or not. So the same-child/different-child question
    #: `.any(p, q)` exists to answer does not arise here — there is no second
    #: child for a condition to land on — and AND/OR is ordinary composition,
    #: `where=p & q` and `where=p | q`, rather than anything this keyword has
    #: to spell for itself.
    where: tuple[AnyPred, ...] = ()

    def __canonical__(self) -> tuple[object, ...]:
        """Structural identity, as `AnyCol.__canonical__` gives columns.

        `__eq__` is spoken for — it builds a predicate — and `__hash__` is
        identity, so an aggregate had no way to say it *means* the same thing
        as another. `Select.order_by` matched columns structurally and
        aggregates by `is`, which made two textually identical programs behave
        differently:

            agg = q.codelist.codes.count()
            select(n=agg).order_by(agg)              # worked
            select(n=count()).order_by(count())      # TypeError

        Reads the columns' own canonical form rather than the objects, for the
        same reason: every accessor read builds a fresh `Col`.

        Declared here rather than on `Agg` because `kind` and `columns` are
        here, and the compiler reads them off whatever it narrowed to.
        """
        return (
            "agg",
            self.kind,
            tuple(column.__canonical__() for column in self.columns),
            # `where=` is part of what an aggregate *means*: two `count()`s
            # differing only by their restriction are different computations,
            # and leaving this out would let `order_by` match one against the
            # other — the identity bug this method exists to fix, one level in.
            #
            # Through `canonical_key` rather than `__canonical__`: a predicate
            # does not implement that protocol, it *is* an attrs value the
            # walker reads structurally — which is also what makes a
            # conjunction inside the restriction order-insensitive here.
            tuple(canonical_key(predicate) for predicate in self.where),
        )


@attrs.frozen(eq=False)
class AggPred(AnyPred):
    """A test on an aggregate — the vocabulary `having()` is written in.

    Exported, and abstract, for the reason `WinPred` is: a clause's signature
    has to name what it takes, and naming the *concrete* node would publish
    compiler detail. `test_public_surface` holds both halves of that — every
    type in a public signature must be importable, and no concrete expression
    node may be.

    Non-generic for the reason `AnyAgg` is: `AggCompare[R]` is invariant
    through `Pred[R]`, so a frame's own predicate does not satisfy an
    `AggCompare[RowKind]` parameter. Row kinds are erased by compile time and
    `having()` has already checked the predicate belongs to this root.

    The data lives here so the compiler can read it off whatever it narrowed
    to — the same reasoning as `AnyAgg`.
    """

    agg: AnyAgg
    op: str
    value: object


@attrs.frozen(eq=False)
class AggCompare[R: RowKind](AggPred, Pred[R]):
    """An aggregate over a fan-out, compared to a literal.

    `q.code.children.count() == 0` — "how many children, and is that zero".
    Compiles to a correlated *scalar* subquery in the `WHERE`, so it is
    row-preserving like the projected form and unlike a `GROUP BY … HAVING`.

    This is the affirmative way to ask about an empty fan-out, and the one the
    peers converged on: polars spells it `list.len() == 0`, ibis
    `length() == 0`, Django `annotate(Count(...)).filter(n=0)`. SQLAlchemy's
    `children == None` is affirmative too but reads as a null test.

    Django's other spelling, `filter(children__isnull=True)`, is a `LEFT JOIN`
    plus `IS NULL` — deliberately not copied, because the join multiplies rows
    in the positive direction where a correlated subquery does not.
    """


@attrs.frozen(eq=False)
class Agg[R: RowKind](AnyAgg):
    """A projected aggregate over the fan-out a column's path crosses.

    `kind` is `list`, `count` or `n_unique`. `columns` is what is aggregated —
    several only for `list`, which then produces a list of **structs**.

    That multi-column form is not sugar. Two separate `list()` subqueries over
    one child align only incidentally: `list()` has no defined order without an
    `ORDER BY`, so `labels[i]` is not reliably the label of `codes[i]`, and it
    diverges the moment either side gains an order. A struct makes the
    alignment structural rather than promised, and costs one subquery instead
    of two. See `docs/design/query-language-examples.md` §4.
    """

    def desc(self) -> Descending[Agg[R]]:
        """This aggregate as a descending sort key — the top-N shape.

        `group_by(...).agg(n=...).order_by(n.desc()).limit(1)` is why a lazy
        projection is worth having: sorting a materialised frame has already
        fetched every row. Same `Descending` marker a column uses, so the
        spelling does not change with what is being sorted.

        Declared here rather than on `AnyAgg` so the key names the row kind.
        `AnyAgg` is the non-generic base
        the compiler narrows to and is not exported, so a signature returning
        `Descending[AnyAgg]` asked a caller to name a type they cannot import.
        Moving it down rather than overriding it is what `Col.desc` already
        does: `Descending` is `@frozen` over a single read-only field, so
        covariance would be sound, but attrs declares `key: K` as a plain
        mutable attribute and PEP 695 therefore infers `K` invariant — which
        makes the narrowing an incompatible override. There is no base method
        to conflict with once the declaration is here.
        """
        return Descending(self)

    # Comparisons build an `AggCompare`, so value-equality is off here for the
    # same reason it is off on `Win`: the override is genuinely incompatible
    # with `object.__eq__`, and that incompatibility *is* the feature — it is
    # what makes `count() == 0` a predicate rather than a bool.
    #
    # Approved suppression, on the grounds that it mirrors `Win.__eq__`
    # exactly — same override, same reason, same pair of checkers.
    #
    # Declared `AggCompare[R]`, which is what all six return (#414). They said
    # `Pred[R]`, so `having(col.count() > 1)` — whose parameter is `AggPred` —
    # was a type error for every typed caller; the existing `having` tests
    # passed only because their fixture is untyped. `AggCompare` is both an
    # `AggPred` and a `Pred[R]`, so `filter` still takes it unchanged.
    def __hash__(self) -> int:
        return id(self)

    def __eq__(self, other: object) -> AggCompare[R]:  # pyrefly: ignore[bad-override]
        return _agg_compared(self, "=", other)

    def __ne__(self, other: object) -> AggCompare[R]:  # pyrefly: ignore[bad-override]
        return _agg_compared(self, "!=", other)

    def __bool__(self) -> bool:
        msg = _ambiguous_truth("an aggregate")
        raise TypeError(msg)

    def __gt__(self, other: object) -> AggCompare[R]:
        return _agg_compared(self, ">", other)

    def __ge__(self, other: object) -> AggCompare[R]:
        return _agg_compared(self, ">=", other)

    def __lt__(self, other: object) -> AggCompare[R]:
        return _agg_compared(self, "<", other)

    def __le__(self, other: object) -> AggCompare[R]:
        return _agg_compared(self, "<=", other)

    def over(self, *partition: Col[R]) -> Win[R]:
        """Recompute this aggregate as a window function.

        `q.dsd.id.count()` counts a fan-out's children; `.over(q.dsd.agency)`
        instead counts the *rows of the outer query* sharing an agency. Same
        SQL function, a different thing entirely — which is why the framing is
        a separate call rather than an argument.
        """
        return Win(self.columns, self.kind, tuple(partition))


def _agg_compared[R: RowKind](agg: Agg[R], op: str, other: object) -> AggCompare[R]:
    """An aggregate compared with a value — and only a value (#511).

    Two aggregates, or an aggregate and a column, reached DuckDB as
    `Unable to transform python value of type Agg`: the right-hand side was
    bound as a parameter. Comparing two expressions is not in the language
    yet (`query-language.md` §13, the first open question).

    Raises:
        TypeError: If `other` is an aggregate or a column.
    """
    if isinstance(other, AnyAgg | AnyCol):
        msg = (
            f"an aggregate can be compared with a value, not with {_expression_kind(other)}: comparing "
            "two expressions is not supported yet. Select both and compare them in polars."
        )
        raise TypeError(msg)
    return AggCompare(agg, op, other)


def _expression_kind(value: object) -> str:
    return "another aggregate" if isinstance(value, AnyAgg) else "a column"


# ── columns ───────────────────────────────────────────────────────────────


class AnyCol:
    """Non-generic base for column references, for the same reason as `AnyPred`.

    Carries everything the compiler reads — the path, the relation and the
    physical column — none of which depend on the row kind.
    """

    __slots__ = ("_column", "_order", "_origin", "_path", "_projected", "_table")

    def __init__(self, path: tuple[str, ...], table: str, column: str) -> None:
        self._path = path
        self._table = table
        self._column = column
        #: The root accessor this column was read from — `_Dataflow` for
        #: `q.dataflow.dsd.id` — or `None` for a declaration and a column built
        #: at a call site. Set by `_carry` as the column is bound. `path` and
        #: `table` cannot say it: every maintainable roots at `sdmx.artefact`,
        #: so `q.codelist.agency` and `q.dataflow.agency` are otherwise the same
        #: column, and a query over one silently answered for the other (#510).
        self._origin: type[AnyCols] | None = None
        #: `(column, descending)` an aggregate over this column orders by, or
        #: `None` for the scan's own order. Set by `sort_by` / `sort`.
        self._order: tuple[AnyCol, bool] | None = None
        #: How the caller spells this column — `id`, not `resource_id`. Set by
        #: `__set_name__` on the declaration and carried onto every bound copy
        #: by `_carry`. Empty for a column built at a call site rather than
        #: declared on an accessor, which then falls back to the physical name.
        self._projected = ""

    def __set_name__(self, owner: type, name: str) -> None:
        """Record the attribute name this column is declared under.

        The one place the accessor's spelling is known. `q.code.id` is
        `col("code", "code_id")` on a class attribute called `id`, and nothing
        downstream can recover `id` from the declaration — so it is captured
        here, at class creation, rather than reverse-engineered.

        Fires once per declaration, including inherited ones: `_Maintainable`
        declares `id` and seventeen accessors inherit that same object.
        """
        self._projected = name

    def alias(self, name: str) -> Self:
        """Name this column in the output.

        The universal escape, and the only way to name an expression *itself*
        rather than the call site it appears at. `select(**named)` handles the
        top level; `alias` also reaches inside `Many.list`, whose struct fields
        take their names from the columns passed to it and which is positional
        by design:

            codes.list(codes.id, codes.parent.id.alias("parent"))

        Without it those are two struct fields called `id` and the database
        refuses the query, with no way for the caller to intervene (#363).

        Named for SQL's `AS` and for polars' method of the same name. A prefix
        or suffix rule is deliberately absent: polars has `name.prefix` because
        one polars expression can expand to *many* columns, where a single
        replacement name cannot apply. Every projectable thing here is exactly
        one column — `select` refuses an accessor and refuses a bare aggregate
        — so a rule could only ever be a longer spelling of this method.
        """
        renamed = copy.copy(self)
        renamed._projected = name
        return renamed

    def _carry(self, bound: Self, reader: AnyCols) -> Self:
        """Copy declaration-only state onto a freshly bound copy.

        `__get__` rebuilds through each subclass's own constructor — the
        established pattern for columns carrying extra state — and the spelling
        is not a constructor argument, because it belongs to the *declaration*
        rather than to the column's identity. So it is copied after the
        rebuild, in one place, and every `__get__` routes through here.

        `reader` is the accessor the column is read from; its `origin` is what
        `Query.filter` holds a predicate's column to (#510).
        """
        bound._projected = self._projected
        bound._order = self._order
        bound._origin = reader.origin
        return bound

    @property
    def origin(self) -> type[AnyCols] | None:
        """The root accessor this column was reached from, or `None` if unknown."""
        return self._origin

    @property
    def path(self) -> tuple[str, ...]:
        """Declared edge names walked from the root, outermost first."""
        return self._path

    @property
    def table(self) -> str:
        """Relation the column physically lives on."""
        return self._table

    @property
    def column(self) -> str:
        """Physical column name."""
        return self._column

    @property
    def projected_name(self) -> str:
        """What this column is called when projected without an explicit alias.

        **How the caller spelled it.** `select(q.code.id)` yields a column
        called `id`, not `code_id`; `q.dsd.type` yields `type`, not
        `artefact_type`.

        This used to be the physical column, on the reasoning that someone
        porting a hand-written `SELECT code_id …` would get a frame needing no
        renaming. That serves one audience — a raw-SQL migration — at the cost
        of every caller writing new code, who gets back a column they did not
        name and cannot guess: `df["id"]` raises `KeyError` because the frame
        says `resource_id`. Across the 34 accessors there were 29 such
        mismatches, concentrated on `id` (17 relations) and `type` (17).

        The deciding argument is that `ObsCol` already overrode this rule and
        recorded why — "the physical name is an implementation detail of the
        binding … getting back a column called `GEO` leaks the layout". The
        same is true of `resource_id`, and the library applied opposite
        conventions on either side of one `select()`. Now it applies one (#363).

        Raw SQL is unaffected: `reg.sql()` and `sl.schema` name physical
        columns, which is correct there and is what that seam is for.

        Falls back to `self.column` — the *property*, not the slot — for a
        column built at a call site rather than declared on an accessor.
        `Label` overrides to append the language, since `select(name["en"],
        name["fr"])` needs `name_en` and `name_fr` or one silently wins.
        """
        if self._projected:
            return self._projected
        return self.column

    @property
    def sort_column(self) -> str:
        """Physical column to *order* by, usually the column itself.

        The exception is `version`, whose text ordering puts `1.9` after
        `1.10` (#311). Separate from `column` rather than replacing it,
        because the two questions differ: a comparison must read what the
        registry published, and only the sort wants the baked key.

        Defaults to `column`, the *public* property, not the `_column` slot.
        `Label` overrides `column` — a localised value is stored in a `MAP`
        and read as `name[?]` — so defaulting to the slot made every label's
        sort key differ from its own reference, and the compiler emitted
        `ORDER BY name` without the language bind. The SQL corpus caught it.
        """
        return self.column

    def __bool__(self) -> bool:
        msg = _ambiguous_truth("a column")
        raise TypeError(msg)

    # The *reflected* arm of the precedence trap, and the one a guard on
    # `Pred` cannot reach. `q.code.id == "A" | q.code.id == "B"` parses as
    # `q.code.id == ("A" | q.code.id)`, so Python calls `str.__or__(col)`,
    # gets `NotImplemented`, and falls through to `__ror__` here — the
    # predicate's operator is never involved. Without these the caller sees
    # "unsupported operand type(s) for |: 'str' and 'Str'", which names a
    # private type and no fix.
    def __ror__(self, other: object) -> NoReturn:
        _refuse_boolean_operand("|", other)

    def __rand__(self, other: object) -> NoReturn:
        _refuse_boolean_operand("&", other)

    def __or__(self, other: object) -> NoReturn:
        _refuse_boolean_operand("|", other)

    def __and__(self, other: object) -> NoReturn:
        _refuse_boolean_operand("&", other)

    def __get__(self, obj: object, owner: type | None = None) -> Self:
        """Bind this declaration to the path of the accessor it is read from.

        A column declaration *is* a `Col`, so no cast is needed to make the
        annotation true — the class attribute and the value it yields are the
        same type, differing only in the path they carry.

        The bound path is *trimmed* here, at the one place a path is built, so
        `q.code.parent.id` and `q.code.parent_id` produce the same object and
        nothing downstream — planner, compiler, `canonical_key`, `explain` —
        can tell them apart or drift on them. See `_edges.trim`.
        """
        if not isinstance(obj, AnyCols):
            return self
        # `obj._path`, not `obj.path`: an accessor declaring a column called
        # `path` shadows the property, and reading it here would re-enter this
        # method for ever. Guarded as well — `test_query_accessors.py` refuses
        # the shadowing outright — but the guard cannot run while the recursion
        # exists, because collection walks every accessor attribute and dies
        # first. So the footgun is removed rather than only forbidden.
        path, table, column = trim(obj.root, (*obj._path, *self._path), self._table, self._column)
        return self._carry(type(self)(path, table, column), obj)

    def __canonical__(self) -> tuple[object, ...]:
        """Structural identity, for `canonical_key` (#270).

        The class name is part of it: a `Str` and a `Num` over the same column
        compare differently and would key the same without it. Subclasses
        carrying more — a language, a window frame, an operator tree — extend
        this rather than replacing it, so a new field cannot be forgotten
        silently.

        The sort key is part of it for the same reason the language is on a
        label: `codes.id.list()` and `codes.id.sort().list()` are different
        questions, and keying them alike would let a cached answer to one serve
        the other. The *alias* deliberately is not — renaming a column does not
        change which rows come back, and `select(a=…)` and `select(b=…)` should
        share a fetch.
        """
        order = None if self._order is None else (self._order[0].__canonical__(), self._order[1])
        return ("col", type(self).__name__, self._path, self._table, self._column, order)

    @override
    def __repr__(self) -> str:
        route = ".".join((*self._path, self._column))
        return f"<{type(self).__name__} {route} ({self._table}.{self._column})>"


class Col[R: RowKind](AnyCol):
    """A column reference, carrying the path walked to reach it.

    Not an attrs class: `__eq__` is overridden to build a predicate rather than
    to compare, which is the one place this API departs from Python's usual
    meaning and the reason `sl.q.` exists as a visible marker.
    """

    __slots__ = ()

    # `__eq__` builds a predicate, so the usual identity contract is off.
    # Hashing by identity keeps these usable as dict keys during compilation.
    @override
    def __hash__(self) -> int:
        return id(self)

    @override
    def __eq__(self, other: object) -> Pred[R]:  # pyrefly: ignore[bad-override]
        if other is None:
            # `= NULL` is never true in SQL, so this compiled, ran and matched
            # nothing. The older DSLs refused it; this one did not until §3
            # ported the test that said so.
            msg = (
                "comparing to None matches nothing — `= NULL` is never true in SQL. "
                "Use is_null(), or is_not_null() for the complement."
            )
            raise TypeError(msg)
        return Compare(self, "=", other)

    @override
    def __ne__(self, other: object) -> Pred[R]:  # pyrefly: ignore[bad-override]
        return Compare(self, "!=", other)

    def list(self, *, where: AnyPred | None = None) -> Agg[R]:
        """Collect this column across the fan-out its path crosses.

        Args:
            where: Restrict which children are collected — or, on a column
                that does not fan out, under `agg` or `group_by`, which of the
                group's rows; every aggregate here takes it the same way.
                `AnyPred` rather than `Pred[C]` because a column does not
                carry the child's row kind — the fan-out does, so
                `fan.list(col, where=…)` is the spelling the checker can hold
                to one relation. Here the predicate is checked when the query
                compiles.
        """
        return Agg[R]("list", (self,), _where(where))

    def count(self, *, where: AnyPred | None = None) -> Agg[R]:
        """Count the children the fan-out reaches — `count(*)`, not of values."""
        return Agg[R]("count", (self,), _where(where))

    def n_unique(self, *, where: AnyPred | None = None) -> Agg[R]:
        """Count distinct values of this column across the fan-out."""
        return Agg[R]("n_unique", (self,), _where(where))

    def sum(self, *, where: AnyPred | None = None) -> Agg[R]:
        """Total this column across the fan-out.

        Numeric columns only. The compiler carries no column types, so a
        `sum` over text is refused by DuckDB — `No function matches the given
        name and argument types 'sum(VARCHAR)'` — rather than here, because the
        compiler carries no column types to check it against.
        """
        return Agg[R]("sum", (self,), _where(where))

    def mean(self) -> Agg[R]:
        """Average this column across the fan-out — SQL's `avg`.

        Named for polars, like every other aggregate here, and compiled to the
        SQL spelling the way `n_unique` becomes `count(DISTINCT …)`.

        **Nulls are skipped, not counted as zero**, so the mean of `[2, NULL]`
        is 2 rather than 1. SQL and polars agree on this.
        """
        return Agg[R]("mean", (self,))

    def min(self) -> Agg[R]:
        """Smallest value of this column across the fan-out.

        Not restricted to numbers: SQL and polars both order text, so
        `min` over a code id is its lexicographic first. That makes `min`/`max`
        usable where `sum`/`mean` are not.
        """
        return Agg[R]("min", (self,))

    def max(self) -> Agg[R]:
        """Largest value of this column across the fan-out. See `min`."""
        return Agg[R]("max", (self,))

    def unique(self) -> Agg[R]:
        """The distinct values of this column across the fan-out.

        `list` keeps duplicates, `n_unique` counts them away and returns a
        number; this is the third corner — *which* values, each once.

        The two `unique`s in this language mean different things and are not
        interchangeable, which is worth reading carefully once:
        `Projection.unique()` deduplicates whole **rows** (`SELECT DISTINCT`),
        and this deduplicates **values** inside one fan-out. Both names are
        polars', where `DataFrame.unique()` and `Expr.unique()` divide exactly
        the same way, so importing that split imports a distinction users of
        polars already hold.

        `n_unique()` is kept rather than left as `unique().count()` for the
        reason polars keeps it: `count(DISTINCT x)` is a single pass, and
        counting a materialised distinct list is not.
        """
        return Agg[R]("unique", (self,))

    def rank(self, *, descending: bool = False) -> WinFn[R]:
        """Rank rows by this column within a window — ties share a rank.

        The framing is a separate call: `.rank()` says *what* is computed and
        `.over(...)` says *over which rows*. "The latest version of each
        artefact" is `version.rank(descending=True).over(agency, id) == 1`.
        """
        return WinFn(self, "rank", descending=descending)

    def dense_rank(self, *, descending: bool = False) -> WinFn[R]:
        """Like `rank`, but without gaps after a tie."""
        return WinFn(self, "dense_rank", descending=descending)

    def row_number(self, *, descending: bool = False) -> WinFn[R]:
        """Number rows by this column within a window — ties broken arbitrarily.

        Use `rank` when tied rows should share a number. `row_number` picks one
        of them, and which one is not defined.
        """
        return WinFn(self, "row_number", descending=descending)

    def lag(self, offset: int = 1, *, descending: bool = False) -> WinFn[R]:
        """This column's value `offset` rows earlier in the window."""
        return WinFn(self, "lag", descending=descending, offset=offset)

    def lead(self, offset: int = 1, *, descending: bool = False) -> WinFn[R]:
        """This column's value `offset` rows later in the window."""
        return WinFn(self, "lead", descending=descending, offset=offset)

    def sort_by(self, key: Col[R], *, descending: bool = False) -> Self:
        """Order an aggregate over this column by `key`.

        Ordering is a property of the *expression* rather than an argument to
        the aggregate — polars' shape, where `pl.col("x").sort_by("ord")`
        composes with whatever aggregates it afterwards.

            codes.id.sort_by(codes.position).list()

        `key` must cross the same fan-out as the column being collected; a key
        from outside it has no value per collected row, and is refused where
        the aggregate's own columns are checked.

        Declared on `Col[R]` rather than on `AnyCol`, where the implementation
        would otherwise sit, so the parameter can name the row kind. `AnyCol`
        is the non-generic base the compiler reads and is not part of the
        public surface — a signature mentioning it asks a caller to name a type
        they cannot import.

        Without an order an aggregate returns whatever the scan produced, which
        is not an order at all — the hazard `__key__` already removes from
        `ORDER BY`, where two rows equal on the caller's keys "come back in
        whatever order the scan produced". The default is unchanged:
        an unsorted `list()` is still scan-ordered, because adding an
        `ORDER BY` to every aggregate costs a sort nobody asked for.
        """
        ordered = copy.copy(self)
        ordered._order = (key, descending)
        return ordered

    def sort(self, *, descending: bool = False) -> Self:
        """Order an aggregate over this column by the column itself.

            codes.id.sort().list()      # ['CA', 'MX', 'US', 'W']

        The only ordering `unique()` accepts. A `DISTINCT` aggregate collapses
        the rows any other key would have come from — which duplicate's key
        would it use? — so SQL requires the sort key to be the distinct
        expression, and DuckDB refuses anything else outright.
        """
        return self.sort_by(self, descending=descending)

    def desc(self) -> Descending[Col[R]]:
        """This column as a descending sort key.

        `Descending` is the one the existing DSLs already use — direction
        marked on the *key* rather than a `descending=[...]` list parallel to
        the keys, as SQLAlchemy, ibis and Django all do. Shared rather than
        restated so `.desc()` means one thing across every DSL here, as the
        migration this milestone ends in requires.

        There is no `asc()`: an unmarked key is ascending.
        """
        return Descending(self)

    def is_null(self) -> Pred[R]:
        return IsNull(self)

    def is_not_null(self) -> Pred[R]:
        return IsNull(self, negate=True)

    def is_in(self, values: Iterable[object] | SqlRelation) -> Pred[R]:
        """Membership, in a collection of values or in a single-column relation.

        The relation form takes anything satisfying `sdmxlib.sql.SqlRelation` —
        a one-column `Select` (`q.select(col)`), or a producer from another
        library — and compiles to one `IN (SELECT ...)`. `reg.sql(...)` is
        refused: raw text carries no row kind to check. Fetching its rows and passing them as a list
        would work and is what a caller does without this, at the cost of N
        binds and a round trip.

        A bare `str` is rejected: `is_in("CA")` silently meaning
        `is_in(["C", "A"])` shipped once, found only by placing the API
        beside polars, which rejects it.

        Raises:
            TypeError: On a `str`/`bytes`, or on a relation with more than one
                column — `IN` needs exactly one, and a column-count error at
                the database names neither the call site nor the fix.
        """
        if _is_relation(values):
            columns = values.columns
            if len(columns) != 1:
                got = ", ".join(columns) or "none"
                # A relation that offers a named one-column view says so, and
                # `AncestorMap` is the case: two columns because it is a
                # *mapping*, used as a join input, with `.ancestors()` for the
                # membership question. "Select one column in the subquery" is
                # the right advice for a projection and useless for that one.
                fix = (
                    "Narrow it with .ancestors()."
                    if callable(getattr(values, "ancestors", None))
                    else "Select one column in the subquery."
                )
                msg = f"is_in() needs a one-column relation, got {len(columns)} ({got}). {fix}"
                raise TypeError(msg)
            sql, params = values.to_sql()
            source, label = _source_of(values)
            return InRelation(self, sql, tuple(params), source=source, source_label=label)
        if isinstance(values, str | bytes):
            msg = (
                f"is_in() takes an iterable of values, not {type(values).__name__!r}. "
                f"A string is an iterable of characters, so is_in({values!r}) would "
                f"mean is_in({list(values)!r}). Pass a list: is_in([{values!r}])."
            )
            raise TypeError(msg)
        if isinstance(values, Mapping):
            # Iterable, and yields its *keys* — the same shape as the bare
            # string (#261) and just as silent. Named separately because the
            # fix is different: the caller usually wants `.values()`.
            msg = (
                f"is_in() takes an iterable of values, got a {type(values).__name__!r} — "
                f"iterating it would use its keys. Pass the values you mean: "
                f"is_in(list(mapping.values()))."
            )
            raise TypeError(msg)
        if type(values).__name__ == "RawSql":
            # Named rather than imported: `raw` imports this module, and the
            # check is a courtesy over a refusal that already happens — `RawSql`
            # carries no `to_sql`, so it misses `_is_relation` and would fall
            # through to "not iterable", naming neither the boundary nor the fix.
            msg = (
                "is_in() will not splice reg.sql() into a structural query. "
                "Raw SQL is a terminal: it has no row kind to check against "
                "this column, and it cannot be pushed down to a non-DuckDB "
                "backend. Materialise the crossing instead — "
                'is_in(reg.sql(...).collect()["<column>"]) — or use a query, '
                "which composes lazily: is_in(reg.query(...).select(...))."
            )
            raise TypeError(msg)
        return InSet(self, tuple(values))


def _check_literal(column: AnyCol, holds: str, value: object) -> None:
    """Refuse a literal whose type the column cannot hold.

    No checker can catch it — `__eq__` overrides `object.__eq__` and cannot
    narrow its parameter — so this is a runtime guard, and it names the field
    because "str, got int" alone barely improves on what DuckDB would say.

    `bool` is refused for an `int` column though it is a subclass: `depth ==
    True` is never intended, and it is the same reasoning `check_limit` uses
    to reject `limit(True)`.
    """
    if value is None:
        return
    if isinstance(value, AnyCol | AnyAgg):
        field = ".".join((*column.path, column.column)) if column.path else column.column
        msg = (
            f"{field} is compared with {_expression_kind(value)}, which is not supported yet — a "
            "comparison takes a value (#511; query-language.md §13). Select both and compare "
            "them in polars."
        )
        raise TypeError(msg)
    if holds == "str":
        ok = isinstance(value, builtins.str)
    elif holds == "bool":
        ok = isinstance(value, bool)
    else:
        ok = isinstance(value, int | float) and not isinstance(value, bool)
    if ok:
        return
    raise _literal_type_error(column, holds, value)


def _literal_type_error(column: AnyCol, holds: str, value: object) -> TypeError:
    """The refusal `_check_literal` raises, for a column checking its own way."""
    field = ".".join((*column.path, column.column)) if column.path else column.column
    suggestion = f' Did you mean "{value}"?' if holds == "str" else ""
    return TypeError(f"{field} holds {holds}, got {type(value).__name__}.{suggestion}")


class Str[R: RowKind](Col[R]):
    """A text column."""

    __slots__ = ()

    @override
    def __eq__(self, other: object) -> Pred[R]:
        _check_literal(self, "str", other)
        return super().__eq__(other)

    @override
    def __ne__(self, other: object) -> Pred[R]:
        _check_literal(self, "str", other)
        return super().__ne__(other)

    # `__eq__` is overridden to build a predicate, so the identity hash `Col`
    # declares has to be restated — Python drops an inherited `__hash__` on any
    # class that defines `__eq__`.
    @override
    def __hash__(self) -> int:
        return id(self)

    @override
    def is_in(self, values: Iterable[object] | SqlRelation) -> Pred[R]:
        """As `Col.is_in`, plus the member-type check.

        Only the first member is checked. What that misses is a *mixed* set,
        and every realistic version of this mistake is homogeneous — an int
        literal list, a `range`, a dataframe column that came back int64.
        """
        # Every refusal `Col.is_in` names — bare `str`, `Mapping`, `RawSql`,
        # a multi-column relation — is left to it, and only a genuine iterable
        # of values is listed here. Checking the member type first would
        # replace those messages with "not iterable", which names neither the
        # boundary nor the fix; that shipped and is what this ordering pins.
        if _is_relation(values) or _is_opaque(values) or not _is_iterable(values):
            return super().is_in(values)
        listed = list(values)
        if listed:
            _check_literal(self, "str", listed[0])
        return super().is_in(listed)

    def __add__(self, other: Str[R] | builtins.str) -> Str[R]:
        """Concatenation — SQL's `||`, spelled as Python spells it for strings.

        `+` means addition on `Num` and concatenation here, exactly as in
        Python. The two cannot be confused because the operand types do not
        overlap: `q.code.position + "x"` and `q.code.id + 1` are both rejected.

        SQL's `||` yields `NULL` if either side is `NULL`, which is not what
        Python's `+` does — but it is what every other operation in this DSL
        does with a missing value, so it is left alone rather than wrapped in
        `coalesce`. A caller wanting the other reading says so.
        """
        return StrExpr("||", (self, other))

    def __radd__(self, other: builtins.str) -> Str[R]:
        return StrExpr("||", (other, self))

    def len(self) -> Num[R]:
        """Character length — SQL's `length()`, named for Python's `len`.

        A method rather than `__len__`, which must return a real `int`: the
        whole point is to defer the computation to the database.
        """
        return NumExpr("length", (self,))

    @property
    def str(self) -> StrNS[R]:
        """Matching operations, named for polars' `.str`.

        Note this shadows the builtin *inside this class body*: a later
        annotation of `str` here would resolve to the property, not to the
        type. Confirmed at runtime — `get_type_hints` returns the property
        object — and pyrefly catches it where basedpyright does not. Any
        further members needing the builtin must spell it `builtins.str`.
        """
        return StrNS(self)


class Label[R: RowKind](Str[R]):
    """One language of a localised label — `name["en"]`.

    Prose, so it carries the two folding rungs an id does not.
    """

    __slots__ = ("_lang",)

    def __init__(self, path: tuple[str, ...], table: str, column: str, lang: str) -> None:
        super().__init__(path, table, column)
        self._lang = lang

    @property
    def lang(self) -> str:
        """The language tag this label was keyed by."""
        return self._lang

    @property
    @override
    def column(self) -> builtins.str:
        """`name_en`, not `name` — the language is part of the output name.

        Two languages of one label are the case that forces this: `select(
        name["en"], name["fr"])` projects two columns, and without the suffix
        both are called `name` and one silently wins. The physical column is
        still `name`; `physical` is what the compiler reads.
        """
        return f"{self._column}_{self._lang}" if self._lang else self._column

    @property
    @override
    def projected_name(self) -> builtins.str:
        """`name_en` — the accessor spelling plus the language.

        Overridden because the base now prefers the declared spelling, which
        for a label is `name` and would lose the language: `select(name["en"],
        name["fr"])` would project two columns both called `name` and one would
        silently win. `column` already composes the two, and for a label the
        declared spelling and the stored field are the same word.
        """
        return self.column

    @property
    def physical(self) -> builtins.str:
        """The stored column, without the language suffix."""
        return self._column

    @property
    @override
    def str(self) -> LabelNS[R]:
        return LabelNS(self)

    @override
    def __canonical__(self) -> tuple[object, ...]:
        """Adds the language, which is exactly what the base loses.

        `name["en"]` and `name["fr"]` are the same path, table and column, so
        without this they key identically — and a cache would serve one
        language's rows for the other.
        """
        return (*super().__canonical__(), self._lang)

    @override
    def __get__(self, obj: object, owner: type | None = None) -> Self:
        if not isinstance(obj, AnyCols):
            return self
        return self._carry(type(self)((*obj._path, *self._path), self._table, self._column, self._lang), obj)


class SideLabel[R: RowKind](Label[R]):
    """A localised label held in a side table rather than inline.

    A maintainable's labels live in `sdmx.localized_text`, keyed by
    `(owner_urn, field, lang)` — one row per language, not a `MAP` column like
    `code.name`. It compiles to a correlated scalar subquery, which is
    row-preserving and yields `NULL` when the language is absent, exactly as a
    missing map key would.

    `field` is the `localized_text.field` value — `name`, `description` — and
    `owner` names the column on the reading relation that carries the URN the
    side table is keyed by. `key` names the column on the *side* table that
    `owner` is matched against.

    Two side tables have this shape, not one. `localized_text` is keyed by
    `(owner_urn, field, lang)` and holds every maintainable's labels;
    `annotation_text` is keyed by `(annotation_id, lang)` and holds one field,
    so it carries no discriminator. An empty `field` says so and drops the
    condition rather than emitting an always-true one.
    """

    __slots__ = ("_field", "_key", "_owner")

    def __init__(
        self,
        path: tuple[str, ...],
        table: str,
        field: str,
        owner: str,
        lang: str,
        key: str = "owner_urn",
    ) -> None:
        super().__init__(path, table, field, lang)
        self._field = field
        self._owner = owner
        self._key = key

    @override
    def __canonical__(self) -> tuple[object, ...]:
        return (*super().__canonical__(), self._field, self._owner, self._key)

    @property
    def field(self) -> str:
        """The `localized_text.field` this label is stored under, or `""`."""
        return self._field

    @property
    def owner(self) -> str:
        """Column on the reading relation holding the key it is joined by."""
        return self._owner

    @property
    def key(self) -> str:
        """Column on the side table that `owner` is matched against."""
        return self._key

    @override
    def __get__(self, obj: object, owner: type | None = None) -> Self:
        if not isinstance(obj, AnyCols):
            return self
        return self._carry(
            type(self)((*obj.path, *self._path), self._table, self._field, self._owner, self._lang, self._key), obj
        )


class Localised[R: RowKind]:
    """A localised column, keyed by language to get a `Label`.

    Subscript rather than a default: no API in this library picks a language
    for the caller, so `name` alone is not a column and cannot be projected.
    """

    __slots__ = ("_column", "_origin", "_path", "_table")

    def __init__(self, path: tuple[str, ...], table: str, column: str) -> None:
        self._path = path
        self._table = table
        self._column = column
        #: Handed on to the `Label` a subscript makes — see `AnyCol.origin`.
        self._origin: type[AnyCols] | None = None

    def __getitem__(self, lang: str) -> Label[R]:
        label = Label[R](self._path, self._table, self._column, lang)
        label._origin = self._origin
        return label

    # A localised column is not comparable until a language is chosen, and
    # forgetting the subscript is the easiest mistake here to make: `name` is
    # spelled like a column and reads like one. Left to Python it compared by
    # identity and yielded `False`, so the filter ran and matched nothing.
    def __eq__(self, other: object) -> bool:
        _refuse_comparison("a localised column", other, 'Subscript the language first: name["en"] == "Canada".')

    def __ne__(self, other: object) -> bool:
        _refuse_comparison("a localised column", other, 'Subscript the language first: name["en"] != "Canada".')

    def __bool__(self) -> bool:
        msg = _ambiguous_truth("a localised column")
        raise TypeError(msg)

    def __contains__(self, other: object) -> bool:
        # Without this, `in` falls back to iterating `__getitem__`, which
        # accepts any key and so never terminates on a real miss.
        _refuse_comparison(
            "a localised column", other, 'Subscript the language and match: name["en"].str.contains(...).'
        )

    def __iter__(self) -> object:
        _refuse_comparison("a localised column", None, 'Subscript a language: name["en"].')

    def __hash__(self) -> int:
        return id(self)

    def __get__(self, obj: object, owner: type | None = None) -> Self:
        if not isinstance(obj, AnyCols):
            return self
        # `obj._path`, not `obj.path`: an accessor declaring a column called
        # `path` shadows the property, and reading it here would re-enter this
        # method for ever. Guarded as well — `test_query_accessors.py` refuses
        # the shadowing outright — but the guard cannot run while the recursion
        # exists, because collection walks every accessor attribute and dies
        # first. So the footgun is removed rather than only forbidden.
        bound = type(self)((*obj._path, *self._path), self._table, self._column)
        bound._origin = obj.origin
        return bound


class SideLocalised[R: RowKind]:
    """A localised column held in `sdmx.localized_text`, keyed by language.

    `Localised`'s twin — same contract, different storage — so it carries the
    same refusals. It had none until the derived surface guard found it, which
    is exactly the failure mode that guard exists for: the hole was fixed on
    one of the pair and the other was not reached by any test.
    """

    __slots__ = ("_field", "_key", "_origin", "_owner", "_path", "_table")

    def __init__(self, path: tuple[str, ...], table: str, field: str, owner: str, key: str = "owner_urn") -> None:
        self._path = path
        self._table = table
        self._field = field
        self._owner = owner
        self._key = key
        #: Handed on to the `SideLabel` a subscript makes — see `AnyCol.origin`.
        self._origin: type[AnyCols] | None = None

    def __getitem__(self, lang: str) -> SideLabel[R]:
        label = SideLabel[R](self._path, self._table, self._field, self._owner, lang, self._key)
        label._origin = self._origin
        return label

    def __eq__(self, other: object) -> bool:
        _refuse_comparison("a localised column", other, 'Subscript the language first: name["en"] == "Canada".')

    def __ne__(self, other: object) -> bool:
        _refuse_comparison("a localised column", other, 'Subscript the language first: name["en"] != "Canada".')

    def __bool__(self) -> bool:
        msg = _ambiguous_truth("a localised column")
        raise TypeError(msg)

    def __contains__(self, other: object) -> bool:
        _refuse_comparison(
            "a localised column", other, 'Subscript the language and match: name["en"].str.contains(...).'
        )

    def __iter__(self) -> object:
        _refuse_comparison("a localised column", None, 'Subscript a language: name["en"].')

    def __hash__(self) -> int:
        return id(self)

    def __get__(self, obj: object, owner: type | None = None) -> Self:
        if not isinstance(obj, AnyCols):
            return self
        bound = type(self)((*obj.path, *self._path), self._table, self._field, self._owner, self._key)
        bound._origin = obj.origin
        return bound


class Vocabulary[R: RowKind](Str[R]):
    """A text column whose legal values are a closed, known set.

    `q.artefact.type` and `q.component.role` hold one of a short fixed list
    the library itself writes. Compared against anything else the answer is
    always an empty result — and *silently*, which is the worst shape a
    mistake can take in a query language:

        q.artefact.type == "Codlist"        # typo   -> []
        q.component.role == "dimension"     # case   -> []

    Both look like they work. Neither is distinguishable from "this catalogue
    happens to contain none", which is a perfectly ordinary answer.

    A column class rather than a check at the call site, for the reason
    `Version` is one: the divergence belongs to the column, so every operator
    that compares one gets it without being told, and a new operator cannot
    forget.

    The values come from `storage._kinds`, which is what the *writer* uses, so
    the language cannot drift from what is actually in the column. They arrive
    as a **callable** rather than a set: `accessors` is imported from inside
    `storage`, so naming that module at class-definition time is a cycle. The
    call happens when a predicate is built, by which point everything is
    loaded.
    """

    __slots__ = ("_allowed", "_spelling")

    def __init__(
        self,
        path: tuple[str, ...],
        table: str,
        column: str,
        allowed: Callable[[], frozenset[str]],
        spelling: str,
    ) -> None:
        super().__init__(path, table, column)
        self._allowed = allowed
        self._spelling = spelling

    @property
    def allowed(self) -> frozenset[str]:
        """The legal values, for a caller who wants to enumerate them."""
        return self._allowed()

    def _check_member(self, value: object, *, negated: bool = False) -> None:
        """Refuse a value the column can never hold.

        Only `str` is checked: a non-string is already refused by
        `Str._check_literal` with a better message about the *type*, and this
        would otherwise shadow it.

        `negated` only changes what the message says the consequence is. Both
        are mistakes worth refusing — `== "Codlist"` matches nothing and
        `!= "Codlist"` matches everything — but telling someone their filter is
        empty when it is actually a no-op sends them looking in the wrong place.
        """
        allowed = self.allowed
        if not isinstance(value, str) or value in allowed:
            return
        near = sorted(v for v in allowed if v.lower() == value.lower())
        hint = f" Did you mean {near[0]!r}?" if near else ""
        consequence = "matches every row" if negated else "matches nothing"
        options = ", ".join(sorted(allowed))
        msg = (
            f"{self._spelling} never holds {value!r}, so this {consequence} — which is "
            f"almost certainly not the question.{hint} Legal values: {options}."
        )
        raise ValueError(msg)

    @override
    def __eq__(self, other: object) -> Pred[R]:
        self._check_member(other)
        return super().__eq__(other)

    @override
    def __ne__(self, other: object) -> Pred[R]:
        self._check_member(other, negated=True)
        return super().__ne__(other)

    @override
    def __hash__(self) -> int:
        return id(self)

    @override
    def is_in(self, values: Iterable[object] | SqlRelation) -> Pred[R]:
        """As `Str.is_in`, refusing any member the column cannot hold.

        Every member, not just the first: a set is where a typo hides best,
        because the other members still match and the result looks plausible.
        """
        # Three calls rather than one union: pyrefly wants type arguments on a
        # subscripted generic here and basedpyright refuses one in `isinstance`.
        # A bare `Mapping` satisfies both, as it already does above.
        if isinstance(values, Iterable) and not isinstance(values, str) and not isinstance(values, Mapping):
            members = tuple(values)
            for member in members:
                self._check_member(member)
            return super().is_in(members)
        return super().is_in(values)

    @override
    def __get__(self, obj: object, owner: type | None = None) -> Self:
        """Carry the vocabulary through, as `AnyCol.__get__` carries the path.

        Reimplemented rather than delegated: the base rebuilds with `type(self)(path, table, column)`, and this
        class takes a fourth argument. Without this override, reading
        `q.artefact.type` off an accessor raises a `TypeError` from the
        rebuild — which is how every column class carrying extra state has to
        do it.
        """
        if not isinstance(obj, AnyCols):
            return self
        path, table, column = trim(obj.root, (*obj._path, *self._path), self._table, self._column)
        return self._carry(type(self)(path, table, column, self._allowed, self._spelling), obj)

    @override
    def __canonical__(self) -> tuple[object, ...]:
        """Identity is the base's.

        The allowed set is a property of the column, not of this reference to
        it, so two reads of `q.artefact.type` key the same.
        """
        return super().__canonical__()


class Version[R: RowKind](Str[R]):
    """The `version` column, ordered by its baked sort key.

    A `Str` in every other respect — `==`, `is_in` and the `.str` namespace
    all read `version`, which is what a registry published and what a caller
    types. Only ordering diverges, and it has to: `1.9` sorts after `1.10` as
    text, so `order_by(version.desc())` and `rank()` pick the wrong artefact
    for "the latest version" (#311).

    A column class rather than a flag on `AnyCol`, so the divergence is
    visible where the accessor declares it and nothing else acquires it by
    accident.
    """

    __slots__ = ()

    @property
    @override
    def sort_column(self) -> str:
        """`version_sort`, baked at write time by `storage.writers`."""
        return "version_sort"

    def _ordered(self, op: str, other: object) -> Pred[R]:
        """`version <op> value`, compared on the baked key rather than as text.

        Ordering comparisons were left out entirely, deliberately: `>=` on the
        raw column makes `1.9 >= 1.10` true, and a wrong answer is worse than
        a missing feature. But the consequence was that "every artefact at 2.0
        or later" had no spelling at all in a domain where versions are
        first-class, and the refusal arrived as a bare Python message —
        `'>=' not supported between instances of 'Version' and 'str'` — which
        is the one place this language speaks in someone else's voice.

        Nothing new is needed. `version_sort` is already baked beside the
        column and already what `order_by` and `rank()` compare, and the same
        `sort_key` that produced it maps the literal. So the comparison is
        exact, not approximate, and agrees with the ordering by construction
        rather than by promise.
        """
        if not isinstance(other, str):
            got = type(other).__name__
            msg = (
                f"version comparisons take a version string, got {got}. "
                'Write it as SDMX publishes it: q.dsd.version >= "2.0".'
            )
            raise TypeError(msg)
        key: Str[R] = Str(tuple(self.path), self.table, self.sort_column)
        return Compare(key, op, version_sort_key(other))

    def __lt__(self, other: object) -> Pred[R]:
        return self._ordered("<", other)

    def __le__(self, other: object) -> Pred[R]:
        return self._ordered("<=", other)

    def __gt__(self, other: object) -> Pred[R]:
        return self._ordered(">", other)

    def __ge__(self, other: object) -> Pred[R]:
        return self._ordered(">=", other)


class ColumnKind(NamedTuple):
    """What an observation column holds, and how that is known (#511).

    Attributes:
        holds: ``"str"`` for text — a code, a time period, a declared string
            type — or ``"num"`` for a number.
        because: Why, as the clause a refusal reads out: ``"is enumerated by
            CL_AREA"``, ``"is declared Double"``, ``"has no type in its DSD and
            is stored as VARCHAR"``.
    """

    holds: Literal["str", "num"]
    because: builtins.str


def _kind_refusal(component_id: builtins.str, kind: ColumnKind, value: object) -> TypeError | None:
    """The refusal for comparing *value* with a column holding *kind*, or `None` if they agree.

    A column operand, an aggregate and ``None`` are left to the comparison's
    own rules: this is about a literal of the wrong kind, which DuckDB either
    refuses with a binder error naming neither the component nor the fix, or —
    for ``=`` against text — answers with no rows.
    """
    if value is None or isinstance(value, AnyCol | AnyAgg):
        return None
    if kind.holds == "str":
        if isinstance(value, builtins.str):
            return None
        hint = (
            " — or, if its values are numbers, declare its Facet (e.g. Double) in the DSD"
            if "stored as" in kind.because
            else ""
        )
        return TypeError(
            f"{component_id} {kind.because}, so its values are text; it is compared with "
            f"{type(value).__name__} {value!r}, which would compare text with a number. Compare with "
            f'"{value}"{hint}.'
        )
    if isinstance(value, int | float) and not isinstance(value, bool):
        return None
    return TypeError(
        f"{component_id} {kind.because}, so its values are numbers; it is compared with "
        f"{type(value).__name__} {value!r}. Compare with a number."
    )


class Untyped[R: RowKind](Col[R]):
    """A column whose type nothing has stated, and its SDMX name.

    Every other column class knows what it holds and enforces it:
    `Str.__eq__` refuses an int literal, `Num.__eq__` refuses a str, and that
    check (`_check_literal`) is why they are separate classes rather than one
    with a flag. A dataflow's observation column has no such declaration —
    the physical column is whatever its producer wrote — so this one holds
    *anything* and checks nothing.

    It offers exactly the operations that mean one thing without knowing the
    type, and no more:

    * comparison and ordering, which is what a time range needs;
    * `is_in`, without `Str`'s member-type check;
    * `-`, `*`, `/`, which are numeric whatever the operands were, and which
      is what a share needs;
    * `.str`, where the *caller* says to read it as text.

    **`+` is deliberately absent.** It means addition on `Num` and
    concatenation on `Str`, and with no declared type there is nothing to pick
    by: inheriting `Str`'s would make `obs.col("A") + obs.col("B")` compile to
    `||`, silently concatenating two measures into `"12"`. Absent, it is a
    plain `TypeError` at the call.

    **Why not simply put ordering on `Col`.** `Version` extends `Str` and
    overrides only `sort_column`, so an inherited text `>=` would make
    `version >= "1.10"` **true** for `1.9`: text ordering of versions, as a
    predicate rather than as an `ORDER BY`. Today that mistake is unspellable,
    and widening the base
    would make it spellable everywhere in order to fix it in one place. A
    column class that opts in leaves the structural surface exactly as tight as
    it was.

    The remaining cost is bounded and honest: `obs.col("REF_AREA") * 2`
    type-checks and fails at DuckDB, because nothing here can know better.
    §6.3 of `docs/design/data-plane-query.md` is how it learns — a component's
    DSD representation says whether it is enumerated or carries a `DataType`,
    so a typed accessor can return `Str` or `Num` exactly, and this class stays
    for the dynamic case.
    """

    __slots__ = ("_component_id", "_kind")

    def __init__(
        self, path: tuple[str, ...], table: str, column: str, component_id: str = "", kind: ColumnKind | None = None
    ) -> None:
        """`component_id` is the SDMX id this column was asked for by.

        `kind`, when known, is what the column holds: a literal of the
        other kind is refused as the predicate is built. Unknown — no DSD
        type and no stored type to read — checks nothing, as before.
        """
        super().__init__(path, table, column)
        self._component_id = component_id or column
        self._kind = kind

    @property
    def kind(self) -> ColumnKind | None:
        """What this column holds, when the DSD or the store says."""
        return self._kind

    def _checked(self, value: object) -> object:
        """*value*, if its kind agrees with this column's — see `_kind_refusal`."""
        if self._kind is not None:
            refusal = _kind_refusal(self._component_id, self._kind, value)
            if refusal is not None:
                raise refusal
        return value

    @override
    def __eq__(self, other: object) -> Pred[R]:
        return super().__eq__(self._checked(other))

    @override
    def __ne__(self, other: object) -> Pred[R]:
        return super().__ne__(self._checked(other))

    # `__eq__` is overridden, so the identity hash `Col` declares is restated.
    @override
    def __hash__(self) -> int:
        return id(self)

    @override
    def is_in(self, values: Iterable[object] | SqlRelation) -> Pred[R]:
        """As `Col.is_in`, plus the kind check on the first member — as `Str.is_in` checks its own."""
        if _is_relation(values) or _is_opaque(values) or not _is_iterable(values):
            return super().is_in(values)
        listed = list(values)
        if listed:
            self._checked(listed[0])
        return super().is_in(listed)

    @property
    @override
    def projected_name(self) -> str:
        """The SDMX component id. See `AnyCol.projected_name`."""
        return self._component_id

    def __lt__(self, other: object) -> Pred[R]:
        return Compare(self, "<", self._checked(other))

    def __le__(self, other: object) -> Pred[R]:
        return Compare(self, "<=", self._checked(other))

    def __gt__(self, other: object) -> Pred[R]:
        return Compare(self, ">", self._checked(other))

    def __ge__(self, other: object) -> Pred[R]:
        return Compare(self, ">=", self._checked(other))

    def __sub__(self, other: object) -> Num[R]:
        return NumExpr("-", (self, other))

    def __rsub__(self, other: object) -> Num[R]:
        return NumExpr("-", (other, self))

    def __mul__(self, other: object) -> Num[R]:
        return NumExpr("*", (self, other))

    def __rmul__(self, other: object) -> Num[R]:
        return NumExpr("*", (other, self))

    def __truediv__(self, other: object) -> Num[R]:
        return NumExpr("/", (self, other))

    def __rtruediv__(self, other: object) -> Num[R]:
        return NumExpr("/", (other, self))

    def __neg__(self) -> Num[R]:
        return NumExpr("-", (0, self))

    @property
    def str(self) -> StrNS[R]:
        """Text operations, where the caller states the reading.

        `contains` on an untyped column is a claim the *caller* makes — which
        is the difference between this and an inherited `+`: one is asked for
        by name, the other would be picked silently.
        """
        return StrNS(self)


class Bool[R: RowKind](Col[R]):
    """A boolean column.

    Its own type rather than a `Str` carrying `"true"` / `"false"`, because
    the storage column really is `BOOLEAN` and `is_final == "true"` would
    compile to a comparison DuckDB rejects. The literal check is the strict
    one: `True`, not `1` — `int` is a `bool`'s supertype, so admitting it
    would let `is_final == 1` through and that is never what was meant.
    """

    __slots__ = ()

    @override
    def __eq__(self, other: object) -> Pred[R]:
        _check_literal(self, "bool", other)
        return super().__eq__(other)

    @override
    def __ne__(self, other: object) -> Pred[R]:
        _check_literal(self, "bool", other)
        return super().__ne__(other)

    @override
    def __hash__(self) -> int:
        return id(self)


class Arr[R: RowKind](Col[R]):
    """A stored list column — `ancestor_ids` on every tree relation.

    The reason ancestry is a *path* here and not a recursive query. The writer
    bakes each row's ancestors at write time (see `docs/design/caching.md`, the
    "input immutable within an artefact version" case), so "everything under X"
    is one containment test against a column rather than a recursive CTE.

    That is also why the design's corollary — recursion stays a named domain
    verb, the compiler never emits a recursive CTE — costs so little: the two
    transitive SKOS verbs were never recursive at query time either.
    """

    __slots__ = ()

    def has(self, value: object) -> Pred[R]:
        """Whether the stored list contains `value`.

        `q.component.concept_roles.has("FREQ")` is every component playing
        that role. For a code tree, ancestry is the `ancestors` edge —
        `q.code.ancestors.id == "W"` — not a list column.
        """
        return ArrayHas(self, value)


class Num[R: RowKind](Col[R]):
    """A numeric column, with the comparisons and arithmetic text does not carry.

    The operators are typed on `Num[R] | int | float` rather than on `object`,
    which is the reason this class exists at all: `q.code.id + 1` is a question
    with no answer, and a `Str` that carried `+` as addition would accept it.
    """

    __slots__ = ()

    @override
    def __eq__(self, other: object) -> Pred[R]:
        _check_literal(self, "int", other)
        return super().__eq__(other)

    @override
    def __ne__(self, other: object) -> Pred[R]:
        _check_literal(self, "int", other)
        return super().__ne__(other)

    # `__eq__` is overridden to build a predicate, so the identity hash `Col`
    # declares has to be restated — Python drops an inherited `__hash__` on any
    # class that defines `__eq__`.
    @override
    def __hash__(self) -> int:
        return id(self)

    def __add__(self, other: Num[R] | Win[R] | float) -> Num[R]:
        return NumExpr("+", (self, other))

    def __radd__(self, other: float) -> Num[R]:
        return NumExpr("+", (other, self))

    def __sub__(self, other: Num[R] | Win[R] | float) -> Num[R]:
        return NumExpr("-", (self, other))

    def __rsub__(self, other: float) -> Num[R]:
        return NumExpr("-", (other, self))

    def __mul__(self, other: Num[R] | Win[R] | float) -> Num[R]:
        return NumExpr("*", (self, other))

    def __rmul__(self, other: float) -> Num[R]:
        return NumExpr("*", (other, self))

    def __truediv__(self, other: Num[R] | Win[R] | float) -> Num[R]:
        """Division — floating point, following the database.

        `x / 0` is `inf` in DuckDB and `0 / 0` is `nan`; neither raises and
        neither is `NULL`. That is IEEE-754 rather than SQL's usual
        missing-value story, and it is left alone: wrapping it would hide the
        database's own answer behind a rule this DSL invented.
        """
        return NumExpr("/", (self, other))

    def __rtruediv__(self, other: float) -> Num[R]:
        return NumExpr("/", (other, self))

    def __neg__(self) -> Num[R]:
        return NumExpr("-", (0, self))

    def __lt__(self, other: object) -> Pred[R]:
        return Compare(self, "<", other)

    def __le__(self, other: object) -> Pred[R]:
        return Compare(self, "<=", other)

    def __gt__(self, other: object) -> Pred[R]:
        return Compare(self, ">", other)

    def __ge__(self, other: object) -> Pred[R]:
        return Compare(self, ">=", other)


class ComponentStr[R: RowKind](Str[R]):
    """A text column named by an SDMX component — `ObsQuery.text`'s (#511).

    `Str` exactly, but projected under the component id rather than the
    physical column, as `Untyped` is: a frame's columns are component ids.
    """

    __slots__ = ("_component_id",)

    def __init__(self, path: tuple[str, ...], table: str, column: str, component_id: str) -> None:
        """`component_id` is the SDMX id this column was asked for by."""
        super().__init__(path, table, column)
        self._component_id = component_id

    @property
    @override
    def projected_name(self) -> str:
        """The SDMX component id. See `AnyCol.projected_name`."""
        return self._component_id


class ComponentNum[R: RowKind](Num[R]):
    """A numeric column named by an SDMX component — `ObsQuery.num`'s (#511). See `ComponentStr`."""

    __slots__ = ("_component_id",)

    def __init__(self, path: tuple[str, ...], table: str, column: str, component_id: str) -> None:
        """`component_id` is the SDMX id this column was asked for by."""
        super().__init__(path, table, column)
        self._component_id = component_id

    @property
    @override
    def projected_name(self) -> str:
        """The SDMX component id. See `AnyCol.projected_name`."""
        return self._component_id


# ── windows ───────────────────────────────────────────────────────────────
#
# Two stages, because they answer different questions and a windowed result is
# *not* filterable where an ordinary one is.
#
#   q.dsd.version.rank(descending=True)              -> WinFn[R]   what
#   ...              .over(q.dsd.agency, q.dsd.id)   -> Win[R]     over which rows
#   ...                                       == 1   -> WinPred[R] the test
#
# `WinPred[R]` is deliberately *not* a `Pred[R]`, so `filter()` rejects it and
# `qualify()` rejects an ordinary predicate. That is the third composition law
# made structural: a rank is computed over whatever set precedes it, so a
# windowed test does not commute with a plain one, and the DSL puts the
# order-dependence in the vocabulary rather than leaving it to call order.


@attrs.frozen
class WinFn[R: RowKind]:
    """A window function with no framing yet. `.over()` supplies that.

    Unusable on its own — there is no `Col` here to project or compare — which
    is the point: a window function without a partition is a question nobody
    means to ask, and splitting the two stages makes that unspellable rather
    than merely discouraged.
    """

    column: AnyCol
    function: str
    descending: bool = False
    offset: int = 1

    def over(self, *partition: Col[R]) -> Win[R]:
        """Select this function over rows sharing the given columns.

        No arguments means one partition covering every row — `rank()` over the
        whole result rather than within a group. That is SQL's own meaning for
        an empty `PARTITION BY`, so it is spelled by omission rather than
        refused.
        """
        return Win((self.column,), self.function, tuple(partition), descending=self.descending, offset=self.offset)


class Temporal[R: RowKind](Num[R]):
    """A `TIMESTAMP` column, read back as UTC.

    Derived from `Num` for the ordering operators: an instant compares and
    sorts exactly as a number does, and `valid_from < cutoff` is the query
    this column exists for. What `Num` cannot do is tell a projected frame
    that the value is an instant, which is the rest of this class.

    The store's convention is **UTC-naive** — DuckDB's `TIMESTAMP` carries no
    zone, and handing it a tz-aware value converts to the *session's* local
    time, so the same instant read on two machines is two instants (#321,
    `sdmxlib/_time.py`). The round-trip that module documents is therefore
    `naive in -> aware out`: the wall time is unchanged, it is simply labelled.

    `readers` already honour that, so `reg.get(urn).valid_from` is tz-aware.
    A projected frame did not, so the same field read two ways gave a naive
    datetime and an aware one, which do not compare and cannot be subtracted.
    Labelling happens in polars rather than in SQL because `AT TIME ZONE 'UTC'`
    yields a `TIMESTAMPTZ` that renders in the session's zone — reintroducing
    exactly the machine-dependence being removed.

    A column class rather than `Num`, which is what these were declared as at
    first: `Num` compares and orders correctly but carries no way to tell the
    frame builder that a value is an instant, and that is the whole difference.

    **Literals go in the way the writers put values in** (#415). Every one is
    normalised with `sdmxlib._time.to_storage` — an aware `datetime` converted
    to UTC and its zone dropped, a naive one taken as UTC already, a `date` as
    midnight UTC — before it reaches DuckDB. Without that, DuckDB converted an
    aware literal to the *session's* zone first: `is_in([aware])` matched
    nothing and `< aware` compared against an instant shifted by the local
    offset, so one query gave different rows on different machines. It went
    unseen because on a UTC session the shift is zero.

    And the literal check is its own. `Num`'s demands an `int`, so `==`
    refused a `datetime` while `<` — which `Num` does not check — took one, and
    took an `int` just as readily.
    """

    __slots__ = ()

    def _literal(self, value: object) -> datetime:
        """*value* as the store holds it, or a `TypeError` naming the column.

        Raises:
            TypeError: On anything but a `datetime` or `date` — `None`
                included, since `= NULL` and `< NULL` are never true.
        """
        if value is None:
            msg = (
                f"comparing {self.column} to None matches nothing — a comparison with NULL "
                "is never true in SQL. Use is_null(), or is_not_null() for the complement."
            )
            raise TypeError(msg)
        if isinstance(value, datetime):
            return to_storage(value)
        # After `datetime`, which is a `date` too.
        if isinstance(value, date):
            return to_storage(datetime(value.year, value.month, value.day, tzinfo=UTC))
        raise _literal_type_error(self, "datetime", value)

    @override
    def __eq__(self, other: object) -> Pred[R]:
        # To `Col` directly, past `Num`, whose integer check is the defect.
        return Col.__eq__(self, self._literal(other))

    @override
    def __ne__(self, other: object) -> Pred[R]:
        return Col.__ne__(self, self._literal(other))

    @override
    def __lt__(self, other: object) -> Pred[R]:
        return Compare(self, "<", self._literal(other))

    @override
    def __le__(self, other: object) -> Pred[R]:
        return Compare(self, "<=", self._literal(other))

    @override
    def __gt__(self, other: object) -> Pred[R]:
        return Compare(self, ">", self._literal(other))

    @override
    def __ge__(self, other: object) -> Pred[R]:
        return Compare(self, ">=", self._literal(other))

    @override
    def __hash__(self) -> int:
        return id(self)

    @override
    def is_in(self, values: Iterable[object] | SqlRelation) -> Pred[R]:
        """As `Col.is_in`, with every member checked and normalised.

        Every member, not just the first as `Str.is_in` does: a mixed set is
        unlikely, but an aware member left un-normalised is a silent miss, so
        each one is converted anyway and the check comes free.
        """
        if _is_relation(values) or _is_opaque(values) or not _is_iterable(values):
            return super().is_in(values)
        # `None` stays a member, as `Str.is_in` lets it: NULL in an `IN` set
        # is inert rather than a mistake worth refusing.
        return super().is_in([None if member is None else self._literal(member) for member in values])


class AnyWinPred:
    """Non-generic base for windowed predicates, as `AnyPred` is for plain ones.

    `WinPred[R]` is invariant, so `WinPred[DsdRow]` does not satisfy a
    `WinPred[RowKind]` parameter. `qualify` has already checked the row kind by
    the time the compiler sees one.
    """

    __slots__ = ()


class WinPred[R: RowKind](AnyWinPred):
    """A test on a windowed value. **Not** a `Pred[R]`, and that is load-bearing.

    A `WHERE` runs before the window is computed, so a rank compared there
    would be a rank over a set that no longer exists by the time it is
    filtered. Keeping the two predicate types apart means `filter(rank == 1)`
    is a type error naming `qualify` rather than a wrong answer.
    """

    __slots__ = ()


@attrs.frozen(eq=False)
class WinCompare[R: RowKind](WinPred[R]):
    """A windowed value against a literal."""

    column: Win[R]
    op: str
    value: object


class Win[R: RowKind](Col[R]):
    """A framed window expression: a function, its column, and its partition.

    A `Col[R]`, so it projects like any other column —
    `select(n=q.dsd.id.count().over(q.dsd.agency))` is fine. Only the *filter*
    position is restricted, and by the return type of its comparisons rather
    than by a runtime check.
    """

    __slots__ = ("_descending", "_function", "_offset", "_partition", "_source")

    def __init__(
        self,
        source: tuple[AnyCol, ...],
        function: str,
        partition: tuple[AnyCol, ...],
        *,
        descending: bool = False,
        offset: int = 1,
    ) -> None:
        first = source[0] if source else None
        super().__init__(
            first.path if first is not None else (),
            first.table if first is not None else "",
            first.column if first is not None else "",
        )
        self._source = source
        self._function = function
        self._partition = partition
        self._descending = descending
        self._offset = offset

    @property
    def source(self) -> tuple[AnyCol, ...]:
        """Columns the function is applied to."""
        return self._source

    @property
    def function(self) -> str:
        """SQL window function name, or the aggregate kind it came from."""
        return self._function

    @property
    def partition(self) -> tuple[AnyCol, ...]:
        """Columns whose shared values define one window."""
        return self._partition

    @property
    def descending(self) -> bool:
        """Direction of the window's own `ORDER BY`."""
        return self._descending

    @property
    def offset(self) -> int:
        """Row displacement, for `lag` and `lead`."""
        return self._offset

    # Comparisons build a `WinPred`, so the identity contract is off here for
    # the same reason it is on `Col`. Approved suppression: the override is
    # genuinely incompatible — `Col.__eq__` returns `Pred[R]` — and that
    # incompatibility *is* the feature, since it is what stops a windowed test
    # reaching `filter()`.
    @override
    def __hash__(self) -> int:
        return id(self)

    @override
    def __eq__(self, other: object) -> WinPred[R]:  # pyrefly: ignore[bad-override]
        return WinCompare(self, "=", other)

    @override
    def __ne__(self, other: object) -> WinPred[R]:  # pyrefly: ignore[bad-override]
        return WinCompare(self, "!=", other)

    def le(self, value: object) -> WinPred[R]:
        """`<= value` — "the three newest" rather than "the newest"."""
        return WinCompare(self, "<=", value)

    def lt(self, value: object) -> WinPred[R]:
        """`< value`."""
        return WinCompare(self, "<", value)

    def ge(self, value: object) -> WinPred[R]:
        """`>= value`."""
        return WinCompare(self, ">=", value)

    def gt(self, value: object) -> WinPred[R]:
        """`> value`."""
        return WinCompare(self, ">", value)

    @override
    def __canonical__(self) -> tuple[object, ...]:
        """The frame as well as the column.

        Two ranks over the same column differing only in partition, direction
        or offset are different questions; the base key sees none of that.
        """
        return (
            *super().__canonical__(),
            self._function,
            tuple(c.__canonical__() for c in self._source),
            tuple(c.__canonical__() for c in self._partition),
            self._descending,
            self._offset,
        )

    @override
    def __repr__(self) -> str:
        by = ", ".join(c.column for c in self._partition) or "<all rows>"
        return f"<Win {self._function} over {by}>"


class Computed:
    """Mixin marking a column derived from others, and holding the derivation.

    `op` is either an infix operator (`+`, `||`) or a function name
    (`length`); `operands` mixes `AnyCol` with plain values. One node shape
    keeps the compiler's job to "render this tree", which is what lets a
    computed column be projected, sorted, compared and windowed without any of
    those learning about arithmetic.

    **A plain mixin, not an `AnyCol` subclass**, and listed first so its
    `__get__` wins. Deriving it from `AnyCol` too would give `NumExpr` two
    bases that both carry `AnyCol.__init__`, which basedpyright rejects as an
    unsafe diamond — correctly, since nothing guarantees both run. Here only
    `Num` / `Str` contribute a constructor and the concrete class calls it.

    The slots live on the concrete classes rather than here for a related
    reason: two bases each declaring non-empty `__slots__` is an instance
    layout conflict at class-creation time. Declared as properties the
    concrete classes' slots override, so the properties below type-check
    against them; as bare annotations they were attributes nothing here sets.
    """

    __slots__ = ()

    @property
    def _op(self) -> builtins.str:
        raise NotImplementedError

    @_op.setter
    def _op(self, value: builtins.str) -> None:
        raise NotImplementedError

    @property
    def _operands(self) -> tuple[object, ...]:
        raise NotImplementedError

    @_operands.setter
    def _operands(self, value: tuple[object, ...]) -> None:
        raise NotImplementedError

    @property
    def op(self) -> builtins.str:
        """Infix operator or function name."""
        return self._op

    @property
    def operands(self) -> tuple[object, ...]:
        """Operands in order — `AnyCol` for columns, anything else a literal."""
        return self._operands

    def __get__(self, obj: object, owner: type | None = None) -> Self:
        """Never a class attribute, so binding a path to it would be wrong.

        `AnyCol.__get__` rebuilds the declaration with the reading accessor's
        path, which assumes the three-argument constructor these do not have. A
        computed column is built at a call site and already carries its
        operands' paths.
        """
        return self

    def __canonical__(self) -> tuple[object, ...]:
        """The operator and its operands, which the base key does not reach.

        `position + 1` and `position - 1` share a path, table and column — the
        first column operand's — so without this they key the same. Literal
        operands are tagged by type so an `int` and a `str` cannot collide.
        """
        operands = tuple(
            o.__canonical__() if isinstance(o, AnyCol) else ("lit", type(o).__name__, o) for o in self._operands
        )
        return ("computed", type(self).__name__, self._op, operands)

    def __repr__(self) -> builtins.str:
        return f"<{type(self).__name__} {self._op}{self._operands}>"


def _is_iterable(value: object) -> TypeIs[Iterable[object]]:
    """Whether `value` can be listed, without listing it."""
    return isinstance(value, Iterable)


def _is_opaque(value: object) -> bool:
    """Whether `value` is iterable but must not be treated as a list of members.

    A `str` iterates characters and a `Mapping` iterates keys — both silently,
    which is why `Col.is_in` names each one. Written as a helper rather than
    inline because `isinstance` cannot take a parameterised `Mapping[K, V]`,
    pyrefly refuses the bare one as an implicit `Any` type argument, and
    `_as_members` in `_common` already exists for the same reason.
    """
    if isinstance(value, builtins.str | builtins.bytes):
        return True
    return isinstance(value, Mapping)


def _source_of(relation: SqlRelation) -> tuple[object, builtins.str | None]:
    """The database token `relation` reports and how to name it, or `(None, None)`.

    Structural rather than `isinstance(relation, HasSource)`: the protocol is
    runtime-checkable, but a producer that raises from `source` — a closed
    registry, say — should be treated as "cannot say" rather than fail the
    caller's `is_in` with someone else's error.
    """
    try:
        source = getattr(relation, "source", None)
        label = getattr(relation, "source_label", None)
    except Exception:  # noqa: BLE001  # a producer's failure is not this call's
        return None, None
    return source, label if isinstance(label, builtins.str) else None


def _is_relation(value: object) -> TypeIs[SqlRelation]:
    """Whether `value` is a `SqlRelation` rather than a collection.

    A structural check by hand rather than `isinstance` against the Protocol:
    `SqlRelation` is not `@runtime_checkable`, and making it so would match any
    object happening to carry those two names. `TypeIs` is what narrows the
    parameter for the checker, since a `hasattr` probe does not.
    """
    return callable(getattr(value, "to_sql", None)) and not isinstance(getattr(type(value), "columns", None), str)


def _origin(operands: tuple[object, ...]) -> tuple[tuple[builtins.str, ...], builtins.str, builtins.str]:
    """Where a computed column reports itself as coming from.

    Its first column operand's `path` / `table` / `column`, so anything reading
    a single path still gets a meaningful one. The compiler walks `operands`
    when it needs all of them, because two may sit on different relations and
    need different aliases.
    """
    first = next((o for o in operands if isinstance(o, AnyCol)), None)
    if first is None:
        return (), "", ""
    return first.path, first.table, first.column


class NumExpr[R: RowKind](Computed, Num[R]):
    """A computed numeric column. A `Num`, so arithmetic composes."""

    __slots__ = ("_op", "_operands")

    def __init__(self, op: builtins.str, operands: tuple[object, ...]) -> None:
        super().__init__(*_origin(operands))
        self._op = op
        self._operands = operands


class StrExpr[R: RowKind](Computed, Str[R]):
    """A computed text column. A `Str`, so `.str` and `+` compose."""

    __slots__ = ("_op", "_operands")

    def __init__(self, op: builtins.str, operands: tuple[object, ...]) -> None:
        super().__init__(*_origin(operands))
        self._op = op
        self._operands = operands


# ── the `.str` namespace, and the folding ladder ──────────────────────────


class _StrOps[R: RowKind]:
    """String matching at one folding strength."""

    __slots__ = ("_column", "_fold")

    def __init__(self, column: AnyCol, fold: Fold) -> None:
        self._column = column
        self._fold = fold

    def contains(self, value: str) -> Pred[R]:
        return Match(self._column, "contains", self._fold, value)

    def starts_with(self, value: str) -> Pred[R]:
        return Match(self._column, "starts_with", self._fold, value)

    def ends_with(self, value: str) -> Pred[R]:
        return Match(self._column, "ends_with", self._fold, value)


class _CaselessOps[R: RowKind](_StrOps[R]):
    """`Fold.CASE`, which uniquely also carries equality.

    An accent-insensitive *equality* is a question nobody has asked, whereas a
    case-insensitive id lookup is the complaint #222 opens with — so `==` stops
    here and `unaccented` / `folded` carry the string ops alone.
    """

    __slots__ = ()

    def __eq__(self, other: object) -> Pred[R]:  # pyrefly: ignore[bad-override]
        return Compare(self._column, "=", other, fold=Fold.CASE)

    def __ne__(self, other: object) -> Pred[R]:  # pyrefly: ignore[bad-override]
        return Compare(self._column, "!=", other, fold=Fold.CASE)

    @override
    def __hash__(self) -> int:
        return id(self)


class StrNS[R: RowKind](_StrOps[R]):
    """`.str` on any string column — exact matching, plus the case rung.

    `unaccented` and `folded` are **not** here. They live on `LabelNS`, because
    an id is not prose: folding diacritics and punctuation out of `CL_FREQ` is
    not a question anyone means to ask (#255).
    """

    __slots__ = ()

    def __init__(self, column: AnyCol) -> None:
        super().__init__(column, Fold.NONE)

    @property
    def caseless(self) -> _CaselessOps[R]:
        """Ignore case — `id.str.caseless == "cl_freq"` finds `CL_FREQ`."""
        return _CaselessOps(self._column, Fold.CASE)


class LabelNS[R: RowKind](StrNS[R]):
    """`.str` on a localised label, which adds the two prose rungs."""

    __slots__ = ()

    @property
    def unaccented(self) -> _StrOps[R]:
        """Ignore case and diacritics — "Montreal" finds "Montréal"."""
        return _StrOps(self._column, Fold.ACCENTS)

    @property
    def folded(self) -> _StrOps[R]:
        """Ignore case, diacritics *and* punctuation.

        Materially more aggressive, which is why it is asked for by name:
        `"REF_AREA"` also matches `REF AREA`, and `"50%"` also matches
        `50 cents`.
        """
        return _StrOps(self._column, Fold.FULL)


# ── the accessor base, and the declaration factories ──────────────────────


class AnyCols:
    """Non-generic base for accessors, so a bound or parameter can name one.

    `Cols[R]` is invariant in `R` — the parameter is a phantom and appears
    nowhere in the body — so `Cols[ConceptRow]` does not satisfy a
    `Cols[RowKind]` bound. Factories that need to *construct* an accessor bound
    to this instead, which costs nothing and avoids reaching for `Any`.
    """

    __slots__ = ("_origin", "_path", "_root")

    #: Relation this accessor's row belongs to — where its columns live and
    #: where a root query selects from. For a maintainable carrying an
    #: extension table this is the *spine* (`artefact`), and an edge whose
    #: foreign key sits on the extension crosses the identity split as a
    #: compound hop rather than moving the root. See `accessors.py`.
    __table__: str = ""

    #: Joins the root always has, beyond anything the paths reach.
    #:
    #: Empty for every structural relation: their joins come from declared
    #: edges, resolved per path. A dataflow may keep series-level attributes
    #: in a sidecar table, which is a join no edge describes and every query
    #: needs (#332).
    __joins__: tuple[RootJoin, ...] = ()

    #: Physical columns the relation is partitioned on, outermost first.
    #:
    #: Empty for every structural relation — they are one table each. A
    #: dataflow's observations may be partitioned, and a stream ordered by a
    #: prefix of these can be served partition by partition: bounded memory
    #: *and* a guaranteed order, where a global sort gives only the second
    #: (#331).
    __partitions__: tuple[str, ...] = ()

    #: Key columns the natural order sorts by an item scheme's positions.
    #:
    #: Empty for every structural relation, whose key is a URN. A dataflow's
    #: dimensions hold codes, and its observations come in codelist order
    #: rather than code-id text order (GH-213): the root-key tiebreak sorts
    #: each listed column by its code's position first.
    __key_ranks__: tuple[KeyRank, ...] = ()

    #: Schema qualifying `__table__`, or `None` when it is qualified already.
    #:
    #: Every structural relation lives in `sdmx`. A dataflow's observations do
    #: not — a binding names them `<database_role>.<table_location>` — so this
    #: is what lets one compiler serve both (#297).
    __schema__: str | None = STRUCTURAL_SCHEMA

    #: Column carrying this relation's human-facing identifier, for `ids()`.
    __id__: str = "resource_id"

    #: Columns of `__table__` that uniquely identify a row, appended to every
    #: `ORDER BY` as a tiebreak.
    #:
    #: Without one, two rows equal on the caller's keys come back in whatever
    #: order the scan produced, and a page boundary moves between calls —
    #: `limit`/`offset` are only meaningful over a total order. Declared rather
    #: than derived so the guard in `tests/unit/test_query_order.py` can check
    #: it against the live primary keys; a tiebreak that is not actually unique
    #: fails silently, which is the failure mode of the whole clause.
    __key__: tuple[str, ...] = ("urn",)

    #: Constraint identifying this accessor's rows within `__table__`, as
    #: `(column, values)`.
    #:
    #: Most artefact types share one relation — `q.codelist` and `q.dsd` are
    #: both `sdmx.artefact` — so a root query needs the discriminator or it
    #: returns every artefact. This is the same mechanism as a `Facet`, applied
    #: at the root rather than to a child.
    __constraint__: tuple[str, tuple[str, ...]] | None = None

    #: What a backend calls this relation, when one is asked whether it can
    #: scan it — the SDMX REST resource. `None` for a structural accessor,
    #: whose `__constraint__` already carries its artefact type and *is* the
    #: resource. Set explicitly where that does not hold: an observation root
    #: is built per binding, so its table is one dataflow's and cannot be what
    #: a capability is keyed by.
    __resource__: str | None = None

    #: The REST resources a scan of this relation must fetch, for a root that
    #: is *derived* rather than stored. `None` for every stored relation,
    #: where one resource answers the scan.
    #:
    #: `hierarchy_applicability` is the case: a view over three relations, so
    #: there is no single endpoint to ask and the fetch has to expand. Named
    #: here rather than inferred from the view's SQL because a backend has to
    #: read it *before* any query runs, and parsing DDL to find out is a
    #: dependency the query language does not have.
    __derived_from__: tuple[str, ...] | None = None

    #: Explicit output projection for an unprojected query, as
    #: `(table, column, output name)` triples. Empty means "every column of the
    #: row" — `_Scope.star` — which is right for a structural relation whose
    #: column names *are* what a caller asked about.
    #:
    #: An observation relation is the other case: its columns are one
    #: producer's physical names, while everything else in the language speaks
    #: component ids. Without this, `frame()` hands back `geo` where every
    #: filter said `REF_AREA`.
    __projection__: tuple[tuple[str, str, str], ...] = ()

    def __init__(self, path: tuple[str, ...] = (), root: str = "", origin: type[AnyCols] | None = None) -> None:
        self._path = path
        #: The root accessor class a hop was reached from; `None` on a root,
        #: which is its own origin. See `origin`.
        self._origin = origin
        #: Relation `_path` is measured from — the *root* accessor's table, not
        #: this one's. Empty on an unbound declaration and on a root accessor,
        #: where `__table__` is the root by definition; filled in by `__get__`
        #: as a hop is bound, so a column at the end of a path can resolve it
        #: (see `_joins.trim`). Not part of `__canonical__`: it is derivable
        #: from the path and adding it would key two equal accessors apart.
        self._root = root

    @property
    def path(self) -> tuple[str, ...]:
        return self._path

    @property
    def root(self) -> str:
        """Relation this accessor's path is measured from."""
        return self._root or type(self).__table__

    @property
    def origin(self) -> type[AnyCols]:
        """The root accessor class this one was reached from — itself, at a root.

        `root` is a *table*, and every maintainable shares one, so it cannot
        tell `q.codelist` from `q.dataflow`; the class can (#510).
        """
        return self._origin or type(self)

    # An accessor is a namespace, not a value. `filter(q.code == "A")` — a
    # forgotten `.id` — compared by identity and yielded `False`, so the query
    # ran and matched nothing. `filter()` already refuses a bare accessor; this
    # is the same refusal one step earlier, before the bool exists.
    def __eq__(self, other: object) -> bool:
        _refuse_comparison("a relation", other, 'Compare one of its columns: q.code.id == "A".')

    def __ne__(self, other: object) -> bool:
        _refuse_comparison("a relation", other, 'Compare one of its columns: q.code.id != "A".')

    def __bool__(self) -> bool:
        msg = _ambiguous_truth("a relation")
        raise TypeError(msg)

    def __contains__(self, other: object) -> bool:
        _refuse_comparison("a relation", other, 'Compare one of its columns: q.code.id == "A".')

    def __hash__(self) -> int:
        return id(self)

    def __canonical__(self) -> tuple[object, ...]:
        """Structural identity of an accessor, for `canonical_key` (#270).

        The class name carries the root's discriminator: `q.dsd` and
        `q.codelist` are the same table and differ only by `__constraint__`, so
        keying on the table alone would make two queries over different
        artefact types compare equal.
        """
        return ("accessor", type(self).__name__, self._path)

    def __get__(self, obj: object, owner: type | None = None) -> Self:
        """Bind this edge declaration onto the reading accessor's path.

        Reads `_path` rather than the `path` property for the reason
        `AnyCol.__get__` does.

        The reading accessor's `root` comes along, because a hop accessor's own
        `__table__` is where it *lands*, and resolving a path needs where it
        *started*.
        """
        if not isinstance(obj, AnyCols):
            return self
        return type(self)((*obj._path, *self._path), obj.root, obj.origin)

    @override
    def __repr__(self) -> str:
        route = ".".join(self._path) or "<root>"
        return f"<{type(self).__name__} {route}>"


class Cols[R: RowKind, A = object](AnyCols):
    """A relation accessor, parameterised on the query root's row kind and artefact.

    Instances are cheap and immutable; walking an edge builds a new one rather
    than mutating, so an accessor is safe to share as a module singleton
    without one query's path leaking into another's.

    `R` is the row kind of the *root*, so a predicate built for one root cannot
    be handed to a query over another. `A` is what `collect()` builds from this
    relation's rows, and it is pinned by the accessor *class* rather than left
    free — `_Code` always collects `Code`, at whatever root the path started
    from. Roots that build nothing pin `Never`; that is 16 of the 37.

    `A` defaults to `object`, which is what `collect()` returned for every root
    before `A` existed. That default is what let the map be filled in per accessor
    rather than in one edit: an accessor that pins nothing behaves exactly as
    it did, and `Cols[R]` keeps meaning what it meant.
    """

    __slots__ = ()


def col[R: RowKind](table: str, column: str) -> Str[R]:
    """Declare a text column."""
    return Str((), table, column)


def vocab[R: RowKind](table: str, column: str, allowed: Callable[[], frozenset[str]], spelling: str) -> Vocabulary[R]:
    """Declare a text column with a closed vocabulary. See `Vocabulary`.

    `spelling` is how a caller writes the column — `q.artefact.type` — which
    is what a refusal should name. The storage column it reads is
    `artefact_type`, and telling someone about that sends them looking at a
    schema the language otherwise hides.
    """
    return Vocabulary((), table, column, allowed, spelling)


def tcol[R: RowKind](table: str, column: str) -> Temporal[R]:
    """Declare a `TIMESTAMP` column. See `Temporal` for the zone convention."""
    return Temporal((), table, column)


def vcol[R: RowKind](table: str, column: str) -> Version[R]:
    """Declare the version column, which orders by its baked sort key (#311)."""
    return Version((), table, column)


def ncol[R: RowKind](table: str, column: str) -> Num[R]:
    """Declare a numeric column."""
    return Num((), table, column)


def bcol[R: RowKind](table: str, column: str) -> Bool[R]:
    """Declare a boolean column."""
    return Bool((), table, column)


def acol[R: RowKind](table: str, column: str) -> Arr[R]:
    """Declare a stored-list column."""
    return Arr((), table, column)


def extcol[R: RowKind](table: str, column: str) -> Str[R]:
    """Declare a text column that lives on an accessor's *extension* table.

    A maintainable accessor roots at `sdmx.artefact`, because that is where its
    hoisted surface and its discriminator live. A column belonging to the
    extension — `data_constraint.constraint_type` — is therefore one hop away,
    across the identity split, and has to say so: declared with `col()` it
    would carry an empty path and bind against the spine alias, which the
    binder rejects.

    The hop name and the table name coincide for every identity split, which is
    what lets this take one argument's worth of information twice over.
    """
    return Str((table,), table, column)


def label[R: RowKind](table: str, column: str) -> Localised[R]:
    """Declare a localised label column — a `MAP(VARCHAR, VARCHAR)`."""
    return Localised((), table, column)


def text[R: RowKind](
    field: str, *, owner: str = "urn", table: str = "localized_text", key: str = "owner_urn"
) -> SideLocalised[R]:
    """Declare a localised label held in a side table keyed by language.

    Defaults describe `sdmx.localized_text`, which is where every
    maintainable's labels live. `annotation_text` is the other shape — one
    field, keyed by `annotation_id` — and passes all four.
    """
    return SideLocalised((), table, field, owner, key)


class _LazyEdge:
    """An edge whose target class is named now and resolved on first read.

    Taking the class *object* meant a target defined later in the file could
    not be named, so the edge silently fell back to `_Maintainable` and the
    path stopped there. Seventeen edges did, and which ones was decided by
    where 63 classes happened to sit in one file — `q.dsd.components.concept`
    continued because `_Concept` is defined above `_Component`, while its
    sibling `q.dsd.components.codelist` did not because `_Codelist` is defined
    below it (#352).

    That made **file order part of the public API**: reordering for
    readability would add or remove what users can express, and no test could
    have told you. Resolving by name removes the ordering constraint
    entirely; `test_query_edges` then pins the resulting shape so it is
    declared rather than inherited.

    The module is taken from the owning class via `__set_name__` rather than
    captured at call time, so this works for any module that declares edges,
    not only `accessors`.
    """

    __slots__ = ("_hop", "_module", "_resolved", "_target")

    def __init__(self, hop: tuple[str, ...], target: str) -> None:
        self._hop = hop
        self._target = target
        self._module: str | None = None
        self._resolved: type[AnyCols] | None = None

    def __set_name__(self, owner: type, name: str) -> None:
        """Record where to look. Called once, when the owning class is built."""
        self._module = owner.__module__

    def _target_type(self) -> type[AnyCols]:
        """The accessor class this edge lands on, resolved once.

        Deliberately not resolved in `__set_name__`: the owning class is being
        built at that moment, so a target defined below it does not exist yet —
        which is the whole problem. First *read* is the earliest safe point.
        """
        if self._resolved is None:
            module = sys.modules[self._module] if self._module else None
            found = getattr(module, self._target, None)
            if not isinstance(found, type):
                msg = (
                    f"edge target {self._target!r} is not a class in "
                    f"{self._module!r}. An edge names its target so it can be "
                    "declared before the target exists; the name still has to "
                    "resolve by the time anyone reads the edge."
                )
                raise PathError(msg)
            self._resolved = found
        return self._resolved

    def __get__(self, obj: object, owner: type | None = None) -> AnyCols:
        """Bind the hop onto the reading accessor's path.

        As `AnyCols.__get__` does, with the target class looked up rather than
        taken from `type(self)` — which is the whole difference.
        """
        target = self._target_type()
        if not isinstance(obj, AnyCols):
            return target(self._hop)
        return target((*obj._path, *self._hop), obj.root, obj.origin)  # sibling accessor, same protocol

    def __repr__(self) -> str:
        return f"<edge {'.'.join(self._hop)} -> {self._target}>"


def edge[T: AnyCols](name: str | tuple[str, ...], target: str) -> T:
    """Declare a to-one edge, named as it is declared in `_edges.EDGES`.

    A *tuple* declares a compound hop that a caller still spells as one
    attribute. `_Dataflow.dsd` is the case: the FK lives on the extension
    table, so reaching it from the spine is `("dataflow", "dsd")` — two edges
    that no caller should have to know about, because the identity split is
    physical layout. Nothing new is needed to resolve it; a declared path was
    already a tuple, and `q.dsd.dimensions.concept.id` is the same shape.

    `target` is the accessor class's **name**, not the class. The declared
    annotation is what the checker reads — it already was — and the string is
    what runtime resolves, on first access, from the declaring module. See
    `_LazyEdge` for why the class object could not be passed and what taking it
    used to cost.

    The declared return type is what a descriptor's factory always declares:
    the value stored on the class is a `_LazyEdge`, and reading it back through
    the protocol yields the `T` the annotation promises.

    `cast` rather than a suppression, because this is exactly the "unchecked
    assertion, one line from what it asserts about" case CLAUDE.md reserves it
    for. The hop through `object` is basedpyright's own prescription for a cast
    between types that do not overlap — which these deliberately do not, since
    the stored value and the read value are different types by design.
    """
    return cast("T", cast("object", _LazyEdge((name,) if isinstance(name, str) else name, target)))


# ── fan-out ───────────────────────────────────────────────────────────────


def _columns_of_pred(predicate: AnyPred) -> list[AnyCol]:
    """The columns a predicate reads at its own level — not inside a nested `.any()`.

    A `Quantified` part is rooted at *its* child, so its columns answer to that
    child, not to this one. `_closure.columns_in` makes the same cut for the
    same reason; restated rather than imported, `_closure` being above us.
    """
    match predicate:
        case Compare(column=c) | Match(column=c) | ArrayHas(column=c) | InRelation(column=c) | IsNull(column=c):
            return [c]
        case InSet(column=c):
            return [c]
        case AggCompare(agg=agg):
            return list(agg.columns)
        case BoolOp(parts=parts):
            return [c for part in parts for c in _columns_of_pred(part)]
        case Not(part=part):
            return _columns_of_pred(part)
        case _:
            return []


def refuse_other_relation(
    predicates: Iterable[AnyPred], table: str, constraint: tuple[str, tuple[str, ...]] | None, *, clause: str
) -> None:
    """Refuse a predicate built from a relation other than the one it is about (#511).

    `q.codelist.codes.any(q.codelist.id == "X")` — the parent's column inside
    the child's quantifier — reached DuckDB as `Referenced table "s1" not
    found`. A column knows the root accessor it was read from (#510), so the
    mistake is visible as the predicate is built, and named in `sl.q` terms.
    The same origin rule `Query.filter` applies: table and, where both have
    one, discriminator.

    Args:
        predicates: The predicates `clause` was handed.
        table: The relation they must be about.
        constraint: Its discriminator, if it has one.
        clause: How the caller spelled the call, for the message.

    Raises:
        TypeError: If a predicate's column was reached from another relation.
    """
    for predicate in predicates:
        for column in _columns_of_pred(predicate):
            origin = column.origin
            if origin is None:
                continue
            theirs = origin.__constraint__
            if origin.__table__ == table and (theirs is None or constraint is None or theirs == constraint):
                continue
            name = origin.__name__.lstrip("_").lower()
            msg = (
                f"{clause} got a predicate built from q.{name}, but it is about each row of "
                f"{table!r} it reaches. Write it from that relation's own accessor — the one "
                f"`sl.q` names for it — and reach anything else by a hop from there."
            )
            raise TypeError(msg)


class FanOut[P: RowKind, C: RowKind]:
    """A to-many edge, giving both spellings for reaching a child.

    **Dotting through** it works — a `Many` accessor *is* the child accessor at
    the parent's row kind — and means *some* child, compiling to a correlated
    `EXISTS` in a filter and `list(...)` in a projection. That is the common
    case and pays no ceremony:

        q.dsd.components.concept.id == "REF_AREA"

    **`.any(*preds)`** groups predicates onto **one** child. The two readings
    are genuinely different and both are wanted, so the rule is positional:

    > Predicates inside one `.any()` call land on one child. Predicates outside
    > it are independent.

        q.dsd.components.any(q.component.concept.id == "REF_AREA",
                             q.component.codelist.id == "CL_GEO")

    Dot-through alone cannot say "the same child satisfies both" — Django
    distinguishes them by chaining, which is unavailable here because `filter`
    chaining is AND by a law with property tests behind it. polars makes the
    same distinction with `pl.element()` bound by `.list.eval()`; the binder
    here is the child's own accessor, which differs only in being typed.

    The child accessor is noise when a fan-out holds one predicate — there is
    nothing to group — and is what marks the grouping when it holds several.
    """

    __slots__ = ()

    def any(self, *preds: Pred[C]) -> Pred[P]:
        """Some child satisfies **all** of `preds` — one correlated `EXISTS`."""
        self._refuse_other_relation(preds, ".any()")
        return Quantified("any", _fan_path(self), preds)

    def _refuse_other_relation(self, preds: Iterable[AnyPred], clause: str) -> None:
        """Hold `preds` to this fan-out's child — the accessor it subclasses."""
        if isinstance(self, AnyCols):
            refuse_other_relation(preds, self.__table__, self.__constraint__, clause=clause)

    def list(self, *columns: Col[P], where: Pred[C] | None = None) -> Agg[P]:
        """Collect several of the child's columns as one list of structs.

        One subquery, and alignment by construction — the reason this is the
        recommended form whenever more than one field is wanted.

        Args:
            columns: What to collect, dotted through this fan-out.
            where: Which children to collect, as a child-rooted predicate
                (#373). Typed `Pred[C]`, so a predicate about another relation
                is a checker error rather than a compile one — the reason to
                prefer this spelling over the column's own `list(where=…)`.
        """
        if not columns:
            msg = "list() needs at least one column to collect."
            raise TypeError(msg)
        self._refuse_other_relation(_where(where), "list(where=…)")
        return Agg[P]("list", columns, _where(where))

    def unique(self, *columns: Col[P], where: Pred[C] | None = None) -> Agg[P]:
        """Collect several of the child's columns, each combination once.

        The `list` of this pair, deduplicated — and deduplicated over the
        *struct*, so two children agreeing on every collected column collapse
        and two differing in one do not.
        """
        if not columns:
            msg = "unique() needs at least one column to collect."
            raise TypeError(msg)
        self._refuse_other_relation(_where(where), "unique(where=…)")
        return Agg[P]("unique", columns, _where(where))

    def count(self, *, where: Pred[C] | None = None) -> Agg[P]:
        """Number of children — `count(*)` over the fan-out.

        With `where=`, the children that satisfy it: `count(where=…) == 0` is
        how "none of them are like this" reads, and it stays row-preserving
        like every other correlated aggregate.
        """
        self._refuse_other_relation(_where(where), "count(where=…)")
        return Agg[P]("count", (_fan_marker(self),), _where(where))

    def every(self, *preds: Pred[C]) -> Pred[P]:
        """Every child satisfies all of `preds`.

        Vacuously true when there are no children, which is standard logic and
        a standard surprise. The negation inside is null-safe (`IS NOT TRUE`),
        so a child whose column is NULL counts as violating — see
        `docs/design/query-language-examples.md` §3.
        """
        self._refuse_other_relation(preds, ".every()")
        return Quantified("every", _fan_path(self), preds)


def _where(predicate: AnyPred | None) -> tuple[AnyPred, ...]:
    """Normalise a `where=` argument to the tuple `AnyAgg` stores.

    One predicate, or `None` for the unrestricted aggregate — which is what
    every aggregate was before #373.

    A tuple is deliberately *not* accepted. `.any(p, q)` is variadic because
    "one child satisfies both" and "some child each" are different questions
    it has to tell apart; a restriction is evaluated per candidate child, so
    that question does not arise and a variadic form would only be a second
    spelling of `&`. It stays a tuple internally because `AnyAgg.where` is
    the compiler's list of clauses to conjoin.
    """
    if predicate is None:
        return ()
    if not isinstance(predicate, AnyPred):
        msg = (
            "where= takes one predicate, not a collection. Combine several "
            "with & (all of them) or | (any of them): "
            "where=(q.component.role == 'Dimension') & (q.component.id == 'GEO')."
        )
        raise TypeError(msg)
    return (predicate,)


def _fan_marker(fan: object) -> AnyCol:
    """A column standing for the fan-out itself, for `count(*)`."""
    return AnyCol(_fan_path(fan), "", "*")


def _fan_path(fan: object) -> tuple[str, ...]:
    if isinstance(fan, AnyCols):
        return fan.path
    msg = f"expected a fan-out accessor, got {type(fan).__name__}"  # pragma: no cover
    raise TypeError(msg)  # pragma: no cover


@attrs.frozen(eq=False)
class Quantified[R: RowKind, C: RowKind](Pred[R]):
    """A quantified predicate over a to-many edge.

    `path` is the route to the fan-out; `parts` are **child**-rooted predicates
    AND-ed together *inside* the quantifier, which is what makes them land on
    one child rather than on any child each.

    Two parameters, because the two ends genuinely differ: the predicate is
    about a row of `R` while its parts are about rows of `C`. `Pred` is
    invariant in its row kind — `__and__` takes another `Pred[R]`, a
    contravariant position — so a single parameter cannot carry both.
    """

    quantifier: str
    path: tuple[str, ...]
    parts: tuple[Pred[C], ...]


def many[T: AnyCols](inverse: str | tuple[str, ...], target: type[T]) -> T:
    """Declare a to-many edge by the inverse name in `_edges.EDGES`.

    Takes a tuple for the same reason `edge()` does; `_Dsd.flows` crosses the
    dataflow extension and back out to the spine.

    Identical in shape to `edge()`: the declaration is the target accessor at
    a one-hop relative path. What differs is only the *target class*, which
    mixes in `FanOut` — so the cardinality change is visible in the type rather
    than in a naming convention.
    """
    return target((inverse,) if isinstance(inverse, str) else inverse)
