# Performance: Bulk Reads & Resolver Cache

How sdmxlib's DuckDB-backed store makes reads cheap. The read path and the
resolver cache live on `BundleRegistry` (`local/registry.py`), which
`LocalRegistry` extends with writes.
For *using* the cache, see the
[Local Registry tutorial — §7 Resolver Cache](../local_registry_tutorial.md).
This page is the architecture reference: why each layer exists, what it
guarantees, and how to reason about correctness and memory.

The current shape was designed in response to three field bugs (issues
#72, #73, #74). Each fix removed a specific N+1 pattern. They compose:
together they take a workload that hung for minutes down to sub-second.

## The problem

### Record: #72–#74, v0.20.1 to v0.27.2

A real workload — listing every `MetadataSet` of a `Metadataflow`, where
all 293 sets reference the same 30-attribute MSD — exposed the issue:

| Version | Time | What was happening |
|---|---|---|
| v0.20.1 | hung indefinitely (60k-node Hierarchy) | per-Ref SQL probe + per-item annotation read |
| v0.27.0 | 2.9 s | per-item annotation read removed |
| v0.27.1 | 15.1 s (**regression!**) | annotation read fixed for the per-call walk, but the MSD was re-resolved on every `get()` |
| v0.27.2 | < 2 s | registry-level cache (an LRU then, FIFO since) amortises MSD reads across calls |

The pattern is general: when many artefacts share a heavy parent
(`Dataflow → DSD`, `MetadataSet → MSD`, `HierarchicalCode → Code`), an
unmemoised resolver re-loads that parent once per child reference.

## The three layers

```
┌──────────────────────────────────────────────────────────────────┐
│  BundleRegistry.get / get_all                                    │
│      │                                                           │
│      ▼                                                           │
│  ┌─────────────────────────────────────────────────────┐         │
│  │ _CallScopedMemo (per-call dict over the _FIFOCache) │         │  Layer 3
│  └─────────────────────────────────────────────────────┘         │  (#74)
│      │                                                           │
│      ▼                                                           │
│  _resolve_refs                                                   │
│      │  collect → batch-resolve by URN → next frontier           │  Layer 2
│      │  Code / HierarchicalCode by URN; other items by scheme    │  (#72, #378)
│      ▼                                                           │
│  readers._read_metadata_structure / _read_codelist_eager / ...   │
│      │  one query per artefact subtree for annotations           │  Layer 1
│      │  (no per-item _read_annotations)                          │  (#73)
│      ▼                                                           │
│  DuckDB                                                          │
└──────────────────────────────────────────────────────────────────┘
```

`to_registry` uses Layer 1 and skips Layers 2 and 3: it reads every artefact eagerly
into an `InMemoryRegistry` under that registry's binding, and each `Ref`
finds its target there, so nothing is resolved during the load.

### Layer 1 — bulk subtree reads (#73)

**File**: `src/sdmxlib/storage/readers.py`.

Sixteen per-artefact readers in `storage/readers.py` read their
annotations in bulk, among them `_read_codelist_eager`,
`_read_concept_scheme`, `_read_metadata_structure`, `_read_data_structure`,
`_read_hierarchy`, `_read_category_scheme`, the five organisation-scheme
readers, `_read_value_list`, `_read_data_constraint` and the map readers.
Before #73 they looped over items and called `_read_annotations(item_urn)`
per item, so a 331-concept ConceptScheme fired ~330+ SQL round-trips just
for annotations.

The fix is `_read_annotations_subtree(conn, artefact_urn)`, which reads
through `storage/resolve.py`'s `annotation_rows`:

```sql
-- 1. annotation × annotation_text, every annotation the artefact owns
SELECT a.owner_urn, a.id, a.annotation_id, a.title, a.annotation_type,
       a.value, atxt.lang, atxt.text
FROM sdmx.annotation a
LEFT JOIN sdmx.annotation_text atxt ON atxt.annotation_id = a.id
WHERE a.owner_artefact_urn = ?
ORDER BY a.owner_urn, a.ordinal, a.id, atxt.lang

-- 2. only if (1) returned rows: their URLs, under the same predicate
SELECT u.annotation_id, u.url, u.lang, u.link_urn, u.link_type
FROM sdmx.annotation_url u JOIN sdmx.annotation a ON a.id = u.annotation_id
WHERE a.owner_artefact_urn = ?
ORDER BY u.annotation_id, u.position
```

`owner_artefact_urn` is the indexed column the writer sets on every
annotation an artefact owns — its own, every item's, and its unidentifiable
parts' (`<artefact urn>#maps[3]`). It replaced an
`owner_urn = ? OR owner_urn LIKE '%=<suffix>.%'` predicate that selected the
same rows but could use no index: 21 ms to find a DSD's two annotations on a
585,675-row table (#426). The URLs were a correlated subquery in the first
statement until !660; DuckDB planned it badly enough for a point read to cost
320 µs with no annotations at all, so they are now a second statement issued
only when there is something to attach them to.

Returns `{owner_urn: Annotations}`. The reader then does a dict lookup
per item instead of a round-trip. At most two SQL calls cover the parent
plus every code / concept / component / hierarchy node beneath it,
regardless of item class.

The same pattern reads contacts: `_contacts_by_owner` fetches every
contact of a scheme's items in one statement for the five readers whose
items carry contacts (`_read_agency_scheme`, `_read_data_provider_scheme`,
`_read_metadata_provider_scheme`, `_read_data_consumer_scheme`,
`_read_organisation_unit_scheme`).

After Layer 1, each artefact reader's SQL count is **O(1)** in item
count, not O(items).

### Layer 2 — batched ref resolution (#72)

**File**: `src/sdmxlib/local/registry.py`, `BundleRegistry` methods
`_resolve_refs`, `_collect_refs`, `_pending_urns`, `_resolve_urns`,
`_resolve_scheme_items`, `_frontier`, `_load_parent_scheme`.

The old `_resolve_refs` walked the attrs graph depth-first with side
effects: for every `Ref` it found, it fired SQL immediately to resolve
the target. For item URNs (`Code`, `Concept`) that meant re-loading the
parent scheme *per Ref*. A DSD with 20 components into one
`ConceptScheme` of 331 concepts loaded the scheme 20 times. A 60k-node
`Hierarchy` whose nodes referenced the same `Codelist` loaded the
codelist 60k times.

The new implementation is **collect → batch-resolve → follow** with a
frontier loop:

```python
frontier = [obj]
while frontier:
    pending = []
    for item in frontier:
        self._collect_refs(item, walked, pending)  # pure Python, no SQL
    if not pending:
        break

    needed = self._pending_urns(pending, memo)     # dedup, minus the memo
    if needed:
        self._resolve_urns(cur, needed, memo)      # bulk SQL

    frontier = self._frontier(pending, memo, walked)  # transitive resolution
```

**Nothing is assigned.** Before #492 the third stage `setattr`-ed a resolved
`Ref` back onto its owner. The model is now `@frozen`, and every `Ref` a store
read builds is *bound* as it is built to a `_MemoResolver` over this call's
memo: the `Ref` finds its target by looking its URN up there on access. So
filling the memo is what resolves the graph, and the third stage only decides
what to walk next.

**Three phases:**

1. **Collect** — `_collect_refs` recurses through `attrs.fields`,
   appending `(owner, field_name, Ref)` tuples for every unresolved
   `Ref` it finds. Uses `walked: set[int]` to avoid revisiting the same
   object instance (cycle-safe). No SQL.
2. **Resolve** — `_resolve_urns` partitions the deduplicated URNs:
   - **Top-level URNs** (e.g. `MetadataStructure`, `Dataflow`): one
     `readers.get_artefact()` per distinct URN, no fan-out.
   - **Item URNs**: grouped by parent scheme URN via `_parent_scheme_urn`,
     then served by `_resolve_scheme_items` along the cheapest of three
     routes. A parent scheme already built in the memo serves its items
     for free. Otherwise a `Code` or `HierarchicalCode` URN is read by URN,
     one batched query per parent (`resolve.codes_by_urn`,
     `resolve.hcodes_by_urn`, #378), and kept for this call only
     (`set_local`). A `Concept`, or an item URN with `version="latest"`,
     loads its parent scheme *once* (`_load_parent_scheme`, memoised
     against the same memo) and looks each item up in it.
3. **Follow** (`_frontier`) — no write: each owning `Ref` is already bound
   to the memo, so it resolves the moment the memo holds its target.
   Newly-resolved targets that haven't been walked yet enter the next
   frontier; their own Refs get resolved transitively in
   the next loop iteration (e.g. resolving a `Dataflow → DSD` triggers
   walking the DSD's component Refs).

**Result.** SQL round-trips for resolution are **O(distinct parent
schemes + distinct top-level URNs)** in the call, not O(refs).

### Layer 3 — bounded resolver cache (#74, bound corrected in #386)

**File**: `src/sdmxlib/local/registry.py`, classes `_FIFOCache` and
`_CallScopedMemo`.

Layer 2 collapses fan-out *within* a single `_resolve_refs` call. But
across calls — `for u in urns: reg.get(u)` — each call had a fresh
memo, so any shared parent was re-loaded N times.

Layer 3 promotes the memo from per-call to per-registry, with two
critical refinements: **bounded** and **per-call overlay**.

#### Bounded in items (`_FIFOCache`)

```python
class _FIFOCache(dict[str, object]):
    def __init__(self, maxitems: int): ...
    def __setitem__(self, key, value):
        # refuse an over-weight value; else insert and evict
        # insertion-oldest until the running weight fits
    def get(self, key, default=None):
        # lock-free plain-dict read, count hits/misses
```

**Until #386 the bound counted entries, which bounded nothing useful.** Its
entries differ in size by four orders of magnitude, so the
number said nothing about the only thing it existed to control. On
the 2021 Census Profile bundle the five largest artefacts total **~4.4 GB and occupy 5 of
256 slots**: a service could report `size: 5, max_size: 256`, look
healthy, and be holding 4.4 GB. `caching.md` requires a runtime cache to
be bounded, and an entry count met that requirement in name only.

The bound is now **items**, and the correspondence to memory is measured.

##### Record: #386, bytes per item on the 2021 Census Profile bundle

Measured with `tracemalloc`, in one process so a one-time 4.4 MB warm-up is
not charged to every artefact:

| artefact | items | retained | bytes/item |
| --- | --- | --- | --- |
| `CL_GEOGRAPHY` (`Codelist`) | 579,951 | 404.5 MB | **731** |
| `H_CP_GEO_FED2023` (`Hierarchy`) | 499,143 | 913.1 MB | 1,918 |
| `H_CP_GEO_ADMINISTRATIVE` | 63,404 | 130.6 MB | 2,061 |
| `H_CP_GEO_FSA` | 1,660 | 3.3 MB | ~2,000 |
| `H_CP_GEO_HCCSS` | 28 | 0.07 MB | — |
| `DF_CP_2021` (`Dataflow`) | 0 | 0.08 MB | — |

Cost is linear in items *within* a class, and the spread *across* classes is
**2.9x**. That is what makes an item count a fair weigher where an entry
count was not.

**Default `resolve_cache_items=1_000_000`** — 0.7 GB of codes to 2.0 GB
of nodes. It holds either of that bundle's two largest artefacts outright
but not both at once. The default is a starting point: raising it is now a
memory decision someone can make.

**A floor of 64 items per entry.** A zero-item artefact still retains
0.00–0.08 MB, about what 64 items weigh. Without a floor a flood of tiny
artefacts is unbounded — the one thing an entry count *was* good at. It
is also what a lazy artefact weighs, correctly: a `LazyCodelist` holds a
URN and a connection, not 579,951 codes.

**An entry heavier than the whole bound is refused, not admitted.**
Caffeine and Guava admit and then evict; here that would mean one
over-budget read evicting the entire working set on its way to evicting
itself. Refusing keeps resident weight under the bound at every instant.
`resolve_cache_info()["refused"]` counts them.

**A large entry arrives by one narrow route.**
Measured while fixing #386: ordinary reads on the 2021 Census Profile bundle leave the
cache holding *floor-weight entries only* — a `Metadataflow`, a
`MetadataStructure`, a `LazyHierarchy`, 64 apiece. #378 rerouted item
resolution to a targeted by-URN read, so resolving a `Ref[Code]` no longer
drags in the whole codelist. What remains of the old path is
`_items_from_loaded_scheme`, kept for two cases: a `Concept`, and a
`version="latest"` item URN whose stored `code_urn` carries a concrete
version and so cannot be matched directly. That second case still loads
the parent scheme eagerly and promotes it, so a bundle whose refs say
`latest` puts 579,951 codes and ~400 MB into one slot.

So the 4.4 GB figure #386 opens with is reachable, but by that route
rather than by the default read path. The bound is worth having either
way — an entry count could not describe the state above, and how large an
entry will turn out to be is not something a cache gets to assume.

`maxitems=0` silently drops inserts, effectively disabling caching for
workloads where each request resolves unique URNs.

Weighing is O(1) — the length of an already-built `ItemList`, never a
traversal and never a query. An estimate in bytes would be more honest
and would cost a walk of the whole graph on every insert.

#### Per-call overlay (`_CallScopedMemo`)

```python
class _CallScopedMemo(dict[str, object]):
    def __init__(self, cache: _FIFOCache): ...
    def __contains__(self, key):
        return super().__contains__(key) or key in self._cache
    def get(self, key, default=None):
        # local dict first, fall through to the FIFO cache
    def set_local(self, key, value):
        # this call only — never promoted (scheme items, #378)
    def promote(self):
        # at end of call, push this call's entries into the FIFO cache
```

Writes during `_resolve_refs` go to the per-call dict, **not** directly
to the shared cache. Two reasons:

1. **Correctness under tight cache sizes.** If a write went straight to
   a small cache, an entry could be evicted between the write (in
   `_resolve_urns`) and the read (in `_frontier`, in
   `_load_parent_scheme`, or by a bound `Ref` on access). Refs would be
   left unresolved. The overlay holds in-flight entries in an unbounded
   per-call dict, so single-call resolution is independent of cache size —
   including `maxitems=0`.
2. **Promote-on-success.** Entries enter the cache only at the end of the
   call, via `promote()`. If the call raises midway, the cache state
   doesn't get half-populated with results from a failed walk.

#### Cache invalidation

The cache is cleared on every `add` / `drop` / `replace`. Any of these
could invalidate any parent→child relationship anywhere in the cache,
so the safe move is a full clear rather than tracking which URNs
depend on which.

For typical workloads (read-heavy with batched mutations: `add_all` /
`ingest` followed by long read phases), clears during ingestion are
no-ops because the cache is empty during population. The cache warms
during the read phase.

For explicit invalidation outside the mutation API (e.g. a webhook
telling the service the upstream registry mutated):

```python
local.clear_resolve_cache()
```

#### Why `walked` is per-call

`_collect_refs` uses `walked: set[int]` of `id(obj)` to avoid
re-walking the same artefact. If `walked` were shared across calls,
stale ids could collide with new objects after eviction (Python's
`id()` is just a memory address; freed addresses get reused).

Per-call `walked` removes that hazard entirely. Re-walking a
cache-hit target on each call is cheap: it's pure-Python attr
iteration, and `_collect_refs` short-circuits at every Ref that's
already resolved.

## Observability

```python
local.resolve_cache_info()
# {"entries": 42, "weight": 812_004, "max_items": 1_000_000,
#  "hits": 9123, "misses": 50, "refused": 0}
```

Wire into ops dashboards. **Watch `weight`, not `entries`**, as #386 showed: an entry is an artefact, artefacts differ in size by four
orders of magnitude, and five of them can be 4.4 GB. `entries` is still
reported because a hit rate is per lookup rather than per item.

A near-100% hit rate on a workload that iterates structurally-related
artefacts confirms the cache is doing its job. A near-0% rate has two
different causes and `refused` tells them apart:

- `refused == 0` — each request resolves unique URNs. Lower
  `resolve_cache_items`, or set it to `0`.
- `refused` rising — the working set contains artefacts too heavy for the
  bound to hold at all, so they are never resident. Raise
  `resolve_cache_items` past the largest one, or accept the re-read.

## Correctness invariants

The implementation maintains these:

- **Idempotent.** Already-resolved Refs are skipped in `_collect_refs`,
  so calling `_resolve_refs` twice on the same artefact does no extra
  SQL.
- **Cycle-safe.** Per-call `walked` prevents infinite recursion.
- **Order-independent.** Resolution doesn't depend on which Ref is
  resolved first; the dedup-by-URN step ensures each URN is loaded at
  most once per call, and the memo handles transitive references in
  any order.
- **Mutation-safe.** Every mutation path clears the cache. Within a
  single resolve call, the per-call overlay guarantees writes are
  visible to later reads regardless of FIFO eviction.
- **Partial-failure-safe.** A `_resolve_refs` call that raises before
  `promote()` leaves the shared cache untouched.

## Uncached results

- **Top-level `reg.get(urn)` results.** The resolver cache holds
  artefacts only as Ref targets (the things `_resolve_urns` loaded to
  satisfy a Ref). A direct `reg.get(msd_urn)` still pays for the
  primary read. Caching `get` returns is feasible (per SDMX IM,
  MaintainableArtefacts are versioned and effectively immutable), but
  has not been done; `caching.md` asks for a profile naming the cost
  first.
- **A LazyCodelist / LazyHierarchy returned by `get`.** These hold the
  connection and do SQL on demand. `get` returns one directly, without
  `_resolve_refs` and without caching it; they are cheap to construct, and
  `LazyCodelist.code_map` memoises its own results per instance. A facade
  reached as a **Ref target** is different: a DSD's read resolves its
  codelists as facades, and those enter the memo and are promoted into the
  resolver cache like any other target. `_items_from_cached_scheme` then
  declines to serve items from a cached facade, because its subscript
  queries the store once per item (GH-150).

## Performance budget — what to expect

For a workload reading N artefacts that all reference one heavy parent
P with `resolve_cache_items >= 1`:

- **First call**: 1 read for the artefact + 1 read for P + transitive
  refs from P.
- **Each subsequent call** (within the cache window): 1 read for the
  artefact, 0 reads for P (cache hit), 0 reads for P's transitive
  refs (also cached as side effects of resolving P).

So N calls cost `N + 1 + transitive(P)` reads instead of
`N * (1 + 1 + transitive(P))`. For a downstream SDMX REST service's listing (N=293,
MSD=45ms), that's ~2s instead of ~15s.

## Where to look in the code

| File | What it does |
|---|---|
| `src/sdmxlib/storage/readers.py` | Layer 1: `_read_annotations_subtree` and the per-artefact readers that consume it. |
| `src/sdmxlib/storage/resolve.py` (`annotation_rows`) | Layer 1: the two annotation statements, by `owner_artefact_urn`. |
| `src/sdmxlib/local/registry.py` (`_resolve_refs`, `_collect_refs`, `_resolve_urns`, `_resolve_scheme_items`, `_frontier`, `_load_parent_scheme`, `_MemoResolver`) | Layer 2: collect / batch / transitive frontier; `Ref`s bound to the memo. |
| `src/sdmxlib/storage/resolve.py` (`codes_by_urn`, `hcodes_by_urn`) | Layer 2: the by-URN item reads (#378). |
| `src/sdmxlib/local/registry.py` (`_FIFOCache`, `_CallScopedMemo`, `BundleRegistry._resolve_cache`) | Layer 3: bounded FIFO cache + per-call overlay + invalidation. |
| `tests/unit/test_storage_query_bounds.py` | Regression bounds. Every fix is guarded by a test asserting *count* of SQL round-trips, not wall-clock time — so future N+1 regressions fail in CI. |

## Serialization

The SDMX-JSON layer models the wire shape as typed `msgspec.Struct` DTOs
used in both directions: `to_*` converters (domain → DTO) encoded through
the shared `_dto.ENCODER`, and typed `msgspec.json.decode` on read
([JSON and parsing](json-and-parsing.md) is the design). `serialize()`
validates by default: `serialize(validate=True)` runs `check_serialisable`
first and raises one accumulated `ValidationError` before any bytes are
produced (#205); `validate=False` skips it. The measurements behind the
msgspec choice and the comparison with pysdmx are in
[evidence/serialization.md](evidence/serialization.md).

## See also

- [Local Registry tutorial — §7 Resolver Cache](../local_registry_tutorial.md) — using the cache.
- [Local Registry tutorial](../local_registry_tutorial.md) — the store, end to end.
- Issues [#72](https://github.com/StatCat-Suite/sdmxlib/blob/main/history/gitlab/issues/72.md),
  [#73](https://github.com/StatCat-Suite/sdmxlib/blob/main/history/gitlab/issues/73.md),
  [#74](https://github.com/StatCat-Suite/sdmxlib/blob/main/history/gitlab/issues/74.md) — original
  bug reports with profile data.
