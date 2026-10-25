# The bundle format

A bundle is a directory holding SDMX artefacts and observations, published as
one unit, once per release: each publication is a directory of its own.
statcatlib writes bundles, sdmxlib-rest serves them, and sdmxlib reads,
checks, copies and fetches them. This page is the contract between them.

It describes **manifest format 2.0** and **overlay format 1.2**. sdmxlib's
code is the reference implementation (`sdmxlib.bundle`,
`LocalRegistry.bundle`), and the two JSON Schemas ship with it:
`sdmxlib.bundle.manifest_schema()` and `sdmxlib.bundle.overlay_schema()`.
`tests/unit/test_bundle_spec.py` keeps the field tables below equal to those
schemas.

MUST, MUST NOT and MAY carry their RFC 2119 meanings. A *producer* writes a
bundle; a *reader* opens one.

## 1. Layout

```text
<bundle>/
  data/                    data files shared by every publication of the bundle
    <schema>/<table>/ducklake-<uuid>.parquet
  <token>/                 one publication, named by its token; never rewritten
    meta.duckdb            the artefact store: the sdmx.* schema, bindings included
    lake.ducklake          the DuckLake catalog for observations, over ../data/
    bundle.manifest.json   the manifest, written last
```

- `meta.duckdb` MUST be present. It holds every artefact in the store schema
  that `sdmxlib.storage.schema` defines, at the version recorded in the
  manifest's `schema_version`.
- `lake.ducklake` holds the catalog for observations, and `data/` their data
  files, in DuckLake's own layout. A publication with no bound dataflow MAY
  leave the catalog out. A published catalog holds no rows itself and no
  delete file in its current snapshot: `write_manifest` flushes the rows
  DuckLake inlines into the catalog, and rewrites each table that holds delete
  files (§7).
- `data/` is shared. A data file is written once, under a fresh UUID, and
  every publication whose catalog references it lists it. A producer MUST
  NOT delete, rewrite or clean up a file in `data/`
  (`ducklake_cleanup_old_files` and `ducklake_delete_orphaned_files`
  included): an earlier publication may still list it. Deleting is garbage
  collection's job, which keeps every file some manifest still lists.
- A publication directory is written once. A change is a new publication,
  whose catalog is a copy of the previous one's, extended (§7).
- `bundle.manifest.json` marks the publication as published. A directory
  without one is unpublished: `LocalRegistry.bundle` refuses it, and its
  builder reopens it with `LocalRegistry.build(bundle, resume=token)`.
- `bundle.manifest.json.sig` is reserved for a detached signature of the
  manifest. The manifest never lists it, the `files` check never reports it,
  and `copy_bundle` copies it just before the manifest.
- A producer MAY add files of its own to the publication directory, such as
  a search index. The manifest MUST list them like any other file (§2), so
  they MUST be in place before the manifest is written.

A reader MUST resolve every path relative to the publication directory as it
is now, and the data files through the manifest's `data_path`. DuckLake
records the data path it was written with; sdmxlib attaches the lake with
`OVERRIDE_DATA_PATH`, because a bundle is moved, copied and unpacked
elsewhere.

## 2. The manifest

`bundle.manifest.json` is a UTF-8 JSON object. sdmxlib writes it indented by
two spaces, but a reader MUST NOT depend on the layout.

### `Manifest`

| field | presence | meaning |
| --- | --- | --- |
| `format_version` | required | The manifest format, `major.minor`: `"2.0"` for this page. |
| `bundle_id` | required | The producer's stable name for the bundle, kept across rebuilds. It names *which* bundle, never which publication. |
| `token` | required | A UUIDv7 minted when the publication's build started, and the name of its directory (§3). |
| `previous_token` | required | The `token` of the publication this one extends, or `null` for a bundle's first. |
| `created_at` | required | When the manifest was written: an RFC 3339 timestamp in UTC. |
| `producer` | required | What built the bundle: a `Producer`. |
| `sdmxlib_version` | required | The version of sdmxlib that wrote the manifest. |
| `schema_version` | required | The store schema of `meta.duckdb`, such as `"2.48"` (§4). |
| `duckdb_version` | required | The DuckDB release that wrote the DuckDB files, such as `"2.0.0"`. |
| `duckdb_storage_version` | required | The newest storage version among `meta.duckdb` and `lake.ducklake`, the integer each file's header records (§4). |
| `ducklake_version` | required | The DuckLake extension that wrote `lake.ducklake`, or `null` for a publication without one. |
| `ducklake_spec_version` | required | The DuckLake format the catalog records, such as `"1.1"`, or `null` for a publication without one (§4). |
| `data_path` | required | Where the data files live, relative to the publication directory: `"../data/"`. |
| `files` | required | Every file in the publication directory except the manifest, and every data file the lake catalog references, as `BundleFile`s sorted by `root`, then `path`. |
| `bindings` | required | One `BindingCount` per bound dataflow, sorted by `dataflow`. |
| `artefacts` | required | One `ArtefactEntry` per maintainable artefact in the store but the `DataflowBinding`s, sorted by `urn`. |
| `canonical_form` | required | The canonical form every fingerprint in the manifest hashes, `"1"` (§9). Fingerprints at different forms are never compared. |
| `content_fingerprint` | optional | The bundle fingerprint (§9), or `null`. |
| `product` | optional | The producer's own fields, as a JSON object. Readers carry it and do not interpret it. |
| `extensions` | optional | Sections owned by other packages, keyed by package name, each a JSON object. Readers carry them and do not interpret them. |
| `provenance` | optional | Where a fetched bundle came from: a `Provenance`, or `null`. |

### `Producer`

| field | presence | meaning |
| --- | --- | --- |
| `name` | required | The builder's name, such as `"sdmxlib.bundle.fetch_bundle"`. |
| `version` | required | The builder's version. |

### `BundleFile`

| field | presence | meaning |
| --- | --- | --- |
| `path` | required | The file's path, `/`-separated, relative to the directory `root` names. |
| `size` | required | Its size in bytes. |
| `sha256` | required | The SHA-256 of its contents, as lowercase hex. |
| `md5` | required | The MD5 of its contents, as lowercase hex. An upload's `Content-MD5` header is the base64 of the same 16 bytes. |
| `root` | required | `"publication"` for a file in the publication directory, `"data"` for a data file under `data_path`. |
| `tier` | required | The producer's default placement: `1` pulled before a server is ready, `2` pulled lazily to local disk, `3` read remotely by byte range (§7). |
| `carried_from` | optional | For a data file, the `token` of the manifest whose `size`, `sha256` and `md5` this entry copies; `null` for one hashed by this publication. |

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

### `Provenance`

`fetch_bundle` writes it. A bundle built from data the producer holds leaves
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

A minor format version only adds optional fields. A reader of `2.x`:

- MUST read a manifest of any `2.x`, and MUST ignore a field it does not know;
- MUST refuse a manifest whose major version is not `2`. Format 1.x bundles
  kept their data files inside one directory, so they are rebuilt;
- MUST refuse a `bundle.manifest.json` with no `format_version`. That file is
  a builder's own format from before this one, and the producer must republish
  the bundle with `write_manifest`.

A producer MUST NOT add top-level fields of its own. Its fields go in
`product`, and fields another package owns go in `extensions` under that
package's name.

## 3. Identity

`token` identifies a publication. A producer MUST mint a new UUIDv7 when a
publication's build starts, name the publication's directory by it, and write
that token in the manifest; a reader MUST NOT change it. The token exists
before the build because the directory it names does.

The token is minted rather than computed from the content, because no hash of
the files identifies the content:

- two builds from the same inputs differ in every file's bytes, both DuckDB's
  files and DuckLake's UUID-named data files;
- the same rows sit in different data files depending on how many inserts
  wrote them and how the table was compacted;
- DuckLake snapshot ids restart at 0 in every catalog, so they say nothing
  across bundles.

The per-file `sha256` values serve integrity: they prove the files are the
ones the manifest describes (§6). Iceberg, Delta and DuckLake follow the same
model, with a snapshot id for identity and file checksums for integrity.

A cache or an HTTP `ETag` MAY key on `token`. When overlays are applied at
serve time, it MUST key on the effective token instead (§5.4).

## 4. Store and observations

### Opening read-only

A reader MUST open a published bundle read-only: `meta.duckdb` with DuckDB's
`read_only`, and the lake attached `READ_ONLY`. Any number of processes MAY
then hold one bundle open at once, and a checker MAY run beside a server.
A published bundle is never written, so a reader MUST also move DuckDB's
spill directory outside it: a read-only database spills to `<file>.tmp`
beside itself by default.

`LocalRegistry.bundle(path)` does this for the publication at `path` and
returns a `BundleRegistry`, which has the read methods and no writes and no
`sql()`. `LocalRegistry.build(bundle)` starts a publication, writable, for a
builder (§7).

A server holding many bundles open SHOULD hold them in one DuckDB engine, so
they share one buffer pool, one thread pool and one memory limit:

```python
engine = sl.BundleEngine(memory_limit="70%", threads=8)
census = sl.LocalRegistry.bundle(census_path, engine=engine)
trade = sl.LocalRegistry.bundle(trade_path, engine=engine)
```

Each publication attaches under its own prefix, `<bundle_id>__<token>`: its
`meta.duckdb` as `<prefix>__meta`, and its lake, when observations are first
read, as `<prefix>__lake`. A registry's handles read only its own catalog, so
a query on one bundle never reads another. A binding keeps the role it was
stored with, `lake`; `BundleRegistry.binding_for` and `BundleRegistry.locate`
return it naming `<prefix>__lake`, which is what SQL built from a binding
needs. The same publication opened twice in one engine shares one
attachment, released when the last registry over it closes. Without
`engine=`, a bundle gets an engine of its own, closed with the registry.

### The schema and engine checks

A reader MUST check three versions from the manifest **before opening any
file**, and refuse the publication, leaving its files unopened, if one fails:

| version | rule | sdmxlib raises |
| --- | --- | --- |
| `schema_version` | equal to the store schema the reader reads, `sdmxlib.storage.schema.SCHEMA_VERSION` | `StoreSchemaError` |
| `duckdb_storage_version` | at most the storage version the reader's DuckDB writes: DuckDB reads every older one | `StoreFormatError` |
| `ducklake_spec_version` | `null`, or at most the DuckLake format the reader's DuckLake writes, `sdmxlib.bundle.DUCKLAKE_SPEC_VERSION`: DuckLake reads an older catalog read-only without migrating it | `StoreFormatError` |

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

A bundle is never migrated in place: it is rebuilt from its source with a
matching sdmxlib.

### Bindings

Each dataflow with observations has a `DataflowBinding` artefact in
`meta.duckdb`. The binding names the table, as `table_location`
(`schema.table`) inside the database `database_role`, and maps each SDMX
component to its column.

A stored binding's `database_role` is `lake`. `LocalRegistry.bundle`
attaches `lake.ducklake` under `<prefix>__lake` (Opening read-only) when
observations are first read, and `BundleRegistry.locate` gives a binding
naming that catalog. Opening a bundle to read artefacts alone never attaches
the lake.

The manifest's `bindings` MUST list exactly the dataflows bound in the store,
each with the row count of its table.

## 5. Overlays

An overlay changes display annotations without a rebuild. It changes
annotations only: never ids, codes, structure or observations. Overlays are
separate JSON documents kept outside the bundle directory. A file added to a
published bundle is a `files` finding (§6).

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

- with no overlays, it is the manifest's `token`;
- otherwise it is the lowercase hex SHA-256 of the UTF-8 text formed by
  joining these lines with `\n`:
  1. `v2`, the format of this text;
  2. the manifest's `token`;
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
Applied alone to a bundle whose token is
`0192f6a0-5c1e-7c3a-9f00-0a1b2c3d4e5f`, with no hooks, the hashed text is

```text
v2
0192f6a0-5c1e-7c3a-9f00-0a1b2c3d4e5f
default
1903c3b4ad0c0ec6dd9ae8111675d2eec167b4c94921b62fba9e9ac09230777a
```

and the effective token is
`8fca15078e912b6d9f91d251020291c66abcd34f61726ec0aa77a791cf6a2412`. With a
hook for `NOTE` from package `acme-labels`, named `notes`, at version `1.2`,
the third line is `NOTE=acme-labels:notes@1.2` and the token is
`ed56fe44047ea4fbca399ea38e102f1aecfa9d96a53a5174ba05e70204d55344`.

Changing, adding, removing or reordering an overlay changes the effective
token. So do serving another audience that applies different entries,
changing a hook or its version, and republishing the bundle. Two audiences
that apply the same entries share a token.

### 5.5 The render version

A served response's bytes depend on the code that writes them as well as on
the bundle. The effective token covers the bundle and its overlays.
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

### 5.6 Folding overlays into a bundle

`fold_overlays` applies overlays to the store for good, its `texts`
included. A text applies after the annotation entries, in the same order of
specificity (§5.3): `set` writes the language's text and `remove` drops it.
Removing the last language of a name is refused, since SDMX requires a
name. A server MUST NOT apply an overlay with a `text` that applies to its
audience: a name is structure, and feeds search and every label map, so it
changes only through a new publication. `apply_overlays` and
`effective_token` refuse one.

It starts a new
publication extending the one it reads, replaces every artefact an entry
changes in the new `meta.duckdb`, and publishes it. The new manifest keeps
`bundle_id`, `producer`, `product`, `extensions` and `provenance`, and has a
new `token` and the folded publication's as `previous_token`. The folded
publication is left as it was. After folding, a server no longer applies
those overlays.

## 6. Conformance

`check_bundle(path, at=...)` checks a published publication, and the
`sdmxlib bundle check PATH --at PROFILE` command runs it from a shell
(`uv tool install sdmxlib-cli`). The command prints each finding and exits 1
if there is any. `at` names the profile, which is where the check runs:

- `publish`, where the bundle is built: everything, hashed in full;
- `receive`, where a copy lands: the publication's files hashed, `data/` by
  size, or in a blob store by `Content-MD5`;
- `open`, each time a server opens it: sizes only, and no check that reads
  the store.

Each profile runs these checks in order and reports every finding, not only
the first:

| check | passes when | publish | receive | open | on failure |
| --- | --- | --- | --- | --- | --- |
| `manifest` | the manifest parses, in a format this reader reads (§2) | yes | yes | yes | the run stops |
| `files`, the publication's | every listed file is present at its `size` with its `sha256` and `md5`, and no unlisted file is in the publication directory | yes | yes | size only | continues |
| `files`, `data/`'s | every listed entry is present at its `size` with its `sha256` and `md5`; `data/` is shared, so only listed entries are checked | yes | size, unless `lookup=` | no | continues |
| `remote` | every `data/` entry is in the blob store at its `size` with a `Content-MD5` equal to its `md5` (see Remote files) | no | with `lookup=` | no | continues |
| `manifest` | the manifest's `schema_version`, `duckdb_storage_version` and `ducklake_spec_version` pass the reader's checks, so the files can be opened (§4) | yes | yes | yes | the run stops |
| `lake` | every file the current snapshot references exists at the size DuckLake recorded and none is a delete file, and every file the catalog references from any snapshot it keeps is in the manifest; runs only when `lake.ducklake` exists | yes | yes | no | continues |
| `bindings` | the store's bindings are the manifest's, and each table holds the count the manifest gives | yes | yes | no | continues |
| `references` | every reference in the store names an artefact or an item in the store | yes | yes | no | continues |
| `fingerprints` | every artefact, binding and content fingerprint, recomputed, is the manifest's (§9) | yes | with `fingerprints=True` | no | continues |

A store file that cannot be read, a damaged `meta.duckdb` or `lake.ducklake`,
is a finding under the check that tried to read it, and ends the run.

At `receive` the lake and the bindings read the data where the registry
reads it, local or mounted, even when `lookup=` checks `data/` in the blob
store.

### The receive stamp

`check_on_open(path, stamps=dir, lookup=None, extensions=None)` is what a
server runs when it opens a publication. It runs `receive` once per token and
manifest, and `open` after that:

- With no stamp at `<stamps>/<bundle_id>/<token>`, or a stamp for another
  manifest under the same token, it runs `receive`. When that finds nothing,
  it writes the stamp: a JSON file holding the manifest's `sha256`, the checks
  that ran and the time.
- With a stamp holding this manifest's `sha256`, it runs `open`, which hashes
  nothing.

`stamps` belongs to the server, outside every bundle: a stamp records what
that machine checked. A `bundle_id` that cannot name one directory below
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
2.6 GB census bundle with 2.16 million stored references the check takes
0.15 s.

A package that owns part of a bundle MAY pass its own checks as
`extensions={name: check}`. Each runs last at every profile, is called as
`check(root, manifest, registry, at)` so it can do less at `open`, and its
findings carry its name.

### Remote files

A server reads tier-3 files from blob storage and never holds them, so it
cannot hash them. A blob store reports a file's size and, when the uploader
set it, its `Content-MD5`: the base64 of the MD5 the manifest records in hex
(`content_md5(entry)` computes it). A publish job MUST set `Content-MD5` on
every upload.

`verify_remote(manifest, lookup, tiers=(3,))` checks the files of the given
tiers against what `lookup(entry)` returns for each, a `RemoteFile(size,
content_md5)` or `None`. sdmxlib holds no blob-store client: the caller's
lookup asks the store. Each file missing, of another size, without a
`Content-MD5`, or with a different one is a finding under the check `remote`.

The `Report` lists the checks that ran as `checked`, so a clean report also
says how much it covered. Another implementation conforms when it refuses
what the `manifest` checks refuse, and when a bundle it publishes passes
every check.

## 7. Producing a bundle

1. Start a publication with `LocalRegistry.build(bundle)`, or
   `LocalRegistry.build(bundle, previous=token)` to extend the last one. It
   mints the token, creates `<bundle>/<token>/`, which the builder's
   `publication` names, and with `previous=` copies that publication's
   `meta.duckdb` and `lake.ducklake` in, so only what changed is written and
   the lake adds only new data files to `data/`.
2. Write the artefacts at this sdmxlib's store schema and the observations
   into the lake, adding a `DataflowBinding` for each dataflow. A builder
   that writes in more than one step reopens the publication with
   `build(bundle, resume=token)`. Close every connection that wrote: DuckDB
   checkpoints a database when its last connection closes, and a write-ahead
   log left beside a database holds rows its checksum does not cover.
   `write_manifest` refuses while one remains, and the `files` check names one
   found in a published bundle.
3. Add any files of your own to the publication directory.
4. Call `write_manifest(publication, bundle_id=..., producer=...)` last, with
   `product`, `extensions`, `provenance`, `tiers`, `lifecycle` and
   `expire_snapshots` as needed. It first settles the lake:
   - rows DuckLake inlined into the catalog are flushed into data files;
   - every table holding delete files is rewritten into data files without
     them (`ducklake_rewrite_data_files` with `delete_threshold => 0`: the
     default threshold keeps a small delete, which is exactly a daily delta);
   - with `lifecycle="final"`, each table is compacted to one data file, a
     bound table sorted by its binding's `sort_order`, else its dimension
     columns in key order, unless the producer set a sort;
   - with `expire_snapshots=True`, every snapshot but the current one is
     expired. Without it, earlier snapshots stay readable and the manifest
     lists the files they reach.

   Then it rewrites `meta.duckdb` into a fresh file, at the storage version
   it was written at, without the keys and indexes a reader does not use:
   the primary keys of `hierarchy_node` and `artefact_ref`,
   `hierarchy_node`'s `UNIQUE (hierarchy_urn, path)`, and `artefact_ref`'s
   two indexes. A published store is never written, so they enforce nothing.
   Measured on the census bundle, they were 520 MB of a 1,022 MB store, and
   no read benchmark changed without them. `LocalRegistry.build(bundle,
   previous=token)` puts them back on the copy it extends. A reader MUST NOT
   rely on any key or index in a published store.

   It never deletes a file from `data/`. `tiers` maps
   a path prefix to a placement tier, the longest matching prefix winning:
   `{"search/": 2}`. A publication file no prefix matches is tier 1 if it is
   `meta.duckdb` or `lake.ducklake` and tier 2 otherwise; a data file is
   always tier 3. It inventories the
   publication's files and every data file its catalog references, counts
   each binding's rows through DuckLake, records the engine versions and
   every artefact, and writes the manifest to a
   temporary file renamed into place, so a reader never sees half of one. A
   data file the previous publication's manifest lists at the same size keeps
   that manifest's `sha256`, with `carried_from` naming its token, so a
   republish hashes only the files it wrote.
5. Run `check_bundle` on the result.

A publication is published once: `write_manifest` refuses one that already
has a manifest, and a change is a new publication. A builder that wrote its
own `bundle.manifest.json` before this format MUST republish with
`write_manifest`, moving its own fields into `product`
([Upgrading](upgrading.md#sdmxlib-owns-bundlemanifestjson)).

sdmxlib also produces bundles itself:

- `fetch_bundle(source, flow, path, key=..., start=..., end=...)` fetches one
  dataflow from an SDMX REST source, narrowed by a key and periods, with its
  structural closure, and records `Provenance`. `sdmxlib bundle fetch` runs
  it from a shell.
- `sdmxlib.testing.synthetic_bundle(path, shape, seed=...)` builds a bundle
  of a given `Shape` deterministically from its seed, for tests and
  benchmarks. A `Dim` may carry `HierarchyShape`s: each becomes a `Hierarchy`
  over the dimension's codelist and a `HierarchyAssociation` linking it to
  the dimension.
- `sdmxlib.testing.profile_bundle(path, flow)` reads the `Shape` of one
  dataflow in a real bundle (counts and fan-outs, never values), each
  dimension's associated hierarchies included, so a synthetic bundle can
  stand in for it.

## 8. Copying a publication

`copy_bundle(source, destination)` copies a published publication, and
`sdmxlib bundle copy SRC DST` runs it from a shell. It is how a server
receives a bundle and how a mirror is made. It copies the files the manifest
lists, the data files into the `data_path` beside `destination`, checks each
against its `size`, `sha256` and `md5`, and copies the manifest last, byte for byte.
A data file already at the destination with the listed digest is kept, so
receiving the next publication of a bundle copies only its new data files. An
interrupted copy leaves a directory without a manifest, which readers refuse
and `check_bundle` reports as unpublished; running the copy again completes
it.

`tiers=` (`--tiers 1,2` from a shell) copies only the files of those placement
tiers, which is what a server holds when it reads tier 3 remotely. Such a copy
is incomplete by intent: `check_bundle` reports the files it left out.

## 9. Fingerprints

A token names a publication; a fingerprint names its content. Two
publications with equal content have equal fingerprints whatever their
tokens, file bytes, file layout or DuckDB release, so a result cache keys on
fingerprints and never on a token. `write_manifest` computes all four.

| fingerprint | over | value |
| --- | --- | --- |
| table, `BindingCount.fingerprint` | every row of the binding's table | the sum over rows of each row's MD5 (the lower 64 bits, DuckDB's `md5_number_lower`) modulo 2^62, then `:` and the row count. The row's text is DuckDB's `to_json` of the row, columns in table order |
| artefact, `ArtefactEntry.fingerprint` | the artefact's canonical text (below) | its SHA-256 |
| structure, `BindingCount.structure_fingerprint` | the dataflow's structural closure | the SHA-256 of the closure's artefact fingerprints in URN order, joined by `\n` |
| bundle, `content_fingerprint` | every artefact and every binding | the SHA-256 of `artefacts:`, each artefact fingerprint in URN order, `bindings:`, then `<dataflow URN>\t<table fingerprint>` per binding in dataflow order, joined by `\n` |

The table fingerprint does not move with row order, and moves when a value
moves between rows, a row is duplicated, or any value changes. In SQL:

```sql
SELECT coalesce(sum((md5_number_lower(to_json(t)) % 4611686018427387904)::HUGEINT), 0)::VARCHAR
    || ':' || count(*)::VARCHAR
FROM lake.<schema>.<table> AS t
```

A dataflow's **structural closure** is what the dataflow and its data
structure refer to, transitively; every artefact that refers to either (a
constraint, a provision agreement, a hierarchy association: these point at
the dataflow, not the reverse); and what those refer to, transitively. A
reference to an item counts as one to the artefact holding it.

### Canonical text of an artefact

An artefact fingerprint hashes the artefact's canonical text, which
`sdmxlib.bundle.canonical_json` produces. Two artefacts with equal content
give byte-equal text, and a change to any field the model holds changes it.
The rules below are canonical form `1`, `sdmxlib.bundle.CANONICAL_FORM`; a
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
