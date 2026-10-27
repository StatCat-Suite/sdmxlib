"""Predicates to SQL.

Two rules decide everything here.

**A path that crosses a to-many hop compiles to a correlated subquery, never a
join.** That is what makes `q.dsd.dimensions.concept.id == "X"` mean *some*
dimension while leaving the row count alone — the property #254 chose `EXISTS`
for, and the reason two fan-outs in one filter cannot produce a cartesian
product the way two joins would.

**Predicates inside one `.any()` share a subquery; predicates outside it do
not.** That is the whole difference between "some dimension has that concept
and some has that codelist" and "one dimension has both", which return
different rows.

Compilation is organised by **scope**: the outer query is one, each correlated
subquery is another. A scope collects every flat path its predicates need,
builds *one* join plan for them, and compiles against it. Doing it per-leaf
instead would defeat shared-prefix deduplication, which the benchmark in
`query-language-evidence.md` made a requirement rather than an optimisation.

Values are always bound, never interpolated. Identifiers come from the edge
graph, which `tests/unit/test_query_edges.py` validates against the live
schema, so the only strings reaching the SQL text are ones the schema vouches
for.
"""

import itertools
from collections.abc import Iterable, Sequence
from typing import NamedTuple

import attrs

from sdmxlib.model._label_match import Fold, eq_sql, match_sql
from sdmxlib.query._expr import (
    Agg,
    AggCompare,
    AggPred,
    AnyAgg,
    AnyCol,
    AnyCols,
    AnyCursor,
    AnyPred,
    AnyWinPred,
    ArrayHas,
    BoolOp,
    Compare,
    Computed,
    InRelation,
    InSet,
    IsNull,
    Label,
    Match,
    Not,
    OrderKey,
    Quantified,
    RowIn,
    RowKind,
    SideLabel,
    Win,
    WinCompare,
)
from sdmxlib.query._joins import (
    STRUCTURAL_SCHEMA,
    Hop,
    Join,
    JoinPlan,
    KeyRank,
    PathError,
    RootJoin,
    plan_joins,
    resolve_path,
)


@attrs.frozen
class Sql:
    """A SQL fragment and the binds it consumes, in placeholder order."""

    text: str
    params: tuple[object, ...] = ()


#: Alias the outer scope always gets. `_Namespace` hands out `s0`, `s1`, … in
#: order and the root is allocated first, so this is a fact about the compiler
#: rather than a guess — asserted in `tests/unit/test_query_compile.py`.
#:
#: A constant rather than a field on `CompiledQuery`, because that had to
#: become a plain `(sql, params)` pair to satisfy `sdmxlib.sql.SqlRelation`.
ROOT_ALIAS = "s0"

#: The position an item takes when its scheme gives it none, or does not list
#: it: DuckDB's largest `INT`, so it sorts after every positioned item, as
#: `NULLS LAST` would, and the keyset comparison never meets a null rank.
_UNRANKED = 2_147_483_647


class CompiledQuery(NamedTuple):
    """A runnable statement and its binds, in placeholder order.

    A `NamedTuple` so it is **both** `compiled.sql` and `sql, params = compiled`
    — which is what lets `Query` satisfy `sdmxlib.sql.SqlRelation` structurally
    rather than through an adapter. That protocol is how a lazy single-column
    relation splices into `WHERE col IN (SELECT ...)`, and it is implemented by
    other libraries too, so conforming means a new-language query goes wherever
    a `CodeQuery` already goes.

    `params` is a `list` rather than a tuple to match the protocol exactly. A
    `tuple[object, ...]` is not assignable to `list[object]`, so the conformance
    would have been structural in spirit and rejected by the checker.
    """

    sql: str
    params: list[object]


class _Namespace:
    """Hands out alias namespaces that stay distinct across nested scopes."""

    __slots__ = ("_counter",)

    def __init__(self) -> None:
        self._counter = itertools.count()

    def next(self) -> tuple[str, str]:
        """A root alias, and a prefix for the joins hanging off it."""
        index = next(self._counter)
        return f"s{index}", f"s{index}_"


def _split_at_fan_out(hops: Sequence[Hop]) -> tuple[int, Hop] | None:
    """Index and hop of the first to-many step, or `None` if the path is flat."""
    for index, hop in enumerate(hops):
        if hop.to_many:
            return index, hop
    return None


#: Hops a caller never writes: the identity split from a maintainable's
#: extension table back to the artefact spine, which a compound edge walks on
#: the caller's behalf (`dsd.dataflows` is `("dataflows", "spine")`).
_UNSPOKEN_HOPS = frozenset({"spine"})


def spelled(path: tuple[str, ...]) -> str:
    """A path as a refusal should show it — without the hops the caller never wrote (#511).

    `'dsd.dataflows.spine'` named an internal hop in an error about
    `q.component.dsd.dataflows.id`.
    """
    return ".".join(step for step in path if step not in _UNSPOKEN_HOPS)


def refuse_to_many_projection(root_table: str, path: tuple[str, ...], route: str) -> None:
    """Refuse a projected path that fans out, or return.

    Shared by `Query.select`, which checks when the column is handed over, and
    by the compiler, which checks again when the query runs. Two callers, one
    message — the compiler's copy is the backstop for a path that reaches
    projection without passing through `select` (a `group_by` key, a rebased
    predicate), and it must not be able to say something different.

    Cheap enough to do eagerly: `resolve_path` reads the declared edge graph
    and touches no database, so the to-many-ness of `annotations` is knowable
    the moment the column is written (#342).
    """
    if _split_at_fan_out(resolve_path(root_table, path)) is None:
        return
    msg = (
        f"{route!r} crosses a to-many edge, so it has no single value "
        f"per row. Collect it — .list() for the values, .count() for "
        f"how many, .n_unique() for distinct — or root the query at "
        f"the child instead, which gives one row per child."
    )
    raise PathError(msg)


def _order_clause(agg: AnyAgg, order: Sql | None, collected: str) -> tuple[str, tuple[object, ...]]:
    """The ` ORDER BY …` an ordered aggregate carries, or nothing.

    Refuses `unique()` ordered by anything but itself. A `DISTINCT` aggregate
    collapses the rows any other key would have come from, so there is no
    answer to "which duplicate's key" — SQL says so too, and DuckDB refuses it
    outright: *"In a DISTINCT aggregate, ORDER BY expressions must appear in
    the argument list"*. Caught here so the message names the fix rather than
    the binder's rule (#327).
    """
    if order is None:
        return "", ()
    if agg.kind == "unique" and order.text.removesuffix(" DESC") != collected:
        msg = (
            "unique() can only be ordered by itself — use .sort(), not "
            ".sort_by(other). A distinct aggregate collapses the rows the "
            "other column's values would have come from, so there is no one "
            "key per collected value."
        )
        raise PathError(msg)
    return f" ORDER BY {order.text}", order.params


def _empty_list_as_empty(expression: str) -> str:
    """`[]` rather than `NULL` for a fan-out that reached no rows.

    SQL's `list()` over zero rows is `NULL`, and this language usually takes
    SQL's answer. Not here: it takes polars' *names* for the aggregates —
    `list`, `unique`, `n_unique`, `mean` — and #326 established that adopting a
    name without its semantics is the thing to avoid. polars answers `[]`.

    The concrete cost of `NULL` was that `len(unique()) == n_unique()` — the
    invariant #326 restored — needed `or []` at the call site to hold for a
    group with no rows, so every caller wrote the normalisation this does once.

    **The distinction a `NULL` carried is not lost.** "No rows at all" and
    "rows, all null" stay apart as `[]` versus `[None]`, which is exactly how
    polars keeps them apart too — #341 was filed believing polars could not
    express the difference, and it can (`[]` for an aggregate over zero rows,
    `[None]` for one null row).
    """
    return f"COALESCE({expression}, [])"


def _facet_clause(hop: Hop, alias: str) -> Sql:
    """The constant a facet applies to the child — `component_type = ?`."""
    if hop.facet is None:
        return Sql("")
    values = hop.facet.values
    if len(values) == 1:
        return Sql(f"{alias}.{hop.facet.column} = ?", (values[0],))
    marks = ", ".join("?" for _ in values)
    return Sql(f"{alias}.{hop.facet.column} IN ({marks})", tuple(values))


def _conjoin(parts: Iterable[Sql]) -> Sql:
    kept = [p for p in parts if p.text]
    if not kept:
        return Sql("TRUE")
    if len(kept) == 1:
        return kept[0]
    text = " AND ".join(p.text for p in kept)
    params = tuple(itertools.chain.from_iterable(p.params for p in kept))
    return Sql(f"({text})", params)


def _reference(column: AnyCol, alias: str) -> Sql:
    """Qualify a column, with whatever binds the reference itself consumes.

    Three shapes, and a caller writes the same thing for the last two:

    * a plain column — `alias.col`
    * an inline localised `MAP` — `alias.name[?]`, how `code.name` is stored
    * a side-table label — a correlated scalar subquery into
      `sdmx.localized_text`, how every maintainable's labels are stored

    The subquery is row-preserving and yields `NULL` when the language is
    absent, which is exactly what a missing map key does. So the two storage
    shapes are indistinguishable at the call site, which is the point:
    `q.dsd.name["en"]` and `q.code.name["en"]` read the same and mean the same.
    """
    if isinstance(column, SideLabel):
        # No `field` discriminator on `annotation_text` — it holds one field,
        # so an `AND field = ?` would be an always-true condition reading as
        # though something were being selected on.
        field = " AND lt.field = ?" if column.field else ""
        params = (column.field, column.lang) if column.field else (column.lang,)
        return Sql(
            f"(SELECT lt.text FROM {STRUCTURAL_SCHEMA}.{column.table} lt"
            f" WHERE lt.{column.key} = {alias}.{column.owner}"
            f"{field} AND lt.lang = ?)",
            params,
        )
    if isinstance(column, Label):
        return Sql(f"{alias}.{column.physical}[?]", (column.lang,))
    return Sql(f"{alias}.{column.column}")


#: Window functions taking no argument. `rank() OVER (...)` is a syntax error
#: with a column inside the parentheses; `lag(v, 1) OVER (...)` is one without.
_NULLARY_WINDOWS = frozenset({"rank", "dense_rank", "row_number"})

#: Aggregates whose windowed form takes **no** `ORDER BY`.
#:
#: An aggregate given a window ORDER BY acquires a *running* meaning:
#: `sum(v) OVER (PARTITION BY g ORDER BY v)` is a cumulative total, while
#: `sum(v) OVER (PARTITION BY g)` is the partition's. `.over()` means the
#: second everywhere else in this language, and percent-of-total —
#: `x.sum() / x.sum().over()` — is wrong under the first.
_FRAMED_AGGREGATES = frozenset({"count", "n_unique", "list", "unique", "sum", "mean", "min", "max"})


def _window(column: Win[RowKind], refs: Sequence[Sql], order_ref: Sql | None = None) -> Sql:
    """A framed window expression — `fn(...) OVER (PARTITION BY ... ORDER BY ...)`.

    `refs` are the already-qualified source and partition references, source
    first, because qualifying needs a join plan and this does not have one.

    `order_ref` is the source rendered as a *sort* key, which differs from the
    source itself only for `version` (#311). Two references rather than one
    because the source plays two roles: `lag(version)` returns the value a
    caller sees and orders by the key that sorts correctly, and collapsing
    them would either return the padded key or rank `1.9` above `1.10`.
    """
    arity = len(column.source)
    source, partition = refs[:arity], refs[arity:]
    if column.function in _NULLARY_WINDOWS:
        arguments = ""
    elif column.function in {"lag", "lead"}:
        arguments = f"{source[0].text}, {column.offset}"
    elif column.function == "unique":
        arguments = f"DISTINCT {source[0].text}"
    else:
        # `count` lands here too, and deliberately: it emitted `count(*)`
        # until the #304 triage, on the reasoning that a windowed count counts
        # rows rather than values. But the fan-out marker — the one source
        # that is not a real column — cannot reach a window at all: its path
        # is to-many by construction and `_flat_reference` refuses that
        # above. So `count(*)` dropped the counted column from the SQL while
        # still emitting its bind parameters, and
        # `description["en"].count().over(...)` raised `Parameter
        # argument/count mismatch` while any nullable column counted its
        # NULLs. Once corrected the branch said exactly what this one says,
        # and two mutants survived on the strength of being unable to tell
        # them apart — so it is gone.
        arguments = source[0].text
    # Each spells a DuckDB function that is not what the language calls it:
    # `unique` is `list(DISTINCT …)` and `mean` is `avg`. `n_unique` is not
    # here: it returns below as two windowed aggregates, before `name` or
    # `arguments` is read, and its entries in both were dead (#324).
    name = {"unique": "list", "mean": "avg"}.get(column.function, column.function)

    frame = []
    params: list[object] = []
    if partition:
        frame.append(f"PARTITION BY {', '.join(p.text for p in partition)}")
    if column.function not in _FRAMED_AGGREGATES:
        # rank / row_number / lag / lead are meaningless without an order, and
        # an aggregate acquires a *running* meaning if given one — so the
        # window's own ORDER BY is emitted for exactly the first group.
        sort = order_ref if order_ref is not None else source[0]
        frame.append(f"ORDER BY {sort.text}{' DESC' if column.descending else ''} NULLS LAST")
    for ref in refs:
        params.extend(ref.params)
    over = f"OVER ({' '.join(frame)})"
    if column.function == "n_unique":
        # Two windowed aggregates, for the same reason the plain form is two
        # (#326): `count(DISTINCT x)` does not count a null and this language
        # says a null is a value. Emitted here as well as there because a rule
        # that held in a `SELECT` and not in an `OVER` would be worse than the
        # original disagreement — the caller would have no name to tell them
        # apart. `test_the_window_form_counts_null_the_same_way` caught this
        # branch being missed the first time.
        reference = source[0].text
        params = [*params, *source[0].params]
        return Sql(
            f"(COALESCE(count(DISTINCT {reference}) {over}, 0)"
            f" + COALESCE(CAST(bool_or({reference} IS NULL) {over} AS INT), 0))",
            tuple(params),
        )
    return Sql(f"{name}({arguments}) {over}", tuple(params))


def _field_name(column: AnyCol) -> str:
    """Struct field name for a collected column — the language for a label."""
    return column.lang if isinstance(column, Label) and column.lang else column.projected_name


type _Leaf = (
    Compare[RowKind] | IsNull[RowKind] | InSet[RowKind] | Match[RowKind] | ArrayHas[RowKind] | InRelation[RowKind]
)


def _operator(pred: _Leaf, reference: str, lang: tuple[object, ...] = ()) -> Sql:
    match pred:
        case Match(op=op, fold=fold, value=value):
            # Built by `_label_match`, shared with the existing DSLs: #222 and
            # #255 are what that module cost, and a second implementation would
            # be a second chance to get escaping and folding wrong.
            text, params = match_sql(reference, op, fold, value)
            return Sql(text, lang + tuple(params))
        case Compare(op=op, value=value, fold=fold) if fold is not Fold.NONE:
            text, params = eq_sql(reference, fold, value, negate=op == "!=")
            return Sql(text, lang + tuple(params))
        case Compare(op=op, value=value):
            return Sql(f"{reference} {op} ?", (*lang, value))
        case IsNull(negate=negate):
            return Sql(f"{reference} IS {'NOT ' if negate else ''}NULL", lang)
        case ArrayHas(value=value):
            # `? = ANY(col)`, not `col @> [?]`: the first is the portable
            # spelling and reads as the question actually being asked.
            return Sql(f"? = ANY({reference})", (*lang, value))
        case InRelation(sql=sql, params=params):
            return Sql(f"{reference} IN ({sql})", lang + tuple(params))
        case InSet(values=values):
            if not values:
                # `In(d, [])` is False, normalised here rather than emitted as
                # `IN ()`, which is a syntax error in most dialects.
                return Sql("1 = 0")
            # The whole set as *one* list parameter (#411), not a placeholder
            # per value as #301 left it. Binding is linear either way, because
            # DuckDB's Python client converts each value, but the list is never
            # slower and usually much faster — duckdb 1.5.5, 1,000 / 50,000
            # values, `IN (?, …)` against `= ANY(?)`:
            #
            #     pandas absent      1.1x / 1.1x
            #     pandas stubbed     1.5x / 2.1x   (`sys.modules["pandas"] = None`)
            #     pandas installed   2.6x / 9.3x
            #
            # #301 measured 1.0x and kept the placeholders. That run had pandas
            # absent, where DuckDB retries `import pandas` twice for every bound
            # value to check for `pandas.NA` and never remembers the failure;
            # the probe swamped both shapes, so they tied. Strip it out and the
            # per-placeholder overhead shows.
            #
            # `ANY` over a list holding NULL has `IN`'s three-valued semantics,
            # so no row changes — the differential properties are what say so.
            #
            # Still linear, though. What is flat is registering an Arrow buffer,
            # which is `Session.value_set`, explicit because doing it implicitly
            # would make `to_sql()` output reference a relation that does not
            # exist. #301 surveyed six consumers before leaving it explicit:
            # none of them calls `is_in` at all, and the ones filtering by a
            # set use a subquery, which is already free.
            return Sql(f"{reference} = ANY(?)", (*lang, list(values)))
        case _:  # pragma: no cover - exhaustive over the leaf kinds
            msg = f"cannot compile {type(pred).__name__}"
            raise PathError(msg)


def _member_collapse(
    *,
    hop: Hop,
    tail: tuple[str, ...],
    leaves: Sequence[AnyPred],
    negate: bool,
    violations: bool,
    strip: int,
    outer_alias: str,
) -> Sql | None:
    """`ancestors.id == X` as `? = ANY(outer.ancestor_ids)`, or `None` (#349).

    A to-many hop is a correlated `EXISTS`, correctly and in general. But a
    *membership* hop's far side is reached by looking the outer row's own list
    up in the target's key — so when the only thing asked of that far side is
    an equality **on that same key**, the subquery joins a table solely to
    assert that a constant appears in an array the outer row already carries.
    `? = ANY(s0.ancestor_ids)` says it without the join.

    Measured on the 2021 Census Profile bundle (`hierarchy_node`, 1.88M rows), an
    8,943-node subtree walk: 63.9 ms to 9.1 ms for `.ids()`, 145 ms to 41 ms
    for `.collect()`.

    Narrow by necessity. The rewrite drops two things the `EXISTS` asserted,
    and each one bounds it:

    * **The ancestor row must exist.** The collapsed form only reads the outer
      row's list. The two agree exactly when every id in `ancestor_ids` is a
      live key — which the writer maintains by baking the closure, and which
      nothing asserted until `test_ancestor_ids_are_referentially_intact`.
    * **The other composite pairs.** `ancestor_ids` is scoped by construction
      — a node's list holds ids from its own hierarchy — so
      `s1.hierarchy_urn = s0.hierarchy_urn` is implied by that same invariant
      rather than separately assumed.

    Hence the guards: forward only (the reverse hop's list is on the *inner*
    row, where no rewrite reaches it), no facet, no tail, and exactly one leaf
    whose column is the joined key itself — or, on a nested tree, the id column
    `Edge.member_ids` pairs with a second baked list. There the edge joins on
    paths (#439) and the rewrite reads the ancestors' ids, which the writer
    bakes from the same walk and so holds to the same invariant. `ancestors.level.id == "PR"` keeps
    its join, as does `ancestors.id.is_in([...])` — a list-overlap form exists
    but is a different rewrite, not this one.

    Positive quantification only. `none()` and `every()` turn on a child
    *failing* a test, and "no row satisfies" over a NULL list is not
    `NOT (? = ANY(NULL))`, which is NULL rather than true.
    """
    index = hop.edge.member_of
    if index is None or not hop.forward or hop.facet is not None or tail or negate or violations:
        return None
    if len(leaves) != 1:
        return None
    source_column, target_column = hop.edge.columns[index]
    # The edge's own list, or — on a nested tree, whose edge joins on paths —
    # the parallel list of the members' ids (`Edge.member_ids`, #439).
    lists = {target_column: source_column}
    if hop.edge.member_ids is not None:
        id_list, id_column = hop.edge.member_ids
        lists[id_column] = id_list
    match leaves[0]:
        case Compare(op="=", fold=Fold.NONE, column=column, value=value) if (
            not isinstance(value, AnyCol)
            and column.table == hop.table
            and column.column in lists
            and not column.path[strip:]
        ):
            return Sql(f"? = ANY({outer_alias}.{lists[column.column]})", (value,))
        case _:
            return None


class _Scope:
    """One query level: the outer statement, or one correlated subquery.

    Holds a single join plan covering every flat path its predicates reach, so
    two routes through the same relation share one join.
    """

    __slots__ = (
        "_names",
        "_plan",
        "_ranked",
        "_ranks",
        "_rendered",
        "_root_joins",
        "_strip",
        "_table",
        "_tail",
        "_used",
    )

    def __init__(
        self,
        table: str,
        predicates: Sequence[AnyPred],
        names: _Namespace,
        *,
        strip: int = 0,
        tail: tuple[str, ...] = (),
        extra_paths: Sequence[tuple[str, ...]] = (),
        schema: str | None = STRUCTURAL_SCHEMA,
        joins: Sequence[RootJoin] = (),
        ranks: Sequence[KeyRank] = (),
    ) -> None:
        self._table = table
        self._names = names
        #: Hops this scope must walk before any predicate path applies.
        #:
        #: A fan-out is not always the last hop of the edge it is declared as:
        #: `dsd.flows` is `("flows", "spine")`, to-many out to the dataflow
        #: extension and to-one back to the artefact spine. The subquery opens
        #: at the to-many hop, so `"spine"` is left over and every path inside
        #: it is relative to the far end.
        #:
        #: The correlation still anchors on `self.alias`, the *first* table —
        #: that is the row the FK points at, whatever is joined after it.
        self._tail = tail
        #: Path elements already consumed by an enclosing scope.
        #:
        #: A dot-through leaf carries a *parent*-rooted path, so once its
        #: fan-out hop opens a subquery the remainder must be read relative to
        #: the child. `.any()` parts are child-rooted already and strip nothing
        #: — which is exactly the difference between the two spellings.
        self._strip = strip
        alias, prefix = names.next()
        paths = self._flat_paths(predicates)
        for extra in extra_paths:
            if extra and extra not in paths:
                paths.append(extra)
        self._plan = plan_joins(
            table,
            sorted(paths),
            root_alias=alias,
            alias_prefix=prefix,
            # Only the *root* can sit outside `sdmx`. A nested scope opens at a
            # fan-out, which is a declared edge, and every edge target is
            # structural — so the default is right for every inner scope.
            root_schema=schema,
        )
        #: Joins the *root* declared, keyed by table. No path reaches one, so
        #: `alias_of` — keyed by path prefix — cannot find it and `reference`
        #: has to look here instead.
        #:
        #: Alias index 0 because `plan_joins` numbers hop aliases from 1, so a
        #: root join can never collide with one.
        self._root_joins: dict[str, Join] = {
            declared.table: Join(f"{prefix}0", declared.table, optional=True, using=declared.using, schema=None)
            for declared in joins
        }
        #: Root-join tables something in this statement actually reads.
        #:
        #: A hop join is planned from the paths a predicate names, so it exists
        #: only where it is needed. A root join has no path, so until #388 it
        #: was emitted unconditionally — which on the 2021 Census Profile bundle made `count()`
        #: over 1.14 B observations 2600x the scan it decorates, for a join
        #: nothing selected from. DuckDB will not drop it itself: a `LEFT JOIN`
        #: is only row-preserving if the right side is unique on the key, and a
        #: DuckLake sidecar declares no such constraint. Measured, and it does
        #: not: adding a `UNIQUE INDEX` on the sidecar's key left the hash join
        #: in the plan and the 2.6 s on the clock.
        #:
        #: Recorded rather than derived. `_alias_of_join` is the only way an
        #: alias here reaches the SQL, so the set is complete by construction —
        #: where a static pass over the clauses would have to enumerate them and
        #: could fall behind a new one, which is how `_projected_prefixes` and
        #: `_qualify_paths` each came by their own docstring about a clause they
        #: had missed.
        self._used: set[str] = set()
        #: The item-scheme join each ranked key column sorts through, keyed by
        #: the column. `LEFT` and row-preserving: the scheme's table is unique
        #: on scheme and item id.
        self._ranks: dict[str, tuple[Join, KeyRank]] = {
            rank.column: (
                Join(
                    f"{prefix}k{index}",
                    rank.table,
                    optional=True,
                    on=(
                        f"{prefix}k{index}.{rank.scheme_column} = ? "
                        f"AND {prefix}k{index}.{rank.item_column} = {alias}.{rank.column}"
                    ),
                ),
                rank,
            )
            for index, rank in enumerate(ranks)
        }
        #: Ranked columns something in this statement sorts by. Pruned as
        #: `_used` is: a `count()` sorts nothing, so it joins no codelist.
        self._ranked: set[str] = set()
        #: Set by `from_sql`, so a reference made after the `FROM` was rendered
        #: is a raise rather than an alias the statement never joined.
        self._rendered = False

    @property
    def plan(self) -> JoinPlan:
        return self._plan

    def from_sql(self) -> Sql:
        """The `FROM` clause, with the root joins nothing referenced left out.

        **Call this last.** It reads `_used`, which fills in as the other
        clauses compile, so a caller that renders the `FROM` before its
        `ORDER BY` would drop a join that clause goes on to name. That fails at
        the binder rather than quietly — an unknown alias is not a wrong
        answer — but the entry points below still assemble the statement in
        clause order and build this at the end.

        Pruning is safe because the join is `LEFT` and its right side is a
        series-attributes sidecar: one row per series key. A sidecar that
        duplicated a key would already duplicate observations in an
        unprojected read, so the join is row-preserving wherever it is correct
        at all — which is the assumption DuckDB cannot make for itself and we
        can.

        The item-scheme joins a ranked key column sorts through come last, one
        per column the statement sorts by, each binding its scheme's URN.
        """
        self._rendered = True
        used = tuple(join for table, join in self._root_joins.items() if table in self._used)
        # Ahead of the hop joins: this one hangs off the root, and a hop's
        # `ON` may name the root alias but never the sidecar's.
        plan = attrs.evolve(self._plan, joins=(*used, *self._plan.joins)) if used else self._plan
        ranked = [(join, rank) for column, (join, rank) in self._ranks.items() if column in self._ranked]
        text = "\n".join([plan.to_sql(), *(join.to_sql() for join, _ in ranked)])
        return Sql(text, tuple(rank.scheme for _, rank in ranked))

    def ranked(self, column: str) -> Sql | None:
        """The position `column`'s value holds in its item scheme, or `None` for a column without one.

        An item the scheme does not list, or one with no position, ranks after
        every positioned item: `_UNRANKED` stands in for its position, so the
        rank is never null and the keyset comparison in `_after_sql` needs no
        null case for it.
        """
        entry = self._ranks.get(column)
        if entry is None:
            return None
        if self._rendered:
            msg = (
                f"the order on {column!r} was compiled after this statement's FROM clause was "
                f"rendered, so the join it needs had already been pruned. Build every other "
                f"clause before from_sql()."
            )
            raise PathError(msg)
        self._ranked.add(column)
        return Sql(f"coalesce({entry[0].alias}.position, {_UNRANKED})")

    def rank_of(self, column: str, value: object) -> Sql:
        """The position `value` holds in `column`'s item scheme, as `ranked` reads it.

        Uncorrelated, so DuckDB evaluates it once per statement: a cursor's
        values are fixed when the statement is built.
        """
        _, rank = self._ranks[column]
        return Sql(
            f"coalesce((SELECT position FROM {STRUCTURAL_SCHEMA}.{rank.table} "
            f"WHERE {rank.scheme_column} = ? AND {rank.item_column} = ?), {_UNRANKED})",
            (rank.scheme, value),
        )

    def _use(self, table: str, join: Join) -> str:
        """Mark a root join referenced and give back its alias.

        The one place `_used` is written, so that recording the reference
        cannot be forgotten at a call site — which is what makes the set
        complete rather than merely careful.
        """
        if self._rendered:
            # Not a wrong answer — the binder would refuse the unknown alias —
            # but this message names the cause, which `Binder Error: Referenced
            # table "s0_0" not found` does not.
            msg = (
                f"a column on {table!r} was compiled after this statement's "
                f"FROM clause was rendered, so the join it needs had already "
                f"been pruned. Build every other clause before from_sql()."
            )
            raise PathError(msg)
        self._used.add(table)
        return join.alias

    def _alias_of_join(self, table: str) -> str | None:
        """The alias of the root join on `table`, or `None` when there is none.

        `None` is the ordinary answer: a column of the root's own table, or of
        a hop, reaches its alias through `alias_of` instead.
        """
        join = self._root_joins.get(table)
        return None if join is None else self._use(table, join)

    @property
    def alias(self) -> str:
        """Alias of the relation this scope opens at.

        What the correlation anchors on, which is not necessarily where a
        predicate path starts — see `_tail`.
        """
        return self._plan.root_alias

    @property
    def star(self) -> str:
        """Every column of the row, for a query that projected nothing.

        `s0.*` and not `*`, because a path hop joins a *different* relation
        whose columns are not part of this row — a filter on `code.codelist`
        must not put the codelist's columns in the result.

        A **root** join is the opposite case: it carries columns that are part
        of the row and that no path reaches, so it belongs in the star. Its
        keys are excluded because `USING` merges them and the root's copy is
        already there — without `EXCLUDE` the frame would carry `FREQ` twice
        and polars would refuse it. `EXCLUDE` emptying the star is fine: a
        sidecar of nothing but keys contributes nothing, rather than erroring.
        """
        joined = [
            f"{self._use(table, declared)}.* EXCLUDE ({', '.join(declared.using)})"
            for table, declared in self._root_joins.items()
        ]
        return ", ".join([f"{self.alias}.*", *joined])

    def declared_projection(self, projection: Sequence[tuple[str, str, str]]) -> str:
        """A root's own output projection, with each table resolved to its alias.

        The root knows which column carries which component and which table it
        lives on; only the scope knows what that table is called here. So the
        two halves meet at this method rather than either guessing.
        """
        return ", ".join(
            f"{joined if (joined := self._alias_of_join(table)) is not None else self.alias}.{column} AS {name}"
            for table, column, name in projection
        )

    def _rel(self, path: tuple[str, ...]) -> tuple[str, ...]:
        """A declared path, read relative to this scope."""
        return (*self._tail, *path[self._strip :])

    # ── path collection ───────────────────────────────────────────────────

    def _flat_paths(self, predicates: Sequence[AnyPred]) -> list[tuple[str, ...]]:
        """Every to-one route this scope must join.

        A path crossing a fan-out contributes only its prefix — the part before
        the to-many hop — because everything after belongs to a subquery.
        """
        found: set[tuple[str, ...]] = set()
        for pred in predicates:
            self._collect(pred, found)
        return sorted(found)

    def _collect(self, pred: AnyPred, found: set[tuple[str, ...]]) -> None:
        match pred:
            case BoolOp(parts=parts):
                for part in parts:
                    self._collect(part, found)
            case Not(part=part):
                self._collect(part, found)
            case Quantified(path=path):
                self._add_prefix(self._rel(tuple(path)), found)
            case AggCompare(agg=agg):
                # Only the prefix *before* the fan-out is joined; the child
                # side lives in the correlated subquery, exactly as it does for
                # a projected aggregate.
                self._add_columns(agg.columns, found)
            case Compare() | IsNull() | InSet() | Match() | ArrayHas() | InRelation():
                column: AnyCol = pred.column
                self._add_columns(_leaf_columns(column), found)
            case _:  # pragma: no cover - exhaustive over the Pred hierarchy
                return

    def _add_columns(self, columns: Iterable[AnyCol], found: set[tuple[str, ...]]) -> None:
        for column in columns:
            self._add_prefix(self._rel(tuple(column.path)), found)

    def _add_prefix(self, path: tuple[str, ...], found: set[tuple[str, ...]]) -> None:
        if not path:
            return
        split = _split_at_fan_out(resolve_path(self._table, path))
        prefix = path if split is None else path[: split[0]]
        if prefix:
            found.add(prefix)

    # ── projection ────────────────────────────────────────────────────────

    def projection(self, name: str, item: AnyCol | AnyAgg) -> Sql:
        """One `SELECT` item, aliased to `name`."""
        if isinstance(item, Win):
            ref = self.window(item)
            return Sql(f"{ref.text} AS {name}", ref.params)
        if isinstance(item, AnyAgg):
            return self._aggregate(name, item)
        path = self._rel(tuple(item.path))
        if not isinstance(item, Computed):
            refuse_to_many_projection(self._table, path, spelled(path))
        if isinstance(item, Computed):
            ref = self._flat_reference(item, context="in a computed column")
            return Sql(f"{ref.text} AS {name}", ref.params)
        ref = self.reference(item)
        return Sql(f"{ref.text} AS {name}", ref.params)

    def window(self, column: Win[RowKind]) -> Sql:
        """Qualify a window expression's columns against this scope's plan."""
        refs = [self._flat_reference(part) for part in (*column.source, *column.partition)]
        order_ref = self._sort_reference(column.source[0]) if column.source else None
        return _window(column, refs, order_ref)

    def qualify(self, pred: AnyWinPred) -> Sql:
        """One `QUALIFY` term — a test on a windowed value."""
        if not isinstance(pred, WinCompare):  # pragma: no cover - the only kind
            msg = f"cannot compile {type(pred).__name__}"
            raise PathError(msg)
        ref = self.window(pred.column)
        return Sql(f"{ref.text} {pred.op} ?", (*ref.params, pred.value))

    def reference(self, column: AnyCol, *, alias: str | None = None) -> Sql:
        """Render a column against this scope, computed or not.

        The single place a column becomes SQL. A `Computed` node is rendered by
        recursion, because its operands may sit on *different* relations and so
        need different aliases — which is why this lives on the scope, holding
        the join plan, rather than on the free `_reference` that takes one.

        `alias` overrides the lookup, for a correlated subquery whose plan is
        not this one.

        A `Win` is rendered as its window, not as the column underneath it.
        Missing that branch made every windowed value inside a computed
        expression compile to its own source column: `depth / depth.sum().over(…)`
        became `(s0.depth / s0.depth)`, so a share was silently `1` for every
        row. Handled here rather than in `_computed`, because ordering by a
        window reaches this the same way.
        """
        if isinstance(column, Win):
            return self.window(column)
        if isinstance(column, Computed):
            return self._computed(column, alias=alias)
        if alias is None and (declared := self._alias_of_join(column.table)):
            # A column on a root-declared join. `USING` merges only the join
            # *keys*, so a non-key sidecar column read through the root alias
            # is a binder error rather than a wrong answer — which is why the
            # column carries its table at all.
            return _reference(column, declared)
        at = alias if alias is not None else self._plan.alias_of[self._rel(tuple(column.path))]
        return _reference(column, at)

    def _computed(self, column: Computed, *, alias: str | None) -> Sql:
        parts = [
            self.reference(operand, alias=alias) if isinstance(operand, AnyCol) else Sql("?", (operand,))
            for operand in column.operands
        ]
        params = tuple(itertools.chain.from_iterable(p.params for p in parts))
        if column.op.isalpha():
            return Sql(f"{column.op}({', '.join(p.text for p in parts)})", params)
        return Sql(f"({f' {column.op} '.join(p.text for p in parts)})", params)

    def _flat_reference(self, column: AnyCol, *, context: str = "in a window") -> Sql:
        """Render a column, refusing one that fans out."""
        for part in _leaf_columns(column):
            path = self._rel(tuple(part.path))
            if _split_at_fan_out(resolve_path(self._table, path)) is not None:
                route = spelled(path)
                msg = (
                    f"cannot use {route!r} {context}: it crosses a to-many "
                    f"edge, so it has no single value per row. Collect it "
                    f"first, or root the query at the child."
                )
                raise PathError(msg)
        return self.reference(column)

    def _sort_reference(self, column: AnyCol, *, context: str = "in a window") -> Sql:
        """Render a column as its *sort* key, refusing one that fans out.

        Identical to `_flat_reference` except for the physical column: see
        `AnyCol.sort_column`, which differs only for `version`.
        """
        flat = self._flat_reference(column, context=context)
        if column.sort_column == column.column:
            return flat
        at = self._plan.alias_of[self._rel(tuple(column.path))]
        return Sql(f"{at}.{column.sort_column}")

    def order(self, key: OrderKey) -> Sql:
        """One `ORDER BY` term.

        `NULLS LAST` is emitted rather than left to the default. DuckDB's
        `default_null_order` happens to be `NULLS_LAST` already, but it is a
        *session setting* — and the backends this compiler is meant to grow
        (§4) do not agree with each other. Saying it costs nothing and removes
        a way for the same query to sort differently on two connections.
        """
        path = self._rel(tuple(key.column.path))
        split = _split_at_fan_out(resolve_path(self._table, path))
        if split is not None:
            route = spelled(path)
            msg = (
                f"cannot order by {route!r}: it crosses a to-many edge, so it "
                f"has no single value per row to sort on. Order by a column of "
                f"the row itself, or root the query at the child."
            )
            raise PathError(msg)
        # The *sort* reference: `version` orders by its baked key, because
        # `1.9` sorts after `1.10` as text (#311). Must agree with
        # `_order_terms`, which builds the keyset comparison — a cursor
        # carrying `version` while `ORDER BY` read `version_sort` would skip
        # rows, silently, and only on catalogues that reach double digits.
        ref = self._sort_reference(key.column, context="in an order")
        return Sql(f"{ref.text}{'' if key.ascending else ' DESC'} NULLS LAST", ref.params)

    def grouped(self, name: str, item: AnyCol | AnyAgg) -> Sql:
        """One `SELECT` item of a grouped query.

        An `Agg` here is a *plain* SQL aggregate over the rows of its group —
        `count(*)`, `list(x)` — not the correlated subquery the same `Agg`
        means in an ungrouped projection. The expression says what to compute;
        the clause says over what. That is the split `.over()` already makes
        for windows, applied a third time.
        """
        if isinstance(item, AnyAgg):
            body = self._grouped_agg_body(item, context="in a grouped aggregate")
            return Sql(f"{body.text} AS {name}", body.params)
        ref = self._flat_reference(item, context="as a group key")
        return Sql(f"{ref.text} AS {name}", ref.params)

    def _grouped_agg_body(self, agg: AnyAgg, *, context: str) -> Sql:
        """A plain aggregate over the group's rows, honouring the column's `.sort()`.

        The order was dropped here until #414: `col.sort().unique()` compiled to
        `list(DISTINCT col ORDER BY col)` through a fan-out but to an unordered
        `list(DISTINCT col)` in a `group_by().agg()` — the caller asked for an
        order and silently did not get one. Read off the column exactly as
        `_agg_subquery` reads it, so the two readings of an `Agg` agree.
        """
        refs = [self._flat_reference(column, context=context) for column in agg.columns]
        order = agg.columns[0]._order if agg.columns else None
        order_sql: Sql | None = None
        if order is not None:
            key_ref = self._flat_reference(order[0], context=context)
            order_sql = Sql(f"{key_ref.text}{' DESC' if order[1] else ''}", key_ref.params)
        sort_key = None
        if agg.columns and agg.columns[0].sort_column != agg.columns[0].column:
            sort_key = self._sort_reference(agg.columns[0], context=context)
        # `where=` here restricts which of the group's own rows aggregate, so it
        # compiles in this scope, as SQL's `FILTER (WHERE …)`. It was dropped
        # until this was found writing `llms-dsl.txt`: `count(where=…)` over a
        # root column compiled to a bare `count(*)`, the whole group, silently.
        rows = _conjoin([self.compile(predicate) for predicate in agg.where]) if agg.where else None
        return self._agg_body_from(agg, refs, order_sql, sort_key=sort_key, rows=rows)

    def grouped_having(self, pred: AggPred) -> Sql:
        """One `HAVING` term — an aggregate compared to a value.

        The aggregate is built by `_agg_body_from`, exactly as `grouped()`
        builds it for the `SELECT` list, so a `HAVING` can never describe a
        different computation from the column it filters on.

        Note this is the *grouped* reading of an `Agg` — a plain SQL aggregate
        over the group's rows. The same `AggCompare` in a root-grain `filter`
        means the correlated-subquery reading instead, which is why the two
        clauses compile it through different methods rather than sharing one.
        """
        body = self._grouped_agg_body(pred.agg, context="in a HAVING clause")
        return Sql(f"{body.text} {pred.op} ?", (*body.params, pred.value))

    def _aggregate(self, name: str, agg: AnyAgg) -> Sql:
        """A projected aggregate — the correlated subquery, aliased."""
        body = self._agg_subquery(name, agg)
        return Sql(f"{body.text} AS {name}", body.params)

    def _agg_subquery(self, name: str, agg: AnyAgg) -> Sql:
        """A correlated subquery collecting across one fan-out.

        Row-preserving by construction: the outer query is untouched, which is
        why several aggregates in one projection cannot multiply anything the
        way several joins would — and why the same subquery is safe to drop
        into a `WHERE`, which is what `count() == 0` does. A `GROUP BY … HAVING`
        would not be: it would collapse the very rows being filtered.
        """
        order = agg.columns[0]._order if agg.columns else None
        # The key is resolved with the aggregate's own columns so it is held to
        # the same rule: one aggregate spans one fan-out, and a key from
        # outside it has no value per collected row.
        ordered_columns = (*agg.columns, order[0]) if order is not None else agg.columns
        paths = [self._rel(tuple(c.path)) for c in ordered_columns]
        resolved: list[tuple[int, Hop]] = []
        for path in paths:
            split = _split_at_fan_out(resolve_path(self._table, path))
            if split is None:
                msg = (
                    f"{name!r} aggregates a path that never fans out, so there "
                    f"is nothing to aggregate over. Project the column directly."
                )
                raise PathError(msg)
            resolved.append(split)

        index, hop = resolved[0]
        edge = paths[0][: index + 1]
        if any(p[: i + 1] != edge for (i, _), p in zip(resolved, paths, strict=True)):
            msg = f"{name!r} collects columns from different fan-outs. One aggregate spans one edge; use one per edge."
            raise PathError(msg)

        outer_alias = self._plan.alias_of[paths[0][:index]]
        remainders = [p[index + 1 :] for p in paths]
        # `agg.where` predicates are child-rooted, so they go in as this
        # scope's own predicates — the same shape `_exists` uses for
        # `.any(...)`, which is what keeps a restriction inside an aggregate
        # compiling like a restriction anywhere else, hops and nested
        # fan-outs included (#373).
        inner = _Scope(hop.table, agg.where, self._names, strip=0, extra_paths=remainders)
        correlation = Sql(hop.condition(outer=outer_alias, inner=inner.alias))
        facet = _facet_clause(hop, inner.alias)
        # Built only when there is one: `_conjoin([])` is `TRUE`, and folding
        # that in would add `AND TRUE` to every unrestricted aggregate — a
        # diff in the SQL of queries this change is not supposed to touch.
        restriction = [inner.compile(predicate) for predicate in agg.where]
        where = _conjoin([correlation, facet, *restriction])
        order_sql: Sql | None = None
        if order is not None:
            key_ref = inner.reference(order[0], alias=inner.plan.alias_of[remainders[-1]])
            direction = " DESC" if order[1] else ""
            order_sql = Sql(f"{key_ref.text}{direction}", key_ref.params)
        body = self._agg_body(agg, inner, remainders[: len(agg.columns)], order_sql)
        source = inner.from_sql()
        return Sql(
            f"(SELECT {body.text} FROM {source.text} WHERE {where.text})",
            body.params + source.params + where.params,
        )

    def _agg_body(self, agg: AnyAgg, inner: _Scope, remainders: list[tuple[str, ...]], order: Sql | None = None) -> Sql:
        """The aggregate expression and the binds its references need.

        Goes through `_reference` rather than qualifying by hand, so a
        localised label inside an aggregate is subscripted by language like
        anywhere else — collecting the whole `MAP` instead is the bug this
        fixes, and it was invisible until a label was projected through a
        fan-out.
        """
        parts = [
            inner.reference(col, alias=inner.plan.alias_of[rest])
            for rest, col in zip(remainders, agg.columns, strict=True)
        ]
        # The sort key on the same alias as the value, resolved in this scope.
        # It was resolved from the query root, so `version` through a fan-out
        # read the path as crossing that fan-out and refused (#324).
        sort_key = None
        if agg.columns and agg.columns[0].sort_column != agg.columns[0].column:
            sort_key = Sql(f"{inner.plan.alias_of[remainders[0]]}.{agg.columns[0].sort_column}")
        return self._agg_body_from(agg, parts, order, sort_key=sort_key)

    def _agg_body_from(
        self,
        agg: AnyAgg,
        parts: Sequence[Sql],
        order: Sql | None = None,
        *,
        sort_key: Sql | None = None,
        rows: Sql | None = None,
    ) -> Sql:
        """The aggregate expression itself, over already-qualified references.

        Shared by the correlated form and the grouped one, so `list` of several
        columns produces the same struct in both and `n_unique` cannot mean
        two different things depending on which clause reached it.

        `sort_key` is the first column's sort key, when it sorts on something
        other than itself (`version`), qualified by the caller as `parts` are.
        Only the caller knows the scope it is in.

        `rows` restricts which rows each aggregate call reads — the grouped
        reading of `where=`, as `FILTER (WHERE …)`. The correlated reading
        puts it in its subquery's `WHERE` instead, and passes none.
        """
        refs = [p.text for p in parts]
        params = tuple(itertools.chain.from_iterable(p.params for p in parts))
        flt = "" if rows is None else f" FILTER (WHERE {rows.text})"
        fparams = () if rows is None else rows.params
        match agg.kind:
            case "count":
                return Sql(f"count(*){flt}", fparams)
            case "n_unique":
                return Sql(_n_unique_sql(refs[0], flt), (*params, *fparams) * 2)
            case "min" | "max" if sort_key is not None:
                # A column that sorts on something other than itself must
                # aggregate on it too, or `order_by` and `max()` disagree about
                # which value is largest — which is exactly what they did for
                # `version`, where `max()` answered `1.9` over `1.10` while
                # `order_by(version.desc())` correctly answered `1.10` (#351).
                #
                # `arg_max(value, key)` rather than `max(key)`: the caller asked
                # for the version, not its sort key, so the answer has to come
                # back in the column's own terms. That is also why this cannot
                # be done by swapping the reference.
                return Sql(f"arg_{agg.kind}({refs[0]}, {sort_key.text}){flt}", (*params, *sort_key.params, *fparams))
            case "sum" | "mean" | "min" | "max":
                # `mean` is polars' name and `avg` is SQL's, the same split
                # `n_unique`/`count(DISTINCT …)` already crosses.
                function = "avg" if agg.kind == "mean" else agg.kind
                return Sql(f"{function}({refs[0]}){flt}", (*params, *fparams))
            case "list" | "unique" if len(refs) == 1:
                distinct = "DISTINCT " if agg.kind == "unique" else ""
                clause, order_params = _order_clause(agg, order, refs[0])
                return Sql(
                    _empty_list_as_empty(f"list({distinct}{refs[0]}{clause}){flt}"), (*params, *order_params, *fparams)
                )
            case "list" | "unique":
                fields = ", ".join(f"'{_field_name(col)}': {ref}" for col, ref in zip(agg.columns, refs, strict=True))
                distinct = "DISTINCT " if agg.kind == "unique" else ""
                clause, order_params = _order_clause(agg, order, f"{{{fields}}}")
                return Sql(
                    _empty_list_as_empty(f"list({distinct}{{{fields}}}{clause}){flt}"),
                    (*params, *order_params, *fparams),
                )
            case _:  # pragma: no cover - closed set
                msg = f"unknown aggregate {agg.kind!r}"
                raise PathError(msg)

    # ── compilation ───────────────────────────────────────────────────────

    def compile(self, pred: AnyPred) -> Sql:
        match pred:
            case BoolOp(op=op, parts=parts):
                joiner = " AND " if op == "and" else " OR "
                compiled = [self.compile(p) for p in parts]
                text = joiner.join(c.text for c in compiled)
                params = tuple(itertools.chain.from_iterable(c.params for c in compiled))
                return Sql(f"({text})", params)
            case Not(part=InRelation() as relation):
                # `NOT (x IN (subquery))` is UNKNOWN for *every* row as soon as
                # the subquery yields one NULL, so the anti-join silently
                # returns nothing (#350). `NOT EXISTS` is the null-safe form —
                # the same one `_row_in` already uses, which is why
                # `Query.difference()` was right on data this was wrong on.
                return self._anti_join(relation)
            case Not(part=part):
                inner = self.compile(part)
                return Sql(f"NOT ({inner.text})", inner.params)
            case AggCompare(agg=agg, op=op, value=value):
                body = self._agg_subquery("agg", agg)
                return Sql(f"{body.text} {op} ?", (*body.params, value))
            case RowIn():
                return self._row_in(pred)
            case Quantified():
                return self._quantified(pred)
            case Compare() | IsNull() | InSet() | Match() | ArrayHas() | InRelation():
                # `InRelation` goes through `_leaf` like every other leaf, and
                # did not: it called `reference()` directly, which assumes the
                # column is flat. A sub-query `is_in` over a path crossing a
                # fan-out — `artefact.described_by.metadata_set.urn.is_in(...)`
                # — then failed in the binder with a `KeyError` on the prefix
                # the plan never registered, because `_add_prefix` stops at the
                # fan-out and leaves the correlation to `_exists`.
                return self._leaf(pred)
            case _:  # pragma: no cover
                msg = f"cannot compile {type(pred).__name__}"
                raise PathError(msg)

    def _anti_join(self, pred: InRelation[RowKind]) -> Sql:
        """`column NOT IN (subquery)`, spelled so a NULL cannot swallow the row.

        `x NOT IN (SELECT c ...)` is SQL's oldest trap: one NULL in `c` makes
        the predicate UNKNOWN for every row, and the result is empty rather
        than wrong-by-a-few. That matters here more than it might elsewhere,
        because `is_in` over a subquery is the documented way to ask the
        questions the edge graph only exposes in one direction — "codelists
        nothing uses" — and those run against nullable foreign keys by nature.

        Correlated on the *reference*, not on a key: unlike `_row_in` the left
        side is one column and may sit behind a join, so it is whatever
        `reference()` resolved it to.
        """
        ref = self.reference(pred.column)
        alias = f"{self._plan.root_alias}_anti"
        # `AS alias(value)` renames the subquery's single output column, so this
        # does not have to know or parse what the relation called it.
        #
        # The `IS NOT NULL` is on the *left* side, not inside the subquery. A
        # NULL in the subquery is already handled — it simply fails to match, so
        # NOT EXISTS holds, which is the fix. A NULL on the left is the separate
        # question, and the answer is the one the cookbook already documents for
        # columns: a row whose value is NULL satisfies neither `p` nor `~p`.
        # Without this line `~is_in` would start returning rows that `is_in`
        # also excludes, and the two would no longer partition.
        return Sql(
            f"({ref.text} IS NOT NULL AND NOT EXISTS "
            f"(SELECT 1 FROM ({pred.sql}) AS {alias}(value) WHERE {alias}.value = {ref.text}))",
            (*ref.params, *pred.params, *ref.params),
        )

    def _row_in(self, pred: RowIn[RowKind]) -> Sql:
        """Semi- or anti-join against an already-compiled query.

        The subquery arrives compiled — it is a whole `Query`, with its own
        predicates, order and slice — so nothing here plans it; this only
        correlates the root's key columns onto it.
        """
        alias = f"{self._plan.root_alias}_set"
        on = " AND ".join(f"{alias}.{column} = {self._plan.root_alias}.{column}" for column in pred.columns)
        keyword = "NOT EXISTS" if pred.negate else "EXISTS"
        return Sql(f"{keyword} (SELECT 1 FROM ({pred.sql}) {alias} WHERE {on})", pred.params)

    def _leaf(self, pred: _Leaf) -> Sql:
        column: AnyCol = pred.column
        path = self._rel(tuple(column.path))
        split = _split_at_fan_out(resolve_path(self._table, path))
        if split is None:
            ref = self.reference(column)
            return _operator(pred, ref.text, ref.params)
        # Dotting through a fan-out means *some* child, which is the same
        # correlated EXISTS `.any()` builds — with one part rather than several.
        index, hop = split
        return self._exists(
            prefix=path[:index],
            hop=hop,
            tail=path[index + 1 :],
            leaves=[pred],
            negate=False,
            strip=len(tuple(column.path)),
        )

    def _quantified(self, pred: Quantified[RowKind, RowKind]) -> Sql:
        path = self._rel(tuple(pred.path))
        split = _split_at_fan_out(resolve_path(self._table, path))
        if split is None:
            msg = f"{'.'.join(path)!r} is not a to-many edge, so there is nothing to quantify over."
            raise PathError(msg)
        index, hop = split
        return self._exists(
            prefix=path[:index],
            hop=hop,
            tail=path[index + 1 :],
            leaves=list(pred.parts),
            negate=pred.quantifier in {"every", "none"},
            violations=pred.quantifier == "every",
            strip=0,
        )

    def _exists(
        self,
        *,
        prefix: tuple[str, ...],
        hop: Hop,
        tail: tuple[str, ...],
        leaves: Sequence[AnyPred],
        negate: bool,
        strip: int,
        violations: bool = False,
    ) -> Sql:
        """One correlated subquery over the children reached by `hop`.

        `negate` picks the keyword, `violations` picks what the subquery looks
        for. The two were one flag until `none()` needed them apart:

        | | keyword | subquery finds |
        | --- | --- | --- |
        | `any` | `EXISTS` | a child that satisfies |
        | `none` | `NOT EXISTS` | a child that satisfies |
        | `every` | `NOT EXISTS` | a child that *violates* |
        """
        outer_alias = self._plan.alias_of[prefix]
        collapsed = _member_collapse(
            hop=hop,
            tail=tail,
            leaves=leaves,
            negate=negate,
            violations=violations,
            strip=strip,
            outer_alias=outer_alias,
        )
        if collapsed is not None:
            return collapsed
        inner = _Scope(hop.table, leaves, self._names, strip=strip, tail=tail)
        correlation = Sql(hop.condition(outer=outer_alias, inner=inner.alias))
        facet = _facet_clause(hop, inner.alias)
        conditions = _conjoin([inner.compile(leaf) for leaf in leaves])

        if violations and conditions.text:
            # `IS NOT TRUE` rather than `NOT (...)`: a child whose column is
            # NULL must count as a violation, not be skipped. Without this a
            # non-enumerated dimension silently satisfies a universal — see
            # `docs/design/query-language-examples.md` §3.
            conditions = Sql(f"({conditions.text}) IS NOT TRUE", conditions.params)

        where = _conjoin([correlation, facet, conditions])
        keyword = "NOT EXISTS" if negate else "EXISTS"
        source = inner.from_sql()
        return Sql(
            f"{keyword} (SELECT 1 FROM {source.text} WHERE {where.text})",
            source.params + where.params,
        )


def _scope_clause(fetch_scope: str | None, alias: str, schema: str | None) -> Sql:
    """Restrict a root to the artefacts one fetch brought (§5.13, #340).

    Empty for every local query: a `LocalRegistry` has no fetch, so its rows
    *are* its answer and there is nothing to scope. Only a REST-backed query
    passes a key, and then the rows it may see are exactly those recorded
    under that key by `fetch_into`.

    A subquery rather than a join, so the shape does not change with the size
    of the fetch and the clause composes with the discriminator instead of
    rewriting the `FROM`.

    Schema-qualified from the root's own `__schema__` rather than left to the
    search path: `stream()` runs on a cursor of its own — one per reader, so
    two streams cannot overwrite each other's row buffer — and that cursor does
    not inherit the handle's `search_path`. An unqualified name worked
    everywhere except there, which is the shape of bug a terminal-by-terminal
    test finds and a single happy-path one does not.
    """
    if fetch_scope is None:
        return Sql("")
    table = f"{schema}.fetch_scope" if schema else "fetch_scope"
    return Sql(f"{alias}.urn IN (SELECT urn FROM {table} WHERE fetch_key = ?)", (fetch_scope,))


def _n_unique_sql(reference: str, row_filter: str = "") -> str:
    """`n_unique` counting a null as a value, the way polars does (#326).

    `count(DISTINCT x)` skips nulls and `list(DISTINCT x)` keeps them, so
    DuckDB's two functions disagree with each other — and taking one for
    `n_unique` and the other for `unique` inherited the disagreement:
    `len(unique())` and `n_unique()` differed by one on any nullable column,
    with nothing in either name to say why.

    Both names are polars', where a null *is* a value and the two agree
    always. `list()` already keeps nulls here too, so `n_unique` was the odd
    one out on both counts. CLAUDE.md's rule is to check a DSL decision
    against polars where there is a counterpart; this is the case where the
    name was taken and the semantics were not.

    `COALESCE` on both halves, which the obvious expression needs and an
    all-cases probe is what finds: `bool_or` over no rows is NULL, so an empty
    relation answered `NULL` rather than `0`. Verified against polars on
    empty, all-null, mixed and null-free columns.

    `row_filter` is a `FILTER (WHERE …)` clause, applied to both halves: they
    are two aggregate calls, and restricting one would count the other's rows.
    """
    return (
        f"COALESCE(count(DISTINCT {reference}){row_filter}, 0)"
        f" + COALESCE(CAST(bool_or({reference} IS NULL){row_filter} AS INT), 0)"
    )


def _constraint_clause(constraint: tuple[str, tuple[str, ...]] | None, alias: str) -> Sql:
    """The discriminator identifying a root's rows within its relation.

    Several artefact types share `sdmx.artefact`, so a query rooted at
    `q.codelist` without this would return every artefact. Same mechanism as a
    `Facet`, applied at the root.
    """
    if constraint is None:
        return Sql("")
    column, values = constraint
    if len(values) == 1:
        return Sql(f"{alias}.{column} = ?", (values[0],))
    marks = ", ".join("?" for _ in values)
    return Sql(f"{alias}.{column} IN ({marks})", tuple(values))


def _leaf_columns(column: AnyCol) -> tuple[AnyCol, ...]:
    """The table-backed columns a reference reads, flattening computed nodes.

    A `Win` contributes its **partition** columns as well as its source. Those
    are reached through nothing else, so without this a window nested inside a
    computed expression — `depth / depth.sum().over(codelist.id)` — planned no
    join for `codelist` and the render died on `KeyError: ('codelist',)`.
    `_columns_of` already did this for a *top-level* window, which is why the
    gap only showed up once a window could appear as an operand.
    """
    if isinstance(column, Win):
        return tuple(leaf for part in (*column.source, *column.partition) for leaf in _leaf_columns(part))
    if isinstance(column, Computed):
        return tuple(
            leaf for operand in column.operands if isinstance(operand, AnyCol) for leaf in _leaf_columns(operand)
        )
    return (column,)


def _columns_of(item: AnyCol | AnyAgg) -> tuple[AnyCol, ...]:
    """Every column an item references, window partitions included.

    A `Win` is a `Col`, so its own path covers the source; its partition
    columns are reached through nothing else and would otherwise never be
    joined.
    """
    if isinstance(item, Win):
        return tuple(leaf for part in (*item.source, *item.partition) for leaf in _leaf_columns(part))
    if isinstance(item, AnyAgg):
        return tuple(leaf for column in item.columns for leaf in _leaf_columns(column))
    return _leaf_columns(item)


def _prefixes(table: str, columns: Iterable[AnyCol], into: list[tuple[str, ...]]) -> None:
    """Append each column's pre-fan-out prefix, keeping the list unique."""
    for column in columns:
        path = tuple(column.path)
        split = _split_at_fan_out(resolve_path(table, path))
        prefix = path if split is None else path[: split[0]]
        if prefix and prefix not in into:
            into.append(prefix)


def _qualify_paths(table: str, qualify: Sequence[AnyWinPred]) -> list[tuple[str, ...]]:
    """Routes the outer scope must join for its `QUALIFY` terms."""
    found: list[tuple[str, ...]] = []
    for pred in qualify:
        if isinstance(pred, WinCompare):
            _prefixes(table, _columns_of(pred.column), found)
    return found


def _grouped_aggregates(items: Sequence[tuple[str, AnyCol | AnyAgg]], having: Sequence[AggPred]) -> list[AnyAgg]:
    """Every aggregate a grouped `SELECT` computes, in its list and its `HAVING`."""
    return [*(item for _, item in items if isinstance(item, AnyAgg)), *(pred.agg for pred in having)]


def _projected_prefixes(table: str, items: Sequence[tuple[str, AnyCol | AnyAgg]]) -> list[tuple[str, ...]]:
    """Every to-one route the outer scope must join for a projection.

    A flat column contributes its whole path; one that fans out contributes the
    part *before* the to-many hop, because that is where its correlated
    subquery anchors. Same rule `_Scope._add_prefix` applies to predicates —
    stated separately only because projections are collected before the scope
    that would apply it exists.

    Aggregates count too. Reading only the flat columns was enough while every
    fan-out was the first hop, since an empty prefix is the root alias and
    needs no join; `q.dataflow.dsd.dimensions.id.list()` anchors two hops out
    and raised `KeyError` instead.
    """
    found: list[tuple[str, ...]] = []
    for _, item in items:
        _prefixes(table, _columns_of(item), found)
    return found


def _order_sql(
    root: AnyCols,
    scope: _Scope,
    order: Sequence[OrderKey] | None,
    *,
    tiebreak: Sequence[str] | None = None,
) -> Sql:
    """The `ORDER BY` clause, caller keys first and the tiebreak last.

    `None` means emit nothing — for `count(*)` and `EXISTS`, which sort no rows
    and where a key naming a column outside the aggregate is a binder error.
    An empty sequence still emits the tiebreak, so an unsorted query is
    *stable* rather than merely unspecified: `limit`/`offset` paging over a
    scan order that can change between calls silently skips and repeats rows.

    `tiebreak` overrides what that last part is. `SELECT DISTINCT` passes the
    projection's *ordinal positions*, because the root key is no longer in the
    result and SQL will not sort by a column the projection dropped — while
    leaving a distinct result in scan order would reintroduce exactly the
    instability the default tiebreak exists to remove. Ordinals need no binds
    and cannot name something unprojected.
    """
    if order is None:
        return Sql("")
    parts = [scope.order(key) for key in order]
    keys = list(tiebreak) if tiebreak is not None else _key_order(root, scope, order)
    text = ", ".join([*(p.text for p in parts), *keys])
    if not text:
        return Sql("")
    return Sql(f"\nORDER BY {text}", tuple(itertools.chain.from_iterable(p.params for p in parts)))


def _key_order(root: AnyCols, scope: _Scope, order: Sequence[OrderKey]) -> list[str]:
    """The root-key tiebreak's `ORDER BY` terms: each key column, after its rank when it has one.

    A dataflow's dimension enumerated by a codelist sorts by the code's
    position, then by its id (GH-213). Every other key column sorts as stored,
    as does one a caller's key already sorts by (`_ranked_key`).
    """
    terms: list[str] = []
    for column in root.__key__:
        rank = _ranked_key(scope, order, column)
        if rank is not None:
            terms.append(rank.text)
        terms.append(f"{scope.alias}.{column} NULLS LAST")
    return terms


def _ranked_key(scope: _Scope, order: Sequence[OrderKey], column: str) -> Sql | None:
    """`column`'s rank in the tiebreak, or `None` when it sorts as stored.

    A key column a caller's key already sorts by gets no rank: every tie the
    tiebreak breaks holds one value of it, so its rank decides nothing, and
    leaving it out leaves out its codelist's join. A partition stream ordered
    by geography otherwise builds `CL_GEOGRAPHY`'s 579,951 positions once per
    partition, 40 % of its time on the census bundle.
    """
    if any(key.column.column == column and not scope._rel(tuple(key.column.path)) for key in order):
        return None
    return scope.ranked(column)


class _SortTerm(NamedTuple):
    """One key of a query's total order, as the keyset comparison reads it."""

    reference: Sql
    ascending: bool
    #: The rank this term sorts by before its own value, and the key column it
    #: ranks — `None` for a term sorted as stored. See `_key_order`.
    rank: tuple[Sql, str] | None = None


def _order_terms(root: AnyCols, scope: _Scope, order: Sequence[OrderKey]) -> list[_SortTerm]:
    """Every `ORDER BY` term, tiebreak included, one per cursor value.

    The keyset half of the sort key: `after()` compares exactly the columns the
    order sorts by, and a cursor built from a different list would skip rows.
    `_Scope.order` builds the `ORDER BY` itself, and the two must use the same
    reference for each key — both go through `_sort_reference`, which is where
    `version` diverges (#311).
    """
    # `sort_reference`, not `reference`: `version` orders by its baked key
    # (#311). Applied *here* rather than at each call site because this is
    # documented as the one place that knows the full sort key — so `ORDER BY`
    # and the keyset `after()` comparison stay built from the same columns. A
    # cursor carrying `version` while the sort read `version_sort` would skip
    # rows, silently and only on catalogues that reach double digits.
    terms = [_SortTerm(scope._sort_reference(key.column), key.ascending) for key in order]
    for column in root.__key__:
        rank = _ranked_key(scope, order, column)
        terms.append(
            _SortTerm(Sql(f"{scope.alias}.{column}"), ascending=True, rank=None if rank is None else (rank, column))
        )
    return terms


def _after_sql(root: AnyCols, scope: _Scope, order: Sequence[OrderKey], cursor: AnyCursor) -> Sql:
    """`WHERE` narrowing to rows strictly after `cursor` in this query's order.

    Lexicographic, built inside out: *after* on the first key, or equal on it
    and after on the rest. Written as nested comparisons rather than SQL's row
    constructor because the directions can disagree — "newest version first,
    then id ascending" is the common shape and `(a, b) > (?, ?)` cannot say it.

    Nulls follow the `NULLS LAST` the order emits. A row whose key is null
    sorts after every non-null one, so it is *after* a non-null cursor value
    and tied with a null one; nothing sorts after a null, which is why a null
    cursor value contributes no strict term at all. Getting this wrong drops
    rows silently, which is the failure mode paging exists to avoid.
    """
    terms = _order_terms(root, scope, order)
    if len(terms) != len(cursor.values):
        msg = (
            f"this cursor has {len(cursor.values)} key(s) but the query orders "
            f"by {len(terms)}. A cursor is only meaningful against the order "
            f"that produced it — page with the same order_by()."
        )
        raise PathError(msg)
    if tuple(term.ascending for term in terms) != cursor.ascending:
        msg = (
            "this cursor was taken from a query with a different sort "
            "direction. A cursor is only meaningful against the order that "
            "produced it — page with the same order_by()."
        )
        raise PathError(msg)

    # A ranked term compares its rank first, then its value: the cursor
    # carries the value alone, and its rank is looked up from it.
    compared: list[tuple[Sql, bool, Sql | None]] = []
    for term, value in zip(terms, cursor.values, strict=True):
        if term.rank is not None:
            rank, column = term.rank
            compared.append((rank, term.ascending, scope.rank_of(column, value)))
        compared.append((term.reference, term.ascending, None if value is None else Sql("?", (value,))))

    clause = Sql("1 = 0")  # past the last row: nothing follows
    for reference, ascending, value in reversed(compared):
        equal = (
            Sql(f"{reference.text} IS NULL", reference.params)
            if value is None
            else Sql(f"{reference.text} IS NOT DISTINCT FROM {value.text}", (*reference.params, *value.params))
        )
        strictly = (
            Sql("1 = 0")
            if value is None
            else Sql(
                f"({reference.text} {'>' if ascending else '<'} {value.text} OR {reference.text} IS NULL)",
                (*reference.params, *value.params, *reference.params),
            )
        )
        rest = strictly if clause.text == "1 = 0" else _disjoin(strictly, _conjoin([equal, clause]))
        clause = rest
    return clause


def _disjoin(left: Sql, right: Sql) -> Sql:
    if left.text == "1 = 0":
        return right
    if right.text == "1 = 0":
        return left
    return Sql(f"({left.text} OR {right.text})", left.params + right.params)


def _order_paths(table: str, order: Sequence[OrderKey] | None) -> list[tuple[str, ...]]:
    """Routes the scope must join for its sort keys.

    A key that fans out is refused later, by `_Scope.order`, with a message;
    here it just contributes its prefix like anything else.
    """
    found: list[tuple[str, ...]] = []
    for key in order or ():
        path = tuple(key.column.path)
        split = _split_at_fan_out(resolve_path(table, path))
        prefix = path if split is None else path[: split[0]]
        if prefix and prefix not in found:
            found.append(prefix)
    return found


def compile_projection(
    root: AnyCols,
    predicates: Sequence[AnyPred],
    items: Sequence[tuple[str, AnyCol | AnyAgg]],
    *,
    order: Sequence[OrderKey] | None = (),
    distinct: bool = False,
    qualify: Sequence[AnyWinPred] = (),
    group_keys: Sequence[AnyCol] = (),
    aggregated: bool = False,
    having: Sequence[AggPred] = (),
    positional_order: Sequence[tuple[int, bool]] = (),
    fetch_scope: str | None = None,
) -> CompiledQuery:
    """Compile a projected `SELECT` — `select()` rather than the whole row.

    `positional_order` is `Select.order_by`, given as 1-based positions in the
    projection. Ordinals rather than expressions for the reason `DISTINCT` and
    `GROUP BY` already use them here: the key is in the `SELECT` list, and
    naming it twice would re-derive a localised label's subquery and its binds.
    """
    table = root.__table__
    extra = [
        *_projected_prefixes(table, items),
        *_order_paths(table, order),
        *_qualify_paths(table, qualify),
    ]
    # No separate pass for `group_keys`: `Grouped` always puts them at the
    # front of `items`, so `_projected_prefixes` has already joined them.
    # Measured — adding one was a redundant call no mutation could kill.
    #
    # `aggregated` rather than `group_keys` decides how an item compiles (#414):
    # a root-level `Query.agg` aggregates over every row with no keys at all,
    # and keying this on the keys sent it down the per-row projection path,
    # which correlates each aggregate over a fan-out and refuses at the root.
    # The keys still decide `GROUP BY` and the tiebreak below.
    grouped = aggregated or bool(group_keys)
    # A grouped aggregate's `where=` is over the group's own rows, so it is
    # this scope's to join for, like a predicate. Per row, an aggregate's
    # `where=` is the child's, and its correlated subquery joins for it.
    restrictions = [p for agg in _grouped_aggregates(items, having) for p in agg.where] if grouped else []
    scope = _Scope(
        table,
        [*predicates, *restrictions],
        _Namespace(),
        extra_paths=extra,
        schema=root.__schema__,
        joins=root.__joins__,
        ranks=root.__key_ranks__,
    )
    projected = [scope.grouped(name, item) if grouped else scope.projection(name, item) for name, item in items]
    constraint = _constraint_clause(root.__constraint__, scope.alias)
    where = _conjoin(
        [constraint, _scope_clause(fetch_scope, scope.alias, root.__schema__), *(scope.compile(p) for p in predicates)]
    )
    select = ", ".join(p.text for p in projected)
    params = tuple(itertools.chain.from_iterable(p.params for p in projected))
    # Clauses are collected rather than appended to a growing statement, so the
    # `FROM` can be rendered after all of them — see `_Scope.from_sql`.
    clauses: list[str] = []
    if where.text != "TRUE":
        clauses.append(f"\nWHERE {where.text}")
    windowed = _qualify_sql(scope, qualify)
    clauses.append(windowed.text)
    if group_keys:
        # By ordinal, like the `DISTINCT` tiebreak and for the same reason: the
        # keys are already in the SELECT list, and naming them twice would mean
        # re-deriving a localised label's subquery and its binds.
        clauses.append("\nGROUP BY " + ", ".join(str(index) for index in range(1, len(group_keys) + 1)))
    having_params: tuple[object, ...] = ()
    if group_keys and having:
        # By expression, not by ordinal. `GROUP BY 1` is standard and
        # `HAVING 1 > 2` is not — an ordinal there is read as the literal.
        # The aggregate is compiled the same way `grouped()` compiles it for
        # the SELECT list, so the two cannot drift.
        #
        # Binds are kept apart from the projection's and spliced in below in
        # clause order, because DuckDB matches `?` positionally: appending them
        # here would place them before the WHERE's and bind every value to the
        # wrong parameter.
        compiled_having = [scope.grouped_having(p) for p in having]
        clauses.append("\nHAVING " + " AND ".join(c.text for c in compiled_having))
        having_params = tuple(itertools.chain.from_iterable(c.params for c in compiled_having))
    tiebreak = None
    if group_keys:
        # A grouped result has no root key left to tie-break on, so the keys
        # themselves order it. Without this, groups come back in whatever order
        # the hash aggregate produced — which is not stable between runs.
        tiebreak = [f"{index} NULLS LAST" for index in range(1, len(group_keys) + 1)]
    if distinct:
        _check_distinct_order(scope, items, order)
        tiebreak = [
            f"{position} NULLS LAST" for position, (_, item) in enumerate(items, start=1) if not isinstance(item, Agg)
        ]
    if positional_order:
        # A projection's own order wins outright, and the row-level `order`
        # would name columns the projection may have dropped.
        keys = [f"{position} {'ASC' if ascending else 'DESC'} NULLS LAST" for position, ascending in positional_order]
        ordered = Sql("\nORDER BY " + ", ".join([*keys, *(tiebreak or [])]))
    elif aggregated and not group_keys:
        # One row, so nothing to order — and the root-key fallback below would
        # name row columns that are no longer in an aggregate's scope, which
        # DuckDB rejects with a GROUP BY binder error.
        ordered = Sql("")
    else:
        ordered = _order_sql(root, scope, order, tiebreak=tiebreak)
    # `from_sql` last: it emits only the root joins something above referenced.
    source = scope.from_sql()
    sql = f"SELECT {'DISTINCT ' if distinct else ''}{select} FROM {source.text}"
    return CompiledQuery(
        sql + "".join(clauses) + ordered.text,
        list(params + source.params + where.params + windowed.params + having_params + ordered.params),
    )


def _qualify_sql(scope: _Scope, qualify: Sequence[AnyWinPred]) -> Sql:
    """The `QUALIFY` clause.

    SQL's own name for a post-window filter, and DuckDB supports it natively.
    A backend without it needs the subquery wrapper —
    `SELECT * FROM (SELECT *, rank() OVER (...) rn ...) WHERE rn = 1` — which
    is the one compiler obligation §4 of the design leaves open; see #306.
    """
    if not qualify:
        return Sql("")
    parts = [scope.qualify(pred) for pred in qualify]
    text = " AND ".join(p.text for p in parts)
    return Sql(f"\nQUALIFY {text}", tuple(itertools.chain.from_iterable(p.params for p in parts)))


def _check_distinct_order(
    scope: _Scope,
    items: Sequence[tuple[str, AnyCol | AnyAgg]],
    order: Sequence[OrderKey] | None,
) -> None:
    """`SELECT DISTINCT` may only be ordered by something it projects.

    SQL's own rule, and a real one rather than pedantry: after the duplicates
    collapse, a column the projection dropped has several values per surviving
    row and no defined one to sort on. Raised here with the fix named, rather
    than left to the backend's message.
    """
    projected = {scope.projection(name, item).text.rsplit(" AS ", 1)[0] for name, item in items}
    for key in order or ():
        reference = scope.order(key).text.removesuffix(" NULLS LAST").removesuffix(" DESC")
        if reference not in projected:
            route = ".".join((*key.column.path, key.column.column))
            msg = (
                f"cannot order a unique() projection by {route!r}: it is not "
                f"among the projected columns, and after the duplicates "
                f"collapse there is no single value of it left to sort on. "
                f"Project it too, or drop it from order_by()."
            )
            raise PathError(msg)


def compile_page(
    root: AnyCols,
    predicates: Sequence[AnyPred],
    *,
    urn_column: str,
    order: Sequence[OrderKey] = (),
    qualify: Sequence[AnyWinPred] = (),
    after: AnyCursor | None = None,
    limit: int,
    fetch_scope: str | None = None,
) -> tuple[CompiledQuery, tuple[bool, ...]]:
    """A page of URNs, with every sort key alongside so a cursor can be built.

    Its own entry point rather than a flag on `compile_select`, because a page
    always selects the order columns and always applies a limit — a caller
    cannot ask for one without the other and get anything meaningful.

    Returns the statement and the direction of each key, which the cursor
    carries so `after()` can refuse one taken from a differently sorted query.
    """
    table = root.__table__
    extra = [*_order_paths(table, order), *_qualify_paths(table, qualify)]
    scope = _Scope(
        table,
        predicates,
        _Namespace(),
        extra_paths=extra,
        schema=root.__schema__,
        joins=root.__joins__,
        ranks=root.__key_ranks__,
    )
    terms = _order_terms(root, scope, order)
    constraint = _constraint_clause(root.__constraint__, scope.alias)
    keyset = _after_sql(root, scope, order, after) if after is not None else Sql("")
    scoped = _scope_clause(fetch_scope, scope.alias, root.__schema__)
    where = _conjoin([constraint, scoped, keyset, *(scope.compile(p) for p in predicates)])

    selected = ", ".join([f"{scope.alias}.{urn_column}", *(term.reference.text for term in terms)])
    params = tuple(itertools.chain.from_iterable(term.reference.params for term in terms))
    windowed = _qualify_sql(scope, qualify)
    ordered = _order_sql(root, scope, order)
    # `from_sql` last: it emits only the root joins something above referenced.
    where_sql = f"\nWHERE {where.text}" if where.text != "TRUE" else ""
    source = scope.from_sql()
    sql = f"SELECT {selected} FROM {source.text}{where_sql}"
    compiled = CompiledQuery(
        f"{sql}{windowed.text}{ordered.text}\nLIMIT {limit}",
        list(params + source.params + where.params + windowed.params + ordered.params),
    )
    return compiled, tuple(term.ascending for term in terms)


def compile_select(
    root: AnyCols,
    predicates: Sequence[AnyPred] = (),
    *,
    columns: str | None = None,
    order: Sequence[OrderKey] | None = None,
    qualify: Sequence[AnyWinPred] = (),
    after: AnyCursor | None = None,
    fetch_scope: str | None = None,
) -> CompiledQuery:
    """Compile a `SELECT` over the relation `root` names, restricted by `predicates`.

    Takes the accessor rather than a table name so the root's discriminator
    travels with it — `q.codelist` and `q.dsd` are the same relation and differ
    only by that constraint.
    """
    table = root.__table__
    extra = [*_order_paths(table, order), *_qualify_paths(table, qualify)]
    scope = _Scope(
        table,
        predicates,
        _Namespace(),
        extra_paths=extra,
        schema=root.__schema__,
        joins=root.__joins__,
        ranks=root.__key_ranks__,
    )
    constraint = _constraint_clause(root.__constraint__, scope.alias)
    keyset = _after_sql(root, scope, order or (), after) if after is not None else Sql("")
    scoped = _scope_clause(fetch_scope, scope.alias, root.__schema__)
    where = _conjoin([constraint, scoped, keyset, *(scope.compile(p) for p in predicates)])
    if columns is None:
        columns = scope.declared_projection(root.__projection__) if root.__projection__ else scope.star
    windowed = _qualify_sql(scope, qualify)
    ordered = _order_sql(root, scope, order)
    # `from_sql` last: it emits only the root joins something above referenced.
    where_sql = f"\nWHERE {where.text}" if where.text != "TRUE" else ""
    source = scope.from_sql()
    sql = f"SELECT {columns} FROM {source.text}{where_sql}"
    return CompiledQuery(
        sql + windowed.text + ordered.text,
        list(source.params + where.params + windowed.params + ordered.params),
    )
