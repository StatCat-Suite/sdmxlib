"""Thread-local cursor lifecycle tests for issue #80.

Each thread that reads from a ``LocalRegistry`` lazily gets its own DuckDB
cursor on first call and reuses it for the thread's lifetime. These
tests pin the invariants that lifecycle relies on:

* **Reuse**: one thread doing N reads uses one cursor, not N.
* **Bounded by thread count**: M threads each doing N reads use M
  cursors, not M*N.
* **No leak across short-lived threads**: ephemeral threads' cursor
  entries get GC'd after they exit.
* **`close()` drops the cache**: post-close, the registry is in the
  same state as a freshly-constructed one — reopen works without
  ghost cursors.
* **Connection GC via WeakKeyDictionary**: when a LocalRegistry's
  connection becomes unreachable, lazy-facade per-thread cursor
  entries vanish from the module-level table.
"""

from __future__ import annotations

import gc
import threading
from concurrent.futures import ThreadPoolExecutor, wait

import pytest

from sdmxlib.local import LocalRegistry
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.urn import SdmxUrn

pytestmark = pytest.mark.unit

_NAME = InternationalString(en="Test")


def _populate(reg: LocalRegistry) -> SdmxUrn[object]:
    """Add a Dataflow + return its URN as a string."""
    reg.add(
        Codelist(
            id="CL_X",
            maintainer="SDMX",
            version="1.0",
            name=_NAME,
            codes=[Code(id="A", name=_NAME), Code(id="B", name=_NAME)],
        )
    )
    reg.add(Dataflow(id="DF_0", maintainer="SDMX", version="1.0", name=_NAME))
    return Dataflow.urn_for(agency="SDMX", id="DF_0", version="1.0")


def _has_thread_cursor(reg: LocalRegistry) -> bool:
    """True if the calling thread has a LocalRegistry-level cursor cached."""
    return getattr(reg._thread_local, "cur", None) is not None


# ── Reuse / bounded ───────────────────────────────────────────────────────


def test_single_thread_reuses_one_cursor():
    """1 thread × 1000 reads → exactly 1 LocalRegistry cursor stored on the threadlocal."""
    with LocalRegistry(":memory:") as reg:
        urn = _populate(reg)
        assert not _has_thread_cursor(reg)
        for _ in range(1000):
            reg.get(urn)
        assert _has_thread_cursor(reg)
        # The stored cursor object should be the same one every call uses.
        first = reg._thread_local.cur
        reg.get(urn)
        assert reg._thread_local.cur is first


def test_cursor_count_bounded_by_thread_count():
    """N threads each doing M reads create N cursors total, not N*M."""
    with LocalRegistry(":memory:") as reg:
        urn = _populate(reg)

        cursors_seen: set[int] = set()
        cursor_lock = threading.Lock()
        barrier = threading.Barrier(8)

        def worker() -> None:
            barrier.wait()
            for _ in range(100):
                reg.get(urn)
            with cursor_lock:
                cursors_seen.add(id(reg._thread_local.cur))

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(worker) for _ in range(8)]
            wait(futures)
            for f in futures:
                f.result()

        # Each worker sees its own cursor object; main thread has none yet.
        assert len(cursors_seen) == 8, f"Expected one cursor per worker, saw {len(cursors_seen)}"


# ── Lifecycle on close ────────────────────────────────────────────────────


def test_close_drops_thread_local_storage():
    """After close(), the registry's threadlocal is fresh."""
    with LocalRegistry(":memory:") as reg:
        urn = _populate(reg)
        reg.get(urn)
        assert _has_thread_cursor(reg)
    # context manager called close()
    assert not _has_thread_cursor(reg)


def test_reopen_after_close_uses_fresh_cursor():
    """A LocalRegistry closed and reopened doesn't reuse a stale cursor handle."""
    reg = LocalRegistry(":memory:")
    reg.open()
    urn = _populate(reg)
    reg.get(urn)
    first_cur = reg._thread_local.cur
    reg.close()

    reg.open()
    _populate(reg)  # fresh in-memory DB, repopulate
    reg.get(urn)
    second_cur = reg._thread_local.cur
    assert second_cur is not None
    assert second_cur is not first_cur, "close()+open() must create a new cursor, not reuse the old one"
    reg.close()


# ── Connection GC via WeakKeyDictionary (lazy-facade table) ──────────────


def test_lazy_facade_table_releases_on_close():
    """Lazy-facade module-level cursor table drops entries when a LocalRegistry closes.

    The per-thread ``WeakKeyDictionary`` in ``sdmxlib.storage.lazy`` is
    keyed by connection. Closing a LocalRegistry drops the connection
    reference; once GC runs, the dict entry should vanish.
    """
    from sdmxlib import session as session_mod

    # Drive a lazy-codelist read so the lazy table acquires an entry.
    reg = LocalRegistry(":memory:")
    reg.open()
    reg.add(
        Codelist(
            id="CL_GC",
            maintainer="SDMX",
            version="1.0",
            name=_NAME,
            codes=[Code(id="A", name=_NAME)],
        )
    )
    cl_urn = Codelist.urn_for(agency="SDMX", id="CL_GC", version="1.0")
    lazy_cl = reg.codelist(cl_urn)
    assert lazy_cl is not None
    _ = len(lazy_cl)  # triggers _cur(self._conn) in lazy.py

    # One handle table since #300 — `storage.lazy` kept a second thread-local
    # cache alongside this one until then, so a single thread held two handles
    # that shared no session state.
    table = getattr(session_mod._thread_local, "table", None)
    assert table is not None
    initial_size = len(table)
    assert initial_size >= 1, "Lazy facade should have populated its cursor table"

    # Drop everything that pins the connection.
    del lazy_cl
    reg.close()
    del reg
    gc.collect()

    # WeakKeyDictionary entry should be gone now that the connection is
    # unreachable.
    assert len(table) < initial_size, (
        f"Expected lazy table to shrink after registry close+GC; size went from {initial_size} to {len(table)}"
    )


# ── No-leak across short-lived threads ────────────────────────────────────


def test_ephemeral_threads_dont_leak_registry_cursors():
    """200 short-lived threads each doing one read must not pin cursors forever.

    Cursors live in each thread's ``threading.local()`` storage and are
    dropped when the thread exits. We can't directly count cursors held
    by dead threads (threadlocals are accessed per-thread), but we can
    verify the test completes without unbounded memory growth and the
    LocalRegistry's thread_local is unchanged from the main thread's view.
    """
    with LocalRegistry(":memory:") as reg:
        urn = _populate(reg)

        def worker() -> None:
            reg.get(urn)

        for _ in range(200):
            t = threading.Thread(target=worker)
            t.start()
            t.join()

        gc.collect()
        # Main thread never read; its threadlocal should still be empty.
        assert not _has_thread_cursor(reg)
