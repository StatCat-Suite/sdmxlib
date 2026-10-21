"""One execution handle per thread, with somewhere to put its state (#300).

Three handles existed on a single thread — `reg.connection`, a thread-local in
`_query_common`, and a *second* thread-local in `storage.lazy` — and they
disagreed. Not in principle: measurably, about `search_path`, which meant
custom SQL written against one could not run on another and a `SET` reached
exactly one of three.

Nothing depended on them agreeing, which is why it survived; #271 was the first
feature to need per-connection state and found half the query paths could not
see it.

## What this file pins

- **One handle.** `cursor_for` and `storage.lazy._cur` are the same function.
- **A place for state.** `on_connect` reaches every handle, existing and
  future — the `sqlalchemy.PoolEvents.connect` equivalent. Without it there is
  nowhere to put anything that must hold everywhere.
- **A visible lifetime.** `session()` scopes the handle, and nesting refcounts
  so a helper cannot release its caller's.
- **`reg.connection` unchanged.** It must stay a stable, capturable object:
  `DuckDBDataStore.from_registry` stores it in a field, and a thread-local
  property would let a store built on one thread execute on another thread's
  handle — the unsafe sharing #75/#80 removed.

## Two bugs found while building it, both pinned below

A first prototype kept *its own* thread-local cache and became a fourth handle.
A first implementation tracked initialised handles in one global set, so a
newly opened store's default hook was applied to every handle in the process —
including closed ones. 23 tests failed with "Connection already closed".
"""

import ast
import threading
from collections import Counter
from collections.abc import Callable
from pathlib import Path

import duckdb
import pytest

import sdmxlib
from sdmxlib.local import LocalRegistry
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.query.accessors import q
from sdmxlib.session import cursor_for
from sdmxlib.storage import lazy as lazy_mod

pytestmark = pytest.mark.unit


def _lazy_cur() -> Callable[[duckdb.DuckDBPyConnection], duckdb.DuckDBPyConnection]:
    """`lazy`'s own `_cur` binding, read from the module dict.

    The name is private to `lazy`, so reaching it as an attribute reads as a
    private import. These tests are *about* that binding being `cursor_for`,
    so it has to be the module's own rather than the original imported here.
    """
    cur: Callable[[duckdb.DuckDBPyConnection], duckdb.DuckDBPyConnection] = lazy_mod.__dict__["_cur"]
    return cur


def _search_path(cur: duckdb.DuckDBPyConnection) -> str:
    row = cur.execute(_SEARCH_PATH).fetchone()
    assert row is not None
    return str(row[0])


_SEARCH_PATH = "SELECT current_setting('search_path')"


@pytest.fixture
def reg():
    with LocalRegistry(":memory:") as registry:
        registry.add(
            Codelist(
                id="CL",
                maintainer="AG",
                version="1.0",
                name={"en": "Codes"},
                codes=[Code(id=c, name={"en": c.lower()}) for c in ("A", "B", "C")],
            )
        )
        yield registry


# ── One handle ───────────────────────────────────────────────────────────────


def test_there_is_exactly_one_handle_per_thread(reg) -> None:
    """The consolidation. `storage.lazy` kept a second cache until #300."""
    base = reg.connection
    assert cursor_for(base) is _lazy_cur()(base)
    assert cursor_for(base) is cursor_for(base)


def test_the_handle_is_not_the_base_connection(reg) -> None:
    """They are different objects with different jobs, and both are needed.

    The base is stable and capturable; the handle is per-thread and must not be
    held. Asserting they differ keeps anyone from "simplifying" one into the
    other.
    """
    assert cursor_for(reg.connection) is not reg.connection


def test_every_thread_gets_its_own(reg) -> None:
    base = reg.connection
    mine = cursor_for(base)
    theirs: list[object] = []
    thread = threading.Thread(target=lambda: theirs.append(cursor_for(base)))
    thread.start()
    thread.join()
    assert theirs[0] is not mine


# ── Somewhere to put state ───────────────────────────────────────────────────


def test_the_handles_agree_about_search_path(reg) -> None:
    """The divergence that started this, inverted into an assertion.

    Before #300: base `'sdmx'`, both handles `''`. So `SELECT ... FROM code`
    worked on one and needed `sdmx.code` on the others.
    """
    base = reg.connection
    paths = {
        _search_path(base),
        _search_path(cursor_for(base)),
        _search_path(_lazy_cur()(base)),
    }
    assert paths == {"sdmx"}
    # The consequence a caller sees: unqualified names resolve everywhere.
    assert reg.execute("SELECT count(*) FROM code")[0][0] == 3


def test_on_connect_reaches_handles_created_later(reg) -> None:
    """A hook registered now must apply to a thread that starts afterwards.

    The half that a naive implementation gets right by accident. The other half
    is below.
    """
    reg.on_connect(lambda handle: handle.execute("SET default_null_order='NULLS FIRST'"))
    seen: list[str] = []

    def worker():
        with reg.session() as active:
            seen.append(active.execute("SELECT current_setting('default_null_order')").fetchone()[0])

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    assert seen == ["NULLS_FIRST"]


def test_on_connect_reaches_handles_that_already_exist(reg) -> None:
    """And the half that is easy to miss.

    A hook installed after a thread has already been working must still apply,
    or "session state" means "state on handles created after you thought of
    it".
    """
    cursor_for(reg.connection)  # this thread's handle already exists
    reg.on_connect(lambda handle: handle.execute("SET default_null_order='NULLS FIRST'"))
    assert reg.execute("SELECT current_setting('default_null_order')")[0][0] == "NULLS_FIRST"


def test_a_hook_does_not_leak_to_another_store(reg) -> None:
    """Handles are tracked per base connection, not in one global set.

    The implementation bug: with a single global set, opening a second store
    applied its default hook to *every* handle in the process, including those
    of closed connections. 23 tests failed with "Connection already closed"
    before this was per-base.
    """
    reg.on_connect(lambda handle: handle.execute("SET default_null_order='NULLS FIRST'"))
    with LocalRegistry(":memory:") as other:
        assert other.execute("SELECT current_setting('default_null_order')")[0][0] == "NULLS_LAST"
    assert reg.execute("SELECT current_setting('default_null_order')")[0][0] == "NULLS_FIRST"


def test_a_closed_store_does_not_break_a_new_one() -> None:
    """Directly: open, close, open. The failure mode was cross-store contamination."""
    with LocalRegistry(":memory:") as first:
        first.execute("SELECT 1")
    with LocalRegistry(":memory:") as second:
        assert second.execute("SELECT 1")[0][0] == 1


# ── A visible lifetime ───────────────────────────────────────────────────────


def test_the_session_is_the_same_handle_the_dsl_uses(reg) -> None:
    """Otherwise a value set registered in the block is invisible to a query.

    The prototype bug: a session backed by its own cache is a *fourth* handle,
    and `codes.filter(...).ids()` cannot see anything registered on it.
    """
    with reg.session() as active:
        assert active.handle is cursor_for(reg.connection)


def test_the_handle_is_released_when_the_block_ends(reg) -> None:
    with reg.session() as active:
        inside = active.handle
    assert cursor_for(reg.connection) is not inside


def test_nesting_refcounts_so_a_helper_cannot_release_its_caller(reg) -> None:
    """A library function opening a session inside a caller's must not free it.

    `scoped_session` has the same hazard. Only the outermost block releases;
    otherwise a helper returning would silently invalidate anything the caller
    had registered.
    """
    with reg.session() as outer:
        with reg.session() as inner:
            assert inner.handle is outer.handle
        # the inner block has exited, and the caller's handle survives
        assert cursor_for(reg.connection) is outer.handle
        survivor = outer.handle
    assert cursor_for(reg.connection) is not survivor


def test_the_dsl_works_inside_and_outside_a_session(reg) -> None:
    """Queries resolve the handle themselves; the session is for custom SQL."""
    codes = reg.get(Codelist.urn_for(agency="AG", id="CL", version="1.0")).codes
    outside = codes.filter(q.code.id != "Z").ids()
    with reg.session():
        assert codes.filter(q.code.id != "Z").ids() == outside


# ── reg.connection stays capturable ──────────────────────────────────────────


def test_the_base_connection_is_stable(reg) -> None:
    """The property `DuckDBDataStore.from_registry` depends on.

    It stores `registry.connection` in a field. If that returned a per-thread
    handle, a store built on one thread would execute on that thread's handle
    from another — exactly the sharing #75/#80 removed. So this identity is not
    incidental; it is the reason `session()` exists as a separate, scoped thing.
    """
    assert reg.connection is reg.connection

    captured = reg.connection
    seen: list[bool] = []
    thread = threading.Thread(target=lambda: seen.append(captured is reg.connection))
    thread.start()
    thread.join()
    assert seen == [True], "the base connection must not vary by thread"


def test_a_registry_derived_store_shares_the_registry_handle(reg) -> None:
    """`DuckDBDataStore.from_registry` captures the base, so both resolve alike.

    The property that makes capturing the base correct: the store holds a
    stable object and derives the per-thread handle at use, so a session opened
    through either reaches the same one. Had `connection` been made
    thread-local instead, this is the assertion that would have started
    failing across threads.
    """
    import sdmxlib as sl

    store = sl.DuckDBDataStore.from_registry(reg)
    assert store.connection is reg.connection
    with reg.session() as from_registry, store.session() as from_store:
        assert from_registry.handle is from_store.handle


# ── Observability ────────────────────────────────────────────────────────────


def test_open_sessions_counts_live_handles_for_this_store(reg) -> None:
    """Live, not lifetime-served — which is the number worth watching.

    The first version of this test asserted 1 after `reg.execute(...)` and got
    0. That is correct and is the point: `execute` is scoped, so it releases on
    the way out and leaves nothing behind. A thread-per-request server that
    stays inside its blocks should see this track *active* requests, not
    served ones.
    """
    assert reg.open_sessions == 0
    with reg.session():
        assert reg.open_sessions == 1
        with reg.session():
            assert reg.open_sessions == 1, "nesting shares one handle"
    assert reg.open_sessions == 0

    reg.execute("SELECT 1")
    assert reg.open_sessions == 0, "a one-shot leaves no handle behind"


def test_a_dsl_query_opens_a_session_implicitly(reg) -> None:
    """The fourth door, and the one `open_sessions` is easiest to misread over.

    A query cannot wrap itself in a block — it resolves the handle at execution
    and returns — so `cursor_for` is called directly and nothing releases the
    result. That is the intended steady state (one handle per live thread,
    which is what #75/#80 wanted), but it means this counter is *not* "requests
    in flight" unless every path goes through `session`.
    """
    codes = reg.get(Codelist.urn_for(agency="AG", id="CL", version="1.0")).codes
    assert reg.open_sessions == 0
    codes.ids()
    assert reg.open_sessions == 1, "a bare query opens one implicitly"
    codes.ids()
    assert reg.open_sessions == 1, "and reuses it"
    with reg.session():
        pass
    assert reg.open_sessions == 0, "a scoped block releases the shared handle"


def test_cursor_for_is_not_public_surface() -> None:
    """It hands out an *unmanaged* handle — no scope, no release.

    Package-internal, for the query compilers that must resolve at execution.
    `__all__` is where that is stated: importing still works (nothing in Python
    prevents it, and `_query_common` needs it), but it is not part of the
    surface, which is the contract #265's guard reads.
    """
    from sdmxlib import session as session_module

    assert "cursor_for" not in session_module.__all__
    assert callable(session_module.cursor_for), "still importable for internal use"


def test_open_sessions_does_not_count_another_store(reg) -> None:
    """Per base connection, like the hooks — for the same reason."""
    with reg.session(), LocalRegistry(":memory:") as other:
        assert reg.open_sessions == 1
        assert other.open_sessions == 0
        with other.session():
            assert other.open_sessions == 1
            assert reg.open_sessions == 1


# ── An unfinished result does not refuse the next relation (DuckDB 2.0) ────


def test_a_relation_runs_after_a_fetchone_left_its_result_open(reg) -> None:
    """The pair that broke: `key_counts` ends in `fetchone()`, which never
    finishes a result, and `Select.collect` then ran `sql()` on the same
    thread's handle — refused with `ResourceInUseException` by DuckDB 2.0."""
    cursor_for(reg.connection).execute("SELECT count(*) FROM sdmx.artefact").fetchone()
    assert reg.query(q.dataflow).select(q.dataflow.id).collect().height == 0


#: Calls named `sql`, `query`, `register` or `unregister` in `src/` whose
#: receiver is not a DuckDB handle, so `completed` has nothing to finish.
_NOT_A_HANDLE = Counter(
    {
        ("api/registry.py", "self._query_store()", "query"): 1,  # the registry's path language
        ("local/registry.py", "self", "query"): 3,  # likewise
        ("formats/sdmx_json/_data_common.py", "pool", "register"): 1,  # the annotation pool
        ("formats/sdmx_json/_data_common.py", "self._annotations", "register"): 2,  # likewise
    }
)


def test_every_relation_call_on_a_handle_goes_through_completed() -> None:
    """A static inventory, because the failure is non-local: the `fetchone()`
    that strands a result and the `sql()` it breaks live in unrelated modules,
    and only the order calls arrive on one thread decides whether it shows.

    Exact on both sides: a new unguarded call fails it, and so does an exempt
    one that has gone, so the exemptions cannot outlive their reasons.
    """
    root = Path(sdmxlib.__file__).parent
    unguarded: Counter[tuple[str, str, str]] = Counter()
    guarded = 0
    for path in sorted(root.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"sql", "query", "register", "unregister"}
            ):
                continue
            receiver = node.func.value
            if (
                isinstance(receiver, ast.Call)
                and isinstance(receiver.func, ast.Name)
                and receiver.func.id == "completed"
            ):
                guarded += 1
                continue
            unguarded[path.relative_to(root).as_posix(), ast.unparse(receiver), node.func.attr] += 1
    assert unguarded == _NOT_A_HANDLE
    assert guarded == 17, "the guarded sites moved — recount, and check each still names a handle"
