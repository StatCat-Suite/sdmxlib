# sdmxlib.storage

## Overview

`sdmxlib.storage` persists SDMX structural metadata in DuckDB. `schema.py`
defines the relational schema in the `sdmx` namespace. `writers.py` writes
domain artefacts with `put_*` functions, which first delete the artefact's
existing rows by type. `readers.py` rebuilds artefacts eagerly, and `lazy.py`
rebuilds them on demand; both use the shared queries in `resolve.py`. The
package also handles the bundle manifest (`_manifest.py`) and the trimmed copy
of the store that publication ships (`_trim.py`).

## Interface

- `create_schema` (`schema.py`): creates the `sdmx` schema and runs the DDL.
  Calling it twice is safe. It upserts `schema_version` into `_meta` and
  returns a cursor with `search_path='sdmx'`. `SCHEMA_VERSION` is the current
  version, which statcatlib also reads.
- `migrate` (`schema.py`): drops the `sdmx` schema with `CASCADE` and calls
  `create_schema` again.
- `schema_exists`, `get_schema_version` and `meta_value` (`schema.py`): read
  `_meta`.
- `check_scan_integrity`, `ScanIntegrityError`, `StoreSchemaError` and
  `ReadOnlySchemaError` (`schema.py`): store integrity checks and their errors.
- `sdmx_cursor`, `use_sdmx`, `sdmx_catalog`, `connection_is_read_only` and
  `holds_rows` (`schema.py`): helpers for connections and catalogs.
- `put_codelist`, `put_value_list`, `put_data_structure`, `put_dataflow`,
  `put_concept_scheme`, `put_hierarchy`, `put_data_constraint`,
  `put_category_scheme`, `put_categorisation`, `put_provision_agreement`,
  `put_agency_scheme` and the other `put_*` functions (`writers.py`): write one
  artefact. Each takes an optional `source_registry=`. `put_version_rewrite`
  records an artefact's original version in `version_rewrite`.
- `put_artefact` (`writers.py`): the dispatching writer `LocalRegistry` calls.
  It picks the writer up the class's MRO, stores a registered class without a
  dedicated writer as spine and text only, and raises `TypeError` for a class
  the store does not persist. `drop_artefact` deletes an artefact and its child
  rows and returns whether it existed.
- `put_availability(conn, dataflow_urn, availability)` (`writers.py`): stores
  a dataflow's `AvailabilityConstraint`, keyed by the dataflow URN, in place of
  any earlier one. `clear_availability` deletes every stored availability.
- `get_artefact(conn, urn, *, eager=, lazy_conn=, refused=)` (`readers.py`):
  rebuilds a stored artefact from its URN. A codelist or hierarchy comes back
  as a `LazyCodelist` or `LazyHierarchy` unless `eager=True`. An item URN
  (a `Code`, `Category`, `Dimension`) is answered through the scheme that
  holds it. `refused=` collects refusals in place of raising.
  `urns_of_type` lists the stored URNs of one class. `make_lazy_codelist` and
  `make_lazy_hierarchy` build lazy wrappers. `code_map_for_dsd` returns code
  maps for a DSD's components, and `get_availability` a dataflow's
  availability constraint.
- `LazyCodelist` and `LazyHierarchy` (`lazy.py`): store-backed facades whose
  walks return a `Query`.
- `codes_by_urn`, `codes_by_ids`, `resolve_code_keys`, `hcodes_by_urn`,
  `read_levels`, `build_representation`, `istring_sql`, `json_to_istring`,
  `annotations_for_urns` and `links_for_urns` (`resolve.py`): batch resolvers
  that turn key rows into domain objects for readers and the path language.
- `checked` (`_checked.py`): wraps an artefact build and turns a
  `ValidationError` into an `SdmxConformanceError` carrying one `Refusal`.
- `compact_for_publication` and `restore` (`_trim.py`): drop primary keys and
  indexes from `TRIMMED_TABLES` for publication, and add them back where
  missing.
- `Manifest`, `read_manifest`, `encode_manifest`, `refuse_engine_mismatch` and
  `storage_version_of` (`_manifest.py`): the msgspec bundle manifest and the
  DuckDB storage-version checks. The module also defines `ManifestError`,
  `StoreFormatError`, `MANIFEST_NAME`, `FORMAT_VERSION`, `DATA_PATH`,
  `DUCKLAKE_SPEC_VERSION`, `publication_storage_version`,
  `engine_storage_version` and `generate_schema` (the manifest's JSON Schema).
- `ARTEFACT_KINDS`, with `CLASS_TO_NAME`, `NAME_TO_CLASS`, `ARTEFACT_TYPES` and
  `COMPONENT_TYPES` derived from it (`_kinds.py`): the single
  class-to-`artefact_type` registry. `writers.py`,
  `readers.py`, `local/registry.py` and `query/accessors.py` read it.
- `parsed` (`_parsed.py`): a process-wide `ParsedStatements` cache of parsed
  DuckDB statements for fixed store-read texts, bounded at `MAX_TEXTS = 256`
  and single-flight through `SingleFlight`.
- `table_columns` and `value_counts` (`_columns.py`): column-level reads of a
  bound table. `observations_sql` and `distinct_periods` (`_synthetic.py`):
  the SQL behind `sdmxlib.testing`'s synthetic bundles.
- `unresolved_references` and `Unresolved` (`_references.py`): find dangling
  references between artefacts. `code_map` (`_projections.py`): code-to-label
  projection.

## Invariants

- `SCHEMA_VERSION` (`schema.py`, currently `"2.51"`) is written to
  `sdmx._meta` with `INSERT OR REPLACE`. statcatlib reads it, so changing it
  affects that repo.
- `create_schema` drops lines that start with `--`, splits the DDL on `;`, and
  runs each statement. A semicolon inside a statement would break that split.
- Every TIMESTAMP is stored as naive UTC, and readers tag it UTC again
  (`sdmxlib/_time.py`).
- Values are bound as parameters (`?` or `$name`), with these exceptions.
  `code_map` (`_projections.py`) and `code_map_for_dsd` (`readers.py`)
  interpolate the caller's `lang` into the SQL text (GH-262).
  `_trim.compact_for_publication` interpolates file paths and the storage
  version into `ATTACH`. `use_sdmx`, `holds_rows`, `_trim._rebuild`,
  `_delete_artefact_cascade`, `_columns` and `_synthetic` interpolate
  identifiers and computed integers.
- Before a rewrite, a writer deletes the artefact's rows with deletes chosen by
  artefact type. When the artefact is not stored, the deletes are skipped.
- Every eager build passes through `checked()`. `get` raises on a bad
  artefact; a bulk read records it in `LocalRegistry.refused` and carries on.
  `get_artefact` returns a lazy facade unchecked; the facade runs `checked()`
  when it materialises or when `partial()` builds a subset.
- Compaction keeps the source store's DuckDB `storage_version` (the
  `STORAGE_VERSION` option of `ATTACH`).
- `migrate` deletes all stored data. Call it only on an empty store or with
  `rebuild=True`.

## Side Effects

- `create_schema` runs `CREATE SCHEMA` and `CREATE TABLE` DDL, sets
  `search_path='sdmx'` and upserts `_meta`.
- `migrate` runs `DROP SCHEMA sdmx CASCADE`, which deletes all stored data.
- `put_*` writers `DELETE` the artefact's rows, then `INSERT` the new ones.
  The spine write (`_put_spine`) stamps `fetched_at` when `source_registry` is
  given, and issues a `RuntimeWarning` when it overwrites a row that carries a
  different `source_registry`; the last write wins.
- `refuse_engine_mismatch` creates and opens a throwaway DuckDB file in a
  temporary directory on every call (`engine_storage_version`).
- `readers.py`, `lazy.py` and `resolve.py` run only `SELECT`s.
- `compact_for_publication` attaches the store read-only, copies it through
  `.trim` and `.new` files, replaces the original `meta.duckdb`, and deletes the
  temporary file.
- `restore` rebuilds tables and runs `CREATE INDEX` on an open store when the
  trimmed keys or indexes are missing.

## Verification

- Run: `just test` (unit tests) and `just check` (lint and typecheck).
- The real-bundle tier in `just ci` reads the published census bundle through
  these readers.
