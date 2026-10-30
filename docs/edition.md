# The edition format

A product is a directory holding SDMX artefacts and observations, sealed as
one unit, once per release: each sealed state is an edition, a directory of
its own. statcatlib writes editions, sdmxlib-rest serves them, and sdmxlib
reads, checks, copies and fetches them. This page is the contract between
them. Which edition of a product is live is the Inventory's to say (the
[sharded catalog spec](design/sharded-bundle-catalog.md) §6), not this
page's.

It describes **manifest format 3.0** and **overlay format 1.2**. sdmxlib's
code is the reference implementation (`sdmxlib.edition`,
`LocalRegistry.edition`), and the two JSON Schemas ship with it:
`sdmxlib.edition.manifest_schema()` and `sdmxlib.edition.overlay_schema()`.
`tests/unit/test_edition_spec.py` keeps the field tables below equal to those
schemas.

MUST, MUST NOT and MAY carry their RFC 2119 meanings. A *producer* writes an
edition; a *reader* opens one.

## 1. Layout

```text
<product>/
  data/                    data files shared by every edition of the product
    <schema>/<table>/ducklake-<uuid>.parquet
  <edition_id>/            one edition, named by its edition id; never rewritten once sealed
    metadata.duckdb        the artefact store: the sdmx.* schema, bindings included
    tables.ducklake        the DuckLake catalog for observations, over ../data/
    manifest.json          the manifest, written last
```

- `metadata.duckdb` MUST be present. It holds every artefact in the store schema
  that `sdmxlib.storage.schema` defines, at the version recorded in the
  manifest's `schema_version`.
- `tables.ducklake` holds the catalog for observations, and `data/` their data
  files, in DuckLake's own layout. An edition with no bound dataflow MAY
  leave the catalog out. A sealed catalog holds no rows itself and no
  delete file in its current snapshot: `seal` flushes the rows
  DuckLake inlines into the catalog, and rewrites each table that holds delete
  files (§7).
- `data/` is shared. A data file is written once, under a fresh UUID, and
  every edition whose catalog references it lists it. A producer MUST
  NOT delete, rewrite or clean up a file in `data/`
  (`ducklake_cleanup_old_files` and `ducklake_delete_orphaned_files`
  included): an earlier edition may still list it. Deleting is garbage
  collection's job, which keeps every file some manifest still lists.
- An edition directory is written once. A change is a new edition, whose
  catalog is a copy of the previous one's, extended (§7).
- `manifest.json` marks the edition as sealed. A directory without one is an
  unsealed edition: `LocalRegistry.edition` refuses it, and its builder
  reopens it with `LocalRegistry.build(product, resume=edition_id)`.
- `manifest.json.sig` is reserved for a detached signature of the
  manifest. The manifest never lists it, the `files` check never reports it,
  and `copy_edition` copies it just before the manifest.
- A producer MAY add files of its own to the edition directory, such as
  a search index. The manifest MUST list them like any other file (§2), so
  they MUST be in place before the manifest is written.

A reader MUST resolve every path relative to the edition directory as it
is now, and the data files through the manifest's `data_path`. DuckLake
records the data path it was written with; sdmxlib attaches the lake with
`OVERRIDE_DATA_PATH`, because an edition is moved, copied and unpacked
elsewhere.

## 2. The manifest

`manifest.json` is a UTF-8 JSON object. sdmxlib writes it indented by
two spaces, but a reader MUST NOT depend on the layout.

### `Manifest`

| field | presence | meaning |
| --- | --- | --- |
| `format_version` | required | The manifest format, `major.minor`: `"3.0"` for this page. |
| `product_id` | required | The producer's stable name for the product, kept across editions. It names *which* product, never which edition. |
| `edition_id` | required | A UUIDv7 minted when the edition's build started, and the name of its directory (§3). |
| `previous` | required | The `edition_id` of the edition this one extends, or `null` for a product's first. |
| `created_at` | required | When the manifest was written: an RFC 3339 timestamp in UTC. |
| `producer` | required | What sealed the edition: a `Producer`. |
| `sdmxlib_version` | required | The version of sdmxlib that wrote the manifest. |
| `schema_version` | required | The store schema of `metadata.duckdb`, such as `"2.52"` (§4). |
| `duckdb_version` | required | The DuckDB release that wrote the DuckDB files, such as `"2.0.0"`. |
| `duckdb_storage_version` | required | The newest storage version among `metadata.duckdb` and `tables.ducklake`, the integer each file's header records (§4). |
| `ducklake_version` | required | The DuckLake extension that wrote `tables.ducklake`, or `null` for an edition without one. |
| `ducklake_spec_version` | required | The DuckLake format the catalog records, such as `"1.1"`, or `null` for an edition without one (§4). |
| `data_path` | required | Where the data files live, relative to the edition directory: `"../data/"`. |
| `files` | required | Every file in the edition directory except the manifest, and every data file the lake catalog references, as `EditionFile`s sorted by `root`, then `path`. |
| `bindings` | required | One `BindingCount` per bound dataflow, sorted by `dataflow`. |
| `artefacts` | required | One `ArtefactEntry` per maintainable artefact in the store but the `DataflowBinding`s, sorted by `urn`. |
| `canonical_form` | required | The canonical form every fingerprint in the manifest hashes, `"1"` (§9). Fingerprints at different forms are never compared. |
| `content_fingerprint` | optional | The product fingerprint (§9), or `null`. |
| `producer_fields` | optional | The producer's own fields, as a JSON object. Readers carry it and do not interpret it. |
| `extensions` | optional | Sections owned by other packages, keyed by package name, each a JSON object. Readers carry them and do not interpret them. |
| `provenance` | optional | Where a fetched edition came from: a `Provenance`, or `null`. |

### `Producer`

| field | presence | meaning |
| --- | --- | --- |
| `name` | required | The builder's name, such as `"sdmxlib.edition.fetch_edition"`. |
| `version` | required | The builder's version. |

### `EditionFile`

| field | presence | meaning |
| --- | --- | --- |
| `path` | required | The file's path, `/`-separated, relative to the directory `root` names. |
| `size` | required | Its size in bytes. |
| `digests` | required | Its contents' digests, an object of algorithm name to lowercase hex. It MUST hold `sha256`, `md5` and `crc32c` (below), and MAY hold others; a reader compares those it knows and ignores the rest. |
| `root` | required | `"edition"` for a file in the edition directory, `"data"` for a data file under `data_path`. |
| `tier` | required | The producer's default placement: `"required"` pulled before a server is ready, `"lazy"` pulled to local disk on first use, `"remote"` read remotely by byte range (§7). |
| `carried_from` | optional | For a data file, the `edition_id` of the manifest whose `size` and `digests` this entry copies; `null` for one hashed by this edition. |

Each required digest is there for a reader that holds a different copy of the
file:

| algorithm | value | read by |
| --- | --- | --- |
| `sha256` | 64 hex digits | every check run on the files themselves, and the integrity anchor: the only collision-resistant one of the three |
| `md5` | 32 hex digits | Azure Blob Storage, which keeps it as `Content-MD5`: the base64 of the same 16 bytes |
| `crc32c` | 8 hex digits | S3, which keeps it whole for a file uploaded in any number of parts (`x-amz-checksum-crc32c`, the base64 of the same 4 bytes, big-endian), and GCS, which keeps it for every file |

### `BindingCount`

| field | presence | meaning |
| --- | --- | --- |
| `dataflow` | required | The bound dataflow's URN. |
| `observations` | required | How many observation rows the binding's table holds. |
| `structural_version` | required | The URN of the data structure the binding was written against. |
| `fingerprint` | required | The table fingerprint of the binding's table (§9), `<sum>:<count>`. |
| `structure_fingerprint` | required | The structure fingerprint of the dataflow (§9). |

### `ArtefactEntry`

| field | presence | meaning |
| --- | --- | --- |
| `urn` | required | The artefact's URN, at its pinned version. |
| `stability` | required | `"semantic"` for a final semantic version (three numeric parts, no extension, major at least 1), which SDMX 3.0 holds immutable; `"legacy"` for any other. |
| `partial` | required | `true` for a partial item scheme, a subset of the full one. |
| `fingerprint` | required | The artefact fingerprint (§9): the SHA-256 of its canonical text, lowercase hex. |
| `partial_of` | optional | For a partial, the artefact fingerprint of the full artefact the producer cut it from (`seal(partial_of=...)`). A catalog compares it with the full artefact's entry where that is published, so the partial's items never need to be in the manifest. Absent for a full artefact, and for a partial whose producer did not record it. Added in 2.1. |

### `Provenance`

`fetch_edition` writes it. An edition built from data the producer holds leaves
it `null`.

| field | presence | meaning |
| --- | --- | --- |
| `source` | required | The registry's stable id, such as `"ECB"`. |
| `endpoint` | required | The REST endpoint fetched from. |
| `api` | required | The SDMX REST API version spoken to it. |
| `retrieved_at` | required | When the fetch started, in UTC. |
| `dataflow` | required | The dataflow's URN, at the version it resolved to. |
| `key` | optional | The SDMX data key asked for; absent or `null` for every series. |
| `start` | optional | The inclusive `startPeriod` asked for. |
| `end` | optional | The inclusive `endPeriod` asked for. |
| `structures_only` | optional | `true` when observations were left out on purpose; `false` by default. |
| `artefacts` | optional | The URN of every structural artefact fetched, sorted. |

### Versions

A minor format version only adds optional fields. A reader of `3.x`:

- MUST read a manifest of any `3.x`, and MUST ignore a field it does not know;
- MUST refuse a manifest whose major version is not `3`. Format 2.x wrote
  `bundle.manifest.json` with `bundle_id` and `token`, beside `meta.duckdb`
  and `lake.ducklake`; a 2.x catalog is rebuilt by re-ingesting, with no
  migrator before sdmxlib 1.0;
- MUST refuse a `manifest.json` with no `format_version`. That file is a
  builder's own format from before this one, and the producer must seal a new
  edition with `seal`.

A producer MUST NOT add top-level fields of its own. Its fields go in
`producer_fields`, and fields another package owns go in `extensions` under
that package's name.

## 3. Identity

`edition_id` identifies an edition. A producer MUST mint a new UUIDv7 when an
edition's build starts, name the edition's directory by it, and write it in
the manifest; a reader MUST NOT change it. The id exists before the build
because the directory it names does.

The edition id is minted rather than computed from the content, because no
hash of the files identifies the content:

- two builds from the same inputs differ in every file's bytes, both DuckDB's
  files and DuckLake's UUID-named data files;
- the same rows sit in different data files depending on how many inserts
  wrote them and how the table was compacted;
- DuckLake snapshot ids restart at 0 in every catalog, so they say nothing
  across products.

The per-file `sha256` values serve integrity: they prove the files are the
ones the manifest describes (§6). Iceberg, Delta and DuckLake follow the same
model, with a snapshot id for identity and file checksums for integrity.

A cache or an HTTP `ETag` MAY key on `edition_id`. When overlays are applied
at serve time, it MUST key on the effective token instead (§5.4).

## 4. Store and observations

### Opening read-only

A reader MUST open a sealed edition read-only: `metadata.duckdb` with
DuckDB's `read_only`, and the lake attached `READ_ONLY`. Any number of
processes MAY then hold one edition open at once, and a checker MAY run
beside a server. A sealed edition is never written, so a reader MUST also move DuckDB's
spill directory outside it: a read-only database spills to `<file>.tmp`
beside itself by default.

`LocalRegistry.edition(path)` does this for the edition at `path` and
returns an `EditionRegistry`, which has the read methods and no writes and no
`sql()`. `LocalRegistry.build(product)` starts an edition, writable, for a
builder (§7).

A server holding many editions open SHOULD hold them in one DuckDB engine, so
they share one buffer pool, one thread pool and one memory limit:

```python
engine = sl.EditionEngine(memory_limit="70%", threads=8)
census = sl.LocalRegistry.edition(census_path, engine=engine)
trade = sl.LocalRegistry.edition(trade_path, engine=engine)
```

Each edition attaches under its own prefix, `<product_id>__<edition_id>`,
with every character of the product id outside `[A-Za-z0-9_]` made `_` and
the edition id's hyphens dropped (`census-2021-cpt` and `01a178b7-b023-…`
give `census_2021_cpt__01a178b7b023…`): its `metadata.duckdb` as
`<prefix>__metadata`, and its lake, when observations are first read, as
`<prefix>__tables`. `EditionRegistry.catalog_name` returns the first, so a
caller with a schema of its own in the store can `USE "<catalog>".<schema>`.
A registry's handles read only its own catalog, so a query on one edition
never reads another. A binding keeps the role it was
stored with, `lake`; `EditionRegistry.binding_for` and `EditionRegistry.locate`
return it naming `<prefix>__tables`, which is what SQL built from a binding
needs. The same edition opened twice in one engine shares one attachment,
released when the last registry over it closes. Without `engine=`, an
edition gets an engine of its own, closed with the registry.

### Data files in object storage

A server MAY hold only an edition's `required` and `lazy` files on local disk
and read its data files, the `remote` tier, from the object store they were
uploaded to. Nothing in the edition changes: the files are uploaded as they
are, and the manifest's `data_path` stays relative. The server names where
the edition is in the store, and the reader resolves `data_path` against
that root in place of the local directory:

```python
engine = sl.EditionEngine(
    secrets=[sl.StoreSecret("azure", provider="credential_chain", options={"account_name": "acct"})],
)
census = sl.LocalRegistry.edition(
    pod_path,  # the required and lazy files, from copy_edition(..., tiers=("required", "lazy"))
    remote=store_url,  # such as "abfss://catalog@acct.dfs.core.windows.net/products/census/019a…/"
    engine=engine,
)
census.data_path  # the store's data/ beside the edition: "abfss://catalog@acct.dfs.core.windows.net/products/census/data/"
```

`remote` is any URI DuckDB reads: `s3://`, which also reaches S3-compatible
servers through an `endpoint` option, `r2://`, `gs://`, `az://`, `abfss://`,
`http(s)://`, and `file://` for a store mounted as a filesystem. The engine
loads the DuckDB extension the scheme needs (`httpfs` or `azure`). A
`StoreSecret` is a DuckDB secret, with DuckDB's types, providers and options;
the engine creates each one, so every edition it holds reads with them. A
`credential_chain` provider reads the cloud identity a pod already has, so
the server holds no keys.

`check_edition` and `check_on_open` take the same `remote=` and `engine=`. At
`receive`, the `lake` and `bindings` checks then read the data files in the
store, and the `remote` check sizes each listed one there; a `lookup`
(below) checks them by a digest the store keeps as well. `at="publish"` refuses
`remote`, since sealing hashes every file where it is built.

### The schema and engine checks

A reader MUST check three versions from the manifest **before opening any
file**, and refuse the edition, leaving its files unopened, if one fails:

| version | rule | sdmxlib raises |
| --- | --- | --- |
| `schema_version` | equal to the store schema the reader reads, `sdmxlib.storage.schema.SCHEMA_VERSION` | `StoreSchemaError` |
| `duckdb_storage_version` | at most the storage version the reader's DuckDB writes: DuckDB reads every older one | `StoreFormatError` |
| `ducklake_spec_version` | `null`, or at most the DuckLake format the reader's DuckLake writes, `sdmxlib.edition.DUCKLAKE_SPEC_VERSION`: DuckLake reads an older catalog read-only without migrating it | `StoreFormatError` |

DuckDB numbers its storage versions: 64 for files compatible with DuckDB 1.0,
65 with 1.2, 66 with 1.3, 67 with 1.4, 68 with 1.5, 69 with 2.0. A
producer records the number as DuckDB reads it, from the file's active
database header, without opening the file as a database. A DuckDB file
starts with a main header (an 8-byte checksum, the `DUCK` magic, a
little-endian 64-bit version) and holds two database headers, at bytes 4096
and 8192. The active one has the higher iteration count, the 64-bit integer
after its 8-byte checksum. Its storage field is the 64-bit integer 56 bytes
after the checksum:

- When the main header's version is 999, as DuckDB 2.0 writes it, or the
  field is 69 or more, the field is the storage version.
- Otherwise the field is a serialization number: 0 to 3 name 64, 4 names 65,
  5 names 66, 6 names 67 and 7 names 68. Some old releases wrote 64 itself
  there, which names 64.

The main header's own version is not the storage version: from DuckDB 2.0
it is 999, and a file bumped in place to a newer version keeps the version
it was created with there, at most 65. A manifest that records 999, as
sdmxlib 0.73.0 wrote for a file in DuckDB 2.0's format, names no version:
a reader MUST read it from the files' database headers instead.

An edition is never migrated in place: it is rebuilt from its source with a
matching sdmxlib.

### Bindings

Each dataflow with observations has a `DataflowBinding` artefact in
`metadata.duckdb`. The binding names the table, as `table_location`
(`schema.table`) inside the database `database_role`, and maps each SDMX
component to its column.

A stored binding's `database_role` is `lake`. `LocalRegistry.edition`
attaches `tables.ducklake` under `<prefix>__tables` (Opening read-only) when
something first asks for a binding or reads observations, and
`EditionRegistry.locate` gives a binding naming that catalog, attached.
Opening an edition to read artefacts alone never attaches the lake.

The manifest's `bindings` MUST list exactly the dataflows bound in the store,
each with the row count of its table.

A column a codelist or value list enumerates MUST hold codes of it, or
`NULL`. `NULL` is how a value is absent: an empty string, or any other
stand-in for "no value", is not a code and refuses the seal.
`seal` checks a column whose component's enumeration the store
holds; one whose DSD or enumeration the store does not hold is not checked.

### Availability

`metadata.duckdb` holds each binding's availability: the SDMX 3.1
`AvailabilityConstraint` for the unfiltered query, one cube region of the
values each dimension holds, and the observation count. The IM makes it no
maintainable artefact ("generated dynamically based on the query", SDMX 3.1
§12.3.2), so it has no id, version or name and is keyed by the dataflow, in
the tables `availability` and `availability_value`.
`EditionRegistry.availability(flow)` reads it, so a server answers
`/availability/<flow>/all/all` without scanning the observations: on a
1.14-billion-row edition, 0.25 s read and serialised in place of a 2.6 s
scan. A query narrowed by a key is still computed. `seal` stores it
unless `availability=False`.

## 5. Overlays

An overlay changes display annotations without a rebuild. It changes
annotations only: never ids, codes, structure or observations. Overlays are
separate JSON documents kept outside the product directory. A file added to a
sealed edition is a `files` finding (§6).

### 5.1 The document

#### `Overlay`

| field | presence | meaning |
| --- | --- | --- |
| `format_version` | required | The overlay format, `major.minor`: `"1.2"` for this page. A `1.0` or `1.1` overlay reads the same; 1.1 defines `scope`, and 1.2 adds `texts`. |
| `id` | required | The overlay's name. |
| `entries` | required | `OverlayEntry`s, in document order. |
| `texts` | optional | `OverlayText`s, in document order: names and descriptions to change. Only folding applies them (§5.6). |

#### `OverlayEntry`

| field | presence | meaning |
| --- | --- | --- |
| `selector` | required | Which artefacts and items it applies to (§5.2). |
| `op` | required | `"set"`, `"add"` or `"remove"` (§5.3). |
| `type` | required | The annotation type it acts on. |
| `annotation` | optional | The annotation `set` and `add` write. It MUST be present for `set` and `add`, and absent or `null` for `remove`. |
| `scope` | optional | The audience the entry applies to. A server that serves an audience applies the entries whose `scope` is that audience or `null`; a `null` scope applies to every audience. A server that serves no audience applies every entry. |

#### `OverlayAnnotation`

The annotation's type is the entry's `type`.

| field | presence | meaning |
| --- | --- | --- |
| `id` | optional | The annotation id. |
| `title` | optional | The annotation title. |
| `value` | optional | The annotation value. |
| `text` | optional | The annotation text, as an object from language tag to string. |

#### `OverlayText`

One language of an artefact's or item's name or description.

| field | presence | meaning |
| --- | --- | --- |
| `selector` | required | Which artefacts and items it applies to (§5.2). |
| `op` | required | `"set"` or `"remove"` (§5.3). |
| `field` | required | `"name"` or `"description"`. |
| `lang` | required | The language tag it acts on. |
| `text` | optional | The text `set` writes. It MUST be present for `set`, and absent or `null` for `remove`. |
| `scope` | optional | As `OverlayEntry`'s. |

A reader of overlay format `1.x` MUST refuse another major version, and MUST
refuse a field it does not know at any level. Unlike the manifest, an overlay
is input that changes what is served, so a misspelled field is an error.

### 5.2 Selectors

| selector | selects | specificity |
| --- | --- | --- |
| `*` | every artefact and every item | 0 |
| a class name: `Codelist`, `Code`, `Dimension`, … | every artefact or item of that SDMX class | 1 |
| `AGENCY:ID` | the maintainable artefacts with that agency and id, at every version and of every class | 2 |
| a URN | one artefact, or one item | 3 |

An item is named by its SDMX URN. A nested item's URN carries its dotted path,
as in `urn:sdmx:org.sdmx.infomodel.categoryscheme.Category=AG:CS(1.0).PARENT.CHILD`.
An `AGENCY:ID` selector never selects an item.

### 5.3 Operations and their order

An entry acts on the target's annotations of its `type` and leaves every
other annotation as it was:

- `remove` drops every annotation of the type;
- `add` appends the entry's annotation;
- `set` drops every annotation of the type, then appends what the merge hook
  returns for the dropped annotations and the new one. The default hook
  returns the new one alone, so `set` replaces.

Entries apply in ascending specificity, so the most specific applies last and
wins. Entries of equal specificity apply in the order the overlays are given,
and within one overlay in document order.

sdmxlib's merge hooks are passed as `merge=`, a `MergeHook` per annotation
type. A `MergeHook` names its `package`, `name` and `version`, and carries
the function. A hook that changes what it returns MUST change its version,
since the effective token covers it (§5.4). An implementation without a hook
MUST replace.

`apply_overlays`, `effective_token` and `fold_overlays` take `audience=`:
given one, each applies only the entries scoped to it and the unscoped ones.

### 5.4 The effective token

A server that applies overlays at serve time MUST key its caches on the
effective token:

- with no overlays, it is the manifest's `edition_id`;
- otherwise it is the lowercase hex SHA-256 of the UTF-8 text formed by
  joining these lines with `\n`:
  1. `v3`, the format of this text;
  2. the manifest's `edition_id`;
  3. the hooks: `type=<package>:<name>@<version>` for each annotation type
     with a merge hook, sorted by type and joined with `,`, or `default` when
     there is none;
  4. each overlay's digest **as applied**, with the entries that do not apply
     to the audience removed, in the order the overlays are applied.

A hook's package, name and version, and an annotation type with a hook,
never hold `,`, `=`, `@`, `:` or a newline.

An overlay's digest is the lowercase hex SHA-256 of its canonical JSON. That
JSON has no whitespace and sorts the keys of every object. Every field
appears, with `null` for an absent optional and `{}` for an empty `text`,
except `texts`, which is left out when it is empty, so an overlay that
changes no name digests as it did in format 1.1.
Non-ASCII characters are written as UTF-8, unescaped. This overlay:

```json
{"format_version": "1.0", "id": "labels", "entries": [
  {"selector": "Codelist", "op": "set", "type": "NOTE",
   "annotation": {"text": {"en": "Ünïcode"}}}]}
```

has the canonical form

```text
{"entries":[{"annotation":{"id":null,"text":{"en":"Ünïcode"},"title":null,"value":null},"op":"set","scope":null,"selector":"Codelist","type":"NOTE"}],"format_version":"1.0","id":"labels"}
```

and the digest `1903c3b4ad0c0ec6dd9ae8111675d2eec167b4c94921b62fba9e9ac09230777a`.
Applied alone to an edition whose edition id is
`0192f6a0-5c1e-7c3a-9f00-0a1b2c3d4e5f`, with no hooks, the hashed text is

```text
v3
0192f6a0-5c1e-7c3a-9f00-0a1b2c3d4e5f
default
1903c3b4ad0c0ec6dd9ae8111675d2eec167b4c94921b62fba9e9ac09230777a
```

and the effective token is
`f6850812689c5af4872b4c931d684127e37a8c47c0b9e76e29e72095645ce93b`. With a
hook for `NOTE` from package `acme-labels`, named `notes`, at version `1.2`,
the third line is `NOTE=acme-labels:notes@1.2` and the token is
`5d778ce03b5557a0e17535898bd6c75c34f8c3693856fccbfb8f22ee2e19d84e`.

Changing, adding, removing or reordering an overlay changes the effective
token. So do serving another audience that applies different entries,
changing a hook or its version, and sealing a new edition. Two audiences
that apply the same entries share a token.

### 5.5 The render version

A served response's bytes depend on the code that writes them as well as on
the edition. The effective token covers the edition and its overlays.
`sdmxlib.formats.render.render_version(media_type)` covers the writer, with
one version per wire format sdmxlib writes:

- structures in SDMX-ML 2.1, 3.0 and 3.1 and SDMX-JSON 2.0;
- availability in SDMX-JSON 1.0;
- metadata in SDMX-ML 3.0 and SDMX-JSON 2.0;
- data in SDMX-CSV 2.0.0 and SDMX-JSON 1.0 and 2.0.

A server that caches what it writes MUST key on the render version of the
format beside the effective token. A writer change then gives new keys and
the old entries age out. A release whose writers write the same bytes leaves
every version, and every key, as it was. Each version is the first 16 hex
characters of the sha256 of that format's golden file, one pinned input
written; a test refuses a writer change whose golden file and version have
not moved with it.

### 5.6 Folding overlays into an edition

`fold_overlays` applies overlays to the store for good, its `texts`
included. A text applies after the annotation entries, in the same order of
specificity (§5.3): `set` writes the language's text and `remove` drops it.
Removing the last language of a name is refused, since SDMX requires a
name. A server MUST NOT apply an overlay with a `text` that applies to its
audience: a name is structure, and feeds search and every label map, so it
changes only through a new edition. `apply_overlays` and
`effective_token` refuse one.

It starts a new edition extending the one it reads, replaces every artefact
an entry changes in the new `metadata.duckdb`, and seals it. The new manifest
keeps `product_id`, `producer`, `producer_fields`, `extensions` and
`provenance`, and has a new `edition_id` and the folded edition's as
`previous`. The folded edition is left as it was. After folding, a server no
longer applies those overlays.

## 6. Conformance

`check_edition(path, at=...)` checks a sealed edition, and the
`sdmxlib edition check PATH --at PROFILE` command runs it from a shell
(`uv tool install sdmxlib-cli`). The command prints each finding and exits 1
if there is any. `at` names the profile, which is where the check runs:

- `publish`, where the edition is sealed: everything, hashed in full;
- `receive`, where a copy lands: the edition's files hashed, `data/` by
  size, or in an object store by a digest the store keeps;
- `open`, each time a server opens it: sizes only, and no check that reads
  the store.

Each profile runs these checks in the order listed and reports every finding,
not only the first:

| check | passes when | publish | receive | open | on failure |
| --- | --- | --- | --- | --- | --- |
| `manifest` | the manifest parses, in a format this reader reads (§2) | yes | yes | yes | the run stops |
| `files`, the edition's | every listed file is present at its `size` with every digest the manifest records, and no unlisted file is in the edition directory | yes | yes | size only | continues |
| `files`, `data/`'s | every listed entry is present at its `size` with every digest the manifest records; `data/` is shared, so only listed entries are checked | yes | size, unless `lookup=` or `remote=` | no | continues |
| `expect` | every artefact, binding and content fingerprint the manifest records equals an earlier manifest's, read from the two manifests (§9); for a new edition that is to change no content, such as a rebuild at a new store schema | with `expect=` | with `expect=` | with `expect=` | continues |
| `remote` | every `data/` entry is in the object store at its `size`, with every digest the store and the manifest both hold equal (see Remote files); with `remote=` and no `lookup=`, every `data/` entry at its `size` in the store | no | with `lookup=` or `remote=` | no | continues |
| `manifest` | the manifest's `schema_version`, `duckdb_storage_version` and `ducklake_spec_version` pass the reader's checks, so the files can be opened (§4) | yes | yes | yes | the run stops |
| `lake` | every file the current snapshot references exists at the size DuckLake recorded and none is a delete file, and every file the catalog references from any snapshot it keeps is in the manifest; runs only when `tables.ducklake` exists | yes | yes | no | continues |
| `bindings` | the store's bindings are the manifest's, and each table holds the count the manifest gives | yes | yes | no | continues |
| `references` | every reference in the store names an artefact or an item in the store | yes | yes | no | continues |
| `fingerprints` | every artefact, binding and content fingerprint, recomputed, is the manifest's (§9) | yes | with `fingerprints=True` | no | continues |
| `availability` | every coded column holds codes of its enumeration or `NULL`, and the stored availability is the data's: each dimension's values and the observation count, rescanned (§4) | yes | no | no | continues |
| `partials` | every partial whose entry records `partial_of` has a full artefact at its URN in the `structures=` edition with that fingerprint, and the partial's items, each under the same parent, are among the full artefact's; a codelist's, concept scheme's and category scheme's items are compared, another scheme's fingerprint alone | with `structures=` | with `structures=` | no | continues |

A store file that cannot be read, a damaged `metadata.duckdb` or `tables.ducklake`,
is a finding under the check that tried to read it, and ends the run.

At `receive` the lake and the bindings read the data where the registry
reads it, local or mounted, even when `lookup=` checks `data/` in the blob
store.

### The receive stamp

`check_on_open(path, stamps=dir, remote=None, engine=None, lookup=None, extensions=None)` is what a
server runs when it opens an edition. It runs `receive` once per edition and
manifest, and `open` after that:

- With no stamp at `<stamps>/<product_id>/<edition_id>`, or a stamp for
  another manifest under the same edition id, it runs `receive`. When that finds nothing,
  it writes the stamp: a JSON file holding the manifest's `sha256`, the checks
  that ran and the time.
- With a stamp holding this manifest's `sha256`, it runs `open`, which hashes
  nothing.

`stamps` belongs to the server, outside every product: a stamp records what
that machine checked. A `product_id` that cannot name one directory below
`stamps` is a `manifest` finding, and the check is not stamped. The returned
report's `at` says which profile ran.

The `references` check reads the stored references in SQL and loads no
artefact. They are the rows of `sdmx.artefact_ref` and every reference column
of the item and side tables, each held by the artefact that owns its row. A
target resolves when it is an artefact's URN or an item's, built from the
owning scheme's URN and the item's id, or its path for a nested item. A URN
without a version names the latest version the store holds, and SDMX 2.1's
`Attribute` and `PrimaryMeasure` match SDMX 3.0's `DataAttribute` and
`Measure`. A finding's subject is the URN of the artefact holding the
reference, and its detail names the reference's role and target. On a
2.6 GB census edition with 2.16 million stored references the check takes
0.15 s.

A package that owns part of an edition MAY pass its own checks as
`extensions={name: check}`. Each runs last at every profile, is called as
`check(root, manifest, registry, at)` so it can do less at `open`, and its
findings carry its name.

### Remote files

A server reads `remote` files from object storage and never holds them, so it
cannot hash them. A store reports a file's size and the digests it keeps,
each base64, and which it keeps depends on the store and the upload:

| store | keeps | digest | upload sets |
| --- | --- | --- | --- |
| Azure Blob Storage | `Content-MD5`, when the uploader sets it | `md5` | `content_md5(entry)` as `Content-MD5`, or `x-ms-blob-content-md5` on a block upload |
| Amazon S3 | the checksum the uploader picks; for a multipart upload only a CRC is of the whole file | `crc32c` | `content_crc32c(entry)` as `x-amz-checksum-crc32c`, with checksum type `FULL_OBJECT` |
| Google Cloud Storage | CRC32C always; MD5 except for a composite object | `crc32c` | nothing: GCS computes it |

A publish job MUST upload every file so its store keeps a digest the
manifest records; each store keeps one of the three for any upload.

`verify_remote(manifest, lookup, tiers=("remote",))` checks the files of the given
tiers against what `lookup(entry)` returns for each: a `RemoteFile(size,
digests)`, `digests` naming each algorithm in lowercase (`md5`, `sha256`,
`crc32c`), or `None`. sdmxlib holds no blob-store client: the caller's lookup
asks the store. Every algorithm both sides hold is compared; a composite
checksum, written with S3's `-<parts>` suffix, is a digest of the parts and
is set aside. Each file missing, of another size, whose store keeps no digest
the manifest records, or with one that differs is a finding under the check
`remote`.

The `Report` lists the checks that ran as `checked`, so a clean report also
says how much it covered. Another implementation conforms when it refuses
what the `manifest` checks refuse, and when an edition it seals passes
every check.

## 7. Producing an edition

1. Start an edition with `LocalRegistry.build(product)`, or
   `LocalRegistry.build(product, previous=edition_id)` to extend the last one.
   It mints the edition id, creates `<product>/<edition_id>/`, which the
   builder's `edition_dir` names, and with `previous=` copies that edition's
   `metadata.duckdb` and `tables.ducklake` in, so only what changed is written and
   the lake adds only new data files to `data/`.
2. Write the artefacts at this sdmxlib's store schema (`add_all`) and the
   observations into the lake (`write_observations(schema=..., table=...,
   source=..., partition_by=..., sorted_by=...)`), adding a `DataflowBinding`
   for each dataflow. The table's layout is DuckLake's: `partition_by` sets
   its `PARTITIONED BY` and `sorted_by` its `SORTED BY`, DuckLake sort
   expressions such as `"REF_AREA"`, `"TIME_PERIOD DESC"` or a space-filling
   curve, and DuckLake writes the rows sorted. The lake's catalog records both
   and ships with the edition, and a query reads the partitioning from there
   ([GH-205](https://github.com/StatCat-Suite/sdmxlib/issues/205)).
   `write_observations` replaces the table by default; `mode="append"`
   inserts into it and keeps its layout, so it refuses a `partition_by` or
   `sorted_by`. A builder
   that writes in more than one step reopens the edition with
   `build(product, resume=edition_id)`. Close every connection that wrote: DuckDB
   checkpoints a database when its last connection closes, and a write-ahead
   log left beside a database holds rows its checksum does not cover.
   `seal` refuses while one remains, and the `files` check names one
   found in a sealed edition.
3. Add any files of your own to the edition directory.
4. Call `seal(edition_dir, product_id=..., producer=...)` last, with
   `producer_fields`, `extensions`, `provenance`, `tiers`, `lifecycle`,
   `expire_snapshots` and `availability` as needed. It scans each binding's
   columns once: a coded value that is not a code refuses the seal,
   naming the column, the value and its row count, and each dimension's values
   are stored as the binding's availability (§4). An edition holds its own
   bindings' availability or, with `availability=False`, none: whatever the
   store held, copied from a previous edition, is deleted first. It first
   settles the lake:
   - rows DuckLake inlined into the catalog are flushed into data files;
   - every table holding delete files is rewritten into data files without
     them (`ducklake_rewrite_data_files` with `delete_threshold => 0`: the
     default threshold keeps a small delete, which is exactly a daily delta);
   - with `lifecycle="final"`, the lake's files are merged, so a table holds
     one file, or one per partition. DuckLake sorts what it merges by the
     table's `SORTED BY`; `seal` sets no sort of its own;
   - with `expire_snapshots=True`, every snapshot but the current one is
     expired. Without it, earlier snapshots stay readable and the manifest
     lists the files they reach.

   Then it rewrites `metadata.duckdb` into a fresh file, at the storage version
   it was written at, without the keys and indexes a reader does not use:
   the primary keys of `hierarchy_node` and `artefact_ref`,
   `hierarchy_node`'s `UNIQUE (hierarchy_urn, path)`, and `artefact_ref`'s
   two indexes. A sealed store is never written, so they enforce nothing.
   Measured on the census edition, they were 520 MB of a 1,022 MB store, and
   no read benchmark changed without them. `LocalRegistry.build(product,
   previous=edition_id)` puts them back on the copy it extends. A reader MUST
   NOT rely on any key or index in a sealed store.

   It never deletes a file from `data/`. `tiers` maps
   a path prefix to a placement tier, the longest matching prefix winning:
   `{"search/": "lazy"}`. An edition file no prefix matches is `required` if
   it is `metadata.duckdb` or `tables.ducklake` and `lazy` otherwise; a data
   file is always `remote`. It lists the edition's files and every data file
   its catalog references, counts
   each binding's rows through DuckLake, records the engine versions and
   every artefact, and writes the manifest to a
   temporary file renamed into place, so a reader never sees half of one. A
   data file the previous edition's manifest lists at the same size keeps
   that manifest's digests, with `carried_from` naming its edition id, so a
   new edition hashes only the files it wrote.
5. Run `check_edition` on the result.

An edition is sealed once: `seal` refuses one that already has a manifest,
and a change is a new edition. A builder that wrote its own manifest before
this format MUST seal a new edition with `seal`, moving its own fields into
`producer_fields`.

sdmxlib also produces editions itself:

- `fetch_edition(source, flow, path, key=..., start=..., end=...)` fetches one
  dataflow from an SDMX REST source, narrowed by a key and periods, with its
  structural closure, and records `Provenance`. The closure is fetched with
  `references=all`, so it holds the constraints, categorisations and
  hierarchy associations that refer to the dataflow; over SDMX 2.1 no
  hierarchy association arrives, since 2.1 has none.
  `sdmxlib edition fetch` runs it from a shell.
- `build_edition(source, dataflows, path, product_id=..., producer=..., observations=...)`
  builds an edition from dataflows' structural closures at a `RestRegistry`
  or a sealed edition and the observations a producer holds, a Polars frame or
  a DuckDB relation over a DuckLake table say. It cuts each codelist to the
  codes the observations and the allowed constraints use, with their
  ancestors, and records `partial_of`; a codelist a hierarchy places stays
  full, since SDMX has no partial hierarchy. `fetch_edition` is a call to it
  with every codelist full.
- `sdmxlib.testing.synthetic_edition(path, shape, seed=...)` builds an
  edition of a given `Shape` deterministically from its seed, for tests and
  benchmarks. A `Dim` may carry `HierarchyShape`s: each becomes a `Hierarchy`
  over the dimension's codelist and a `HierarchyAssociation` linking it to
  the dimension.
- `sdmxlib.testing.profile_edition(path, flow)` reads the `Shape` of one
  dataflow in a real edition (counts and fan-outs, never values), each
  dimension's associated hierarchies included, so a synthetic edition can
  stand in for it.

## 8. Copying an edition

`copy_edition(source, destination)` copies a sealed edition, and
`sdmxlib edition copy SRC DST` runs it from a shell. It is how a server
receives an edition and how a mirror is made. It copies the files the manifest
lists, the data files into the `data_path` beside `destination`, checks each
against its `size` and every digest the manifest records, and copies the manifest last, byte for byte.
A data file already at the destination with the listed digest is kept, so
receiving the next edition of a product copies only its new data files. An
interrupted copy leaves a directory without a manifest, which readers refuse
and `check_edition` reports as unsealed; running the copy again completes it.

`tiers=` (`--tiers required,lazy` from a shell) copies only the files of
those placement tiers, which is what a server holds when it reads the
`remote` tier remotely. Such a copy
is incomplete by intent: `check_edition` reports the files it left out.

### Pulling from object storage

sdmxlib holds no blob-store client, so a server pulls an edition from object
storage with the deployment's own tool: rclone, azcopy, `gcloud storage` or
`aws s3`. `edition_files(manifest, tiers=...)` (`sdmxlib edition files`)
lists what that copy needs, one path per line under the product directory:
data files as `data/<schema>/<table>/ducklake-….parquet`, then the edition's
own as `<edition_id>/metadata.duckdb`.

The list leaves out the manifest. Those tools copy several files at once, so
no order in a list makes the manifest land last; the server copies it in a
second step, after every listed file has arrived, and a pull that stops part
way leaves an edition readers refuse:

```bash
# 1. the manifest, staged outside the edition, to list from
rclone copyto store:products/census/$EDITION/manifest.json /stage/census/manifest.json
# 2. every file the required and lazy tiers need
sdmxlib edition files /stage/census --tiers required,lazy \
  | rclone copy --files-from - store:products/census /var/products/census
# 3. the manifest, last
sdmxlib edition files /stage/census --manifest \
  | rclone copy --files-from - store:products/census /var/products/census
```

`check_on_open` then verifies the copy, with `remote=` when the remote tier stays in
the store ([Data files in object storage](#data-files-in-object-storage)).

## 9. Fingerprints

An edition id names an edition; a fingerprint names its content. Two
editions with equal content have equal fingerprints whatever their ids, file
bytes, file layout or DuckDB release, so a result cache keys on fingerprints
and never on an edition id. `seal` computes all four.

| fingerprint | over | value |
| --- | --- | --- |
| table, `BindingCount.fingerprint` | every row of the binding's table | the sum over rows of each row's MD5 (the lower 64 bits, DuckDB's `md5_number_lower`) modulo 2^62, then `:` and the row count. The row's text is DuckDB's `to_json` of the row, columns in table order |
| artefact, `ArtefactEntry.fingerprint` | the artefact's canonical text (below) | its SHA-256 |
| structure, `BindingCount.structure_fingerprint` | the dataflow's structural closure | the SHA-256 of the closure's artefact fingerprints in URN order, joined by `\n` |
| product, `content_fingerprint` | every artefact and every binding | the SHA-256 of `artefacts:`, each artefact fingerprint in URN order, `bindings:`, then `<dataflow URN>\t<table fingerprint>` per binding in dataflow order, joined by `\n` |

The table fingerprint does not move with row order, and moves when a value
moves between rows, a row is duplicated, or any value changes. In SQL:

```sql
SELECT coalesce(sum((md5_number_lower(to_json(t)) % 4611686018427387904)::HUGEINT), 0)::VARCHAR
    || ':' || count(*)::VARCHAR
FROM lake.<schema>.<table> AS t
```

A dataflow's **structural closure** is what the dataflow and its data
structure refer to, transitively; every artefact other than a dataflow that
refers to either (a constraint, a provision agreement, a hierarchy
association: these point at the dataflow, not the reverse); and what those
refer to, transitively. Another dataflow of the same data structure refers to
it and is left out, so a change to one dataflow leaves its siblings' structure
fingerprints. A reference to an item counts as one to the artefact holding
it.

### Canonical text of an artefact

An artefact fingerprint hashes the artefact's canonical text, which
`sdmxlib.edition.canonical_json` produces. Two artefacts with equal content
give byte-equal text, and a change to any field the model holds changes it.
The rules below are canonical form `1`, `sdmxlib.edition.CANONICAL_FORM`; a
change to any of them is a new form, and fingerprints made at different forms
are never compared.

The text is compact JSON in UTF-8 with every object's keys sorted, built from
the model object as follows:

| value | text |
| --- | --- |
| a model object (an artefact, item, component, facet, annotation, …) | an object holding `"$class"`, its class name, and every field the class takes at construction, `null` included; fields the class derives itself are left out |
| `maintainer` | the agency's id: it refers to an Agency item, whose own content belongs to its scheme's text |
| a reference | its URN, never followed |
| an international string | an object of language tag to text |
| a list or tuple, including items and annotations | an array, in its order |
| a set | an array sorted by each element's canonical text |
| an enum | its member's name, such as `"ALLOWED"` |
| a datetime | UTC, `YYYY-MM-DDTHH:MM:SS.ffffffZ` |
| a date | `YYYY-MM-DD` |
| a float | its shortest round-trip form; a non-finite float is refused |
| a string, integer, boolean or null | itself |

Any other value is refused, so a new kind of field cannot encode as something
ambiguous. The text does not depend on the wire format an artefact was read
from: the fields SDMX-JSON cannot carry are in it.
