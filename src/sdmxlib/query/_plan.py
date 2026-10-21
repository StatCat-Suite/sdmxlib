"""`Plan[R]` — what a query says, separated from how it runs.

`Query` used to hold both: eight fields describing the question and two —
`_conn`, `_resolve` — describing the DuckDB connection that would answer it.
That is fine with one backend and impossible with two, because a planner
deciding *where* a query runs cannot be handed an object that has already
chosen. So the eight move here and `Query` keeps the two, plus a `Plan`.

This is step 1 of `docs/design/query-pushdown.md` §7, and it is deliberately
inert: no behaviour changes, and `tests/support/sql_corpus.py` diffing to
nothing is the whole test.

**There is no separate IR.** §2 of that document argues the case at length; the
short version is that a `Plan` *is* the IR, arrived at by deleting wiring from
a class that already described a query structurally. The one transformation we
owe a backend — evaluating a path prefix separately and applying it as a set
membership (§9.1) — consumes a `Plan` and produces `Plan`s, so it is closed
over this type. A transformation whose output could not be written as a `Plan`
is the thing that would justify a node set instead; none is known.

The module name was earned rather than free: it previously held the *join*
planner, which resolves paths to hops. That is now `_joins.py`, which is what
it always did.
"""

from typing import cast

import attrs

from sdmxlib.query._expr import Cols, Cursor, OrderKey, Pred, RowKind, WinPred


def scanned[R: RowKind, A](root: Cols[R, A]) -> Cols[R]:
    """`root` as a `Plan` holds it: the artefact phantom `A` erased.

    `Plan` is the compiler's view and carries no `A` — what a root *builds* is
    a caller-facing fact, and nothing in the compiler reads it. `Query` keeps
    `A` and is where it is recovered. Threading it through `Plan`, `Push` and
    `Program` instead was measured at 8 further signatures, none of which would
    use it.

    A function because the erasure is not an assignment: `Cols` is invariant
    in `A` (pyrefly infers that for a phantom parameter, where basedpyright did
    not), so `Cols[R, Dataflow]` is not a `Cols[R]`. This is the one place that
    says so — `build()` and any test constructing a `Plan` directly go through
    it rather than each carrying the cast (#425). Both parameters are phantoms,
    so the cast has no runtime meaning.
    """
    return cast("Cols[R]", root)


@attrs.frozen
class Plan[R: RowKind]:
    """The question a query asks, with nothing about answering it.

    Every field is one of `query-language.md` §4's plan nodes, which is the
    measurement that made an IR unnecessary: `Scan` is `root`, `Filter` is
    `predicates`, `Sort` is `order_keys`, `Limit` is the window. `Join` is not
    a field because it is not a choice — the edge graph declares the routes and
    a path names one, so joins are derived from `predicates` and `order_keys`
    rather than stored beside them.

    Frozen and refining: every `Query` clause evolves a new `Plan`, and the
    refinement laws in `_common.py` govern which evolutions are admissible.

    **Two plans are compared with `canonical_key`, not with `==`.** `root` is
    an accessor, and an accessor refuses comparison — `q.dataflow == x` raises
    rather than returning `False`, because a relation is not a value and a
    silent `False` there is a filter that compiles and matches nothing. So the
    `==` attrs synthesises here raises too, which is inherited rather than
    chosen and is fine: `canonical_key` is the comparison this type needs, it
    treats `predicates` as a set, and it is what a cache keys on anyway (#270).

    Attributes:
        root: The relation scanned, as an accessor carrying `__table__` and
            `__constraint__`.
        predicates: Conjunction. Order is not meaning, which is why
            `_common._COMMUTATIVE_FIELDS` keys this field for `canonical_key`.
        qualifiers: Windowed predicates — SQL's `QUALIFY`. A conjunction too,
            and keyed commutatively for the same reason.
        order_keys: The caller's sort, replacing rather than appending (#294).
        default_order: The relation's *natural* order, set by the collection a
            query came from rather than by a caller. Distinct from
            `order_keys` because the two are different claims — see
            `Query.default_order`.
        unordered: Whether the caller has declined a total order — the third
            state `order_by(None)` sets, distinct from *having no keys*. Empty
            `order_keys` still emits the root-key tiebreak; this drops it, and
            with it the `ORDER BY` entirely (#389).
        cursor: Keyset paging position, or `None` for the first page.
        limit_n: Window size, or `None` for unbounded.
        offset_n: Window start.
    """

    root: Cols[R]
    predicates: tuple[Pred[R], ...] = ()
    qualifiers: tuple[WinPred[R], ...] = ()
    order_keys: tuple[OrderKey, ...] = ()
    default_order: tuple[OrderKey, ...] = ()
    unordered: bool = False
    cursor: Cursor[R] | None = None
    limit_n: int | None = None
    offset_n: int = 0

    @property
    def order(self) -> tuple[OrderKey, ...]:
        """What to sort by: the caller's keys, or the relation's natural order.

        Says nothing about `unordered` — a plan that has declined ordering
        still remembers the keys it would sort by, so `order_by(None)` is
        undone by a later `order_by(...)` rather than by rebuilding. The
        compiler reads the flag; this reads the keys.
        """
        return self.order_keys or self.default_order

    def sliced(self) -> bool:
        """Whether a window has been applied, which closes the plan to refinement."""
        return self.limit_n is not None or bool(self.offset_n)
