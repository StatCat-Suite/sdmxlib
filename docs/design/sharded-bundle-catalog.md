# Sharded bundle catalog: design

Status: fourth revision. sdmxlib's part is built and released in 0.73.0: M0, M2, M4, M5 and M7, the name overlays and the format specification, with M3 shelved (§16). The audit of 2026-10-25 found sdmxlib pieces the later milestones depend on and no milestone listed: reading tier 3 from object storage (GH-180), store-neutral remote verification (GH-181), pulling by tier with any copy tool (GH-182), the fingerprint comparison §9.2 runs (GH-183), the derived tables of §7.1 (GH-184) and the block cache (GH-185). statcatlib's and sdmxlib-rest's parts, M6 and M8 to M13, are in progress. Audience: implementers working on sdmxlib, statcatlib and sdmxlib-rest. Written against sdmxlib 0.72.1 and DuckDB 2.0.0; spike results and the decisions they settled are in §16 (M1) and §17. The work is the [Sharded bundle catalog](https://github.com/StatCat-Suite/sdmxlib/milestone/9) milestone, and GH-136 is the cross-repo plan.

This document specifies how a dissemination catalog of many SDMX products is published as a set of bundles, indexed, routed to serving pods, verified, cached and migrated. It builds on two published formats and changes both:

- sdmxlib bundle format, manifest 1.1 and overlay 1.0: https://statcat-suite.github.io/sdmxlib/bundle/
- statcatlib bundle format, statcat section 2: https://statcat-suite.github.io/statcatlib/bundle-format/

MUST, MUST NOT, SHOULD and MAY carry their RFC 2119 meanings. A *producer* writes a bundle or an index; a *reader* opens one; a *server* is a reader that answers SDMX REST requests; the *publish job* is the workflow that produces publications.

---

## 1. Vision

One bundle is one publication of one publish unit: a set of dataflows released together, with their structures, observations, catalog records and search index, described by one manifest. A publish unit may be one table, one product, or a family of products that share a release and a lifecycle; §4.0 gives the criterion. A catalog is a set of bundles and one index that says which bundle holds which dataflow and which artefact. A serving pod attaches the bundles its routing slice needs and nothing else. A publication never changes in place; a new publication is a new directory and the index moves to it. A bundle moves between a publish job, a blob store, a pod, a backup and another agency's machine without any of them rewriting it.

The properties this buys:

1. **Publish unit equals shard unit equals backup unit equals cache unit.** One concept, one directory, one identity.
2. **Immutability by construction.** A publication has a token; its directory is named by that token; readers of the old token keep a consistent view while the index flips.
3. **Verification without a database.** The index is a function of manifests.
4. **Independent lifecycles.** Census 2021 is final and never rebuilt; CPI republishes monthly; CODR tables republish daily.
5. **Atomic releases.** A daily release of a few hundred tables becomes visible in one write.
6. **Portability.** A bundle is a directory with checksums. Another NSO can receive one, run `check_bundle`, and serve it.

### 1.1 Scope today and the target

Today: single large products, the census profiles, served through sdmxlib-rest as the .Stat getdata backend. One bundle, gigabytes of observations, hundreds of megabytes of structures (most of it bilingual labels on the geography and characteristic codelists), a per-language search index.

Target: a CODR-scale catalog in .Stat. On the order of 7,000 tables, most of them megabytes, a few gigabytes, with a daily 08:30 release that updates tens to a few hundred tables through delta files, under embargo.

Every rule in this document is written to hold at both scales. Where a rule exists only for the CODR target, the section says so.

## 2. Motivation

### 2.1 The workload

SDMX REST `getdata` hits one dataflow per request. Cross-dataflow joins do not occur on the serving path. Published vintages change on a schedule and never between publications. Traffic is skewed: on a release day one product takes most requests; across CODR a few hundred tables take most requests and thousands get a handful of hits a day.

This is functional partitioning, the easy form of sharding: no hot keys to rebalance across writers, no cross-shard transactions, no live resharding.

### 2.2 The single-catalog limits

The server today runs one DuckLake catalog file on the pod. At catalog scale that costs cold start proportional to the whole catalog on every pod; a republish of one product redistributing the whole file; one backup unit for thousands of change cadences; and one DuckDB storage version for everything.

### 2.3 What the bundle formats already give

The sdmxlib bundle is the shard: a stable `bundle_id`, a minted publication `token`, per-file SHA-256, a schema check before open, a manifest written last through rename, overlays with a reproducible effective token, conformance checks. The statcat bundle adds the DCAT catalog schema, per-language LanceDB search tables, and an annotation fingerprint that already demonstrates logical content hashing.

### 2.4 What is missing

A catalog index; immutable publication directories with data files shared across publications; read-only open; manifest integrity; the engine versions that wrote the files; an artefact inventory readable without DuckDB; fingerprints that are correct enough to key an immutable cache on; placement tiers; a render version in cache keys; two effective-token holes; a finality model that works for legacy-versioned structures; shared-structure invariants; a shared-engine serving model in sdmxlib; and rollout and release procedures.

---

## 3. Architecture

```
          publish job                            blob storage                           serving tier
┌────────────────────────────────┐   ┌───────────────────────────────────┐   ┌──────────────────────────────┐
│ FMR / statcatlib (structures)  │   │ bundles/<bundle_id>/data/         │   │ getdata pools (hot, base)    │
│ per-bundle lake catalogs,      │──▶│ bundles/<bundle_id>/<token>/      │──▶│   ShardRouter, shared engine │
│ copy-and-extend                │   │ catalog/index/<token>.json        │   │ search pool                  │
│ build → check → upload,        │   │ catalog/structures/<token>.json   │   │ structures: the structures   │
│ manifest last → build index    │   │ catalog/current   ◀── mutable     │   │   bundle, served like any    │
│ → flip current at release      │   │ results/<key>     ◀── pods write  │   │   other                      │
└────────────────────────────────┘   └───────────────────────────────────┘   └──────────────────────────────┘
```

Blob storage is the hand-off. The publish job and the GC job write `bundles/` and `catalog/`; pods read them and write `results/`; the public reads nothing directly (§10.1).

| layer | document | owner | identity |
|---|---|---|---|
| sdmxlib bundle | `bundle.manifest.json` | sdmxlib | `token` (UUIDv7) |
| statcat bundle | `extensions.statcat`, `search/index.json` | statcatlib | same token |
| overlay | `<name>.overlay.json` | server operator | overlay digest |
| catalog index | `catalog.index.json`, `catalog/structures/` | statcatlib | index `token` |
| routing | in-memory shard map | sdmxlib-rest | index token |
| caches | result store, CDN | sdmxlib-rest | fingerprints, render version, effective token |

---

## 4. Bundle format changes (manifest 2.0)

The layout change (`data_path`, `root`, `tier`) is a major version. A 1.x reader refuses a 2.0 manifest by the existing rule. The store schema was to bump in the same release with the labels split; the split was shelved (§4.1), and 0.73.0 keeps store schema 2.48. Readers and producers roll out together as one pinned image set (§9).

### 4.0 Bundle granularity

A bundle groups dataflows that are **published together** and **share a lifecycle**; grouping those that also **share structures** is preferred. The mechanism is indifferent: a bundle holds any number of bindings, the index maps each dataflow to its bundle, routing hashes on `bundle_id`, and cache keys are per binding. The grouping decides four costs:

| unit the bundle is | consequence of grouping |
|---|---|
| publication | every member republishes together; catalog, labels and search files are rebuilt for the whole bundle and re-pulled by every pod holding it |
| lifecycle | `final` and `live` are per bundle, so a final member cannot share a bundle with a live one |
| closure | members sharing a DSD or codelists vendor them once; grouping removes duplicated labels |
| attach and readiness | members are pinned, pulled and evicted together |

The practical ceiling is tier-1 size: the bundle's catalog and labels must pull inside the readiness budget (§7.1). Examples: a census profile split into dataflows by geography level, or a family of tables derived from one DSD and released together, is one bundle; two census vintages are two bundles because one is final and one is live; CODR tables default to one per table because release days differ table by table, with subject-level grouping where tables always release together (open question 9).

### 4.1 Layout

```
bundles/<bundle_id>/
  data/                           immutable data files shared by every publication of this bundle_id
    <schema>/<table>/ducklake-<uuid>.parquet     DuckLake's own layout
  <token>/                        one publication; never rewritten
    bundle.manifest.json          written last
    meta.duckdb                   sdmx.* and statcat.*: ids, versions, parents, hierarchies, DSDs,
                                  bindings, constraints, catalog records            (tier 1)
    lake.ducklake                 DuckLake catalog; data_path = ../data/             (tier 1)
    search/                       per-language LanceDB tables, index.json, embeddings.json (tier 2, search pool)
```

- `data/` is shared across publications. A data file is written once under a fresh UUID and referenced by every publication whose lake catalog lists it. A republish writes only new files. Producers MUST NOT delete, rewrite or run cleanup against `data/`; deletion is the GC job's alone (§11.4).
- A publication's `lake.ducklake` is made by **copying the previous publication's catalog and extending it** (new snapshot, new files registered). A producer MAY expire old snapshots to shrink the catalog and MUST NOT run `ducklake_cleanup_old_files` or `ducklake_delete_orphaned_files`; files those would delete are still referenced by earlier publications. The first publication of a `bundle_id` starts an empty catalog.
- Shelved (GH-139; §7.1 has the measurement): names stay in `meta.duckdb`, and this split was not built. As planned: `meta.duckdb` and `labels.duckdb` split the store schema by role. The store `schema_version` bumps with this split; an old reader refuses the bundle by the existing exact-match rule rather than serving unlabelled artefacts. The schema stays one schema; the cold tables (`InternationalString` text, annotations, descriptions) live in the second file, attached under an alias, and the query layer resolves each table to the catalog that holds it.
- `search/` table names carry the bundle token rather than an integer generation; `publish_bundle` drops orphaned tables.

### 4.2 New manifest fields

| field | presence | meaning |
|---|---|---|
| `previous_token` | required | The `token` this publication supersedes, or `null`. |
| `canonical_form` | required | Version of the canonical row and model text the fingerprints hash (§4.4). |
| `content_fingerprint` | optional | Bundle fingerprint (§4.4), or `null`. |
| `duckdb_version` | required | DuckDB release that wrote the DuckDB files. |
| `duckdb_storage_version` | required | Storage version of the DuckDB files. |
| `ducklake_version` | required | DuckLake extension version that wrote `lake.ducklake`. |
| `ducklake_spec_version` | required | DuckLake spec version recorded in the catalog. |
| `data_path` | required | Where the lake's data files live, relative to the publication directory: `"../data/"`. A reader resolves it against where the publication is, a local directory or, for tier 3, the remote root the deployment names (GH-180). An absolute location would tie a bundle to one storage account, so the manifest never records one. |
| `artefacts` | required | Every maintainable artefact in the store: `ArtefactEntry` list sorted by `urn`. |

`BundleFile`:

| field | presence | meaning |
|---|---|---|
| `root` | required | `"publication"`: `path` is relative to the publication directory. `"data"`: `path` is relative to `data_path`. |
| `tier` | required | `1` pulled before a pod is ready; `2` pulled lazily to local disk; `3` read remotely by byte range. |
| `md5` | required for `root: "data"` | MD5 of the file, matching the `Content-MD5` the publish job sets on the blob. Azure keeps it; S3 and GCS keep no MD5 for a file uploaded in parts, and GH-181 adds a digest they do keep. |
| `carried_from` | optional | The `token` whose manifest this entry's digests were carried forward from. |

`BindingCount`:

| field | presence | meaning |
|---|---|---|
| `structural_version` | required | The pinned DSD URN the binding was written against. |
| `fingerprint` | required | Table fingerprint (§4.4). |
| `structure_fingerprint` | required | Structural fingerprint of the dataflow (§4.4); the result-store key reads it (§7.4). |

`ArtefactEntry`:

| field | presence | meaning |
|---|---|---|
| `urn` | required | The pinned URN. |
| `stability` | required | `"semantic"` (three-part version without extension; SDMX 3.0 guarantees immutability) or `"legacy"`. |
| `partial` | required | `true` for a partial codelist or scheme vendored as a subset (§8). |
| `fingerprint` | required | Artefact fingerprint (§4.4). |

`extensions.<name>` sections SHOULD carry their own `format_version`.

### 4.3 Constructors and reader rules

- Two constructors, two types. `LocalRegistry.build(path)` wires the layout writable (`meta.duckdb`, the lake with its `DATA_PATH`) and returns `LocalRegistry` with the write surface; `fetch_bundle`, `synthetic_bundle` and `fold_overlays` build through it. `LocalRegistry.bundle(path)` requires `bundle.manifest.json`, opens read-only, and returns `BundleRegistry`, a type carrying `get`, `query`, `obs`, `dsd`, `binding_for` and `browse` and **no `sql()`**: in a shared engine a fully qualified name reaches any attached catalog, `search_path` scopes unqualified names only, so a request-reachable raw SQL surface would read other bundles. The `require_manifest=` argument goes.
- Read-only open: the DuckDB files with `access_mode=READ_ONLY`, the lake with `ATTACH ... (READ_ONLY, OVERRIDE_DATA_PATH ...)`. Any number of processes may hold a published bundle open. Each process MUST set `temp_directory` outside the publication directory (the default is `<file>.tmp` beside the database, inside the publication).
- A reader MUST compare `duckdb_storage_version` and `ducklake_spec_version` with its engine before opening any file. It refuses a storage version **newer** than its engine reads (`StoreFormatError`, naming both); older storage versions open, as DuckDB allows. `ducklake_spec_version` follows the same rule: DuckLake 1.1 reads the census bundle's 1.0 catalog read-only without migrating it (the file's sha256 is unchanged), so only a newer spec is refused. The sdmxlib `schema_version` stays exact-match.
- A reader MUST resolve data files through `data_path`.
- A reader MUST ignore `bundle.manifest.json.sig` in the `files` check (reserved for a detached signature).

### 4.4 Fingerprints

Three levels. Each is tagged by `canonical_form`, the version of the text it hashes; a change to that text (a DuckDB JSON rendering change, a model serialiser change) bumps `canonical_form`, and fingerprints at different `canonical_form` values are never compared. Golden tests on the canonical text catch the need for a bump.

**Row text.** `to_json(t)` for the table alias `t`, columns in schema order, which DuckDB renders with keys in column order. Typed values render through DuckDB's JSON conversion; `canonical_form` covers its format.

**Table fingerprint.** Order-independent, duplicate-safe, stable across engine releases:

```sql
SELECT
  sum((md5_number_lower(to_json(t)) % 4611686018427387904)::HUGEINT)::VARCHAR
    || ':' || count(*)::VARCHAR
FROM lake.<schema>.<table> AS t;
```

MD5 is a fixed standard. Each term is below 2^62, so a HUGEINT sum does not overflow below 2^66 rows. Swapping two values between rows changes two row digests; duplicate rows add their digest twice.

**Artefact fingerprint.** SHA-256 of the artefact's canonical model text, `sdmxlib.bundle.canonical_json` at `CANONICAL_FORM` 1 (GH-126; msgspec cannot encode the attrs model, and the overlay digest works only because overlays are msgspec structs). The text is compact key-sorted JSON: every attrs object carries `"$class"` (its Python class) and every init field, `null` included; `maintainer` is the agency id; a `Ref` is its URN alone, never followed; an enum is its member name; a datetime is UTC with microseconds and `Z`; lists keep their order and sets sort by canonical text; a non-finite float, a naive datetime and any undefined type are refused. Golden texts cover all 30 artefact classes, and a walk asserts every field of every object reaches the text. The model carries fields SDMX-JSON cannot (the 28 declared gaps), so the wire format is unsuitable here; `docs/bundle.md` §9 states the form for other implementations.

**Structural fingerprint of a dataflow.** The artefact fingerprints of the dataflow's closure, sorted by URN, joined with `\n`, SHA-256. The closure is: outgoing references from the dataflow and its DSD (concept schemes, codelists as vendored including partials, hierarchies), **plus the artefacts that reference the dataflow or DSD** (constraints, provision agreements, hierarchy associations), plus those artefacts' own outgoing references. Constraints and associations point at the dataflow rather than the reverse, so an outgoing-only walk would miss a constraint change that alters what is served.

**Bundle fingerprint** (`content_fingerprint`). SHA-256 of `artefacts:` then each artefact fingerprint in URN order, `bindings:` then `<dataflow urn>\t<table fingerprint>` in dataflow order, joined with `\n`.

### 4.5 Invariants, stated as requirements

- **Closure.** A bundle MUST resolve every reference inside itself.
- **Pinned URNs.** Every artefact MUST be stored under its pinned URN.
- **Equivalence of copies.** Two bundles holding the same full (non-partial) pinned URN MUST hold the same logical content. SDMX guarantees this for semantic versions; for legacy versions the fingerprint is the guarantee and the index builder enforces it (§6.4). A partial artefact is compared as a subset of the full one. The registry's pinned-URN cache carries the same qualification: legacy and partial artefacts are cached under `(bundle token, urn)`.
- **Checkpointed, flushed lake.** A producer MUST `CHECKPOINT` every DuckDB file before the manifest (DuckDB replays a WAL on read-only open, and a WAL's contents sit outside the checksummed file, so a shipped `.wal` makes the `files` check meaningless; the check refuses an unlisted `.wal` naming the missing checkpoint), MUST run `ducklake_flush_inlined_data`, and MAY expire snapshots. No cleanup against `data/` (§4.1).
- **Copy-on-write revisions.** A `live` publication that revises or deletes rows MUST, after `ducklake_flush_inlined_data` (which turns an inlined delete into a delete file), rewrite every table that holds delete files into new data files with `ducklake_rewrite_data_files(catalog, delete_threshold => 0)` (or the catalog option `rewrite_delete_threshold` set to 0), then compact. The default threshold skips a table whose deleted rows are a small fraction, which is exactly a daily delta; spike M1(d) measured that it leaves the delete file at the default and removes it at 0. No delete file ships; the check refuses one.
- **Compaction for final publications.** A producer SHOULD merge to one sorted file per table when the publish job declares `lifecycle: final` (§6.3). Artefact finality plays no part.
- **Publication directories.** Written once, never rewritten. Data files written once, never modified.
- **Carry-forward.** A republish MAY carry forward a data file's `size`, `sha256` and `md5` from the previous manifest, marking `carried_from`, and MUST verify size and the blob's `Content-MD5` against them.
- **Blob digests.** The publish job MUST set `Content-MD5` on every uploaded file. This is the Azure rule; GH-181 states it for every store the read path supports.

### 4.6 `check_bundle` profiles

```python
check_bundle(path, at: Literal["publish", "receive", "open"], ...)
```

| check | publish | receive | open |
|---|---|---|---|
| manifest parse, format, versions | yes | yes | yes |
| files, `root: publication`: size, sha256, nothing unlisted | yes | yes, once per token (stamp, §4.7) | size only |
| files, `root: data`: listed entries only | sha256 in full | size and blob `Content-MD5` against `md5` | no |
| lake: every file the current snapshot references exists at the recorded size | yes | yes | no |
| bindings counts | yes | yes | no |
| references (SQL, §4.8) | yes | yes | no |
| fingerprint recompute | yes | optional | no |
| extensions (statcat) | yes | yes | section only |

The orphan dry run is gone from the `lake` check; orphans are defined over the union of manifests and belong to GC (§11.4).

### 4.7 Receive-time stamp

After a successful receive-time check a reader MAY write `<pod-local>/verified/<bundle_id>/<token>` recording the manifest SHA-256. A later open at the same token with the same manifest SHA-256 runs the `open` profile; with no stamp, or a stamp for another manifest digest, the reader runs the `receive` profile first.

Built in GH-130 as `check_on_open(path, stamps=dir)`: the stamp is `<stamps>/<bundle_id>/<token>`, JSON holding the manifest sha256, the checks that ran and the time, written by a rename once `receive` finds nothing. At `receive`, `data/` is checked by local size, or with `lookup=` by size and `Content-MD5` through `verify_remote`; the lake and the bindings still read the data where the registry reads it. `fingerprints=True` opts `receive` into the recompute. Extensions take the profile as a fourth argument.

### 4.8 `references` as SQL

`storage/_references.py` builds the check (GH-129). The references are the rows of `artefact_ref` together with every reference column the declared edges in `query/_edges.py` name, plus five columns outside the edge table (`UNDECLARED_REFERENCES`). The edge columns are needed: a concept's core representation has no `artefact_ref` row. A pair that `artefact_ref` already holds counts once.

The anti-join runs against every URN in the store: `artefact.urn` and one URN per stored item, built from the owning scheme's URN, the item class and the item's id or path, for the 20 item kinds the store keeps (`STORED_ITEMS`). A target with no exact match is tried under the spellings the loading check accepts: a version-less URN matches any version held, and `CLASS_ALIAS_GROUPS` folds the renamed component classes.

The loading check is the differential oracle in `tests/unit/test_references_sql.py`, over every fixture and the canonical corpus, each with every artefact dropped and with one leaf item of each kind deleted. Measured on the census bundle: 0.15 s over 2.16 million references; 0.5 s more when a target needs the spelling pass.
---

## 5. Overlay changes (overlay 1.1 / effective token)

### 5.1 Scope selection enters the token

A server that applies only entries whose `scope` matches an audience MUST compute the overlay digest over the overlay **as applied**. A `null` scope applies to every audience.

### 5.2 Merge hook identities enter the token

Hooks are per annotation type. The effective token string becomes `v2\n<manifest token>\n<hooks>\n<overlay digest 1>\n...`, where `<hooks>` is `type=<package>:<name>@<version>` for each type with a non-default hook, sorted by type, joined with `,`, or `default` when none. The leading `v2` names the string's format (GH-131).

Built in GH-131: a hook is a `MergeHook(package, name, version, fn)`, and `apply_overlays`, `effective_token` and `fold_overlays` take `audience=`, applying the entries scoped to it and the unscoped ones. Overlay format 1.1 defines `scope`.

### 5.3 Search-affecting overlays

A server that serves search MUST refuse at load any overlay with entries acting on a type in `SEARCH_ANNOTATION_TYPES` (`overlay_affects_search`), logging the entries. The fix is `fold_overlays`, `build_index`, `publish_bundle`.

### 5.4 Names

Decided (GH-134): an overlay MAY change a `name` or `description`, and only `fold_overlays` applies such an entry. Built in GH-163 as overlay format 1.2's `texts`: `OverlayText` entries that set or remove one language of a name or description.

- **The format.** An overlay entry gains `name` and `description` operations on artefacts and items, set per language, beside the annotation operations. The common case is a mistyped French name on a code.
- **Folded, never served.** `fold_overlays` writes the change into a new publication. `previous=` copies `meta.duckdb` and reuses every data file, so a one-word fix is a new token over the same lake. `apply_overlays` refuses an overlay carrying a name or description operation and names `fold_overlays`; a server therefore never applies one at load.
- **Why.** A name is structure in SDMX. It feeds search and every label map, and §7.2 caches structure per `(bundle token, urn)`. Served at load, a name overlay would make served structure differ from the bundle's, be search-affecting (§5.3), and require every label map and structure cache to key on the effective token. Folded, the new publication's token covers the change, and every cache, the search index and the manifest's fingerprints stay consistent with what is served.

---

## 6. Catalog index (index format 1.0)

### 6.1 Purpose

One JSON document lists every published bundle the catalog serves; a side document maps every artefact URN to its bundle. Both are built from manifests and `statcat.dataset` rows, never from `sdmx.*`.

### 6.2 Layout in blob storage

```
bundles/<bundle_id>/data/
bundles/<bundle_id>/<token>/
catalog/index/<index token>.json         one index publication; never rewritten
catalog/structures/<index token>.json    URN → (bundle_id, token); (agency, id) → versions
catalog/current                          the current index token; ETag-conditional put
results/<key>                            result store; written by pods and the publish job
```

### 6.3 Documents

`catalog.index.json`:

| field | presence | meaning |
|---|---|---|
| `format_version` | required | `"1.0"`. |
| `token`, `previous_token`, `created_at`, `producer` | required | As for the manifest. |
| `schema_versions` | required | Sorted set of sdmxlib `schema_version` values across entries. |
| `bundles` | required | `IndexEntry` list sorted by `bundle_id`, then `token`. |
| `products` | optional | `ProductGroup` list (§6.5). |
| `extensions` | optional | Sections keyed by package; the catalog-level search index (§12) under `statcat`. |

`IndexEntry`:

| field | presence | meaning |
|---|---|---|
| `bundle_id`, `token`, `previous_token`, `created_at`, `schema_version`, `duckdb_storage_version`, `ducklake_spec_version`, `content_fingerprint`, `canonical_form` | required | From the manifest. |
| `location`, `data_path` | required | Resolved URIs. |
| `manifest_sha256`, `manifest_size` | required | Of `bundle.manifest.json`. |
| `bytes`, `pull_bytes` | required | Sum of `files[].size`; sum for tier 1 and 2. |
| `dataflows` | required | `{urn, observations, structural_version, fingerprint, stability}` per binding. |
| `artefacts` | required | Count. The URN list is in `catalog/structures/`. |
| `kind` | required | `"product"` or `"structures"` (§8). |
| `datasets`, `search` | optional | From the statcat section. |
| `lifecycle` | required | `"final"` or `"live"`; set by the publish job from statcatlib. |
| `state` | required | `"staged"`, `"serving"`, `"draining"`, `"retired"` (§11.3). |
| `pool` | required | Serving pool (§7.4). |
| `pinned` | required | Attach before ready. |

`catalog/structures/<token>.json`: `{"urns": {"<urn>": {"bundle_id", "token"}}, "versions": {"<agency>:<id>": ["2.0", "2.1", ...]}}`. Only full artefacts are listed; a product request resolves its own closure inside its bundle, and the builder reads partial URNs from manifests for invariant 4 without publishing them. `latest`, `~` and unversioned requests resolve through `versions`, then `urns`.

`ProductGroup`: `{product_id, members: [{bundle_id, role}]}`, role in `current`, `vintage`, `superseded`.

### 6.4 Invariants the builder enforces

1. One home per dataflow per `schema_version` among `serving` entries. During a rollout (§9.2) a dataflow has two serving homes at two schema versions; both share a `bundle_id`, which is what the ingress hashes on, so routing is unaffected.
2. `manifest_sha256` matches the bytes fetched.
3. Every `schema_version` present appears in `schema_versions`; every entry's `canonical_form` is one the builder knows.
4. Full artefacts agree: equal fingerprints wherever one URN appears in more than one entry. Partials are subsets of the full artefact (ids and parents) in the structures bundle.
5. Final means final: a republish of a `final` `bundle_id` carries an equal `content_fingerprint`, or the entry is first set `live`.
6. Chain continuity on `previous_token`.
7. State pairs: `draining` has a `serving` sibling; `staged` has no `serving` entry with the same token.
8. Known pools; `pinned` entries are `serving` or `staged`.
9. Every full artefact has exactly one `kind: structures` home among `serving` entries (§8 allows several structures bundles per agency).
10. Publish order: a product publication whose partials gained codes is refused `serving` until the structures publication holding those codes is `serving`.

### 6.5 Multi-vintage products

Census 2021 and 2026 are two bundles with distinct DSDs and one product; `products` holds the grouping, generated from statcatlib.

### 6.6 Build

Input is statcatlib's serving list `(bundle_id, token, manifest_sha256, lifecycle, pool, pinned, state, kind)`. The builder fetches manifests in parallel, skipping those whose `manifest_sha256` it parsed for the previous index, enforces §6.4, writes the index and structures documents under a new token, and leaves `current` to the release step (§11.2). A scan of blob storage only reports orphans.

---

## 7. Routing and serving

### 7.0 Units

| unit | what it is | cardinality |
|---|---|---|
| bundle | the unit of publication, verification, caching and lifecycle | thousands |
| pod | the unit of compute: one DuckDB engine holding many bundles read-only, attached on demand, evicted when cold | a handful per pool |
| pool | the unit of scaling: identical pods responsible for a set of bundles | hot, base, search |
| routing | bundle → one pod in its pool by consistent hashing | derived from the index |

A pod holds many bundles; a bundle is held by as many pods as its pool has replicas. A pod never writes a bundle.

### 7.1 Placement tiers

| tier | rule | files |
|---|---|---|
| 1 | pulled before ready; readiness gates on it | manifest, `meta.duckdb`, `lake.ducklake` |
| 2 | pulled lazily to node-local disk, in the background after ready or on first use | `search/` in the search pool |
| 3 | read from object storage by byte range through a block cache | everything under `data_path` |

The manifest's `tier` is the producer's default; a pool's configuration MAY promote a tier. A lake has one data path, so tier 3 is promoted whole: every data file local, or every one remote. A publication file outside `data_path`, such as `search/`, MAY be promoted alone.

Tier 3 needs sdmxlib to open a publication whose data files are remote: `LocalRegistry.bundle(path, remote=...)` resolves `data_path` against the remote root, over any store DuckDB reads (`s3://`, `gs://`, `az://`, `abfss://`), with the credentials as DuckDB secrets on the `BundleEngine` (GH-180). The block cache is measured before it is configured (GH-185).

**Names stay in `meta.duckdb`.** The plan to split names, descriptions and annotations into a `labels.duckdb` was shelved after measuring the census bundle (GH-139):

| census `meta.duckdb` | size |
|---|---|
| as published | 1,099 MB |
| full copy, with every key and index | 1,022 MB |
| data only, no keys or indexes, without labels | 294 MB |
| names, descriptions and annotations alone | 31 MB |

Labels are about 3% of the tier-1 pull, so a split would save 31 MB at the cost of a store schema bump and a catalog-aware rewrite of every name read. Index structure is about 700 MB of the file. GH-165 measured each index against the read benchmarks: `write_manifest` drops `hierarchy_node`'s and `artefact_ref`'s keys and `artefact_ref`'s indexes, which cost no read, and keeps `code`'s key and `annotation`'s indexes, each of which halves the speed of a small hierarchy's eager read. The census `meta.duckdb` goes from 1,022 MB to 501 MB. Every format, labelled or not, reads names from `meta.duckdb`.

Derived tables a rebuild reproduces from the bundle's own content (label lookups, availability, pre-pivoted slices) were to be materialised at publish into `data_path` as tier 3. Which of them are worth baking, where each lives and who builds it is open in GH-184. Runtime caches (external file cache, `cache_httpfs`, parsed DSD objects) are node-local and unlisted.

### 7.2 Shared engine

A pod runs one DuckDB engine and attaches every bundle it serves into it, so one buffer pool and one thread pool are shared and `memory_limit` means one thing. sdmxlib today assumes one bundle per connection (`search_path = 'sdmx'`, `sdmx.*` qualification, a fixed `lake` alias). The change that supports many bundles per engine without qualifying every table:

- **Per-cursor `search_path`.** Each bundle attaches its DuckDB files under per-bundle aliases (`<bundle_id>__<token short>__meta`, `__lake`; a `__labels` alias went with the shelved split, §7.1). A request's cursor sets `search_path` to the bundle's `meta` catalog, so the existing `sdmx.<table>` spellings resolve as `<schema>.<table>` inside those catalogs. sdmxlib already runs each request on a per-thread cursor; the router sets the path before handing it over.
- **Role-to-alias map.** `DataflowBinding.database_role` names a role, which it keeps; the registry maps the role to the physical alias at attach time, so the stored binding stays `"lake"` and the SQL uses the per-bundle alias.
- **Caches keyed by bundle token** (resolve cache, label maps, parsed DSDs).

The first step is a spike verifying DuckDB 2.0's resolution of two-part names under a catalog-qualified `search_path`; the refactor is milestone M7 and precedes the router.

Built in GH-133 as `sl.BundleEngine` and `LocalRegistry.bundle(path, engine=)`. Each handle runs `USE "<prefix>__meta".sdmx`, which sets its current database as well as its search path, so the checks that filter on `current_database()` read the bundle too; a cursor derived from a handle does not inherit it, so the registry points every handle it makes. A bundle opened without an engine gets one of its own, so there is one opening path. Each registry is over one publication, so its caches are already per token. GH-160 measured sharing them across bundles: a registry's resolve cache retains about 2.2 MB, so 16 bundles holding the same structures duplicate about 35 MB, and the shared cache is not built. GH-160 stays open to re-measure once a deployment holds many distinct bundles.

### 7.3 Shard router

```python
@dataclass(frozen=True, slots=True)
class Shard:
    bundle_id: str
    token: str
    location: str
    data_path: str
    manifest_sha256: str
    schema_version: str
    pinned: bool

class ShardRouter:
    def __init__(self, index: CatalogIndex, structures: StructuresIndex, *, pool: str,
                 schema_version: str, max_attached: int = 200): ...
    def shard_for(self, flow: SdmxUrn[Dataflow]) -> Shard:        # KeyError → 404 upstream
    def shard_for_artefact(self, urn: SdmxUrn[object]) -> Shard:  # structures document; resolves latest/~
    def registry(self, shard: Shard) -> BundleRegistry:           # pull tier 1, verify, attach, set search_path; LRU evict + DETACH
    def reload(self, index: CatalogIndex, structures: StructuresIndex) -> None
```

Rules: the map holds `serving` entries for this pool at this schema; a dataflow routed only to another schema answers 503 naming both; a pod polls `catalog/current`, verifies a new index and reloads, with in-flight requests finishing on the old registry object; the manifest's SHA-256 is verified against the index entry before anything else is read; the LRU budget counts attached catalogs and evicted files stay on node-local disk.

**Prefetch pulls files and never attaches.** A `staged` pinned entry is pulled and verified to node-local disk and is attached only once the index lists it `serving`. An attached catalog is reachable by its alias from any cursor in the engine, so an embargoed bundle MUST NOT be attached, which is also why `BundleRegistry` has no `sql()` (§4.3).

### 7.4 Cache keys

| cache | key |
|---|---|
| per-pod structural cache | `(bundle token, urn)` |
| result store | `sha256(canonical_form, structural fingerprint of the dataflow, table fingerprint of the binding, applied-overlay digests, hook ids, render version, canonical query, format, lang)` |
| CDN | the result-store key as ETag; `Cache-Control: immutable`; no invalidation |

**Render version.** The bytes depend on the code that writes them. sdmxlib carries a `RENDER_VERSION` per data format, bumped by the rule that any change to a writer's golden files bumps it; sdmxlib-rest carries its own for the envelope it adds. Both enter the key, so a writer fix produces new keys and old entries age out. Because the key names the serving code, pre-materialised results are produced by the serving code (§11.2 step 3), never by the publish job. Built in GH-132 as `sdmxlib.formats.render.RENDER_VERSION`, one version per written format, structures and metadata included, each the first 16 hex of its golden file's sha256 (`tests/golden/render/`), so the bump rule is enforced by a test rather than remembered.

**Delivery.** The CDN's origin is sdmxlib-rest. On a hit in `results/`, the pod streams the body from blob storage to the CDN under the public URL with `Cache-Control: immutable`; the client never sees a storage URL, there is no cross-origin redirect, no CORS on the storage account, and no SAS lifetime to cache. The cost is one pass of the bytes through a pod per CDN miss, which the CDN's long TTL bounds. The earlier redirect-to-SAS pattern is retired.

Keys are per binding, so a delta to one dataflow invalidates that dataflow's results only. The bundle token identifies the publication and is never a cache key.

### 7.5 Pools and replication

Partitioning places caches; replication is capacity. Pools: `hot` (the release of the day and perennial heavy products; pinned; pre-scaled), `base` (the long tail, lazy), `search` (search requests only). Each is a stateless Deployment with its own autoscaler; the ingress routes by pool, then by bounded-load consistent hashing on `bundle_id` (Envoy ring hash or Maglev with `hash_balance_factor`), deriving `bundle_id` from the parsed dataflow through the same index the pods poll.

| probe or hook | condition |
|---|---|
| startup | current index fetched; timeout covers pinned tier-1 pulls |
| readiness | every pinned bundle verified and attached; index token current; search pool: declared language tables reachable |
| liveness | the process answers |
| preStop | stop accepting; finish in-flight; DETACH |

A pod is ready for its pool's whole job or out of rotation. Autoscaling the `hot` pool uses KEDA with a cron scaler ahead of known releases, a Prometheus scaler on origin requests per second per pod, and CPU as the floor. Capacity above the pods, in order: result store and CDN; pre-materialisation at publish; node-local block cache on a `hostPath` with topology spread; single-flight per pod and request coalescing at the ingress. Rejected: StatefulSets, per-pod persistent volumes, any pod that is the only holder of a bundle. Resource shape: `memory_limit` about 70 % of the container, requests equal to limits; a four-core pod answers on the order of a hundred origin requests per second.

### 7.6 Serving topology rule

The catalog is a network of addressable, immutable objects routed by key. Serving is a tree. SDMX REST requests are single-bundle by construction: getdata names one dataflow; a structure request resolves through `catalog/structures/` to one bundle (the structures bundle for a full artefact, the product bundle for its vendored closure). sdmxlib-rest has no coordinator and no leaf-to-leaf calls. Cross-bundle composition (the agent service comparing vintages, a holdings-wide listing) belongs to the service that needs it, which calls sdmxlib-rest once per bundle and merges. The actor model (one instance per key) is kept as the description of the catalog and of the publish side, where one writer per `bundle_id` is what the workflow engine's per-workflow serialisation provides, and is rejected for serving because it caps per-key throughput where release-day traffic lands.

---

## 8. Shared structures

Each product bundle vendors its structural closure, and one **structures bundle** per agency vendors every full codelist, concept scheme, category scheme and hierarchy. Decisions:

- **Partial codelists in product bundles.** A product bundle MUST vendor a codelist or scheme as the partial artefact (`isPartial="true"`, `partial: true`) holding the codes its content constraints use **plus their ancestors**, closed under parent. Any hierarchy the bundle holds MAY place only codes the partial holds. For a CODR table this is the member list CODR stores per table.
- **Structures bundles** (`kind: structures`) hold the full artefacts, have no bindings, live in the `base` pool unpinned (so they never gate readiness and attach lazily on the first full-artefact request), and answer `/structure` requests through `catalog/structures/`. They are the source the catalog-level search index (§12) is built from and the reference the builder checks partials against (§6.4 item 4). An agency MAY split them by change rate so one edit does not re-ship everything: for example `structures-geo` (the full geography, large and slow-moving), `structures-classifications` (NAICS, NOC, SGC), `structures-concepts` (concept schemes, small and frequently edited). Invariant 9 requires one home per full artefact, however the split is drawn.
- **Publish order.** In a release, structures publications are `serving` before any product publication whose partials depend on them (§6.4 item 10; §11.2).
- **First structure request.** Unpinned structures bundles attach on the first full-artefact request, so the first `/structure` request for a large one (the full geography) waits for its tier-1 pull, labels included. Pools that serve structure endpoints MAY pin the large structures bundles to move that pull before readiness.
- Product bundles and the structures bundle are published, verified and served by the same mechanism; the index marks the kind.

Layered bundles (`requires`) remain the recorded alternative and stay rejected.

---

## 9. Schema versions and fleet rollout

### 9.1 Versions and checks

| version | recorded | rule | checked by |
|---|---|---|---|
| sdmxlib `schema_version` | manifest | exact | sdmxlib reader (exists) |
| DuckDB storage version | manifest | refuse newer | sdmxlib reader (new) |
| DuckLake spec version | manifest | refuse newer | sdmxlib reader (new) |
| `canonical_form` | manifest | known values | index builder, cache keys |
| statcat `schema_version` | `statcat._meta` | exact | statcatlib `Catalog.open()` (statcatlib#39, in progress) |
| statcat `index_version` | manifest `extensions.statcat` | known values | statcatlib `Catalog.open()` (new) |

### 9.2 Rollout procedure for a schema bump

1. Pin sdmxlib, statcatlib, DuckDB and DuckLake in one image tag for the publish job and the pods.
2. Republish every serving bundle at the new schema into new token directories sharing `data/`, with `previous_token` and fingerprints; `check_bundle(at="publish", expect=<previous manifest>)` stops the job if any fingerprint moved, naming the binding or artefact (GH-183, not yet built).
3. Publish an index holding both publications of each `bundle_id` as `serving`, with both schema versions.
4. Roll pods; old pods see old entries, new pods new ones; the ingress routes by `bundle_id`.
5. Publish an index with only the new entries; old ones follow §11.3.

Result-store keys survive the bump wherever `canonical_form`, fingerprints and render versions are unchanged. A `canonical_form` bump is a one-time cold cache by design.

---

## 10. Blob storage

### 10.1 Hand-off and access

Blob storage is the only channel between the publish job and the pods. Principals:

| principal | `bundles/`, `catalog/` | `results/` |
|---|---|---|
| publish job | write | write (pre-materialised results) |
| GC job | delete | delete |
| pods, search pods | read | read, write |
| the public, the CDN | none | none directly |

The public reaches a result through sdmxlib-rest, which streams `results/<key>` to the CDN (§7.4, Delivery). A pod reads a key only after computing it from a `serving` entry, so an embargoed key, though present in `results/`, is unreachable until the index flips. Nothing serves `bundles/` or `results/` to the public directly.

### 10.2 Namespace

DuckLake's reads are byte-range GETs of files whose names it holds; it never lists a directory, so flat and hierarchical namespaces serve the hot path identically. Hierarchical namespace (ADLS Gen2 with HNS, the current provisioning) helps the publish side: atomic directory rename (`.staging-<token>/` → `<token>/`), directory delete for GC, POSIX ACLs for the table above. It forgoes blob versioning, point-in-time restore and object replication. Decision: keep HNS; the design replaces most of what versioning gave (immutable publications, the index chain, recovery by pointing `current` back), and account-level replication (RA-GZRS or a scheduled copy) with retention longer than index retention covers a deleted token directory.

### 10.3 Backup invariant

An index is restorable only while every data file its publications reference exists. GC deletes nothing younger than the backup retention window; the backup copies `bundles/` and `catalog/` whole.

---

## 11. Publication, release and retirement

### 11.1 Publish sequence (per bundle)

1. Copy the previous publication's `lake.ducklake` into staging; write new data files into `data/` under fresh UUIDs; for a `live` republish rewrite revised files copy-on-write; ingest structures (partials for products, full for the structures bundle) and catalog records into `meta.duckdb`.
2. Checkpoint every DuckDB file; flush inlined data; optionally expire snapshots; compact if `final`. No cleanup against `data/`.
3. `build_index()`; drop orphaned search tables.
4. `publish_bundle(...)` with `previous_token`, fingerprints, `canonical_form`, carry-forward with `carried_from`.
5. `check_bundle(at="publish")`.
6. Upload: data files with `Content-MD5` set; then the publication directory under `.staging-<token>/` with `Content-MD5` set; rename to `<token>/` (HNS) or upload the manifest last.
7. Record `(bundle_id, token, manifest_sha256, lifecycle, pool, pinned, kind, state="staged")` in statcatlib.

### 11.2 Release sequence (per release)

1. Every publication in the release is uploaded and `staged` (§11.1), structures publications included.
2. The builder writes an index and structures document with the new publications `staged` and the current ones `serving`, and flips `current` to it. Pods pull pinned staged bundles to disk without attaching. Routing does not see staged entries; the embargo holds (§10.1).
3. A pre-materialisation job, running the **pods' image** so render versions match, opens each staged publication from its own files, computes the release set (full-table downloads per language, the slices the front end requests on load) and writes it to `results/` under the new keys. It runs in a job, never in a serving pod, so nothing embargoed is attached in a pod.
4. At release time the builder writes an index with the new publications `serving` and the superseded `draining`, structures entries ordered first by invariant 10, and flips `current` with one ETag-conditional put.
5. The next index moves `draining` to `retired` after a fixed delay.

### 11.3 States

| state | routable | pods may prefetch | directory may be deleted |
|---|---|---|---|
| `staged` | no | pinned only, to disk, never attached | no |
| `serving` | yes | yes | no |
| `draining` | no; new requests route to the serving sibling | no | no |
| `retired` | no | no | after the backup retention window |

### 11.4 Garbage collection

The GC job reads the current index chain back through the retention window and deletes: `retired` publication directories older than the window; files under `bundles/<bundle_id>/data/` listed in no manifest of any publication in `staged`, `serving`, `draining`, or `retired` within the window; `results/` keys reachable from no serving entry's fingerprints, after a grace period. GC reads manifests only, never a bundle's DuckDB files. It is the only principal that deletes under `bundles/`.

### 11.5 Copy rule

`sdmxlib bundle copy SRC DST [--tiers 1,2,3]` reads the manifest, copies the listed files of the requested tiers (data files by `root: data` entries), verifies, then writes the manifest last. It copies between filesystems: a mirror, a mount, another agency's disk.

sdmxlib holds no blob client, so a pod pulls from object storage with the deployment's own tool. `sdmxlib bundle files PUB --tiers 1,2` lists the files those tiers need, the manifest last, for `rclone copy --files-from` or azcopy (GH-182); `check_on_open` verifies the result.

---

## 12. Search across shards

Each product bundle carries per-language LanceDB tables over its own datasets, codes, concepts and reference metadata, served by the search pool from tier-2 files. Holdings-wide search is a catalog-level index built at index time from the union of `statcat.dataset` rows and the full codelists in the structures bundle, stored under `catalog/search/<index token>/`, recorded in the index's `extensions.statcat`, and served by the search pool. Fan-out across bundle tables is rejected (BM25 statistics do not compose across tables). Open: whether geography-first queries from the agent need the catalog-level code index to cover partials.

---

## 13. Traps

1. In-place republish deletes Parquet under a reader → token directories; shared immutable `data/`; manifest last; drain. §4.1, §11.
2. Snapshot cleanup on a shared `data/` deletes files earlier publications reference → producers never run cleanup; GC owns deletion over the union of manifests. §4.1, §11.4.
3. Republish cost scales with bundle size → shared `data/`; copy-and-extend; carry-forward. §4.1, §4.5.
4. A false-equal fingerprint under an immutable cache serves wrong observations forever → row-level MD5 folded by modular sum with count; model-based artefact fingerprint; `canonical_form`. §4.4.
5. A writer fix never reaches a cached query → render version in the key. §7.4.
6. Engine-hash fingerprints change on a DuckDB upgrade → MD5. §4.4.
7. Manifest has no integrity of its own → `manifest_sha256` in the index; reserved `.sig`. §6.3, §4.3.
8. Engine versions unrecorded → required fields; refuse newer storage and newer DuckLake spec. §4.2, §9.1.
9. Schema bump forces a lockstep fleet rebuild → two-schema index; per-schema routing; fingerprint-verified rebuilds. §9.2.
10. One delta invalidates every dataflow in a bundle → binding-level keys. §7.4.
11. Overlay `scope` and per-type merge hooks change bytes without changing the token → applied digest; per-type hook ids; `null` scope defined. §5.
12. Serve-time overlays desynchronise search → refuse; fold then rebuild. §5.3.
13. Shared codelists drift between bundles → pinned URNs; fingerprint agreement; structures bundle as reference. §4.5, §6.4, §8.
14. Partial codelists missing ancestors fail sdmxlib's parent check → closed under parents. §8.
15. Full codelists vendored per table at CODR scale → partials; structures bundle. §8.
16. `references` costs 30 s and 5 GB → anti-joins generated from the edge table. §4.8.
17. Every pod re-hashes gigabytes → receive stamp; tier-3 by size and `Content-MD5`. §4.6, §4.7.
18. Blob storage does not compute SHA-256 → publish job sets `Content-MD5`; `md5` in the manifest. §4.2, §4.5.
19. A multi-gigabyte pull set makes a scaled pod unavailable for minutes → tiers; search pool; the published store trimmed of unused keys (the labels split is shelved). §4.1, §7.1.
20. Labelled formats need labels on the first request → names stay in `meta.duckdb`, a tier-1 file. §7.1.
21. A 1.x reader opens a new-layout bundle and finds no data → manifest 2.0, which a 1.x reader refuses; pinned image sets. §4, §9.
22. Copy order undefined → `bundle copy`; HNS rename. §11.5, §10.2.
23. Inlined data, old snapshots, `.wal` files ship → checkpoint, flush, expire. §4.5.
24. Delete files on the serving path → copy-on-write at publish. §4.5.
25. One bundle per engine in sdmxlib → per-cursor `search_path`; role-to-alias map; token-keyed caches. §7.2.
26. `open_bundle`'s default policy writes to the lake → read-only constructor. §4.3.
27. Old statcatlib returns zero hits silently; `statcat._meta` unchecked → version checks on open. §9.1.
28. Search `generation` collides across builders → token-named tables. §4.1.
29. One dataflow in two serving bundles → invariant 1. §6.4.
30. Index built by scanning blob storage resurrects an orphan → serving list from statcatlib. §6.6.
31. One process per bundle → read-only open; checkpoint; `temp_directory` outside the publication. §4.3, §4.5.
32. Legacy versions are never final → token immutability; `lifecycle`; required fingerprints. §4.5, §6.3.
33. Embargoed results readable before release → no public access to `results/`; a pod reads a key only after computing it from a serving entry, and streams it to the CDN. §7.4, §10.1.
34. A Postgres DuckLake catalog on the serving path → file catalogs per bundle; Postgres optional upstream. §14.
35. The actor model for serving → replicated leaves in pools; no coordinator. §7.6.
36. Partial readiness within a pod → pools per workload. §7.5.
37. Deleted token directory with HNS → account-level replication with retention longer than index retention. §10.
38. `token` reads as an auth token in server code → consider `publication` in a later major. Cosmetic.
39. Raw `sql()` in a shared engine reads any attached bundle by alias, embargoed ones included → no `sql()` on `BundleRegistry`; staged bundles pulled, never attached. §4.3, §7.3.
40. An outgoing-only closure misses constraints and hierarchy associations, which reference the dataflow → closure includes incoming references. §4.4.
41. A small delta stays inlined, and the flush produces a delete file → explicit rewrite after flush. §4.5.
42. The publish job pre-materialising results must know the pods' render version → pre-materialisation runs the pods' image as a release step. §7.4, §11.2.
43. A cached 302 to a SAS URL expires under the client; cross-origin redirects need CORS → sdmxlib-rest streams results to the CDN. §7.4.
44. One structures bundle per agency re-ships the full geography on every concept edit → split by change rate; unpinned; publish order invariant. §8, §6.4.
45. msgspec cannot encode the attrs model → canonical model encoder in M4. §4.4.

---

## 14. Catalog authoring and the Postgres option

The publish job authors each bundle's lake catalog directly as a DuckDB file by copy-and-extend (§11.1). The design depends on nothing else.

A central Postgres DuckLake catalog is an option for governance and analytics across products (concurrent writers, point-in-time recovery, one place that knows everything). Adopting it requires exporting one bundle's slice into a file catalog, which means copying spec tables whose snapshot, schema and file ids are global across products. Milestone M1 is the spike that decides it; if the export does not yield a valid per-bundle catalog, Postgres stays a reporting copy and authoring stays as above. Postgres on the serving path is rejected for the reasons in v2: a shared network hop in every query's planning, a shared dependency for every pod, and the loss of the verifiable per-bundle catalog.

---

## 15. Prior art

| system | what it shares | what to borrow |
|---|---|---|
| Search serving (Google, LinkedIn Galene, Pinterest Manas, Airbnb Nebula) | immutable index segments shipped to replica sets | pools; coordinator-to-leaf tree |
| Elasticsearch time-based indices with ILM | one index per release; read-only; force-merged | the `final` lifecycle with compaction |
| PMTiles, Cloud-Optimised GeoTIFF | immutable file per release by HTTP range | tier 3 |
| Netlify-style atomic deploys | immutable build directories; pointer flip | token directories; `current` |
| Iceberg / Delta / DuckLake | snapshot identity; shared immutable data files | shared `data/`; the per-file checksum this format adds |
| BagIt (RFC 8493) | payload manifest of checksums; tag manifest | manifest integrity |
| Debian repositories | `Release` over `Packages`, hashed and signed | index over manifests |
| Python wheels | `RECORD` + `WHEEL` | vendoring at scale |
| OCI distribution | manifest of blobs by digest; tag index | fingerprints |
| Frictionless Data Package, RO-Crate | resource hashes; provenance | the statistical framing |
| Hive metastore federation, Azure SQL shard map | catalogs behind one routing layer; cached key map | route in the application |
| SQLite-per-tenant (Turso, LiteFS), Datasette | embedded database files routed by key | format version in the routing key |
| Durable Objects, Orleans, Akka, Ray actors | one instance per key | the catalog's object model; the write side; rejected for serving |
| Iceberg REST catalogs (Polaris, Nessie) | catalog service for writers; pinned metadata files for readers | Postgres upstream, files for serving |

---

## 16. Prototype plan

Order follows risk removed per unit of work. Synthetic bundles and `profile_bundle` shapes supply the corpus; a synthetic CODR-shaped catalog (7,000 small bundles, Zipf traffic) arrives at M8.

**M0. sdmxlib: read-only open.** `LocalRegistry.build(path)` for writers; `LocalRegistry.bundle(path)` requires a manifest and returns `BundleRegistry` with no `sql()`; `access_mode=READ_ONLY`; `ATTACH (READ_ONLY)`; `temp_directory` outside the publication; `CHECKPOINT` in `write_manifest`; `.wal` refused by the files check. Test: two processes open one published bundle and both answer `obs()`; `check_bundle` runs beside a server; `add` and `sql` are absent on the returned type. Built in GH-122.

**M1. Spikes.** (a) Per-cursor `search_path` across two attached bundles: **passed**. (b) Copy-and-extend over shared `data/` with snapshots expired and no cleanup: **passed**; the DuckLake layout is `<schema>/<table>/ducklake-<uuid>.parquet` and a small delta stays inlined until flushed. (c) Postgres DuckLake export of one bundle's slice to a file catalog: open; decides §14. (d) Delete-file rewrite: **passed**; `ducklake_rewrite_data_files` with `delete_threshold => 0` leaves no delete file, while the default threshold keeps small deletes as delete files.

**M2. sdmxlib: manifest 2.0 and layout.** Token directories; shared `data/`; `root`, `tier`, `md5`, `carried_from`; version fields; `data_path`; `artefacts` with `stability` and `partial`; `BindingCount` fields; `StoreFormatError` (newer-only for storage); copy-on-write rewrite after flush; `sdmxlib bundle copy --tiers`. Test: a reader on token A keeps answering while B is written sharing A's files; a republish of a CODR-shaped 50 MB bundle with a 1 MB delta uploads under 5 MB plus the catalog files; the census bundle's per-publication catalog upload (`meta.duckdb`, `lake.ducklake`) is measured and recorded; a bundle written by a newer DuckDB is refused with no file opened; no delete file survives a revising republish. Built in GH-123, GH-124, GH-125 and GH-128; GH-165 trims the published `meta.duckdb` to 548 MB on the census bundle.

**M3. sdmxlib: labels split with schema bump.** Shelved after measuring (GH-139, §7.1); GH-165 measures and trims the indexes in its place. As planned: `labels.duckdb`; store `schema_version` bump; cross-catalog table resolution; per-format label needs documented. Test: a key-only CSV request touches `meta.duckdb` only; an SDMX-JSON request attaches labels; old sdmxlib refuses the bundle by schema.

**M4. sdmxlib: fingerprints and canonical form.** The canonical model encoder (`Ref` as URN, defined forms for `InternationalString`, `ItemList`, `Annotations`, `Link`, enums, datetimes; golden tests per artefact class) is the largest piece. Then the row-text golden row per column type; table, artefact, structural (incoming edges included) and bundle fingerprints; `canonical_form`; `RENDER_VERSION` per format with the golden-file bump rule. Test: swapping two values between rows changes the table fingerprint; `{a,a}` vs `{b,b}` differ; the same bundle fingerprints identically on two DuckDB releases at one `canonical_form`; a change confined to a declared SDMX-JSON gap changes the artefact fingerprint; a constraint change on a dataflow changes its structural fingerprint. Built in GH-126, GH-127 and GH-132.

**M5. sdmxlib: effective token and check profiles.** Applied-overlay digest; per-type hook ids; `null` scope; `check_bundle(at=...)`; anti-joins generated from `_edges.py`; receive stamp; tier-3 verification by size and `Content-MD5`. Test: findings identical to the loading check across the corpus; `references` under 1 s on the census bundle. Built in GH-129, GH-130 and GH-131; `references` takes 0.15 s on the census bundle.

**M6. statcatlib: partials, structures bundle, local fixes.** Partial vendoring closed under parents; the structures bundle kind; `statcat._meta` check (statcatlib#39); refuse unknown `index_version`; token-named search tables. Test: a CODR-shaped product closure under 10 MB; a partial lacking an ancestor is refused at build.

**M7. sdmxlib: shared engine.** Per-cursor `search_path`; role-to-alias map; token-keyed caches. Test: 200 synthetic bundles attached in one engine; queries against each return that bundle's rows; the `lake` alias collision test passes with two bundles. Built in GH-133, with the 200-bundle test in `tests/unit/test_bundle_engine.py`; the token-keyed caches stay per registry (§7.2, GH-160).

**M8. statcatlib: catalog index 1.0 and structures document.** Builder, invariants §6.4, states, `pool`, `pinned`, `lifecycle`, `kind`, `products`, manifest-digest skip, `catalog/structures/`. Test: each invariant refused by name; a 7,000-entry build with 200 changes fetches 200 manifests; `latest` resolves through the structures document.

**M9. statcatlib: publish, release, GC.** Staging and HNS rename; `Content-MD5`; the pre-materialisation job on the pods' image; the two-index release flip with structures first; GC over the index chain. Test: a 200-table synthetic release becomes visible in one `current` write with zero failed requests; GC deletes exactly the unreferenced files and nothing inside the retention window; a request for a staged key before the flip returns 404 from every pod; a product whose partial gained a code is refused `serving` until its structures publication serves.

**M10. sdmxlib-rest: router and pools.** Builds on sdmxlib's remote open (GH-180), store-neutral verification (GH-181) and tiered file lists (GH-182). Index and structures polling; per-pool and per-schema visibility; LRU attach; tiered pull with pool promotion; `pinned` readiness; staged prefetch to disk without attach; binding-level keys with render version; result streaming to the CDN. Test: cold pod with one pinned bundle ready under 60 s; p99 first-attach for an unpinned bundle under 3 s; an index flip under load with no failed requests; a delta to one dataflow leaves other dataflows' results warm.

**M11. Kubernetes manifests.** Deployments for `hot`, `base`, `search`; KEDA; probes; preStop; PDB; topology spread; `hostPath` block cache; Envoy Gateway route with bounded-load hashing; identities per §10.1. Test: scaling `hot` from 4 to 20 under a synthetic release-day load remaps no more than 1/N of keys per added pod with zero failed requests.

**M12. Drills.** §9.2 end to end with mixed pods; restore by pointing `current` back; restore from the account-level backup after deleting a token directory. Test: zero 404s during the bump; hit rate across the bump; both restores verified by `check_bundle(at="receive")` on every entry.

**M13. Catalog-level search.** Built from the structures bundle and `statcat.dataset` union; served by the search pool.

---

## 17. Open questions

1. Name overlays (§5.4): settled in GH-134. Name and description operations, applied only by `fold_overlays`; built in GH-163.
2. Whether `token` becomes `publication` in a later major.
3. Detached signature format (minisign vs sigstore) when cross-agency sharing becomes concrete.
4. Code search scope for the agent (§12).
5. Republishing structures with semantic three-part versions; an FMR governance change decoupled from this work.
6. CODR bundle granularity: one per table by default, subject-level merging where tables always release together (§4.0).
7. `labels.duckdb` pull time on the census bundle: settled by GH-139. Labels are 31 MB of the census `meta.duckdb` and stay in it (§7.1).
8. How to split the structures bundles for StatCan (§8): by artefact family, by change rate, or both.

Settled in the v3 review: partials are not listed in `catalog/structures/` (routing needs full artefacts and `versions` only); `schema_version` stays exact-match (store bumps change queried columns, and §9.2 absorbs them); `BundleRegistry` has no `sql()`; the row text stays `to_json(t)` with a golden row per column type under `canonical_form`.
