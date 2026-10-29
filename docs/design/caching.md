# Caching Strategy: When to Cache, Bake, or Fix the Query

sdmxlib accumulates caches one optimization pass at a time — a label
map here, a resolver cache there, a per-thread cursor somewhere else. Left
unmanaged, that accretion becomes tech debt: every ad-hoc cache adds an
invalidation contract nobody tracks, a thread-safety hazard, and a
per-process cold start. Worse, a cache can *hide* a design problem (a bad
query plan, an unpartitioned table) instead of fixing it, so the slow
path is still there for the cold case — you've just stopped looking at it.

This page is the decision framework. Read it **before** adding a cache
during a performance pass. It is the companion to
[Performance](performance.md) (how the resolver cache works) and
[Concurrency](concurrency.md) (the thread-safety model caches must respect).

## Rule zero: measure before you cache

A cache is a *measured* decision, not a reflex. The trigger is a profile
showing the **computation** is expensive **and** reused — not a load test
showing a slow **symptom**. If you cannot point at the line that is slow,
you do not cache; you measure.

### Record: issue #155, "the 58-second label map"

This case study records the #155 investigation as it stood at the time; the
label cache it mentions has since been removed.

Issue #155 reported `slpl.label()` taking ~58 s on first call and ~290 s
across five concurrent users, attributing the cost to `code_map`'s SQL
projection and proposing a per-codelist build lock plus the process-global
label cache that then existed.

Measured against a real 2021 Census Profile bundle, **none of that attribution held**:

| Claim in the issue | Measured reality |
|---|---|
| `code_map` SQL is the ~58 s cost | 7.8 ms for CL_CHARACTERISTIC (2,631 codes); 70–80 ms for the largest codelists (70k codes / a 966k-row `code` table) |
| Missing / unused index on `code` | `idx_code_codelist` exists; data is plain `meta.duckdb`, not DuckLake; planner seq-scans because the table is small enough not to matter |
| Cold `label()` is ~58 s | 1.3 s cold / 0.2 s warm on a 25-component DSD against a 594k-code bundle |
| The slow load test is label-build contention | The consumer measured was an LLM agent built on sdmxlib, and its slowness was **its model round-trips**, not the label map (which measured 8 ms). Warm runs were no faster than cold ones — a cache would make warm *faster* — so the variance was the model provider's, not a cache effect. |

The only reproducible defect was a genuine but **small** thundering herd:
five concurrent cold-cache threads each rebuild every map (~5× the work,
0.55 s → 2.48 s). Worth fixing — but a 5× amplification of a 0.5 s cost,
not a 58 s one.

The lesson: had we shipped the lock + cache, we would have "fixed" a
58 s problem that does not exist, while the real latency (LLM time, cold
encoder load, cold DuckLake attach) stayed invisible behind a cache we
felt good about. **Profile the operation in isolation before believing a
load test's attribution.**

## Three kinds of "cache" — keep them separate

Conflating these is what generates debt. They differ in every property
that matters.

| | **A. Build-time baked** | **B. Physical layout** | **C. Runtime process-local** |
|---|---|---|---|
| Examples | `code.depth`, `code.ancestor_ids`; a future `code_label` table | indexes, clustering / sort order, narrow projection tables | `RestRegistry`'s fetch memo (`SingleFlight`), the resolver's `_FIFOCache`, `LazyCodelist`'s per-language `code_map` memo |
| Computed | once, when the bundle is written | once, at write time | lazily, per process, per worker |
| Invalidation | none — rebuilt with the data | none | **required** (staleness, memory bound, thread-safety) |
| Multiuser | shared by every worker, even non-Python consumers | shared by everyone | **cold on every new worker** |
| Cost | bundle size, build time | build time | memory + concurrency hazards |

`code.depth` and `code.ancestor_ids` (defined in
`src/sdmxlib/storage/schema.py`) are the exemplar of category
**A**: denormalized transitive-closure
results baked at write time so hierarchy reads are O(1) — no lock, no
invalidation, no per-process warmup, shared by every reader. That is the
pattern to generalize.

## The decision hierarchy

When a read is slow, walk these **in order** and stop at the first that
applies. Each step earlier in the list has strictly better correctness
and multiuser properties than the ones below it.

1. **Profile it, and stop if it is fast.** (#155 stops here.)
2. **If physical layout you control at build time makes it slow,** fix the
   layout (**B**): cluster/partition by the filter key, narrow
   the projection, confirm the plan. Helps every consumer in every
   language, with zero invalidation surface. A cache on top of a fixable
   query is debt.
3. **If the input is immutable within a bundle / artefact version,**
   **bake it (A)** — don't runtime-cache it. Per the SDMX information
   model, a versioned `MaintainableArtefact` in a bundle is effectively
   frozen, and its codelists and languages are fixed at build time. Bake
   the precomputed result into the bundle. Baking also eliminates the
   multiuser cold start, because the data ships in the bundle and every
   worker reads it cheaply.
4. **Only if it is intrinsically per-process and cannot be baked**
   (keyed on runtime parameters, a live `RestRegistry`, user input) →
   use a runtime cache (**C**) — and route it through `SingleFlight`, the
   shared primitive below, never a fresh module global.

### Litmus shortcuts

- *"Does this cache need invalidation?"* If yes, that is a red flag:
  baked data never needs it (you rebuild the bundle). If you are writing
  a `clear_*_cache()`, ask whether the value should have been baked.
- *"Will a fresh worker pay this cost again?"* If yes and the input is
  immutable, you want **A**, not **C**.
- *"Is the underlying query fixable at build time?"* If yes, fix **B**
  first; a cache would only mask it.

## The thread-safety contract for category C

When a runtime cache *is* the right answer, it must honour the model in
[Concurrency](concurrency.md). The contract sdmxlib commits to:

> **Concurrent reads are safe and do not duplicate work.**

The requirements that follow:

1. **Single-flight, not just a lock.** The first arrival builds; concurrent
   arrivals for the same key await the *same* result rather than each
   rebuilding. A plain mutex serializes but still lets the second caller
   rebuild after the first releases unless the cache is re-checked under
   the lock (double-checked locking).
2. **One mechanism, bound to a lifecycle.** A new runtime cache goes through
   **`_singleflight.SingleFlight`** (`src/sdmxlib/EGM.md` states the rule):
   `run(key, work)` runs `work` once per key while concurrent callers wait
   on it, a bounded FIFO remembers completed keys, a hit takes no lock, and
   a failure is not remembered, so waiters retry. Only idempotent work
   belongs there, because eviction repeats the work. Its owner ties
   `clear()` to a lifecycle (a registry's close, a store's mutation).
3. **Bounded in a unit that corresponds to what you are bounding.** This
   requirement used to read "bounded (size or weakref)" and the resolver
   cache satisfied it — by entry count, while its entries ranged from
   0.1 MB to over 900 MB. Five artefacts filled 2% of the counter and held
   ~4.4 GB (#386). An entry count is a bound only where entries are
   *comparable*; that is the axis the ecosystem splits on. Know which side a
   new cache is on before picking a number:

   | | bound | why it works there |
   | --- | --- | --- |
   | `functools.lru_cache`, CPython's `re` cache, Django `LocMemCache` | entries | values are small and comparable |
   | Caffeine, Guava `CacheBuilder` | `maximumWeight` + a `Weigher` | *their own docs draw this exact distinction* |
   | .NET `MemoryCache` | `SizeLimit` + per-entry `Size` | an entry with no `Size` **throws** under a limit |
   | Redis | `maxmemory`, in bytes | never in keys |
   | SQLAlchemy identity map | none — the `Session` is the bound | lifecycle-scoped instead |

   If your values differ by more than about an order of magnitude, weigh
   them. The weigher must be **O(1)** — the resolver cache reads a
   pre-built `ItemList` length and never traverses or queries — and the
   items-to-bytes correspondence must be **measured and written next to the
   default**, so a number in the cache's own unit can be read as memory.
   #386 happened because that measurement was missing: the constant it
   corrects carried no comment and arrived in a bulk cleanup commit.

### The runtime caches today

`SingleFlight` arrived with #336, after most of the caches below, and only
two of them go through it. The others predate the rule. The inventory,
against requirements 1–3 and a lifecycle:

| Cache | Where | Single-flight | Bound | Cleared by |
| --- | --- | --- | --- | --- |
| `RestRegistry._fetched` | `api/registry.py` | yes, `SingleFlight` | `fetch_cache_size` keys, FIFO | the registry instance; a data fetch that overwrites a dataflow's table evicts the earlier question |
| `storage._parsed.parsed` (SQL text → parsed `Statement`) | `storage/_parsed.py` | yes, `SingleFlight` | `MAX_TEXTS` (256) texts | never; a module global, keyed on immutable SQL text |
| `_FIFOCache` (the resolver cache) | `local/registry.py` | **no**: lock-free hits, locked inserts; two threads that miss one URN both read it | `resolve_cache_items` items, FIFO | every write (`add`, `drop`, a transaction) and `clear_resolve_cache()` |
| `_CallScopedMemo` | `local/registry.py` | per call, so no sharing | none; lives for one call | end of the call, promoting into `_FIFOCache` |
| `LazyCodelist._code_map_cache` | `storage/lazy.py` | double-checked lock, one lock per instance across all languages | languages asked for | the instance's lifetime |
| `SchemaInspector`'s column, key-count and partition caches | `local/sql.py` | one lock per inspector | tables described | `invalidate` / `invalidate_all` on a lake write |
| `SdmxClient._freshness` | `api/client.py` | locked writes, not single-flight | `freshness_size`, FIFO | `forget_freshness` |
| `RestRegistry._settled` (`latest` → pinned) | `api/registry.py` | **no lock**; concurrent eviction may race, untested | 256, FIFO | `_forget` |
| `RestRegistry._refused` | `api/registry.py` | no lock | **unbounded** | never |
| `functools.cache` / `lru_cache` | `api/federated.py` (unbounded), `model/urn.py`, others | n/a; pure functions of immutable input | per site | never |

New work goes through `SingleFlight` and does not copy the locking of
`_FIFOCache` or `LazyCodelist`.

Place the cache at the **source** of the cost (e.g. `code_map` /
`LazyCodelist`), not at one call site, so every caller dedups and the
contract holds uniformly.

## Off-request-path warmup

For category-C caches that can't be baked, a server should pay the cold
cost **once, off the request path** — by making the same calls at boot,
before traffic arrives (e.g. one `code_map(lang=…)` per DSD it serves) —
rather than letting an unlucky first request eat it. The library ships no
dedicated `warm_*` API; warming is the caller exercising the ordinary read
path. (It is *not* a substitute for single-flight: warmup handles cold
start, single-flight handles the herd if warmup is skipped or a key is
missed.)

## A cache key covers completeness as well as identity

Two bugs in this codebase were the same mistake, and neither was about
eviction, locking, or staleness. Both were a cache answering the right key
with an answer that was not good enough for the question asked.

- **#338**: a `latest` URN used as a cache key. `latest` names a *pointer*, so
  the key identified something whose meaning changes — the entry was correct
  when written and wrong later.
- **#344**: `Session._resolved_map` recorded *that* a URN had been fetched
  with its references, not *how completely*. A `detail=referencepartial` fetch
  and a `detail=full` one set the same flag, so an explicit `partial=False`
  was answered from the trimmed fetch. Silently, and for a whole release.

The pattern: **a boolean "have we got it" where the real question is ordered.**
"Resolved" is not yes/no, it is `unresolved < partial < full`, and the hit
condition is *at least as complete as asked* — which a flag cannot express.
`api/session.Completeness` is the fix and the shape to copy.

Two things to check for any cache of *fetched* data:

- [ ] Does the key identify something immutable? A pointer, a "current"
      selector, or anything the authority can redefine is not a key. The
      one exception is a pointer the authority revalidates before every
      use: `RestRegistry._settled` maps `latest` to the pinned URN it last
      resolved to (#339), and is read only after a 304 or a freshness
      window the provider granted confirms the pointer has not moved.
- [ ] Can the same key be legitimately requested at different depths,
      details, or projections? If so the entry needs a **level**, compared
      with `>=`, not a flag. Store the deepest seen (`max` on write, so a
      later shallow read cannot downgrade it) and miss when what is held is
      shallower than what was asked.

**Test both directions and both key kinds.** #344 survived a regression test
written specifically for it, because every case in
`tests/unit/test_rest_partial.py` used a `latest` URN — which re-asks for an
unrelated reason (#338) and so passed while the pinned path was broken. That
file is now parametrised over both. A guard that only exercises a case passing
for another reason reads as coverage while checking nothing.

## Checklist for a performance pass

Before adding or extending a cache during optimization work:

- [ ] Profiled the operation **in isolation** (not just the end-to-end
      symptom) and can name the slow line.
- [ ] Confirmed it is not fixable as layout (**B**) at build time.
- [ ] Confirmed the input is genuinely per-process — otherwise bake (**A**).
- [ ] If runtime (**C**): routed through `SingleFlight`, bounded,
      invalidation tied to a lifecycle, placed at the source.
- [ ] The bound is in a unit that **corresponds to what it bounds**, and if
      the values vary in size, it is a measured weight rather than a count
      (#386). The correspondence is written next to the default.
- [ ] Documented the cache's invalidation contract where it lives.
- [ ] Added a regression test asserting the **count** of expensive calls
      (SQL round-trips / builds), not wall-clock — as the resolver-cache
      tests do in `tests/unit/test_storage_query_bounds.py`.

## See also

- [Performance](performance.md) — the resolver cache, how `get` uses it.
- [Concurrency](concurrency.md) — the thread-safety model caches must
  respect.
- `src/sdmxlib/storage/schema.py` — where baked columns like `depth` /
  `ancestor_ids` live.
- Issue [#155](https://github.com/StatCat-Suite/sdmxlib/blob/main/history/gitlab/issues/155.md) — the
  mis-attributed "58 s label map" that motivated this page.
