# Concurrency

How sdmxlib's registries behave under threaded access — what's safe, what
isn't, and where the ceilings are. `EditionRegistry` (the read-only DuckDB
store a published bundle opens as), `LocalRegistry` (which extends it with
writes) and `RestRegistry` (HTTP, with a store behind it) commit to the same
contract; `RestRegistry` arrived later and is covered in its own section
below.

The current shape was designed in response to issue #75: under any
thread-pooled FastAPI workload, concurrent `LocalRegistry.get(...)` calls
corrupted result sets. The fix landed in v0.28.0 (#78). This page
documents the resulting model so deployments can pick the right pattern
without rediscovering the constraints.

## Summary

| Operation | Thread-safe? |
|---|---|
| `EditionRegistry.get`, `get_all`, `codelist`, `code_map`, `dsd_code_map`, `to_registry`, `__contains__`, `__len__`, `__repr__` (inherited by `LocalRegistry`) | ✅ Yes |
| `EditionRegistry.query(...)`, `obs(...)` and their terminals | ✅ Yes |
| `LocalRegistry.add`, `drop`, `replace`, `add_all`, `ingest`, `write_observations` | ❌ No — single-threaded only |
| Concurrent access to the **same** `LazyCodelist` / `LazyHierarchy` instance from multiple threads | ✅ Yes |
| Several Python *processes* opening one published bundle as a `EditionRegistry` | ✅ Yes — it opens its database `read_only` |
| Sharing a writable `LocalRegistry` across Python *processes* | ❌ No — one DuckDB connection per process |
| `RestRegistry.get`, `query`, `obs`, `download` | ✅ Yes |
| `RestRegistry.load`, and writes to the `LocalRegistry` passed as `store=` | ❌ No — single-threaded only |

The `DataStore` Protocol is write-side only (#329). Observations are read
through `obs()`, whose terminals run on the per-thread handles described
below.

## Read safety

Each thread gets its own DuckDB cursor, lazily created on first read
and reused for the thread's lifetime (`EditionRegistry._read_cursor`,
`local/registry.py`):

```python
def _read_cursor(self) -> duckdb.DuckDBPyConnection:
    cur: duckdb.DuckDBPyConnection | None = getattr(self._thread_local, "cur", None)
    if cur is not None:
        return cur
    # (raises if the registry is not open)
    cur = adopt(self._conn, use_sdmx(self._conn.cursor(), self._sdmx_catalog()))
    self._thread_local.cur = cur
    return cur
```

`use_sdmx` (`storage/schema.py`) runs `USE "<catalog>".sdmx`, because a
DuckDB cursor inherits neither its parent's current database nor its search
path. `adopt` (`session.py`) records the cursor as derived from the
registry's connection, so a handle `cursor_for` later takes from it runs that
connection's session hooks too.

A DuckDB connection object holds one result buffer, overwritten by every
`execute()` on it. The bug in #75 was two threads racing
between `execute()` and `fetchone()` on that shared buffer — one
thread's row data ended up in another thread's result.

Per-thread cursors close that race: each thread's `cur.execute(...)` /
`cur.fetchone()` pair runs on its own buffer. Cursors are *not*
recreated per call — the first read in a thread creates the cursor,
subsequent reads reuse it. This matches the psycopg2-in-worker-thread
pattern and avoids the per-call `conn.cursor()` cost.

`LazyCodelist` / `LazyHierarchy`, the query language's `Query`, the
storage resolve helpers and `rollup.AncestorMap` hold the long-lived
connection (not a cursor) and obtain a per-thread cursor for every call
through `sdmxlib.session.cursor_for(conn)`. It backs onto a
`threading.local()` whose value is a
`weakref.WeakKeyDictionary[Connection, Cursor]` — when a registry's
connection becomes unreachable (close + GC), the dict entry vanishes
automatically. A new handle runs the connection's session hooks; every
store installs one that runs `use_sdmx` on it
(`_install_default_session_hooks`), so the handle reads the store's
catalog.

A second per-thread table lives in `storage/_projections.py`
(`_thread_cursor`), for the `code_map` projection and
`readers.code_map_for_dsd`. It has the same shape, a `threading.local()`
holding a `WeakKeyDictionary`, and builds its handles with `sdmx_cursor`,
which runs `use_sdmx` directly instead of the session hooks. One thread
therefore holds up to two hook-managed handles and one projection handle per
store.

### Cursor lifecycle

Invariants:

- **One `_read_cursor` per (thread, registry)**, plus one `cursor_for`
  handle and one `_projections` handle per (thread, connection), all
  created lazily. N worker threads on a single registry → up to about 3N
  cursors, fixed for the threads' lifetime — not one per call.
- **`close()` drops the registry's threadlocal** so a subsequent
  `open()` starts fresh — no stale cursors after reopen.
- **`WeakKeyDictionary` keying for lazy facades** lets the cursor
  table self-clean when connections die, so there's no `id()` reuse
  hazard.
- **Cursors die with their thread** (Python GC reclaims the
  threadlocal storage when the thread terminates).

For deployment context: on a typical FastAPI/uvicorn setup, the
threadpool is long-lived (~40 stable workers parked between
requests). A registry ends up with ~40 threads' worth of cursors, stable for
the process lifetime — no per-request cursor churn.

Lifecycle tests live in `tests/unit/test_cursor_lifecycle.py`.

### Parallelism on one connection

Handles taken from one DuckDB connection run their statements
concurrently: four parallel queries on four handles took 1.32x the time of
one query (measured for #300 on DuckDB 1.5.3, recorded in `cursor_for`'s
docstring). Per-thread handles therefore cost no SQL parallelism, and
per-thread results are always **correct**.

End-to-end `get` throughput is still roughly flat with worker count. The
pinned baseline in `bench/concurrency/baselines.json` (sdmxlib 0.50.0,
workload `concurrent_get_mixed_{N}w`) reads 885 ops/s at 1 worker, 1,045 at
4, 815 at 8 and 798 at 16. Which part of a `get` holds that plateau, the SQL
or the Python that builds the artefact, has not been profiled, and the
baseline predates DuckDB 2.0.

`EditionEngine` (`local/engine.py`) attaches several published bundles into
one DuckDB connection, and each `EditionRegistry` opened on it takes its own
handle from that connection (`engine.cursor(prefix)`). Registries on one
engine share its connection, its `threads` setting and its memory limit.

## Write safety — single-threaded

`add`, `drop`, `replace`, `add_all`, `ingest` use a single long-lived
cursor (`LocalRegistry._c`) and are **not** safe under concurrent callers.
This is a deliberate scope choice: SDMX as a domain is heavily
read-skewed (structural metadata is published, then read many times),
and supporting concurrent writes would require either a pool or a
write lock, neither of which has a real workload to motivate it today.

If your application mutates the registry from multiple threads,
serialise it externally:

```python
import threading

_write_lock = threading.Lock()


def add_safely(reg, artefact):
    with _write_lock:
        reg.add(artefact)
```

Reads can still run concurrently with each other while a writer holds
the lock — the lock only serialises *other writers*.

## Resolver cache — FIFO, lock-free reads, not single-flight

`resolve_cache_items=` on `EditionRegistry` and `LocalRegistry` configures a
bounded URN → resolved-target cache shared across calls, bounded in *items*
rather than entries (#386). Its purpose is to amortise parent-scheme
reads — e.g. listing 293 MetadataSets that all reference the same MSD
should load the MSD once, not 293 times. (See
[Performance → Resolver Cache](performance.md).)

The cache is a plain `dict` subclass with FIFO eviction:

- **Cache hits are lock-free.** A hit is a plain `dict[k]` lookup,
  which is GIL-atomic in CPython. No `threading.Lock`, no
  serialisation between reader threads.
- **Mutations hold a per-cache `threading.Lock`.** Inserts +
  insertion-order eviction + `clear()` all go through the lock, but
  these are off the hot path (cache-miss path only). The running weight
  #386 added is maintained under that same lock, so weighing changed the
  eviction rule and not the concurrency design.
- **A miss is not single-flight.** Two threads that miss the same URN both
  read it from the store, and the second insert replaces the first. The
  cache predates `SingleFlight`; [caching](caching.md#the-runtime-caches-today)
  lists it with the other caches that do not go through the primitive.
- **Hit/miss counters are best-effort.** They use lockless `+=`, which
  races under contention. The numbers reported by
  `resolve_cache_info()` may slightly under-count. They remain a
  useful diagnostic, but should not be used as a load-bearing assertion.

### Record: LRU to FIFO in v0.27.x

The implementation in v0.27.x was an LRU — every read called
`move_to_end()` on the underlying `OrderedDict`. That mutation on every
hit is exactly what would force a lock around the hot path under
concurrent access. We chose to give up LRU recency in exchange for
lock-free reads. At realistic working-set sizes for SDMX parent-scheme
reuse, the hit-rate difference between FIFO and LRU is small.

This is a documented trade-off. If `resolve_cache_info()` ever reports
poor hit rates against a working set that should otherwise fit — and
`refused` is zero, so it is recency and not weight — file an issue and we'll revisit (clock-pro, copy-on-write snapshot, etc. are
all on the table — none of them shipped today because the FIFO numbers
look fine in practice).

### Disabling the cache

`LocalRegistry(uri, resolve_cache_items=0)` disables caching entirely. Every
Ref resolution does a full read. This is the escape hatch if a
deployment hits contention on cache mutation or suspects the cache itself
of misbehaving.

## Deployment patterns

### Single-developer analysis (notebook / one-off script)

```python
with sl.LocalRegistry("store.duckdb") as reg:
    dsd = reg.get(dsd_urn)
    # ... use reg from the main thread only
```

One thread, one cursor created on first read and reused for every
subsequent call. Nothing to configure. This is the case sdmxlib started
out optimising for.

### Long-running web app (FastAPI / Flask / similar)

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
import sdmxlib as sl


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.reg = sl.LocalRegistry("store.duckdb").__enter__()
    try:
        yield
    finally:
        app.state.reg.close()


app = FastAPI(lifespan=lifespan)


@app.get("/dataflow/{urn}")
def read_dataflow(urn: str):
    return app.state.reg.get(sl.Dataflow.parse_urn(urn))
```

One `LocalRegistry` instance per worker process, shared across the
threadpool. FastAPI sync route handlers run on `anyio.to_thread`'s
default pool (40 threads); each thread lazily acquires its own cursor
on first read and reuses it, so concurrent requests don't corrupt each
other's results and there's no per-request cursor churn.

For multiple processes, run multiple uvicorn workers — each gets its
own registry and its own DuckDB connection, and the OS schedules
process-level parallelism. A published bundle opens read-only, so every
worker can open the same files.

### A LazyCodelist held across requests

A `LazyCodelist` returned from `reg.codelist(urn)` or
`reg.get(codelist_urn, eager=False)` holds the registry's long-lived
connection (not a cursor). It can safely be stashed in a long-lived
in-memory cache keyed by URN, and methods called from any thread —
each thread resolves to its own per-thread cursor via
`session.cursor_for`'s `WeakKeyDictionary`-backed thread-local store.

## Throughput ceiling

One registry's `get` throughput stops rising at about 4 workers in
`bench/concurrency/` (see [Parallelism on one
connection](#parallelism-on-one-connection) for the numbers). Past that,
throughput falls slightly.

If your workload needs more:

1. **Multiple processes.** uvicorn `--workers N`, each with its own
   registry and connection. This scales until the database files' OS-level
   read concurrency becomes the bottleneck, which is rare for
   metadata-sized stores.
2. **Connection pool.** sdmxlib provides none. If you have a real workload
   that needs one, file an issue with the bench numbers.

## Performance notes

Thread-local cursor reuse means the per-call overhead is essentially
a `getattr` lookup.

### Record: v0.28.0 to v0.28.1 (#78, #80)

The fix for #75 landed in v0.28.0 (#78) as a cursor per call; v0.28.1 (#80)
switched to thread-local cursors. With that switch, steady-state reads
(after the first call per thread that pays the cursor-creation cost)
measured within a few percent of the pre-v0.28.0 shared-cursor design: the
`code_map` trivial-SELECT workload that was +25% slower under the per-call
design came back within ~6% of baseline, with 0 errors under concurrency.

## RestRegistry — the same contract, stated later (#336)

`RestRegistry` was silent about threads until #336, and nobody could rely on
or violate an unstated contract. The intended shape of that API is a server
(§4.1 of [registry-and-catalog](registry-and-catalog.md) is a FastAPI facade).
It now commits to the same sentence the rest of this page does — **concurrent
reads are safe and do not duplicate work** — which took four fixes:

1. **One query store.** The lazy `:memory:` store is built under a
   double-checked lock. Two threads previously built two, and the loser's was
   *leaked still open* holding that thread's fetched artefacts. A query then
   read an empty store and correctly reported nothing, with no error to show
   anything was wrong.
2. **One fetch per question.** `_fetched` was a plain unbounded `set`, so
   eight threads asking one question made eight HTTP round-trips. It is now a
   `SingleFlight` (`sdmxlib/_singleflight.py`): the first arrival fetches, the
   rest wait on it, and the completed-key set is bounded and evicts FIFO.
   Eviction only costs a re-fetch, which is why the guarded work must stay
   idempotent.
3. **One question per observations table.** A dataflow's table holds one
   question's rows at a time, so the lock spans the *ingest*, not just the
   check — two threads asking different questions take turns rather than
   interleaving two CSVs into one table.
4. **No execution on `.connection`.** `_current_catalog` ran
   `SELECT current_database()` on the store's base connection, which #300 had
   already ruled out. Two threads sharing it overwrite each other's row
   buffer — the #75 mode — so `fetchone()` returned `None` and a branch
   commented *"DuckDB always answers this"* raised. This was found by the
   tests written for (1)–(3), not by review.

`SingleFlight` caches the fact that work already happened, which is what a
fetch produces; the resolver's `_FIFOCache` caches a *value* and is not
single-flight. A new runtime cache goes through `SingleFlight`
([caching](caching.md#the-thread-safety-contract-for-category-c)), and
[the inventory there](caching.md#the-runtime-caches-today) lists which
existing caches do.

## See also

- [Performance & Caching](performance.md) — what the resolver cache is
  *for* (bulk reads, N+1 amortisation).
- Issue [#75](https://github.com/StatCat-Suite/sdmxlib/blob/main/history/gitlab/issues/75.md) — original bug report, k6
  numbers, stack traces.
- Issue [#78](https://github.com/StatCat-Suite/sdmxlib/blob/main/history/gitlab/issues/78.md) — initial fix (per-call
  cursors).
- Issue [#80](https://github.com/StatCat-Suite/sdmxlib/blob/main/history/gitlab/issues/80.md) — switch to thread-local
  cursors (v0.28.1).
- Issue [#300](https://github.com/StatCat-Suite/sdmxlib/blob/main/history/gitlab/issues/300.md) — `cursor_for` and the
  session hooks, and the 1.32x measurement.
