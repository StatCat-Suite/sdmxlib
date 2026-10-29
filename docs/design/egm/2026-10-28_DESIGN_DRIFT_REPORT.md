# EGM drift report: the design documents against the code

Run on 2026-10-28 for [GH-267](https://github.com/StatCat-Suite/sdmxlib/issues/267)
over the 21 documents in `docs/design/`, on `main` at `fc93cbd0` (sdmxlib
0.77.0). Nine reviewers in fresh contexts, using the `EGM.md` contracts as the
map of the code, recommended a label for each document and listed the claims
the code contradicts: **WRONG** (false) or **OUTDATED** (true once, not now),
with file and line evidence. Several ran the examples against an in-memory
registry or the fake REST service. Quotes and line numbers refer to the
documents and code as they stood that day.

Labels: **CURRENT** (the design the code implements and new work follows),
**EVIDENCE** (a dated record of measurements or a decision), **SUPERSEDED**
(an approach the code does not follow).

Two findings point at the code, and were filed: a REST query that projects
across a path is fetched short and answers null
([GH-271](https://github.com/StatCat-Suite/sdmxlib/issues/271), reproduced),
and `fold_overlays` drops each partial's `cut_from`
([GH-272](https://github.com/StatCat-Suite/sdmxlib/issues/272)).

## Labels

| Document | Recommended | Split recommended |
|---|---|---|
| `query-language.md` | CURRENT | no |
| `query-naming.md` | CURRENT | no |
| `query-composition.md` | CURRENT | no (its peer and gap tables are labelled reasoning) |
| `query-tree-paths.md` | CURRENT | no |
| `query-pushdown.md` | CURRENT | §10 survey, §11 ECB byte counts, §12 materialise numbers to evidence |
| `query-language-examples.md` | CURRENT | no |
| `query-language-cookbook.md` | CURRENT | no |
| `query-language-evidence.md` | EVIDENCE | its "since built" notes move to `query-language.md` |
| `query-language-explorations.md` | SUPERSEDED | "Strict path with a bare inner predicate" is the reasoning the design adopted |
| `data-plane-query.md` | CURRENT | §1–§2 and the §5 measurements to evidence |
| `data-plane-pushdown.md` | CURRENT | §4 (readings A/B, the 2M-row measurements) to evidence |
| `registry-and-catalog.md` | CURRENT | §5 (settled and reopened) and §6 (landing plan, done) to evidence |
| `caching.md` | CURRENT | the #155 case study could move to evidence |
| `concurrency.md` | CURRENT | the v0.27/v0.28 history could move to evidence |
| `json-and-parsing.md` | CURRENT | the milestone %8 narrative could be compressed |
| `phantom-generics.md` | CURRENT | no |
| `performance.md` | CURRENT | "The problem", the bytes/item table and the msgspec serialisation section to evidence |
| `mutation-testing.md` | CURRENT | the `collections.py` and `validation.py` run sections to `mutation-evidence.md` |
| `mutation-evidence.md` | EVIDENCE | needs a dated header |
| `sharded-bundle-catalog.md` | CURRENT | no |
| `sharded-bundle-catalog-plan.md` | CURRENT | no |

## Drift, by document

### query-language.md

- [OUTDATED] §2.5: a predicate over another maintainable root sharing `sdmx.artefact` is "caught only statically"; since #510 `filter` compares each column's `origin` and refuses it at runtime, and the old behaviour answered silently for the wrong relation (`query.py:779-795`, `:328-340`).
- [WRONG] §11: the same pair is "not caught" at runtime (`query.py:785-795`).
- [OUTDATED] §3.4: a parent-rooted `.any()` is a binder error at runtime; it raises `TypeError` naming the fix before SQL runs (#511, `_expr.py:2940-2976`, `:3007-3010`, `:3069`).
- [WRONG] §5: "`explain()`, `to_sql()` and `columns` run nothing"; `Query.columns` runs `_cursor()` (a REST fetch first) and `DESCRIBE` (`query.py:639-656`, `:1360-1375`).
- [OUTDATED] §13 item 1: comparing two aggregates "fails at the database"; `_agg_compared` raises `TypeError` (#511, `_expr.py:565-582`).
- [WRONG] §13 item 4: `IS DISTINCT FROM` is used for `every`; the compiler uses `IS NOT TRUE` (`_compile.py:1302-1306`; `FanOut.every`'s docstring at `_expr.py:3066` is stale too).
- [WRONG] §14: `sl.Str` is "not public"; `sl.Str`, `Col`, `Cols`, `Num`, `Untyped`, `Pred` and `RowKind` are exported, and no `CodeCols` exists (`__init__.py:26-42`).
- [OUTDATED] §7.1: `SliceError` is also raised by `intersect`, `difference`, `after`, `group_by` and `agg` after a slice (`query.py:1023`, `1039`, `1131`, `1864-1871`, `1918-1924`).

### query-naming.md

- [OUTDATED] "every to-one hop names its target class": `cube_region.constraint`, `key_value.region`, `hierarchy_applicability.association`, `item_map.scheme_map`, the maps' `source`/`target` and `hierarchy_association.linked_object`/`context_object` do not (`accessors.py:1693-1958`, `:2011`).
- [OUTDATED] §Deviations claims to list every deviation; it omits those hops and the to-many `annotation.urls` and `metadata_set.targets` (`accessors.py:565`, `:1044`).
- Unsure: whether `group` and `key_value` count among the "42 exact IM class names".

### query-composition.md

- [OUTDATED] The `SliceError` list omits `intersect`, `difference`, `after`, `group_by` and `agg` (as above).

### query-tree-paths.md

- [WRONG] §1: the quoted `Edge` declaration (`forward=`, `on=`) paraphrases it; the fields are `name=`, `column=`, `target_column=` (`_edges.py:55-66`, `:221-239`).
- [IMPRECISE] §2: cardinality at runtime comes from the hop's direction; key constraints check the declarations in `test_query_edges.py` (`_joins.py:62-69`).
- [OUTDATED] §3: `codelist` is also declared on `representation_map_source`/`_target` and `metadata_attribute` (`_edges.py:181-641`).

### query-pushdown.md

- [WRONG] §11 "Projection decides the fetch", "Implication for the staging" and §14: no code adds projected paths to `references=`; `Capability.prefetch` and `can_project` are read only by tests. Reproduced as a null answer over REST (GH-271) (`_rest.py:157-175`, `query.py:655`, `:682-705`, `_pushdown.py:329-340`).
- [WRONG] §11 "The prefetch needs its own bound": `references_for` returns `none`/`children`/`descendants` by depth and never names a type (`_closure.py:249-277`).
- [OUTDATED] §11 "The planner's choice": `plan_program` has no prefetch branch (`_pushdown.py:854-983`).
- [OUTDATED] §2, §7 step 3, §9.1: `Session.value_set` stitching two calls; the executor fetches and reruns the query, and nothing evaluates a `Membership` (`_execute.py:110-166`); §13 says so.
- [WRONG] §1, §6, §9.1: `structure_urn`; the column is `dataflow.dsd_urn` (`schema.py:705-707`).
- [WRONG] §9.1: a `Membership` "step"; it rides on a `Residual(member_of=...)` (`_pushdown.py:593-706`, `:975-976`).
- [OUTDATED] §10: "the declaration is six lines"; `SDMX_REST` now declares nine prefixes, derived scans and per-root bounding (`_pushdown.py:497-586`).
- [WRONG] §4: the as-built `Capability` snippet's defaults (`_pushdown.py:300-307`).

### query-language-examples.md

- [WRONG] §8: `dsd.dimensions["FREQ"].codelist()` raises `TypeError` (`.codelist` is a property returning the codelist; `datastructure.py:235`).
- [WRONG] §8: `collect()` builds categories; `q.category` has no builder and raises `TypeError`.
- [OUTDATED] §1, §5: two quoted refusal messages no longer match the code's text.
- [OUTDATED] §4: an SQL listing omits the `ORDER BY s0.urn NULLS LAST` tiebreak.

### query-language-cookbook.md

No drift detected. Every snippet run matched or was refused where the page says.

### query-language-evidence.md

- [OUTDATED] `sl.artefact.agency_id` is now `sl.q.artefact.agency` (`accessors.py:602`, `:2023`).
- [OUTDATED] "`in` supported, by row id": `Query.__contains__` raises `TypeError` (`query.py:1936-1942`).
- [OUTDATED] `concept.repr_codelist_urn` is `codelist_urn` (`schema.py:226-228`).
- [OUTDATED] "Three is the likely choice": decided; `_Dsd` declares separate fan-outs (`accessors.py:951-958`).
- [WRONG] `reg.codelists()` does not exist; and `codes.list(sl.q.code.id)` is a type error, the spelling is `codes.id.list()` (`_expr.py:851`, `:3019-3035`).
- [WRONG] Cross-references to "To-many edges" and "What the design still cannot express", sections that no longer exist.
- Proposed header: peer surveys, measurements and prototypes from the #306/#280 design pass, August 2026, with in-place corrections through October 2026; not maintained against the code.

### query-language-explorations.md

- Replaced by form B, rooted paths through declared edges (`accessors.py:851-863`, `_expr.py:3009`, `:3061`, `_compile.py:154-174`, `_edges.py`).
- [OUTDATED] Several prototype claims now built or renamed (`_ManyComponent` dots through, `_Dataflow`, `codelist_urn`, `FanOut.every`); dangling references to "Reaching a grandchild through a to-many", "Fan-out: how graph APIs answer it", "The root determines the grain", "To-many edges" and open question 13.

### data-plane-query.md

- [OUTDATED] §6.2: `ids()` fails late in DuckDB; it raises `TypeError` before SQL, as `urns()` and `page()` do (`observations.py:537-555`). The `observations.py` module docstring and `DuckDBDataStore.obs`'s docstring are stale too (`observations.py:19-37`, `data_store.py:496-512`).
- [OUTDATED] §7 step 3: #327 is closed; column kinds reach the compiler (#511), and `Col.sort_by(key).list()` is an ordered aggregate (`_expr.py:957-985`, `_compile.py:1124-1128`).
- [OUTDATED] §3: the `any_value(x ORDER BY y)` row has no as-built note; ordered `list`, `arg_min`/`arg_max` exist (`_compile.py:1107-1117`).
- [OUTDATED] §6.3: an enumerated representation is `Ref[Codelist | ValueList]` (`observations.py:404-409`).
- [OUTDATED] §3: no note that the observation root refuses `page()` (`observations.py:547-550`).

### data-plane-pushdown.md

- [WRONG] §4 and §6 outcomes: "bounded" by a file DuckDB reads; the ingest loads the whole slice into the query store, and `Query.stream`'s docstring says a fetched stream is held in RAM with the default store (`api/registry.py:1140-1153`, `data_store.py:631-636`, `query.py:1666-1678`).
- [WRONG] §4 B: `client.stream_data_to`; the method is `stream_data_at` (`client.py:609`).
- [OUTDATED] §4: the streaming rule was never built; the docstring it criticises now scopes its figures.
- [OUTDATED] §2: `SDMX_REST_DATA_21`/`_30` are consulted only by tests; `data_request_for` branches on the version (`_pushdown.py:441-481`, `_rest.py:407`).
- [IMPRECISE] Status: `is_bounded` returns a bool; the refusal on the REST path is `_refuse_unbounded_fetch` (`api/registry.py:84-111`).

### registry-and-catalog.md

- [OUTDATED] §1.1: the compiled SQL "scans the whole store"; each query is scoped to its own fetch (`_compile.py:1317-1339`, `api/registry.py:758-763`).
- [OUTDATED] §3.1: the as-built method table omits `refused`, `scan_json`, `build`, `bundle`, `publication`, `availability`, `locate`, `manifest`, `routing`/`default`/`source_for` and others, and has no `BundleRegistry` column.
- [OUTDATED] §3.2: `scan_csv` "a polars helper on a REST registry, move to `sdmxlib.polars`"; it is on both registries, with `scan_json` (`local/registry.py:2862`, `api/registry.py:1156`).
- [WRONG] §4.1: the "binding passed to the writer" wart; `write_sdmx_csv` takes a DSD (`sdmx_csv/writer.py:65-72`).
- [WRONG] §4.3: `.name["en"].contains(...)` raises `AttributeError`; the spelling is `.str.contains(...)`.
- [OUTDATED] §5.7: cited line ranges now point at unrelated code; the writers take a DSD and `dataflow_link` a `Ref[Dataflow]` (`_data_common.py:702`, `:741`).
- [OUTDATED] §6 step 7: `polars.sink_json` is `ObsQuery.sink_json`.
- [OUTDATED] §1.1: quotes a `query-pushdown.md` heading that no longer exists; §5.9 "Topology B" is defined only in `history/gitlab/issues/372.md`.

### caching.md

- [WRONG] `LazyCodelist.code_map` "single-flight": a double-checked lock shared across languages, not through `SingleFlight` (`storage/lazy.py:151-152`, `:591-600`).
- [WRONG] The contract requires single-flight and names `_FIFOCache` as a model; `_FIFOCache` is not single-flight, so two threads missing one URN both read it (`local/registry.py:396-435`).
- [OUTDATED] Which primitive is the rule disagrees with `src/sdmxlib/EGM.md`.
- [OUTDATED] The "a pointer is not a key" rule omits `RestRegistry._settled`, a revalidated `latest` pointer (`api/registry.py:270-275`, `:576-608`).
- Inventory of the runtime caches against the rules: `RestRegistry._fetched` meets all four; `storage/_parsed` is single-flight and bounded but a module global; `_FIFOCache`, `LazyCodelist._code_map_cache`, `SchemaInspector`'s caches, `SdmxClient._freshness` and `RestRegistry._settled` (no lock; possible race, untested) do not go through the primitive; `RestRegistry._refused` is unbounded and never cleared; many `functools` caches are pure.
- [OUTDATED] GitLab URLs for #155; "the module-global weakref dict" and "the process-global label cache" no longer exist.

### concurrency.md

- [WRONG] §Read safety: the cursor code (`SET search_path`); `_read_cursor` runs `use_sdmx` and `adopt` (`local/registry.py:984-995`).
- [WRONG] One shared cursor table; `storage/_projections.py` keeps a second per-thread table (`_projections.py:22-34`).
- [WRONG] No `SET search_path` on `cursor_for` handles; each runs the `use_sdmx` hook (`local/registry.py:738-748`).
- [OUTDATED] "about 2N cursors"; about 3N with the second table.
- [OUTDATED] One connection runs one statement at a time; `cursor_for`'s docstring records 1.32x for four parallel queries (`session.py:146-148`). One of the two is stale.
- [OUTDATED] Several registries need several connections; `BundleEngine` shares one (`local/engine.py:88-107`).
- [WRONG] "Both meet the checklist in caching" (`_FIFOCache`, as above).
- [OUTDATED] The read rows are attributed to `LocalRegistry`; they are on `BundleRegistry`.
- [OUTDATED] GitLab URLs for #75, #78, #80; "see `bench/`" names no file.

### json-and-parsing.md

- [WRONG] "no `dict[str, Any]` wire islands left": the structure and data writers build envelopes as dicts (`sdmx_json/writer.py:64-72`, `:292`, `:318-324`; `data_v1.py:141-184`; `data_v2.py:161-208`).
- [OUTDATED] "decode their envelope into a Struct in one pass": the per-entry fallback decode (`reader.py:637-688`).

### phantom-generics.md

- [WRONG] "Never use `Any` on the phantom": the SDMX-ML reader's `ref_el_to_urn` and `_checked` use `SdmxUrn[Any]`, the only two such sites in `src/` (`sdmx_ml/_common.py:582`, `:681-697`).

### performance.md

- [WRONG] The diagram routes `to_registry` through `_CallScopedMemo` and `_resolve_refs`; it reads eagerly and resolves nothing (`local/registry.py:2414-2429`; `_resolve_refs`'s docstring at `:2014-2015` is stale too).
- [OUTDATED] §Layer 2: items resolved by loading each parent scheme; since #378 `Code` and `HierarchicalCode` are read by URN (`local/registry.py:150-152`, `:2152-2240`).
- [OUTDATED] The resolver machinery is on `BundleRegistry` (`local/registry.py:566`, `:640`).
- [OUTDATED] §Layer 1: the bulk contact read serves five readers; `_read_annotations_subtree` serves 17.
- [WRONG] Lazy facades "bypass the cache entirely"; one reached as a Ref target is memoised (`local/registry.py:2203-2215`).
- [OUTDATED] GitLab URLs for #72–#74; "milestone #8" is `%8`.

### mutation-testing.md

- [OUTDATED] "ten modules, 3,212 statements": `just mutate-scope` gives 3,383.
- [WRONG] `validation.py` timeouts as non-termination; #303 showed them mis-booked (RLIMIT_CPU in forked children), and #314's forkserver fixed the hang (`tools/mutate_area.py:9-24`, `pyproject.toml:245-250`).
- [OUTDATED] "with a mutmut timeout configured": none is; the remedy is `mutate-area`.
- [OUTDATED] The page does not describe `tools/mutmut_forksafe.py`, which `just mutate` runs through.

### mutation-evidence.md

- [OUTDATED] "2,300 statements" in two sections; 3,383 now.
- [OUTDATED] "Every baseline above installment 3": installments 1–2.
- Proposed header: mutation runs 2026-08 to 2026-10 (#218, #324, #458), installments 1–6 on mutmut 3.7, 7–12 on mutmut 3.8 forkserver.

### sharded-bundle-catalog.md

- [WRONG] §4: 0.75.0 moved the store schema to 2.49 (GH-194); 2.51, with GH-205 and GH-197, came in 0.76.0 (`docs/upgrading.md:90`, `:126`).
- [OUTDATED] §4.4: `fetch_bundle` asks for `references=descendants`; it uses `resolve="all"` and `"parents"` through `build_bundle` (`bundle/_fetch.py:92-125`).
- [OUTDATED] Status: "written against 0.75.0"; the spec relies on 0.76.0 work, and 0.77.0 is current; §16 M6 files partial vendoring under statcatlib, which `build_bundle` does.
- [OUTDATED] Intro: "manifest 1.1 and overlay 1.0" against `docs/bundle.md`'s manifest 2.1 and overlay 1.2.
- [WRONG] §7.2: `<token short>`; the prefix is the whole token, sanitised (`local/engine.py:130`).
- [WRONG] §8: `build_bundle` cuts "a codelist or scheme"; it cuts `Codelist`s only, keeps referenced codes, and leaves one full for extensions, a missing column, no observations or a cut keeping everything (`bundle/_build.py:15-21`, `:197-227`).
- [OUTDATED] §4.6: no row for the `partials` check (publish and receive with `structures=`, refused at open).
- [WRONG] §4.2: `semantic` stability also needs a major version of at least 1 (`model/version.py:121-137`).
- [WRONG] §4.4: the table fingerprint SQL lacks the `coalesce(…, 0)` the code applies (`bundle/_fingerprint.py:34-38`).
- [WRONG, unsure] §4.7: `check_on_open` takes no `fingerprints=`; `check_bundle` does.
- Code issue: `fold_overlays` drops `cut_from` (GH-272).

### sharded-bundle-catalog-plan.md

- [OUTDATED] Phase 1 and track M (GH-185) are built and released in 0.76.0; the plan still lists them as pending, `_build.py` as "(Create)", and the tier-3 harness under paths it never had (`bench/tier3/`).
- [WRONG] Phase 1 item 2: `build_bundle`'s signature (a required `bundle_id`; `producer` before `observations`), that it runs `check_bundle`, a staging registry, and the partials rule (`bundle/_build.py:50-227`).
- [WRONG] Phase 1 item 1: `Completeness.CLOSED`; direction is a separate `Session._parents` set (`api/session.py:13-43`, `:107`, `:166-189`).
- [WRONG] Phase 1 item 4: three cassettes and a constraint-edit cassette; two exist, and the tests live in `test_rest_references.py` and `test_bundle_cut_from.py`.
- [WRONG] Phase 1 item 5: `docs/upgrading.md` sections for `resolve` and manifest 2.1, which were additive and have none.
- [WRONG] Track M: "`just ci` does not" run `test-stores`; it does (`justfile:255`).
- [OUTDATED] "`../bundle.md`: manifest 2.0"; it is 2.1.
