# Sharded catalog of products and editions: design

Status: sixth revision. It carries the model the statcatlib RFC [Products, editions and the Inventory](https://github.com/StatCat-Suite/statcatlib/blob/main/docs/design/products-editions-inventory.md) settled (StatCat-Suite/statcatlib#81, its §11): the bundle, publication and token become the **product**, **edition** and **edition id**, the manifest becomes format 3.0, and the **Inventory** replaces the catalog index (§6) and statcatlib's serving list. The seal cost was measured before this revision (GH-248, §7.1), and the seal keeps its design. Names in this document are manifest 3.0's, released in sdmxlib 0.80.0 with store schema 2.52; §18 maps every renamed symbol, key, file and CLI command to the name before it.

sdmxlib's part of the earlier revisions is built: M0, M2, M4, M5 and M7, the name overlays and the format specification, released in 0.73.0, with M3 shelved (§16); then reading the remote tier from object storage (GH-180), store-neutral remote verification (GH-181), pulling by tier with any copy tool (GH-182), the fingerprint comparison §9.2 runs (GH-183) and the derived tables of §7.1 (GH-184), released in 0.75.0; then the incoming references of a fetched closure (GH-221), `build_edition` with its partials (GH-223), `partial_of` and manifest 2.1 (GH-224) and sibling dataflows left out of each other's structural fingerprints (GH-228), and the remote-tier measurement (GH-185), released in 0.76.0. This revision's sdmxlib work is GH-250 (the rename and manifest 3.0), GH-251 and GH-252 (the Inventory core and its conformance suite) and GH-253 to GH-259 (the behaviour changes of §18.3). statcatlib's and sdmxlib-rest's parts are in progress. Audience: implementers working on sdmxlib, statcatlib and sdmxlib-rest. Written against sdmxlib 0.79.0 and DuckDB 2.0.0; spike results and the decisions they settled are in §16 (M1) and §17. The work is the [Sharded bundle catalog](https://github.com/StatCat-Suite/sdmxlib/milestone/9) milestone, and GH-136 is the cross-repo plan.

This document specifies how a dissemination catalog of many SDMX products is published as editions, recorded in an Inventory, routed to serving pods, verified, cached and migrated. It builds on two published formats and changes both:

- sdmxlib edition format, manifest 3.0 and overlay 1.2: https://statcat-suite.github.io/sdmxlib/edition/
- statcatlib catalog format, statcat section 3: https://statcat-suite.github.io/statcatlib/bundle-format/

MUST, MUST NOT, SHOULD and MAY carry their RFC 2119 meanings. A *producer* writes an edition; a *writer* writes Inventory versions (statcatlib); a *reader* opens an edition or reads the Inventory; a *server* is a reader that answers SDMX REST requests; the *publish job* produces editions, and the *release job* writes the Inventory versions that release them.

---

## 1. Vision

A **catalog** (the `dcat:Catalog`) holds **products**. A product is the unit of publication, routing and lifecycle: a set of dataflows released together, with their structures, observations, catalog records and search index. A product holds a sequence of sealed **editions** over one shared `data/` directory; each edition is described by one manifest and never changes. The **Inventory**, a chain of immutable versions, decides each product's live edition. A serving pod attaches the editions its routing slice needs and nothing else. A release is one new Inventory version. An edition moves between a publish job, a blob store, a pod, a backup and another agency's machine without any of them rewriting it.

The properties this buys:

1. **Product equals shard unit equals backup unit equals cache unit.** One concept, one directory, one identity.
2. **Immutability by construction.** An edition has an `edition_id`; its directory is named by it; readers of an old edition keep a consistent view while the Inventory moves on.
3. **Verification without a database.** The Inventory names each edition with its manifest's SHA-256.
4. **Independent lifecycles.** Census 2021 is final and never rebuilt; CPI republishes monthly; CODR tables republish daily.
5. **Atomic releases.** A daily release of a few hundred tables becomes visible in one Inventory version.
6. **Portability.** An edition is a directory with checksums. Another NSO can receive one, run `check_edition`, and serve it.

### 1.1 Scope today and the target

Today: single large products, the census profiles, served through sdmxlib-rest as the .Stat getdata backend. One product, gigabytes of observations, hundreds of megabytes of structures (most of it bilingual labels on the geography and characteristic codelists), a per-language search index.

Target: a CODR-scale catalog in .Stat. On the order of 7,000 tables, most of them megabytes, a few gigabytes, with a daily 08:30 release that updates tens to a few hundred tables through delta files, under embargo.

Every rule in this document is written to hold at both scales. Where a rule exists only for the CODR target, the section says so.

## 2. Motivation

### 2.1 The workload

SDMX REST `getdata` hits one dataflow per request. Cross-dataflow joins do not occur on the serving path. Published vintages change on a schedule and never between editions. Traffic is skewed: on a release day one product takes most requests; across CODR a few hundred tables take most requests and thousands get a handful of hits a day.

This is functional partitioning, the easy form of sharding: no hot keys to rebalance across writers, no cross-shard transactions, no live resharding.

### 2.2 The single-catalog limits

The server today runs one DuckLake catalog file on the pod. At catalog scale that costs cold start proportional to the whole catalog on every pod; a republish of one product redistributing the whole file; one backup unit for thousands of change cadences; and one DuckDB storage version for everything.

### 2.3 What the formats already give

The sdmxlib edition is the shard: a stable `product_id`, a minted `edition_id`, per-file SHA-256, a schema check before open, a manifest written last through rename, overlays with a reproducible effective token, conformance checks. The statcat section adds the DCAT catalog schema, per-language LanceDB search tables, and an annotation fingerprint that already demonstrates logical content hashing.

### 2.4 What this revision adds

An Inventory that records every product's editions and their states, read the same way on a laptop and by a pod; one rule for which edition is live; embargo held by storage identity and by a split between public and operational readers; atomic, conditional writes that two writers cannot both win; and a crash rule that never re-releases withdrawn data.

---

## 3. Architecture

```
          publish job                            blob storage                              serving tier
┌────────────────────────────────┐   ┌─────────────────────────────────────────┐   ┌──────────────────────────────┐
│ FMR / statcatlib (structures)  │   │ products/<product_id>/data/             │   │ getdata pools (hot, base)    │
│ build(previous=) per product,  │──▶│ products/<product_id>/<edition_id>/     │──▶│   ShardRouter, shared engine │
│ copy-and-extend, seal          │   │ pending/<product_id>/<edition_id>.json  │   │ search pool                  │
│ → check → upload, manifest     │   │ inventory/<seq>.json, inventory/leaves/ │   │ structures: the structure    │
│ last → register pending        │   │ CURRENT          ◀── hint, forward-only │   │   product, served like any   │
│ release job → Inventory version│   │ results/<key>    ◀── pods write         │   │   other                      │
└────────────────────────────────┘   └─────────────────────────────────────────┘   └──────────────────────────────┘
```

Blob storage is the hand-off. The publish job, the release job and the GC job write the catalog; pods read it and write `results/`; the public reads nothing directly (§10.1).

| layer | document | owner | identity |
|---|---|---|---|
| sdmxlib edition | `manifest.json` | sdmxlib | `edition_id` (UUIDv7) |
| statcat section | `extensions.statcat`, `search/index.json` | statcatlib | same `edition_id` |
| overlay | `<name>.overlay.json` | server operator | overlay digest |
| Inventory | `inventory/<seq>.json`, `inventory/leaves/<sha256>.json`, `CURRENT` | schemas, validation, transitions and reader: sdmxlib; writer and `extensions.statcat`: statcatlib | `seq`, root SHA-256 |
| routing | in-memory shard map | sdmxlib-rest | Inventory `seq` |
| caches | result store, CDN | sdmxlib-rest | fingerprints, render version, effective token |

---

## 4. Edition format (manifest 3.0)

Manifest 3.0 renames manifest 2.1's identity keys, files and enums (§18) and changes nothing else in what a manifest records. A 2.x reader refuses a 3.0 manifest, and a 3.0 reader refuses a 2.x one, by the existing major-version rule. The store schema moves from 2.51 to 2.52 in the same release: the store's `_meta` keys `publication_token` and `previous_token` become `edition_id` and `previous`. There is no migrator before 1.0: a catalog is rebuilt by re-ingesting (`docs/upgrading.md`). After 1.0, a format change is an additive minor version or ships a migrator that carries the Inventory history forward (statcatlib RFC, decision 29).

Earlier versions: manifest 2.0 introduced the layout (`data_path`, `root`, `tier`); 0.73.0 kept store schema 2.48; 0.75.0 moved it to 2.49 for stored availability (GH-194); 0.76.0 moved it to 2.51, dropping the binding's layout columns for DuckLake's catalog (GH-205) and adding a reported attribute's order (GH-197). Readers and producers roll out together as one pinned image set (§9).

### 4.0 Product granularity

A product groups dataflows that are **published together** and **share a lifecycle**; grouping those that also **share structures** is preferred. The mechanism is indifferent: an edition holds any number of bindings, the Inventory maps each product to its live edition, routing hashes on `product_id`, and cache keys are per binding. The grouping decides four costs:

| unit the product is | consequence of grouping |
|---|---|
| publication | every member republishes together; catalog, labels and search files are rebuilt for the whole product and re-pulled by every pod holding it |
| lifecycle | `final` and `revisable` are per product, so a final member cannot share a product with a revisable one |
| closure | members sharing a DSD or codelists vendor them once; grouping removes duplicated labels |
| attach and readiness | members are pinned, pulled and evicted together |

The practical ceiling is the required tier's size: the product's catalog and labels must pull inside the readiness budget (§7.1). Examples: a census profile split into dataflows by geography level, or a family of tables derived from one DSD and released together, is one product; two census vintages are two products, grouped in a `ProductSeries`, because each is final; CODR tables default to one product per table because release days differ table by table, with subject-level grouping where tables always release together (open question 6).

### 4.1 Layout

```
<catalog>/catalog.json                       format_version, catalog_id, restore_epoch
<catalog>/CURRENT                            hint: {seq, root_sha256, restore_epoch}
<catalog>/inventory/<seq>.json               an Inventory root, created exclusively (seq zero-padded to 12 digits)
<catalog>/inventory/leaves/<sha256>.json     Inventory leaves, content-addressed
<catalog>/pending/<product_id>/<edition_id>.json   blob storage only: the upload inbox (§11.2)
<catalog>/products/<product_id>/
  data/                                      immutable data files shared by every edition of the product
    <schema>/<table>/ducklake-<uuid>.parquet   DuckLake's own layout
  journal/<edition_id>.json                  the seal journal (§6.7); removed once the writer records the edition
  <edition_id>/                              one edition; never rewritten once sealed
    manifest.json                            written last; its presence is what makes the edition sealed
    metadata.duckdb                          sdmx.* and statcat.*: ids, versions, parents, hierarchies, DSDs,
                                             bindings, constraints, catalog records          (required)
    tables.ducklake                          DuckLake catalog; data_path = ../data/           (required)
    search/                                  per-language LanceDB tables, index.json, embeddings.json (lazy, search pool)
```

- An **unsealed edition** is an `<edition_id>/` directory with no manifest. A product has at most one (§18.3, GH-253). sdmxlib never calls it a draft: a draft is a `1.0.0-draft` artefact version.
- Blob storage uses the same names, so an upload is a copy.
- `data/` is shared across editions. A data file is written once under a fresh UUID and referenced by every edition whose lake catalog lists it. A republish writes only new files. Producers MUST NOT delete, rewrite or run cleanup against `data/`; deletion is the GC job's alone (§11.4).
- An edition's `tables.ducklake` is made by **copying the previous edition's catalog and extending it** (new snapshot, new files registered). A producer MAY expire old snapshots and MUST NOT run `ducklake_cleanup_old_files` or `ducklake_delete_orphaned_files`; files those would delete are still referenced by earlier editions. A product's first edition starts an empty catalog.
- **Refusing other layouts.** Opening a directory with a root-level `meta.duckdb`, a `bundle.manifest.json`, or no `catalog.json` beside existing files is refused, and nothing is created (statcatlib; the edition reader refuses a 2.x manifest).
- Shelved (GH-139; §7.1 has the measurement): names stay in `metadata.duckdb`, and the planned `labels.duckdb` split was not built.
- `search/` table names carry the `edition_id` rather than an integer generation; the producer drops orphaned tables before it seals.

### 4.2 Manifest fields

| field | presence | meaning |
|---|---|---|
| `format_version` | required | `"3.0"`. |
| `product_id` | required | The product this edition belongs to. |
| `edition_id` | required | This edition's id, a UUIDv7, and the name of its directory. |
| `previous` | required | The `edition_id` this edition was built from, or `null`. |
| `created_at`, `producer` | required | When and by what the edition was sealed. |
| `canonical_form` | required | Version of the canonical row and model text the fingerprints hash (§4.4). |
| `content_fingerprint` | optional | Product fingerprint of the edition (§4.4), or `null`. |
| `duckdb_version` | required | DuckDB release that wrote the DuckDB files. |
| `duckdb_storage_version` | required | Storage version of the DuckDB files. |
| `ducklake_version` | required | DuckLake extension version that wrote `tables.ducklake`. |
| `ducklake_spec_version` | required | DuckLake spec version recorded in the catalog. |
| `data_path` | required | Where the lake's data files live, relative to the edition directory: `"../data/"`. A reader resolves it against where the edition is, a local directory or, for the remote tier, the remote root the deployment names (GH-180). An absolute location would tie an edition to one storage account, so the manifest never records one. |
| `artefacts` | required | Every maintainable artefact in the store: `ArtefactEntry` list sorted by `urn`. |
| `producer_fields` | optional | Free-form fields the producer records and sdmxlib does not read. Manifest 2.x called it `product`, which after the rename would not be the product. |

`EditionFile`:

| field | presence | meaning |
|---|---|---|
| `root` | required | `"edition"`: `path` is relative to the edition directory. `"data"`: `path` is relative to `data_path`. |
| `tier` | required | `"required"` pulled before a pod is ready; `"lazy"` pulled to local disk on first use; `"remote"` read from object storage by byte range (§7.1). |
| `digests` | required | The file's digests by algorithm, lowercase hex: `sha256`, `md5` (the `Content-MD5` an Azure blob keeps) and `crc32c` (what S3 keeps whole for a multipart upload and GCS keeps for every file), and any other a store adds (GH-181). |
| `carried_from` | optional | The `edition_id` whose manifest this entry's digests were carried forward from. |

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
| `stability` | required | `"semantic"` (a three-part version with no extension and a major version of at least 1, as `model/version.py`'s `is_final` reads it; SDMX 3.0 guarantees its immutability) or `"legacy"` (any other version, `0.1.0` included). |
| `partial` | required | `true` for a partial codelist or scheme vendored as a subset (§8). |
| `fingerprint` | required | Artefact fingerprint (§4.4). |
| `partial_of` | optional | For a partial, the artefact fingerprint of the full artefact it was cut from; absent for a full artefact. The release job compares it with the structure product's entry for the same URN (§6.5), so the partial's codes never need to be in the manifest. |

`extensions.<name>` sections SHOULD carry their own `format_version`.

**Worked example** (abridged):

```json
{
  "format_version": "3.0",
  "product_id": "14100287",
  "edition_id": "01a18e76-2bad-7480-abfe-2ba8a6889a45",
  "previous": "01a18e63-e292-7250-a332-b79d9b7a7efd",
  "created_at": "2026-10-30T12:30:00.000000Z",
  "producer": {"name": "statcatlib", "version": "0.12.0"},
  "schema_version": "2.52",
  "data_path": "../data/",
  "files": [
    {"path": "codr/t14100287/ducklake-0193….parquet", "size": 7425310, "root": "data", "tier": "remote",
     "digests": {"sha256": "…", "md5": "…", "crc32c": "…"}, "carried_from": "01a18e63-e292-7250-a332-b79d9b7a7efd"},
    {"path": "metadata.duckdb", "size": 9187328, "root": "edition", "tier": "required", "digests": {"sha256": "…", "md5": "…", "crc32c": "…"}},
    {"path": "tables.ducklake", "size": 3944448, "root": "edition", "tier": "required", "digests": {"sha256": "…", "md5": "…", "crc32c": "…"}}
  ],
  "bindings": [{"dataflow": "urn:…Dataflow=STC:DF_14100287(1.0)", "observations": 5475519, "structural_version": "urn:…DataStructure=STC:DSD_14100287(1.0)", "fingerprint": "…:5475519", "structure_fingerprint": "…"}],
  "artefacts": [{"urn": "urn:…Codelist=STC:CL_GEO(1.0)", "stability": "semantic", "partial": true, "fingerprint": "…", "partial_of": "…"}],
  "canonical_form": "1",
  "content_fingerprint": "…",
  "producer_fields": {},
  "extensions": {"statcat": {"format_version": "3"}}
}
```

### 4.3 Constructors and reader rules

- Two constructors, two types. `LocalRegistry.build(product_dir, previous=, resume=)` starts or resumes the product's unsealed edition (the store with its `DATA_PATH`) and returns `LocalRegistry` with the write surface; `fetch_edition`, `synthetic_edition` and `fold_overlays` build through it. `LocalRegistry.edition(edition_dir)` requires `manifest.json`, opens read-only, and returns `EditionRegistry`, a type carrying `get`, `query`, `obs`, `dsd`, `binding_for` and `browse` and **no `sql()`**: in a shared engine a fully qualified name reaches any attached catalog, `search_path` scopes unqualified names only, so a request-reachable raw SQL surface would read other editions.
- `seal(edition_dir, ...)` (manifest 2.x's `write_manifest`) turns the unsealed edition into a sealed one (§11.1); `discard(edition_dir)` deletes an unsealed one and refuses a sealed one; `list_editions(product_dir)` lists each edition with its id, whether it is sealed, its `previous` and its creation time, reading manifests only.
- Read-only open: the DuckDB files with `access_mode=READ_ONLY`, the lake with `ATTACH ... (READ_ONLY, OVERRIDE_DATA_PATH ...)`. Any number of processes may hold a sealed edition open. Each process MUST set `temp_directory` outside the edition directory (the default is `<file>.tmp` beside the database, inside the edition).
- A reader MUST compare `duckdb_storage_version` and `ducklake_spec_version` with its engine before opening any file. It refuses a storage version **newer** than its engine reads (`StoreFormatError`, naming both); older storage versions open, as DuckDB allows. `ducklake_spec_version` follows the same rule: DuckLake 1.1 reads the census edition's 1.0 catalog read-only without migrating it (the file's sha256 is unchanged), so only a newer spec is refused. The sdmxlib `schema_version` stays exact-match.
- A reader MUST resolve data files through `data_path`.
- A reader MUST ignore `manifest.json.sig` in the `files` check (reserved for a detached signature).

### 4.4 Fingerprints

Three levels. Each is tagged by `canonical_form`, the version of the text it hashes; a change to that text (a DuckDB JSON rendering change, a model serialiser change) bumps `canonical_form`, and fingerprints at different `canonical_form` values are never compared. Golden tests on the canonical text catch the need for a bump. The rename changes no canonical text, so `canonical_form` stays `1` and every fingerprint of an unchanged edition is unchanged.

**Row text.** `to_json(t)` for the table alias `t`, columns in schema order, which DuckDB renders with keys in column order. Typed values render through DuckDB's JSON conversion; `canonical_form` covers its format.

**Table fingerprint.** Order-independent, duplicate-safe, stable across engine releases:

```sql
SELECT
  coalesce(sum((md5_number_lower(to_json(t)) % 4611686018427387904)::HUGEINT), 0)::VARCHAR
    || ':' || count(*)::VARCHAR
FROM lake.<schema>.<table> AS t;
```

An empty table fingerprints as `0:0`. MD5 is a fixed standard. Each term is below 2^62, so a HUGEINT sum does not overflow below 2^66 rows. Swapping two values between rows changes two row digests; duplicate rows add their digest twice.

**Artefact fingerprint.** SHA-256 of the artefact's canonical model text, `sdmxlib.edition.canonical_json` at `CANONICAL_FORM` 1 (GH-126; msgspec cannot encode the attrs model, and the overlay digest works only because overlays are msgspec structs). The text is compact key-sorted JSON: every attrs object carries `"$class"` (its Python class) and every init field, `null` included; `maintainer` is the agency id; a `Ref` is its URN alone, never followed; an enum is its member name; a datetime is UTC with microseconds and `Z`; lists keep their order and sets sort by canonical text; a non-finite float, a naive datetime and any undefined type are refused. Golden texts cover all 30 artefact classes, and a walk asserts every field of every object reaches the text. The model carries fields SDMX-JSON cannot (the 28 declared gaps), so the wire format is unsuitable here; `docs/edition.md` §9 states the form for other implementations.

**Structural fingerprint of a dataflow.** The artefact fingerprints of the dataflow's closure, sorted by URN, joined with `\n`, SHA-256. The closure is: outgoing references from the dataflow and its DSD (concept schemes, codelists as vendored including partials, hierarchies), **plus the artefacts other than a dataflow that reference the dataflow or DSD** (constraints, provision agreements, hierarchy associations), plus those artefacts' own outgoing references. Another dataflow of the same DSD references it too and is left out, so an edit to one dataflow leaves its siblings' fingerprints and cached results (GH-228). Constraints and associations point at the dataflow rather than the reverse, so an outgoing-only walk would miss a constraint change that alters what is served.

The fingerprint covers the closure the store holds, so the producer has to fetch the incoming references too. From an SDMX REST registry that is `references=all` on the dataflow and `references=parents` on its DSD over the 3.0 API; SDMX 2.1 has no hierarchy association, and FMR 12.4.2's 2.1 API returns none. `build_edition`, which `fetch_edition` calls, asks a `RestRegistry` for each dataflow with `resolve="all"` and its DSD with `resolve="parents"`, so its editions hold the incoming artefacts (GH-221).

**Product fingerprint of an edition** (`content_fingerprint`). SHA-256 of `artefacts:` then each artefact fingerprint in URN order, `bindings:` then `<dataflow urn>\t<table fingerprint>` in dataflow order, joined with `\n`.

### 4.5 Invariants, stated as requirements

- **Closure.** An edition MUST resolve every reference inside itself.
- **Pinned URNs.** Every artefact MUST be stored under its pinned URN.
- **Equivalence of copies.** Two editions holding the same full (non-partial) pinned URN MUST hold the same logical content. SDMX guarantees this for semantic versions; for legacy versions the fingerprint is the guarantee and the release job enforces it (§6.5). A partial artefact is compared as a subset of the full one. The registry's pinned-URN cache carries the same qualification: legacy and partial artefacts are cached under `(edition_id, urn)`.
- **Checkpointed, flushed lake.** A producer MUST `CHECKPOINT` every DuckDB file before the manifest (DuckDB replays a WAL on read-only open, and a WAL's contents sit outside the checksummed file, so a shipped `.wal` makes the `files` check meaningless; the check refuses an unlisted `.wal` naming the missing checkpoint), MUST run `ducklake_flush_inlined_data`, and MAY expire snapshots. No cleanup against `data/` (§4.1).
- **Copy-on-write revisions.** A `revisable` edition that revises or deletes rows MUST, after `ducklake_flush_inlined_data` (which turns an inlined delete into a delete file), rewrite every table that holds delete files into new data files with `ducklake_rewrite_data_files(catalog, delete_threshold => 0)` (or the catalog option `rewrite_delete_threshold` set to 0), then compact. The default threshold skips a table whose deleted rows are a small fraction, which is exactly a daily delta; spike M1(d) measured that it leaves the delete file at the default and removes it at 0. No delete file ships; the check refuses one.
- **Compaction for final products.** A producer SHOULD merge each table to one file, or one per partition, when the product is `final`; `seal(lifecycle="final")` does. DuckLake sorts what it merges by the table's `SORTED BY`, which the producer sets where it writes the rows (`write_observations(sorted_by=...)`, GH-205); the seal sets no sort of its own. Artefact finality plays no part.
- **Edition directories.** Written once, never rewritten after the seal. Data files written once, never modified.
- **Carry-forward.** An edition MAY carry forward a data file's `size` and digests from the previous manifest, marking `carried_from`, and MUST verify size and a digest the store keeps against them.
- **Blob digests.** The publish job MUST upload every file so its store keeps a digest the manifest records: `Content-MD5` on Azure, a full-object CRC32C on S3; GCS computes CRC32C itself (`docs/edition.md`, Remote files).

### 4.6 `check_edition` profiles

```python
check_edition(path, at: Literal["publish", "receive", "open"], ...)
```

| check | publish | receive | open |
|---|---|---|---|
| manifest parse, format, versions | yes | yes | yes |
| files, `root: edition`: size, every recorded digest, nothing unlisted | yes | yes, once per edition (stamp, §4.7) | size only |
| files, `root: data`: listed entries only | every recorded digest | size | no |
| expect: fingerprints equal an earlier manifest's | with `expect=` | with `expect=` | with `expect=` |
| remote: `data/` in the object store, size and a digest the store keeps | no | with `lookup=` (or sizes with `remote=`) | no |
| lake: every file the current snapshot references exists at the recorded size | yes | yes | no |
| bindings counts | yes | yes | no |
| references (SQL, §4.8) | yes | yes | no |
| fingerprint recompute | yes | optional | no |
| availability: coded columns hold codes, stored availability is the data's | yes | no | no |
| tags: every stored language tag is in normalised form | yes | yes | no |
| partials: each `partial_of` equals the full artefact's fingerprint, and the partial's codes and parents are in it | with `structures=` | with `structures=` | refused |
| extensions (statcat) | yes | yes | section only |

Orphans are defined over the union of manifests and belong to GC (§11.4).

### 4.7 Receive-time stamp

After a successful receive-time check a reader MAY write `<pod-local>/verified/<product_id>/<edition_id>` recording the manifest SHA-256. A later open of the same edition with the same manifest SHA-256 runs the `open` profile; with no stamp, or a stamp for another manifest digest, the reader runs the `receive` profile first.

Built in GH-130 as `check_on_open(path, stamps=dir)`: the stamp is `<stamps>/<product_id>/<edition_id>`, JSON holding the manifest sha256, the checks that ran and the time, written by a rename once `receive` finds nothing. At `receive`, `data/` is checked by local size, or with `lookup=` by size and a digest the store keeps through `verify_remote`; the lake and the bindings still read the data where the registry reads it. `check_edition(at="receive", fingerprints=True)` opts into the recompute; `check_on_open` takes no `fingerprints=`, so a server's receive check never recomputes. Extensions take the profile as a fourth argument.

### 4.8 `references` as SQL

`storage/_references.py` builds the check (GH-129). The references are the rows of `artefact_ref` together with every reference column the declared edges in `query/_edges.py` name, plus five columns outside the edge table (`UNDECLARED_REFERENCES`). The edge columns are needed: a concept's core representation has no `artefact_ref` row. A pair that `artefact_ref` already holds counts once.

The anti-join runs against every URN in the store: `artefact.urn` and one URN per stored item, built from the owning scheme's URN, the item class and the item's id or path, for the 20 item kinds the store keeps (`STORED_ITEMS`). A target with no exact match is tried under the spellings the loading check accepts: a version-less URN matches any version held, and `CLASS_ALIAS_GROUPS` folds the renamed component classes.

The loading check is the differential oracle in `tests/unit/test_references_sql.py`, over every fixture and the canonical corpus, each with every artefact dropped and with one leaf item of each kind deleted. Measured on the census edition: 0.15 s over 2.16 million references; 0.5 s more when a target needs the spelling pass.

---

## 5. Overlay changes (overlay 1.1 / effective token)

### 5.1 Scope selection enters the token

A server that applies only entries whose `scope` matches an audience MUST compute the overlay digest over the overlay **as applied**. A `null` scope applies to every audience.

### 5.2 Merge hook identities enter the token

Hooks are per annotation type. The effective token string is `v3\n<edition_id>\n<hooks>\n<overlay digest 1>\n...`, where `<hooks>` is `type=<package>:<name>@<version>` for each type with a non-default hook, sorted by type, joined with `,`, or `default` when none. The leading `v3` names the string's format; manifest 2.x's `v2` named the edition by its `token` (GH-131). The effective token is a derived identity of what a server applies, and keeps its name.

Built in GH-131: a hook is a `MergeHook(package, name, version, fn)`, and `apply_overlays`, `effective_token` and `fold_overlays` take `audience=`, applying the entries scoped to it and the unscoped ones. Overlay format 1.1 defines `scope`.

### 5.3 Search-affecting overlays

A server that serves search MUST refuse at load any overlay with entries acting on a type in `SEARCH_ANNOTATION_TYPES` (`overlay_affects_search`), logging the entries. The fix is `fold_overlays`, `build_index`, `seal`.

### 5.4 Names

Decided (GH-134): an overlay MAY change a `name` or `description`, and only `fold_overlays` applies such an entry. Built in GH-163 as overlay format 1.2's `texts`: `OverlayText` entries that set or remove one language of a name or description.

- **The format.** An overlay entry gains `name` and `description` operations on artefacts and items, set per language, beside the annotation operations. The common case is a mistyped French name on a code.
- **Folded, never served.** `fold_overlays` writes the change into a new edition. `previous=` copies `metadata.duckdb` and reuses every data file, so a one-word fix is a new edition over the same lake. `apply_overlays` refuses an overlay carrying a name or description operation and names `fold_overlays`; a server therefore never applies one at load.
- **Why.** A name is structure in SDMX. It feeds search and every label map, and §7.2 caches structure per `(edition_id, urn)`. Served at load, a name overlay would make served structure differ from the edition's, be search-affecting (§5.3), and require every label map and structure cache to key on the effective token. Folded, the new edition covers the change, and every cache, the search index and the manifest's fingerprints stay consistent with what is served.

---

## 6. The Inventory (format 1.0)

The Inventory replaces manifest 2.x's catalog index, its structures document and statcatlib's serving list. It decides each product's live edition on every catalog: a laptop's directory and a .Stat container alike. It is a chain of immutable, versioned documents, each a **root** plus one **leaf** per product.

Ownership (statcatlib RFC decision 8): sdmxlib owns the JSON schemas, validation, the state-transition rules and a reader, and holds no blob client: the reader takes a `fetch(path) -> bytes | None` callable (§11.5). statcatlib owns the writer and the `extensions.statcat` section, which carries the `CatalogDescription`. sdmxlib-rest pods read the Inventory through their sdmxlib dependency.

### 6.1 `catalog.json` and `CURRENT`

`catalog.json`, written once when the catalog is created and rewritten only to bump `restore_epoch`:

| field | presence | meaning |
|---|---|---|
| `format_version` | required | `"1.0"`. |
| `catalog_id` | required | A UUIDv7. An export (`CatalogAdmin.export`) keeps it. |
| `restore_epoch` | required | An integer, starting at 0. A whole-catalog restore or failover bumps it, and readers reset their `seq` floor when it changes (§6.6). |

`CURRENT`, a hint naming the newest Inventory version: `{"seq": 42, "root_sha256": "…", "restore_epoch": 0}`. Writes to it are conditional and only move forward. Nothing trusts it without verifying the root it names.

### 6.2 Root

`inventory/<seq>.json`, created exclusively (§6.6):

| field | presence | meaning |
|---|---|---|
| `format_version` | required | `"1.0"`. |
| `seq` | required | This version's sequence number. The first version is 1. |
| `previous_inventory` | required | The `seq` this version was built from, or `null` for version 1. |
| `restore_epoch` | required | Equal to `catalog.json`'s when written. |
| `created_at` | required | An RFC 3339 UTC instant ending in `Z`. |
| `producer` | required | `{name, version}` of the library that wrote this root. |
| `embargo` | required | `true` or `false`. With `false`, a seal releases its edition in the same version (§6.4). |
| `products` | required | A map from `product_id` to `{leaf, leaf_sha256}`, `leaf` the path `inventory/leaves/<sha256>.json`. |
| `retired` | required | Tombstones: every `{product_id, edition_id, retired_at}` retired within the tombstone window, so a retired edition stays distinguishable from one never recorded. Kept at least as long as backups (§11.6). |
| `event` | required | `{actor, reason, kind}`: who acted and why. `kind` is one of `seal`, `release`, `rollback`, `withdraw`, `retire`, `embargo-change`, `restore`, `correct` and `describe`. `describe` records a change to the catalog description in `extensions.statcat` and nothing else. A reader refuses a root whose `kind` it does not know, so a new kind ships in every reader before any writer uses it: `describe` needs sdmxlib 0.81.0 or later (GH-334). `actor` comes from the writer's identity (a token's object id), never from the caller. |
| `extensions` | optional | Sections keyed by package; `statcat` holds the `CatalogDescription`. |

### 6.3 Leaf

`inventory/leaves/<sha256>.json`: one product's entries for every edition that is not retired, content-addressed by the SHA-256 of its bytes and written before the root that names it. A one-product change writes one new leaf and one new root; every other leaf is reused by hash.

| field | presence | meaning |
|---|---|---|
| `format_version` | required | `"1.0"`. |
| `product_id` | required | The product. |
| `kind` | required | `"data"` or `"structure"` (§8). |
| `lifecycle` | required | `"final"` or `"revisable"`. |
| `editions` | required | `LeafEntry` list sorted by `edition_id`. |

`LeafEntry`:

| field | presence | meaning |
|---|---|---|
| `edition_id` | required | The edition. |
| `previous` | required | Its manifest's `previous`. |
| `state` | required | `embargoed`, `live`, `superseded` or `withdrawn` (§6.4). |
| `manifest_sha256` | required | SHA-256 of the edition's `manifest.json`. A reader verifies it before reading anything else. |
| `schema_version` | required | The store schema the edition was written at. |
| `state_changed_at` | required | When the entry took its state, an RFC 3339 UTC instant. |
| `release_at` | required | For an `embargoed` entry, the instant it becomes releasable, or `null` for no scheduled release; `null` otherwise. |
| `structure_source` | optional | Where the structures came from when a registry is authoritative: `{registry, query, retrieved_at, fingerprints}` (statcatlib RFC §8). |
| `artefacts` | optional | `{urn: {fingerprint, partial_of}}`, the manifest's partials, a 1.x optional field (statcatlib RFC decision 25). The release job reads the manifests it verifies, so it enforces the structures-first rule without it. |

Phase 4's routing fields (`pool`, `pinned`, `location`, `pull_bytes`, `dataflows`) arrive as 1.x optional fields.

**Versioning.** A minor version adds only optional fields; readers ignore unknown fields and refuse a newer major version.

**Worked example.** Version 42, after CODR table 14100287's September release, with census unchanged:

```json
{
  "format_version": "1.0",
  "seq": 42,
  "previous_inventory": 41,
  "restore_epoch": 0,
  "created_at": "2026-10-09T12:30:00.412Z",
  "producer": {"name": "statcatlib", "version": "0.12.0"},
  "embargo": true,
  "products": {
    "14100287": {"leaf": "inventory/leaves/9f2c….json", "leaf_sha256": "9f2c…"},
    "census-2021-cpt": {"leaf": "inventory/leaves/41b0….json", "leaf_sha256": "41b0…"}
  },
  "retired": [{"product_id": "14100287", "edition_id": "01a17b02-…", "retired_at": "2026-09-30T00:00:00Z"}],
  "event": {"actor": "release-job@stc", "reason": "08:30 release", "kind": "release"},
  "extensions": {"statcat": {"format_version": "3", "description": {"title": {"en": "…", "fr": "…"}}}}
}
```

```json
{
  "format_version": "1.0",
  "product_id": "14100287",
  "kind": "data",
  "lifecycle": "revisable",
  "editions": [
    {"edition_id": "01a18e63-e292-7250-a332-b79d9b7a7efd", "previous": "01a17f00-…", "state": "superseded",
     "manifest_sha256": "…", "schema_version": "2.52", "state_changed_at": "2026-10-09T12:30:00.412Z", "release_at": null},
    {"edition_id": "01a18e76-2bad-7480-abfe-2ba8a6889a45", "previous": "01a18e63-e292-7250-a332-b79d9b7a7efd", "state": "live",
     "manifest_sha256": "…", "schema_version": "2.52", "state_changed_at": "2026-10-09T12:30:00.412Z", "release_at": null}
  ]
}
```

### 6.4 States and transitions

Every entry has one of four states. Retirement is a tombstone, never a state.

| state | meaning |
|---|---|
| `embargoed` | sealed and recorded, not routable, with `release_at` |
| `live` | routable; the product's head at its `schema_version` |
| `superseded` | replaced by a newer live edition; a valid rollback target |
| `withdrawn` | rolled back from or cancelled; never routed again automatically |

| from | to | operation | precondition | who |
|---|---|---|---|---|
| (sealed) | `live` | seal, embargo off | fast-forward: the product has no live edition at this `schema_version` and `previous` is `null`, or `previous` is that live edition | statcatlib `WorkingCopy.seal()` |
| (sealed) | `embargoed` | upload or register | the same fast-forward check | publish job |
| `embargoed` | `live` | release, or `release_due(now)` | `release_at` ≤ now (the storage server's time), or explicit; fast-forward check | `CatalogAdmin` |
| `embargoed` | `withdrawn` | cancel | none | `CatalogAdmin` |
| `embargoed` | `withdrawn` | `supersede_pending(product, by=)` | the new edition is built on the same live edition | `CatalogAdmin` |
| `live` | `superseded` | another edition of the product goes live | (the same Inventory version) | (the same writer) |
| `live` | `withdrawn` | `rollback(product, to=Y, if_live=X)` | the live edition is X (by default the one live when the call started), and Y is `superseded` | `CatalogAdmin` |
| `superseded` | `live` | the same rollback | as above | `CatalogAdmin` |
| (tombstone) | `superseded` | `reinstate(product, edition, reason)` | the manifest and every listed file verify | `CatalogAdmin` |
| `superseded`, `withdrawn` | tombstone | retention | past the window | prune job |

- **The head.** A product's head is its `live` entry at the reader's `schema_version` (statcatlib RFC decision 24). During a store schema rollout (§9.2) a product has one live edition per schema version: old pods read the old one, new pods the new one. A product with no live edition has no head.
- **Fast-forward.** A release whose edition's `previous` is not the product's live edition at its `schema_version` is refused, naming both editions. Forks are refused here, at release, and never at seal: a seal-time refusal would block every publish after a rollback.
- **Rollback is conditional and forward.** `if_live` defaults to the edition live when the call started; a retry is refused if the live edition is no longer X. `to` defaults to the most recent superseded edition. A rollback is a new Inventory version; it never moves `CURRENT` back, which would drop other products' later releases.
- **Rollback ripples.** An unsealed or pending edition built on a withdrawn one then fails the fast-forward check. The error names the withdrawn base, the current live edition, and the fix: discard, then build from the live edition.
- **Release batches report per product.** `release_due` releases what it can and refuses the rest, persisting each refusal's reason, without failing the batch.
- **The release clock is the storage server's.** The release job takes `now` from the storage response `Date` header with a skew bound and refuses to run if its own clock is ahead; `release_due(now=None)` defaults to that time and refuses a naive datetime.

sdmxlib states these rules as pure functions a writer calls before it publishes a version (GH-251): given the current root and leaves, an operation and its arguments, they return the new leaf entries or raise a refusal naming the rule.

**The live view.** It is derived inside sdmxlib's reader and never stored. sdmxlib exposes two separately constructed reader types:

- `LiveInventory` returns only `live` entries and drops products with no live edition. The public catalog and the pods' routing use it, so a public reader never sees an embargoed entry and has nothing to filter.
- `FullInventory` returns every state. Only the operational objects (statcatlib's `CatalogAdmin` and `EmbargoReader`) and the release job construct one.

### 6.5 Invariants the release job enforces

1. A product has at most one `live` edition per `schema_version`.
2. `manifest_sha256` matches the bytes fetched.
3. Every entry's `canonical_form` is one the release job knows.
4. Full artefacts agree: equal fingerprints wherever one URN appears as a full artefact in more than one live or embargoed edition. Every partial's `partial_of` equals the fingerprint of the full artefact at its URN in the live or embargoed structure product's edition. The partial is a subset of that artefact because the producer cut it from it; `check_edition(at="publish")` checks the subset, where both artefacts are at hand.
5. Final means final: a new edition of a `final` product carries an equal `content_fingerprint`, or the product is first made `revisable`.
6. Chain continuity: every entry's `previous` is an edition of the same product, recorded or tombstoned.
7. Every full artefact has exactly one `kind: structure` home among live editions (§8 allows several structure products per agency).
8. Publish order: a data edition is refused `live` until, for each of its partials, the structure edition whose fingerprint equals the partial's `partial_of` is `live`. An edit to a full artefact at an unchanged URN therefore holds back every product cut from the old one until that product is republished from the new one.

The release job reads every manifest it releases to verify `manifest_sha256`, so it reads the artefacts and partials there. A 7,000-entry catalog's leaf fetch is timed in Phase 4, and a baked summary is added only if that measurement shows the cost (`docs/design/caching.md`).

### 6.6 Write and read protocol

**Atomic exclusive publish.** Exclusive creation of `inventory/<seq>.json` is the compare-and-swap, and it MUST win with the file already complete: `O_EXCL`, and ADLS DFS create followed by append and flush, both create the name before its content, so a crash leaves an empty head that blocks every later write.

- **Locally:** write a temporary file, fsync it, then `link()` it into place, or rename it with `renameat2(RENAME_NOREPLACE)`, `renamex_np(RENAME_EXCL)` or `MoveFileEx` without the replace flag; then fsync the directory.
- **On blob storage:** a single-shot `Put Blob` (or `Put Block List`) with `If-None-Match: *`, never DFS create plus append. statcatlib uses `obstore` (`put(..., mode="create")`). Untested on OneLake's Blob API.
- Leaves are written first, by the same rule; a leaf that exists already has the same bytes, since its name is its hash.

**Retry.** A writer that loses re-reads the new head and re-evaluates every precondition against it: the fast-forward check, the expected live edition, and the `embargo` header. A stale automatic release must not write `live` after a concurrent change turned embargo on.

**`CURRENT`.** Written after the root, conditionally and forward only. A reader:

1. reads `CURRENT`;
2. verifies the named root's SHA-256, and falls back to the version before it on a mismatch;
3. probes `seq+1`, `seq+2`, … until one is absent, with no LIST;
4. never accepts a lower `seq` than one it already holds, unless `restore_epoch` changed, which resets its floor.

This is Delta's `_last_checkpoint` pattern. The release job brings `CURRENT` up to the newest root on startup. A reader skips an invalid root, alerts, and keeps the last valid version.

**Unsupported locations.** Synced folders (Dropbox, OneDrive) and network shares are documented as unsupported.

**An escape route.** The Inventory is a small log of its own, close to Delta's `_delta_log` with a hint pointer. If it ever needs checkpoints, leaf compaction or more tree levels, that need triggers a review of replacing it with a Delta table of Inventory rows rather than adding a feature. GH-252's conformance suite runs against a Delta-backed prototype to prove the route (statcatlib RFC decision 31).

### 6.7 Crash recovery

A seal writes the manifest (sdmxlib) and then an Inventory version (statcatlib), so a crash between them leaves a **sealed-but-unrecorded** edition: one with a manifest that no Inventory version has named.

**The seal journal.** Before it writes the manifest, `seal` writes `products/<product_id>/journal/<edition_id>.json`: `{product_id, edition_id, previous, sealed_at}`, by the same complete-before-visible rule as a root (GH-259). The writer removes the entry once it has recorded the edition.

**The repair is narrow.** The writer records an unrecorded edition only when:

1. no Inventory version within retention names it, and it is not a tombstone; **and**
2. a journal entry shows this writer sealed it.

A broad scan would re-release withdrawn editions: an edition rolled back and then retired still has a manifest naming the live edition as `previous`. The repair runs when an unsealed edition is started, before `build` picks `previous`, and again on seal and resume. Reads never repair. This replaces manifest 2.x's rule that a scan only reports orphans, for the local case; on a server an unrecorded edition is an alert with an owner (§11.6).

**Rebuilding the Inventory.** `rebuild-inventory` rebuilds from the manifests only for a catalog that has never been rolled back, since `withdrawn` is recorded in no manifest. Otherwise it refuses and names the missing history.

### 6.8 Multi-vintage products

Census 2021 and 2026 are two products with distinct DSDs, grouped in one `ProductSeries` that statcatlib records in its extension.

---

## 7. Routing and serving

### 7.0 Units

| unit | what it is | cardinality |
|---|---|---|
| product | the unit of publication, verification, caching and lifecycle | thousands |
| pod | the unit of compute: one DuckDB engine holding many editions read-only, attached on demand, evicted when cold | a handful per pool |
| pool | the unit of scaling: identical pods responsible for a set of products | hot, base, search |
| routing | product → one pod in its pool by consistent hashing | derived from the Inventory |

A pod holds many editions; an edition is held by as many pods as its pool has replicas. A pod never writes an edition.

### 7.1 Placement tiers

| tier | rule | files |
|---|---|---|
| `required` | pulled before ready; readiness gates on it | manifest, `metadata.duckdb`, `tables.ducklake` |
| `lazy` | pulled to node-local disk, in the background after ready or on first use | `search/` in the search pool |
| `remote` | read from object storage by byte range through DuckDB's in-memory external file cache | everything under `data_path` |

The manifest's `tier` is the producer's default; a pool's configuration MAY promote a tier. A lake has one data path, so the remote tier is promoted whole: every data file local, or every one remote. An edition file outside `data_path`, such as `search/`, MAY be promoted alone.

The remote tier needs sdmxlib to open an edition whose data files are remote: `LocalRegistry.edition(path, remote=...)` resolves `data_path` against the remote root, over any store DuckDB reads (`s3://`, `gs://`, `az://`, `abfss://`), with the credentials as DuckDB secrets on the `EditionEngine` (GH-180).

**Remote-tier reads, measured** (GH-185; `bench/tier3/`, the census edition's data files in Azurite, 4 DuckDB threads). DuckDB's in-memory external file cache, on by default, serves a repeated read in the same process with no request: one geography costs 938 GETs and 85 MB cold and none again, one characteristic 1,922 GETs and 224 MB and none again. A process restart empties it, and spilling evicted blocks (`external_file_cache_spill`) does not survive one either, since each engine spills under its own temp directory; at a 400 MB `memory_limit` spill halves what a repeated characteristic read fetches (235 MB to 121 MB). No sdmxlib cache is built: the repeated read, the one a cache serves, is already free. A cold read's cost is its request count, about 90 KB per GET by default. Larger requests cut it: a 1 MB `external_file_cache_remote_min_block_size` reads the geography in 133 GETs (150 MB), `external_file_cache_request_sizing = GRID` in 82 (169 MB), at about twice the bytes and the cache memory. On the emulator, which has no per-request latency, they are no faster. In Azure Blob Storage, read from a 4-vCPU VM in the account's region (Canada Central, GH-231), they are slower: a cold geography read takes 1.9 s at the default sizing, 2.1 s with the 1 MB block and 2.1 s with `GRID`, and a characteristic 3.9 s, 4.4 s and 4.6 s. `EditionEngine` keeps DuckDB's default sizing. A 400 MB `memory_limit` nearly doubles a cold read there (3.4 s and 7.4 s). A pod sets any of these on `EditionEngine.connection`. A cache that survives a restart would be an extension such as `cache_httpfs` on node-local disk, and is not configured.

**Names stay in `metadata.duckdb`.** The plan to split names, descriptions and annotations into a `labels.duckdb` was shelved after measuring the census edition (GH-139):

| census `metadata.duckdb` | size |
|---|---|
| as published before GH-165 | 1,099 MB |
| full copy, with every key and index | 1,022 MB |
| data only, no keys or indexes, without labels | 294 MB |
| names, descriptions and annotations alone | 31 MB |

Labels are about 3% of the required pull, so a split would save 31 MB at the cost of a store schema bump and a catalog-aware rewrite of every name read. Index structure is about 700 MB of the file. GH-165 measured each index against the read benchmarks: the seal drops `hierarchy_node`'s and `artefact_ref`'s keys and `artefact_ref`'s indexes, which cost no read, and keeps `code`'s key and `annotation`'s indexes, each of which halves the speed of a small hierarchy's eager read. The census `metadata.duckdb` goes from 1,022 MB to 501 MB. Every format, labelled or not, reads names from `metadata.duckdb`.

**Seal cost** (GH-248; `bench/seal/`, sdmxlib 0.79.0, DuckDB 2.0.0, Apple M5, medians). `build(previous=)` copies the previous edition's store and restores the keys the sealed store leaves out; the seal fingerprints, settles the lake, counts and surveys the bindings, and compacts the store into a fresh file. Thresholds were set before measuring: the census key restore at most 60 s, since both alternatives (strip keys only in the sealed copy, or carry an unstripped copy forward) remove only that step; and a CODR table's whole seal of a one-month update at most 10 s, so 7,000 tables reseal in about 5 h on 4 workers.

| phase | census correction edition | CODR 14100287, one month appended |
|---|---|---|
| copy of the previous store | 0.25 s (an APFS clone; 499 MiB on a filesystem without clones) | 0.00 s |
| key restore | **6.34 s** (store 499 → 1,231 MiB) | 0.01 s |
| artefact fingerprints | 66.48 s | 0.03 s |
| binding counts (table fingerprint) | 217.36 s | 0.64 s |
| binding survey | 6.40 s | 0.09 s |
| compaction | 14.92 s (1,796 MiB written; 1,231 → 499 MiB) | 0.05 s |
| **seal** | 314.31 s | **1.60 s** |

Both are under their thresholds, so the seal keeps its design. 69% of the census seal is the table fingerprint over 1.14 billion rows, which every design recomputes, including for an edition that changes no observation.

Derived tables were to be materialised at publish into `data_path` as the remote tier: label lookups, availability, pre-pivoted slices. GH-184 measured each on the census edition, and one is stored: the flow-wide availability, which scanned every row on every request (2.6 s, warm or cold). It is the SDMX 3.1 `AvailabilityConstraint`, which the IM makes no maintainable artefact, stored in `metadata.duckdb` (required tier) at seal, 13.9 MB on census, read and serialised in 0.25 s (GH-194). Availability narrowed by a key (30 ms), label maps (160 ms once per process for 580k codes) and slices (0.24 s) are computed when asked; §11.2's pre-materialised results serve the release-day set. Runtime caches (external file cache, `cache_httpfs`, parsed DSD objects) are node-local and unlisted.

### 7.2 Shared engine

A pod runs one DuckDB engine and attaches every edition it serves into it, so one buffer pool and one thread pool are shared and `memory_limit` means one thing.

- **Per-cursor `search_path`.** Each edition attaches its DuckDB files under per-edition aliases (`<product_id>__<edition_id>__metadata` and `__tables`, where each character an alias cannot hold becomes `_` in the `product_id` and is dropped from the `edition_id`). A request's cursor sets `search_path` to the edition's metadata catalog, so the existing `sdmx.<table>` spellings resolve as `<schema>.<table>` inside it. `EditionRegistry.catalog_name` returns the attached metadata catalog's name (GH-257), so a caller with its own schema in the store (statcatlib's `statcat`) runs `USE "<catalog>".statcat` on its cursors and keeps its SQL unqualified.
- **Role-to-alias map.** `DataflowBinding.database_role` names a role, which it keeps; the registry maps the role to the physical alias at attach time, so the stored binding stays `"lake"` and the SQL uses the per-edition alias.
- **Caches keyed by edition** (resolve cache, label maps, parsed DSDs).

Built in GH-133 as `sl.EditionEngine` and `LocalRegistry.edition(path, engine=)`. Each handle runs `USE "<prefix>__metadata".sdmx`, which sets its current database as well as its search path, so the checks that filter on `current_database()` read the edition too; a cursor derived from a handle does not inherit it, so the registry points every handle it makes. An edition opened without an engine gets one of its own, so there is one opening path. Each registry is over one edition, so its caches are already per edition. GH-160 measured sharing them across editions: a registry's resolve cache retains about 2.2 MB, so 16 editions holding the same structures duplicate about 35 MB, and the shared cache is not built. GH-160 stays open to re-measure once a deployment holds many distinct editions.

### 7.3 Shard router

```python
@dataclass(frozen=True, slots=True)
class Shard:
    product_id: str
    edition_id: str
    location: str
    data_path: str
    manifest_sha256: str
    schema_version: str
    pinned: bool

class ShardRouter:
    def __init__(self, inventory: LiveInventory, *, pool: str, schema_version: str, max_attached: int = 200): ...
    def shard_for(self, flow: SdmxUrn[Dataflow]) -> Shard:        # KeyError → 404 upstream
    def shard_for_artefact(self, urn: SdmxUrn[object]) -> Shard:  # structure products; resolves latest/~
    def registry(self, shard: Shard) -> EditionRegistry:          # pull required tier, verify, attach, set search_path; LRU evict + DETACH
    def reload(self, inventory: LiveInventory) -> None
```

Rules: the map holds `live` entries for this pool at this schema; a dataflow routed only to another schema answers 503 naming both; a pod conditional-GETs `CURRENT`, probes forward, fetches the root and only the leaves whose hash changed, verifies a new version and reloads, with in-flight requests finishing on the old registry object; the manifest's SHA-256 is verified against the leaf entry before anything else is read; the LRU budget counts attached catalogs and evicted files stay on node-local disk. A pod fails static on a read error and exposes the Inventory's staleness against the expected release instant as a metric.

**A pod reads only the live view.** It constructs `LiveInventory`, which holds no embargoed entry, so it neither attaches nor prefetches one. An attached catalog is reachable by its alias from any cursor in the engine, so an embargoed edition MUST NOT be attached, which is also why `EditionRegistry` has no `sql()` (§4.3). The day's pinned hot products are promoted to node-local disk before 08:30 by a job outside the pods.

### 7.4 Cache keys

| cache | key |
|---|---|
| per-pod structural cache | `(edition_id, urn)` |
| result store | `sha256(canonical_form, structural fingerprint of the dataflow, table fingerprint of the binding, applied-overlay digests, hook ids, render version, canonical query, format, lang)` |
| CDN | the public URL; the result-store key as ETag; `Cache-Control: public, max-age=60, stale-while-revalidate=30`, revalidated by `If-None-Match` |

**Render version.** The bytes depend on the code that writes them. sdmxlib carries a `RENDER_VERSION` per data format, bumped by the rule that any change to a writer's golden files bumps it; sdmxlib-rest carries its own for the envelope it adds. Both enter the key, so a writer fix produces new keys and old entries age out. Because the key names the serving code, pre-materialised results are produced by the serving code (§11.2 step 3), never by the publish job. Built in GH-132 as `sdmxlib.formats.render.RENDER_VERSION`, one version per written format, structures and metadata included, each the first 16 hex of its golden file's sha256 (`tests/golden/render/`), so the bump rule is enforced by a test rather than remembered.

**Delivery.** The CDN's origin is sdmxlib-rest. On a hit in `results/`, the pod streams the body from blob storage to the CDN under the public URL; the client never sees a storage URL, there is no cross-origin redirect, no CORS on the storage account, and no SAS lifetime to cache.

**Freshness.** An SDMX REST URL names a query, and the same URL answers differently after a release, a delta or a rollback, so the CDN MUST NOT cache it as immutable. Every response under a public URL carries `Cache-Control: public, max-age=60, stale-while-revalidate=30` and the result-store key as its ETag. When the TTL expires the CDN revalidates with `If-None-Match`; the pod computes the key from the current Inventory version and answers `304 Not Modified` when it matches, without reading `results/`, so an unchanged result costs the origin one key computation and no bytes. A release, delta or rollback reaches the CDN within `max-age` plus `stale-while-revalidate` after the Inventory version that makes it, 90 s at these values, and never before it. A 404 or 503 carries the same two directives and no ETag, so a dataflow first released at 08:30 is visible at the CDN by 08:31:30. Both values are deployment settings; the release-visibility goal is measured against these. Every rollback also purges the product from the CDN.

Keys are per binding, so a delta to one dataflow changes that dataflow's keys only: its next revalidation fetches new bytes, and every other dataflow's revalidation answers 304. The `edition_id` identifies the edition and is never a cache key.

### 7.5 Pools and replication

Partitioning places caches; replication is capacity. Pools: `hot` (the release of the day and perennial heavy products; pinned; pre-scaled), `base` (the long tail, lazy), `search` (search requests only). Each is a stateless Deployment with its own autoscaler; the ingress routes by pool, then by bounded-load consistent hashing on `product_id` (Envoy ring hash or Maglev with `hash_balance_factor`), deriving `product_id` from the parsed dataflow through the same Inventory the pods read.

| probe or hook | condition |
|---|---|
| startup | current Inventory version fetched; timeout covers pinned required-tier pulls |
| readiness | every pinned edition verified and attached; Inventory version current; search pool: declared language tables reachable |
| liveness | the process answers |
| preStop | stop accepting; finish in-flight; DETACH |

A pod is ready for its pool's whole job or out of rotation. Autoscaling the `hot` pool uses KEDA with a cron scaler ahead of known releases, a Prometheus scaler on origin requests per second per pod, and CPU as the floor. Capacity above the pods, in order: result store and CDN; pre-materialisation at publish; node-local block cache on a `hostPath` with topology spread; single-flight per pod and request coalescing at the ingress. Rejected: StatefulSets, per-pod persistent volumes, any pod that is the only holder of an edition. Resource shape: `memory_limit` about 70 % of the container, requests equal to limits; a four-core pod answers on the order of a hundred origin requests per second.

**Throughput at 08:30.** A standard GPv2 account targets about 20,000 requests a second. 300 released tables × 20 hot pods × about 1,000 cold remote-tier GETs is about 6 million GETs, so the day's pinned hot products are promoted to node-local disk before 08:30, and throttling is load-tested in the deployment's region.

### 7.6 Serving topology rule

The catalog is a network of addressable, immutable objects routed by key. Serving is a tree. SDMX REST requests are single-product by construction: getdata names one dataflow; a structure request resolves to one product (the structure product for a full artefact, the data product for its vendored closure). sdmxlib-rest has no coordinator and no leaf-to-leaf calls. Cross-product composition (the agent service comparing vintages, a holdings-wide listing) belongs to the service that needs it, which calls sdmxlib-rest once per product and merges. The actor model (one instance per key) is kept as the description of the catalog and of the publish side, where one writer per product is what the workflow engine's per-workflow serialisation provides, and is rejected for serving because it caps per-key throughput where release-day traffic lands.

---

## 8. Shared structures

Each data product vendors its structural closure, and one **structure product** per agency vendors every full codelist, concept scheme, category scheme and hierarchy. Decisions:

- **Partial codelists in data products.** A data product MUST vendor a codelist as the partial artefact (`isPartial="true"`, `partial: true`) holding the codes the product uses **plus their ancestors**, closed under parent. For a CODR table this is the member list CODR stores per table. `build_edition(partials=True)` applies the rule (GH-223, `edition/_build.py`). It cuts `Codelist`s only; concept and category schemes stay full. A partial keeps the codes the observations hold in the columns the codelist enumerates, the codes the dataflows' allowed constraints name (a cascading selection adds the descendants it selects), and the codes another kept artefact references, each with its ancestors (`Codelist.partial`). A codelist stays full when a Hierarchy in the edition places its codes (SDMX has no partial hierarchy, so the hierarchy stays whole and its codes with it), when it has extensions, when a component it enumerates has no column in the written observations, when a dataflow it serves has no observations, and when the cut would keep every code. `fetch_edition` builds with `partials=False`.
- **Overlays on a partial.** `fold_overlays` carries each partial's `partial_of` into the folded edition's manifest (GH-272), so §6.5 items 4 and 8 and `check_edition(structures=)` still compare a folded product's partials with the full artefacts.
- **Structure products** (`kind: structure`) hold the full artefacts, have no bindings, live in the `base` pool unpinned (so they never gate readiness and attach lazily on the first full-artefact request), and answer `/structure` requests for full artefacts. They are the source the catalog-level search index (§12) is built from and the reference the release job checks partials against (§6.5 item 4). An agency MAY split them by change rate so one edit does not re-ship everything: for example `structure-geo` (the full geography, large and slow-moving), `structure-classifications` (NAICS, NOC, SGC), `structure-concepts` (concept schemes, small and frequently edited). Invariant 7 requires one home per full artefact, however the split is drawn.
- **Publish order.** In a release, structure editions are `live` before any data edition whose partials were cut from them (§6.5 item 8). Each partial records the fingerprint of the full artefact it was cut from (`partial_of`, §4.2), which is how the release job knows the dependency without reading codes.
- **First structure request.** Unpinned structure products attach on the first full-artefact request, so the first `/structure` request for a large one (the full geography) waits for its required-tier pull, labels included. Pools that serve structure endpoints MAY pin the large structure products to move that pull before readiness.
- **Structures from FMR.** When FMR is authoritative, `metadata.duckdb` is a per-edition cache of an FMR closure plus the statcat records, and the leaf entry records its `structure_source`. FMR edits `1.0` artefacts in place, so a version number is not an identity; the release rules compare fingerprints.
- Data products and structure products are sealed, verified and served by the same mechanism; the leaf marks the kind.

Layered editions (`requires`) remain the recorded alternative and stay rejected.

---

## 9. Schema versions and fleet rollout

### 9.1 Versions and checks

| version | recorded | rule | checked by |
|---|---|---|---|
| sdmxlib `schema_version` | manifest, leaf entry | exact | sdmxlib reader |
| DuckDB storage version | manifest | refuse newer | sdmxlib reader |
| DuckLake spec version | manifest | refuse newer | sdmxlib reader |
| `canonical_form` | manifest | known values | release job, cache keys |
| Inventory `format_version` | root, leaf | refuse a newer major | sdmxlib Inventory reader |
| statcat `schema_version` | `statcat._meta` | exact | statcatlib `Catalog.open()` |
| statcat `index_version` | manifest `extensions.statcat` | known values | statcatlib `Catalog.open()` |

### 9.2 Rollout procedure for a schema bump

1. Pin sdmxlib, statcatlib, DuckDB and DuckLake in one image tag for the publish job and the pods.
2. Republish every live product at the new schema into new editions sharing `data/`, with `previous` and fingerprints; `check_edition(at="publish", expect=<previous edition>)` (`sdmxlib edition check --expect`) stops the job if any fingerprint moved, naming the binding or artefact (GH-183).
3. Release them: each product then has one live edition per schema version, since the head is per (product, `schema_version`).
4. Roll pods; old pods read the old entries, new pods the new ones; the ingress routes by `product_id`.
5. Once the rollback window for the pod image closes, supersede the old-schema entries; they follow §11.6.

Result-store keys survive the bump wherever `canonical_form`, fingerprints and render versions are unchanged. A `canonical_form` bump is a one-time cold cache by design. A seal throughput target is set before Phase 5: at the measured 1.6 s per CODR seal (§7.1), a full republish of 7,000 tables fits one non-release window on 4 workers.

---

## 10. Blob storage

### 10.1 Hand-off and access

Blob storage is the only channel between the jobs and the pods. Principals:

| principal | `products/`, `pending/` | `inventory/`, `CURRENT` | `results/` |
|---|---|---|---|
| publish job | write | none | write (pre-materialised results) |
| release job | read | write | none |
| GC job | delete | read | delete |
| pods, search pods | read | read | read, write |
| preview deployment (`EmbargoReader`) | read | read | none |
| the public, the CDN | none | none | none directly |

**The boundary is the account or container.** On ADLS Gen2, RBAC is evaluated before POSIX ACLs, so any principal with Storage Blob Data Reader on the container reads embargoed bytes, and data engineers and analytics workspaces usually hold that role. So the catalog lives in a container (or account) that only the jobs and the pods hold a role on; `results/` goes on a separate account; and every release checks, as a precondition at the 08:00 pre-flight against a reviewed allowlist, which principals hold Data Reader on the catalog's container, counting nested groups, eligible PIM assignments and inherited subscription roles. "Only the release job writes `inventory/`" needs separate write paths per identity, by ACL-only roles or separate containers. The jobs, the pods and the preview deployment use workload identities, with no keys and no SAS tokens.

The public reaches a result through sdmxlib-rest, which streams `results/<key>` to the CDN (§7.4, Delivery). A pod reads a key only after computing it from a `live` entry, so an embargoed key, though present in `results/`, is unreachable until its release. Nothing serves `products/` or `results/` to the public directly.

A copy of a catalog is outside the embargo boundary. `CatalogAdmin.export(live_only=True)` makes a safe copy, and a live-only replica regenerated from `CURRENT` is the only touchpoint for analytics and governance tools that need their own read identity (statcatlib RFC decision 30).

### 10.2 Namespace

DuckLake's reads are byte-range GETs of files whose names it holds; it never lists a directory, so flat and hierarchical namespaces serve the hot path identically, and the Inventory reader probes names rather than listing. Hierarchical namespace (ADLS Gen2 with HNS) helps the publish side: directory delete for GC and POSIX ACLs for the table above. Exclusive creation goes through the Blob API (§6.6), never DFS create plus append. HNS forgoes blob versioning, point-in-time restore and object replication; the design replaces most of what versioning gave (immutable editions, the Inventory chain, recovery by rollback), and account-level replication (RA-GZRS or a scheduled copy) covers a deleted edition directory.

### 10.3 Backup invariant

An Inventory version is restorable only while every data file its editions reference exists. Backups copy in dependency order: `data/`, edition directories, leaves, roots, then `CURRENT`; `pending/` is excluded. `verify-catalog` walks the newest root and checks every leaf, every `manifest_sha256` and every listed file's digest; its exit status is the monthly restore test. After a whole-catalog restore or failover, `catalog.json`'s `restore_epoch` is bumped (§6.1), and a restored Inventory version is never made current: its contents come back through `reinstate`.

---

## 11. Seal, release and retirement

### 11.1 Seal sequence (per product)

1. `LocalRegistry.build(product_dir, previous=<live edition>)` copies the previous edition's `metadata.duckdb` and `tables.ducklake` and restores the keys the sealed store leaves out; it refuses when the product already has an unsealed edition, naming it and pointing at `resume=` and `discard()` (GH-253). Files outside the lake that the previous manifest lists at tier `lazy`, such as `search/`, are copied too, or a set the caller names (GH-258).
2. Write new data files into `data/` under fresh UUIDs; for a `revisable` product rewrite revised files copy-on-write; ingest structures (partials for data products, full for the structure product) and catalog records into `metadata.duckdb`.
3. `build_index()`; drop orphaned search tables.
4. `seal(edition_dir, product_id=..., ...)`: write the journal entry (§6.7); checkpoint every DuckDB file; flush inlined data; optionally expire snapshots; compact if `final`; fingerprint; compact the store; write `manifest.json` last with `previous`, fingerprints, `canonical_form`, and carry-forward with `carried_from`. No cleanup against `data/`.
5. `check_edition(at="publish")`.
6. Record: on a laptop with embargo off, statcatlib writes the Inventory version that makes the edition `live`, then removes the journal entry. On a server, upload (§11.2).

### 11.2 Upload and release

1. **Upload never writes `CURRENT` or `inventory/`.** It copies data files with a digest the store keeps (`Content-MD5` on Azure, a full-object CRC32C on S3), then the edition directory with its manifest last, and verifies every file the manifest lists. Then it writes `pending/<product_id>/<edition_id>.json` with the `edition_id` and `release_at`, put-if-absent.
2. **`pending/` is an inbox, not an authority.** The release job folds pending registrations into an Inventory version as `embargoed` entries, then deletes them; from then on the Inventory is the only authority. Two pending editions of one product built on the same live edition are both refused and reported. A pending entry whose `edition_id` (a UUIDv7, so it carries a time) is older than the tombstone window is refused, so a `pending/` file restored from an old backup cannot resurrect withdrawn data.
3. **Pre-materialisation.** A job running the **pods' image**, so render versions match, opens each embargoed edition from its own files through `EmbargoReader`, computes the release set (full-table downloads per language, the slices the front end requests on load) and writes it to `results/` under the new keys. It runs in a job, never in a serving pod.
4. **Release.** At release time the release job writes one Inventory version moving every due `embargoed` entry to `live` and the replaced ones to `superseded`, structure editions first by invariant 8, with `event.kind: release`. Only the release job writes Inventory versions on the server, once per batch, under `concurrencyPolicy: Forbid`. A batched seal on a laptop likewise records many products in one version.

A release interrupted before step 4 leaves every entry `embargoed`: nothing new is visible and nothing old is lost, and the release resumes at step 3 or 4.

### 11.3 States

| state | routable | pods may prefetch | directory may be deleted |
|---|---|---|---|
| `embargoed` | no | no | no |
| `live` | yes | yes | no |
| `superseded` | no | no | no, until it is retired |
| `withdrawn` | no | no | no, until it is retired; `expunge` deletes at once |
| tombstone | no | no | after the backup retention window |

**Expunge.** Withdrawing a confidential release needs more than `withdrawn`: `expunge` purges the CDN, the `results/` keys and pod disks, deletes the edition's bytes at once with an audit entry, and records which backups need scrubbing.

### 11.4 Garbage collection

The GC job reads the Inventory chain back through the retention window and deletes: edition directories retired longer than the window; files under `products/<product_id>/data/` listed in no manifest of any edition recorded or tombstoned within the window; `results/` keys reachable from no live entry's fingerprints, after a grace period. GC reads manifests only, never an edition's DuckDB files. It is the only principal that deletes under `products/`, and it refuses to start if the retention inequalities of §11.6 fail. It has a dry run and a deletion log, and alerts on a deviation from the dry run's forecast.

### 11.5 Copy rule

`sdmxlib edition copy SRC DST [--tiers required,lazy,remote]` reads the manifest, copies the listed files of the requested tiers (data files by `root: data` entries), verifies, then writes the manifest last. It copies between filesystems: a mirror, a mount, another agency's disk.

sdmxlib holds no blob client, so a pod pulls from object storage with the deployment's own tool. `sdmxlib edition files EDITION --tiers required,lazy` lists the files those tiers need for `rclone copy --files-from` or azcopy, and `--manifest` the manifest alone, copied in a second step since those tools copy in parallel (GH-182); `check_on_open` verifies the result. The Inventory reader takes a `fetch(path) -> bytes | None` callable for the same reason: the caller supplies `obstore`, `fsspec` or a local read.

### 11.6 Retention and alerts

One configuration states the retention windows and checks their order:

- backup retention ≤ the `data/` GC window ≤ the tombstone window (§10.3);
- the longest pod poll interval plus fetch time < Inventory version retention;
- objects reachable from the newest K roots are never deleted.

Old-schema live entries stay until the rollback window for the pod image closes (§9.2 step 5).

Alerts, each with a runbook: embargoed editions past `release_at` (with `release_due`'s persisted refusal reasons and a manual release command that takes a reason); sealed-but-unrecorded editions on a server (an owner, and a discard-or-reinstate decision); `CURRENT` behind the newest root (`advance-current`); GC deletions off the dry-run forecast; storage 503s; the release job missed or failed; a root that fails validation; pod staleness over a threshold; old `pending/` entries; a failed access precondition; backup age; replication lag; stale unsealed editions on build nodes.

---

## 12. Search across shards

Each data product's editions carry per-language LanceDB tables over its own datasets, codes, concepts and reference metadata, served by the search pool from lazy-tier files. BM25 tables are rebuilt at each seal; embeddings are carried forward by `build(previous=)` and reused by a hash of the embedded text, so unchanged text is never re-embedded. Holdings-wide search is a catalog-level index built from the union of live `statcat.dataset` rows and the full codelists in the structure products, stored under `search/<seq>/`, recorded in the Inventory root's `extensions.statcat`, and served by the search pool. Fan-out across edition tables is rejected (BM25 statistics do not compose across tables). Open: whether geography-first queries from the agent need the catalog-level code index to cover partials.

---

## 13. Traps

1. In-place republish deletes Parquet under a reader → edition directories; shared immutable `data/`; manifest last; superseded editions kept through retention. §4.1, §11.
2. Snapshot cleanup on a shared `data/` deletes files earlier editions reference → producers never run cleanup; GC owns deletion over the union of manifests. §4.1, §11.4.
3. Republish cost scales with product size → shared `data/`; copy-and-extend; carry-forward. §4.1, §4.5.
4. A false-equal fingerprint under an immutable cache serves wrong observations forever → row-level MD5 folded by modular sum with count; model-based artefact fingerprint; `canonical_form`. §4.4.
5. A writer fix never reaches a cached query → render version in the key. §7.4.
6. Engine-hash fingerprints change on a DuckDB upgrade → MD5. §4.4.
7. Manifest has no integrity of its own → `manifest_sha256` in the leaf entry; reserved `.sig`. §6.3, §4.3.
8. Engine versions unrecorded → required fields; refuse newer storage and newer DuckLake spec. §4.2, §9.1.
9. Schema bump forces a lockstep fleet rebuild → one live edition per (product, `schema_version`); fingerprint-verified rebuilds. §9.2.
10. One delta invalidates every dataflow in a product → binding-level keys. §7.4.
11. Overlay `scope` and per-type merge hooks change bytes without changing the token → applied digest; per-type hook ids; `null` scope defined. §5.
12. Serve-time overlays desynchronise search → refuse; fold then rebuild. §5.3.
13. Shared codelists drift between products → pinned URNs; fingerprint agreement; structure product as reference. §4.5, §6.5, §8.
14. Partial codelists missing ancestors fail sdmxlib's parent check → closed under parents. §8.
15. Full codelists vendored per table at CODR scale → partials; structure products. §8.
16. `references` costs 30 s and 5 GB → anti-joins generated from the edge table. §4.8.
17. Every pod re-hashes gigabytes → receive stamp; remote tier by size and a digest the store keeps. §4.6, §4.7.
18. Object storage keeps no SHA-256 of a multipart upload → `md5` and `crc32c` in each file's `digests`; the publish job sets one the store keeps. §4.2, §4.5.
19. A multi-gigabyte pull set makes a scaled pod unavailable for minutes → tiers; search pool; the sealed store trimmed of unused keys (the labels split is shelved). §4.1, §7.1.
20. Labelled formats need labels on the first request → names stay in `metadata.duckdb`, a required-tier file. §7.1.
21. A 2.x reader opens a 3.0 edition, or the reverse → the major-version rule refuses it; pinned image sets. §4, §9.
22. Copy order undefined → `edition copy`; manifest last. §11.5.
23. Inlined data, old snapshots, `.wal` files ship → checkpoint, flush, expire. §4.5.
24. Delete files on the serving path → copy-on-write at seal. §4.5.
25. One edition per engine in sdmxlib → per-cursor `search_path`; role-to-alias map; edition-keyed caches. §7.2.
26. A read path that writes to the lake → read-only constructor. §4.3.
27. Old statcatlib returns zero hits silently; `statcat._meta` unchecked → version checks on open. §9.1.
28. Search `generation` collides across builders → edition-named tables. §4.1.
29. One product with two live editions at one schema → invariant 1 and the fast-forward check. §6.4, §6.5.
30. An Inventory built by scanning storage resurrects a withdrawn edition → the narrow repair; `rebuild-inventory` refuses after a rollback. §6.7.
31. One process per edition → read-only open; checkpoint; `temp_directory` outside the edition. §4.3, §4.5.
32. Legacy versions are never final → edition immutability; `lifecycle`; required fingerprints. §4.5, §6.3.
33. Embargoed results readable before release → no public access to `results/`; a pod reads a key only after computing it from a live entry, and streams it to the CDN. §7.4, §10.1.
34. A Postgres DuckLake catalog on the serving path → file catalogs per edition; Postgres optional upstream. §14.
35. The actor model for serving → replicated leaves in pools; no coordinator. §7.6.
36. Partial readiness within a pod → pools per workload. §7.5.
37. Deleted edition directory with HNS → account-level replication with retention longer than Inventory retention. §10.
38. `token` reads as an auth token in server code → `edition_id`. §18.
39. Raw `sql()` in a shared engine reads any attached edition by alias, embargoed ones included → no `sql()` on `EditionRegistry`; pods read `LiveInventory` and never attach an embargoed edition. §4.3, §7.3.
40. An outgoing-only closure misses constraints and hierarchy associations, which reference the dataflow → closure includes incoming references, and the producer fetches them (GH-221). §4.4.
41. A small delta stays inlined, and the flush produces a delete file → explicit rewrite after flush. §4.5.
42. The publish job pre-materialising results must know the pods' render version → pre-materialisation runs the pods' image as a release step. §7.4, §11.2.
43. A cached 302 to a SAS URL expires under the client; cross-origin redirects need CORS → sdmxlib-rest streams results to the CDN. §7.4.
44. One structure product per agency re-ships the full geography on every concept edit → split by change rate; unpinned; publish order invariant. §8, §6.5.
45. msgspec cannot encode the attrs model → canonical model encoder in M4. §4.4.
46. `Cache-Control: immutable` on an SDMX REST URL keeps pre-release bytes at the CDN after the release, a delta or a rollback → short TTL, the result-store key as ETag, `304` on revalidation. §7.4.
47. A release job that checks partials needs their codes, and the census geography partial has 579k → each partial records the fingerprint it was cut from (`partial_of`); the subset is checked at seal. §4.2, §6.5.
48. Exclusive create that names the file before its content leaves an empty head after a crash → complete-before-visible: link or no-replace rename locally, single-shot `Put Blob` with `If-None-Match: *` remotely. §6.6.
49. A stale automatic release lands after embargo was turned on → retries re-evaluate every precondition, the `embargo` header included. §6.6.
50. A whole-catalog restore leaves `CURRENT` below the seq every pod holds → `restore_epoch` resets readers' floor. §6.1, §6.6.
51. A restored `pending/` file republishes withdrawn data → `pending/` excluded from backups; old edition ids refused; tombstones outlive backups. §11.2, §11.6.
52. Any principal with Data Reader on the container reads embargoed bytes → the account or container is the boundary; the reader-principal precondition. §10.1.

---

## 14. Catalog authoring and the Postgres option

The publish job authors each edition's lake catalog directly as a DuckDB file by copy-and-extend (§11.1). The design depends on nothing else.

A central Postgres DuckLake catalog is an option for governance and analytics across products (concurrent writers, point-in-time recovery, one place that knows everything). Adopting it requires exporting one edition's slice into a file catalog, which means copying spec tables whose snapshot, schema and file ids are global across products. Milestone M1 is the spike that decides it; if the export does not yield a valid per-edition catalog, Postgres stays a reporting copy and authoring stays as above. Postgres on the serving path is rejected: a shared network hop in every query's planning, a shared dependency for every pod, and the loss of the verifiable per-edition catalog. A governance mirror (Postgres DuckLake or Unity) MAY be fed from the Inventory chain for audit queries; it is never authoritative.

---

## 15. Prior art

| system | what it shares | what to borrow |
|---|---|---|
| Search serving (Google, LinkedIn Galene, Pinterest Manas, Airbnb Nebula) | immutable index segments shipped to replica sets | pools; coordinator-to-leaf tree |
| Elasticsearch time-based indices with ILM | one index per release; read-only; force-merged | the `final` lifecycle with compaction |
| PMTiles, Cloud-Optimised GeoTIFF | immutable file per release by HTTP range | the remote tier |
| Netlify-style atomic deploys | immutable build directories; pointer flip | edition directories; one Inventory version per release |
| Iceberg / Delta / DuckLake | snapshot identity; shared immutable data files; `_delta_log` with `_last_checkpoint` | shared `data/`; the Inventory chain and `CURRENT`; the per-file checksum this format adds |
| BagIt (RFC 8493) | payload manifest of checksums; tag manifest | manifest integrity |
| Debian repositories | `Release` over `Packages`, hashed and signed | Inventory root over leaves |
| Python wheels | `RECORD` + `WHEEL` | vendoring at scale |
| OCI distribution | manifest of blobs by digest; tag index | fingerprints; content-addressed leaves |
| Frictionless Data Package, RO-Crate | resource hashes; provenance | the statistical framing |
| Hive metastore federation, Azure SQL shard map | catalogs behind one routing layer; cached key map | route in the application |
| SQLite-per-tenant (Turso, LiteFS), Datasette | embedded database files routed by key | format version in the routing key |
| Durable Objects, Orleans, Akka, Ray actors | one instance per key | the catalog's object model; the write side; rejected for serving |
| Iceberg REST catalogs (Polaris, Nessie) | catalog service for writers; pinned metadata files for readers | Postgres upstream, files for serving |
| .Stat Suite point-in-time release | embargoed data in the dissemination database behind a header | derived state regenerated from `CURRENT`, never patched; embargo off by default for every reader; UTC release instants |

---

## 16. Prototype plan

Order follows risk removed per unit of work. Synthetic editions and `profile_edition` shapes supply the corpus; a synthetic CODR-shaped catalog (7,000 small products, Zipf traffic) arrives at M8.

**M0. sdmxlib: read-only open.** `LocalRegistry.build(path)` for writers; `LocalRegistry.edition(path)` requires a manifest and returns `EditionRegistry` with no `sql()`; `access_mode=READ_ONLY`; `ATTACH (READ_ONLY)`; `temp_directory` outside the edition; `CHECKPOINT` in the seal; `.wal` refused by the files check. Built in GH-122.

**M1. Spikes.** (a) Per-cursor `search_path` across two attached editions: **passed**. (b) Copy-and-extend over shared `data/` with snapshots expired and no cleanup: **passed**; the DuckLake layout is `<schema>/<table>/ducklake-<uuid>.parquet` and a small delta stays inlined until flushed. (c) Postgres DuckLake export of one edition's slice to a file catalog: open; decides §14. (d) Delete-file rewrite: **passed**; `ducklake_rewrite_data_files` with `delete_threshold => 0` leaves no delete file, while the default threshold keeps small deletes as delete files.

**M2. sdmxlib: manifest 2.0 and layout.** Edition directories; shared `data/`; `root`, `tier`, `md5`, `carried_from`; version fields; `data_path`; `artefacts` with `stability` and `partial`; `BindingCount` fields; `StoreFormatError` (newer-only for storage); copy-on-write rewrite after flush; `sdmxlib edition copy --tiers`. Built in GH-123, GH-124, GH-125 and GH-128; GH-165 trims the sealed `metadata.duckdb` to 501 MB on the census edition.

**M3. sdmxlib: labels split with schema bump.** Shelved after measuring (GH-139, §7.1); GH-165 measures and trims the indexes in its place.

**M4. sdmxlib: fingerprints and canonical form.** The canonical model encoder; the row-text golden row per column type; table, artefact, structural (incoming edges included) and product fingerprints; `canonical_form`; `RENDER_VERSION` per format with the golden-file bump rule. Built in GH-126, GH-127 and GH-132.

**M5. sdmxlib: effective token and check profiles.** Applied-overlay digest; per-type hook ids; `null` scope; `check_edition(at=...)`; anti-joins generated from `_edges.py`; receive stamp; remote-tier verification by size and `Content-MD5`. Built in GH-129, GH-130 and GH-131; `references` takes 0.15 s on the census edition.

**M6. statcatlib: partials, structure products, local fixes.** Partial vendoring closed under parents, which sdmxlib's `build_edition` does (GH-223, §8), for statcatlib's ingest to call; the structure kind; `statcat._meta` check; refuse unknown `index_version`; edition-named search tables. Test: a CODR-shaped product closure under 10 MB; a partial lacking an ancestor is refused at build.

**M7. sdmxlib: shared engine.** Per-cursor `search_path`; role-to-alias map; edition-keyed caches. Built in GH-133, with the 200-edition test in `tests/unit/test_edition_engine.py`; the edition-keyed caches stay per registry (§7.2, GH-160).

**M8. The Inventory.** sdmxlib: the schemas, validation, transition rules, `LiveInventory`, `FullInventory` and the reader over `fetch` (GH-251), and a conformance suite run against statcatlib's writer and a Delta-backed prototype (GH-252). statcatlib: the writer, the narrow repair, `CatalogAdmin`. Test: each invariant and refusal by name; a one-product change writes one leaf and one root; a reader holding seq 5 never accepts 4 and finds 6 by probing; two concurrent seals on different products both land; a writer killed between create and the last byte leaves the previous head readable and writable.

**M9. statcatlib: upload, release, GC.** `pending/` and the release job; `Content-MD5`; the pre-materialisation job on the pods' image; structures first; GC over the Inventory chain with the retention check. Test: a 200-table synthetic release becomes visible in one Inventory version with zero failed requests; GC deletes exactly the unreferenced files and nothing inside the retention window; a request for an embargoed dataflow returns 404 from every pod; a data edition whose partial gained a code is refused `live` until its structure edition is live.

**M10. sdmxlib-rest: router and pools.** Builds on sdmxlib's remote open (GH-180), store-neutral verification (GH-181) and tiered file lists (GH-182). `CURRENT` polling with probing; per-pool and per-schema visibility; LRU attach; tiered pull with pool promotion; `pinned` readiness; binding-level keys with render version; result streaming to the CDN. Test: cold pod with one pinned product ready under 60 s; p99 first attach for an unpinned product under 3 s; an Inventory version under load with no failed requests; a delta to one dataflow leaves other dataflows' results warm.

**M11. Kubernetes manifests.** Deployments for `hot`, `base`, `search`; KEDA; probes; preStop; PDB; topology spread; `hostPath` block cache; Envoy Gateway route with bounded-load hashing; identities per §10.1. Test: scaling `hot` from 4 to 20 under a synthetic release-day load remaps no more than 1/N of keys per added pod with zero failed requests.

**M12. Drills.** §9.2 end to end with mixed pods; rollback; restore from the account-level backup after deleting an edition directory, with a `restore_epoch` bump. Test: zero 404s during the bump; hit rate across the bump; every restore verified by `verify-catalog` and `check_edition(at="receive")` on every live entry.

**M13. Catalog-level search.** Built from the structure products and the live `statcat.dataset` union; served by the search pool.

---

## 17. Open questions

1. Name overlays (§5.4): settled in GH-134. Name and description operations, applied only by `fold_overlays`; built in GH-163.
2. Whether `token` becomes `publication`: settled by this revision. The token is the `edition_id` (§18).
3. Detached signature format (minisign vs sigstore) when cross-agency sharing becomes concrete.
4. Code search scope for the agent (§12).
5. Republishing structures with semantic three-part versions; an FMR governance change decoupled from this work.
6. CODR product granularity: one per table by default, subject-level merging where tables always release together (§4.0).
7. `labels.duckdb` pull time on the census edition: settled by GH-139. Labels are 31 MB of the census `metadata.duckdb` and stay in it (§7.1).
8. How to split the structure products for StatCan (§8): by artefact family, by change rate, or both.

Settled in the v3 review: partials are not listed separately for routing (routing needs full artefacts only); `schema_version` stays exact-match (store bumps change queried columns, and §9.2 absorbs them); `EditionRegistry` has no `sql()`; the row text stays `to_json(t)` with a golden row per column type under `canonical_form`.

---

## 18. The rename and the release

### 18.1 Release and versions

| | before | after |
|---|---|---|
| sdmxlib release | 0.79.0 | 0.80.0, after the pre-release 0.80.0rc1 |
| manifest `format_version` | `"2.1"` | `"3.0"` |
| store `SCHEMA_VERSION` | `"2.51"` | `"2.52"` |
| Inventory `format_version` | none | `"1.0"` |
| `catalog.json` `format_version` | none | `"1.0"` |
| `CANONICAL_FORM` | `"1"` | `"1"`, unchanged |
| effective token string | `v2\n<token>…` | `v3\n<edition_id>…` |

statcatlib's Phase 2a pins 0.80.0 and stays on a branch until sdmxlib-rest has an adoption issue that pins it and reads manifest 3.0 (statcatlib RFC §11).

### 18.2 Rename table

| kind | before | after |
|---|---|---|
| file | `bundle.manifest.json` | `manifest.json` |
| file | `bundle.manifest.json.sig` | `manifest.json.sig` |
| file | `meta.duckdb` | `metadata.duckdb` |
| file | `lake.ducklake` | `tables.ducklake` |
| layout | `bundles/<bundle_id>/<token>/` | `products/<product_id>/<edition_id>/` |
| layout | `catalog/index/`, `catalog/structures/`, `catalog/current` | `inventory/<seq>.json`, `inventory/leaves/`, `CURRENT` |
| manifest key | `bundle_id` | `product_id` |
| manifest key | `token` | `edition_id` |
| manifest key | `previous_token` | `previous` |
| manifest key | `product` | `producer_fields` |
| `ArtefactEntry` key | `cut_from` | `partial_of` |
| `BundleFile` value | `root: "publication"` | `root: "edition"` |
| `BundleFile` value | `tier: 1`, `2`, `3` | `tier: "required"`, `"lazy"`, `"remote"` |
| lifecycle | `live` | `revisable` (`live` is now an Inventory state only) |
| index kind | `structures` | `structure` |
| index grouping | `ProductGroup` | `ProductSeries` |
| index state | `staged`, `serving`, `draining`, `retired` | `embargoed`, `live`, `superseded`, `withdrawn`, and a tombstone |
| store `_meta` key | `publication_token` | `edition_id` |
| store `_meta` key | `previous_token` | `previous` |
| engine alias | `<bundle_id>__<token>__meta`, `__lake` | `<product_id>__<edition_id>__metadata`, `__tables` |
| receive stamp | `<stamps>/<bundle_id>/<token>` | `<stamps>/<product_id>/<edition_id>` |
| module | `sdmxlib.bundle` | `sdmxlib.edition` |
| class | `BundleRegistry` | `EditionRegistry` |
| class | `BundleEngine` | `EditionEngine` |
| class | `BundleFile` | `EditionFile` |
| method | `LocalRegistry.bundle(path)` | `LocalRegistry.edition(path)` |
| property | `LocalRegistry.publication` | `LocalRegistry.edition_dir` |
| method | `LocalRegistry.build(path, previous=, resume=)` | unchanged; `path` is the product directory |
| function | `write_manifest(path, bundle_id=, product=, cut_from=, …)` | `seal(path, product_id=, producer_fields=, partial_of=, …)` |
| function | `check_bundle` | `check_edition` |
| function | `fetch_bundle` | `fetch_edition` |
| function | `build_bundle` | `build_edition` |
| function | `copy_bundle` | `copy_edition` |
| function | `bundle_files` | `edition_files` |
| function | `inventory(root)` | `list_files(root)`, freeing "Inventory" for the routing document |
| function | `sdmxlib.testing.synthetic_bundle` | `sdmxlib.testing.synthetic_edition` |
| function | `sdmxlib.testing.profile_bundle` | `sdmxlib.testing.profile_edition` |
| type | `Lifecycle = Literal["live", "final"]` | `Lifecycle = Literal["revisable", "final"]` |
| type | `Tier = Literal[1, 2, 3]` | `Tier = Literal["required", "lazy", "remote"]` |
| constant | `MANIFEST_NAME = "bundle.manifest.json"` | `MANIFEST_NAME = "manifest.json"` |
| CLI | `sdmxlib bundle check \| fetch \| copy \| files` | `sdmxlib edition check \| fetch \| copy \| files` |
| CLI option | `--tiers 1,2,3` | `--tiers required,lazy,remote` |
| docs page | `docs/bundle.md` | `docs/edition.md` |

Unchanged: `check_on_open`, `ManifestError`, `StoreFormatError`, `Manifest`, `ArtefactEntry`, `BindingCount`, `Producer`, `Provenance`, `DATA_PATH`, the overlay API and `effective_token`.

### 18.3 New in 0.80.0

| | what | issue |
|---|---|---|
| `build()` refuses a second unsealed edition | names the existing one and its creation time, and points at `resume=` and `discard()` | GH-253 |
| `list_editions(product_dir)` | each edition's id, sealed or not, `previous` and creation time, from manifests only | GH-254 |
| `discard(edition_dir)` | deletes an unsealed edition; refuses a sealed one; leaves `data/` to GC | GH-255 |
| `synthetic_edition(…, seal=False)` | stops before the seal and returns the writable registry | GH-256 |
| `EditionRegistry.catalog_name` | the attached metadata catalog's name | GH-257 |
| `build(previous=)` copies lazy-tier files | `search/` and any set the caller names | GH-258 |
| the seal journal | `journal/<edition_id>.json` before the manifest | GH-259 |
| `sdmxlib.inventory` | the root, leaf, `catalog.json` and `CURRENT` schemas, the transition rules, `LiveInventory`, `FullInventory` and the reader | GH-251 |
| the Inventory conformance suite | crash and concurrency tests a writer runs, and a Delta-backed prototype | GH-252 |
