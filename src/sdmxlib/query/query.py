"""`Query[R]` — a lazy, composable query over one relation.

Strictly lazy: nothing executes until a terminal is called. That is not
tidiness, it is the precondition for pushdown — a planner cannot push what has
already been evaluated, and every implicit-execution point is somewhere
`explain()` stops describing what will actually happen. So there is no
`__iter__`, no `__len__` and no `__contains__`; the operations they stood for
are named terminals, and two of them (`count`, `exists`) are *faster* than what
they replace because SQL can answer without materialising rows.

Composition follows `query-composition.md`'s refinement principle — every
clause narrows, and nothing a later caller adds can undo what an earlier call
established. The window helpers live in `_common` (`narrow_limit`,
`advance_window`), so the laws have one implementation and cannot drift.
"""

import annotationlib
import functools
import itertools
from collections.abc import Callable, Iterator, Mapping, Sequence
from typing import Never, Protocol, Self, cast, overload, override

import attrs
import duckdb
import polars as pl
import pyarrow as pa

from sdmxlib._read_errors import converting, csv_errors, reads_scan, type_mismatch
from sdmxlib.model.datastructure import DataStructure
from sdmxlib.model.representation import Facet
from sdmxlib.query._closure import columns_in
from sdmxlib.query._common import (
    Descending,
    advance_window,
    canonical_key,
    check_limit,
    check_offset,
    narrow_limit,
    unwrap_order_key,
)
from sdmxlib.query._compile import (
    ROOT_ALIAS,
    CompiledQuery,
    compile_page,
    compile_projection,
    compile_select,
    refuse_to_many_projection,
    spelled,
)
from sdmxlib.query._expr import (
    Agg,
    AggPred,
    AnyAgg,
    AnyCol,
    AnyCols,
    AnyPred,
    AnyWinPred,
    BoolOp,
    Col,
    Cols,
    Compare,
    Computed,
    Cursor,
    InRelation,
    Not,
    OrderKey,
    Pred,
    RowIn,
    RowKind,
    Temporal,
    Win,
    WinFn,
    WinPred,
)
from sdmxlib.query._plan import Plan, scanned
from sdmxlib.query._pushdown import DUCKDB, Program, Push, plan_program
from sdmxlib.session import completed, cursor_for, frame_of, own_handle


def _partition_prefix(root: AnyCols, order: Sequence[OrderKey]) -> tuple[str, ...]:
    """The partition columns a request for `order` can be served by, or `()`.

    A stream can be served partition by partition — bounded *and* ordered —
    only when the requested order begins with the partition columns, in their
    order and ascending. Then visiting partitions in key order and sorting
    inside each yields the same rows as one global sort, because partitions are
    disjoint on those columns.

    Ask for anything else and this cannot help: `ORDER BY TIME_PERIOD` over a
    table partitioned on `REF_AREA` interleaves partitions, and no per-partition
    plan reproduces that without merging them.
    """
    partitions = root.__partitions__
    if not partitions or len(order) < len(partitions):
        return ()
    for column, key in zip(partitions, order[: len(partitions)], strict=True):
        if not key.ascending or key.column.path or key.column.column != column:
            return ()
    return partitions


def _stream_by_partition[R: RowKind, A](
    query: Query[R, A],
    partitions: tuple[str, ...],
    chunk_rows: int,
    compile_one: Callable[[Query[R, A]], CompiledQuery],
) -> pa.RecordBatchReader | None:
    """One sorted read per partition, visited in key order.

    The rows are identical to a global sort's, because partitions are disjoint
    on the columns the order starts with. What differs is the memory: each sort
    covers one partition, so peak tracks the largest partition rather than the
    table — 39.7 MB against 126 MB over 4M rows on a partitioned DuckLake
    table, and lower still as partitions get smaller (#331).

    Returns `None` when there is nothing to plan — no partition values, so the
    caller falls back to the ordinary path rather than streaming an empty
    reader with no schema to give it.
    """
    values = query._partition_values(partitions)
    if not values:
        return None

    def readers() -> Iterator[pa.RecordBatchReader]:
        for value in values:
            compiled = compile_one(query._narrowed_to(partitions, value))
            # A cursor each: the reader holds it until drained, and `from_batches`
            # does not pull the next partition until this one is finished — which
            # is exactly what keeps peak at one partition rather than their sum.
            cursor = own_handle(query._cursor())
            yield completed(cursor).sql(compiled.sql, params=list(compiled.params)).to_arrow_reader(chunk_rows)

    stream = readers()
    first = next(stream)
    return _chain(itertools.chain([first], stream), first.schema)


def _chain(readers: Iterator[pa.RecordBatchReader], schema: pa.Schema) -> pa.RecordBatchReader:
    """Present several readers as one, without materialising any of them.

    `from_batches` pulls lazily, so the second partition's query does not run
    until the first is drained — which is what keeps peak memory at one
    partition rather than the sum.
    """
    return pa.RecordBatchReader.from_batches(schema, itertools.chain.from_iterable(readers))


def _bridge() -> tuple[Callable[..., pl.LazyFrame], Callable[[DataStructure], dict[str, pl.DataType]]]:
    """`sdmxlib.polars.label` and `schema` — the data plane's frame side, imported where used, in one place."""
    from sdmxlib.polars import label, schema  # noqa: PLC0415 - the data plane's frame side, imported where used

    return label, schema


def _typed(frame: pl.DataFrame, root: AnyCols, columns: Mapping[str, str] | None = None) -> pl.DataFrame:
    """`frame` with each component's column typed from the DSD.

    The compiler is structure-agnostic, so a result took whatever DuckDB held —
    ``VARCHAR`` codes, whatever a producer wrote for values. It is typed from
    the structure instead: a codelist's codes as `pl.Enum`, a `Facet`'s type
    as its polars type (`sdmxlib.polars.schema`) — the rule the retired
    `Dataset` applied to a file (#516). A root with no DSD, every structural one, is
    unchanged, as is any column that is not a component: an aggregate, an
    alias of one, an extra column.

    **Only what the DSD declares** — a codelist (or a measure dimension's
    concepts), a `Facet`, the time dimension's format. A component with no
    representation is typed by nothing, and keeps the type it is stored
    with: `schema`'s fallback for it is `pl.String`, which is right for a
    CSV, where everything is text, and wrong for a table, where it would
    turn a stored `DOUBLE` into text.

    `columns` maps a component id to the name it was projected under, as
    `_labelled` takes it; without it, names are component ids.

    Raises:
        ValueError: If a value cannot take its component's type — a code its
            codelist does not list, text where the DSD declares a number or
            a date. Fail fast; the message names the way to the raw rows and
            to the report of every bad value.
    """
    dsd = getattr(root, "dsd", None)
    if not isinstance(dsd, DataStructure):
        return frame
    named = dict(columns) if columns is not None else {}
    declared = _declared_components(dsd)
    casts = {
        name: dtype
        for component, dtype in _bridge()[1](dsd).items()
        if component in declared
        if (name := named.get(component, component) if columns is not None else component) in frame.columns
        and frame.schema[name] != dtype
    }
    if not casts:
        return frame
    try:
        return frame.with_columns(_converted(name, frame.schema[name], dtype) for name, dtype in casts.items())
    except pl.exceptions.PolarsError as exc:
        raise type_mismatch(dsd.id, exc, raw_rows=True) from exc


#: `xs:boolean`'s lexical forms.
_BOOLEANS = {"true": True, "false": False, "1": True, "0": False}


def _converted(name: str, stored: pl.DataType, dtype: pl.DataType) -> pl.Expr:
    """Column `name`, stored as `stored`, as `dtype` — parsed when it is text.

    A cast is not a parse: polars 2.0 casts no text to a date, a datetime or a
    time (1.x did dates only), and no version casts text to a boolean. A value
    stored as ``VARCHAR`` — every observation column is — is parsed instead. A
    datetime with an offset lands in UTC, and every datetime is naive UTC, the
    store's convention (`sdmxlib._time`).
    """
    column = pl.col(name)
    if stored != pl.String():
        return column.cast(dtype)
    match dtype:
        case pl.Date():
            return column.str.to_date()
        case pl.Datetime():
            return column.str.to_datetime(time_unit=dtype.time_unit, time_zone="UTC").dt.replace_time_zone(None)
        case pl.Time():
            return column.str.to_time()
        case pl.Boolean():
            return column.replace_strict(_BOOLEANS, return_dtype=pl.Boolean)
        case _:
            return column.cast(dtype)


def _declared_components(dsd: DataStructure) -> frozenset[str]:
    """The components whose type the DSD states — see `_typed`."""
    found = {
        c.id
        for c in (*dsd.dimensions, *dsd.attributes, *dsd.measures)
        if c.resolved_enumeration is not None or isinstance(c.representation, Facet)
    }
    if dsd.measure_dimension is not None and dsd.measure_dimension.concepts is not None:
        found.add(dsd.measure_dimension.id)
    if dsd.time_dimension is not None:
        found.add(dsd.time_dimension.id)
    return frozenset(found)


def _labelled(
    frame: pl.DataFrame,
    root: AnyCols,
    labels: str | Sequence[str],
    suffix: str,
    columns: Mapping[str, str] | None = None,
) -> pl.DataFrame:
    """Attach label columns, or say why it cannot.

    An argument on the terminal rather than a `.labelled()` clause, because
    every other clause compiles to SQL and this one does not: it runs over the
    materialised frame. Saying so in the signature keeps the refinement chain
    meaning one thing.

    Raises:
        TypeError: If the root has no DSD — a query built straight from a
            store without `dsd=`, since a store cannot resolve one.
        ValueError: If nothing was labelled. Raised by `label`, and for the
            reason it was made to raise: a projection of columns that are none
            of them enumerated produces no label column, and silently returning
            the frame is how `with_labels=True` came to be a no-op.
    """
    # `isinstance`, not `getattr(root, "dsd", None)`. A *structural* root can
    # have a `dsd` too — `_Dataflow.dsd` is a declared edge — so the duck-test
    # passed for `q.dataflow` and handed an accessor object to `label`, which
    # died on `'_Dsd' object has no attribute 'measure_dimension'` a layer
    # down. The intended refusal never fired. This is the `hasattr` narrowing
    # hazard CLAUDE.md names, in the wild.
    dsd = getattr(root, "dsd", None)
    if dsd is None:
        msg = (
            "labels need this dataflow's structure, and this query has none. "
            "Build it through a registry — `reg.obs(flow)`, which resolves the DSD — "
            "or pass the structure to the store: `store.obs(binding, dsd=dsd)`."
        )
        raise TypeError(msg)
    if not isinstance(dsd, DataStructure):
        # A *structural* root can carry a `dsd` too — `_Dataflow.dsd` is a
        # declared edge — so the `getattr` above is satisfied by an accessor
        # and the intended refusal never fired. It died inside the label
        # builder instead, on `'_Dsd' object has no attribute
        # 'measure_dimension'`. The `hasattr`-cannot-narrow hazard, in the wild.
        msg = (
            f"labels turn observation codes into names, and this is a structural query over "
            f"{type(root).__name__.lstrip('_').lower()}. Use `reg.obs(flow_urn).frame(labels='en')` "
            f"for observations, or project the label you want here: "
            f"`select(name=q.codelist.name['en'])`."
        )
        raise TypeError(msg)
    label, _ = _bridge()
    return label(frame.lazy(), dsd, lang=labels, suffix=suffix, columns=columns).collect()


@functools.cache
def _accessor_roots() -> frozenset[str]:
    """The table behind every root on `sl.q`.

    A predicate can only have come from another *root* if its column reads one
    of these. Side tables (`localized_text`, `annotation`) and the observation
    plane's binding-named tables are not roots, so they never trip the check —
    which is what keeps it from refusing the legitimate reads listed at its
    call site.
    """
    return frozenset(root.__table__ for root in _accessor_names())


@functools.cache
def _accessor_names() -> dict[type[AnyCols], str]:
    """`sl.q`'s spelling of each root accessor class — `_DataConstraint` is `data_constraint`.

    For a refusal to name the accessor the caller wrote rather than a private
    class.
    """
    from sdmxlib.query import accessors  # noqa: PLC0415  (cycle: accessors is below us)

    names: dict[type[AnyCols], str] = {}
    for name in annotationlib.get_annotations(type(accessors.q), format=annotationlib.Format.STRING):
        root = getattr(accessors.q, name)
        if isinstance(root, AnyCols):
            names.setdefault(type(root), name)
    return names


def _same_relation(origin: type[AnyCols], root: AnyCols) -> bool:
    """Whether a column reached from `origin` is about the rows `root` scans.

    Table *and* discriminator: every maintainable roots at `sdmx.artefact`, so
    the table alone cannot tell `q.codelist` from `q.dataflow` (#510). An
    origin with no discriminator — `q.artefact`, whose columns every artefact
    has — fits any root on its table, and so does a root with none
    (`reg.artefacts(T)`), which narrows by other means.
    """
    if origin.__table__ != root.__table__:
        return False
    mine, theirs = origin.__constraint__, root.__constraint__
    return mine is None or theirs is None or mine == theirs


def _as_utc(frame: pl.DataFrame, items: Sequence[tuple[str, AnyCol | AnyAgg]]) -> pl.DataFrame:
    """Label every projected `Temporal` column UTC.

    The store is UTC-naive by convention (#321) and `readers` re-attach the
    zone on the way out, so `reg.get(urn).valid_from` is tz-aware. Without
    this a projected frame gave the same field naive, and the two neither
    compare nor subtract.

    `replace_time_zone`, not `convert_time_zone`: the wall time is already
    UTC and must not move. That is the third row of `sdmxlib/_time.py`'s
    round-trip table — *same wall time, now labelled*.
    """
    temporal = [name for name, item in items if isinstance(item, Temporal)]
    if not temporal:
        return frame
    import polars as pl  # noqa: PLC0415 - polars is a type-only import here, deferred as everywhere else

    return frame.with_columns(
        pl.col(name).dt.replace_time_zone("UTC") for name in temporal if frame[name].dtype == pl.Datetime
    )


def _as_int(value: object) -> int:
    """Narrow a `count(*)` cell, which is an integer or the query is not one."""
    if isinstance(value, int):
        return value
    msg = f"expected an integer row count, got {type(value).__name__}"
    raise TypeError(msg)


def _refuse_column(clause: str, value: object, prefix: str = "") -> Never:
    """Raise for a `select` / `group_by` argument that is not a column.

    Split out from the check so the keyword form can name the keyword it
    arrived under — `select(n=...)` is otherwise indistinguishable in the
    message from a positional argument.
    """
    got = type(value).__name__
    if isinstance(value, WinFn):
        # An unframed window is the one refusal here that is a *missing call*
        # rather than a wrong kind of thing, and the generic message sent
        # people looking for a column they already had. `.over()` is required
        # rather than defaulted because a window with no partition is a
        # different question from one with, and the author should say which.
        msg = (
            f"{clause}() takes a column, got {prefix}{got} — a window function that has not been framed. "
            f"Add `.over(...)` to say what it is computed within: `.over()` for one partition "
            f"covering every row, `.over(q.dsd.agency)` for one per agency."
        )
        raise TypeError(msg)
    hint = " A predicate says *which rows*, not which column to project." if isinstance(value, Pred) else ""
    msg = (
        f"{clause}() takes a column, got {prefix}{got}.{hint} "
        f"Pass e.g. `q.dsd.id`; aggregates go in `select(n=q.dsd.id.count())`."
    )
    raise TypeError(msg)


def _refuse_duplicate_names(items: Sequence[tuple[str, object]]) -> None:
    """Refuse a projection that would produce two columns with one name.

    Every maintainable's `id` reads `artefact.resource_id`, so projecting two
    of them — `select(q.dataflow.id, q.dataflow.dsd.id)` — named both
    `resource_id`. polars then silently renamed the second to `resource_id_1`,
    which is a frame whose columns do not mean what the query said and whose
    second column is named after an implementation detail of the collision.

    Refused rather than auto-renamed because only the caller knows which is
    which, and `select(flow=..., dsd=...)` is one keyword away.
    """
    seen: dict[str, int] = {}
    for name, _ in items:
        seen[name] = seen.get(name, 0) + 1
    clashing = sorted(name for name, count in seen.items() if count > 1)
    if not clashing:
        return
    which = ", ".join(repr(name) for name in clashing)
    msg = (
        f"select() would produce more than one column called {which}. Every maintainable's "
        "`id` reads the same storage column, so projecting two of them collides — and only "
        "you know which is which. Give them names: "
        "`select(flow=q.dataflow.id, dsd=q.dataflow.dsd.id)`."
    )
    raise TypeError(msg)


def _require_column(clause: str, value: object) -> None:
    if not isinstance(value, AnyCol):
        _refuse_column(clause, value)


class CrossSourceError(ValueError):
    """A relation from one database was spliced into a query on another (§5.9).

    Its own type rather than a bare `ValueError` because the caller's remedy is
    structural — give both sides one database — and a program that federates
    may want to catch exactly this while letting other `ValueError`s through.
    """


def _foreign_sources(predicate: AnyPred, *, mine: object) -> list[str]:
    """Labels for every source inside `predicate` that is not `mine`.

    Walks the boolean structure, because a cross-source relation can sit under
    an `&`, a `|` or a `~` as easily as at the top — and a check that only
    looked at the outermost node would pass the very composition a caller
    reaches for when combining registries.

    Compared with `is`. A token is an identity, never parsed: two connections
    to the same file are still two databases as far as a spliced subquery is
    concerned. (A mutant swapping `is` for `==` survives the suite, and
    honestly so: a `DuckDBPyConnection` defines no `__eq__`, so the two agree
    today. `is` states the intent for a future token that does.)
    """
    found: list[str] = []
    stack: list[object] = [predicate]
    while stack:
        node = stack.pop()
        match node:
            case InRelation(source=source, source_label=label) if source is not None and source is not mine:
                found.append(label or "an unidentified database")
            case BoolOp(parts=parts):
                stack.extend(parts)
            case Not(part=part):
                stack.append(part)
            case _:
                pass
    return found


class SliceError(ValueError):
    """A clause was added after the query was sliced.

    Every clause compiles into one `SELECT`, so a `filter` added after a
    `limit` lands in the `WHERE` and runs *before* it — returning rows the
    query being refined did not contain. Both readings are defensible, so the
    DSL refuses rather than picking one silently. Django does the same, for the
    same reason.
    """


def _as_artefacts[T](built: Sequence[object]) -> list[T]:
    """The single place a resolver's `object` becomes the root's artefact type.

    `Resolver` is registered per root at runtime (`_RESOLVERS`, keyed by
    table), so no static type relates the callable to the accessor that
    selected it — the same shape `storage.readers.get_artefact` has, and the
    reason that one is allowed to return `Any`.

    The correspondence is real, and it is guarded rather than asserted here:
    `tests/unit/test_query_collect_types.py` runs every root and checks the
    element type against the accessor's declared `A`. Narrowing per element
    instead would need a runtime type to compare against, which is precisely
    what this function does not have — so the check lives where the map is
    known, and this stays one unchecked step rather than 21 scattered ones.
    """
    return cast("list[T]", list(built))


@attrs.frozen
class Query[R: RowKind, A = object]:
    """A lazy query over the relation `root` names.

    `A` is what the object-building terminals return — `collect()`,
    `first()`, `one()`, `one_or_none()` and `Page.collect()`. It comes off the
    root accessor (see `Cols`) and defaults to `object`, which is what all
    five returned for every root before `A` existed.

    `A` is **invariant**, so it has to be carried rather than dropped: the
    set-op methods take `Query[R, A]`, and `Select` / `Grouped` declare the
    parameter even though they never read it, because they hold a query and a
    `Query[R, A]` does not satisfy a `Query[R]`. That is the cost of the
    parameter and it is worth naming — the alternative was 21 overloads on
    each terminal, kept in step with `_RESOLVERS` by hand.

    The root is stored as `Cols[R]`, without `A`. The compiler never needs it:
    `A` exists for the caller's benefit, and the runtime correspondence
    between a root and what it builds lives in `_RESOLVERS`, guarded by
    `tests/unit/test_query_collect_types.py`.

    Construct through `sdmxlib.query.registry.query`, not directly: the
    connection and resolver are wiring rather than part of the question.
    """

    #: The question. Everything a second backend would need and nothing it
    #: would have to ignore — see `_plan.Plan` and pushdown §3.
    plan: Plan[R]
    _conn: duckdb.DuckDBPyConnection = attrs.field(eq=False, repr=False, alias="conn")
    #: Batched resolver from **key rows** to domain objects, or `None` when
    #: this root has none and the resolving terminals should refuse.
    #:
    #: Keys rather than URNs because not every resolvable relation has one: a
    #: code is keyed by `code_urn`, a reported metadata value by
    #: `(set_urn, attribute_path, sibling_index)`. Each row is the tuple of the
    #: root's `__key__` columns, in order, so the resolver knows what it is
    #: being handed without asking.
    #:
    #: `Sequence` rather than `list` because `list` is invariant, so a resolver
    #: returning `list[Dataflow]` would not satisfy a `list[object]` parameter.
    _resolve: Resolver | None = attrs.field(eq=False, repr=False, alias="resolve")
    #: Called with the plan immediately before the first statement runs, for a
    #: backend that must *fetch* what the query is about before it can be
    #: asked — the REST case (#312 §13). `None` for a store that already
    #: holds its rows.
    #:
    #: At the terminal rather than at construction, because a plan is not
    #: finished until then: `reg.query(q.dataflow)` names no agency, and
    #: fetching there would fetch a registry. Strict laziness is what makes
    #: this possible, and is why #306 named it the precondition for pushdown.
    #:
    #: `eq=False`, so it is wiring rather than part of the question: two
    #: queries that mean the same thing must key the same whether or not one
    #: of them has to fetch first.
    _prepare: Callable[[Plan[R], frozenset[tuple[str, str]] | None], None] | None = attrs.field(
        default=None, eq=False, repr=False, alias="prepare"
    )
    #: How to name this query's database when refusing a cross-source splice
    #: (§5.9). The registry's uri; `None` when built without one. `eq=False`
    #: with the connection, so two queries that mean the same thing key the
    #: same regardless of which registry object produced them.
    _source_label: str | None = attrs.field(default=None, eq=False, repr=False, alias="source_label")
    #: Identity of the *database*, distinct from `_conn` because `_conn` may be
    #: a per-thread cursor. Two cursors off one base connection are one
    #: database, and comparing the cursors would refuse the same-registry case
    #: `test_a_relation_valued_filter_crosses_the_planes` exists to protect.
    _source: object = attrs.field(default=None, eq=False, repr=False, alias="source")

    # ── the plan, read through ────────────────────────────────────────────
    #
    # Eight one-line properties rather than eight fields, because a caller
    # asking `q.limit_n` is asking about the question and should not have to
    # know it now lives one level down. Read-only: the plan is refined by
    # `_with`, never assigned.

    @property
    def root(self) -> Cols[R]:
        """The relation scanned."""
        return self.plan.root

    @property
    def predicates(self) -> tuple[Pred[R], ...]:
        """The conjunction narrowing the scan."""
        return self.plan.predicates

    @property
    def qualifiers(self) -> tuple[WinPred[R], ...]:
        """The windowed conjunction — SQL's `QUALIFY`."""
        return self.plan.qualifiers

    @property
    def order_keys(self) -> tuple[OrderKey, ...]:
        """The caller's sort keys, empty when nobody has sorted."""
        return self.plan.order_keys

    @property
    def default_order(self) -> tuple[OrderKey, ...]:
        """The relation's natural order, when nobody has sorted.

        Set by the collection a query came from rather than by a caller —
        `codelist.codes` is in declaration order, matching the eager
        `ItemList` it stands in for.

        Separate from `order_keys` because the two are different claims. A
        caller's `order_by` is a decision the refinement laws govern: it blocks
        `group_by`, and a later call may not append to it. A
        default is neither — it is what the rows mean when nobody has said, so
        `codelist.codes.group_by(...)` must work, and the first `order_by`
        replaces it outright rather than sorting within it.
        """
        return self.plan.default_order

    @property
    def unordered(self) -> bool:
        """Whether the caller has declined a total order — `order_by(None)`.

        A third state, not the absence of one. `order_keys` empty still emits
        the root-key tiebreak, so *unsorted* and *unordered* are different
        claims and Django separates them too (`QuerySet.ordered`).
        """
        return self.plan.unordered

    @property
    def cursor(self) -> Cursor[R] | None:
        """The keyset paging position, or `None` for the first page."""
        return self.plan.cursor

    @property
    def limit_n(self) -> int | None:
        """The window size, or `None` for unbounded."""
        return self.plan.limit_n

    @property
    def offset_n(self) -> int:
        """The window start."""
        return self.plan.offset_n

    def _cursor(self, projection: frozenset[tuple[str, str]] | None = None) -> duckdb.DuckDBPyConnection:
        """The connection to run on, after any fetch the backend owes.

        Every statement this class runs goes through here, which is the point:
        a terminal that reached for `self._conn` directly would query a store
        the REST backend had not filled yet, and answer nothing without error.

        `projection` is the `(table, column)` pairs the caller is about to
        read, and **only a projection may supply it** (§5.14). `None` means
        "artefacts", which is what `collect()` / `one()` / `first()` want: a
        stub carries no items, so a narrower fetch would hand them a codelist
        with zero codes and no error. Every terminal here passes `None`; only
        `Select`, which *is* the projected query, knows it needs nothing beyond
        the columns it names.
        """
        if self._prepare is not None:
            self._prepare(self.plan, projection)
        return cursor_for(self._conn)

    @property
    def _fetch_scope(self) -> str | None:
        """The fetch whose rows this query may see, or `None` for all of them.

        `None` for a `LocalRegistry`: it has no fetch, so its rows *are* its
        answer (§5.13, #340). A REST-backed query is scoped to what its own
        fetch brought, or a withdrawn artefact still in the store answers
        alongside the provider's reply.

        Derived from the plan rather than carried as a field, which is what
        makes this work at all: the key is `canonical_key(plan)`, known while
        the SQL is compiled. A `fetched_at` watermark is not — terminals
        compile before they fetch — and `to_sql()` is contractually
        non-executing, so it could not be made to.

        Restricted to roots keyed by URN on `artefact`, which over REST is all
        of them (`Capability.scans` is `_maintainable_roots()`). The guard is
        for `ObsQuery`, which also carries a `prepare` hook but whose rows are
        observations in a bound table, with no urn to scope by.
        """
        if self._prepare is None or self.root.__table__ != "artefact" or self.root.__key__ != ("urn",):
            return None
        return str(canonical_key(self.plan))

    def _projection_columns(self, items: tuple[tuple[str, AnyCol | AnyAgg], ...]) -> frozenset[tuple[str, str]] | None:
        """The `(table, column)` pairs a projection reads, or `None` to refuse.

        `None` means "cannot narrow", which asks for `full` — the safe answer.
        Returned for anything that is not a plain column **on the root**: an
        aggregate (`codes.id.list()`), a column reached across a path, or a
        computed expression whose operands may be either. All three read items
        a stub does not carry.

        The tempting shape was to contribute only the columns and let
        `closure(plan)` catch the crossed paths. It does not: the closure is
        derived from the *plan* — predicates and order — while a projection's
        path lives on the `Select`. So `select(codes=…list())` came out
        `allstubs` and would have returned empty lists, silently. That is the
        exact failure `detail_for` documents, arriving by the one route
        nothing was checking, and it is why this refuses anything non-trivial
        rather than guessing: the saving is kept where it is obviously sound,
        `select(q.codelist.id)`, and given up everywhere else.
        """
        columns: set[tuple[str, str]] = set()
        for _, item in items:
            if not isinstance(item, AnyCol) or item.path:
                return None
            columns.add((item.table, item.column))
        return frozenset(columns)

    def _with(self, plan: Plan[R]) -> Self:
        """Carry the binding onto a refined plan.

        Takes a built `Plan` rather than `**changes`, which was the first cut
        and was wrong: `**changes: object` type-checks nothing, so a clause
        assigning `limit_n=` a tuple would compile. pyrefly rejected it, and
        erasing the field types is precisely what a typed plan exists to
        prevent — so the call sites evolve the plan themselves, under the
        checker, and this only carries `_conn` and `_resolve` across.
        """
        return attrs.evolve(self, plan=plan)

    # ── composition ───────────────────────────────────────────────────────

    def filter(self, *predicates: Pred[R]) -> Self:
        """Narrow by AND-ing `predicates` onto the query.

        Several predicates in one call and several calls are the same thing —
        `filter` is AND, the one composition law unanimous across SQLAlchemy,
        Django, ibis and polars.

        Typed on the query's own row kind, so a predicate built for another
        root is rejected here rather than compiling to a `WHERE` against a
        relation absent from the `FROM`.

        Raises:
            SliceError: If the query has already been sliced.
            TypeError: If handed something that is not a predicate — a bare
                column or an accessor. The row-kind phantoms catch a predicate
                built for *another* root statically; they cannot catch a value
                that is not a predicate at all, and untyped call sites are
                where that arrives. `CodeQuery` refused it and this did not.
            CrossSourceError: If a predicate carries a relation from a
                different database — see `_refuse_foreign_source`.
        """
        self._refuse_after_slice("filter")
        for predicate in predicates:
            if isinstance(predicate, AnyWinPred):
                msg = (
                    "filter() got a comparison on a window, which SQL tests after WHERE. "
                    "Pass it to qualify(): .qualify(col.rank().over(...) == 1)."
                )
                raise TypeError(msg)
            if not isinstance(predicate, AnyPred):
                got = type(predicate).__name__
                msg = (
                    f"filter() takes a predicate, got {got}. A column is not a "
                    f'question about a row — compare it: filter(q.code.id == "X"). '
                    f"An accessor is a namespace; name one of its columns."
                )
                raise TypeError(msg)
            self._refuse_foreign_source(predicate)
            self._refuse_foreign_root(predicate)
        return self._with(attrs.evolve(self.plan, predicates=(*self.predicates, *predicates)))

    def _refuse_foreign_root(self, predicate: AnyPred) -> None:
        """Refuse a predicate built for a different relation.

        `reg.query(q.dataflow).filter(q.code.id == "A")` is a type error, and a
        typed codebase never sees it. A notebook does — and reached DuckDB,
        which answered:

            BinderException: Table "s0" does not have a column named "code_id"
              Candidate bindings: : "fetched_at"

        Every other misuse in this language gets a message written for it.
        This one named an alias the caller never wrote, a column they never
        wrote either, and suggested `fetched_at`.

        Reachability, not table equality: `q.dataflow.dsd.id` reads `artefact`
        through a hop and is perfectly valid, while `q.code.id` reads `code`
        from nowhere. `resolve_path` is what the compiler itself uses, so a
        predicate this accepts is one the compiler can plan.
        """
        root = self.root.__table__
        for column in columns_in(predicate):
            # The column knows the root accessor it was reached from, so a
            # predicate built off another one is refused whatever its path —
            # including `q.codelist.agency` on a query over dataflows, which
            # reads the same table and so passed every check below while
            # silently answering for dataflows (#510).
            origin = column.origin
            if origin is not None and not _same_relation(origin, self.root):
                names = _accessor_names()
                theirs = names.get(origin, origin.__name__.lstrip("_").lower())
                mine = names.get(type(self.root), type(self.root).__name__.lstrip("_").lower())
                msg = (
                    f"filter() got a predicate built from q.{theirs}, but this query is over "
                    f"q.{mine}. A predicate belongs to the relation it was built from: write it "
                    f"from q.{mine} (q.{mine}.{column.projected_name}), or reach the other relation "
                    f"by a declared hop from q.{mine}."
                )
                raise TypeError(msg)
            # Only a column read *directly* off another accessor root is the
            # mistake this catches. Deliberately narrow:
            #
            # * a path is fine — `q.dataflow.dsd.id` reads `artefact` through a
            #   declared hop, which is the commonest traversal in SDMX;
            # * a side table is fine — `q.dsd.name["en"]` reads
            #   `localized_text` by correlated subquery and has no path;
            # * the observation plane is fine — it roots at a binding's table
            #   and joins a sidecar, neither of which `_edges` knows.
            #
            # What is left is exactly `q.code.id` handed to a query over
            # dataflows, which is what reached DuckDB as a binder error.
            if column.path or column.table == root:
                continue
            if column.table in _accessor_roots():
                msg = (
                    f"filter() got a predicate about {column.table!r}, but this query is "
                    f"rooted at {root!r}. A predicate belongs to the relation it was built "
                    f"from — root the query there, or reach it by a declared hop "
                    f"(q.<root>.<hop>.{column.column})."
                )
                raise TypeError(msg)

    @property
    def source(self) -> object:
        """The database this query reads — `sdmxlib.sql.HasSource`.

        The registry's base connection, not a per-thread cursor, so two queries
        from one registry report the same token from any thread. Compared with
        `is`; `source_label` is what a message shows.
        """
        return self._source if self._source is not None else self._conn

    @property
    def source_label(self) -> str:
        """How to name this query's database in an error.

        A `DuckDBPyConnection` has no useful `repr`, so a refusal built from
        the token alone reads *"a different database (DuckDBPyConnection)"*
        twice — naming two sources and distinguishing neither, which is
        exactly what §5.9 asks the message not to do.
        """
        return self._source_label or f"<{type(self._conn).__name__} 0x{id(self._conn):x}>"

    def _refuse_foreign_source(self, predicate: AnyPred) -> None:
        """Refuse a predicate whose subquery reads a different database (§5.9).

        The failure this prevents is not an error but a **wrong answer**.
        `obs.filter(obs.col(X).is_in(other_registry.query(...)))` splices the
        other registry's *SQL text* into this connection, where the same table
        names resolve to this database's rows. Measured before the fix: two
        registries whose codelists disagreed about which codes are named
        "Ontario" returned the consumer's answer, silently, and it looked
        entirely plausible.

        Refused rather than repaired. Splicing the other side's N rows in as
        binds would reintroduce the parameter explosion `InRelation` exists to
        avoid, and doing it invisibly is what §1.3's third rule forbids.
        Auto-`ATTACH` was rejected outright: the chain must not open
        connections the caller did not ask for.

        A relation that cannot report a source is trusted — see
        `sdmxlib.sql.HasSource` for why that is the right default.

        Raises:
            CrossSourceError: Naming both sources and the fix.
        """
        for label in _foreign_sources(predicate, mine=self.source):
            msg = (
                f"This query reads {self.source_label} but the relation inside it reads "
                f"{label}. Their tables are not visible to each other, so the "
                f"subquery would silently read *this* database under the other one's SQL — a "
                f"wrong answer, not an error.\n"
                f"Give both sides one database: open the remote registry with "
                f"`store=<the local registry>` so its artefacts land here, or run the inner "
                f"query against this registry. Passing `.ids()` in as a list also works, at the "
                f"cost of one bind per row."
            )
            raise CrossSourceError(msg)

    def qualify(self, *predicates: WinPred[R]) -> Self:
        """Narrow by AND-ing windowed predicates — SQL's `QUALIFY`.

        A separate clause from `filter` because a windowed test is not an
        ordinary one: `COUNT(*) OVER (...)` is not valid in a `WHERE`, and the
        two land in different parts of the statement.

        **Call order does not decide which set the window sees.** Every clause
        compiles into one `SELECT`, and SQL evaluates `WHERE` before the
        window, so `filter(p).qualify(w)` and `qualify(w).filter(p)` are the
        same query: the rank is computed over the *filtered* rows either way.
        They commute, which is what the refinement principle asks of every
        pair except `limit`/`offset`.

        The other reading — rank across **everything**, then keep the rows
        matching `p` — is a different query rather than a different call
        order, and it is spelled by nesting, exactly as filter-after-slice is::

            ranked = reg.query(q.dataflow).qualify(w).select(urn=q.dataflow.urn)
            reg.query(q.dataflow).filter(q.dataflow.urn.is_in(ranked), p)

        This docstring previously claimed the two call orders expressed those
        two readings. They never did: both emitted byte-identical SQL, so a
        caller reaching for the second got the first with nothing to say so.

        **Why filter-after-qualify is not refused**, unlike filter-after-slice:
        there the two readings return *different rows*, so picking one
        silently is a wrong answer and the guard prevents it. Here they return
        the same rows, so a refusal would fire on a correct, unambiguous
        query — breaking the ordinary case where a helper qualifies and its
        caller narrows — to prevent a misunderstanding rather than a defect.

        The distinction is the *type*, not discipline. `.over()` returns a
        `Win`, whose comparisons produce `WinPred` rather than `Pred`, so
        `filter(rank == 1)` and `qualify(id == "X")` are both rejected by the
        checker with the other clause named.

            reg.query(q.dataflow).qualify(
                q.dataflow.version.rank(descending=True)
                                  .over(q.dataflow.agency, q.dataflow.id) == 1
            )

        Raises:
            SliceError: If the query has already been sliced.
            TypeError: If handed something that is not a windowed comparison.
                The checker refuses it statically; an untyped call site reached
                the compiler, which said only "cannot compile Compare".
        """
        self._refuse_after_slice("qualify")
        for predicate in predicates:
            if not isinstance(predicate, AnyWinPred):
                where = (
                    "an ordinary predicate belongs in filter()"
                    if isinstance(predicate, AnyPred)
                    else f"got {type(predicate).__name__}"
                )
                msg = f"qualify() takes a comparison on a window — col.rank().over(...) == 1; {where}."
                raise TypeError(msg)
        return self._with(attrs.evolve(self.plan, qualifiers=(*self.qualifiers, *predicates)))

    def order_by(self, *keys: Col[R] | Descending[Col[R]] | None) -> Self:
        """Set the sort order, replacing any already set. No arguments clears.

        **Replaces rather than appends**, which is the one place this DSL
        deliberately breaks the refinement principle, and the existing DSLs
        already do the same. Appending cannot be undone, so a helper
        that sorted would silently outrank its caller; and "sort by A, then by
        B" is one call with two keys, not two calls. Direction is per key —
        `q.dsd.version.desc()` — because "newest first, then id ascending" is
        the common shape.

        Ordering by a path that crosses a to-many edge is refused: there is no
        single value per row to sort on.

        **`order_by(None)` declines ordering outright**, which is SQLAlchemy's
        spelling for it and a *third* state rather than the absence of one:

            q.order_by(q.code.id)   # sort by id, then the tiebreak
            q.order_by()            # clear the keys, keep the tiebreak
            q.order_by(None)        # no ORDER BY at all

        The middle one drops the caller's keys and leaves the root-key
        tiebreak, so an unsorted result is still *stable* between calls rather
        than merely unspecified — paging depends on that. On a small relation
        the tiebreak is free. On 4.26 M
        observations it was **5.4x the scan it decorates**, for an order most
        callers of `frame()` never look at, and nothing could decline it
        but `stream()`.

        So this is the caller saying they do not need one. It closes the query
        to `limit`, `offset` and `after`, since a window over an unspecified
        scan order silently skips and repeats rows between calls — the failure
        the tiebreak exists to prevent, which is why declining it and paging
        cannot both be allowed. A later `order_by(...)` reopens it.

        Raises:
            SliceError: If the query has already been sliced. A different order
                over the same window is a different set of rows, so this is the
                same violation `filter` after `limit` is.
            TypeError: If a key is not a column, or if `None` is passed
                alongside one — `None` cancels the order and cannot be one of
                several keys.
        """
        self._refuse_after_slice("order_by")
        resolved: list[OrderKey] = []
        for key in keys:
            if key is None:
                if len(keys) > 1:
                    msg = (
                        f"order_by(None) declines ordering outright, so it "
                        f"cannot be one of {len(keys)} keys. Pass None alone, "
                        f"or pass only columns."
                    )
                    raise TypeError(msg)
                return self._with(attrs.evolve(self.plan, order_keys=(), unordered=True))
            column, ascending = unwrap_order_key(key)
            if not isinstance(column, Col):
                got = type(column).__name__
                hint = " A predicate says *which rows*, not which column to sort on." if isinstance(key, Pred) else ""
                msg = (
                    f"order_by() takes a column, got {got}.{hint} "
                    f"Pass e.g. `q.dsd.id`, or `q.dsd.id.desc()` for descending."
                )
                raise TypeError(msg)
            resolved.append(OrderKey(column, ascending))
        # `unordered=False` unconditionally: `order_by` replaces the ordering
        # outright, which is its documented semantics, so naming keys after
        # `order_by(None)` reopens the query rather than being ignored.
        return self._with(attrs.evolve(self.plan, order_keys=tuple(resolved), unordered=False))

    def intersect(self, other: Query[R, A]) -> Self:
        """Narrow to rows also matched by `other` — a semi-join.

        A method because it *narrows*: the result is always a subset of this
        query, so nothing `other` carries can undo what this one established.
        `union` is a free function for exactly the opposite reason.

        Typed on the row kind, so intersecting dataflows with codes is a type
        error rather than an empty result.

        Raises:
            SliceError: If this query has already been sliced.
            TypeError: If `other` is rooted at a different relation.
        """
        self._refuse_after_slice("intersect")
        return self._with(attrs.evolve(self.plan, predicates=(*self.predicates, self._membership(other, negate=False))))

    def difference(self, other: Query[R, A]) -> Self:
        """Narrow to rows *not* matched by `other` — an anti-join.

        Narrows for the same reason `intersect` does, so it is likewise a
        method.

        Raises:
            SliceError: If this query has already been sliced.
            TypeError: If `other` is rooted at a different relation.
        """
        self._refuse_after_slice("difference")
        return self._with(attrs.evolve(self.plan, predicates=(*self.predicates, self._membership(other, negate=True))))

    def _membership(self, other: Query[R, A], *, negate: bool) -> Pred[R]:
        self._require_same_root(other)
        self._require_same_source(other)
        sql, params = self._key_select(other)
        return RowIn(self.root.__key__, sql, tuple(params), negate=negate)

    def _require_same_source(self, other: Query[R, A]) -> None:
        """Refuse a set operation across two databases (§5.9).

        `intersect` / `difference` splice the other query's SQL exactly as
        `is_in` splices a relation's, so they carry the same wrong answer. The
        check is cheaper here because the argument *is* a `Query` — there is no
        foreign producer that might legitimately be unable to say.

        Raises:
            CrossSourceError: Naming both sources and the fix.
        """
        if other.source is self.source:
            return
        msg = (
            f"This query reads {self.source_label} but the one passed to it reads "
            f"{other.source_label}. Their tables are not visible to each other, so the "
            f"set operation would silently read *this* database under the other one's SQL "
            f"— a wrong answer, not an error.\n"
            f"Give both sides one database: open the remote registry with "
            f"`store=<the local registry>` so its artefacts land here, or run both queries "
            f"against this registry."
        )
        raise CrossSourceError(msg)

    def _require_same_root(self, other: Query[R, A]) -> None:
        """The row kind stops most of this statically; a discriminator does not.

        `q.dsd` and `q.codelist` are both `_Maintainable` over `sdmx.artefact`
        and differ only by `__constraint__`, so their row kinds are distinct
        types but a caller reaching for `Query[ArtefactRow]` can still pair
        them. Same relation *and* same discriminator, or the key columns would
        line up and the answer would silently be empty.
        """
        mine, theirs = type(self.root), type(other.root)
        if mine.__table__ == theirs.__table__ and mine.__constraint__ == theirs.__constraint__:
            return
        msg = (
            f"cannot combine a query over {mine.__name__.lstrip('_').lower()} "
            f"with one over {theirs.__name__.lstrip('_').lower()}: a set "
            f"operation compares rows of one relation. Root both at the same "
            f"accessor."
        )
        raise TypeError(msg)

    def _key_select(self, other: Query[R, A]) -> tuple[str, list[object]]:
        """`other` compiled down to just its key columns.

        Ordered only when `other` is sliced. A `LIMIT` without an `ORDER BY`
        takes an arbitrary N, so the sort is load-bearing there and pure
        overhead everywhere else.
        """
        columns = ", ".join(f"{other._alias}.{column}" for column in other.root.__key__)
        compiled = other.to_sql(columns=columns, ordered=other._sliced())
        return compiled.sql, compiled.params

    def after(self, cursor: Cursor[R] | None) -> Self:
        """Narrow to rows after `cursor` — the keyset half of paging.

        `None` returns the query unchanged, so the first page and every later
        one are the same expression:

            page = reg.query(q.dataflow).order_by(q.dataflow.id).after(cursor).page(100)

        **Why not `offset`.** `offset(n)` makes the database walk and discard
        `n` rows, so page 500 costs 500 pages of work; and a row inserted
        before the window between calls shifts every later page, silently
        repeating one row and skipping another. A keyset comparison does
        neither.

        The design left open whether this should refuse without an
        `order_by`. It does not need to: every query here already carries the
        root's key as a tiebreak, so the order is *always* total and a cursor
        is always well defined. An unsorted query pages by URN, which is
        arbitrary but stable — and stable is the whole requirement.

        The cursor knows how many keys it was built from and in which
        directions, and is refused against a query that has since been sorted
        differently.

        Raises:
            SliceError: If the query has already been sliced.
        """
        if cursor is None:
            return self
        self._refuse_after_slice("after")
        self._refuse_unordered("after")
        return self._with(attrs.evolve(self.plan, cursor=cursor))

    def limit(self, n: int) -> Self:
        """Cap the number of rows, never raising an existing cap.

        Raises:
            SliceError: If the query is unordered — see `_refuse_unordered`.
        """
        self._refuse_unordered("limit")
        check_limit(n)
        return self._with(attrs.evolve(self.plan, limit_n=narrow_limit(self.limit_n, n)))

    def offset(self, n: int) -> Self:
        """Raise the window's floor, holding its ceiling fixed.

        Raises:
            SliceError: If the query is unordered — see `_refuse_unordered`.
        """
        self._refuse_unordered("offset")
        check_offset(n)
        start, length = advance_window(self.offset_n, self.limit_n, n)
        return self._with(attrs.evolve(self.plan, offset_n=start, limit_n=length))

    def _by_id(self, item_id: str) -> Self:
        column = getattr(self.root, "id", None)
        if not isinstance(column, AnyCol):
            msg = (
                f"{self.root.__class__.__name__} rows have no id, so they "
                f"cannot be looked up by one. Filter on a column instead."
            )
            raise TypeError(msg)
        return self.filter(Compare(column, "=", item_id))

    def get(self, item_id: str) -> A | None:
        """The row with `item_id`, or `None` — `ItemList.get` over a relation.

        The lazy and eager forms of a collection have to answer the same
        questions or `codelist.codes` means two different things depending on
        where the codelist came from, which is the whole premise of the lazy
        artefacts.
        """
        return self._by_id(item_id).one_or_none()

    @overload
    def __getitem__(self, key: str) -> A: ...

    @overload
    def __getitem__(self, key: slice) -> Self: ...

    @overload
    def __getitem__(self, key: int) -> Self: ...

    def __getitem__(self, key: str | slice | int) -> A | Self:
        """Slicing refines; indexing does not. A *string* looks up by id.

        The string form is `ItemList.__getitem__` — `codes["FREQ"]` — and is
        the reason indexing is not simply refused outright. It is unambiguous
        against the other two because an id is never a position.
        """
        if isinstance(key, str):
            found = self.get(key)
            if found is None:
                msg = f"no row with id {key!r}"
                raise KeyError(msg)
            return found
        return self._slice(key)

    def _slice(self, key: slice | int) -> Self:
        """Slicing refines; indexing does not.

        polars and Django arrived at this independently — `lf[0:2]` is a
        `LazyFrame` while `lf[0]` raises — and it is the rule that lets the
        familiar spelling survive strict laziness. A slice is `offset` and
        `limit`, so it inherits their laws unchanged.

        Raises:
            TypeError: On positional indexing, which extracts rather than
                refines and needs a total order to mean anything.
        """
        if isinstance(key, int):
            msg = (
                f"a query cannot be indexed positionally ({key!r}): indexing "
                f"extracts a row, and a query is a plan. Slice it to refine "
                f"(q[{key}:{key + 1}]), or take a terminal — .first(), .one()."
            )
            raise TypeError(msg)
        if key.step is not None:
            msg = "a query slice has no step; it compiles to OFFSET and LIMIT."
            raise TypeError(msg)
        out = self
        if key.start:
            out = out.offset(key.start)
        if key.stop is not None:
            start = key.start or 0
            out = out.limit(max(key.stop - start, 0))
        return out

    @property
    def _order(self) -> tuple[OrderKey, ...]:
        """What to sort by: the caller's keys, or the relation's natural order."""
        return self.plan.order

    def _refuse_unordered(self, clause: str) -> None:
        """Refuse a window over a query that declined its total order.

        The other half of `order_by(None)`. A `limit` or a cursor decides
        *which* rows come back, so it needs the order to be total and stable
        across calls — without that two pages overlap and a third is never
        seen, which is #264 and exactly what the tiebreak exists to prevent.
        Declining the tiebreak and then paging would reintroduce it silently,
        so the two are made mutually exclusive rather than documented apart.

        `SliceError` rather than a new type: it is the same law — a clause
        whose meaning depends on the window, refused because the window cannot
        mean anything here.
        """
        if not self.unordered:
            return
        msg = (
            f"cannot {clause}() an unordered query. order_by(None) declined "
            f"the total order, and a window over an unspecified scan order "
            f"silently skips and repeats rows between calls. Order it — "
            f"order_by() restores the tiebreak alone — or read it whole with "
            f"stream(), which is unordered and bounded by design."
        )
        raise SliceError(msg)

    def _refuse_after_slice(self, clause: str) -> None:
        if self.limit_n is None and not self.offset_n:
            return
        msg = (
            f"cannot {clause}() a query that has been sliced. Every clause "
            f"compiles into one SELECT, so the {clause} would run *before* the "
            f"slice and could return rows the sliced query does not contain. "
            f"Filter first, then slice — or express the other reading with "
            f"is_in() over this query."
        )
        raise SliceError(msg)

    # ── inspection: runs nothing ──────────────────────────────────────────

    def _is_ordered(self) -> bool:
        """Whether a stream of this query has to sort.

        Only when the caller asked. The unconditional tiebreak exists so
        `limit`/`offset` paging is stable — a scan order that changes between
        calls silently skips and repeats rows — and a stream reads everything
        once, so it buys nothing there. A slice still needs it, because *which*
        rows a `limit` returns depends on the order.

        This is the whole of `stream`'s memory story: sorting materialises the
        result before the first batch can leave, so an ordered stream is
        unbounded and an unordered one is not.
        """
        return bool(self._order) or self.limit_n is not None or self.offset_n != 0

    def to_sql(self, *, columns: str | None = None, ordered: bool = True) -> CompiledQuery:
        """Compile without executing.

        `columns` defaults to every column of the row — the root relation's,
        plus any root join's, which is what makes a sidecar attribute appear in
        an unprojected result. A terminal that needs one column passes it
        rather than trimming afterwards.

        `ordered=False` drops the `ORDER BY` entirely, for the two terminals
        that reduce to a single row. `count(*)` sorts nothing, and a sort key
        naming a column outside the aggregate is a binder error rather than a
        wasted clause.

        `order_by(None)` drops it too, for every terminal — this is where that
        state reaches the compiler. The parameter and the flag are *and*-ed
        rather than one overriding the other: both are reasons not to sort, and
        neither is a reason to.
        """
        # `None` reaches the compiler, which resolves it to the root alias's
        # star *plus any root join's* — the aliases are its to assign, so it is
        # the only place that can name them.
        selected = columns
        pushed = self._program().pushes[0].plan
        compiled = compile_select(
            pushed.root,
            pushed.predicates,
            columns=selected,
            order=(self._order if ordered and not self.unordered else None),
            qualify=pushed.qualifiers,
            after=pushed.cursor,
            fetch_scope=self._fetch_scope,
        )
        return compiled._replace(sql=compiled.sql + self._window_sql())

    def _program(self) -> Program[R]:
        """This plan split against the backend that will run it.

        DuckDB declares total capability, so the program is one `Push` holding
        the plan unchanged and this is a no-op — which is the point of routing
        through it now rather than when a second backend arrives. The path is
        then already exercised by every query the suite runs, instead of
        appearing at the same moment as the first backend that stresses it.
        """
        program = plan_program(self.plan, DUCKDB)
        if len(program.steps) != 1 or not isinstance(program.steps[0], Push):
            # Unreachable while DuckDB is total, and cheap insurance against
            # the day it is not: compiling one statement out of a program that
            # says two are needed would silently drop the second.
            msg = (
                f"{program.backend} split this query into {len(program.steps)} steps, "
                f"and this compiler emits one statement. A multi-step program needs "
                f"an executor, not a compiler."
            )
            raise NotImplementedError(msg)
        return program

    def explain(self) -> str:
        """A human-readable plan. Runs nothing.

        **Says when rows are fetched first, because the SQL alone does not.**
        A query served over HTTP compiles to an ordinary `SELECT` over the
        store it will be materialised into — so without this line the plan
        reads as a local table scan, and the request that fills that table is
        invisible. The distinction is worth a line: it is the difference
        between reading rows that are already here and downloading them.
        """
        compiled = self.to_sql()
        binds = ", ".join(repr(p) for p in compiled.params) or "none"
        fetch = "" if self._prepare is None else "\n-- fetches before running; the SQL below is over materialised rows"
        return f"{compiled.sql}\n-- binds: {binds}{fetch}"

    @property
    @csv_errors
    def columns(self) -> tuple[str, ...]:
        """Column names this query produces — the `SqlRelation` contract.

        Every column of the root relation, because `to_sql()` with no argument
        selects them all. So a bare `Query` is a *multi*-column relation and
        `is_in` refuses it by name rather than letting the database
        emit a column-count error. `select(one_column)` is the single-column
        form, which is what splices into `WHERE col IN (SELECT ...)`.

        Read with `DESCRIBE`, which plans without running.
        """
        compiled = self.to_sql(ordered=False)
        rows = self._cursor().execute(f"DESCRIBE {compiled.sql}", list(compiled.params)).fetchall()
        return tuple(str(row[0]) for row in rows)

    def canonical_key(self) -> tuple[object, ...]:
        """A stable, hashable key identifying this query's *meaning*.

        Two queries built by different call sequences that mean the same thing
        return the same key — `filter(a).filter(b)` and `filter(b).filter(a)`
        differ as values and emit their clauses in different orders, and agree
        here. That is what a downstream cache needs and what neither the SQL
        text nor the query object offers.

        Deterministic across processes: no `id()`, no `hash()`, no set
        iteration. Built by the same `canonical_key` the four older DSLs use,
        which walks `attrs` fields structurally — so the connection and the
        resolver are excluded by their own `eq=False`, and a field added to
        `Query` is in the key without anything here being updated.

        The expression leaves are not `attrs`, and describe themselves through
        `__canonical__`. That is load-bearing rather than tidy: the fallback
        keys an object on its `repr`, and `AnyCol.__repr__` omits the language,
        so `name["en"]` and `name["fr"]` would key identically — a cache
        serving one language's rows for another.

        Returns:
            A tuple of tuples of primitives. Opaque; compare and hash it, do
            not parse it.
        """
        return canonical_key(self)

    def _window_sql(self) -> str:
        parts = ""
        if self.limit_n is not None:
            parts += f"\nLIMIT {self.limit_n}"
        if self.offset_n:
            parts += f"\nOFFSET {self.offset_n}"
        return parts

    # ── terminals: each runs exactly one statement ────────────────────────

    @property
    def _alias(self) -> str:
        """Alias the compiler gives the root — needed to qualify a column.

        A constant. This used to compile a whole throwaway query to read it
        back, on every `urns()` / `ids()` / `_key_rows()` call.
        """
        return ROOT_ALIAS

    @csv_errors
    def _rows(self, columns: str) -> list[tuple[object, ...]]:
        compiled = self.to_sql(columns=columns)
        return self._cursor().execute(compiled.sql, list(compiled.params)).fetchall()

    @property
    def _urn_column(self) -> str:
        """Physical column carrying this root's URN.

        Read off the accessor's own `urn` declaration rather than assumed to be
        called `urn`: it is `code_urn` on `sdmx.code` and `concept_urn` on
        `sdmx.concept`, so assuming was a binder error on every root but a
        maintainable — `urns()`, `collect()`, `first()` and `one()` all of them.

        Raises:
            TypeError: If the root has no URN. A `dsd_component` row has no
                identity of its own, so there is nothing to resolve to.
        """
        declared = getattr(type(self.root), "urn", None)
        if isinstance(declared, AnyCol):
            return declared.column
        name = type(self.root).__name__.lstrip("_").lower()
        msg = (
            f"q.{name} rows have no URN, so they cannot be resolved to "
            f"artefacts. Use .ids() or .frame() for their columns, or root the "
            f"query at the artefact that owns them."
        )
        raise TypeError(msg)

    def _resolver(self, terminal: str) -> Resolver:
        """This query's resolver, or a refusal naming what does work.

        Registered per root by the registry, because turning rows into domain
        objects is `storage.readers`' job rather than the compiler's. A root
        with none is not a defect — a `dsd_component` row has no object to
        become — so the refusal names the terminals that do not need one.

        Before #306 this asked whether the *root table* was `sdmx.artefact`,
        which was the same answer for the wrong reason and would have refused
        `q.code` for ever.

        Raises:
            TypeError: If nothing can turn this root's rows into objects.
        """
        if self._resolve is not None:
            return self._resolve
        name = type(self.root).__name__.lstrip("_").lower()
        msg = (
            f"{terminal}() builds domain objects, and nothing knows how to "
            f"build one from a q.{name} row. Use .ids() for their identifiers, "
            f".frame() or .select() for their columns, or root the query at "
            f"the artefact that owns them."
        )
        raise TypeError(msg)

    def _key_rows(self) -> list[tuple[object, ...]]:
        """The root's `__key__` columns for every matching row, in order."""
        columns = ", ".join(f"{self._alias}.{column}" for column in self.root.__key__)
        return [tuple(row) for row in self._rows(columns)]

    def urns(self) -> list[str]:
        """Matching URNs, in order. No resolution."""
        return [str(r[0]) for r in self._rows(f"{self._alias}.{self._urn_column}")]

    def ids(self) -> list[str]:
        """Matching ids. No resolution."""
        return [str(r[0]) for r in self._rows(f"{self._alias}.{self.root.__id__}")]

    @csv_errors
    def count(self) -> int:
        """Number of matching rows — `COUNT(*)`, fetching none of them.

        Wrapped in `SELECT count(*) FROM (...)` when the query is sliced or
        qualified, and only then — a bare `count(*)` is cheaper and is what
        most calls get.

        Both cases are wrong without the wrapper, in different ways. Applying a
        *slice* to the aggregate counts every row and then limits the one-row
        result, so `q.limit(3).count()` would return the full count — the
        defect `__len__` once had. A `QUALIFY` alongside `count(*)` is not
        merely wrong but invalid: the window's partition columns are not in the
        aggregate, and DuckDB refuses to bind it.
        """
        if self._sliced() or self.qualifiers:
            inner = self.to_sql(columns="1", ordered=False)
            rows = self._cursor().execute(f"SELECT count(*) FROM ({inner.sql})", list(inner.params)).fetchall()
        else:
            compiled = self.to_sql(columns="count(*)", ordered=False)
            rows = self._cursor().execute(compiled.sql, list(compiled.params)).fetchall()
        return _as_int(rows[0][0]) if rows else 0

    def _window(self, n: int) -> Self:
        """A row cap for a terminal that reduces, bypassing `limit`'s guard.

        `exists`, `one` and `first` cap the rows they read, and none of them is
        a *window* in the sense `_refuse_unordered` is about: nothing pages
        from here, so there is no later call for rows to be skipped between.
        Going through the public `limit` would refuse an unordered query and
        name a method the caller never wrote.
        """
        return self._with(attrs.evolve(self.plan, limit_n=narrow_limit(self.limit_n, n)))

    @csv_errors
    def exists(self) -> bool:
        """Whether any row matches. Fetches no rows."""
        compiled = self._window(1).to_sql(columns="1", ordered=False)
        return bool(self._cursor().execute(compiled.sql, list(compiled.params)).fetchall())

    def first(self) -> A | None:
        """The first matching artefact, or `None`.

        First *in this query's order* — which after `order_by(None)` is no
        order at all, so the row is an arbitrary match rather than a defined
        one. That is what the caller asked for and what SQLAlchemy's `.first()`
        does with an unordered statement; `order_by(...)` is how to mean a
        particular row.
        """
        resolve = self._resolver("first")
        keys = self._window(1)._key_rows()
        return _as_artefacts(resolve(keys))[0] if keys else None

    def one(self) -> A:
        """The single matching artefact.

        Raises:
            ValueError: If the query matched anything other than one row.
        """
        resolve = self._resolver("one")
        found = self._window(2)._key_rows()
        if len(found) != 1:
            msg = f"expected exactly one row, found {len(found)}"
            raise ValueError(msg)
        return _as_artefacts(resolve(found))[0]

    def one_or_none(self) -> A | None:
        """The single matching artefact, or `None` if there were none.

        Raises:
            ValueError: If the query matched more than one row.
        """
        resolve = self._resolver("one_or_none")
        found = self._window(2)._key_rows()
        if len(found) > 1:
            msg = f"expected at most one row, found {len(found)}"
            raise ValueError(msg)
        return _as_artefacts(resolve(found))[0] if found else None

    def collect(self, *, annotations: bool = True) -> list[A]:
        """Resolve and return the matching artefacts.

        The expensive terminal: each match is loaded and its refs resolved. Use
        `urns`, `ids` or `frame` when identifiers or columns are enough — that
        is the 331x difference the milestone opens with.

        `annotations=False` skips the annotation read and leaves every
        `.annotations` empty. On a 579,951-code codelist that join and its
        grouping are **32% of the collect** — more than building the `Code`
        objects — for annotations most callers never look at.

        It is off by default and should stay off by default: a silently empty
        `.annotations` is wrong data, not fast data, and a caller who does not
        know to ask must not get it. Passing it is the caller saying they know.

        Supported on `q.code` and `q.hcode`, where annotations are a separate
        query. Other roots build their objects through readers that always read
        them, and **raise** rather than accept the flag and ignore it.

        Raises:
            TypeError: If nothing can build objects from this root's rows.
            ValueError: If `annotations=False` on a root that cannot honour it.
        """
        resolve = self._resolver("collect")
        rows = self._key_rows()
        return _as_artefacts(resolve(rows) if annotations else resolve(rows, annotations=False))

    @csv_errors
    def page(self, n: int) -> Page[R, A]:
        """The next `n` rows, and a cursor for the ones after them.

        The paging terminal. It selects the sort keys alongside the URNs
        because a cursor is made of them — which is why this is its own
        terminal rather than a flag on `urns()`.

        `Page.cursor` is `None` exactly when the page was short, meaning there
        is nothing after it. A *full* page always yields a cursor, even when it
        happened to be the last one; the alternative is fetching `n + 1` rows
        to find out, and a caller who cares learns it from the next empty page.

        Raises:
            ValueError: If `n` is not a positive int.
            SliceError: If the query is unordered. A page *is* a window, and a
                cursor is made of the sort keys — see `_refuse_unordered`.
        """
        self._refuse_unordered("page")
        check_limit(n)
        if n == 0:
            msg = "page() needs a positive size; a page of zero rows has no cursor to continue from."
            raise ValueError(msg)
        compiled, ascending = compile_page(
            self.root,
            self.predicates,
            urn_column=self._urn_column,
            order=self._order,
            qualify=self.qualifiers,
            after=self.cursor,
            limit=n,
            fetch_scope=self._fetch_scope,
        )
        rows = self._cursor().execute(compiled.sql, list(compiled.params)).fetchall()
        urns = tuple(str(row[0]) for row in rows)
        cursor = Cursor[R](tuple(rows[-1][1:]), ascending) if len(rows) == n else None
        return Page(urns=urns, cursor=cursor, resolve=self._resolve)

    @csv_errors
    def frame(self, *, labels: str | Sequence[str] = (), label_suffix: str = "_label") -> pl.DataFrame:
        """Every column of the root relation, as a `polars.DataFrame`.

        `labels` works as it does on a projection — see `Select.collect`. An
        observation frame's columns are named by component id and typed from
        the DSD (`_typed`): codes as their codelist's `pl.Enum`, values as
        their `Facet` declares.
        """
        compiled = self.to_sql()
        frame = _typed(frame_of(completed(self._cursor()).sql(compiled.sql, params=list(compiled.params))), self.root)
        if not labels:
            return frame
        return _labelled(frame, self.root, labels, label_suffix)

    def stream(self, chunk_rows: int = 10_000) -> pa.RecordBatchReader:
        """The same rows as `frame`, in Arrow batches instead of one frame.

        For a result too large to hold. **Measured, not assumed**, on 4M rows
        of three columns:

            collect()                     441 MB peak RSS
            stream(chunk_rows=1_000)        5 MB
            stream(chunk_rows=50_000)       5 MB
            stream(chunk_rows=500_000)     ~8 MB

        and the streamed figure does not move at 8M rows, where `collect()`
        roughly doubles. That is what "bounded" means here, and it is the
        reason to reach for this at all.

        **Measured against rows that are already in the store**, which is the
        local case and is not every case. A query served over HTTP fetches
        first, so the figures above describe the stream and not the fetch:

        | where the rows are | bounded? |
        | --- | --- |
        | a bundle, or any file-backed store | yes — the figures above |
        | fetched, into a file-backed `store=` | yes, after a full download |
        | fetched, into a `RestRegistry`'s own in-memory store | **no** — materialised in RAM first |

        The third row is the default when no `store=` is given, and `explain()`
        is what tells the two apart: it says whether a query fetches. Streaming
        a large remote result wants a `store=` on disk.

        **`chunk_rows` is not the knob it looks like.** What decides bounded
        from unbounded is whether the query *sorts*, because a sort has to
        materialise the whole result before the first batch can leave. Every
        query carries a key tiebreak by default — see `_is_ordered` — and while
        a stream still did, this cost 210 MB at 4M rows and 368 MB at 8M
        *whatever* `chunk_rows` said: 1,000 and 50,000 measured identically.
        Chunk size only starts to cost anything once it is large enough to be a
        buffer in its own right.

        So size `chunk_rows` for the **consumer** — a writer's preferred batch,
        an HTTP chunk — not for memory. 10k to 50k is a reasonable default and
        anything under a few hundred thousand costs nothing measurable.

        **Both spellings work, and that is the reader's doing rather than a
        choice made here.** `pa.RecordBatchReader` is an iterator *and* a
        context manager, so:

            for batch in query.stream():          # simple
                ...

            with query.stream() as batches:       # deterministic close
                for batch in batches:
                    ...

        The second is worth preferring for a stream abandoned part-way — a
        `break`, an exception, an HTTP client that hangs up. Without it the
        cursor is released whenever the reader is collected, which is soon but
        not defined.

        Arrow batches rather than polars frames because that is what a writer
        wants — CSV, parquet, an HTTP body — and `pl.from_arrow(batch)` is a
        cheap zero-copy step for a caller who wants the other thing (the
        `pl.DataFrame(batch)` constructor is ~2x slower). Converting
        here would force it on everyone.

        Args:
            chunk_rows: Target rows per batch. The last batch is short.

        Returns:
            A reader over the result. Nothing has run when it is returned;
            the first pull executes.
        """
        partitions = _partition_prefix(self.root, self._order)
        if partitions:
            by_partition = _stream_by_partition(self, partitions, chunk_rows, lambda narrowed: narrowed.to_sql())
            if by_partition is not None:
                return self._converting(by_partition)
        compiled = self.to_sql(ordered=self._is_ordered())
        # A cursor of its own, not the shared handle: two concurrent streams
        # on one connection would overwrite each other's row buffer (#75), and
        # the reader holds this one until it is exhausted or closed.
        cursor = own_handle(self._cursor())
        return self._converting(
            completed(cursor).sql(compiled.sql, params=list(compiled.params)).to_arrow_reader(chunk_rows)
        )

    def _converting(self, reader: pa.RecordBatchReader) -> pa.RecordBatchReader:
        """`reader`, converting a scanned file's read errors as it is drained (GH-247).

        Only a scan's rows are parsed while a stream runs, so any other query's
        reader is returned as it is.
        """
        return converting(reader, query=self) if reads_scan(self) else reader

    @csv_errors
    def _partition_values(self, partitions: tuple[str, ...]) -> list[tuple[object, ...]]:
        """Every partition, in key order — one small query before the stream.

        Read from the data rather than declared, because a binding says which
        *columns* partition a table and never which values exist. Cheap beside
        what follows: one `DISTINCT` over the partition columns.
        """
        columns = ", ".join(f"{ROOT_ALIAS}.{column}" for column in partitions)
        root = self.root
        table = f"{root.__schema__}.{root.__table__}" if root.__schema__ else root.__table__
        sql = f"SELECT DISTINCT {columns} FROM {table} {ROOT_ALIAS} ORDER BY {columns}"
        return [tuple(row) for row in self._cursor().execute(sql).fetchall()]

    def _narrowed_to(self, partitions: tuple[str, ...], values: tuple[object, ...]) -> Self:
        """This query, restricted to one partition."""
        root = self.root
        pinned = [
            Compare(Col((), root.__table__, column), "=", value)
            for column, value in zip(partitions, values, strict=True)
        ]
        return self._with(attrs.evolve(self.plan, predicates=(*self.plan.predicates, *pinned)))

    def preview(self, n: int = 5) -> pl.DataFrame:
        """A bounded head, for looking at a query in a REPL.

        `_window` rather than `limit`, so this still works on an unordered
        query: it reads once and shows what it got, which is what a REPL look
        is, rather than a page anything continues from.
        """
        return self._window(n).frame()

    # ── projection ────────────────────────────────────────────────────────

    def select(self, *columns: Col[R], **named: Col[R] | Agg[R]) -> Select[R, A]:
        """Project columns and aggregates, returning a `Select`.

        Positional columns take their own name; keyword arguments name theirs.
        A path crossing a to-many edge has no single value per row, so it must
        be collected — `.list()`, `.count()`, `.n_unique()` — and is refused
        otherwise with the alternatives named.

        Each aggregate compiles to a correlated subquery rather than a join, so
        several in one projection cannot multiply rows the way several joins
        would. They do each cost a pass, which is why `list()` takes several
        columns and returns structs instead of asking for one call per field.
        """
        for column in columns:
            _require_column("select", column)
        for name, item in named.items():
            if not isinstance(item, AnyCol | AnyAgg):
                _refuse_column("select", item, f"{name}=")
        items: list[tuple[str, AnyCol | AnyAgg]] = [(c.projected_name, c) for c in columns]
        items.extend((name, item) for name, item in named.items())
        self._refuse_to_many(items)
        _refuse_duplicate_names(items)
        if not items:
            msg = "select() needs at least one column or aggregate."
            raise TypeError(msg)
        return Select(query=self, items=tuple(items))

    def _refuse_to_many(self, items: Sequence[tuple[str, AnyCol | AnyAgg]]) -> None:
        """Refuse a projected path that fans out, here rather than at `collect`.

        Both halves of the page's "what gets refused" now refuse in the same
        place. `select(...).filter(...)` always did — `Select` has no `filter`,
        so it is a composition error Python catches — while a to-many
        projection returned a `Select` and raised only when the query ran. A
        caller building one interactively got one refusal immediately and the
        other minutes later, with no rule to predict which (#342).

        Nothing about it needs the database: the cardinality is a property of
        the declared edge graph, which `resolve_path` reads directly.

        Skipped when the root is not structural. An observation column belongs
        to a dataflow's own table, named by its binding rather than by
        `_edges`, so there is no declared path to resolve and the compiler
        stays the only checker for that plane.
        """
        root = self.root.__table__
        if root not in _accessor_roots():
            return
        for _name, item in items:
            if isinstance(item, AnyAgg | Computed) or not item.path:
                continue
            refuse_to_many_projection(root, tuple(item.path), spelled(tuple(item.path)))

    def group_by(self, *keys: Col[R]) -> Grouped[R, A]:
        """Group rows by `keys`; call `agg` or `len` on the result.

        **Not a refinement, and returns a different type for that reason.**
        `select` changes what a row is; `group_by` changes it further, into one
        row per distinct key. Neither is a narrowing, so neither belongs in the
        chain the refinement laws govern — which is the same reasoning that
        keeps `union` a free function.

        Two stages rather than one, because the keys and the aggregates answer
        different questions and the aggregates are only meaningful once the
        keys are fixed. `polars` splits it the same way.

            reg.query(q.dataflow).group_by(q.dataflow.agency).agg(
                n=q.dataflow.id.count(),
            ).collect()

        Raises:
            TypeError: If no keys are given.
            SliceError: If the query is ordered or sliced. Neither survives
                grouping — a row sort key that is not a group key is not valid
                SQL, and a limit applied *before* grouping would silently
                aggregate a truncated set. Group first, then order and slice
                the grouped result, which can also order by an aggregate.
        """
        if not keys:
            msg = (
                "group_by() needs at least one key. To aggregate over every row at once, use "
                ".agg(...) — several aggregates, one row — or .count() for the row count alone."
            )
            raise TypeError(msg)
        for key in keys:
            _require_column("group_by", key)
        if self.order_keys or self._sliced():
            msg = (
                "cannot group_by() a query that is ordered or sliced — both "
                "apply to rows, not groups. A row sort key that is not a group "
                "key is not valid SQL, and a limit before grouping aggregates a "
                "truncated set. Group first, then order and slice the result."
            )
            raise SliceError(msg)
        return Grouped(query=self, keys=tuple(keys))

    def agg(self, **named: Agg[R]) -> Select[R, A]:
        """Several aggregates over every row of the query, in one scan.

        One row out, one column per aggregate — `group_by(...).agg(...)` with
        no keys, which is why it is not spelled that way: `group_by()` refuses
        zero keys, and a row count alone is `count()`. The case this serves is
        *several* answers from one pass, e.g. the distinct values each
        dimension takes under a filter, plus the row count — what SDMX-REST's
        `/availability` returns:

            obs.agg(
                SEX=obs.col("SEX").unique(),
                TIME_PERIOD=obs.col("TIME_PERIOD").unique(),
                n=sl.len(),
            ).collect()

        Not always faster than one `select(col).unique()` per column. On
        the 2021 Census Profile bundle (519M rows, six dimensions) one scan lost 2.8x unfiltered
        and won 1.1-4.8x under a filter, so it is an option to take when a
        filter narrows the scan, not a replacement.

        An `Agg` here is a plain SQL aggregate over the query's rows — the same
        expression in `select()` is a correlated subquery over a fan-out.

        Raises:
            TypeError: If no aggregates are given, or one is a plain column —
                with no group keys there is no single value a column could
                take. Project it with `select()`, or `group_by()` it.
            SliceError: If the query is ordered or sliced. An order means
                nothing once every row folds into one, and a limit applied
                first would silently aggregate a truncated set.
        """
        if not named:
            msg = "agg() needs at least one aggregate. For the row count alone, use .count()."
            raise TypeError(msg)
        for name, item in named.items():
            if not isinstance(item, Agg):
                msg = (
                    f"{name!r} is a column, not an aggregate — agg() folds every row into one, so a "
                    f"column has no single value to give. Aggregate it (.unique(), .count(), .max(), …), "
                    f"or keep it per row with select(), or per group with group_by()."
                )
                raise TypeError(msg)
        if self.order_keys or self._sliced():
            msg = (
                "cannot agg() a query that is ordered or sliced — an order means nothing once "
                "every row folds into one, and a limit before aggregating aggregates a truncated "
                "set. Aggregate the whole query, then order or slice something else."
            )
            raise SliceError(msg)
        return Select(query=self, items=tuple(named.items()), aggregated=True)

    # ── no implicit execution ─────────────────────────────────────────────

    def __iter__(self) -> None:
        msg = (
            "a query is not iterable — it is a plan, and iterating would run it "
            "without saying so. Use .collect() for artefacts, or .ids() / "
            ".urns() / .frame() when identifiers or columns are enough."
        )
        raise TypeError(msg)

    def __contains__(self, item: object) -> bool:
        msg = (
            "a query has no `in` — it is a plan, not a collection, and a test "
            "would run SQL without saying so. Ask it: .filter(q.<root>.id == x).exists(), "
            "or .get(x) is not None (#511)."
        )
        raise TypeError(msg)

    def __len__(self) -> int:
        msg = (
            "a query has no len() — it is a plan, not a collection. Use "
            ".count(), which is one COUNT(*) and fetches no rows, or "
            "len(q.collect()) to materialise first."
        )
        raise TypeError(msg)

    @override
    def __repr__(self) -> str:
        where = f", {len(self.predicates)} predicate(s)" if self.predicates else ""
        windowed = f", {len(self.qualifiers)} qualifier(s)" if self.qualifiers else ""
        order = ", unordered" if self.unordered else (f", {len(self._order)} sort key(s)" if self._order else "")
        paged = ", after cursor" if self.cursor is not None else ""
        window = f", window={self.offset_n}:{self.limit_n}" if self._sliced() else ""
        return f"<Query {type(self.root).__name__} lazy{where}{windowed}{order}{paged}{window}>"

    def _sliced(self) -> bool:
        return self.plan.sliced()


@attrs.frozen
class Grouped[R: RowKind, A = object]:
    """A query with its group keys fixed, waiting for aggregates.

    Deliberately not runnable on its own: grouping without aggregating is
    `select(...).unique()`, which already exists and says so. Every way out of
    here produces a `Select`.
    """

    _query: Query[R, A] = attrs.field(alias="query")
    keys: tuple[Col[R], ...]

    def agg(self, **named: Agg[R] | Col[R]) -> Select[R, A]:
        """Aggregate each group, yielding the keys followed by the aggregates.

        Keyword-only, because an aggregate has no name of its own to fall back
        on the way a column does — `count()` over two different columns would
        collide, and positionally-named output columns are how a projection
        silently reorders under a caller.

        An `Agg` here is a plain SQL aggregate over the group's rows, where the
        same expression in `select()` is a correlated subquery over a fan-out.
        The expression says what to compute and the clause says over what.

        Raises:
            TypeError: If no aggregates are given.
        """
        if not named:
            msg = "agg() needs at least one aggregate. For a plain row count per group, use .len()."
            raise TypeError(msg)
        for name, item in named.items():
            if isinstance(item, Win):
                msg = (
                    f"agg({name}=…) got a window, which is computed per row, not per group (#511). "
                    "Select it from the ungrouped query, or aggregate here and rank afterwards."
                )
                raise TypeError(msg)
        # `projected_name`, not `column` — an observation key comes back as its
        # component id, exactly as `select()` and an unprojected frame do.
        # The physical name made grouping the one terminal still leaking
        # the binding's layout.
        items: list[tuple[str, AnyCol | AnyAgg]] = [(key.projected_name, key) for key in self.keys]
        items.extend(named.items())
        return Select(query=self._query, items=tuple(items), group_keys=self.keys, aggregated=True)

    def len(self) -> Select[R, A]:
        """Count rows per group, in a column named `len` — as polars names it.

        A method rather than a bare `count()` aggregate because it counts
        *rows*, not values of any column, so there is nothing to name it after.
        """
        # `projected_name`, not `column` — an observation key comes back as its
        # component id, exactly as `select()` and an unprojected frame do.
        # The physical name made grouping the one terminal still leaking
        # the binding's layout.
        items: list[tuple[str, AnyCol | AnyAgg]] = [(key.projected_name, key) for key in self.keys]
        items.append(("len", Agg[R]("count", ())))
        return Select(query=self._query, items=tuple(items), group_keys=self.keys, aggregated=True)

    @override
    def __repr__(self) -> str:
        by = ", ".join(key.column for key in self.keys)
        return f"<Grouped lazy by [{by}]>"


@attrs.frozen
class Page[R: RowKind, A = object]:
    """One page of results, and where the next one starts.

    Not a query — it has already run. So it extracts rather than refines, which
    is why it carries the dunders `Query` refuses: there is nothing left to
    push down.
    """

    urns: tuple[str, ...]
    #: `None` when the page was short, meaning nothing follows it.
    cursor: Cursor[R] | None
    _resolve: Resolver | None = attrs.field(eq=False, repr=False, alias="resolve")

    def collect(self) -> list[A]:
        """Resolve this page's rows to domain objects.

        Each URN is handed over as a one-tuple, which is exactly right for
        every root `page()` accepts: it needs a `urn` column, and on all of
        those the key *is* that column. Pinned by a test, since the day a root
        has a URN and a wider key this becomes silently wrong.

        Raises:
            TypeError: If nothing can build objects from this root's rows.
        """
        if self._resolve is None:
            msg = "this page's rows have no domain object to become. Use .urns()."
            raise TypeError(msg)
        return _as_artefacts(self._resolve([(urn,) for urn in self.urns]))

    def __len__(self) -> int:
        return len(self.urns)

    def __bool__(self) -> bool:
        return bool(self.urns)


class Resolver(Protocol):
    """Turns a root's key rows into domain objects.

    Registered per root by the registry, because building domain objects is
    `storage.readers`' job rather than the compiler's.

    `annotations` exists because the annotation read is a *separate query* for
    some roots and inseparable for others (#381). Roots that cannot skip it
    raise on `annotations=False` rather than accepting and ignoring it — a flag
    silently ignored leaves the caller measuring no improvement with no way to
    learn why.
    """

    def __call__(self, keys: Sequence[tuple[object, ...]], *, annotations: bool = True) -> Sequence[object]:
        """Build one object per key row, in the order the keys were given."""
        ...


@attrs.frozen
class Select[R: RowKind, A = object]:
    """A projected query. `collect` is the only executing call.

    Deliberately **not** filterable. `select` is not a row refinement — it
    changes what a row *is* — so a predicate added afterwards would be about
    the projection rather than about the relation, and the refinement laws say
    nothing about that. Filter first, then project. `query-composition.md`
    records this as one of the two rows that test whether the principle is
    real: both errors predate it and both fall out of it.
    """

    _query: Query[R, A] = attrs.field(alias="query")
    items: tuple[tuple[str, AnyCol | AnyAgg], ...]
    distinct: bool = False
    #: Non-empty when this frame came from `group_by`, in which case its
    #: aggregates are plain `GROUP BY` aggregates rather than correlated
    #: subqueries.
    group_keys: tuple[Col[R], ...] = ()
    #: True when every item is a plain aggregate over the query's rows — a
    #: `group_by().agg()` (with keys) or a root `Query.agg()` (without, #414).
    #: Separate from `group_keys` because zero keys is a real case: one row
    #: summarising the whole filtered query.
    aggregated: bool = False
    #: `HAVING` terms — predicates over the *groups*, not over the rows.
    having_terms: tuple[AggPred, ...] = ()

    #: Sort keys as `(1-based projected position, ascending)`.
    #:
    #: Positions rather than expressions because a projection can only be
    #: sorted by what it projected: the row it produces has no other columns,
    #: and an aggregate has no name until `agg` gives it one. Resolving at
    #: `order_by` time also means the refusal names the mistake — "not one of
    #: the projected columns" — rather than surfacing as a binder error.
    order: tuple[tuple[int, bool], ...] = ()

    def order_by(self, *keys: Col[R] | Agg[R] | Descending[Col[R]] | Descending[Agg[R]] | str) -> Self:
        """Sort the projection, replacing any order already set.

        Keys must be projected. The top-N shape is what this is for:

            reg.query(q.dataflow).group_by(q.dataflow.agency).agg(
                n=q.dataflow.id.count(),
            ).order_by(...).limit(3)

        Raises:
            TypeError: If a key is not one of the projected items.
        """
        resolved: list[tuple[int, bool]] = []
        for key in keys:
            item: object = key.key if isinstance(key, Descending) else key
            resolved.append((self._position_of(item), not isinstance(key, Descending)))
        return attrs.evolve(self, order=tuple(resolved))

    @staticmethod
    def _same_expression(item: object, projected: object) -> bool:
        """Whether two projected expressions denote the same thing.

        Same class *and* same canonical form. Without the class check a
        `Str` and an `Agg` that happened to canonicalise alike would match;
        with it, the two families are compared only against their own kind.
        """
        if type(item) is not type(projected):
            return False
        if isinstance(item, AnyCol) and isinstance(projected, AnyCol):
            return projected.__canonical__() == item.__canonical__()
        if isinstance(item, AnyAgg) and isinstance(projected, AnyAgg):
            return projected.__canonical__() == item.__canonical__()
        return False

    def _position_of(self, item: object) -> int:
        """1-based position of `item` in the projection.

        Three ways to name a projected item, in the order a caller reaches for
        them:

        * **identity** — they held the object they passed to `agg`;
        * **its output name** — `order_by("n")`, which is what SQL and polars
          both accept and the only spelling available when the projection was
          built somewhere else;
        * **structurally** — an equal column or aggregate, so writing the same
          expression twice works. It has to be structural because every
          accessor read builds a fresh object, and until `Agg.__canonical__`
          existed this covered columns only, which meant
          `select(n=count()).order_by(count())` failed while the identical
          program with the aggregate in a variable succeeded.
        """
        if isinstance(item, str):
            for position, (name, _) in enumerate(self.items, start=1):
                if name == item:
                    return position
        for position, (_, projected) in enumerate(self.items, start=1):
            if projected is item:
                return position
            if self._same_expression(item, projected):
                return position
        names = ", ".join(name for name, _ in self.items)
        if item is None:
            # `order_by(None)` is a *row*-order decision and a projection has
            # no row order of its own — it inherits the query's. Named here
            # rather than left to the generic message, which would say
            # "NoneType is not among (…)" and read as a typo.
            msg = (
                f"order_by(None) declines the row order, which belongs to the "
                f"query rather than to a projection of it. Call it before "
                f"select(): q.order_by(None).select({names})."
            )
            raise TypeError(msg)
        shown = f"{item!r}" if isinstance(item, str) else type(item).__name__
        msg = (
            f"order_by() on a projection takes one of its own columns, and "
            f"{shown} is not among them ({names}). A projected "
            f"row has no other columns to sort by; sort before select() to "
            f"order by one the projection drops."
        )
        raise TypeError(msg)

    def limit(self, n: int) -> Self:
        """Narrow to at most `n` rows — `Query.limit` over the projection."""
        return attrs.evolve(self, query=self._query.limit(n))

    def offset(self, n: int) -> Self:
        """Skip `n` rows — `Query.offset` over the projection."""
        return attrs.evolve(self, query=self._query.offset(n))

    def unique(self) -> Self:
        """Collapse duplicate rows — `SELECT DISTINCT`.

        **Full-row only, and takes no arguments.** A subset form fails twice
        over: `filter(keep).unique(agency)` and `unique(agency).filter(keep)`
        return different rows, so it would not commute with `filter`, and it
        leaves *which* row of each group survives unspecified. Excluding it
        keeps `unique` in the commuting set rather than adding a carve-out to
        the third composition law. A caller wanting one row per group has
        `group_by(...).agg(...)`, which says which one.

        It lives on the projection rather than on `Query` because the rows of a
        keyed relation are already distinct — `unique()` before `select()`
        would always be a no-op.
        """
        return attrs.evolve(self, distinct=True)

    def having(self, *predicates: AggPred) -> Self:
        """Keep only the groups satisfying `predicates` — SQL's `HAVING`.

        The clause `filter` cannot be. `filter` asks about a *row* and
        `group_by` has replaced rows with groups, so a row predicate is no
        longer meaningful — which is what `filter` refuses and should keep
        refusing. A predicate about the group is a different question and had
        no spelling at all:

            reg.query(q.dataflow).group_by(q.dataflow.agency).agg(
                n=q.dataflow.id.count(),
            ).having(q.dataflow.id.count() > 10).collect()

        Note the aggregate is written out again rather than referred to by its
        output name. That is deliberate: `n` is a label on the result, while
        `HAVING` runs *before* the result exists, and SQL dialects disagree
        about whether the alias is visible there. Writing the aggregate says
        what is computed without depending on that.

        **Only meaningful after `group_by`.** An ungrouped projection has one
        row per root row, and an aggregate over it is a correlated subquery the
        root's own `filter` already accepts — `filter(q.codelist.codes.count()
        > 3)` — so there is nothing `having` would add and a second spelling
        would only be a way to get it wrong.

        Terms accumulate and are ANDed, as `filter` does on `Query`, so
        `.having(a).having(b)` and `.having(a, b)` are the same query.
        """
        if not self.group_keys:
            msg = (
                "having() needs a group_by(): it asks about groups, and an unprojected "
                "row has none. An aggregate over the root's own fan-out is a row "
                "predicate — put it in filter() before select(), e.g. "
                "filter(q.codelist.codes.count() > 3)."
            )
            raise TypeError(msg)
        for predicate in predicates:
            if not isinstance(predicate, AggPred):
                got = type(predicate).__name__
                bad = (
                    f"having() takes a comparison against an aggregate, got {got}. "
                    f"Write the aggregate and compare it — `q.dataflow.id.count() > 10` — "
                    f"not a plain column predicate, which belongs in filter() before select()."
                )
                raise TypeError(bad)
        return attrs.evolve(self, having_terms=(*self.having_terms, *predicates))

    def filter(self, *_: object) -> Never:
        """Always raises. Filter before projecting.

        `having()` is the grouped counterpart — see there for why the two are
        different questions rather than two spellings of one.
        """
        msg = (
            "cannot filter() a projected query: select() changes what a row "
            "is, so a predicate afterwards would be about the projection "
            "rather than the relation. Filter first, then select()."
        )
        raise TypeError(msg)

    @property
    def columns(self) -> tuple[str, ...]:
        """The projected names, in order — the `SqlRelation` contract.

        Known without asking the database: a projection names its own columns.
        """
        return tuple(name for name, _ in self.items)

    @property
    def source(self) -> object:
        """The database this frame reads — `sdmxlib.sql.HasSource`.

        On `Select` rather than only on `Query` because a frame is what
        `is_in` is handed: `select()` is what turns a query into a
        single-column relation, so this is the object whose source a consumer
        needs (§5.9).
        """
        return self._query.source

    @property
    def source_label(self) -> str:
        """How to name this frame's database in an error. See `Query`."""
        return self._query.source_label

    def to_sql(self, *, ordered: bool = True) -> CompiledQuery:
        """Compile without executing.

        `ordered=False` drops the `ORDER BY`, for `stream` — see there for why
        an unordered stream is the difference between bounded and unbounded
        memory.
        """
        # A grouped *or* distinct frame drops the row-level **default** order:
        # the natural order of a code is `position`, which is neither a group
        # key nor a projected column, so carrying it in is a binder error
        # rather than a preference. An order the caller *asked* for is a
        # different matter — `group_by` refuses it earlier, and `unique()`
        # reports it through `_check_distinct_order`, which names the fix.
        #
        # `unique()` was missing from this for the same reason it is easy to
        # miss: the two clauses fail identically and only `group_by` had been
        # written down. Without it, `cl.codes.select(...).unique()` was
        # impossible — the default order made it refuse, and `order_by()` with
        # no arguments clears the caller's keys and falls back to that same
        # default, so there was no spelling that worked.
        row_order = self._query.order_keys if (self.aggregated or self.distinct) else self._query._order
        if self._query.unordered:
            # Drops the grouped and `unique()` tiebreaks along with the row
            # order, which is the point: those exist so groups come back in a
            # stable order, and this is the caller saying they do not need one.
            row_order = None
        if not ordered:
            row_order = None
        compiled = compile_projection(
            self._query.root,
            self._query.predicates,
            self.items,
            order=row_order,
            distinct=self.distinct,
            qualify=self._query.qualifiers,
            group_keys=self.group_keys,
            aggregated=self.aggregated,
            having=self.having_terms,
            positional_order=self.order,
            fetch_scope=self._query._fetch_scope,
        )
        return compiled._replace(sql=compiled.sql + self._query._window_sql())

    def explain(self) -> str:
        """A human-readable plan. Runs nothing.

        **Says when rows are fetched first, because the SQL alone does not.**
        A query served over HTTP compiles to an ordinary `SELECT` over the
        store it will be materialised into — so without this line the plan
        reads as a local table scan, and the request that fills that table is
        invisible. The distinction is worth a line: it is the difference
        between reading rows that are already here and downloading them.
        """
        compiled = self.to_sql()
        binds = ", ".join(repr(p) for p in compiled.params) or "none"
        # A Select has no prepare of its own; it delegates to the query it projects.
        fetches = self._query._prepare is not None
        fetch = "\n-- fetches before running; the SQL below is over materialised rows" if fetches else ""
        return f"{compiled.sql}\n-- binds: {binds}{fetch}"

    @csv_errors
    def collect(self, *, labels: str | Sequence[str] = (), label_suffix: str = "_label") -> pl.DataFrame:
        """Execute and return a `polars.DataFrame`. The only executing call.

        `labels` attaches a human-readable column per enumerated component and
        language — `REF_AREA_label_en`. **The language is always in the name**,
        including for one, so adding a second later does not rename the first
        and break whatever read it. `label_suffix` renames the middle part:
        `label_suffix="_name"` gives `REF_AREA_name_en`.

        A column projected under an alias is labelled under that alias — the
        name in the frame is the name a caller reads.

        Args:
            labels: One or more BCP-47 tags. Empty attaches nothing.
            label_suffix: Between the column and the language.

        Raises:
            TypeError: If labels are asked of a query with no DSD — one built
                from a store rather than a catalog.
            ValueError: If no projected column is enumerated, so labelling
                would add nothing. See `_labelled`.
        """
        compiled = self.to_sql()
        cursor = self._query._cursor(self._query._projection_columns(self.items))
        # component id -> the name it was projected under, so an aliased column
        # is typed and labelled under the alias.
        aliases = {item.projected_name: name for name, item in self.items if isinstance(item, AnyCol)}
        frame = _as_utc(frame_of(completed(cursor).sql(compiled.sql, params=list(compiled.params))), self.items)
        frame = _typed(frame, self._query.root, aliases)
        if not labels:
            return frame
        return _labelled(frame, self._query.root, labels, label_suffix, aliases)

    def stream(self, chunk_rows: int = 10_000) -> pa.RecordBatchReader:
        """The same rows as `collect`, in Arrow batches instead of one frame.

        For a result too large to hold. **Measured, not assumed**, on 4M rows
        of three columns:

            collect()                     441 MB peak RSS
            stream(chunk_rows=1_000)        5 MB
            stream(chunk_rows=50_000)       5 MB
            stream(chunk_rows=500_000)     ~8 MB

        and the streamed figure does not move at 8M rows, where `collect()`
        roughly doubles. That is what "bounded" means here, and it is the
        reason to reach for this at all.

        **Measured against rows that are already in the store**, which is the
        local case and is not every case. A query served over HTTP fetches
        first, so the figures above describe the stream and not the fetch:

        | where the rows are | bounded? |
        | --- | --- |
        | a bundle, or any file-backed store | yes — the figures above |
        | fetched, into a file-backed `store=` | yes, after a full download |
        | fetched, into a `RestRegistry`'s own in-memory store | **no** — materialised in RAM first |

        The third row is the default when no `store=` is given, and `explain()`
        is what tells the two apart: it says whether a query fetches. Streaming
        a large remote result wants a `store=` on disk.

        **`chunk_rows` is not the knob it looks like.** What decides bounded
        from unbounded is whether the query *sorts*, because a sort has to
        materialise the whole result before the first batch can leave. Every
        query carries a key tiebreak by default — see `_is_ordered` — and while
        a stream still did, this cost 210 MB at 4M rows and 368 MB at 8M
        *whatever* `chunk_rows` said: 1,000 and 50,000 measured identically.
        Chunk size only starts to cost anything once it is large enough to be a
        buffer in its own right.

        So size `chunk_rows` for the **consumer** — a writer's preferred batch,
        an HTTP chunk — not for memory. 10k to 50k is a reasonable default and
        anything under a few hundred thousand costs nothing measurable.

        **Both spellings work, and that is the reader's doing rather than a
        choice made here.** `pa.RecordBatchReader` is an iterator *and* a
        context manager, so:

            for batch in query.stream():          # simple
                ...

            with query.stream() as batches:       # deterministic close
                for batch in batches:
                    ...

        The second is worth preferring for a stream abandoned part-way — a
        `break`, an exception, an HTTP client that hangs up. Without it the
        cursor is released whenever the reader is collected, which is soon but
        not defined.

        Arrow batches rather than polars frames because that is what a writer
        wants — CSV, parquet, an HTTP body — and `pl.from_arrow(batch)` is a
        cheap zero-copy step for a caller who wants the other thing (the
        `pl.DataFrame(batch)` constructor is ~2x slower). Converting
        here would force it on everyone.

        Args:
            chunk_rows: Target rows per batch. The last batch is short.

        Returns:
            A reader over the result. Nothing has run when it is returned;
            the first pull executes.
        """
        partitions = _partition_prefix(self._query.root, self._query._order)
        if partitions:
            by_partition = _stream_by_partition(
                self._query,
                partitions,
                chunk_rows,
                # A projection has to be re-applied over the narrowed query;
                # a plain one has nothing to re-apply, which is the whole of
                # the difference between the two callers.
                lambda narrowed: attrs.evolve(self, query=narrowed).to_sql(),
            )
            if by_partition is not None:
                return self._query._converting(by_partition)
        compiled = self.to_sql(ordered=self._query._is_ordered())
        # A cursor of its own, not the shared handle: two concurrent streams
        # on one connection would overwrite each other's row buffer (#75), and
        # the reader holds this one until it is exhausted or closed.
        cursor = own_handle(self._query._cursor(self._query._projection_columns(self.items)))
        return self._query._converting(
            completed(cursor).sql(compiled.sql, params=list(compiled.params)).to_arrow_reader(chunk_rows)
        )

    @override
    def __repr__(self) -> str:
        names = ", ".join(name for name, _ in self.items)
        unique = " unique" if self.distinct else ""
        return f"<Select lazy{unique} [{names}]>"


def union[R: RowKind, A](first: Query[R, A], *rest: Query[R, A]) -> Query[R, A]:
    """Every row matched by any of the given queries.

    **A free function, never a method**, and that is the refinement principle
    rather than style. Union *widens*: `helper_scoped_to_estat.union(all_of_it)`
    would undo the helper's scoping, which is precisely what the principle
    exists to prevent. As a function it composes its arguments into a third
    query instead of refining either — the same reasoning that keeps `group_by`
    off the refinement surface.

    The result is a fresh query over the shared relation, carrying one
    membership predicate and none of the arguments' own clauses. Their
    predicates, orders and slices are inside the compiled subqueries, so each
    contributes exactly the rows it matched — including rows the others
    excluded, which a method could not express.

    Args:
        first: The query whose root the result takes.
        rest: Further queries over the same relation. Calling with none returns
            `first` unchanged.

    Raises:
        TypeError: If any query is rooted at a different relation.
        CrossSourceError: If any query reads a different database. `intersect`
            and `difference` refused this from the start and `union` did not,
            because the check lives in `_membership` and `union` does not go
            through it — the compiled subquery is spliced into *this*
            connection either way, where the same table names resolve to this
            database's rows. Measured: two registries holding disjoint
            codelists, `union` returned only the near side's, having silently
            dropped every row the far side contributed (§5.9).
    """
    if not rest:
        return first
    branches: list[str] = []
    params: list[object] = []
    for query in (first, *rest):
        first._require_same_root(query)
        first._require_same_source(query)
        sql, binds = first._key_select(query)
        branches.append(sql)
        params.extend(binds)
    # Each branch is parenthesised. A branch may carry its own `ORDER BY` and
    # `LIMIT` — an operand's slice is honoured — and `SELECT ... LIMIT 1 UNION
    # SELECT ...` is a parse error, not a different reading. `intersect` never
    # meets this because it has one branch and no `UNION` to bind tighter than.
    membership: Pred[R] = RowIn(
        first.root.__key__,
        "\nUNION\n".join(f"({branch})" for branch in branches),
        tuple(params),
    )
    return Query[R, A](
        plan=Plan(root=first.root, predicates=(membership,)),
        conn=first._conn,
        resolve=first._resolve,
        source=first.source,
        source_label=first._source_label,
    )


def build[R: RowKind, A](
    root: Cols[R, A],
    conn: duckdb.DuckDBPyConnection,
    resolve: Resolver | None,
    *,
    predicates: Sequence[Pred[R]] = (),
    default_order: Sequence[OrderKey] = (),
    prepare: Callable[[Plan[R], frozenset[tuple[str, str]] | None], None] | None = None,
    source: object = None,
    source_label: str | None = None,
) -> Query[R, A]:
    """Construct a query. The entry point registries call.

    `default_order` states the relation's *natural* order — what the rows mean
    when nobody has sorted. A parameter rather than something a caller evolves
    on afterwards: `LazyCodelist` used to reach in with
    `attrs.evolve(query, default_order=...)`, which only worked while the plan
    and the binding were the same object.
    """
    return Query(
        # `Plan` carries no `A`; `scanned` erases it — see its docstring.
        plan=Plan(
            root=scanned(root),
            predicates=tuple(predicates),
            default_order=tuple(default_order),
        ),
        conn=conn,
        resolve=resolve,
        prepare=prepare,
        source=source,
        source_label=source_label,
    )
