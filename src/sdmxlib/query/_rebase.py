"""Re-rooting a predicate onto a shorter path.

The §9.1 rewrite needs one thing the compiler never has: a predicate written
against one root, expressed against another. `q.dataflow` filtered by
`dsd.components.concept.id == "REF_AREA"` becomes `q.dsd` filtered by
`components.concept.id == "REF_AREA"`, whose rows a second step then matches
by key. DuckDB never needs this — it has joins — so nothing in `_compile.py`
does it.

**Why this is generic rather than a case per column class.** A column is one of
nine classes with divergent constructors: `Label` carries a language,
`SideLabel` an owner and a key, `Win` a whole window specification, `NumExpr`
an operator and operands. Reconstructing each by calling its `__init__` means
knowing all of that, and a class that gains a field silently loses it here —
producing a *wrong query* rather than an error, since the rebuilt column still
compiles.

So a rebase copies the instance and replaces only `_path`, which every column
has and no column computes from anything else. `copy.copy` respects `__slots__`
and carries every other field across untouched, including ones added later. The
recursion into `_source` / `_partition` / `_operands` is the part that is not
free, and `test_every_column_slot_holding_columns_is_rebased` counts those
slots off the classes rather than trusting this list.

**What is refused, and why refusing is the point.** A `RowIn` carries SQL
already compiled against the root's key columns; there is nothing to re-root
and pretending otherwise would emit a subquery correlated to a table no longer
in the `FROM`. A column whose path does not start with the prefix is not under
it at all. Both raise, because a rewrite that silently declined would leave the
planner believing it had pushed something it had not.
"""

import copy
from typing import cast, override

import attrs

from sdmxlib.query._expr import (
    AggCompare,
    AnyCol,
    ArrayHas,
    BoolOp,
    Compare,
    InRelation,
    InSet,
    IsNull,
    Match,
    Not,
    Pred,
    Quantified,
    RowIn,
    RowKind,
)


class RebaseError(ValueError):
    """A predicate cannot be expressed against the requested root."""


#: Slots whose value is a column, or a tuple of them, and so needs rebasing
#: too. Counted against the classes in the tests rather than trusted here: a
#: new nesting slot that is missed leaves an inner column pointing at the old
#: root, which compiles and answers the wrong question.
_NESTED_SLOTS = ("_source", "_partition", "_operands")


def _strip(path: tuple[str, ...], prefix: tuple[str, ...], column: AnyCol) -> tuple[str, ...]:
    if path[: len(prefix)] != prefix:
        msg = (
            f"{column!r} is reached by {path or '()'}, which does not start with "
            f"{prefix} — it is not under that prefix, so re-rooting there would "
            f"change which rows the predicate is about."
        )
        raise RebaseError(msg)
    return path[len(prefix) :]


def rebase_column[C: AnyCol](column: C, prefix: tuple[str, ...]) -> C:
    """`column` as seen from a scan rooted at `prefix`.

    Args:
        column: The column, pathed from the outer root.
        prefix: Declared edge names to re-root at — the *route*, which may be
            longer than the attribute a caller wrote (`q.dataflow.dsd` is
            `("dataflow", "dsd")`, a composite edge crossing the extension
            table).

    Returns:
        A copy at the shortened path, every other field carried across.

    Raises:
        RebaseError: If the column is not reached through `prefix`.
    """
    out = copy.copy(column)
    # `_path` is private to `AnyCol` and this is the one place outside it that
    # writes the field. The alternative — a public setter — would advertise on
    # every column an operation only a planner has any business performing.
    object.__setattr__(out, "_path", _strip(column.path, prefix, column))
    # The rebased column is about the relation `prefix` lands on, not the root
    # it was read from, and that relation is the planner's choice: the caller's
    # predicate was already held to its own query's root (#510). Unknown is the
    # truthful origin, and `Query.filter` does not refuse an unknown one.
    object.__setattr__(out, "_origin", None)
    for slot in _NESTED_SLOTS:
        nested: object = getattr(out, slot, None)
        if isinstance(nested, tuple):
            object.__setattr__(
                out,
                slot,
                tuple(rebase_column(item, prefix) if isinstance(item, AnyCol) else item for item in nested),
            )
    return out


class _PathOnly(AnyCol):
    """A stand-in so `_strip`'s message can name a path with no column.

    A `Quantified` locates a fan-out by path and has no column of its own, but
    the failure it can hit is exactly the one `_strip` reports.
    """

    def __init__(self, path: tuple[str, ...]) -> None:
        super().__init__(tuple(path), "", "")

    @override
    def __repr__(self) -> str:
        return f"the fan-out at {self.path}"


def rebase[R: RowKind, C: RowKind](predicate: Pred[R], prefix: tuple[str, ...]) -> Pred[C]:
    """`predicate` as asked of a scan rooted at `prefix`.

    **Two row kinds, and that is the point.** The result is a predicate about
    rows of the *inner* relation, not the outer one — re-rooting is exactly a
    change of subject. Returning `Pred[R]` would have type-checked and been a
    lie, and the lie would surface as a planner cheerfully putting a
    dataflow-shaped predicate on a DSD scan. `C` is free for the caller to
    bind, the same bivariance `Ref.unresolved` relies on.

    Structural: every column drops the prefix from its path, composites
    recurse, and a `Quantified`'s own path drops it too while its parts are
    left alone — they are already rooted at the fan-out, not at the outer root.

    Args:
        predicate: The predicate, written against the outer root.
        prefix: The declared edge route to re-root at.

    Returns:
        An equivalent predicate against the inner root.

    Raises:
        RebaseError: If any part of it is not reachable through `prefix`, or
            carries SQL already compiled against the outer root.
    """
    # One cast, and it is a re-labelling rather than a conversion: a row kind
    # is a *phantom*, carried only so the checker can stop a predicate built
    # for one root being filtered on another. Nothing in the value changes
    # here — `_rebased` rebuilds the columns and returns the same shape — so
    # what the cast asserts is exactly the thing this function exists to do,
    # and there is no runtime probe that could witness it. The alternative was
    # six casts, one per `case` arm, saying the same thing six times.
    return cast("Pred[C]", _rebased(predicate, prefix))


def _rebased[R: RowKind](predicate: Pred[R], prefix: tuple[str, ...]) -> Pred[R]:
    """The structural work. See `rebase`, which re-labels the row kind."""
    match predicate:
        case Compare(column=column) | Match(column=column) | ArrayHas(column=column) | InRelation(column=column):
            return attrs.evolve(predicate, column=rebase_column(column, prefix))
        case IsNull(column=column) | InSet(column=column):
            return attrs.evolve(predicate, column=rebase_column(column, prefix))
        case AggCompare(agg=agg):
            rebased = attrs.evolve(agg, columns=tuple(rebase_column(c, prefix) for c in agg.columns))
            return attrs.evolve(predicate, agg=rebased)
        case BoolOp(parts=parts):
            return attrs.evolve(predicate, parts=tuple(_rebased(part, prefix) for part in parts))
        case Not(part=part):
            return attrs.evolve(predicate, part=_rebased(part, prefix))
        case Quantified(path=path, parts=parts):
            # `parts` are child-rooted already: the quantifier's own path is
            # what locates the fan-out, and only that moves.
            del parts  # child-rooted; only the quantifier's own path moves
            return attrs.evolve(predicate, path=_strip(tuple(path), prefix, _PathOnly(tuple(path))))
        case RowIn():
            msg = (
                "cannot re-root a set operation: its subquery is already compiled against "
                "the outer root's key columns, so the SQL would correlate to a relation "
                "no longer in the FROM. Express the set operation on the inner query instead."
            )
            raise RebaseError(msg)
        case _:
            msg = f"{type(predicate).__name__} has no rebase rule; add one rather than letting it re-root silently."
            raise RebaseError(msg)
