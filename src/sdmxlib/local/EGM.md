# sdmxlib.local

## Overview

`sdmxlib.local` is the persistent SDMX store, built on DuckDB. `EditionRegistry`
reads a sealed edition (`metadata.duckdb` plus a DuckLake `tables.ducklake`)
read-only. `LocalRegistry` extends it with writes and starts new editions.
`EditionEngine` lets many editions share one in-memory DuckDB connection by
attaching each one under its own catalog prefix. `DuckDBDataStore` is the data
plane: it attaches the catalogs, serves observation reads and takes writes.

## Interface

- `EditionRegistry` (`registry.py`): read-only registry over an edition. Methods
  include `get`, `get_all`, `codelist`, `artefacts`, `query`, `obs`, `dsd`,
  `binding_for`, `availability`, `browse`, `metadata_report`, `code_map`,
  `dsd_code_map`, `reported_attributes`, `metadata_for_target`,
  `applicable_hierarchies`, `unique_hierarchy`, `check_binding_drift`,
  `validate_binding`, `locate`, `clear_resolve_cache`, `resolve_cache_info` and
  `to_registry`, and the properties `refused`, `manifest`, `data_path` and
  `lake_alias`.
- `EditionRegistry.__init__(uri, *, source_registry=, resolve_cache_items=,
  target=, rebuild=)` (`registry.py`): `rebuild=True` lets opening drop and
  rebuild a store on another schema version; `target=` names a format from
  `formats.gaps.TARGETS`, and `add` then refuses content that format cannot
  represent.
- `EditionRegistry.bundle(...)` (`registry.py`): opens a sealed edition
  read-only and attaches its lake on first use. It reads the manifest first
  and refuses a schema-version mismatch (`StoreSchemaError`) and a DuckDB
  storage or DuckLake format newer than this build reads (`StoreFormatError`,
  through `refuse_engine_mismatch`). It returns a `EditionRegistry`, also when
  called as `LocalRegistry.edition`.
- `EditionRegistry.from_connection(...)` (`registry.py`): wraps a DuckDB
  connection the caller owns. The caller keeps its lifecycle.
- `LocalRegistry` (`registry.py`): `EditionRegistry` plus writes: `add`,
  `add_all`, `drop`, `replace`, `ingest`, `write_observations`, `sql`,
  `scan_csv`, `scan_json` and `transaction`. A `MetadataSet` added beside its metadataflow and MSD is checked against them (IM §7.4.2.1,
  GH-29): one `ImRuleWarning` per call names targets outside the flow's and attributes
  the MSD does not define or allow that often (`validation.metadata_sets_advisories`).
- `LocalRegistry.build(...)` (`registry.py`): creates an edition directory
  `<product>/<edition_id>` (a UUIDv7) over the shared `<product>/data/`. Takes
  `previous=` (copying the previous edition's `lazy`-tier files, or the
  prefixes `carry=` names) or `resume=`. It refuses to start an edition while
  the product has an unsealed one. `resume=` reopens an unsealed edition and raises
  `ManifestError` for a sealed one or one `build` did not start. The
  `edition_dir` property returns the edition directory.
- `EditionEngine` (`engine.py`): shared in-memory DuckDB engine. Provides
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
  query; bytes and SDMX-JSON are a table loaded once. `scan_csv_into` and
  `DuckDBDataStore.ingest_csv` (a REST `obs()`) refuse SDMX-CSV holding a
  `D` (delete) row with `SdmxParseError`, dropping the relation they made
  (`data_store.refuse_deletions`, one pass at scan time; GH-284).
- Package exports (`__init__.py`): `EditionRegistry`, `LocalRegistry`,
  `QueryPlan`, `SchemaInspector`, `LazyCodelist` and `LazyHierarchy`.

## Invariants

- `EditionRegistry._READ_ONLY` is `True` and `LocalRegistry._READ_ONLY` is
  `False`. A registry opened on a file path connects with
  `duckdb.connect(uri, read_only=_READ_ONLY)`. A `bundle()` registry runs on
  `engine.cursor(prefix)` inside a `EditionEngine`.
- Opening runs `_initialise_schema`. A store with no `sdmx` schema, or on
  another schema version and holding no rows or opened with `rebuild=True`, is
  rebuilt by `migrate` (`DROP SCHEMA sdmx CASCADE`). A store on another
  version that holds rows raises `StoreSchemaError`; a read-only connection
  that needs a rebuild raises `ReadOnlySchemaError`. A current schema runs
  `check_scan_integrity`.
- The lake is attached as `ducklake:<root>/tables.ducklake` with `DATA_PATH` and
  `OVERRIDE_DATA_PATH true`. A read-only class also adds `READ_ONLY`.
- Every read cursor is a `conn.cursor()` passed through `use_sdmx(...,
  catalog)` (`storage/schema.py`), because a derived cursor does not inherit
  `USE`. Read cursors are cached per thread through `threading.local`.
- `EditionEngine` attaches each edition's `metadata.duckdb` as `<prefix>__metadata`
  (`READ_ONLY`), where the prefix is `<product_id>__<edition_id>`. Unsafe characters
  become `_` in `product_id` and are removed from `edition_id`, so a UUID edition id loses
  its hyphens. Attachments are
  refcounted, and when the count reaches 0 the engine detaches `__tables` and
  `__metadata`. Attaching an existing prefix from a different meta file raises
  `ValueError`.
- Read-only stores and the engine set `temp_directory` to `spill_directory`, so
  DuckDB never spills inside an edition.
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
  runs `refuse_engine_mismatch` on the previous edition, and requires its
  data path to be `DATA_PATH`; any other path raises `ManifestError`.

## Side Effects

- `LocalRegistry.build` creates `<product>/<edition_id>` and `<product>/data`. With
  `previous=` it copies `metadata.duckdb` and `tables.ducklake` into the new
  edition and runs `restore` to add back the trimmed keys and indexes. A
  new edition rewrites `edition_id` and `previous` in
  `sdmx._meta`.
- Opening a registry can rewrite the store: `migrate` drops and recreates the
  `sdmx` schema, deleting its data.
- Opens DuckDB connections: file-backed ones in the registry and data store,
  and an in-memory one in `EditionEngine`.
- Runs `ATTACH` and `DETACH` for the meta and DuckLake catalogs. `_scan.py`
  attaches `:memory:` as the `sdmx_scans` catalog. Scan relations are never
  dropped and live until the connection closes. Scanned bytes are written to a
  temporary file and read from there.
- Installs and loads DuckDB extensions, and creates secrets for object stores,
  Azure among them. `DuckDBDataStore._needs_azure` is true for any `ducklake:`
  attach spec, by design: a lake's data path may route through Azure unseen in
  the `ATTACH` text. So every edition and `build()` registry runs
  `INSTALL azure`/`LOAD azure` and sets the curl transport.
- Holds `threading.Lock`s in the engine, registry, resolve cache, data store
  and inspector.
- Writes DuckDB spill files to the per-process directory under the system temp
  directory.

## Verification

- Run: `just test` (unit tests) and `just check` (lint and typecheck).
- The real-bundle tier runs in `just ci` (`bench-bundle-assertions`) when
  `$SDMXLIB_BENCH_BUNDLE` names an edition.
