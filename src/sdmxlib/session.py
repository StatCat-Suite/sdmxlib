"""The execution handle: one DuckDB session per thread, per store (#300).

A **database session** — connection-scoped state — and deliberately *not* a
unit of work. There is no identity map and no transaction bookkeeping here, so
a reader arriving from SQLAlchemy's ORM `Session` should discard that meaning.
The word is DuckDB's own for this scope::

    SET SESSION default_null_order='NULLS FIRST'
      this handle    -> NULLS_FIRST
      sibling handle -> NULLS_LAST

One handle is one session, and the state it holds — ``search_path``, settings,
registered relations, temp tables — is exactly what that statement scopes.

## Why not "cursor"

`DuckDBPyConnection.cursor()` returns another `DuckDBPyConnection`, not a
distinct cursor type, and the object carries most of the DBAPI cursor surface
(``execute``, ``fetchone``, ``description``, ``rowcount``). A Python reader
therefore imports DBAPI expectations — created per statement, cheap, discarded
— and ours is the opposite: one per thread, long-lived, stateful. Same word,
opposite lifetime. `duckdb_conn.cursor()` also returns a *fresh* handle on
every call while this returns the thread's existing one, so the spelling would
collide on behaviour as well as on lifetime.

## Why this is scoped rather than a property

The obvious API is a thread-local ``registry.connection``. It cannot be:
``registry.connection`` is a *stable, capturable* object and the codebase
depends on that — ``DuckDBDataStore.from_registry`` stores it in a field. A
thread-local property would let a store built on one thread execute on another
thread's handle, which is the unsafe sharing #75/#80 removed.

So the two stay separate, distinguished by whether holding one is correct:

| | ``registry.connection`` | this session |
| --- | --- | --- |
| lifetime | the store's | the thread's |
| capture into a field | correct | **a bug** |
| pass across threads | yes, as a base to resolve from | no |

A property cannot say "do not hold this". A ``with`` block can.
"""

import threading as _threading
import weakref as _weakref
from collections.abc import Callable, Generator, Iterable, Sequence
from contextlib import contextmanager
from uuid import uuid4

import attrs
import duckdb
import polars as pl
import pyarrow as pa

#: `cursor_for` is deliberately absent: it hands out an *unmanaged* handle —
#: no scope, no release — and is package-internal API for `query/` and
#: `storage.lazy`, which must resolve the handle at execution and cannot wrap
#: themselves in a block. This module is public, so `__all__` is where that
#: distinction gets stated (CLAUDE.md rule 4, and the contract #265's guard
#: keys on). Importing it still works; it is just not part of the surface.
#: `completed` is absent for the same reason: package-internal, for every
#: module that calls `sql`, `register` or `unregister` on a handle; `frame_of` too.
__all__ = ["Session", "SessionFactory", "SessionHook", "ValueSet"]

#: What every per-call value relation's name starts with (`value_match`,
#: `Session.value_set`). Each name is random, so a SQL text naming one never
#: repeats — `storage._parsed` keys on the prefix to keep such texts out.
VALUES_RELATION_PREFIX = "_sl_values_"

#: Per-thread, per-connection handle cache. Weak keys so closing a store lets
#: the entry go without an explicit cleanup hook — no stale handles after id
#: reuse.
type _HandleTable = _weakref.WeakKeyDictionary[duckdb.DuckDBPyConnection, duckdb.DuckDBPyConnection]

_thread_local = _threading.local()

#: A setup callback. The return value is ignored — the natural spelling is
#: ``lambda handle: handle.execute("SET ...")``, and `execute` returns the
#: handle, so demanding ``None`` would force every caller into a statement
#: body for no gain.
type SessionHook = Callable[[duckdb.DuckDBPyConnection], object]


#: Above this many values, a lookup registers an Arrow relation instead of
#: binding. Measured on `sdmx.code`, one filtered SELECT, best of 20:
#:
#:      n        = ANY(?)      registered
#:      1          0.26 ms       0.41 ms
#:     10          0.71 ms       0.41 ms
#:    100          5.34 ms       0.64 ms
#:   2000         99.16 ms       0.85 ms
#:  10000        494.12 ms       2.10 ms
#:
#: The bind path is linear and the registered path is very nearly flat, which
#: is the shape the trick rests on. The *rate* depends on whether pandas is
#: installed, by 40-60x: with pandas absent DuckDB retries ``import pandas``
#: twice per bound value and nothing remembers the failure (#411), so binding
#: costs ~80-110 us a value; with pandas importable it is ~1-2 us. The figures
#: above are the pandas-absent case, which is sdmxlib's own (pandas is not a
#: dependency) and the one this threshold is set for. See `Session.value_set`
#: for both curves. `Session.value_set` is the caller-facing version of the
#: same trick; this is the one the library owes itself, and not using it cost
#: `Query.collect()` 1.2 s over 10k codes.
_REGISTER_ABOVE = 8


@contextmanager
def value_match(cur: duckdb.DuckDBPyConnection, values: Sequence[object]) -> Generator[tuple[str, list[object]]]:
    """A SQL fragment matching *values*, and the parameters it needs.

    Yields `(fragment, params)` where the fragment completes a `WHERE <col>` —
    either `= ANY(?)` for a handful or `IN (SELECT ...)` over a registered
    Arrow relation for a real set.

    The column type is left to pyarrow rather than assumed: annotation ids are
    `BIGINT` and urns are `VARCHAR`, and coercing the first to text produced a
    binder error rather than a wrong answer — the good failure, but a failure.

    The relation is named per call and dropped on exit, for the reasons
    `Session.value_set` gives: a fixed name lets two callers clobber each
    other, and a registration that outlives its query is a leak.
    """
    listed = list(values)
    if len(listed) <= _REGISTER_ABOVE:
        yield "= ANY(?)", [listed]
        return
    name = f"{VALUES_RELATION_PREFIX}{uuid4().hex[:12]}"
    completed(cur).register(name, pa.table({"v": listed}))
    try:
        yield f"IN (SELECT v FROM {name})", []
    finally:
        completed(cur).unregister(name)


def cursor_for(conn: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    """Return this thread's handle for ``conn``, creating it on first call.

    **The** handle. Until #300 there were two of these — one here and one in
    `sdmxlib.storage.lazy` — each with its own thread-local cache, so a single
    thread held two handles that shared no session state. Nothing depended on
    them agreeing, which is why it went unnoticed until #271 needed to register
    a relation and found it invisible to half the query paths.

    Long-lived per thread rather than per call: DuckDB reuses one default-cursor
    row buffer per connection, so concurrent ``execute()`` on the same handle
    overwrite each other's results (#75), and creating a handle per call would
    pay ``cursor()`` on every query (#80).

    Handles from one connection do run concurrently — measured at 1.32x for
    four parallel queries against a single one — so this costs safety only, not
    parallelism.
    """
    table: _HandleTable | None = getattr(_thread_local, "table", None)
    if table is None:
        table = _weakref.WeakKeyDictionary()
        _thread_local.table = table
    cur = table.get(conn)
    if cur is None:
        cur = conn.cursor()
        table[conn] = cur
        _apply_hooks(conn, cur)
    return cur


def completed(handle: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    """``handle``, with any unfinished `execute()` result on it abandoned.

    Every `sql`, `register` and `unregister` call on a handle goes through
    this. DuckDB 2.0 refuses those — and any read of a relation — while the
    handle holds an `execute()` result nobody finished, and `fetchone()` never
    finishes one, even on a one-row result: only a fetch that comes back empty,
    `fetchall()`, or `complete()` does. A thread's handle is shared by every
    read on that thread, so the `fetchone()` that strands a result and the
    `sql()` it then breaks are usually in unrelated modules — `key_counts`
    and `Select.collect` were the first pair.

    `execute()` itself abandons a pending result, as DB-API does and as every
    call did on 1.5; this gives the relation calls the same rule rather than
    making each of forty reads remember to finish. A result still being read
    when a relation starts is lost — as it already was to the next `execute()`.

    Args:
        handle: The handle about to run a relation call.

    Returns:
        ``handle`` itself, so the call reads ``completed(cur).sql(...)``.
    """
    handle.complete()
    return handle


def frame_of(relation: duckdb.DuckDBPyRelation) -> pl.DataFrame:
    """``relation``'s rows as a polars frame.

    `pl()` with its type recovered: polars is optional to DuckDB, so its stub
    returns `Any`, and that `Any` rode unchecked into `_typed` at two sites.

    Args:
        relation: The relation to run.

    Returns:
        Its rows, all of them.

    Raises:
        TypeError: If `pl()` returned something other than a `DataFrame`.
    """
    frame = relation.pl()
    if not isinstance(frame, pl.DataFrame):
        msg = f"DuckDB's pl() returned {type(frame).__name__}, not a polars DataFrame"
        raise TypeError(msg)
    return frame


def _release(conn: duckdb.DuckDBPyConnection) -> None:
    """Drop this thread's handle for ``conn``. The next call builds a fresh one."""
    table: _HandleTable | None = getattr(_thread_local, "table", None)
    if table is not None:
        table.pop(conn, None)


#: Setup callbacks per base connection, and the handles already initialised.
#: Keyed weakly so a closed store's hooks go with it.
_hooks: _weakref.WeakKeyDictionary[duckdb.DuckDBPyConnection, list[SessionHook]] = _weakref.WeakKeyDictionary()
#: Initialised handles, **per base connection**. Per-base rather than one
#: global set: the first draft kept a single set and a newly opened store's
#: default hook was applied to every handle in the process, including ones
#: belonging to already-closed connections — 23 tests failed with "Connection
#: already closed". A hook belongs to one store and must only reach that
#: store's handles.
_handles: _weakref.WeakKeyDictionary[duckdb.DuckDBPyConnection, _weakref.WeakSet[duckdb.DuckDBPyConnection]] = (
    _weakref.WeakKeyDictionary()
)
#: The base connection a handle derives from, for a handle that is itself the
#: parent of further handles: a store's per-thread read cursor, from which a
#: query takes its own handle. Its hooks are the base's (`adopt`).
_bases: _weakref.WeakKeyDictionary[duckdb.DuckDBPyConnection, _weakref.ref[duckdb.DuckDBPyConnection]] = (
    _weakref.WeakKeyDictionary()
)
_hook_lock = _threading.Lock()


def _base_of(handle: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    """The base `handle` derives from, or `handle` itself; call it holding `_hook_lock`.

    Held weakly, so a handle never keeps a closed store's connection alive.
    """
    ref = _bases.get(handle)
    base = ref() if ref is not None else None
    return handle if base is None else base


def own_handle(conn: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    """A new handle on `conn`, shared with nothing, with the hooks of `conn`'s base run on it.

    For a reader that holds its handle until it is exhausted, such as an Arrow
    stream: the thread's shared handle (`cursor_for`) would have its row buffer
    overwritten by the next query on the thread.
    """
    handle = conn.cursor()
    _apply_hooks(conn, handle)
    return handle


def adopt(base: duckdb.DuckDBPyConnection, handle: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    """Record `handle` as derived from `base`, so a handle taken from it runs `base`'s hooks too.

    A DuckDB cursor shares no session state with the connection it came from.
    A store's read cursor is one, and a query on it takes a handle of its own
    with `cursor_for`; without this that handle would miss the store's hooks,
    such as the ``USE`` that points it at a bundle in a shared engine.

    Returns:
        `handle`, for chaining.
    """
    with _hook_lock:
        _bases[handle] = _weakref.ref(_base_of(base))
    return handle


def _apply_hooks(base: duckdb.DuckDBPyConnection, handle: duckdb.DuckDBPyConnection) -> None:
    with _hook_lock:
        base = _base_of(base)
        _bases[handle] = _weakref.ref(base)
        fns = list(_hooks.get(base, ()))
        _handles.setdefault(base, _weakref.WeakSet()).add(handle)
    for fn in fns:
        fn(handle)


def on_connect(base: duckdb.DuckDBPyConnection, fn: SessionHook) -> None:
    """Run *fn* against every handle for ``base`` — existing and future.

    The `sqlalchemy.PoolEvents.connect` equivalent, and the reason #300 is not
    just a deduplication: without it there is nowhere to put anything that must
    hold for *every* handle. That absence is why the handles disagreed about
    ``search_path`` — one had it set at schema creation and the others never
    did.
    """
    with _hook_lock:
        _hooks.setdefault(base, []).append(fn)
        existing = list(_handles.get(base, ()))
    for handle in existing:
        fn(handle)


def handle_count(base: duckdb.DuckDBPyConnection) -> int:
    """Live handles derived from ``base``. Observability, for leak hunting."""
    return len(_handles.get(base, ()))


@attrs.frozen
class ValueSet:
    """A caller-supplied set of values, registered as a relation.

    Satisfies `sdmxlib.sql.SqlRelation`, so it goes straight into
    `is_in` with no change to the DSL — the seam accepts a relation. What was
    missing was a way to turn a plain Python list into one.

    Obtained from `Session.value_set`, and valid only inside that block.
    """

    #: The registered relation's name. Generated, so two value sets never
    #: collide even within one session.
    name: str

    @property
    def columns(self) -> tuple[str, ...]:
        """One column. `is_in` checks arity against this."""
        return ("value",)

    def to_sql(self) -> tuple[str, list[object]]:
        """The relation, with **no binds at all**.

        That is the entire point: the values live in the registered Arrow
        table, not in the parameter list. See `Session.value_set`.
        """
        return f"SELECT value FROM {self.name}", []


class Session:
    """A scoped execution handle. Valid only inside its ``with`` block.

    Obtained from `SessionFactory.session`, normally as ``registry.session()``.
    Do not store one: the point of the scope is that the handle's lifetime is
    visible, and a `Session` outliving its block refers to a handle that may
    have been released or handed to another user of the thread.
    """

    __slots__ = ("_handle",)

    def __init__(self, handle: duckdb.DuckDBPyConnection) -> None:
        self._handle = handle

    @property
    def handle(self) -> duckdb.DuckDBPyConnection:
        """The raw DuckDB handle, for calls this class does not wrap.

        Escape hatch within an escape hatch. Same rule as the `Session` itself:
        use it inside the block, do not keep it.
        """
        return self._handle

    def execute(self, sql: str, params: list[object] | None = None) -> duckdb.DuckDBPyConnection:
        """Run *sql* on this session's handle.

        Args:
            sql: The statement.
            params: Bind parameters. Values only — never interpolate them into
                *sql*, per the SQL style rule in CLAUDE.md.

        Returns:
            The handle, positioned on the result, so ``.fetchall()`` and
            friends chain as they would on a raw connection.
        """
        return self._handle.execute(sql, params) if params is not None else self._handle.execute(sql)

    @contextmanager
    def value_set(self, values: Iterable[object]) -> Generator[ValueSet]:
        """Register *values* as a relation for the duration of the block.

        An eager ``is_in`` binds the list as one parameter (``= ANY(?)``), and
        DuckDB's Python client converts its elements one at a time. Registering hands it a pointer to
        an Arrow buffer instead, so the bind path is linear in the number of
        values and the registered path is very nearly flat::

            with reg.session() as active, active.value_set(ids) as wanted:
                codes.filter(sl.code.id.is_in(wanted)).collect()

        **How linear depends on whether pandas is installed.** Converting each
        bound value, DuckDB checks whether it is ``pandas.NaT`` or
        ``pandas.NA``, and with pandas absent that check retries
        ``import pandas`` — twice per value, every time, because DuckDB does
        not remember the failure. Median of 7 on a 200k-row table,
        duckdb 1.5.5:

        N        bound, no pandas    bound, pandas    registered
        10            5.4 ms             4.1 ms          6.5 ms
        100          13.7 ms             4.3 ms          5.4 ms
        500          43.6 ms             5.0 ms          5.5 ms
        1000         85.6 ms             5.4 ms          5.5 ms
        5000          445 ms            10.0 ms          6.5 ms
        20000        1635 ms            26.6 ms         10.1 ms
        50000        4121 ms            59.1 ms         13.6 ms

        **Reach for this at about a hundred values without pandas, about a
        thousand with it.** sdmxlib does not depend on pandas, so the first
        column is the default; below the crossover a plain list is as fast and
        simpler. The *shape* is the same in both and is what the feature rests
        on — linear against flat, so the gap keeps widening.

        Re-measure before quoting these: they are properties of a dependency
        and its environment, not of this library. The history shows why —
        one measurement found ~48 us a value, a later re-measure ~1.9 us and called
        DuckDB "25x faster", and the difference was pandas being importable in
        one run and not the other, not a DuckDB change.

        **Why the caller owns the window.** A registered relation is session
        state: it outlives the query, a fixed name would let two callers clobber
        each other, and a lazily-built query separates "register" from "execute"
        with no safe moment to free in between. Scoping it to a block removes
        all three — the name is generated, the relation exists only inside, and
        ``finally`` frees it even on error.

        **Inside the block, `to_sql` is self-contained.** The relation is on
        this session's handle, which is the handle the DSL uses, so a compiled
        query naming it can be handed to another library and executed — the
        case an automatic threshold could never serve, because the values would
        be gone before the SQL was.

        Args:
            values: The set. Order and duplicates are irrelevant; `is_in`
                treats it as a set and `canonical_key` canonicalises it.

        Yields:
            A `ValueSet` to pass to `is_in`.
        """
        name = f"{VALUES_RELATION_PREFIX}{uuid4().hex[:12]}"
        completed(self._handle).register(name, pa.table({"value": list(values)}))
        try:
            yield ValueSet(name)
        finally:
            completed(self._handle).unregister(name)


class SessionFactory:
    """Hands out this thread's `Session` for one base connection.

    Mixed into `sdmxlib.local.Registry` and `DuckDBDataStore` rather than
    instantiated directly.
    """

    __slots__ = ()

    def _session_base(self) -> duckdb.DuckDBPyConnection:
        """The stable connection sessions are derived from."""
        raise NotImplementedError

    @contextmanager
    def session(self) -> Generator[Session]:
        """This thread's session, for the duration of the block.

        Nesting is refcounted: a helper that opens a session inside a caller's
        session shares the handle and must not release it on the way out. Only
        the outermost block releases. `sqlalchemy.orm.scoped_session` has the
        same hazard and the same answer.

        Yields:
            The `Session`.
        """
        base = self._session_base()
        depth: dict[int, int] | None = getattr(_thread_local, "depth", None)
        if depth is None:
            depth = {}
            _thread_local.depth = depth
        key = id(base)
        depth[key] = depth.get(key, 0) + 1
        try:
            yield Session(cursor_for(base))
        finally:
            depth[key] -= 1
            if depth[key] == 0:
                del depth[key]
                _release(base)

    def execute(self, sql: str, params: list[object] | None = None) -> list[tuple[object, ...]]:
        """Run one statement on this thread's handle and return its rows.

        For the common case where a caller wants a single custom query and no
        handle. Nothing is exposed to capture, which is the point.

        Args:
            sql: The statement.
            params: Bind parameters. Values only.

        Returns:
            All rows.
        """
        with self.session() as active:
            return active.execute(sql, params).fetchall()

    def on_connect(self, fn: SessionHook) -> None:
        """Run *fn* against every handle for this store — existing and future.

        Args:
            fn: Called with the raw handle. Use it for session state that must
                hold everywhere: ``SET`` settings, ``search_path``, extensions.
        """
        on_connect(self._session_base(), fn)

    @property
    def open_sessions(self) -> int:
        """Live handles for this store, across every thread.

        **Counts handles, not ``with`` blocks**, and the difference matters.
        A DSL query opens a session implicitly — `sdmxlib.query.query.Query`
        resolves the handle at execution and returns, so it cannot wrap itself
        in a block — and that handle then lives until a `session` block ends on
        that thread or the thread does::

            reg.open_sessions  # 0
            codes.ids()  # 1   implicit, and not released
            with reg.session():
                ...  # 0   after the block
            codes.ids()  # 1   a fresh one

        So this is the number to watch for handle accumulation, but it is not
        "requests in flight" unless every path goes through `session`. One
        handle per live thread is the intended steady state — that is what
        #75/#80 wanted, to amortise ``cursor()`` — and the release exists so a
        thread-per-request server can bound it deliberately.
        """
        return handle_count(self._session_base())
