# Concurrency

How sdmxlib's registries behave under threaded access — what's safe, what
isn't, and where the ceilings are. Both `LocalRegistry` (DuckDB-backed
store) and `RestRegistry` (HTTP, with a store behind it) commit to the same
contract; the second one arrived later and is covered in its own section
below.

The current shape was designed in response to issue #75: under any
thread-pooled FastAPI workload, concurrent `LocalRegistry.get(...)` calls
corrupted result sets. The fix landed in v0.28.0 (#78). This page
documents the resulting model so deployments can pick the right pattern
without rediscovering the constraints.

## Summary

| Operation | Thread-safe? |
|---|---|
| `LocalRegistry.get`, `get_all`, `codelist`, `code_map`, `dsd_code_map`, `to_registry`, `__contains__`, `__len__`, `__repr__` | ✅ Yes |
| `LocalRegistry.query(...)`, `obs(...)` and their terminals | ✅ Yes |
| `LocalRegistry.add`, `drop`, `replace`, `add_all`, `ingest`, `write_observations` | ❌ No — single-threaded only |
| Concurrent access to the **same** `LazyCodelist` / `LazyHierarchy` instance from multiple threads | ✅ Yes |
| Sharing a `LocalRegistry` across multiple Python *processes* | ❌ No — one DuckDB connection per process |
| `RestRegistry.get`, `query`, `obs`, `download` | ✅ Yes |
| `RestRegistry.load`, and writes to the `LocalRegistry` passed as `store=` | ❌ No — single-threaded only |

`DataStore.query` / `query_stream` used to sit in this table. #329 removed
them: the `DataStore` Protocol is write-side only, and observations are read
through `obs()`, whose terminals run on the per-thread handles
described below.

## Read safety

Each thread gets its own DuckDB cursor, lazily created on first read
and reused for the thread's lifetime:

```python
def _read_cursor(self) -> duckdb.DuckDBPyConnection:
    cur = getattr(self._thread_local, "cur", None)
    if cur is not None:
        return cur
    # (raises if the registry is not open)
    cur = self._conn.cursor()
    cur.execute("SET search_path = 'sdmx'")
    self._thread_local.cur = cur
    return cur
```

DuckDB serialises `execute()` calls on a single connection, but each
connection has one *default* cursor whose row buffer is overwritten by
every `execute()`. The historical bug (#75) was two threads racing
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
through **one** shared function, `sdmxlib.session.cursor_for(conn)`. It
backs onto a single `threading.local()` whose value is a
`weakref.WeakKeyDictionary[Connection, Cursor]` — when a registry's
connection becomes unreachable (close + GC), the dict entry vanishes
automatically. Until #300 there were two such tables, one in
`session.py` and one in `storage/lazy.py`, so one thread held two handles
that shared no session state; `lazy.py` now imports `cursor_for`. No
`SET search_path` on those handles: their SQL is `sdmx.`-qualified.

### Cursor lifecycle

Invariants:

- **One `_read_cursor` per (thread, registry)**, plus one `cursor_for`
  handle per (thread, connection), both created lazily. N worker threads
  on a single `LocalRegistry` → on the order of 2N cursors, fixed for the
  threads' lifetime — not one per call.
- **`LocalRegistry.close()` drops the threadlocal** so a subsequent
  `open()` starts fresh — no stale cursors after reopen.
- **`WeakKeyDictionary` keying for lazy facades** lets the cursor
  table self-clean when connections die, so there's no `id()` reuse
  hazard.
- **Cursors die with their thread** (Python GC reclaims the
  threadlocal storage when the thread terminates).

For deployment context: on a typical FastAPI/uvicorn setup, the
threadpool is long-lived (~40 stable workers parked between
requests). A `LocalRegistry` ends up with ~40 threads' worth of cursors, stable for the
process lifetime — no per-request cursor churn.

Lifecycle tests live in `tests/unit/test_cursor_lifecycle.py`.

### One connection runs one statement at a time

DuckDB has a per-connection mutex that serialises most internals. A
`LocalRegistry` backed by one DuckDB connection cannot run two SQL
statements truly in parallel — the threads each have their own cursor
(no corruption), but they take turns at the connection's lock. In
practice this gives:

- Per-thread results that are always **correct**.
- Throughput bounded by DuckDB's single-connection serialisation —
  roughly flat past 4–8 workers in the benchmark for read-heavy mixes.

For deployments that need to push past that ceiling, the answer is
**multiple connections** (a connection pool, or one registry per worker
process). That's an open architectural question; not currently provided
by sdmxlib.

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

## Resolver cache — FIFO, lock-free reads

`LocalRegistry(…, resolve_cache_items=…)` configures a bounded URN → resolved-target
cache shared across calls, bounded in *items* rather than entries (#386). Its purpose is to amortise parent-scheme
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
- **Hit/miss counters are best-effort.** They use lockless `+=`, which
  races under contention. The numbers reported by
  `resolve_cache_info()` may slightly under-count. They remain a
  useful diagnostic, but should not be used as a load-bearing assertion.

### FIFO eviction keeps reads lock-free

The previous implementation (v0.27.x) was an LRU — every read called
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
deployment ever hits unforeseen contention on cache mutation or
suspects the cache itself of misbehaving. Matches pre-v0.27.2 behaviour.

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
own `LocalRegistry` and its own DuckDB connection. DuckDB's per-connection
ceiling no longer bounds your total throughput; the OS schedules
process-level parallelism for you.

### A LazyCodelist held across requests

A `LazyCodelist` returned from `reg.codelist(urn)` or
`reg.get(codelist_urn, eager=False)` holds the registry's long-lived
connection (not a cursor). It can safely be stashed in a long-lived
in-memory cache keyed by URN, and methods called from any thread —
each thread resolves to its own per-thread cursor via
`session.cursor_for`'s `WeakKeyDictionary`-backed thread-local store.

## DuckDB connection-level ceiling

Even with isolated per-thread cursors, DuckDB serialises most read
internals at the connection level. Empirically (see `bench/`),
single-connection throughput plateaus around 4–8 concurrent threads on
typical hardware. Past that, more workers don't help and may hurt
(lock contention overhead).

If your workload needs to push past that ceiling:

1. **Multiple processes.** uvicorn `--workers N`, each with its own
   `LocalRegistry`. Simplest answer; scales linearly until the database
   file's OS-level read concurrency becomes the bottleneck (rare for
   metadata-sized DBs).
2. **Connection pool.** Not provided by sdmxlib today. If you have a
   real workload that needs it, file an issue with the bench numbers.

## Performance notes

Thread-local cursor reuse means the per-call overhead is essentially
a `getattr` lookup. Steady-state reads (after the first call per
thread that pays the cursor-creation cost) measure within a few
percent of the pre-v0.28.0 shared-cursor design — the `code_map`
trivial-SELECT workload that was +25% slower in v0.28.0 under the
per-call design is back within ~6% of baseline. Errors under
concurrency remain at 0.

The architectural ceiling (DuckDB's per-connection serialisation, see
above) caps total concurrent read throughput regardless of cursor
strategy — multiple `LocalRegistry` instances or a connection pool would
be needed to push past it.

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

`SingleFlight` is the sibling of the resolver's `_FIFOCache`: that one caches
a *value*, this one caches the fact that work already happened, which is what
a fetch produces. Both meet the checklist in
[caching](caching.md#the-thread-safety-contract-for-category-c); new keyed
work should route through one of them rather than re-derive locking.

## See also

- [Performance & Caching](performance.md) — what the resolver cache is
  *for* (bulk reads, N+1 amortisation).
- Issue [#75] — original bug report, k6 numbers, stack traces.
- Issue [#78] — initial fix (per-call cursors).
- Issue [#80] — switch to thread-local cursors (v0.28.1).

[#75]: https://gitlab.com/pinax-suite/sdmxlib/-/work_items/75
[#78]: https://gitlab.com/pinax-suite/sdmxlib/-/work_items/78
[#80]: https://gitlab.com/pinax-suite/sdmxlib/-/work_items/80
