# Sharded bundle catalog: design

Status: draft for prototyping. Reviewed against sdmxlib 0.72.1 and statcatlib; the corrections below are open issues in the [Sharded bundle catalog](https://github.com/StatCat-Suite/sdmxlib/milestone/9) milestone, and GH-136 is the cross-repo plan. Where a section and a correction disagree, the correction holds. Audience: implementers working on sdmxlib, statcatlib and sdmxlib-rest.

This document specifies how a dissemination catalog of many SDMX products is published as a set of bundles, indexed, routed to serving pods, verified, cached and migrated. It builds on two published formats and changes both:

- sdmxlib bundle format, manifest 1.1 and overlay 1.0: https://statcat-suite.github.io/sdmxlib/bundle/
- statcatlib bundle format, statcat section 2: https://statcat-suite.github.io/statcatlib/bundle-format/

MUST, MUST NOT, SHOULD and MAY carry their RFC 2119 meanings. A *producer* writes a bundle or an index; a *reader* opens one; a *server* is a reader that answers SDMX REST requests.

---

## Review corrections

Found by checking this design against sdmxlib 0.72.1 (DuckDB 2.0.0) and statcatlib. Each is an open issue; the sections stay as drafted until it lands.

- **Fingerprint (§4.3, M5): GH-126.** The table SQL returns one value per column, misses values moved between rows, and collides on duplicate pairs under `bit_xor` + `count`. DuckDB's `hash` is not stable across releases. SDMX-JSON cannot carry 28 model fields, so the artefact fingerprint uses the model. The canonical form needs a version.
- **Read-only open (§4.2, M0): GH-122.** It works: two processes read one bundle, prototyped. A read-only database's default `temp_directory` is inside the bundle, so it MUST be set outside it. DuckDB 2.0 does replay a leftover WAL on a read-only open; checkpointing is still required, because the WAL is outside the checksummed file. `BundleRegistry` needs its own constructor, since a return type cannot depend on whether a file exists.
- **Token before the build (§4.4, §10.1, M1): GH-123.** `write_manifest` mints the token after the files are written.
- **Storage version (§4.2, §9.1): GH-124.** Refuse only a storage version newer than the reader supports; DuckDB reads older ones.
- **Reader-required features (§4.1, §4.2, §10.4): GH-125.** A 1.1 reader cannot ignore `data_path` or a `.sig` sidecar. A blob store does not compute sha256, so remote data is size-checked unless the store's digest is recorded.
- **`references` as SQL (§4.7): GH-129.** The table is `artefact_ref`; item references span 110 declared edges in `query/_edges.py`.
- **Effective token (§5.1, §5.2): GH-131.** Merge hooks are per annotation type, and a `null` scope is undefined.
- **Render key (§7.3): GH-132.** Cache keys must change when a writer's output changes.
- **Memory (§7.1, §7.4, §8): GH-133.** `memory_limit` and threads are per DuckDB instance, so 16 attached bundles oversubscribe a pod.
- **Structure routing (§2.1, §6.3): GH-136.** `/structure` queries and `latest` versions span bundles, so the index needs artefact URNs and version lists.
- **statcatlib traps:** trap 15 is fixed (the statcat schema is checked before any DDL); trap 16 is partly fixed (orphaned language tables are dropped).

---

## 1. Vision

One bundle is one publication of one product release: its structures, its observations, its catalog records and its search index, in one directory, verified by one manifest. A catalog of 800 products is 800 bundles and one index that says which bundle holds which dataflow. A serving pod attaches the bundles its routing slice needs and nothing else. A publication never changes in place; a new publication is a new directory and the index moves to it. A bundle moves between a build job, a blob store, a pod, a backup and another agency's machine without any of them rewriting it.

The properties this buys:

1. **Publish unit equals shard unit equals backup unit equals cache unit.** One concept, one directory, one identity.
2. **Immutability by construction.** A publication has a token; its directory is named by that token; readers of the old token keep a consistent view while the index flips.
3. **Verification without a database.** The index is a function of manifests. Routing, integrity checks and fleet rollout decisions read JSON, never DuckDB.
4. **Independent lifecycles.** Census 2021 is final and never rebuilt; the CPI bundle republishes monthly. One lifecycle does not pay for the other.
5. **Portability.** A bundle is a directory with checksums. Another NSO can receive one, run `check_bundle`, and serve it.

## 2. Motivation

### 2.1 The workload

SDMX REST `getdata` hits one dataflow per request. Cross-dataflow joins do not occur on the serving path. `isFinal` vintages never change. Products are published as units (a census release, a monthly CPI update). The catalog grows by product count, 800+ in CODR terms, while the per-request working set stays one dataflow.

This is functional partitioning (database-per-tenant), the easy form of sharding. There are no hot keys to rebalance, no cross-shard transactions and no live resharding.

### 2.2 The single-catalog limits

A server today runs one DuckLake catalog file on the pod. At 800 products the single file costs:

- cold start proportional to the whole catalog, on every pod;
- a republish of one product redistributes the whole file to every pod;
- one backup unit for data with 800 independent change cadences;
- one DuckDB storage version for everything, so one writer upgrade can make the file unreadable for every reader at once.

### 2.3 What the bundle formats already give

The sdmxlib bundle is the shard. It has a stable `bundle_id`, a minted publication `token`, per-file SHA-256, a schema check before open, a manifest written last through rename, overlays with a reproducible effective token, and conformance checks. The statcat bundle adds the DCAT catalog schema, per-language LanceDB search tables with their own generation scheme, and an annotation fingerprint that already demonstrates logical (byte-independent) content hashing.

### 2.4 What is missing

- A document above the bundle: the catalog index.
- Immutable publication directories and a copy rule.
- Integrity of the manifest itself.
- The DuckDB and DuckLake versions that wrote the files.
- An artefact inventory readable without opening DuckDB.
- A logical content fingerprint, so a rebuild that changes nothing costs nothing.
- Two cache-correctness holes in the effective token (overlay `scope`, merge hooks).
- Written-down invariants for shared structures and pinned URNs.
- A rollout procedure for schema bumps across a fleet.

Sections 4 to 12 supply these.

---

## 3. Architecture

```
                 build job                        blob store                     serving tier
┌──────────────────────────────┐    ┌───────────────────────────────┐    ┌────────────────────────┐
│ FMR / registry (structures)  │    │ bundles/<bundle_id>/<token>/  │    │ pod: ShardRouter       │
│ ingest → bundle dir          │───▶│   bundle.manifest.json        │───▶│   index (polled)       │
│ build_index → publish_bundle │    │   meta.duckdb lake.ducklake   │    │   LRU of attached      │
│ check_bundle(at="publish")   │    │   parquet/ search/            │    │   bundles              │
│ build_index_document         │    │ catalog/<index token>.json    │    │   effective-token      │
│                              │    │ catalog/current → token       │    │   result cache         │
└──────────────────────────────┘    └───────────────────────────────┘    └────────────────────────┘
```

Layers, bottom to top:

| layer | document | owner | identity |
|---|---|---|---|
| sdmxlib bundle | `bundle.manifest.json` | sdmxlib | `token` (UUIDv7) |
| statcat bundle | `extensions.statcat`, `search/index.json` | statcatlib | same token; search `generation` |
| overlay | `<name>.overlay.json` | server operator | overlay digest |
| catalog index | `catalog.index.json` | index builder (statcatlib or the publication registry) | index `token` |
| routing | in-memory shard map | sdmxlib-rest | index token |
| caches | result store, CDN | sdmxlib-rest | effective token |

Each layer is a pure function of the layer below plus its own inputs. The index is computed from manifests. The shard map is computed from the index. Cache keys are computed from tokens.

---

## 4. Bundle format changes (manifest 1.2)

All additions are optional in 1.2 and become required in 2.0. A 1.x reader ignores what it does not know, per the existing rule.

### 4.1 New manifest fields

| field | presence | meaning |
|---|---|---|
| `previous_token` | optional | The `token` of the publication this one supersedes, or `null` for the first. |
| `content_fingerprint` | optional | Logical digest of the bundle's content (§4.3), or `null` when the producer did not compute it. |
| `duckdb_version` | optional | The DuckDB release that wrote `meta.duckdb` and `lake.ducklake`, such as `"1.4.1"`. |
| `duckdb_storage_version` | optional | `PRAGMA storage_info` / `database_size` storage version of `meta.duckdb`. |
| `ducklake_version` | optional | The DuckLake extension version that wrote `lake.ducklake`. |
| `ducklake_spec_version` | optional | The DuckLake spec version recorded in the catalog (`ducklake_metadata` table). |
| `data_path` | optional | Where the lake's data files live, relative to the bundle or an absolute URI. Default `"parquet/"`. |
| `artefacts` | optional | Every maintainable artefact in `meta.duckdb`: a list of `ArtefactEntry` sorted by `urn`. |

`BindingCount` gains:

| field | presence | meaning |
|---|---|---|
| `structural_version` | optional | The DSD URN (pinned) the binding was written against. |

`ArtefactEntry`:

| field | presence | meaning |
|---|---|---|
| `urn` | required | The pinned URN. |
| `stability` | required | `"semantic"` for a three-part version without extension, which SDMX 3.0 guarantees immutable; `"legacy"` for a two-part or otherwise non-semantic version, which carries no such guarantee. |
| `fingerprint` | optional for `semantic`, required for `legacy` | Logical digest of this artefact (§4.3). |

`extensions.<name>` sections SHOULD carry their own `format_version`. statcatlib's `format: 2` already does under a different key; leave it.

### 4.2 Reader rules added

- A reader MUST compare `duckdb_storage_version` and `ducklake_spec_version` with what its DuckDB can open **before opening either file**, when the fields are present, and refuse with a named error (`StoreFormatError`, naming both versions) on mismatch. This joins the `schema_version` check as a pre-open check.
- A reader MUST resolve data files through `data_path` when present. `OVERRIDE_DATA_PATH` already does the work; the manifest makes the location explicit instead of implied by directory layout.
- A reader MUST open a published bundle (one with a manifest) read-only: `meta.duckdb` with `access_mode=READ_ONLY` and the lake with `ATTACH ... (READ_ONLY, OVERRIDE_DATA_PATH ...)`. Any number of processes may hold a published bundle open at once. Each process sets its own `temp_directory`. `LocalRegistry.bundle(path)` applies this rule whenever `bundle.manifest.json` is present and returns a `BundleRegistry` carrying the read surface only (`get`, `query`, `obs`, `dsd`, `binding_for`, `browse`, read-only `sql`), so the absence of `add`, `drop`, `replace`, `ingest` and `fold_overlays` is a property of the type. An unpublished directory keeps the writable open for builders. statcatlib's `Embedded()` policy is this rule.
- A producer MUST `CHECKPOINT` `meta.duckdb` and `lake.ducklake` before writing the manifest, so no `.wal` file exists. A read-only open cannot replay a write-ahead log, and the `files` check refuses an unlisted `.wal` with a message naming the missing checkpoint.
- A reader MUST ignore a sidecar named `bundle.manifest.json.sig` in the `files` check. The name is reserved for a detached signature over the manifest bytes. Authenticity is out of scope for this document; the name is reserved now so the "no unlisted file" rule never has to be relaxed later.

### 4.3 Content fingerprint

`token` answers "which publication". `content_fingerprint` answers "is the content the same". Both are needed: a rebuild after a schema bump mints a new token and should keep the fingerprint.

Definition. The lowercase hex SHA-256 of the UTF-8 text formed by joining with `\n`, in this order:

1. `artefacts:` then each `ArtefactEntry.fingerprint`, in `urn` order;
2. `bindings:` then, for each binding in `dataflow` order, `<dataflow urn>\t<table fingerprint>`.

Artefact fingerprint: SHA-256 of the artefact's canonical SDMX-JSON (sorted keys, no whitespace, every optional present as `null`, annotations in stored order). sdmxlib already canonicalises overlays this way; reuse the serialiser.

Table fingerprint: order-independent, computed in DuckDB without materialising rows:

```sql
SELECT bit_xor(hash(COLUMNS(*)))::VARCHAR || ':' || count(*)::VARCHAR
FROM lake.<schema>.<table>;
```

`hash(COLUMNS(*))` hashes every column of a row; `bit_xor` folds order-independently; the count guards against the xor of duplicate rows cancelling. Cast each column to its canonical text first if DuckDB's `hash` of a type is version-sensitive (verify on the pinned DuckDB release; record the DuckDB version used in the manifest, which 4.1 does).

Cost. Artefacts: proportional to artefact size, same as serialising them. Tables: one scan per table. On a 2.6 GB census bundle, estimate under a minute. A producer MAY skip it and write `null`; a cache MAY key on it only when present.

Uses:

- A cache (result store, CDN) MAY key on `content_fingerprint` when both the old and new publication carry one and they are equal; otherwise it keys on `token` (or effective token).
- The index builder MAY mark a republish as "equivalent" and keep old result-store entries alive.
- After a schema-bump rebuild, `check_bundle` with `expect_fingerprint=` proves the rebuild preserved content.

### 4.4 Invariants, stated as requirements

- **Closure.** A bundle MUST resolve every reference inside itself. The `references` check already enforces it; the layout section states it.
- **Pinned URNs.** Every artefact MUST be stored under its pinned URN. A `latest` resolution during build records the pinned URN it resolved to.
- **Equivalence of copies.** Two bundles holding the same pinned URN MUST hold the same logical content. SDMX 3.0 guarantees this for semantic versions (`2.0.0`) and says nothing for legacy versions (`2.0`), which is what current StatCan structures carry. So the guarantee for legacy versions comes from the fingerprint: `artefacts[].fingerprint` is required for `stability: "legacy"`, and the index builder refuses two bundles whose fingerprints for one legacy URN differ (§6.4). The registry's "a pinned URN caches indefinitely" rule carries the same qualification: a legacy pinned URN is cached under `(urn, fingerprint)`.
- **Flushed lake.** A producer MUST run `ducklake_flush_inlined_data` and SHOULD expire snapshots and run `ducklake_cleanup_old_files` before writing the manifest. A published bundle holds its rows in `parquet/` (or `data_path`) and ships one snapshot.
- **Compaction for final publications.** A producer SHOULD merge adjacent files to one sorted file per table when the publish pipeline declares the bundle's `lifecycle` as `final` (§6.3). Artefact-level finality plays no part: a publication's immutability comes from its token directory, and whether the producer will publish the bundle again is a product-lifecycle fact held in the publication registry, independent of version syntax.
- **Publication directories.** A producer MUST write a publication into a directory named by its token and MUST NOT rewrite a published directory (§10).

### 4.5 `check_bundle` profiles

Replace the prose "runs when built or received, never when opened" with an argument:

```python
check_bundle(path, at: Literal["publish", "receive", "open"], ...)
```

| check | publish | receive | open |
|---|---|---|---|
| manifest parse, format | yes | yes | yes |
| files: size and sha256 | yes | yes, once per token (stamp, §4.6) | size only |
| schema_version, storage versions | yes | yes | yes |
| lake | yes | yes | no |
| bindings counts | yes | yes | no |
| references (SQL, §4.7) | yes | yes | no |
| content_fingerprint recompute | yes | optional | no |
| extensions (statcat) | yes | yes | section only |

### 4.6 Receive-time stamp

Re-hashing a multi-gigabyte bundle on every pod restart is waste. After a successful receive-time check a reader MAY write `<pod-local>/verified/<bundle_id>/<token>` recording the manifest SHA-256 and the check report. A later open at the same token with the same manifest SHA-256 runs the `open` profile. The stamp lives outside the bundle directory; a bundle directory is never written after publication.

### 4.7 `references` as SQL

The store is relational. The current check loads every artefact (30 s and 5 GB on 2.6 GB). Replace with anti-joins:

```sql
SELECT r.source_urn, r.target_urn
FROM sdmx.reference AS r
LEFT JOIN sdmx.artefact AS a ON a.urn = r.target_urn
WHERE a.urn IS NULL;
```

plus the item-level equivalents (codes, concepts, categories referenced by URN with dotted paths). Keep the loading version as a differential oracle in the test suite. Expected cost: milliseconds, so the check moves into the receive profile.

---

## 5. Overlay changes (overlay 1.1 / effective token)

Two holes let two servers, or two audiences on one server, share an effective token for different bodies.

### 5.1 Scope selection enters the token

A server that applies only entries whose `scope` matches an audience MUST compute the overlay digest over the overlay **as applied**: the canonical JSON of the overlay with non-matching entries removed. Equivalently, the server MAY append `scope=<audience>` to the token string after the overlay digests. The first is preferred: the digest then describes what was applied, and two audiences with identical applied entries share a key.

### 5.2 Merge hook identity enters the token

The effective token string becomes:

```
<manifest token>\n<hook id>\n<overlay digest 1>\n<overlay digest 2>...
```

where `<hook id>` is `default` for the replace-only hook sdmxlib ships, and otherwise `<package>:<name>@<version>`. A server with a custom hook MUST register an id. Two servers with different hooks now produce different tokens.

### 5.3 Search-affecting overlays

A serve-time overlay that acts on a type in `SEARCH_ANNOTATION_TYPES` changes what search should return and the index cannot follow. A server that serves search MUST refuse such an overlay at load (`overlay_affects_search` is the test) and log which entries caused the refusal. The fix is `fold_overlays` then `build_index` then `publish_bundle`.

### 5.4 Names

Overlays change annotations only. The common bilingual correction at StatCan is a mistyped French `name` on a code. Decision needed: either extend `OverlayAnnotation` with sibling `name`/`description` operations on items and artefacts, or state on the page that names are structure and need a rebuild. Recommendation: extend, because the selector and specificity machinery already reaches items, and `name` has the same `MAP(lang, text)` shape as `text`.

---

## 6. Catalog index (index format 1.0)

### 6.1 Purpose

One JSON document lists every published bundle the catalog serves. It is built from manifests and `statcat.dataset` rows and never from `sdmx.*`. It is what a pod polls, what a router loads, what backup enumerates and what another agency receives to mirror a catalog.

### 6.2 Layout in the blob store

```
bundles/<bundle_id>/<token>/           one publication; never rewritten
catalog/index/<index token>.json       one index publication; never rewritten
catalog/current                        a small file holding the current index token
```

`catalog/current` is the only mutable object. It is written with an atomic put (blob lease or ETag-conditional write). Readers read `current`, then the index it names, then bundles the index names. A reader that already holds an index keeps using it until it chooses to move.

### 6.3 Document

`catalog.index.json`:

| field | presence | meaning |
|---|---|---|
| `format_version` | required | `"1.0"`. Same tolerance rules as the manifest: ignore unknown fields, refuse another major. |
| `token` | required | UUIDv7 minted at each index publication. |
| `previous_token` | required | The index this supersedes, or `null`. |
| `created_at` | required | RFC 3339 UTC. |
| `producer` | required | `Producer`, as in the manifest. |
| `schema_versions` | required | The set of sdmxlib `schema_version` values present across entries, sorted. A reader learns from this field alone whether it can serve anything. |
| `bundles` | required | `IndexEntry` list sorted by `bundle_id`, then `token`. |
| `products` | optional | `ProductGroup` list (§6.5). |
| `extensions` | optional | Sections keyed by package name, as in the manifest. |

`IndexEntry`:

| field | presence | meaning |
|---|---|---|
| `bundle_id` | required | From the manifest. |
| `token` | required | From the manifest. |
| `previous_token` | required | From the manifest (1.2) or `null`. |
| `location` | required | URI of the publication directory. |
| `manifest_sha256` | required | SHA-256 of `bundle.manifest.json` bytes. |
| `manifest_size` | required | Its size in bytes. |
| `schema_version` | required | sdmxlib store schema. |
| `duckdb_storage_version` | optional | From the manifest. |
| `ducklake_spec_version` | optional | From the manifest. |
| `content_fingerprint` | optional | From the manifest. |
| `created_at` | required | From the manifest. |
| `bytes` | required | Sum of `files[].size`. |
| `dataflows` | required | List of `{urn, observations, structural_version, stability}` from `bindings` and `artefacts`. |
| `lifecycle` | required | `"final"` when the publisher commits not to publish this `bundle_id` again except as a content-preserving rebuild (fingerprint equal); `"live"` otherwise. Set by the publish pipeline from the publication registry; never derived from artefact versions. |
| `artefacts` | optional | Count, or the full URN list when the index is small enough to carry it. Default: count. The per-bundle manifest holds the list. |
| `datasets` | optional | `extensions.statcat.datasets`. |
| `search` | optional | `{languages: [...], embedding_model}` from the statcat section. |
| `state` | required | `"serving"`, `"draining"` or `"retired"` (§10.3). |
| `pool` | required | The serving pool this bundle is routed to, such as `"hot"` or `"base"` (§7.4). |
| `pinned` | required | `true` when every pod in the pool MUST attach this bundle before reporting ready (§7.4). Default `false`. |

`ProductGroup` (§6.5):

| field | presence | meaning |
|---|---|---|
| `product_id` | required | Stable id, such as `"census-profile"`. |
| `members` | required | List of `{bundle_id, role}` where role is `"current"`, `"vintage"` or `"superseded"`. |

### 6.4 Invariants the builder enforces

The builder refuses to write an index that violates any of these, naming the entries:

1. **One home per dataflow.** A pinned dataflow URN appears in exactly one entry with `state = "serving"`.
2. **Manifest integrity.** `manifest_sha256` matches the bytes fetched.
3. **Schema set is explicit.** Every entry's `schema_version` appears in `schema_versions`.
4. **Shared artefacts agree.** For every pinned artefact URN present in more than one entry's manifest with a `fingerprint`, the fingerprints are equal. A `legacy` artefact without a fingerprint is refused. On disagreement the builder refuses; the fix is rebuilding the stale bundle, never patching the index.
4a. **Final means final.** A new publication of a `bundle_id` whose previous entry was `lifecycle: "final"` MUST carry an equal `content_fingerprint`; otherwise the builder refuses, and the publisher either reverts or first republishes the index with the entry set to `live`.
5. **Chain continuity.** An entry's `previous_token` names a token that appeared in the previous index, or `null`.
6. **Draining pairs.** A `draining` entry has a `serving` entry with the same `bundle_id` (§10.3).
7. **Known pools.** Every entry's `pool` is one the builder's configuration names; a `pinned` entry is `serving`.

### 6.5 Multi-vintage products

Census 2021 and 2026 are two bundles with distinct DSDs and one product. The grouping is catalog metadata and lives in `products`, or in the publication registry from which `products` is generated. A bundle knows nothing of its siblings. `role` lets a front end show "current" without a date comparison.

### 6.6 Build

```
for each bundle_id, token in bundles/:
    fetch manifest; verify sha256 against the stored copy? (no stored copy yet: compute and record)
    parse; extract entry
assemble; enforce §6.4; mint token; write catalog/index/<token>.json; atomically update catalog/current
```

Input to the builder is the list of publications meant to serve, supplied by the publish pipeline (the publication registry holds which token of which bundle is current). The builder never scans the blob store to decide what is current; scanning finds orphans and reports them.

---

## 7. Routing and serving

### 7.1 Shard router

```python
@dataclass(frozen=True, slots=True)
class Shard:
    bundle_id: str
    token: str
    location: str
    manifest_sha256: str
    schema_version: str
    content_fingerprint: str | None

class ShardRouter:
    def __init__(self, index: CatalogIndex, *, schema_version: str, max_attached: int = 16): ...
    def shard_for(self, flow: SdmxUrn[Dataflow]) -> Shard:      # KeyError → 404 upstream
    def registry(self, shard: Shard) -> LocalRegistry:          # fetch if absent, verify, attach READ_ONLY, LRU evict + DETACH
    def reload(self, index: CatalogIndex) -> None:              # swap the map; evict shards the new index retires
```

Rules:

- The router builds its map from entries with `state = "serving"` and `schema_version == schema_version` of the running sdmxlib. Entries at another schema are invisible to this pod. A dataflow routed only to invisible entries answers 503 naming both schema versions, never 404.
- A pod polls `catalog/current`; on a new index token it fetches the index, verifies, and calls `reload`. Requests in flight finish on the old registry object.
- Attach is `LocalRegistry.bundle(path, require_manifest=True)` with a never-writing open policy (statcatlib's `Embedded()`).
- Fetch verifies the manifest's SHA-256 against the index entry before anything else is read, then runs the receive profile once per token (§4.6).

### 7.2 Affinity routing

Rendezvous (or bounded-load consistent) hashing on `bundle_id` at the ingress decides which pods attach which bundles. Each pod's attached set is its slice; the cluster's cache is the sum of the slices. A hot bundle spills to a second pod under bounded load. The ingress reads the same index the pods read, so ingress and pod agree on which `bundle_id` exists.

### 7.3 Cache keys

| cache | key |
|---|---|
| per-pod structural cache | `(bundle token, urn)` |
| result store (blob) | `sha256(effective token, canonical query, format, lang)` |
| CDN | the result store key as ETag; long TTL; no invalidation |
| equivalence shortcut | when the new publication's `content_fingerprint` equals the old one's, the server MAY map the new effective token to the old key prefix instead of recomputing |

Canonical query: the parsed, typed SDMX request with dimension values sorted and defaults filled. Two URLs meaning the same query hash identically.

### 7.4 Pools and replication

Two dimensions, kept apart:

- **Partitioning** decides which pods hold which bundle. It is a cache-placement optimisation. Any pod can serve any bundle after a fetch, so partitioning is never a correctness rule.
- **Replication** decides how many pods hold a bundle. It is capacity. Release day is a replication problem: one bundle takes most of the traffic and the other 799 idle.

Pools make replication independent per traffic class. Each index entry names a `pool`; each pool is a stateless Kubernetes Deployment with its own autoscaler; the ingress routes a request to the pool of the dataflow's bundle, then hashes within the pool. Two or three pools suffice: `hot` for the current release and a few high-traffic products, `base` for the long tail. One pool per bundle is 800 autoscalers and is rejected.

Within a pool, the ingress uses bounded-load consistent hashing on `bundle_id` (Envoy ring hash or Maglev with `hash_balance_factor`; exposed by Envoy Gateway and Istio). Adding a pod remaps about 1/N of keys, so scaling churns a bounded share of the cache. Bounded load spills a hot `bundle_id` to the next pod on the ring when one saturates. The ingress derives `bundle_id` from the parsed dataflow URN through the same index the pods poll.

`pinned` marks bundles a pod MUST attach before it reports ready. The release bundle is pinned in the `hot` pool for its release window; the long tail is attached lazily.

Pod lifecycle in a pool:

| probe or hook | condition |
|---|---|
| startup | the pod has fetched the current index; generous timeout to cover the pull of pinned bundles |
| readiness | every `pinned` bundle for this pool is verified and attached; the index token is current |
| liveness | the process answers; never tied to the index or attach state |
| preStop | stop accepting; finish in-flight requests; DETACH; no cache to persist |

Autoscaling the `hot` pool uses three signals through KEDA: a cron scaler that pre-scales before a known release time, a Prometheus scaler on origin requests per second per pod (cache misses, which is the work pods do), and CPU as the floor. CPU alone lags a traffic step by the metrics window plus the pod pull time.

Capacity levers above the pods, in order of effect:

1. **Result store and CDN with `Cache-Control: immutable`.** Final data never changes; repeat requests never reach a pod.
2. **Pre-materialisation at publish.** The publish job writes the predictable release-day set into the result store under canonical keys: full-table downloads per language and the slices the front end requests on load.
3. **Node-local block cache.** `cache_httpfs` on a `hostPath` so pods on one node share Parquet byte ranges; a pod scaled onto a warm node starts with the hot bundle's blocks present. Topology spread keeps the hot pool across nodes.
4. **Single-flight.** Each pod coalesces identical in-flight queries; the ingress MAY coalesce identical requests as well.

Rejected: StatefulSets for shard ownership (slow to scale, no purpose with immutable files); per-pod persistent volumes (an `emptyDir` plus re-pull is faster and does not pin a pod to a zone); any arrangement in which one pod is the only holder of a bundle.

Resource shape: DuckDB `memory_limit` at about 70 % of the container limit, requests equal to limits, so a scaled pod lands with predictable capacity. A getdata slice on a sorted, compacted table is tens of milliseconds, so a four-core pod answers on the order of a hundred origin requests per second; the pod count on release day follows from the miss rate.

Worked estimate: 5,000 requests per second at peak on one bundle, 95 % absorbed above the pods after the first hour (250 origin requests per second, 3 pods) and 60 % in the first hour (2,000, 20 pods). Cron pre-scaling to 20, pre-materialisation lowering the first-hour miss rate, and 30-second pod startup keep the autoscaler off the critical path.

---

## 8. Shared structures

Each bundle vendors its structural closure. `CL_GEO`, SGC and shared concept schemes are copied into every bundle that references them.

Decision: keep vendoring. Reasons:

- A pod attaches one database per request and the `references` check stays closed-world.
- The effective token covers everything served from a bundle; a layered design would have to fold dependency tokens in.
- Deduplication belongs in the registry (FMR, the publication registry), where the artefact lives once and bundles take copies at build.

Costs accepted: storage proportional to bundles × closure size (cheap); build time per bundle for serialising shared codelists (bounded; a build caches the serialised form per pinned URN across bundles).

Guarded by §4.4's pinned-URN and equivalence invariants and §6.4's agreement check. Layered bundles (`requires: [{bundle_id, token}]`) are recorded as the alternative and rejected until a concrete need appears (for example a DGUID codelist large enough that copying it per bundle is measurable in publish time).

---

## 9. Schema versions and fleet rollout

### 9.1 Three versions, three checks

| version | recorded | checked before open by |
|---|---|---|
| sdmxlib `schema_version` | manifest | sdmxlib reader (exists) |
| DuckDB storage version | manifest 1.2 | sdmxlib reader (new) |
| DuckLake spec version | manifest 1.2 | sdmxlib reader (new) |
| statcat `schema_version` | `statcat._meta` | statcatlib `Catalog.open()` (new; today written and unchecked) |

Exact match stays the rule for each. The fleet absorbs the cost through the index, not through tolerance in the reader.

### 9.2 Rollout procedure for a schema bump

1. Pin the new sdmxlib, DuckDB and DuckLake releases in one image tag for the build job and the pods.
2. The build job republishes every serving bundle at the new schema, each into a new token directory, each with `previous_token` and `content_fingerprint`. The job runs `check_bundle(at="publish", expect_fingerprint=<old>)` on each and refuses to proceed if any fingerprint moved.
3. The index builder publishes an index whose `schema_versions` holds both values and whose `bundles` holds both publications of each `bundle_id`, old `state = "serving"`, new `state = "serving"`. Invariant 1 of §6.4 is relaxed for exactly this case: one home per dataflow **per schema version**.
4. Pods roll. Old pods see only old entries, new pods only new entries. The ingress routes by `bundle_id`, which both have.
5. When no old pod remains, the index builder publishes an index with only the new entries and `schema_versions` of one value. Old token directories age out under a lifecycle policy.

Result stores keyed on effective token are warm across the bump wherever `content_fingerprint` held (§7.3).

### 9.3 Statcat index version

A statcatlib older than the index's `index_version` returns zero hits without error today. `Catalog.open()` MUST refuse an `index_version` it does not read. The section already records the version; the check is one comparison.

---

## 10. Publication, transport and retirement

### 10.1 Publish sequence (build job)

1. Build into `bundles/<bundle_id>/<new token>/` on local disk or a staging prefix.
2. Flush inlined data; expire snapshots; cleanup old files; compact if final.
3. `build_index()`; `discard_orphans()`.
4. `publish_bundle(...)` with `previous_token=<current>`, computing `content_fingerprint`.
5. `check_bundle(at="publish")`.
6. Upload every file except the manifest, in parallel; then upload the manifest.
7. Record `(bundle_id, token, manifest_sha256)` as the candidate in the publication registry.
8. Run the index builder; publish the new index; flip `catalog/current`.

Steps 6 and 8 give two atomicity points: a bundle directory without a manifest is unpublished; an index not named by `current` is unpublished.

### 10.2 Copy rule

A tool that copies a published bundle MUST copy the manifest last. `azcopy` and `cp -r` do not promise order, so the copy tool is `sdmxlib bundle copy SRC DST`, which reads the manifest, copies the listed files, verifies, then writes the manifest. The same command is how a pod receives a bundle and how another agency mirrors one.

### 10.3 Draining and retirement

An index entry has a `state`:

- `serving`: routable.
- `draining`: still readable; new requests route elsewhere; pods evict it at the next reload after in-flight work completes. Used for the old publication during a token flip.
- `retired`: listed for provenance; readers MUST NOT attach it. The directory may be deleted after the lifecycle delay.

A publication moves `serving → draining → retired` across successive indexes. A lifecycle policy deletes `retired` directories older than the backup retention window, which keeps the backup invariant: a catalog backup is restorable only while every file it references still exists.

### 10.4 Split layout as the pod default

A pod attaches a bundle in split layout: it fetches `bundle.manifest.json`, `meta.duckdb`, `lake.ducklake` and `search/` to local disk, verifies them, and attaches the lake with `OVERRIDE_DATA_PATH` pointing at the publication's `data_path` in blob storage. Parquet is read remotely through the block cache and never pulled whole.

This is the default because it sets the pod's time-to-ready. The pull set is hundreds of megabytes for the census bundle against gigabytes for the whole directory, so a pod scaled in on release day is ready in tens of seconds (§7.4). The full-directory pull is reserved for mirroring, backup and offline analysis through `sdmxlib bundle copy`.

The `files` check at receive time hashes the pulled files in full and verifies remote data files by size and the blob's stored content hash; the publish profile hashes everything. `data_path` (§4.1) is what makes the split a property of the manifest rather than of the deployment.

---

## 11. Search across shards

Each bundle carries per-language LanceDB tables over its own datasets, codes, concepts and reference metadata. A catalog of 800 bundles has 800 × languages tables.

Two serving modes, chosen per query type:

- **Product-scoped search** (within a dataflow or product): route to the bundle, query its tables. Works today.
- **Holdings-wide search** ("find datasets about rent"): either fan out to every attached bundle and merge by score, which breaks BM25 statistics across tables and touches every shard, or build one catalog-level index over the union of `statcat.dataset` rows from every bundle at index-build time, stored under `catalog/search/<index token>/`.

Recommendation: the catalog-level index for dataset rows (small: one row per dataset per language), and product-scoped search for codes and reference metadata (large, and queried after a product is chosen). The catalog-level index is a statcat extension on the catalog index, versioned like `search/index.json`, built in step 8 of §10.1.

Open: whether code search should also be catalog-wide for an LLM agent built on sdmxlib, where a user names a geography before a product.

---

## 12. Traps

Consolidated list; each has its mitigation in the section cited.

1. **In-place republish deletes Parquet under an attached reader.** Token directories; manifest last; drain state. §4.4, §10.
2. **Manifest has no integrity of its own.** `manifest_sha256` in the index; reserved `.sig` sidecar. §6.3, §4.2.
3. **DuckDB storage version and DuckLake spec version are unrecorded.** A newer writer bricks older readers after the schema check passed. Manifest 1.2 fields; pre-open check. §4.1, §9.1.
4. **Schema bump forces a lockstep rebuild of every bundle before any pod upgrades.** Index with two schema versions; per-schema routing; fingerprint-verified rebuilds. §9.2.
5. **A no-op rebuild cold-starts every cache.** `content_fingerprint`; equivalence shortcut. §4.3, §7.3.
6. **Overlay `scope` and merge hooks change served bytes without changing the effective token.** Digest the applied overlay; hook id in the token. §5.1, §5.2.
7. **Serve-time overlays desynchronise search.** Refuse search-affecting overlays; fold then rebuild. §5.3.
8. **Shared codelists drift between bundles built at different times.** Pinned-URN and equivalence invariants; builder agreement check. §4.4, §6.4.
9. **`references` costs 30 s and 5 GB per bundle.** SQL anti-join. §4.7.
10. **Every pod re-hashes gigabytes on restart.** Receive stamp; open profile. §4.6.
11. **Copy order is undefined.** `sdmxlib bundle copy`. §10.2.
12. **Inlined data and old snapshots ship in `lake.ducklake`.** Flush, expire, cleanup before manifest. §4.4.
13. **`open_bundle`'s default `Auto()` writes to `lake.ducklake` and fails the files check.** Default to a never-writing policy when a manifest is present. (statcatlib)
14. **Old statcatlib returns zero search hits on a newer index without error.** Refuse unknown `index_version`. §9.3.
15. **`statcat._meta.schema_version` is written and never checked.** Check on open. §9.1.
16. **Search `generation` is an integer counter and collides across builders.** Use the bundle token or a UUIDv7 in table names; `publish_bundle` drops orphans. (statcatlib)
17. **One dataflow in two serving bundles.** Index invariant 1. §6.4.
18. **Index built by scanning the blob store resurrects an orphan.** The pipeline supplies the serving list; scans only report. §6.6.
19. **`token` reads as an auth token in server code.** Consider renaming to `publication` in 2.0. Cosmetic.
20. **Large manifests.** 10,000 files at ~120 bytes each is 1.2 MB of JSON; fine. Compaction for final bundles keeps it small. §4.4.
21. **One process per bundle.** `LocalRegistry.bundle` opens `meta.duckdb` for writing, so a second process (a worker, or `check_bundle` beside a server) fails with a conflicting lock. Read-only open for published bundles; checkpoint before manifest; per-process `temp_directory`. §4.2.
22. **Legacy versions are never final.** sdmxlib follows SDMX 3.0, under which `2.0` carries no immutability guarantee, so every `isFinal`-driven decision was false for current StatCan data. Publication immutability comes from token directories; product lifecycle is a publisher assertion in the index; artefact immutability for legacy versions comes from required fingerprints. §4.4, §6.3, §6.4.

---

## 13. Prior art

| system | what it shares | what to borrow |
|---|---|---|
| BagIt (RFC 8493) | directory + payload manifest of checksums + metadata file | tag manifest (manifest integrity); complete vs valid gradations; `fetch.txt` for holey bags ≈ `structures_only` |
| Debian repositories | two-level `Release` over `Packages`, hashed and signed | the index-over-manifests shape; detached signature |
| Python wheels | `RECORD` (per-file sha256, size) + `WHEEL` (format version) | the vendoring model at scale |
| OCI distribution | manifest of blobs by digest; `annotations`; registry index of tags | content addressing (recovered here as `content_fingerprint`) |
| Iceberg / Delta | snapshot id for identity; file sizes for integrity | identity-vs-integrity split; both lack a content checksum per file, which this format adds |
| Frictionless Data Package | `datapackage.json` with resource hashes and schemas | the statistical-data framing |
| RO-Crate | provenance in a JSON-LD metadata file | where to go if `Provenance` grows beyond one fetch |
| Hive metastore federation (Waggle Dance) | many catalogs behind one routing layer | route in the application, pin one engine version |
| Azure SQL shard map | authoritative key→database map cached by clients | the shard map vocabulary and update handling |
| SQLite-per-tenant (Turso, LiteFS) | thousands of embedded database files routed by key | file format version as part of the routing key |
| Nix / Git | content-derived identity | the fingerprint construction |

---

## 14. Prototype plan

Each milestone names its deliverable and an acceptance test. Order follows risk removed per unit of work.

**M0. sdmxlib: read-only open.** `BundleRegistry` returned by `LocalRegistry.bundle` when a manifest is present; `access_mode=READ_ONLY` and `ATTACH (READ_ONLY)`; `CHECKPOINT` in `write_manifest`; `.wal` refused by the files check; per-process `temp_directory`. Test: two processes open one published bundle and both answer `obs()`; `check_bundle` runs against a bundle a server holds open; `add` is absent on the returned type (basedpyright and `hasattr`).

**M1. sdmxlib: publication directories and copy.** `write_manifest` writes into `<bundle_id>/<token>/`; `sdmxlib bundle copy` copies manifest-last and verifies. Test: a reader attached to token A keeps answering while token B is written and A's directory is left untouched.

**M2. sdmxlib: manifest 1.2 fields and pre-open checks.** `duckdb_version`, `duckdb_storage_version`, `ducklake_version`, `ducklake_spec_version`, `previous_token`, `data_path`, `artefacts`, `BindingCount.structural_version`; `StoreFormatError` before open. Test: a bundle written by a newer DuckDB is refused with both versions named and the file unopened (check with `lsof` or by holding an exclusive lock).

**M3. sdmxlib: effective token fixes.** Applied-overlay digest; hook id in the token string; `overlay_affects_search` refusal path exposed for servers. Test: two audiences with different applied entries get different tokens; same applied entries get the same token; changing the hook changes the token.

**M4. sdmxlib: `references` as SQL and check profiles.** `check_bundle(at=...)`; SQL anti-join with the loading check as oracle in tests; receive stamp. Test: identical findings on the synthetic bundle corpus; `references` under 1 s on the 2.6 GB census bundle.

**M5. sdmxlib: content fingerprint and stability.** `content_fingerprint`, `artefacts[].fingerprint` (required for `legacy`), `stability` from the version parser, `(urn, fingerprint)` cache keys for legacy pinned URNs; `check_bundle(expect_fingerprint=)`. Test: two builds from the same inputs at different times produce different tokens and equal fingerprints; one changed observation changes the table fingerprint; `bit_xor` stability across the pinned DuckDB release recorded in a test fixture.

**M6. statcatlib: catalog index 1.0.** `build_index_document(entries) -> CatalogIndex`, the invariants of §6.4, `catalog/current` atomic flip, `products`. Test: a dataflow in two serving entries is refused naming both; a fingerprint disagreement on a shared codelist is refused naming the URN and both bundles.

**M7. statcatlib: the four local fixes.** Check `statcat._meta.schema_version`; refuse unknown `index_version`; never-writing default when a manifest is present; token-named search tables with orphan drop in `publish_bundle`. Test: each failure mode in §12 items 13–16 raises or refuses by name.

**M8. sdmxlib-rest: `ShardRouter` and pools.** Index polling, per-schema visibility, LRU attach with DETACH, 503 on schema-invisible dataflows, verification before attach, split-layout pull, `pinned` gating of readiness, effective-token cache keys with the fingerprint shortcut. Test: synthetic catalog of 200 bundles; time-to-ready of a cold pod with one pinned bundle under 60 s; p99 attach latency for an unpinned bundle; a flip from index T1 to T2 with no failed requests under load.

**M8b. Kubernetes manifests.** One Deployment per pool, KEDA ScaledObject (cron, Prometheus origin-rps, CPU), startup/readiness/liveness probes per §7.4, preStop drain, PodDisruptionBudget, topology spread, `hostPath` block cache, Envoy Gateway route with bounded-load hashing on `bundle_id`. Test: scaling the hot pool from 4 to 20 pods under a synthetic release-day load remaps no more than 1/N of keys per added pod (measured by cache miss rate) and produces zero failed requests.

**M9. Rollout drill.** The §9.2 procedure end to end on the synthetic catalog in a kind cluster: two schema versions, mixed pods, zero 404s, result-store hit rate across the bump measured.

**M10. Catalog-level search.** Dataset-row index over the union; product-scoped code search; the agent's geography-first query shape measured against both.

Synthetic bundles (`sdmxlib.testing.synthetic_bundle`, `statcatlib.testing.synthetic_catalog`) and `profile_bundle` shapes of the real census bundle supply the corpus for every milestone.

---

## 15. Open questions

1. Name overlays (§5.4): extend `OverlayAnnotation` or document the rebuild.
2. Whether `artefacts` in the index carries URN lists or counts by default; depends on index size at 800 bundles with vendored closures (estimate: 800 × ~50 artefacts × ~120 bytes ≈ 5 MB as a list; counts keep it under 500 KB).
3. Whether the `token` rename to `publication` is worth a 2.0.
4. Authenticity: detached signature format (minisign vs sigstore) when cross-agency sharing becomes concrete.
5. Code search scope for the agent (§11).
6. Whether to relax exact-match `schema_version` to minor-compatible once the fleet procedure in §9.2 has run once and its cost is measured.
7. Republishing StatCan structures with semantic three-part versions (`2.0.0`). It changes every URN referencing them and is an FMR governance change, so it is decoupled from this work; once done, the legacy fingerprint requirement stops applying to those artefacts and SDMX's own guarantee takes over.
8. Whether `BundleRegistry` should keep a read-only `sql()` or drop the escape hatch entirely on the served type.
