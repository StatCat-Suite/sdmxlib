# EGM drift report: the EGM.md contracts against the code

Run on 2026-10-28 over the 23 `EGM.md` contracts the EGM bootstrap wrote that
day, before they were first committed. Six reviewers in fresh contexts each
compared a group of contracts with the source and reported only the
differences: **WRONG** (the contract states something false), **MISSING** (an
important omission) or **IMPRECISE** (true but misleading). The findings are
as the reviewers wrote them, with file and line evidence; the contracts were
corrected from them before the first commit, so the line numbers and quotes
refer to that first draft and to the code on `main` at `f9cd36cb`.

Findings that point at the code rather than the contract were filed as issues
and are not fixed here.

## src/sdmxlib/model/EGM.md

- [WRONG] "It depends on the standard library and attrs": model modules also import `typing_extensions` (`codelist.py:11`), `sdmxlib._time.as_utc` (16 modules, e.g. `codelist.py:13`, `header.py:27`), `sdmxlib.sql` (`_label_match.py`, `rollup.py:20`), `sdmxlib.session` (`rollup.py:19`), and `duckdb` and `polars` (`rollup.py:16-17`).
- [WRONG] "`SdmxConformanceError` ... (`errors.py`)": `SdmxConformanceError` is defined in `validation.py`, as is `LossyReadError`, which the contract never mentions (`validation.py:151`, `:252`).
- [WRONG] "`enforce()` ... emits `ImRuleWarning` for each advisory": it emits one `ImRuleWarning` per artefact carrying every broken advisory, and only after all rules pass; when a rule fails it raises and no advisories run (`_invariants.py:105-111`).
- [WRONG] "`Any` stays out of its public signatures": `from_dict(cls, data: dict[str, Any])` (`convert.py:303`), `Ref.__getattr__ -> Any` (`ref.py:281`), `ItemList.to_dict`/`ArtefactList.to_dict -> dict[Any, T]` (`collections.py:231`, `:473`) and `ItemExpr.is_in(values: Collection[Any])` (`expr.py:130`) use `Any`.
- [IMPRECISE] "A Ref keeps the resolver that was current when it was built": only the factories (`Ref.unresolved`, `Ref.of`, `Ref.to`, the `to_ref` converters) read `current()`; a bare `Ref(urn)` defaults `_resolver=None` and is never bound; `InMemoryRegistry.ref` binds its own resolver (`ref.py:212`, `:331`, `:336`, `:382`; `in_memory_registry.py:220`).
- [IMPRECISE] "`InMemoryRegistry` replaces an entry by URN in both `_store` and `_by_type`, so `get` and `get_all` agree": `_intern` writes only the new object's type bucket and never removes the URN from another type's, so a `GeographicCodelist` re-added over a `Codelist` of the same URN leaves the stale copy in `get_all(Codelist)` (`in_memory_registry.py:164-171`, `codelist.py:757`).
- [MISSING] `InMemoryRegistry._resolver`, a `MapResolver` with a runtime item-lookup cache cleared by every `_intern` through `forget_items()` (`_resolvers.py:28-45`, `in_memory_registry.py:61`, `:171`).
- [MISSING] `InMemoryRegistry.get`/`__contains__` answer item URNs through `resolve_item`; `get` takes `expect=`; the registry also has `applicable_hierarchies`, `unique_hierarchy`, `check_binding_drift` and `validate_binding` (`in_memory_registry.py:173-210`, `:240-300`).
- [MISSING] `SdmxRef` and the `Ref.resolved`/`Ref.bound` properties; `UnresolvedRef` is also raised by calling the Ref and when its registry is closed (`ref.py:257-279`, `:410`).
- [IMPRECISE] `ArtefactList` is indexed by int, slice, bare id, `"AG:ID"`, `"AG:ID(ver)"`, `SdmxUrn` or `SdmxRef`, not by an artefact, and is bounded `T: Maintainable` (`collections.py:285-330`).
- [IMPRECISE] "A `MaintainableArtefact` subclass declares `sdmx_package`, `sdmx_class` and `msg_field`" is a convention nothing enforces (`base.py:34-44`).
- [IMPRECISE] `_`-prefixed model modules are internal to sdmxlib, not to `sdmxlib.model`: `formats/`, `storage/`, `local/` and `bundle/` import them; `_label_match.py` holds SQL fragments used only by `query/` (`_label_match.py:1-20`).
- [MISSING] Exported modules absent from Interface: `expr.py`, `organisation.py`, `provision.py`, `metadata_provision.py`, the other `mapping.py` artefacts, `HierarchyAssociation`, `AttachmentConstraint`, `istring.py`, `annotations.py`, `link.py`, `version_rewrite.py`, `rest_api.py`, `_tree.py`, and `version.is_final`/`unreferenceable` (`model/__init__.py:3-129`).

## src/sdmxlib/query/EGM.md

- [IMPRECISE] "A path that crosses a to-many hop compiles to a correlated `EXISTS` subquery, never a join": a membership hop whose only leaf is an equality on the joined key compiles to `? = ANY(s0.ancestor_ids)` (forward, no facet, inside `any`) (`_compile.py:428-497`, `:1285`).
- [IMPRECISE] `FanOut` also provides `.every()` (`NOT EXISTS` over a violating child) and the `list`, `unique` and `count(where=)` aggregates; `Quantified` carries "any" or "every" (`_expr.py:3009-3070`, `_compile.py:1245-1309`).
- [IMPRECISE] "Cardinality is derived from key constraints": at runtime it is read off the hop's direction (forward to-one, backward to-many, `member_of` to-many); `tests/unit/test_query_edges.py` checks the declarations against the schema (`_joins.py:63-70`, `_edges.py:9-15`).
- [IMPRECISE] `Query.explain` returns the compiled SQL, a `-- binds:` line and an optional fetch line; it does not run DuckDB `EXPLAIN` (`query.py:1345-1358`).
- [IMPRECISE] `union` is a module-level function `union(first, *rest)`, not a `Query` method (`query.py:2505`, `__init__.py:14`).
- [MISSING] The result types `Select` (with its own `order_by`, `limit`, `offset`, `unique`, `having`, `to_sql`, `explain`, `columns`, `collect`, `stream`; `Select.filter` raises), `Grouped` (`agg`/`len`) and `Page` (`query.py:1966`, `2031`, `2086`, `2274`).
- [IMPRECISE] `fetch_into` issues the requests and calls `store.add`, skipping an artefact that is not `SupportsUrn` or whose `add` raises `ValueError`, and returns the `Request`s; nothing in `_execute.py` runs the query, which runs when `Query._cursor()` calls the `_prepare` hook `api/registry.py:760` installs (`_execute.py:110-166`, `query.py:639-656`).
- [IMPRECISE] `fetch` issues one request and returns the `StructureMessage`; only `fetch_into` calls `Store.add` (`_execute.py:60-81`, `150-166`).
- [MISSING] Terminals trigger the REST fetch: every statement goes through `Query._cursor()`, which calls `_prepare`, so any terminal on a REST-backed query, `columns` included, performs HTTP first (`query.py:639-656`).
- [MISSING] REST-backed queries are scoped to their own fetch through `_fetch_scope` and a compiled scope clause (`query.py:658-679`, `_compile.py:1317`).
- [IMPRECISE] "Identifiers come only from the edge graph": the observation plane takes names from the `DataflowBinding` and sidecar; `_partition_values` and `_scope_clause` interpolate binding and schema names (`observations.py:115-135`, `query.py:1744-1756`, `_compile.py:1317`).
- [IMPRECISE] `CrossSourceError` is raised in three places: `filter` with an `is_in(relation)` of another source, `intersect`/`difference`, and `union`; a relation that cannot report its source is trusted (`query.py:845-879`, `1040-1069`, `2528`).
- [MISSING] `ObsQuery` refuses `ids`, `urns`, `page` and the artefact terminals with `TypeError` (`observations.py:532-555`).
- [MISSING] All compilation routes through `plan_program(self.plan, DUCKDB)`; a program of more than one step raises `NotImplementedError`; `DUCKDB`, `Program`, `Push` and `PushdownError` are unnamed (`_pushdown.py:589`, `637-711`, `query.py:1324-1343`).
- [IMPRECISE] `RawSql.columns` also runs `DESCRIBE`, and `Query.columns` triggers `_prepare` first (`raw.py:275-283`).
- [MISSING] `_rest.py` also turns an observation query into a `/data` request (`DataRequest`, `data_request_for`, `requests_for_derived`) (`_rest.py:178`, `278`, `342`).

## src/sdmxlib/storage/EGM.md

- [WRONG] "The only interpolated SQL is the `ATTACH` of file paths and the storage version": `code_map` and `code_map_for_dsd` interpolate the caller's `lang` into SQL (`_projections.py:59`, `readers.py:2850`), and `use_sdmx`, `holds_rows`, `_trim._rebuild`, `_delete_artefact_cascade`, `_columns.table_columns` and `_synthetic.distinct_periods` interpolate identifiers (`schema.py:1662`, `:1690`, `_trim.py:94-96`, `writers.py:820`, `_columns.py:12`, `_synthetic.py:45`).
- [IMPRECISE] `get_artefact` returns a `LazyCodelist`/`LazyHierarchy` unchecked by default; the facade is checked when it materialises or `partial()` runs (`readers.py:2668-2679`, `lazy.py:679`, `705`, `1245`).
- [IMPRECISE] `get_artefact` needs `eager=True` to rebuild a codelist or hierarchy, answers item URNs through the owning scheme, and takes `refused=` (`readers.py:2623`, `2663-2729`).
- [IMPRECISE] `put_availability(conn, dataflow_urn, availability)` takes no `source_registry` and writes an availability, not an artefact (`writers.py:2906`).
- [MISSING] `put_artefact` (the dispatching writer `LocalRegistry` calls; `TypeError` for an unpersisted class), `drop_artefact`, `clear_availability` and `put_availability` (`writers.py:2850-2906`).
- [MISSING] `_put_spine` warns (`RuntimeWarning`) when a row is overwritten with a different `source_registry`, and stamps `fetched_at` (`writers.py:196-245`).
- [MISSING] `_kinds.py`, the single class-to-`artefact_type` registry used by writers, readers, `local/registry.py` and `query/accessors.py` (`_kinds.py:1-12`).
- [MISSING] `_parsed.py`'s process-wide bounded single-flight cache of parsed statements (`MAX_TEXTS = 256`); `_columns.py` and `_synthetic.py` are unlisted (`_parsed.py:1-38`).
- [MISSING] `_manifest.py` also defines `ManifestError`, `StoreFormatError`, `MANIFEST_NAME`, `FORMAT_VERSION`, `DATA_PATH`, `DUCKLAKE_SPEC_VERSION`, `publication_storage_version`, `engine_storage_version` and `generate_schema`; `refuse_engine_mismatch` creates a throwaway DuckDB file on every call (`_manifest.py:27-171`, `408`).

## src/sdmxlib/local/EGM.md

- [IMPRECISE] `duckdb.connect(uri, read_only=...)` applies to a registry opened on a file path; a `bundle()` registry runs inside a `BundleEngine`, on `engine.cursor(prefix)` (`registry.py:762-768`, `1511-1520`, `engine.py:139`).
- [IMPRECISE] `DuckLakeVersionError` is raised only when the catalog's format is newer than the installed extension supports, matched on DuckDB's error text; `bundle()` usually refuses earlier with `StoreFormatError` (`data_store.py:48-68`, `310-339`, `registry.py:1510`).
- [IMPRECISE] The engine prefix replaces unsafe characters in `bundle_id` with `_` and removes them from `token`, so a UUID token loses its hyphens (`engine.py:137`).
- [IMPRECISE] `DuckDBDataStore._needs_azure` is true for any `ducklake:` spec, so every bundle and `build()` registry runs `INSTALL azure`/`LOAD azure` and sets the curl transport (`data_store.py:278-301`, `349-369`).
- [MISSING] Opening a registry can rewrite the store: `_initialise_schema` calls `migrate` (`DROP SCHEMA sdmx CASCADE`) on an empty or `rebuild=True` store, raises `StoreSchemaError`/`ReadOnlySchemaError` otherwise, and runs `check_scan_integrity`; `rebuild=` and `target=` are unmentioned (`registry.py:591-627`, `848-891`).
- [MISSING] `bundle()` reads the manifest first and refuses a schema-version or engine-format mismatch; it returns a `BundleRegistry` even from `LocalRegistry.bundle` (`registry.py:1500-1515`).
- [MISSING] `build()` with `previous=` runs `refuse_engine_mismatch` and `restore`, always rewrites `publication_token`/`previous_token`, and `resume=` raises `ManifestError` for a published or foreign publication; `publication` is unlisted (`registry.py:2516-2572`).
- [MISSING] Unlisted `BundleRegistry` members: `refused`, `manifest`, `data_path`, `locate`, `lake_alias`, `dsd_code_map`, `reported_attributes`, `metadata_for_target`, `check_binding_drift`, `validate_binding`, `applicable_hierarchies`, `unique_hierarchy`, `clear_resolve_cache`, `resolve_cache_info`; `BundleEngine.connection` and `attached`.
- [MISSING] The bounded FIFO resolve cache (`_FIFOCache`, `resolve_cache_items=`), cleared on every write and at `transaction()` exit (`registry.py:248`, `334-445`, `2624-2673`).
- [MISSING] Scan relations are never dropped and live until the connection closes; bytes are written to a temporary file first; `scan_frame_into` reports a cast failure through `type_mismatch` (`_scan.py:63-69`, `126-128`, `169-174`).

## src/sdmxlib/api/EGM.md

- [WRONG] "An `obs()` / `data()` response": `RestRegistry` has no `data()` method; only `obs()` ingests through `reading_csv` (`api/registry.py:1146`).
- [IMPRECISE] Only the structural `query()` path uses `SingleFlight`; `get()` does not, and the observation path uses `_data_lock` (`api/registry.py:288-291`, `1127-1134`).
- [IMPRECISE] The constructor also takes `timeout` and `headers`; `api` is ignored for a `ProviderInfo`; `store=` without `id` raises `ValueError` (`api/registry.py:237-267`).
- [MISSING] `RestRegistry.refused`, `registry` and `__contains__`; `get` raises `SdmxConformanceError` for a served artefact the reader refused (`api/registry.py:310`, `368`, `396`, `458-472`).
- [MISSING] With `store=`, a pinned, unresolved read is served from the store before any HTTP; a `latest` URN never is (`api/registry.py:474-496`).
- [IMPRECISE] `download`'s `to` is optional (bytes without it), and the response `Content-Type` is checked too (`api/registry.py:937-1008`, `client.py:634-693`).
- [IMPRECISE] `FmrRegistry` also accepts `auth=(user, pass)`, forces `api="2.1"` and takes only `HttpxTransportOptions` (`api/registry.py:1293-1327`).
- [IMPRECISE] Freshness metadata is cached only for `latest`-version conditional calls, keyed by URL and sorted params; a fresh window returns `NOT_MODIFIED` without a request (`client.py:500-512`).
- [IMPRECISE] Only `get_artefact`/`get_artefact_if_changed` map a 404 to `KeyError`; `get_all` and the data methods raise `HTTPStatusError`; 413/414 become `ResponseTooLargeError` (`client.py:246-260`, `514-546`).
- [IMPRECISE] `download_data` writes a file only when `to` is given (`client.py:681-693`).
- [MISSING] `FederatedRegistry.get` translates `resolve`/`partial` per source from its signature; `__contains__` takes an `SdmxUrn` only.
- [MISSING] `HttpxTransportOptions`, `HttpxAuthOption` and `SdmxClient.api` (`client.py:77`, `85`, `414`).

## src/sdmxlib/bundle/EGM.md

- [WRONG] `check_on_open` runs `receive` on the first call (stamping if clean) and `open` once stamped, never both in one call; an unreadable manifest or unsafe `bundle_id` gets an unstamped `receive` (`_check.py:291-306`).
- [IMPRECISE] `write_manifest`'s `*.wal` and non-code refusals come after `settle_lake`, `clear_availability` and the binding survey have run, so a refused publication is left settled with availability cleared; the non-code check runs only when `lake.ducklake` exists (`_write.py:440-465`, `506-511`).
- [MISSING] `check_bundle` raises `ValueError` for an argument the profile does not use (`_check.py:213-230`).
- [MISSING] Exports: `read_manifest`, `Manifest`, `Producer`, `Provenance`, `BundleFile`, `ArtefactEntry`, `BindingCount`, `Tier`, `ManifestError`, `StoreFormatError`, `DATA_PATH`, `MANIFEST_NAME`, `SIGNATURE_NAME`, `FORMAT_VERSION`, `DUCKLAKE_SPEC_VERSION`, `Lifecycle`, `Merge`, `applied`, `overlay_digest`, `OVERLAY_FORMAT` (`bundle/__init__.py:44-101`).
- [MISSING] `_availability.py` (`survey`, `NonCode`) and `structure_fingerprint` (`_fingerprint.py:72`).
- [IMPRECISE] `build_bundle` cuts codelists only with `partials=True`; `fetch_bundle` passes `partials=False` (`_fetch.py:118-127`, `_build.py:57`).

## src/sdmxlib/builders/EGM.md

- [IMPRECISE] `build_component_availability` with `component_id="all"` covers every bound dimension; an attribute comes back as a `ComponentValueSet`, a dimension as a `KeyValue` (`contentconstraint.py:240`, `263-266`).
- [MISSING] It reads a published `BundleRegistry`'s stored availability, drops a constraint covering every held value, and takes an unconstrained dimension's stored values without a scan; a `LocalRegistry` always scans (`contentconstraint.py:242-251`, `316-332`).
- [IMPRECISE] Only the column is quoted; the table name is interpolated from the binding as is (`contentconstraint.py:113`, `368-370`).
- [IMPRECISE] `annotate_structure_message` also returns its input when no DSD gains an annotation (`metadata_annotation.py:400-401`).
- [MISSING] `build_dsd_metadata_map` orders `*_DATAFLOW_INFO` MSDs first, runs a fixed `NUM_QUERIES = 3` round trips (pinned by a test) and expands wildcard targets in Python (`metadata_annotation.py:97`, `160-163`, `297-301`).
- [IMPRECISE] Every builder also accepts a `LocalRegistry` (`contentconstraint.py:316`).

## src/sdmxlib/EGM.md

- [WRONG] "`SingleFlight` ... Every runtime cache goes through it": `_FIFOCache` (`local/registry.py:334`), `SdmxClient._freshness` (`api/client.py:326`), `RestRegistry._settled` (`api/registry.py:259`) and `functools.cache`s in `api/federated.py` and `model/urn.py` do not; `SingleFlight` is used in `api/registry.py:288` and `storage/_parsed.py:60`.
- [IMPRECISE] The outermost `session()` block releases its cursor, so the next session gets a fresh one; `own_handle` makes a new handle per Arrow stream (`session.py:253-262`, `476-480`).
- [IMPRECISE] `_hook_lock` guards only the bookkeeping; the hooks run outside it (`session.py:281-304`).
- [MISSING] `SessionHook`, module-level `on_connect` and `handle_count`, and `VALUES_RELATION_PREFIX` (`session.py:62`, `69`, `291`, `307`).
- [IMPRECISE] `DataStore.write_observations` is keyword-only and also takes `partition_by` and `lake_alias`.
- [IMPRECISE] `synthetic_bundle` is deterministic in artefacts and rows only; the token and DuckDB bytes differ per build (`testing.py:12-15`).
- [IMPRECISE] `__init__.py` also defines the public `sl.len` aggregate (`__init__.py:213`).

## src/sdmxlib/formats/EGM.md

- [IMPRECISE] `parse` also converts a `ValidationError` raised outside any per-artefact refusal to `SdmxParseError` (`formats/__init__.py:184-191`).
- [MISSING] `serialize` runs `without_dropped(msg, version)` before validating, and raises `ValueError` for an unknown version first (`formats/__init__.py:287-298`).
- [MISSING] With an `InMemoryRegistry`, `parse` binds the read to its resolver; `add_all` runs only after the `strict`/`lossless` checks (`formats/__init__.py:173`, `192-200`).
- [IMPRECISE] `check_positions` also raises for a stated position that is not an integer (`_positions.py:43-47`).
- [IMPRECISE] `DataFormat` also has `.base` and `.version` (`data_format.py:65-83`).

## src/sdmxlib/formats/sdmx_ml/EGM.md

- [WRONG] "No code branches on a version string": `_common.py` branches on `version == "2.1"`, `self.grammar == "2.1"` and `BY_STRUCTURE_NS.get(...) == "2.1"` in many places (`_common.py:253`, `262`, `1657`, `1820`, `1851-1865`, `1905`, `1977`, `2026`, `2189`).
- [WRONG] "`account` attaches `ReadGap` and `ReadAssumption` entries to `StructureMessage.lost`": `ReadAssumption`s go to `msg.assumed`, and `account` also appends refusals to `msg.refused` (`_losses.py:321-323`).
- [IMPRECISE] `V3Writer.write_kept` also takes `metadata_links` and `base_url`, and raises `ValueError` when `metadata_links` lacks `base_url` (`_v3_writer.py:1510-1527`).
- [IMPRECISE] `account` skips the `AvailabilityConstraints` container, synthetic StructureSets and 2.1 elements built as another class (`_losses.py:273`, `299-304`, `328-363`).
- [IMPRECISE] The declared tables `LOSSES` and `ASSUMPTIONS` are unnamed (`_losses.py:87`, `198`).
- [MISSING] `write_header(header=None)` writes the fixed `PLACEHOLDER`, which the header readers read back as `None` (`_header.py:30`, `64-71`, `139-193`).
- [IMPRECISE] The closed-world check also admits any attribute on an element in `OPEN_ATTRIBUTES` (`_closed_world.py:77-84`).

## src/sdmxlib/formats/sdmx_ml21/EGM.md

- [IMPRECISE] A metadata `ContentConstraint` is refused (`METADATA_CONSTRAINT`), not read as a `DataConstraint` (`sdmx_ml/_common.py:84-87`, `1561-1565`).
- [IMPRECISE] `_plain` converts a geographic codelist only when it has extensions (`writer.py:1284-1294`).
- [MISSING] A metadata provision agreement is written as a `ProvisionAgreement` over its metadataflow and read back; `<str:HierarchicalCodelist>` is unwrapped into `Hierarchy` artefacts and dropped when it wraps only one (`_providers.py:1-21`, `reader.py:1155-1265`).
- [IMPRECISE] The marker list omits `SYNTHETIC_REPORT_ID`, `METADATA_PROVIDER_SCHEME_TYPE`, `METADATA_PROVIDERS_MERGED_TYPE` and `METADATA_PROVIDER_TYPE` (`_synthetic.py:41-58`).

## src/sdmxlib/formats/sdmx_ml30/EGM.md

- [IMPRECISE] The MSD URN goes in the header only when the set's structure ref resolves to an MSD; otherwise the writer uses the ref's own URN in an element chosen by class, and a `MetadataProvisionAgreement` URN falls to `com:Structure` (`metadata.py:173-232`).
- [IMPRECISE] The writer emits only `md:Value` or `com:Text`, so a `com:StructuredText` value is written back as `com:Text` (`metadata.py:92-100`, `264-292`).
- [MISSING] `metadata.read` discards the header's `ReadGap`, so an unbuildable header becomes `None` with no loss recorded (`metadata.py:366`).
- [IMPRECISE] `metadata.read` raises `SdmxParseError` itself only for a set with no structure ref and a bad timestamp; lxml errors and `ValidationError`s propagate to `parse` (`metadata.py:314-364`).

## src/sdmxlib/formats/sdmx_ml31/EGM.md

No drift detected.

## src/sdmxlib/formats/sdmx_json/EGM.md

- [WRONG] "`id`, `agencyID`, `version` are declared required": `version` is `str | None = None` on `MaintainableJson`; a missing version is held as 1.0 and recorded as an assumption (`_structure_dto.py:109-114`, `_losses.py:256-257`).
- [IMPRECISE] Unknown wire fields are recorded as `ReadGap`s on `lost`, and unknown top-level sections are refused (`reader.py:693-742`).
- [MISSING] `lost` is lazy and holds the document's bytes until first read; an entry its struct cannot decode is refused alone through a per-entry fallback (`reader.py:637-690`, `718`).
- [IMPRECISE] `write_kept` calls `with_derived_targets` and expands `%` wildcard codelists before `_refuse_unwritable` (`writer.py:282-290`).
- [MISSING] The data readers read only `data_sets[0]` (and `structures[0]` in 2.0); further data sets are ignored (`data_v1.py:202`, `data_v2.py:227-228`).
- [IMPRECISE] The metadata writer stamps `$schema` with the 2.1 metadata schema URL (`metadata.py:50`, `264`).

## src/sdmxlib/formats/sdmx_json_1_0/EGM.md

- [MISSING] `write_contentconstraint` raises `ValueError` for a naive `prepared`, and zoneless date-time periods fold into the `ValidationError` (`writer.py:265`, `271`; `_dto.py:252-264`).

## src/sdmxlib/formats/sdmx_csv/EGM.md

- [IMPRECISE] `structure_id` may be a `Dataflow`, `DataStructure` or `ProvisionAgreement`, from which `structure` is derived; `ValueError` when `structure` is not in `STRUCTURE_KINDS` (`writer.py:65-120`).
- [MISSING] The public `STRUCTURE_KINDS` constant (`reader.py:59`).
- [IMPRECISE] `_peek_dataflow_id` falls back to `STRUCTURE_ID` when there is no `DATAFLOW` column (`reader.py:76-79`).
- [IMPRECISE] `_resolve_structure` follows provision agreement → dataflow → structure, returns `(DataStructure, Ref[Dataflow] | None)` and raises `ValueError` or `TypeError` (`reader.py:138-203`).
- [IMPRECISE] The helpers read the first 4096 characters, so a longer first row is truncated, and decode bytes with `errors="replace"` (`reader.py:64-103`).

## cli/EGM.md

No drift detected.

## cli/src/EGM.md

No drift detected.

## cli/src/sdmxlib_cli/EGM.md

- [WRONG] "A bad `SOURCE`, `FLOW` or tier raises `typer.BadParameter` ... it never falls through to the library": `_flow` raises `BadParameter` only for an empty agency or id; a malformed URN or short form raises the library's `SdmxParseError`, and `_flow("ECB:X(bad v)")` returns a URN (`cli/src/sdmxlib_cli/__init__.py:93-100`).
- [IMPRECISE] `fetch` requires `PATH` absent or empty (`FileExistsError` otherwise), with the publication at `<path>/<token>/` and data at `<path>/data/` (`bundle/_fetch.py:51-87`).
- [IMPRECISE] `--api` applies only to a URL source (`bundle/_fetch.py:88`).

## tools/EGM.md

- [IMPRECISE] `just generate-allowed-children` takes no parameters and always rewrites; `--check` works only by running the script directly, and `test_closed_world.py` is the guard (`justfile:107-108`, `generate_allowed_children.py:502-511`).
- [MISSING] `mutate_compare.py` also rewrites `pyproject.toml` and deletes `mutants/` on every arm (`mutate_compare.py:64-87`).
- [MISSING] `check_return_widening.py` exempts the pairs in `ALLOWED` and the functions in `ALLOWED_FUNCTIONS` (`check_return_widening.py:63-80`, `225-228`).
- [IMPRECISE] `check_docstrings.py` exits 2 when pydoclint crashes, even without `--check` (`check_docstrings.py:164-166`).
- [IMPRECISE] `check_prose.py`'s phrase scan also reads the `.py` and `.toml` files and the justfile under `CODE` (`check_prose.py:41-43`, `136-140`).

## fuzz/EGM.md

- [IMPRECISE] Seeds over `MAX_LEN` (65536) bytes are never copied into the corpus (`tests/fuzz/targets.py:141-152`).

## EGM.md

- [IMPRECISE] "`sdmxlib.model` ... does no I/O": `model/rollup.py`'s `AncestorMap` holds a DuckDB connection and runs SQL (`rollup.py:16-20`, `55`, `107-118`).
- [IMPRECISE] "`formats/` owns all XML and JSON": true of SDMX wire formats; bundle and manifest JSON is parsed and written in `storage/` and `bundle/` (`storage/_manifest.py:24`, `bundle/_check.py:15-321`, `bundle/_overlay.py:31`, `storage/resolve.py:19`).
- [IMPRECISE] `.egmignore` does not list `tests/`, which EGM's own rules exclude (`.egmignore:1-13`).
- [IMPRECISE] `test_public_surface` checks that every type in a public signature or a documented raise is importable from `sdmxlib`, not public names in general (`tests/unit/test_public_surface.py:157`, `336`).
- [IMPRECISE] Hosted workflows also run the fuzzer, the docs build, zizmor, Scorecard and the release, besides `just check` (`.github/workflows/`).

## src/EGM.md

No drift detected.
