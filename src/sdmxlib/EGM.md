# sdmxlib

## Overview

`sdmxlib` fetches, models, stores and queries SDMX structural metadata,
reference metadata and observation data. It works over SDMX REST APIs, SDMX
files, or a local DuckDB store. `__init__.py` re-exports the public API from
the subpackages in `__all__`: the registries, the model types, the query
language (`sl.q`, `Col`, `Agg`, `CompiledQuery`, `RawSql`, `sl.schema`), the
format helpers (`DataFormat`, `Gap`, `dropped`), `DataStore`, `Session`,
`ValueSet` and the version-rewrite types. It also defines the `sl.len`
aggregate itself. The modules directly in this directory hold infrastructure
the subpackages share.

## Interface

- `Session`, `SessionFactory` and `ValueSet` (`session.py`): one DuckDB
  session per thread, per store. A `Session` has `execute`, `value_set` and
  `handle`. `SessionFactory` is the mixin `LocalRegistry` and `DuckDBDataStore`
  use, providing `session()` (nested sessions are counted), `execute`,
  `on_connect` and `open_sessions`. `SessionHook` is the type of an
  `on_connect` callback. The internal helpers are `cursor_for`, `own_handle`,
  `adopt`, `frame_of`, `value_match`, the module-level
  `on_connect(base, fn)` and `handle_count(base)` (live handles, for leak
  hunting), and `VALUES_RELATION_PREFIX` (`"_sl_values_"`).
- `SqlRelation` and `HasSource` (`sql.py`): protocols for something that
  splices into `is_in` (`columns`, `to_sql() -> (sql, params)`) and for
  something that names where it reads (`source`). statcatlib implements
  `SqlRelation`.
- `DataStore` and `WriteSource` (`data_store.py`): the protocol for the write
  side of the data plane, `write_observations(*, schema, table, source,
  partition_by=(), mode="overwrite" | "append", lake_alias="lake")`, all
  keyword-only. Reads go through `obs()`.
- `ComponentFilter`, `widen_key` and `parse_components` (`rest.py`): turn a
  REST key and component parameters into SQL `WHERE` clauses.
- `schema`, `label`, `validation_report`, `structure_frame` and
  `SupportsCodeMap` (`polars.py`): the Polars bridge. Builds a dtype schema
  from a DSD (`coded_as="enum"`), labels codes, and builds validation reports
  and structure frames.
- `SingleFlight` (`_singleflight.py`): `run(key, work)` runs `work` once per
  key, and a bounded FIFO remembers completed keys. `clear()` empties it.
  `RestRegistry`'s query fetches (`api/registry.py`) and `storage/_parsed.py`
  use it. These runtime caches do not: `_FIFOCache` (`local/registry.py`),
  `SdmxClient._freshness` and `RestRegistry._settled` (bounded FIFO dicts),
  the unbounded `functools.cache` in `api/federated.py`, and the
  `functools.lru_cache` in `model/urn.py`. A new cache goes through
  `SingleFlight` (`docs/design/caching.md`).
- `to_storage`, `from_storage` and `as_utc` (`_time.py`): store every
  TIMESTAMP as naive UTC.
- `reading_csv`, `csv_errors`, `converting`, `reads_scan`, `type_mismatch`
  and `SCANS` (`_read_errors.py`): what reading observations raises. Data
  that cannot be read is `SdmxParseError`; a value that does not fit its
  component's type is the one `ValueError` naming `.validate()`.
  `reading_csv` wraps a `read_csv` statement sdmxlib runs itself (a scan, a
  REST ingest); `csv_errors` and `converting` convert at a query terminal and
  in a drained stream, and only errors DuckDB's CSV reader raised.
- `first` and `count` (`_duckdb.py`): a cursor's first row with its result
  finished, and the row count from a cursor.
- `synthetic_bundle`, `profile_bundle`, `Shape`, `Dim` and `HierarchyShape`
  (`testing.py`): build a seeded synthetic bundle that passes `check_bundle`,
  or take a `Shape` from an existing bundle.
- `RewrittenVersion`, `VersionRewrite` and `strip_leading_zeros`
  (`versions.py`): re-exported from `model.version_rewrite` for
  `add_all(rewrite_version=...)`.
- Subpackages:
  - [`model/`](./model/EGM.md): the frozen attrs SDMX information model,
    `SdmxUrn`, `Ref[T]` and `InMemoryRegistry`.
  - [`formats/`](./formats/EGM.md): SDMX-ML 2.1/3.0/3.1, SDMX-JSON and
    SDMX-CSV readers and writers, and the format gap table.
  - [`storage/`](./storage/EGM.md): the DuckDB schema, writers, readers, lazy
    facades and publication trimming.
  - [`local/`](./local/EGM.md): `BundleRegistry`, `LocalRegistry`,
    `BundleEngine` and `DuckDBDataStore` over DuckDB and DuckLake.
  - [`query/`](./query/EGM.md): the `sl.q` path language, compiled to
    parameterised DuckDB SQL; `ObsQuery`; and raw SQL.
  - [`api/`](./api/EGM.md): `RestRegistry`, `FmrRegistry`,
    `FederatedRegistry`, `Provider` and the httpx2 client.
  - [`bundle/`](./bundle/EGM.md): `write_manifest`, `check_bundle`,
    `copy_bundle`, `build_bundle`, `fetch_bundle`, overlays and
    fingerprints.
  - [`builders/`](./builders/EGM.md): availability constraints and DSD
    metadata annotations, computed from a bundle.

## Invariants

- Layering: `model/` imports nothing from `formats/` and does no I/O.
  `formats/` owns all XML and JSON. `storage/` and `local/` own DuckDB. `api/`
  owns HTTP.
- Each thread holds one DuckDB cursor per base connection, kept in a
  thread-local `WeakKeyDictionary` and reused across calls. `own_handle`
  creates a separate handle for each reader that holds one until exhausted,
  such as an Arrow stream.
- A `Session` is valid only inside its `with` block. Nested sessions share the
  handle. The outermost block releases it, and the next session gets a fresh
  cursor.
- Every read in `src/` finishes its own `execute()` result: a single-row
  read goes through `_duckdb.first`, and no `fetchone()` or `fetchmany()`
  appears elsewhere (`test_no_read_in_src_strands_its_result`). DuckDB 2.0
  refuses a `sql`, `register` or `unregister` call on a handle holding an
  unfinished result, so a read that strands one raises at the next relation
  call on that thread's handle (GH-75).
- `SingleFlight` work must be idempotent: eviction runs it again, and a
  failure is not remembered, so waiters retry. A `maxsize` of 0 or less raises
  `ValueError`.
- Timestamps are stored as UTC with `tzinfo` removed. A naive input is assumed
  to be UTC already.
- `SqlRelation.to_sql()` returns `(sql, params)`, and values are bound as
  parameters.
- `synthetic_bundle` gives the same artefacts and observation rows for a given
  seed and shape, and writes its manifest through `write_manifest`. The token
  and the DuckDB file bytes differ on each build.

## Side Effects

- `session.py` creates DuckDB cursors on demand, keeps them in thread-local
  state, and runs the registered `on_connect` hooks on each new handle.
  `_hook_lock` guards the hook and handle bookkeeping; the hooks run outside
  it.
- `Session.value_set` registers temporary relations named `_sl_values_*` on
  the handle.
- `SingleFlight` uses `threading.Lock` and `threading.Event`. Waiters block
  until the leader finishes.
- `testing.synthetic_bundle` creates directories, writes observations and
  writes a manifest.
- `_read_errors` converts the exceptions raised by CSV reads and by
  `RecordBatchReader` iteration.

## Verification

- Run: `just check` (lint and typecheck) and `just test` (unit tests).
- `just ci` is the full gate and runs locally.
