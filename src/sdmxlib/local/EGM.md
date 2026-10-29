# sdmxlib.local

## Overview

`sdmxlib.local` is the persistent SDMX store, built on DuckDB. `BundleRegistry`
reads a published bundle (`meta.duckdb` plus a DuckLake `lake.ducklake`)
read-only. `LocalRegistry` extends it with writes and starts new publications.
`BundleEngine` lets many bundles share one in-memory DuckDB connection by
attaching each one under its own catalog prefix. `DuckDBDataStore` is the data
plane: it attaches the catalogs, serves observation reads and takes writes.

## Interface

- `BundleRegistry` (`registry.py`): read-only registry over a bundle. Methods
  include `get`, `get_all`, `codelist`, `artefacts`, `query`, `obs`, `dsd`,
  `binding_for`, `availability`, `browse`, `metadata_report`, `code_map`,
  `dsd_code_map`, `reported_attributes`, `metadata_for_target`,
  `applicable_hierarchies`, `unique_hierarchy`, `check_binding_drift`,
  `validate_binding`, `locate`, `clear_resolve_cache`, `resolve_cache_info` and
  `to_registry`, and the properties `refused`, `manifest`, `data_path` and
  `lake_alias`.
- `BundleRegistry.__init__(uri, *, source_registry=, resolve_cache_items=,
  target=, rebuild=)` (`registry.py`): `rebuild=True` lets opening drop and
  rebuild a store on another schema version; `target=` names a format from
  `formats.gaps.TARGETS`, and `add` then refuses content that format cannot
  represent.
- `BundleRegistry.bundle(...)` (`registry.py`): opens a published bundle
  read-only and attaches its lake on first use. It reads the manifest first
  and refuses a schema-version mismatch (`StoreSchemaError`) and a DuckDB
  storage or DuckLake format newer than this build reads (`StoreFormatError`,
  through `refuse_engine_mismatch`). It returns a `BundleRegistry`, also when
  called as `LocalRegistry.bundle`.
- `BundleRegistry.from_connection(...)` (`registry.py`): wraps a DuckDB
  connection the caller owns. The caller keeps its lifecycle.
- `LocalRegistry` (`registry.py`): `BundleRegistry` plus writes: `add`,
  `add_all`, `drop`, `replace`, `ingest`, `write_observations`, `sql`,
  `scan_csv`, `scan_json` and `transaction`.
- `LocalRegistry.build(...)` (`registry.py`): creates a publication directory
  `<bundle>/<uuid7 token>` over the shared `<bundle>/data/`. Takes `previous=`
  or `resume=`. `resume=` reopens an unpublished publication and raises
  `ManifestError` for a published one or one `build` did not start. The
  `publication` property returns the publication directory.
- `BundleEngine` (`engine.py`): shared in-memory DuckDB engine. Provides
  refcounted `attach`/`release`, `cursor(prefix)`, `load_for_uri`, `close`,
  and the properties `connection` and `attached`.
  The package imports it but leaves it out of `__all__`.
- `spill_directory` (`engine.py`): the per-process spill directory under the
  system temp directory.
- `DuckDBDataStore` (`data_store.py`): the data-plane store. Provides `open`,
  `from_registry`, `inspect`, `obs`, `ingest_csv`, `write_observations` and
  `close`. `store_path` and `column_types` are module-level helpers.
- `DuckLakeVersionError` (`data_store.py`): raised by `ATTACH` when a DuckLake
  catalog's format is newer than the installed extension supports, matched on
  DuckDB's error text. `bundle()` usually refuses such a catalog earlier, with
  `StoreFormatError` from the manifest.
- `SchemaInspector`, `QueryPlan` and `SidecarNotUniqueError` (`sql.py`):
  `SchemaInspector` reflects columns, types, keys and partitions and caches
  them. `plan(binding)` returns a `QueryPlan`, the FROM clause for a
  `DataflowBinding`. `SidecarNotUniqueError` is a `ValueError` raised when
  sidecar keys repeat.
- `StoreSecret`, `data_uri`, `extension_for`, `load_for` and `sizes`
  (`object_store.py`): object-store secrets (`StoreSecret.statement()`), URIs
  and DuckDB extensions.
- `scan_csv_into`, `scan_frame_into` and `resolve_flow` (`_scan.py`): a
  scanned file's rows as a relation in the attached `:memory:` catalog
  `sdmx_scans` (`_read_errors.SCANS`). A path is a view DuckDB reads on each
  query; bytes and SDMX-JSON are a table loaded once.
- Package exports (`__init__.py`): `BundleRegistry`, `LocalRegistry`,
  `QueryPlan`, `SchemaInspector`, `LazyCodelist` and `LazyHierarchy`.

## Invariants

- `BundleRegistry._READ_ONLY` is `True` and `LocalRegistry._READ_ONLY` is
  `False`. A registry opened on a file path connects with
  `duckdb.connect(uri, read_only=_READ_ONLY)`. A `bundle()` registry runs on
  `engine.cursor(prefix)` inside a `BundleEngine`.
- Opening runs `_initialise_schema`. A store with no `sdmx` schema, or on
  another schema version and holding no rows or opened with `rebuild=True`, is
  rebuilt by `migrate` (`DROP SCHEMA sdmx CASCADE`). A store on another
  version that holds rows raises `StoreSchemaError`; a read-only connection
  that needs a rebuild raises `ReadOnlySchemaError`. A current schema runs
  `check_scan_integrity`.
- The lake is attached as `ducklake:<root>/lake.ducklake` with `DATA_PATH` and
  `OVERRIDE_DATA_PATH true`. A read-only class also adds `READ_ONLY`.
- Every read cursor is a `conn.cursor()` passed through `use_sdmx(...,
  catalog)` (`storage/schema.py`), because a derived cursor does not inherit
  `USE`. Read cursors are cached per thread through `threading.local`.
- `BundleEngine` attaches each bundle's `meta.duckdb` as `<prefix>__meta`
  (`READ_ONLY`), where the prefix is `<bundle_id>__<token>`. Unsafe characters
  become `_` in `bundle_id` and are removed from `token`, so a UUID token loses
  its hyphens. Attachments are
  refcounted, and when the count reaches 0 the engine detaches `__lake` and
  `__meta`. Attaching an existing prefix from a different meta file raises
  `ValueError`.
- Read-only stores and the engine set `temp_directory` to `spill_directory`, so
  DuckDB never spills inside a bundle.
- Reads are thread-safe. `LocalRegistry` writes are single-threaded.
- Resolved `Ref` targets go into a bounded FIFO cache (`_FIFOCache`, sized by
  `resolve_cache_items=`, `0` disables it) with lock-free reads. Every write
  clears it, and inside `transaction()` it is cleared once at exit.
- A scan refuses a file without a column its binding reads
  (`_require_columns`, `SdmxParseError`), and reports what DuckDB cannot read
  as `SdmxParseError` and a value that does not fit as the type `ValueError`
  (`reading_csv`). `scan_frame_into` reports a polars cast failure through
  `type_mismatch`.
- `build()` refuses `previous=` and `resume=` together. With `previous=` it
  runs `refuse_engine_mismatch` on the previous publication, and requires its
  data path to be `DATA_PATH`; any other path raises `ManifestError`.

## Side Effects

- `LocalRegistry.build` creates `<bundle>/<token>` and `<bundle>/data`. With
  `previous=` it copies `meta.duckdb` and `lake.ducklake` into the new
  publication and runs `restore` to add back the trimmed keys and indexes. A
  new publication rewrites `publication_token` and `previous_token` in
  `sdmx._meta`.
- Opening a registry can rewrite the store: `migrate` drops and recreates the
  `sdmx` schema, deleting its data.
- Opens DuckDB connections: file-backed ones in the registry and data store,
  and an in-memory one in `BundleEngine`.
- Runs `ATTACH` and `DETACH` for the meta and DuckLake catalogs. `_scan.py`
  attaches `:memory:` as the `sdmx_scans` catalog. Scan relations are never
  dropped and live until the connection closes. Scanned bytes are written to a
  temporary file and read from there.
- Installs and loads DuckDB extensions, and creates secrets for object stores,
  Azure among them. `DuckDBDataStore._needs_azure` is true for any `ducklake:`
  attach spec, by design: a lake's data path may route through Azure unseen in
  the `ATTACH` text. So every bundle and `build()` registry runs
  `INSTALL azure`/`LOAD azure` and sets the curl transport.
- Holds `threading.Lock`s in the engine, registry, resolve cache, data store
  and inspector.
- Writes DuckDB spill files to the per-process directory under the system temp
  directory.

## Verification

- Run: `just test` (unit tests) and `just check` (lint and typecheck).
- The real-bundle tier runs in `just ci` (`bench-bundle-assertions`) when
  `$SDMXLIB_BENCH_BUNDLE` names a publication.
