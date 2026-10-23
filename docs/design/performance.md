# Performance: Bulk Reads & Resolver Cache

How sdmxlib's `LocalRegistry` (DuckDB-backed local store) makes reads cheap.
For *using* the cache, see the
[Local Registry tutorial — §7 Resolver Cache](../local_registry_tutorial.md).
This page is the architecture reference: why each layer exists, what it
guarantees, and how to reason about correctness and memory.

The current shape was designed in response to three field bugs (issues
#72, #73, #74). Each fix removed a specific N+1 pattern. They compose:
together they take a workload that hung for minutes down to sub-second.

## The problem

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
│  LocalRegistry.get / get_all / to_registry                       │
│      │                                                           │
│      ▼                                                           │
│  ┌─────────────────────────────────────────────────────┐         │
│  │ _CallScopedMemo (per-call dict over the _FIFOCache) │         │  Layer 3
│  └─────────────────────────────────────────────────────┘         │  (#74)
│      │                                                           │
│      ▼                                                           │
│  _resolve_refs                                                   │
│      │  collect → batch-resolve by URN → next frontier           │  Layer 2
│      │  item URNs grouped by parent scheme, loaded once          │  (#72)
│      ▼                                                           │
│  readers._read_metadata_structure / _read_codelist_eager / ...   │
│      │  one query per artefact subtree for annotations           │  Layer 1
│      │  (no per-item _read_annotations)                          │  (#73)
│      ▼                                                           │
│  DuckDB                                                          │
└──────────────────────────────────────────────────────────────────┘
```

### Layer 1 — bulk subtree reads (#73)

**File**: `src/sdmxlib/storage/readers.py`.

The per-artefact readers (`_read_codelist_eager`, `_read_concept_scheme`,
`_read_metadata_structure`, `_read_data_structure`, `_read_hierarchy`,
`_read_category_scheme`, `_read_agency_scheme`,
`_read_data_provider_scheme`) used to loop over items and call
`_read_annotations(item_urn)` per item. A 331-concept ConceptScheme
fired ~330+ SQL round-trips just for annotations.

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

Same pattern is also applied to agency contacts inside
`_read_agency_scheme` (the only other place items had a hidden
per-item child query).

After Layer 1, each artefact reader's SQL count is **O(1)** in item
count, not O(items).

### Layer 2 — batched ref resolution (#72)

**File**: `src/sdmxlib/local/registry.py`, methods `_resolve_refs`,
`_collect_refs`, `_resolve_urns`, `_frontier`, `_load_parent_scheme`.

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
   - **Item URNs** (`Code`, `Concept`): grouped by parent scheme URN
     via `_parent_scheme_urn`. Each unique parent is loaded *once*
     (`_load_parent_scheme` is memoised against the same memo), then
     every requested item is looked up in the parent's `items` collection
     by dict access.
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

#### Bounded in items, not entries (`_FIFOCache`)

```python
class _FIFOCache(dict[str, object]):
    def __init__(self, maxitems: int): ...
    def __setitem__(self, key, value):
        # refuse an over-weight value; else insert and evict
        # insertion-oldest until the running weight fits
    def get(self, key, default=None):
        # lock-free plain-dict read, count hits/misses
```

**The bound was an entry count until #386, and an entry count was not a
bound.** Its entries differ in size by four orders of magnitude, so the
number said nothing about the only thing it existed to control. On
the 2021 Census Profile bundle the five largest artefacts total **~4.4 GB and occupy 5 of
256 slots**: a service could report `size: 5, max_size: 256`, look
healthy, and be holding 4.4 GB. `caching.md` requires a runtime cache to
be bounded, and this met the letter of that and nothing else.

The bound is now **items**, and the correspondence to memory is measured
rather than assumed — `tracemalloc`, in one process so a one-time 4.4 MB
warm-up is not charged to every artefact:

| artefact | items | retained | bytes/item |
| --- | --- | --- | --- |
| `CL_GEOGRAPHY` (`Codelist`) | 579,951 | 404.5 MB | **731** |
| `H_CP_GEO_FED2023` (`Hierarchy`) | 499,143 | 913.1 MB | 1,918 |
| `H_CP_GEO_ADMINISTRATIVE` | 63,404 | 130.6 MB | 2,061 |
| `H_CP_GEO_FSA` | 1,660 | 3.3 MB | ~2,000 |
| `H_CP_GEO_HCCSS` | 28 | 0.07 MB | — |
| `DF_CP_2021` (`Dataflow`) | 0 | 0.08 MB | — |

Cost is linear in items *within* a class; the spread *across* classes is
**2.9x**, not 10,000x. That is what makes an item count a fair weigher
where an entry count was not.

**Default `resolve_cache_items=1_000_000`** — 0.7 GB of codes to 2.0 GB
of nodes. It holds either of that bundle's two largest artefacts outright
but not both at once. The point of the number is not that it is optimal;
it is that raising it is now a memory decision someone can make.

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

**How a large entry actually gets here, which is narrower than it looks.**
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

Wire into ops dashboards. **Watch `weight`, not `entries`** — that is the
whole of #386: an entry is an artefact, artefacts differ in size by four
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
- **LazyCodelist / LazyHierarchy.** These wrap the connection cursor
  and do SQL on demand. They're returned directly by `get` without
  going through `_resolve_refs`, so they bypass the cache entirely
  (which is correct — they're cheap to construct, and the per-method
  results they return are already memoised at the lazy level via
  per-instance caches like `LazyCodelist.code_map`).

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
| `src/sdmxlib/local/registry.py` (`_resolve_refs`, `_collect_refs`, `_resolve_urns`, `_frontier`, `_load_parent_scheme`, `_MemoResolver`) | Layer 2: collect / batch / transitive frontier; `Ref`s bound to the memo. |
| `src/sdmxlib/local/registry.py` (`_FIFOCache`, `_CallScopedMemo`, `LocalRegistry._resolve_cache`) | Layer 3: bounded FIFO cache + per-call overlay + invalidation. |
| `tests/unit/test_storage_query_bounds.py` | Regression bounds. Every fix is guarded by a test asserting *count* of SQL round-trips, not wall-clock time — so future N+1 regressions fail in CI. |

## Serialization: typed msgspec Structs (migrated from dict + orjson)

The SDMX-JSON layer now models the wire shape as typed `msgspec.Struct` DTOs
used both directions — `to_*` converters (domain → DTO) encoded via a shared
`msgspec.json.Encoder`, and typed `msgspec.convert` / `msgspec.json.decode`
on read. This replaced the original hand-built `dict[str, Any]` + orjson
design in **milestone #8**. orjson was removed as a dependency.

**Is validation coupled to serialize?** Yes, again. `serialize()` once took
`validate=False` by default, on the reasoning that coupling cost the majority
of JSON serialize time. That was reversed: #205 found five distinct ways to
build a message the SDMX schema rejects, and the measured cost is 4–9% of
serialisation, not a majority. `serialize(validate=True)` is now the default
and runs `check_serialisable` first, raising one accumulated `ValidationError`
before any bytes are produced; `validate=False` skips it deliberately. See the
`serialize` docstring.

**Why the switch to msgspec?** The original decision (below) settled on
dict + orjson and explicitly deferred msgspec. It was revisited because the
motivation was never purely raw speed:

- **Typing.** `dict[str, Any]` is a viral `Any` hole through the whole
  format layer; typed Structs give the checker a real shape both directions
  and localise the untyped boundary to one `msgspec.json.decode` call.
- **Design fit.** A Struct-shaped wire layer separate from the domain model
  is the same split `pysdmx` uses (`from_model`), and it made the read side
  a clean inverse of the write side.
- **Footprint.** msgspec is a small, single-purpose Rust dependency — and it
  *replaced* orjson rather than adding to it, so the dependency count didn't
  grow.

The original "1000 codes" measurement that informed the *first* decision —
kept here as evidence, since it also quantifies the alternatives we rejected:

| approach | time | vs the old dict path |
|---|---|---|
| hand-built dict + orjson (old) | 127 µs | — |
| cattrs unstructure + orjson | 272 µs | 2.1× *slower* |
| dataclass DTO + orjson-native | 292 µs | 2.3× *slower* |
| **msgspec Struct + encode (now)** | **91 µs** | **1.4× faster** |

So the chosen path (msgspec Structs) is also the *fastest* of the four — but
the case for it was typing + design cleanliness, not the ~30% write win.
cattrs and orjson's native-dataclass path remain measurably *slower* than a
hand-written emit and were correctly rejected. Data-plane byte streaming was
migrated separately (#183) and benchmarked flat-to-faster on the large case.

### JSON serialisation after the migration, against pysdmx

A profile of the migrated writer settled a question the table above left open.
Both libraries have a domain-model → wire-Struct conversion (`to_*` for us,
`from_model` for pysdmx) — the encode itself is only ~6% of serialize time in
each; the conversion is ~90%+. So the residual gap to pysdmx was never "we
convert and they don't." It was two things:

1. **They are lossy, we are lossless.** pysdmx's model stores one best-match
   `name: str`; its JSON emits only `name`. We model a full
   `InternationalString` and emit `name` + the `names` lang-map (and
   `text`/`texts` on annotations). Their `from_model` is `name=code.name` (a
   pointer copy); our `to_*` must split every istring into `(best, full-map)`.
   That is real work they skip by *discarding languages* — a price we choose to
   pay for a lossless round-trip.

2. **We had two msgspec knobs still on the table**, both fixed in the perf pass
   that followed:
   - `InternationalString.emit_pair()` — collapse the istring split
     (`__bool__` + `as_dict` + `iter`/`next`) into one method with direct
     backing-map access (~6%).
   - **`gc=False` on the wire-Struct base (`WireStruct`)** — the big one
     (~40% of build+encode). Wire DTOs are transient acyclic trees, so the
     cyclic collector scanning millions of them is pure waste; refcounting
     still frees them promptly. pysdmx doesn't use this.

Net on the 3.8M-obs ESTAT HICP fixture (SDMX-JSON 2.1 serialize, median):
**sdmxlib ~4.0 ms vs pysdmx ~4.6 ms — ~16% faster, while emitting every
language pysdmx drops.** A `gc.is_tracked` guard test (`test_dto.py`) locks the
`gc=False` invariant so a stray `msgspec.Struct` base can't silently re-track a
DTO and undo it.

## See also

- [Local Registry tutorial — §7 Resolver Cache](../local_registry_tutorial.md) — using the cache.
- [Local Registry tutorial](../local_registry_tutorial.md) — the store, end to end.
- Issues [#72](https://gitlab.com/pinax-suite/sdmxlib/-/issues/72),
  [#73](https://gitlab.com/pinax-suite/sdmxlib/-/issues/73),
  [#74](https://gitlab.com/pinax-suite/sdmxlib/-/issues/74) — original
  bug reports with profile data.
