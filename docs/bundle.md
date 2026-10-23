# The bundle format

A bundle is a directory holding SDMX artefacts and observations, published as
one unit, once per release: each publication is a directory of its own.
statcatlib writes bundles, sdmxlib-rest serves them, and sdmxlib reads,
checks, copies and fetches them. This page is the contract between them.

It describes **manifest format 2.0** and **overlay format 1.0**. sdmxlib's
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
  leave the catalog out. DuckLake stores a small table inside
  `lake.ducklake` itself, so a publication MAY reference no data file even
  when the lake holds rows.
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
| `data_path` | required | Where the data files live, relative to the publication directory: `"../data/"`. |
| `files` | required | Every file in the publication directory except the manifest, and every data file the lake catalog references, as `BundleFile`s sorted by `root`, then `path`. |
| `bindings` | required | One `BindingCount` per bound dataflow, sorted by `dataflow`. |
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
| `root` | required | `"publication"` for a file in the publication directory, `"data"` for a data file under `data_path`. |
| `carried_from` | optional | For a data file, the `token` of the manifest whose `size` and `sha256` this entry copies; `null` for one hashed by this publication. |

### `BindingCount`

| field | presence | meaning |
| --- | --- | --- |
| `dataflow` | required | The bound dataflow's URN. |
| `observations` | required | How many observation rows the binding's table holds. |

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
- DuckLake inlines small tables into `lake.ducklake`, so the same rows sit in
  different files depending on their count;
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

### The schema check

When a manifest is present, a reader MUST compare its `schema_version` with
the store schema it reads **before opening `meta.duckdb`**. If they differ it
MUST refuse the bundle and leave the file unopened. sdmxlib raises
`StoreSchemaError`; its readers accept one exact schema version,
`sdmxlib.storage.schema.SCHEMA_VERSION`. A bundle is never migrated in place:
it is rebuilt from its source with a matching sdmxlib.

### Bindings

Each dataflow with observations has a `DataflowBinding` artefact in
`meta.duckdb`. The binding names the table, as `table_location`
(`schema.table`) inside the database `database_role`, and maps each SDMX
component to its column.

`LocalRegistry.bundle` attaches `lake.ducklake` under the alias `lake`, which
is `database_role`'s default, when observations are first read. Opening a
bundle to read artefacts alone never attaches the lake.

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
| `format_version` | required | The overlay format, `major.minor`: `"1.0"` for this page. |
| `id` | required | The overlay's name. |
| `entries` | required | `OverlayEntry`s, in document order. |

#### `OverlayEntry`

| field | presence | meaning |
| --- | --- | --- |
| `selector` | required | Which artefacts and items it applies to (§5.2). |
| `op` | required | `"set"`, `"add"` or `"remove"` (§5.3). |
| `type` | required | The annotation type it acts on. |
| `annotation` | optional | The annotation `set` and `add` write. It MUST be present for `set` and `add`, and absent or `null` for `remove`. |
| `scope` | optional | A label carried through and not interpreted. A server MAY use it to choose which entries apply to which audience. |

#### `OverlayAnnotation`

The annotation's type is the entry's `type`.

| field | presence | meaning |
| --- | --- | --- |
| `id` | optional | The annotation id. |
| `title` | optional | The annotation title. |
| `value` | optional | The annotation value. |
| `text` | optional | The annotation text, as an object from language tag to string. |

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

sdmxlib's merge hook is a function per annotation type, passed as `merge=`.
An implementation without a hook MUST replace.

### 5.4 The effective token

A server that applies overlays at serve time MUST key its caches on the
effective token:

- with no overlays, it is the manifest's `token`;
- otherwise it is the lowercase hex SHA-256 of the UTF-8 text formed by
  joining, with `\n`, the manifest's `token` and then each overlay's digest,
  in the order the overlays are applied.

An overlay's digest is the lowercase hex SHA-256 of its canonical JSON. That
JSON has no whitespace and sorts the keys of every object. Every field
appears, with `null` for an absent optional and `{}` for an empty `text`.
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

Changing, adding, removing or reordering an overlay changes the effective
token, and so does republishing the bundle.

### 5.5 Folding overlays into a bundle

`fold_overlays` applies overlays to the store for good. It starts a new
publication extending the one it reads, replaces every artefact an entry
changes in the new `meta.duckdb`, and publishes it. The new manifest keeps
`bundle_id`, `producer`, `product`, `extensions` and `provenance`, and has a
new `token` and the folded publication's as `previous_token`. The folded
publication is left as it was. After folding, a server no longer applies
those overlays.

## 6. Conformance

`check_bundle(path)` checks a published publication, and the `sdmxlib bundle check
PATH` command runs it from a shell (`uv tool install sdmxlib-cli`). The
command prints each finding and exits 1 if there is any. It runs these checks
in order and reports every finding, not only the first:

| check | passes when | on failure |
| --- | --- | --- |
| `manifest` | the manifest parses, in a format this reader reads (§2) | the run stops |
| `files` | every listed file is present with its `size` and `sha256`, and no unlisted file is in the publication directory; `data/` is shared, so only its listed files are checked | continues |
| `manifest` | the manifest's `schema_version` is the reader's, so `meta.duckdb` can be opened (§4) | the run stops |
| `lake` | every file the current snapshot references exists at the size DuckLake recorded, and every file the catalog references from any snapshot it keeps is in the manifest; runs only when `lake.ducklake` exists | continues |
| `bindings` | the store's bindings are the manifest's, and each table holds the count the manifest gives | continues |
| `references` | every reference in the store resolves to an artefact in the store; `references=False` or `--no-references` skips it | continues |

The `references` check loads every artefact. On a 2.6 GB census bundle it
takes about 30 s and 5 GB of memory, so it runs when a bundle is built or
received, not when one is opened to serve.

A package that owns part of a bundle MAY pass its own checks as
`extensions={name: check}`. Each runs after `references`, and its findings
carry its name.

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
   `product`, `extensions` and `provenance` as needed. It inventories the
   publication's files and every data file its catalog references, counts
   each binding's rows through DuckLake, and writes the manifest to a
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

## 8. Copying a publication

`copy_bundle(source, destination)` copies a published publication, and
`sdmxlib bundle copy SRC DST` runs it from a shell. It is how a server
receives a bundle and how a mirror is made. It copies the files the manifest
lists, the data files into the `data_path` beside `destination`, checks each
against its `size` and `sha256`, and copies the manifest last, byte for byte.
A data file already at the destination with the listed digest is kept, so
receiving the next publication of a bundle copies only its new data files. An
interrupted copy leaves a directory without a manifest, which readers refuse
and `check_bundle` reports as unpublished; running the copy again completes
it.

sdmxlib also produces bundles itself:

- `fetch_bundle(source, flow, path, key=..., start=..., end=...)` fetches one
  dataflow from an SDMX REST source, narrowed by a key and periods, with its
  structural closure, and records `Provenance`. `sdmxlib bundle fetch` runs
  it from a shell.
- `sdmxlib.testing.synthetic_bundle(path, shape, seed=...)` builds a bundle
  of a given `Shape` deterministically from its seed, for tests and
  benchmarks.
- `sdmxlib.testing.profile_bundle(path, flow)` reads the `Shape` of one
  dataflow in a real bundle (counts and fan-outs, never values), so a
  synthetic bundle can stand in for it.
