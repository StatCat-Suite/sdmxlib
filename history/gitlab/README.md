# The GitLab archive

sdmxlib was developed on GitLab (`pinax-suite/sdmxlib`) until October 2026. The
tree cites that tracker as a bare `#N` (an issue), `!N` (a merge request) or `%N`
(a milestone). This folder holds every one of them as Markdown, so the history
can be read without GitLab: `#422` is [`issues/422.md`](issues/422.md), `!600`
is [`merge_requests/600.md`](merge_requests/600.md), and `%15` is
[`milestones/15.md`](milestones/15.md). New work is tracked on GitHub as `GH-N`.

Exported on 2026-10-23: 519 issues, 700 merge requests
and 26 milestones, with their comments. GitLab's own event notes
(label changes, mentions, commits pushed) are left out, and attachments are not
exported.

**Some text is withheld.** A body or comment that named or described a
StatCan-internal system (an internal service, bundle builder or agent, a
deployment, an internal host or document) is replaced whole by a one-line
placeholder. 262 of them are: 104 issue bodies, 51 issue comments,
102 merge request bodies, 0 merge request comments and 5 milestone descriptions.
A title that named one says what it was instead ("the 2021 Census Profile
bundle", "an LLM agent built on sdmxlib").

## Milestones

| | title | state |
| --- | --- | --- |
| [%1](milestones/1.md) | Construction, Coercion & Validation | closed |
| [%2](milestones/2.md) | Concurrent reads (issue #75) | closed |
| [%3](milestones/3.md) | Cleanup pass | closed |
| [%4](milestones/4.md) | Cleanup follow-ups | closed |
| [%5](milestones/5.md) | xsd-coverage-followups | closed |
| [%6](milestones/6.md) | coverage-pass | closed |
| [%7](milestones/7.md) | Typing hardening: eliminate needless Any | closed |
| [%8](milestones/8.md) | SDMX-JSON: migrate dict+orjson → typed msgspec Structs | closed |
| [%9](milestones/9.md) | Suppression audit: fix the defect, not the symptom | closed |
| [%10](milestones/10.md) | Testing: audit, properties, and mutation | closed |
| [%11](milestones/11.md) | Wire conformance: no artefact serialises to an invalid document | closed |
| [%12](milestones/12.md) | Cross-version conversion: convert what converts, refuse only what cannot | closed |
| [%13](milestones/13.md) | Query DSLs: properties and mutation for the half the suite skipped | closed |
| [%14](milestones/14.md) | Query DSLs: say what the docs already claim | closed |
| [%15](milestones/15.md) | Querying SDMX: close the gap applications route around | closed |
| [%16](milestones/16.md) | Typing hygiene: enforce the Any rule the docs already claim | closed |
| [%17](milestones/17.md) | Query DSL: a second backend, and the evidence the first one lacks | closed |
| [%18](milestones/18.md) | Read-path performance: what the synthetic benchmarks could not see | closed |
| [%19](milestones/19.md) | Data plane: the first profile of 1.14 billion observations | closed |
| [%20](milestones/20.md) | 3.x-native model: hold all of 3.x, project to 2.1, validate at ingest | closed |
| [%21](milestones/21.md) | Conformance evidence: what the green checks do not prove | closed |
| [%22](milestones/22.md) | Read/write and serving performance: win back M20's cost, fix JSON interop | closed |
| [%23](milestones/23.md) | Full information-model coverage: every maintainable artefact type | closed |
| [%24](milestones/24.md) | Large reads: GC and streaming | closed |
| [%25](milestones/25.md) | Serving SDMX-REST from a local store | closed |
| [%26](milestones/26.md) | Found by the docs audit: correctness and refusals | closed |

## Issues

| | title | state |
| --- | --- | --- |
| [#1](issues/1.md) | Phase 2: domain model types | closed |
| [#2](issues/2.md) | Phase 3: SDMX-ML 2.1 format layer (lxml reader) | closed |
| [#3](issues/3.md) | Phase 4: API layer (SdmxClient, Session, Registry, Query) | closed |
| [#4](issues/4.md) | Phase 5: Polars bridge | closed |
| [#5](issues/5.md) | Code review fixes (v0.2.2 review) | closed |
| [#6](issues/6.md) | Phase 6a: SDMX-ML 2.1 writer | closed |
| [#7](issues/7.md) | Phase 6b: SDMX-ML 3.0 reader | closed |
| [#8](issues/8.md) | Phase 6c: SDMX-ML 3.0 writer | closed |
| [#9](issues/9.md) | Phase 6: FastAPI contrib (Pydantic schemas + router) | closed |
| [#10](issues/10.md) | Phase 6: SDMX-JSON structure reader | closed |
| [#11](issues/11.md) | Phase 6: SDMX-JSON writer | closed |
| [#12](issues/12.md) | feat(model): add Ref.to() classmethod | closed |
| [#13](issues/13.md) | feat(model): add ConceptScheme.ref_to(concept_id) | closed |
| [#14](issues/14.md) | feat(model): __post_init__ coercion on all domain model classes | closed |
| [#15](issues/15.md) | feat(model): DataStructure immutable modification helpers | closed |
| [#16](issues/16.md) | feat(model): add from_dict() / to_dict() in model/convert.py | closed |
| [#17](issues/17.md) | feat(model): add validate() / validate_references() in model/validation.py | closed |
| [#18](issues/18.md) | feat(api): public API re-exports: to_xml, to_json, from_dict, to_dict, validate | closed |
| [#19](issues/19.md) | Add pytest-cov coverage reporting and GitLab badge | closed |
| [#20](issues/20.md) | Round-trip fidelity: parse and re-emit AgencyScheme / OrganisationSchemes | closed |
| [#21](issues/21.md) | Round-trip fidelity: parse and re-emit ContentConstraint / Constraints | closed |
| [#22](issues/22.md) | StructureSet + CodelistMap round-trip fidelity | closed |
| [#23](issues/23.md) | complete.xml fixtures + round-trip acceptance tests | closed |
| [#24](issues/24.md) | Support CoreRepresentation on Concept with automatic inheritance in Dimension/Attribute/Measure | closed |
| [#25](issues/25.md) | Accept bare SdmxUrn on codelist/concept fields | closed |
| [#26](issues/26.md) | Remove Enumeration wrapper — representation is Ref[Codelist] \| TextFormat \| None | closed |
| [#27](issues/27.md) | Replace agency_id: str with maintainer: Agency on MaintainableArtefact | closed |
| [#28](issues/28.md) | Session type subtypes | closed |
| [#29](issues/29.md) | Design: partial resolution caching in Session | closed |
| [#30](issues/30.md) | Redesign: Registry as SDMX IM artefact store, RestRegistry as HTTP client | closed |
| [#31](issues/31.md) | migrate domain model from dataclasses to attrs | closed |
| [#32](issues/32.md) | REST API support and Registry integration | closed |
| [#33](issues/33.md) | to_dict crashes on Attribute.related_dimensions (list[str]) | closed |
| [#34](issues/34.md) | Shared DuckDB storage layer (sdmxlib.storage) | closed |
| [#35](issues/35.md) | Expose public URN builder for SDMX artefacts | closed |
| [#36](issues/36.md) | feat: add typed Urn class for SDMX URN identifiers | closed |
| [#37](issues/37.md) | feat: lazy graph traversal with sub-traversal expressions (pk.each pattern) | closed |
| [#38](issues/38.md) | Add SQL-backed CodelistScope.roots() and CodelistScope.children(parent_id) for tree navigation | closed |
| [#39](issues/39.md) | ContentConstraint should be renamed DataConstraint (SDMX 3.0 IM alignment) | closed |
| [#40](issues/40.md) | HierarchicalCode missing Level reference (SDMX 3.1) | closed |
| [#41](issues/41.md) | sdmx data format | closed |
| [#42](issues/42.md) | DSD component annotations dropped on write (Measure/Dimension/Attribute) | closed |
| [#43](issues/43.md) | feat: add Metadata Structure Definition (MSD) and reference metadata support | closed |
| [#44](issues/44.md) | feat: standards-compliant MetadataSet serialisation (GenericMetadata messages) | closed |
| [#45](issues/45.md) | test: validate reference-metadata pipeline against a real provider when one publishes data | closed |
| [#46](issues/46.md) | Federated Registry Support | closed |
| [#47](issues/47.md) | Federated Layers | closed |
| [#48](issues/48.md) | feat: codelist filter DSL — predicate accessor + lazy fluent | closed |
| [#49](issues/49.md) | feat: require explicit lang on all multilingual APIs (breaking change) | closed |
| [#50](issues/50.md) | feat: SKOS/XKOS-aligned hierarchy navigation on Codelist | closed |
| [#51](issues/51.md) | feat: align hierarchy API on SKOS/XKOS — supersede node().children chain (refines #50) | closed |
| [#52](issues/52.md) | Federation v0.16 follow-ups: async, multi-source Session, richer browse + drift | closed |
| [#53](issues/53.md) | perf: slpl.label() rebuilds full codelist label map on every call | closed |
| [#54](issues/54.md) | perf: LazyCodelist.codes.filter().collect() materializes full Code objects | closed |
| [#55](issues/55.md) | DataStructure: include time dimension in dimension order | closed |
| [#56](issues/56.md) | SDMX REST dotted-key parser | closed |
| [#57](issues/57.md) | LocalDataStore.query should join series_attributes_location | closed |
| [#58](issues/58.md) | Streaming/lazy data query primitive (Arrow record batches) | closed |
| [#59](issues/59.md) | SDMX-CSV writer in sdmxlib.formats.sdmx_csv | closed |
| [#60](issues/60.md) | Small URL/REST conveniences: rest-ref parser and StructureMessage.containing | closed |
| [#61](issues/61.md) | perf: write_sdmx_csv is 10-15x slower than polars/Arrow vectorised CSV write | closed |
| [#62](issues/62.md) | ComponentFilter: accept SQL-relation values for hierarchy-traversal filters | closed |
| [#63](issues/63.md) | Bulk-load codelist depths via typed API | closed |
| [#64](issues/64.md) | Codelist iteration with annotations attached | closed |
| [#65](issues/65.md) | Inherit-along-parent-chain helper for codelists | closed |
| [#66](issues/66.md) | Codelist persist allocates 1.5+ GB and risks SIGABRT at 78k codes | closed |
| [#67](issues/67.md) | _delete_artefact_cascade missing DELETE on hierarchy_level → PK violation re-adding a Hierarchy | closed |
| [#68](issues/68.md) | CodeQuery DSL: filter by Hierarchy/Level membership | closed |
| [#69](issues/69.md) | SDMX 3.1 Constraints | closed |
| [#70](issues/70.md) | Re-running census-load (full pipeline) hits 'Failed to delete all rows from index' on localized_text | closed |
| [#71](issues/71.md) | C-level crash (exit 133) re-running census-load over a populated DB on 0.20.1 | closed |
| [#72](issues/72.md) | LocalRegistry.get() on a 60k-node Hierarchy never returns: _resolve_refs fans out N+1 SQL queries | closed |
| [#73](issues/73.md) | perf(readers): _read_concept_scheme issues N+1 SQL for per-concept annotations | closed |
| [#74](issues/74.md) | perf(readers): MetadataSet read re-resolves MetadataStructure on every call (regression after #72 fix) | closed |
| [#75](issues/75.md) | Concurrent reads via Registry.get / DataStore.query_stream corrupt result sets — shared DuckDB connection without per-request cursor | closed |
| [#76](issues/76.md) | test: reproduce concurrent-read corruption (Phase 1 of #75) | closed |
| [#77](issues/77.md) | bench: capture v0.27.2 read-perf baseline before concurrency refactor (Phase 2 of #75) | closed |
| [#78](issues/78.md) | fix: per-call cursors + lock-free FIFO cache for concurrent reads (Phase 3 of #75) | closed |
| [#79](issues/79.md) | perf+docs: re-bench, document concurrency model, release v0.28.0 (Phase 4 of #75) | closed |
| [#80](issues/80.md) | perf(local): thread-local cursors to amortise per-call lifecycle cost (Phase 5 of #75) | closed |
| [#81](issues/81.md) | feat(data-store): widen _needs_azure() heuristic to detect ducklake ATTACHes | closed |
| [#82](issues/82.md) | Expose concept_role as a first-class field on Dimension / Measure / Attribute | closed |
| [#83](issues/83.md) | Cleanup phase 1: small contract + dead-code fixes | closed |
| [#84](issues/84.md) | Cleanup phase 2: RST docstring sweep + public-API hygiene | closed |
| [#85](issues/85.md) | Cleanup phase 3: shared test conftest + Ref narrowing helper | closed |
| [#86](issues/86.md) | Cleanup phase 4: extract formats/sdmx_ml/_common.py (deduplicate 2.1 ↔ 3.0) | closed |
| [#87](issues/87.md) | Cleanup phase 5: artefact-kind registry + de-underscore local/ + RestRegistry.data simplification | closed |
| [#88](issues/88.md) | Followup phase 1: dedupe SDMX-ML high-level writers (+ misc small wins) | closed |
| [#89](issues/89.md) | Followup phase 2: SQL style sweep — triple-quoted everywhere | closed |
| [#90](issues/90.md) | Followup phase 3: unify near-duplicate SDMX-ML readers with version flags | closed |
| [#91](issues/91.md) | Followup phase 4: document the lint/typecheck disables | closed |
| [#92](issues/92.md) | Design: TZ-aware storage of materialized_at / bound_at | closed |
| [#93](issues/93.md) | High: StructureSet 3.0 split—writer drops on floor | closed |
| [#94](issues/94.md) | High: MetadataStructure / Metadataflow (3.0) untested | closed |
| [#95](issues/95.md) | High: HierarchicalCodelist untested | closed |
| [#96](issues/96.md) | Medium: Annotations only tested on DataStructure | closed |
| [#97](issues/97.md) | Medium: AttachmentConstraint (2.1) and DataConstraint role=Forbidden (3.0) | closed |
| [#98](issues/98.md) | Medium: Attribute attachment levels — Group / Dimension / DataSet untested | closed |
| [#99](issues/99.md) | Medium: Contact multi-field — Telephone / Fax / URI / x400 untested | closed |
| [#100](issues/100.md) | Low: Multi-language tag bias — only en/fr appear | closed |
| [#101](issues/101.md) | Low: ReferencePeriod / ReleaseCalendar on constraints untested | closed |
| [#102](issues/102.md) | Vendor SDMX-ML v3.1.0 XSDs alongside 2.1 / 3.0 | closed |
| [#103](issues/103.md) | 3.1 emission: Categorisation drops version attribute | closed |
| [#104](issues/104.md) | 3.1 emission: DataConstraint reworks role attribute | closed |
| [#105](issues/105.md) | model: add Hierarchy.has_formal_levels boolean | closed |
| [#106](issues/106.md) | model: add Level.coding_format (Facet) | closed |
| [#107](issues/107.md) | model: extend ValueMap with regex/index variants + per-mapping validity | closed |
| [#108](issues/108.md) | model: add HierarchyAssociation artefact | closed |
| [#109](issues/109.md) | model: add Codelist.extensions (composition / inheritance) | closed |
| [#110](issues/110.md) | model: add HierarchicalCode.valid_from / valid_to | closed |
| [#111](issues/111.md) | model cleanup: replace stringly-typed enums (usage, constraint_type) with Python enums | closed |
| [#112](issues/112.md) | model audit: verify Dataflow.dimension_constraint and ProvisionAgreement.is_final | closed |
| [#113](issues/113.md) | model: add Dataflow.dimension_constraint (3.1) | closed |
| [#114](issues/114.md) | model: add is_final to Dataflow, CategoryScheme, Categorisation, ConceptScheme, StructureMap | closed |
| [#115](issues/115.md) | model: add 2.1-only AttachmentConstraint artefact + serializers | closed |
| [#116](issues/116.md) | perf: intern language codes in InternationalString to cut XML parse allocations | closed |
| [#117](issues/117.md) | perf: cache SdmxUrn.__str__ to cut registry-build + JSON-parse allocations | closed |
| [#118](issues/118.md) | perf: ItemList.__getitem__ dispatch overhead (2-3× vs raw dict) | closed |
| [#119](issues/119.md) | perf: Component.codelist property chain ~35 ns per resolve | closed |
| [#120](issues/120.md) | coverage: SDMX-ML 3.1 reader at 51% — needs broader fixture coverage | closed |
| [#121](issues/121.md) | coverage: SDMX-ML 3.1 writer at 56% — needs broader fixture coverage | closed |
| [#122](issues/122.md) | coverage: HierarchyQuery DSL at 65% — filter combinations untested | closed |
| [#123](issues/123.md) | coverage: SDMX-JSON writer at 69% — artefact-type write paths untested | closed |
| [#124](issues/124.md) | coverage: Codelist SKOS hierarchy methods at 72% — entire block (lines 161-227) untested | closed |
| [#125](issues/125.md) | coverage: storage/readers.py at 72% — DataConstraint / Categorisation / AgencyScheme / ProvisionAgreement read paths untested | closed |
| [#126](issues/126.md) | coverage: LazyHierarchy at 79% — query methods untested | closed |
| [#127](issues/127.md) | DSL: node.annotations[type] filters Annotation.title only — text (istring) silently invisible | closed |
| [#128](issues/128.md) | JSON writer non-conformance: items vs named arrays, isFinal, name vs names | closed |
| [#129](issues/129.md) | JSON conformance follow-up: DSD components + DataConstraint shape | closed |
| [#130](issues/130.md) | model: extend ConstraintAttachment with DataProvider attachment kind | closed |
| [#131](issues/131.md) | model: RepresentationMap source/target as dataType (not just artefact Ref) | closed |
| [#132](issues/132.md) | perf: explore lxml iterparse for streaming SDMX-ML parse | closed |
| [#133](issues/133.md) | Replace polars per-batch CSV writer with pyarrow.csv.CSVWriter for 3–5× streaming throughput | closed |
| [#134](issues/134.md) | design: library-level primitive for referencepartial (codes-actually-in-data per codelist) | closed |
| [#135](issues/135.md) | ComponentFilter: support SDMX 3.0 Hierarchy selection for within[] descent | closed |
| [#136](issues/136.md) | Registry.add(HierarchyAssociation) raises TypeError: Unsupported artefact type | closed |
| [#137](issues/137.md) | default_hierarchy() raises on multiple HAs — too strict vs SDMX 3.x IM | closed |
| [#138](issues/138.md) | perf: internal inbound-Ref index on InMemoryRegistry for O(1) applicable-* queries | closed |
| [#139](issues/139.md) | perf: Registry persist is per-row INSERT for artefact_ref / spine / localized_text (3M+ rows → hours) | closed |
| [#140](issues/140.md) | Registry.add_all still slow for large codelists + hierarchies after 0.35.0 — likely codelist Code rows + hierarchy Node rows not yet batched | closed |
| [#141](issues/141.md) | epic: end-to-end persist-phase perf push (post-0.35.0) | closed |
| [#142](issues/142.md) | perf: per-artefact progress logging in Registry.transaction()/add_all (epic #141 step 1) | closed |
| [#143](issues/143.md) | perf: skip _delete_artefact_cascade on first-time writes (epic #141 step 2) | closed |
| [#144](issues/144.md) | perf: type-dispatched _delete_artefact_cascade (epic #141 step 3) | closed |
| [#145](issues/145.md) | perf: eliminate localized_text/annotation LIKE scans (epic #141 step 4) | closed |
| [#146](issues/146.md) | perf: _put_refs executemany is the real bottleneck — switch to Arrow bulk insert (epic #141) | closed |
| [#147](issues/147.md) | Enforce IDType / NestedIDType charset validation on write-side paths by default | closed |
| [#148](issues/148.md) | Generic outbound-ref iterator on MaintainableArtefact | closed |
| [#149](issues/149.md) | TIME_PERIOD start/end ignores FREQ — multi-year periods (A5, A10) emit single-year bounds | closed |
| [#150](issues/150.md) | Canonical Codelist-to-SDMX-JSON serializer (for cache pre-bake) | closed |
| [#151](issues/151.md) | Add canonical ContentConstraint builder + SDMX-JSON serialiser | closed |
| [#152](issues/152.md) | Extend build_dataflow_constraint for per-component availability slices | closed |
| [#153](issues/153.md) | browse() then get().fetch() returns the cached identity stub instead of the full artefact | closed |
| [#154](issues/154.md) | LazyHierarchy.materialize() — 15s for 700k-node trees due to per-node attrs construction | closed |
| [#155](issues/155.md) | concurrency: single-flight code_map to dedup concurrent cold-cache builds (thundering herd) | closed |
| [#156](issues/156.md) | Clearer error when a bundle's DuckLake catalog format exceeds the installed extension | closed |
| [#157](issues/157.md) | SDMX-JSON writer drops annotations on maintainable artefacts (Dataflow at minimum) | closed |
| [#158](issues/158.md) | SDMX-ML writers drop annotations on items/components — silent round-trip loss (sibling of #157) | closed |
| [#159](issues/159.md) | Serializer round-trip audit: systematic silent field drops across ML/JSON/storage (post #157/#158) | closed |
| [#160](issues/160.md) | Storage round-trip fidelity: Codelist extensions, DataConstraint reference_period/release_calendar, Level codingFormat | closed |
| [#161](issues/161.md) | SDMX-CSV writer: labels=name mode (interleave <dim>_label columns) | closed |
| [#162](issues/162.md) | SDMX-JSON writer: emit METADATA annotation on DataStructure when a metadataflow targets a dataflow using it | closed |
| [#163](issues/163.md) | Expose public schema-introspection API (describe/shared columns) + cache per-query DESCRIBE | closed |
| [#164](issues/164.md) | design: QueryPlan as a full statement descriptor (Design-B) — composable query object with to_sql() | closed |
| [#165](issues/165.md) | [T1] Type registry write seams with HasUrn (drop Any) | closed |
| [#166](issues/166.md) | [T1] Narrow heterogeneous artefact stores to dict[str, object] | closed |
| [#167](issues/167.md) | [T1] Bound ItemList[T: HasId] — remove the id cast + pyright ignore | closed |
| [#168](issues/168.md) | [T1] Recover the key type in ItemList/ArtefactList.to_dict via [K] overload | closed |
| [#169](issues/169.md) | [T2] Narrow reader resolution index dict[str, Any] -> dict[str, object] | closed |
| [#170](issues/170.md) | [T2] Replace duck-typed m: Any writer/storage params with a Maintainable Protocol | closed |
| [#171](issues/171.md) | [T2] Parameterize generic reader helpers with PEP-695 type params | closed |
| [#172](issues/172.md) | [T2] Precise small Any narrowings (NSMAP, dispatch tables, freq-dim, polars cast) | closed |
| [#173](issues/173.md) | [T3] Prototype covariant Ref[T_co] and measure Ref[Any]/cast collapse | closed |
| [#174](issues/174.md) | bug: hierarchy_query passes a bare Code to artefact_urn (AttributeError — Code isn't maintainable) | closed |
| [#175](issues/175.md) | design: standardize internal structural Protocol naming (Has* vs Supports* vs domain nouns) | closed |
| [#176](issues/176.md) | design: type the SDMX-JSON parse boundary (TypedDict / recursive JSONRO instead of Any) | closed |
| [#177](issues/177.md) | Reader ref-resolution witness is decorative: Ref[T] return is an unbacked downcast | closed |
| [#178](issues/178.md) | msgspec migration 1 — Foundation: dependency, shared DTO Structs & conventions | closed |
| [#179](issues/179.md) | msgspec migration 2 — De-risk the data plane: numpy serialization & streaming under msgspec (investigation) | closed |
| [#180](issues/180.md) | msgspec migration 3 — Structure JSON write via Struct DTOs | closed |
| [#181](issues/181.md) | msgspec migration 4 — Structure JSON read via typed decode + ingest | closed |
| [#182](issues/182.md) | msgspec migration 5 — Metadata JSON read+write via Struct DTOs | closed |
| [#183](issues/183.md) | msgspec migration 6 — Data JSON v1 + v2 migration (streaming + numpy) | closed |
| [#184](issues/184.md) | msgspec migration 7 — SDMX-JSON 1.0 ContentConstraint writer migration | closed |
| [#185](issues/185.md) | msgspec migration 8 — Remove orjson; update deps, CLAUDE.md & design docs | closed |
| [#186](issues/186.md) | SDMX-JSON: type the last dict islands (CodelistExtension / CodeSelection / MemberValue) | closed |
| [#187](issues/187.md) | Metadata JSON: MetadataSet MSD-direct ref emits non-conformant `metadataStructure` key | closed |
| [#188](issues/188.md) | Version default "1.0" fails metadata JSON schema's strict SemVer VersionType | closed |
| [#189](issues/189.md) | Derive DSD→MSD metadata association from declared Metadataflow/MPA targets (not just populated MetadataSets) | closed |
| [#190](issues/190.md) | Support wildcard (WildcardUrnType) targets in DSD→MSD metadata derivation | closed |
| [#191](issues/191.md) | Emit native SDMX 3.0 reference-metadata links (replace the .Stat METADATA-annotation compat shim) | closed |
| [#192](issues/192.md) | [S1] _maint_fields: Protocol param + TypedDict return — removes 21 type-ignores | closed |
| [#193](issues/193.md) | [S1] Allow `id` as a builtin shadow in config — removes 21 A002/A001 noqas | closed |
| [#194](issues/194.md) | [S1] RegistryReader must describe the registries we actually have — removes 2 attr-defined ignores | closed |
| [#195](issues/195.md) | [S2] Migrate # type: ignore[mypy-code] → # pyright: ignore[rule] (46 sites) + fix the CLAUDE.md rationale | closed |
| [#196](issues/196.md) | [S2] httpx pass-through kwargs: **kwargs: Any + allow-star-arg-any — 1 ignore hiding 17 errors | closed |
| [#197](issues/197.md) | [S3] storage/lazy.py D102: one decision instead of 20 noqas | closed |
| [#198](issues/198.md) | [S3] SLF001: per-file ignores for the 4 intra-package hot files (34 noqas → 4 decisions) | closed |
| [#199](issues/199.md) | [S4] Complexity debt: 12 C901/PLR0915/PLR0912 suppressions across 9 functions | closed |
| [#200](issues/200.md) | [S4] Any inventory: known-shape dict[str, Any] payloads + 6 leaky -> Any returns | closed |
| [#201](issues/201.md) | [S4] Test whether PEP 695 variance inference removes the UP046 noqa on SdmxUrn | closed |
| [#202](issues/202.md) | [S4] Type the 9 writer/storage `Any` params that already have a concrete model type | closed |
| [#203](issues/203.md) | [S4] Design the small Protocols for the remaining duck-typed `Any` params (13 sites) | closed |
| [#204](issues/204.md) | [S4] Document the 8 `Any` annotations that are correct and should stay | closed |
| [#205](issues/205.md) | [S4] Property-guard round-trips emit non-conformant XML: 25 XSD failures | closed |
| [#206](issues/206.md) | idea: can the URN-keyed stores recover T without a suppression? (7 sites) | closed |
| [#207](issues/207.md) | design: a written rule for the public/private barrier (underscores, package-internal seams, SLF001) | closed |
| [#208](issues/208.md) | design: a package-internal seam for Ref resolution (the friend-function case) | closed |
| [#209](issues/209.md) | refactor: SDMX-ML closure factories -> classes (measured: no perf cost); drop the legacy rebindings | closed |
| [#210](issues/210.md) | typing: bind the writer dispatch tables so attribute and converter must agree | closed |
| [#211](issues/211.md) | test-1: shared artefact factories — collapse the duplicated builders across tests/ | closed |
| [#212](issues/212.md) | test-2: mechanical test audit — coverage-gap verdicts, assertion-free tests, style | closed |
| [#213](issues/213.md) | test-3: mutmut pilot — does 91% line coverage mean anything? | closed |
| [#214](issues/214.md) | test-4: semantic test audit — tests coupled to implementation detail | closed |
| [#215](issues/215.md) | test-5: Hypothesis foundation — strategies for SDMX value types, plus dev/CI profiles | closed |
| [#216](issues/216.md) | test-6: Hypothesis round-trip properties across every serializer | closed |
| [#217](issues/217.md) | test-7: Hypothesis differential tests — query DSLs against a pure-Python oracle | closed |
| [#218](issues/218.md) | test-8: mutation testing — adopt, narrow, or drop (decision, with numbers) | closed |
| [#219](issues/219.md) | typing: Ref has two constructor forms and declares one — keep only Ref.to(obj) | closed |
| [#220](issues/220.md) | parse() misroutes structure messages containing 'GenericMetadata' — silent data loss | closed |
| [#221](issues/221.md) | validate() accepts maintainable and agency ids the XSD rejects | closed |
| [#222](issues/222.md) | code_query: id equality is case-sensitive but contains() is not | closed |
| [#223](issues/223.md) | Spec-coverage matrix: 18 of 20 artefact classes have no generative schema-validated coverage | closed |
| [#224](issues/224.md) | DataStructure: six ways to build an artefact no SDMX format can validly serialise | closed |
| [#225](issues/225.md) | SDMX-ML 2.1 writer silently dropped metadataflows, MSDs and hierarchy associations | closed |
| [#226](issues/226.md) | Six wire-conformance defects on the map, constraint and metadata paths | closed |
| [#227](issues/227.md) | Phase 1: gate — every artefact must serialise validly or be refused | closed |
| [#228](issues/228.md) | Phase 2: extract required child elements (minOccurs>=1) from the XSDs | closed |
| [#229](issues/229.md) | Phase 3: close the 24 (artefact, format) pairs that emit invalid documents | closed |
| [#230](issues/230.md) | Phase 4: extract required and prohibited attributes (silent-loss detection) | closed |
| [#231](issues/231.md) | Phase 5: audit the xs:unique identity constraints | closed |
| [#232](issues/232.md) | Phase 6: extend conformance coverage to MetadataMessage | closed |
| [#233](issues/233.md) | A 'latest'-version Ref serialises to a URN SDMX-ML 3.0 rejects | closed |
| [#234](issues/234.md) | MetadataMessage was serialised without validation, and emitted invalid 3.0 headers | closed |
| [#235](issues/235.md) | MetadataStructure has no faithful SDMX-ML 2.1 form: 3.0 -> 2.1 -> 3.0 is not an identity | closed |
| [#236](issues/236.md) | Reading a 2.1 MetadataStructure silently discards it | closed |
| [#237](issues/237.md) | Reading must not silently drop or reshape data — audit and enforce | closed |
| [#238](issues/238.md) | Artefact 'uri' is never read or written, so it is lost on every round-trip | closed |
| [#239](issues/239.md) | SDMX-ML 2.1 container artefacts lose their identity: StructureSet and HierarchicalCodelist | closed |
| [#240](issues/240.md) | A <Ref> without @package is silently dropped, though the schema makes it optional | closed |
| [#241](issues/241.md) | DataConstraint cannot hold a DataKeySet, so 3.0 documents carrying one lose it | closed |
| [#242](issues/242.md) | Cross-version round-trips: shrink refusals to the ones SDMX forces | closed |
| [#243](issues/243.md) | enumeration_format is written and read only in 2.1, silently dropped elsewhere | closed |
| [#244](issues/244.md) | Convert AttachmentConstraint to DataConstraint on 3.x write instead of refusing | closed |
| [#245](issues/245.md) | MSD targets relocated from the structure to the flow: decide the 2.1 <-> 3.0 story | closed |
| [#246](issues/246.md) | Cross-version fidelity harness: assert what converts, what blocks, and what is lossy | closed |
| [#247](issues/247.md) | Fuzz the readers: one declared exception, and pin the hardening we inherited | closed |
| [#248](issues/248.md) | dsl-1: hierarchy_query has 504 statements and zero property tests | closed |
| [#249](issues/249.md) | dsl-2: the code_query oracle models 8 of ~25 DSL operations | closed |
| [#250](issues/250.md) | dsl-3: hostile filter values — the #222 LIKE-metacharacter class, systematically | closed |
| [#251](issues/251.md) | dsl-4: Query[T], item() and the SQL seam have no generative coverage | closed |
| [#252](issues/252.md) | dsl-5: extend mutation scope to the query compilers, and answer #218's properties-vs-examples question | closed |
| [#253](issues/253.md) | dsl-6: triage the 150 mutants the #213 pilot left standing | closed |
| [#254](issues/254.md) | Annotation filters duplicate rows: one item, two annotations of a type, two results | closed |
| [#255](issues/255.md) | code_query folds accents and punctuation in label search; hierarchy_query does not | closed |
| [#256](issues/256.md) | ComponentFilter emits 'col IN ()' for an empty OR-set, and empty means two different things | closed |
| [#257](issues/257.md) | dsl-7: structural AST invariants over every emitted SQL (DuckDB runs multi-statement input) | closed |
| [#258](issues/258.md) | dsl-8: the data plane has no differential tests — ComponentFilter to SELECT, against a Polars oracle | closed |
| [#259](issues/259.md) | dsl-9: make the query compilers total — 18 NotImplementedErrors on the to_sql() path | closed |
| [#260](issues/260.md) | dsl-10: three missing algebraic laws, and limit()/with_annotations() composition is undocumented | closed |
| [#261](issues/261.md) | dsl-11: no set-membership operator — code.id in (A, B, C) is inexpressible | closed |
| [#262](issues/262.md) | dsl-12: give the query DSLs real And/Or/Not instead of a per-field negate flag | closed |
| [#263](issues/263.md) | dsl-13: depth supports only == and !=, and the module docstring documents 'node.depth <= 2' | closed |
| [#264](issues/264.md) | dsl-14: no offset or cursor — pagination is only reachable via the raw-SQL escape hatch | closed |
| [#265](issues/265.md) | dsl-15: the whole accessor chain is typed in private names, and sort_by(key: _Ref) is in the published docs | closed |
| [#266](issues/266.md) | dsl-16: no misuse tests — is_in("CA") shipped because the oracle shared the bug | closed |
| [#267](issues/267.md) | dsl-17: the two query DSLs are 23 byte-identical names apart — factor the shared compiler | closed |
| [#268](issues/268.md) | dsl-18: four of CompileContext's five fields are dead — record why before building on it | closed |
| [#269](issues/269.md) | 19 benchmarks fail on main — bench fixtures build artefacts validate() rejects | closed |
| [#270](issues/270.md) | dsl-19: to_sql() is order-dependent — and so is the IR, so downstream cannot key on either | closed |
| [#271](issues/271.md) | dsl-20: a 5000-code is_in emits 15KB of SQL and 5001 binds | closed |
| [#272](issues/272.md) | dsl-21: code.id == 1 builds and binds an int against a VARCHAR column | closed |
| [#273](issues/273.md) | dsl-22: formalise the IR/SQL boundary — what the design brief gets right, and what it does not | closed |
| [#274](issues/274.md) | q-1: hierarchy rollup — applications hand-roll a 5-step pipeline we can answer in one query | closed |
| [#275](issues/275.md) | q-2: projection and aggregation — why sdmx.code is hand-queried 29 times despite having a DSL | closed |
| [#276](issues/276.md) | q-3: fourteen tables have no query DSL — 76 hand-written sites, mostly artefact discovery | closed |
| [#277](issues/277.md) | q-4: availability loses to hand-written SQL — N scans where consumers write one | closed |
| [#278](issues/278.md) | q-5: every capability in this milestone ships an executable cookbook entry | closed |
| [#279](issues/279.md) | Component.concept accepts a bare item and the writer silently drops it | closed |
| [#280](issues/280.md) | q-4: dsd_component has no query — 21 hand-written sites, the component surface across the catalogue | closed |
| [#281](issues/281.md) | q-5: reference metadata has no query — 13 hand-written sites, and the storage is already flat | closed |
| [#282](issues/282.md) | t-1: three audit findings — a doc comment ruff reads as a directive, and two relocated Any | closed |
| [#283](issues/283.md) | t-2: ruff warnings do not fail the gate, so nobody reads them | closed |
| [#284](issues/284.md) | t-3: ratchet the Any count — ANN401 does not see inside generic arguments | closed |
| [#285](issues/285.md) | t-4: dict[str, Any] — 16 sites with the clearest ruling | closed |
| [#286](issues/286.md) | t-5: SdmxUrn[Any] — 25 sites, triage not sweep | closed |
| [#287](issues/287.md) | t-6: Ref[Any] — 16 sites, check whether AnyRef already covers them | closed |
| [#288](issues/288.md) | t-7: list[Any] — 39 sites in the untyped shell, lowest value | closed |
| [#289](issues/289.md) | t-8: Ref is invariant, which is why Ref[Any] is everywhere | closed |
| [#290](issues/290.md) | ItemList.__hash__ / ArtefactList.__hash__ are declared but raise for every domain item type | closed |
| [#291](issues/291.md) | Query[T].filter mutates in place, so two branches off one query silently AND together | closed |
| [#292](issues/292.md) | dsl-23: no way to ask whether an annotation type is present at all | closed |
| [#293](issues/293.md) | dsl-24: write down the refinement principle, and enforce it with a cross-DSL property | closed |
| [#294](issues/294.md) | dsl-25: sort_by appends, so a caller's sort is silently a no-op | closed |
| [#295](issues/295.md) | dsl-26: rename sort_by to order_by | closed |
| [#296](issues/296.md) | dsl-27: filter()/sort_by() after limit()/offset() is ambiguous and answers silently | closed |
| [#297](issues/297.md) | data-plane: _build_query interpolates caller SQL text — model it, or fence it | closed |
| [#298](issues/298.md) | store: no epoch a consumer can compose into a cache key | closed |
| [#299](issues/299.md) | export hygiene: types named in public signatures outside the query DSLs are not importable | closed |
| [#300](issues/300.md) | execution handle: three divergent handles, no setup hook, no release — consolidate before #271 | closed |
| [#301](issues/301.md) | is_in: should a wide set register automatically? | closed |
| [#302](issues/302.md) | metadata_query has no property tests — 289 statements, one hand-written file, no oracle | closed |
| [#303](issues/303.md) | mutmut books surviving validation mutants as timeouts — 283 of them, and they are not hangs | closed |
| [#304](issues/304.md) | extend mutation scope to the query compilers, and answer #218's properties-vs-examples question | closed |
| [#305](issues/305.md) | A DataStructure holding a MeasureDimension cannot be serialised at all — AttributeError, every version | closed |
| [#306](issues/306.md) | One query language over the SDMX information model | closed |
| [#307](issues/307.md) | Concept.parent is parsed and silently dropped — nested concept schemes come back flat | closed |
| [#308](issues/308.md) | q-6: constraints need a role-filtered junction edge — the attachment lives in artefact_ref | closed |
| [#309](issues/309.md) | DataProvider.contacts and MetadataProvider are declared in the model and never persisted | closed |
| [#310](issues/310.md) | qualify(): call order does not decide the window's input set, and the docstring said it did | closed |
| [#311](issues/311.md) | version ordering is lexicographic — 1.9 sorts after 1.10, so "the latest version" is wrong | closed |
| [#312](issues/312.md) | §4: compile the path language to a second backend, with a capability model that refuses rather than fetches | closed |
| [#313](issues/313.md) | cookbook tail: six Query/Frame methods have no entry, including unique() and explain() | closed |
| [#314](issues/314.md) | mutmut deadlocks on the projection tests — a third failure mode, and it blocks _compile.py's real score | closed |
| [#315](issues/315.md) | MetadataProvider is not modelled at all — the MPA's provider urn points at a relation that does not exist | closed |
| [#316](issues/316.md) | derived round-trip guard: three 'declared in the model, never persisted' bugs shipped because an absence does not fail a test | closed |
| [#317](issues/317.md) | Writer: 43 SQL statements per artefact — a type-blind cascade and a per-value pandas probe | closed |
| [#318](issues/318.md) | detail= has no model in the path language: every REST fetch is detail=full, and browse()'s cheap listing is gone | closed |
| [#319](issues/319.md) | version validation implements SDMX-ML 2.1's grammar, but the library reads and writes 3.0 — the two disagree in both directions | closed |
| [#320](issues/320.md) | is_final and uri are persisted for some maintainables and silently dropped for six others | closed |
| [#321](issues/321.md) | a timezone-aware valid_from/valid_to round-trips to a different instant, naive | closed |
| [#322](issues/322.md) | annotations are lost on Codelist and Hierarchy — the two types read back through a Lazy proxy | closed |
| [#323](issues/323.md) | storage: Dataflow.dimension_constraint is written but read back as the default | closed |
| [#324](issues/324.md) | Triage the _compile.py mutation survivors — 143 left, one cluster done | closed |
| [#325](issues/325.md) | an LLM agent built on sdmxlib writes SQL against five sdmx.* tables and never calls reg.query() — find out why | closed |
| [#326](issues/326.md) | n_unique carries a polars name with SQL null semantics — len(unique()) != n_unique() | closed |
| [#327](issues/327.md) | the aggregate set is thin: no sum/avg/min/max, and no ordered aggregate | closed |
| [#328](issues/328.md) | ordering comparisons live on Num, so a text column has no range filter | closed |
| [#329](issues/329.md) | replace the data plane's SQL-fragment API with catalog.sql(), mirroring reg.sql() | closed |
| [#330](issues/330.md) | store observations sorted at ingestion — for pruning and cheaper sorts, not to skip the sort | closed |
| [#331](issues/331.md) | an ordered stream at bounded memory: read partitions in key order, sort within | closed |
| [#332](issues/332.md) | read a sidecar binding: flat spelling, level-aware plan | closed |
| [#333](issues/333.md) | InMemoryRegistry claims Registry conformance it does not need | closed |
| [#334](issues/334.md) | REST fetch serves only the structure plane — no /metadata requests | closed |
| [#335](issues/335.md) | An unfiltered structural scan is never bound-checked — the widest request is the one nothing refuses | closed |
| [#336](issues/336.md) | RestRegistry's fetch cache has no lock, no single-flight, and no bound | closed |
| [#337](issues/337.md) | sdmxlib cannot parse the SDMX key it generates — the two halves disagree about the time dimension | closed |
| [#338](issues/338.md) | `latest` breaks first-hit-wins: the chain's two caches have opposite policies (design §5.16) | closed |
| [#339](issues/339.md) | a freshness window for the `latest` pointer: honour Cache-Control/ETag, with a caller override | closed |
| [#340](issues/340.md) | a REST query answers from the whole store, not from its own fetch (design §5.13) | closed |
| [#341](issues/341.md) | list()/unique() answer NULL for an empty fan-out where polars answers [] | closed |
| [#342](issues/342.md) | a to-many projection is refused at collect(), a projected filter at the call — same page, two moments | closed |
| [#343](issues/343.md) | FederatedRegistry.get takes no resolve=, so a federation cannot ask for a resolved fetch | closed |
| [#344](issues/344.md) | partial=False is ignored for a pinned URN — the resolved cache does not record how completely it resolved | closed |
| [#345](issues/345.md) | SDMX-ML 3.1 silently drops RepresentationMap data-type endpoints and Level/CodingFormat, and gaps.py says nothing is lost | closed |
| [#346](issues/346.md) | sdmx_ml31 is a 98% copy of sdmx_ml30 — the fork is what makes #345-shaped bugs cheap | closed |
| [#347](issues/347.md) | tests/ is not type-checked, so 59 suppressions in it were dead and nothing could say so | closed |
| [#348](issues/348.md) | Hierarchy ops materialise every node in Python: 220s where SQL takes 0.7s | closed |
| [#349](issues/349.md) | the DSL emits a redundant EXISTS for ancestor hops, and .ids() selects every column — 3.6x on hierarchy walks | closed |
| [#350](issues/350.md) | ~col.is_in(subquery) returns nothing when the subquery has a NULL — the anti-join is broken | closed |
| [#351](issues/351.md) | version.min()/max() compare as text, so agg(version.max()) says 1.9 is later than 1.10 | closed |
| [#352](issues/352.md) | edge() resolves targets by class-definition order, so 17 paths dead-end at _Maintainable and file order is public API | closed |
| [#353](issues/353.md) | the oracle's strip_accents model disagrees with DuckDB on 527 characters — a property fails at 20x on main | closed |
| [#354](issues/354.md) | Category and CategoryScheme annotations are dropped by every SDMX-ML writer | closed |
| [#355](issues/355.md) | Round-trip field losses found by the #354 census (~50 per format) | closed |
| [#356](issues/356.md) | SDMX-ML 2.1 writer emitted <MetadataStructures> out of sequence | closed |
| [#357](issues/357.md) | is_final: emitted where the schema forbids it, and not read back where it exists | closed |
| [#358](issues/358.md) | Should SDMX-JSON carry isFinal at all? | closed |
| [#359](issues/359.md) | Twenty register entries were the fixture setting both sides of an SDMX choice | closed |
| [#360](issues/360.md) | Reader fabricates related_measure="OBS_VALUE" for 3.0 observation attributes | closed |
| [#361](issues/361.md) | Model Process / ProcessStep — the IM's pipeline and provenance artefact | closed |
| [#362](issues/362.md) | MetadataProvisionAgreement is half-wired: modelled and storable, no StructureMessage slot | closed |
| [#363](issues/363.md) | Release blocker? select() names frame columns after the storage column, not the accessor | closed |
| [#364](issues/364.md) | SDMX-JSON: vendor the 2.0 structure schema, validate what we emit, and support 2.1 | closed |
| [#365](issues/365.md) | SDMX-JSON data writers never emit a value's `parent`, so hierarchical codelists arrive flat | closed |
| [#366](issues/366.md) | SDMX-JSON data writer emits no value-level annotations, so Explorer cannot order values | closed |
| [#367](issues/367.md) | Artefact ids are never checked against IDType, so an invalid id serialises silently to SDMX-JSON | closed |
| [#368](issues/368.md) | AnnotationValue is silently dropped: SDMX 3.0 and SDMX-JSON carry it, the model has no field | closed |
| [#369](issues/369.md) | Typed annotation vocabularies — read a creator's conventions (e.g. .Stat) without stringly-typed lookups | closed |
| [#370](issues/370.md) | LocalRegistry.get accepts a str its signature denies, and disagrees with RestRegistry.get | closed |
| [#371](issues/371.md) | 30 model fields declare tuple[...] with no converter, so a list stays a list | closed |
| [#372](issues/372.md) | Hierarchy descent: the layering, not just the seam — a (dataflow_urn, code_id) relation | closed |
| [#373](issues/373.md) | Every aggregate over a fan-out is unconditional — `list(...)` cannot be restricted | closed |
| [#374](issues/374.md) | Offer: a docstring gate — 0 findings today, and ruff's google convention does not cover it | closed |
| [#375](issues/375.md) | intersect two subtrees without the ids round-tripping through Python | closed |
| [#376](issues/376.md) | eager get() spends 42% of a codelist read walking Codes that cannot hold a Ref | closed |
| [#377](issues/377.md) | three per-row allocations the row builders make and discard — 52% of q.code.collect(), 28% of q.hcode.collect() | closed |
| [#378](issues/378.md) | eager get of a hierarchy costs its codelist, not its size — 3.3 s and 1.28 GB for 28 nodes | closed |
| [#379](issues/379.md) | no benchmark can see any of %18 — the suite's largest fixture is 10k items and carries no annotations | closed |
| [#380](issues/380.md) | 22% of a collect() is DuckDB turning two MAP columns into Python dicts, before any object exists | closed |
| [#381](issues/381.md) | annotations are 32% of a collect() and the docstring saying they are cheap was measured at 10k | closed |
| [#382](issues/382.md) | %18's "collect() within 5x of ids()" criterion is unreachable by construction — replace it | closed |
| [#383](issues/383.md) | a Ref walk visits every hierarchy node twice — tree and the _codes flat index are the same objects | closed |
| [#384](issues/384.md) | resolving a Ref re-runs the field's converter on a value that is already a Ref — 10x on setattr | closed |
| [#385](issues/385.md) | try a trusted construction path in the readers — converters are 2.5x, and attrs itself is not the cost | closed |
| [#386](issues/386.md) | the resolver cache is bounded in entries, and its entries range from 0.1 MB to 1372 MB | closed |
| [#387](issues/387.md) | the data plane is unreachable on a real bundle, by both documented routes | closed |
| [#388](issues/388.md) | every observation query joins the sidecar, referenced or not — 2460x on a whole-lake count | closed |
| [#389](issues/389.md) | the default ORDER BY is 35x the scan it decorates, and only stream() can decline it | closed |
| [#390](issues/390.md) | the bundle bench tier is not in `just ci`, so an API rename breaks it silently | closed |
| [#391](issues/391.md) | the bench tier reads no observations, so neither %19 fix has real-bundle coverage | closed |
| [#392](issues/392.md) | Refs in a plain list or tuple are never resolved — six model fields, eager reads included | closed |
| [#393](issues/393.md) | the query DSL has 3 consumer call sites against 132 raw sdmx.* SQL references — nobody has asked why | closed |
| [#394](issues/394.md) | all_artefacts() serves validation and storage with one list, and fails both | closed |
| [#395](issues/395.md) | Six artefact types have no storage kind — three deferred deliberately, three never raised | closed |
| [#396](issues/396.md) | Query.collect() is declared list[object], so every collected artefact needs re-narrowing | closed |
| [#397](issues/397.md) | LocalRegistry.get declares Codelist/Hierarchy but returns the lazy classes, so h.codes type-checks and raises | closed |
| [#398](issues/398.md) | Dimension.codes declares ItemList but returns a Query when the DSD came from a LocalRegistry | closed |
| [#399](issues/399.md) | get(SdmxUrn.parse(...)) types as LazyCodelist whatever the URN names — #397's overloads and Any | closed |
| [#400](issues/400.md) | DuckDB 1.5.3 filter pushdown returns wrong answers on the 2021 Census Profile bundle — silent data loss on read | closed |
| [#401](issues/401.md) | test(bench): the one wall-clock ratio in the bundle tier is flaky in the gate | closed |
| [#402](issues/402.md) | Mutation runs die silently when a test reads a path mutmut does not copy — twice now | closed |
| [#403](issues/403.md) | Guards that stop being able to fail — four in one session, and the rule that catches them | closed |
| [#404](issues/404.md) | validate_references expands item URNs for ConceptScheme only — every Code, Category and Dimension ref reads as broken | closed |
| [#405](issues/405.md) | Readers index Concept items and no others — every Code, Category and Dimension ref parses unresolved | closed |
| [#406](issues/406.md) | Two seams sdmxlib-rest patches around: the JSON writer's missing self-URN, and a class-checked `get` | closed |
| [#407](issues/407.md) | Codelist and DataStructure have no ref_to — #404's constructive half, so every 3.0 Hierarchy author builds item URNs by hand | closed |
| [#408](issues/408.md) | Dataset.validate can never report an invalid code after scan_csv — the DSD-derived pl.Enum schema raises first | closed |
| [#409](issues/409.md) | scan_csv requires the DSD the file already names — no path from an SDMX-CSV message plus a registry to a Dataset | closed |
| [#410](issues/410.md) | SDMX-JSON data writer materialises every DSD codelist per message (perf regression since 0.62.0, #398) | closed |
| [#411](issues/411.md) | DuckDB re-imports pandas twice per bound parameter when pandas is absent — and #271's per-value cost was mostly this | closed |
| [#412](issues/412.md) | A non-unique series-attributes sidecar silently multiplies every query that reaches it | closed |
| [#413](issues/413.md) | Observation columns have no string-matching predicates, so SDMX-REST's c[] co/nc/sw/ew filters can't use the DSL | closed |
| [#414](issues/414.md) | No root-level multi-aggregate: distinct values per column plus a count can't be computed in one scan | closed |
| [#415](issues/415.md) | Temporal query columns compare tz-aware literals in the session's zone, and == refuses a datetime | closed |
| [#416](issues/416.md) | Organisation-scheme version is 1.0 in every format, but SDMX-JSON writes any value and 3.x silently drops it for MetadataProviderScheme | closed |
| [#417](issues/417.md) | SDMX-JSON writer checks no version grammar: 1.2.3.4 is written schema-invalid, and the JSON structure schema does constrain it | closed |
| [#418](issues/418.md) | CodelistExtension.codelist accepts a URN string, then the SDMX-ML 3.0 writer crashes with AttributeError | closed |
| [#419](issues/419.md) | Validate format-independent rules at Registry.add: the store holds artefacts no format can write | closed |
| [#420](issues/420.md) | Version and finality: model 3.x semantics and project to 2.1 (the two grammars disagree in both directions) | closed |
| [#421](issues/421.md) | Union shapes → one canonical 3.x shape with declared 2.1 conversions (measures, extensions, hierarchies, maps, constraints, MSD) | closed |
| [#422](issues/422.md) | SDMX 3.x content the model cannot hold is dropped silently on read | closed |
| [#423](issues/423.md) | Tracking: 2.1 ↔ 3.x incompatibility catalogue for a 3.x-native model | closed |
| [#424](issues/424.md) | Version and finality decisions: derive is_final, refuse 2.1-only versions unless explicitly rewritten, drop drafts from 2.1 | closed |
| [#425](issues/425.md) | Make pyrefly the only type checker — retire basedpyright | closed |
| [#426](issues/426.md) | LocalRegistry.observations() costs ~13–19 ms per call: the flow, DSD and binding are re-read (and pandas re-probed 44 times) on every call | closed |
| [#427](issues/427.md) | SDMX-CSV writer ~22% slower per batch since 0.65: pl.DataFrame(batch) instead of pl.from_arrow (4f65fd7) | closed |
| [#428](issues/428.md) | Re-persisting a Metadataflow (or MetadataProvisionAgreement) with targets violates metadata_declared_target's primary key: cascade doesn't clear it | closed |
| [#429](issues/429.md) | Reference versions are written without checking the target grammar: silently invalid in every format | closed |
| [#430](issues/430.md) | References to non-SDMX classes (FMR PublicationTable, PreDefinedQuery) are written schema-invalid in every format | closed |
| [#431](issues/431.md) | TimeDimension accepts a textType outside TimeDataType and writes it invalid everywhere | closed |
| [#432](issues/432.md) | Code Parent with a leading non-letter (_T) writes invalid SDMX-ML 3.0 (3.0.0 schema erratum, fixed in 3.1) | closed |
| [#433](issues/433.md) | CubeRegion loses KeyValue TimeRange and attribute/component value sets on read, then writes an empty KeyValue | closed |
| [#434](issues/434.md) | 2.1 HierarchicalCodelist in CodelistAliasRef + CodeID form loses every code on read | closed |
| [#435](issues/435.md) | One refused artefact fails the whole document: readers should refuse per artefact | closed |
| [#436](issues/436.md) | Readers silently ignore elements they do not expect (Eurostat str:Name) | closed |
| [#437](issues/437.md) | LocalRegistry drops DataConstraint data_keys, empty KeyValues and key order | closed |
| [#438](issues/438.md) | LocalRegistry drops component enumeration_format | closed |
| [#439](issues/439.md) | Nested items are keyed by leaf id: valid Categories, MetadataAttributes and hierarchy nodes are refused | closed |
| [#440](issues/440.md) | datetime fields are naive or aware depending on the source, so parse != store round trip | closed |
| [#441](issues/441.md) | SDMX-JSON data writer: lazy code resolution costs ~75 ms per message (2 statements per component); batch it | closed |
| [#442](issues/442.md) | LocalRegistry round trip drops value-map validity, level annotations, the MSD 2.1 layer, Agency.parent and most item uris | closed |
| [#443](issues/443.md) | LazyHierarchy.materialize() drops every node's annotations | closed |
| [#444](issues/444.md) | uri is not a queryable path on q.code / q.hcode (or any item relation) | closed |
| [#445](issues/445.md) | SDMX-JSON reader silently ignores keys it does not expect | closed |
| [#446](issues/446.md) | SDMX-ML readers ignore attributes the schema does not allow | closed |
| [#447](issues/447.md) | Closed-world check does not cover the containers above artefacts or the message header | closed |
| [#448](issues/448.md) | Model OrganisationSchemeMap: 3.x readers refuse it, 2.1 reports it lost | closed |
| [#449](issues/449.md) | Dimension positions: accept gaps, refuse disorder | closed |
| [#450](issues/450.md) | Sweep: hierarchy write refusals key on node ids | closed |
| [#451](issues/451.md) | SDMX-JSON writes a wildcarded codelist-extension member value its schema rejects | closed |
| [#452](issues/452.md) | validate: a blank name text (e.g. {"en": ""}) passes _check_name | closed |
| [#453](issues/453.md) | validate: Code.parent_id is never checked to name a code in the codelist | closed |
| [#454](issues/454.md) | validate: SERIES attribute with no related_dimensions passes, then writes an XSD-invalid empty AttributeRelationship | closed |
| [#455](issues/455.md) | SDMX-ML 3.x writer emits Hierarchy levels as flat siblings; the XSD requires a nested chain | closed |
| [#456](issues/456.md) | An information-model rule census: every IM constraint has an owner | closed |
| [#457](issues/457.md) | FMR as an external oracle: our output read and served back by an independent implementation | closed |
| [#458](issues/458.md) | Mutation over the readers and writers: the conformance guards can still fail | closed |
| [#459](issues/459.md) | Measure conformance guards for data and reference-metadata messages | closed |
| [#460](issues/460.md) | Tracking: conformance evidence beyond the structural censuses | closed |
| [#461](issues/461.md) | SDMX-JSON read is 2.3x slower since M20: the unknown-key walk costs more than the decode | closed |
| [#462](issues/462.md) | SDMX-JSON structure write is 3.4x slower since M20: per-code URNs, links and wildcard checks | closed |
| [#463](issues/463.md) | SDMX-ML reads are 32-50% slower since M20: closed-world, links and per-item checks | closed |
| [#464](issues/464.md) | SDMX-ML 2.1 DSD write is 2.3x slower since M20 while 3.0 is flat: the 2.1 projection | closed |
| [#465](issues/465.md) | Structure JSON omits meta.schema, so pysdmx cannot read sdmxlib's output | closed |
| [#466](issues/466.md) | SDMX-JSON read does per-code work twice: converters, links, XML-character checks | closed |
| [#467](issues/467.md) | Model the VTL schemes: Transformation, Ruleset, UserDefinedOperator, VtlMapping, CustomType, NamePersonalisation | closed |
| [#468](issues/468.md) | Model MetadataConstraint (and 2.1's ContentConstraint on a metadata target) | closed |
| [#469](issues/469.md) | Model DataConsumerScheme and OrganisationUnitScheme | closed |
| [#470](issues/470.md) | Model ReportingTaxonomy and ReportingTaxonomyMap | closed |
| [#471](issues/471.md) | Read and write SDMX-ML 2.1 reference metadata: project the Report layer onto 3.0-shaped MetadataSets | closed |
| [#472](issues/472.md) | A bad XML character in SDMX-JSON fails the whole read instead of refusing one artefact (#435) | closed |
| [#473](issues/473.md) | A JSON-read StructureMessage holds its whole source document until lost is read (!592's lazy losses) | closed |
| [#474](issues/474.md) | Readers fill in required values a document omits, and nothing on the message says so | closed |
| [#475](issues/475.md) | SDMX-ML 2.1 ContentConstraint without @type is read as Allowed; the schema default is Actual | closed |
| [#476](issues/476.md) | SDMX-ML 2.1: a <Ref> without @version reads as latest; the XSD defaults it to 1.0 | closed |
| [#477](issues/477.md) | SDMX-ML: a component without @id takes its concept's id; the readers refuse it instead | closed |
| [#478](issues/478.md) | SDMX 3.x un-versioned artefacts: the model holds them as 1.0 | closed |
| [#479](issues/479.md) | A data key set with isIncluded=false is read as included | closed |
| [#480](issues/480.md) | The message header is not read: Sender, Receiver, Source and the rest are dropped silently | closed |
| [#481](issues/481.md) | Validation has no warnings: severity="warning" is declared and never used | closed |
| [#482](issues/482.md) | Facet sequence rules are unchecked: isSequence, interval, start/end values and times (IM §3.6.3.3) | closed |
| [#483](issues/483.md) | Structure map rules are unchecked: mixed source/target kinds, multi-source maps, frequency exclusivity, mapping arity (IM §9–10) | closed |
| [#484](issues/484.md) | A dataflow of an evolving DSD needs a dimension constraint, and nothing checks it (IM §5.3.2.1) | closed |
| [#485](issues/485.md) | An enumerated metadata attribute must use a Codelist; a ValueList is accepted (IM §7.3.4.1) | closed |
| [#486](issues/486.md) | Codelist extension precedence: the IM says the latest codelist wins, apply_extensions lets the earliest win | closed |
| [#487](issues/487.md) | Cross-artefact IM rules are unchecked: partial schemes, constraint values, metadata set targets and values | closed |
| [#488](issues/488.md) | SDMX-JSON data messages don't carry the dataflow's annotations | closed |
| [#489](issues/489.md) | SDMX-JSON 1.0: attributeRelationship {"none": {}} refuses the DSD instead of reading a dataflow attachment | closed |
| [#490](issues/490.md) | parse() reads an SDMX error message as an empty StructureMessage | closed |
| [#491](issues/491.md) | A data constraint holds cube regions or key sets, not both (IM §12.3.3.1); nothing checks it | closed |
| [#492](issues/492.md) | Parse, don't validate: make invalid values unrepresentable, and run validation at every boundary | closed |
| [#493](issues/493.md) | Large reads spend 35% (3.13) to 58% (3.14) of their time in the cyclic GC | closed |
| [#494](issues/494.md) | Fold oracles decompose Hangul syllables that DuckDB's strip_accents keeps whole; the sweep never reached them | closed |
| [#495](issues/495.md) | Require the latest runtime dependencies: polars 2.0, pyarrow 26, msgspec 0.22, lxml 6.1.3 | closed |
| [#496](issues/496.md) | SDMX-JSON schema guards never check formats; our output has date and date-time violations | closed |
| [#497](issues/497.md) | An attribute with an empty related_measures writes an empty measure relationship that 3.x and SDMX-JSON reject | closed |
| [#498](issues/498.md) | test_mutmut_forksafe leaks a deadlocked process on every run, and its 'clean' case flakes | closed |
| [#499](issues/499.md) | Mutation baselines for _expr.py, _joins.py and _common.py, and a full-scope wall-clock | closed |
| [#500](issues/500.md) | bench-compete reports sdmxlib's unchecked writes as INVALID without saying the default would refuse them | closed |
| [#501](issues/501.md) | A node naming a level its hierarchy doesn't define refuses the whole hierarchy: IMF's 99 are unreadable | closed |
| [#502](issues/502.md) | Sweep census lists explicit schema defaults and 2.1's alias form as read losses | closed |
| [#503](issues/503.md) | Writers spell out schema defaults: omit an attribute at its format's default, except identity | closed |
| [#504](issues/504.md) | Mutation 'segfault' verdicts hide survivors: _compile.py's 52 are a mix of kills, survivors and equivalents | closed |
| [#505](issues/505.md) | LocalRegistry(conn) silently ignores a DuckDB connection and opens a stray file | closed |
| [#506](issues/506.md) | A TimeDimension without a concept is accepted, then written XSD-invalid | closed |
| [#507](issues/507.md) | to_json writes a DSD its own reader refuses (time dimension with no representation) | closed |
| [#508](issues/508.md) | add accepts artefacts every writer refuses (attachment=None, enumeration-format sentinel) | closed |
| [#509](issues/509.md) | write_sdmx_json_data defaults lang to "en" | closed |
| [#510](issues/510.md) | A predicate over another relation is accepted when both read the same table, and answers silently | closed |
| [#511](issues/511.md) | Query misuse reaches DuckDB as a raw error instead of a refusal naming the fix | closed |
| [#512](issues/512.md) | Exceptions the docs name are not importable from sl | closed |
| [#513](issues/513.md) | AncestorMap.ancestors() returns a private type | closed |
| [#514](issues/514.md) | Path-keyed trees: broader(id) returns None where depth_of(id) raises | closed |
| [#515](issues/515.md) | REST observations: an unpushable filter fetches the whole flow; the design says refuse | closed |
| [#516](issues/516.md) | reg.data()/DataQuery are still public despite the no-eager-query decision | closed |
| [#517](issues/517.md) | Label column naming differs between Dataset and the query/polars APIs | closed |
| [#518](issues/518.md) | REST: a measure the DSD leaves untyped is fetched as text, so it can't be compared as a number | closed |
| [#519](issues/519.md) | write_observations fails on duckdb 2.0: a view over a Python object cannot be stored in a database file | closed |

## Merge requests

| | title | state |
| --- | --- | --- |
| [!1](merge_requests/1.md) | feat(formats): Phase 3 — SDMX-ML 2.1 reader | merged |
| [!2](merge_requests/2.md) | feat(api): Phase 4 — Session, Query, Registry, and typed URN generics | merged |
| [!3](merge_requests/3.md) | feat(polars): Phase 5 — Polars bridge | merged |
| [!4](merge_requests/4.md) | fix: apply all 12 code review fixes from v0.2.2 review | merged |
| [!5](merge_requests/5.md) | feat(formats): SDMX-ML 2.1 writer (Phase 6a) | merged |
| [!6](merge_requests/6.md) | feat(formats): SDMX-ML 3.0 reader (Phase 6b) | merged |
| [!7](merge_requests/7.md) | feat(formats): SDMX-ML 3.0 writer (Phase 6c) | merged |
| [!8](merge_requests/8.md) | feat(formats): SDMX-JSON 2.0 structure reader | merged |
| [!9](merge_requests/9.md) | feat(formats): add SDMX-JSON 2.0 writer and integration tests | merged |
| [!10](merge_requests/10.md) | feat(model): add Ref.to() classmethod | merged |
| [!11](merge_requests/11.md) | feat(model): add ConceptScheme.ref_to(concept_id) | merged |
| [!12](merge_requests/12.md) | feat: __post_init__ coercion for plain-type construction (#14) | merged |
| [!13](merge_requests/13.md) | feat(model): DataStructure modification helpers (#15) | merged |
| [!14](merge_requests/14.md) | feat(model): from_dict/to_dict converters (#16) | merged |
| [!15](merge_requests/15.md) | feat(model): validate and validate_references (#17) | merged |
| [!16](merge_requests/16.md) | feat: public API re-exports (#18) | merged |
| [!17](merge_requests/17.md) | Audit and fix building_tutorial.md; add docs test suite | merged |
| [!18](merge_requests/18.md) | Add pytest-cov coverage reporting and GitLab badge | merged |
| [!19](merge_requests/19.md) | Restructure docs: thematic cookbook, tutorials, clean design docs | merged |
| [!20](merge_requests/20.md) | feat: parse and re-emit AgencyScheme / OrganisationSchemes | merged |
| [!21](merge_requests/21.md) | feat: ContentConstraint + CubeRegion round-trip fidelity | merged |
| [!22](merge_requests/22.md) | feat: StructureSet + CodelistMap round-trip fidelity | merged |
| [!23](merge_requests/23.md) | feat: complete fixtures + round-trip acceptance tests (issue #23) | merged |
| [!24](merge_requests/24.md) | feat: CoreRepresentation on Concept with automatic inheritance (issue #24) | merged |
| [!25](merge_requests/25.md) | feat: accept bare SdmxUrn on codelist/concept/structure fields | merged |
| [!26](merge_requests/26.md) | Remove Enumeration and codelist field; add AnyRef type alias | merged |
| [!27](merge_requests/27.md) | Replace agency_id: str with maintainer: Agency on MaintainableArtefact | merged |
| [!28](merge_requests/28.md) | feat: Registry as SDMX IM store | merged |
| [!29](merge_requests/29.md) | feat: add sl.read() and local file loading API | merged |
| [!30](merge_requests/30.md) | feat: migrate domain model from dataclasses to attrs | merged |
| [!31](merge_requests/31.md) | feat: REST query DSL with browse/fetch/resolve modes | merged |
| [!32](merge_requests/32.md) | feat(model): add Level to Hierarchy (#40) and rename ContentConstraint → DataConstraint (#39) | merged |
| [!33](merge_requests/33.md) | feat(sql): lift SqlRelation + Predicate + CompileContext to public API | merged |
| [!34](merge_requests/34.md) | feat(sql): add CompileContext.next_alias() for predicate compilers | merged |
| [!35](merge_requests/35.md) | feat(data-plane): DuckLake-backed observations, Azure ADLSv2, ATTACH options fix | merged |
| [!36](merge_requests/36.md) | Draft: Draft: feat(data-store): force curl transport when azure is needed | closed |
| [!37](merge_requests/37.md) | test: concurrent-read corruption reproducers (#76) | merged |
| [!38](merge_requests/38.md) | bench: pin v0.27.2 read-path baseline (#77) | merged |
| [!39](merge_requests/39.md) | fix: per-call cursors + lock-free FIFO cache for concurrent reads (#78) | merged |
| [!40](merge_requests/40.md) | release: docs(concurrency) + v0.28.0 (#79) | merged |
| [!41](merge_requests/41.md) | perf(local): thread-local cursors to amortise per-call lifecycle (#80) | merged |
| [!42](merge_requests/42.md) | feat(data-store): force curl transport when azure is needed | merged |
| [!43](merge_requests/43.md) | feat(model): expose concept_role on Dimension/Measure/Attribute (#82) | merged |
| [!44](merge_requests/44.md) | docs(claude.md): treat polars and duckdb as baseline runtime deps | merged |
| [!45](merge_requests/45.md) | chore(cleanup): phase 1 — small contract + dead-code fixes (#83) | merged |
| [!46](merge_requests/46.md) | chore(cleanup): phase 2 — RST docstring sweep + public-API hygiene (#84) | merged |
| [!47](merge_requests/47.md) | chore(cleanup): phase 3 — narrowing helpers + shared test conftest (#85) | merged |
| [!48](merge_requests/48.md) | chore(cleanup): phase 4 — extract formats/sdmx_ml/_common.py (#86) | merged |
| [!49](merge_requests/49.md) | chore(cleanup): phase 5 — dispatch consolidation + local/ rename (#87) | merged |
| [!50](merge_requests/50.md) | chore(cleanup): followup phase 1 — high-level writer dedup + csv cast (#88) | merged |
| [!51](merge_requests/51.md) | chore(cleanup): followup phase 2 — SQL style sweep (#89) | closed |
| [!52](merge_requests/52.md) | docs(claude.md): SQL quote rule matches the community convention | merged |
| [!53](merge_requests/53.md) | chore(cleanup): followup phase 4 — document the lint/typecheck disables (#91) | merged |
| [!54](merge_requests/54.md) | chore(cleanup): followup phase 3 — more reader + writer dedup (#90) | merged |
| [!55](merge_requests/55.md) | test(xsd): vendor official SDMX-ML 2.1 + 3.0 XSDs (MR1 of XSD validation) | merged |
| [!56](merge_requests/56.md) | test(xsd): wire SDMX-ML 3.0 writer round-trips through XSD validation (MR2) | merged |
| [!57](merge_requests/57.md) | test(xsd): wire SDMX-ML 2.1 writer round-trips through XSD validation (MR3) | merged |
| [!58](merge_requests/58.md) | docs: document SDMX-ML XSD validation in CLAUDE.md (MR4) | merged |
| [!59](merge_requests/59.md) | test(xsd): rewrite ml30/complete.xml to 3.0-conformant shape; un-xfail | merged |
| [!60](merge_requests/60.md) | refactor(model): adopt 3.0-native mapping types; close #93 | merged |
| [!61](merge_requests/61.md) | test(xsd): exercise SDMX 3.0 MSD + Metadataflow; close #94 | merged |
| [!62](merge_requests/62.md) | test(xsd): exercise SDMX 2.1 HierarchicalCodelist; close #95 | merged |
| [!63](merge_requests/63.md) | test(xsd): vendor SDMX-ML v3.1.0 schemas + forward-compat audit; close #102 | merged |
| [!64](merge_requests/64.md) | model: enum-ify usage + constraint_type; add PA.is_final (close #111, #112) | merged |
| [!65](merge_requests/65.md) | model: 3.1-shaped Hierarchy + Level + HierarchicalCode (close #105, #106, #110) | merged |
| [!66](merge_requests/66.md) | model: add Dataflow.dimension_constraint (3.1 evolving-DSD support); close #113 | merged |
| [!67](merge_requests/67.md) | model: add is_final to Dataflow, CategoryScheme, Categorisation, ConceptScheme; close #114 | merged |
| [!68](merge_requests/68.md) | model: add Codelist.extensions (3.0 / 3.1 composition); close #109 | merged |
| [!69](merge_requests/69.md) | model: discriminated-union ValueMap variants + per-mapping validity; close #107 | merged |
| [!70](merge_requests/70.md) | model: add HierarchyAssociation; fix 3.0 Hierarchy XSD bugs (close #108) | merged |
| [!71](merge_requests/71.md) | test(xsd): round-trip Annotations across all maintainable artefacts; close #96 | merged |
| [!72](merge_requests/72.md) | model+test(xsd): Attribute Group / Dataset attachment levels round-trip; close #98 | merged |
| [!73](merge_requests/73.md) | model+test(xsd): Contact multi-field + X400 + namespace fixes; close #99 | merged |
| [!74](merge_requests/74.md) | test(xsd): exercise non-en/fr language tags incl. region-coded BCP-47; close #100 | merged |
| [!75](merge_requests/75.md) | test(xsd): exercise ReferencePeriod + ReleaseCalendar; fix XSD order bugs; close #101 | merged |
| [!76](merge_requests/76.md) | test(xsd): exercise role="Actual" + CubeRegion include=false; partial close #97 | merged |
| [!77](merge_requests/77.md) | feature(format): add SDMX-ML 3.1 reader + writer; close #103, #104 | merged |
| [!78](merge_requests/78.md) | model: add 2.1-only AttachmentConstraint artefact + serializers | merged |
| [!79](merge_requests/79.md) | chore(bench): add pytest-benchmark + pytest-memray harness (Phase 1) | merged |
| [!80](merge_requests/80.md) | chore(bench): Phase 2 — storage / LazyCodelist / local Registry | merged |
| [!81](merge_requests/81.md) | chore(bench): Phase 3 — navigation tax (elegance vs hand-built dict) | merged |
| [!82](merge_requests/82.md) | perf(model): string-first dispatch in ItemList.__getitem__ | merged |
| [!83](merge_requests/83.md) | perf(model): slot-cache resolved codelist on Component | merged |
| [!84](merge_requests/84.md) | chore(bench): competitive parse/serialize vs sdmx1 + pysdmx | merged |
| [!85](merge_requests/85.md) | test(codelist): cover SKOS hierarchy methods | merged |
| [!86](merge_requests/86.md) | test(storage): round-trip 7 artefact types through DuckDB | merged |
| [!87](merge_requests/87.md) | test(hierarchy): cover LazyHierarchy + query execution DSL | merged |
| [!88](merge_requests/88.md) | test(json-writer): cover untested artefact write paths | merged |
| [!89](merge_requests/89.md) | test(sdmx_ml31): port 7 fixtures from 3.0 with divergences applied | merged |
| [!90](merge_requests/90.md) | feat(dsl): node/code.annotations[type,lang] targets text payload | merged |
| [!91](merge_requests/91.md) | perf(istring): intern language codes | merged |
| [!92](merge_requests/92.md) | perf(urn): cache __str__ on the frozen SdmxUrn instance | merged |
| [!93](merge_requests/93.md) | test(json-schema): vendor SDMX-JSON 2.1 schemas + validation helper | merged |
| [!94](merge_requests/94.md) | fix(sdmx_json): conformance pass for simple artefacts | merged |
| [!95](merge_requests/95.md) | fix(sdmx_json): conformance pass for DSD / DataConstraint / maps | merged |
| [!96](merge_requests/96.md) | bench(compete): add SDMX-JSON serialize comparison | merged |
| [!97](merge_requests/97.md) | perf(sdmx_json): swap stdlib json → orjson; ~2.4× serialize speedup | merged |
| [!98](merge_requests/98.md) | fix(sdmx_json): emit codelistExtensions + concept coreRepresentation + Hierarchy levels; broaden fixture coverage | merged |
| [!99](merge_requests/99.md) | perf(sdmx_ml): precompute clark tags + empty singletons in hot read paths | merged |
| [!100](merge_requests/100.md) | feat(model): RepresentationMap source/target accept primitive dataType (#131) | merged |
| [!101](merge_requests/101.md) | feat(model): ConstraintAttachment.data_provider attachment kind (#130) | merged |
| [!102](merge_requests/102.md) | feat(binding): require tz-aware materialized_at / bound_at (#92) | merged |
| [!103](merge_requests/103.md) | feat(hierarchy): descendants_of + default_hierarchy + JSON HA round-trip (#135) | merged |
| [!104](merge_requests/104.md) | feat(code_query): satisfy sl.sql.Predicate Protocol for foreign SQL embedding | merged |
| [!105](merge_requests/105.md) | perf(#139): faster Registry.ingest — executemany refs + tx-wrap add_all | merged |
| [!106](merge_requests/106.md) | perf(local): per-artefact instrumentation for ingest diagnosis (#142) | merged |
| [!107](merge_requests/107.md) | perf(storage): _put_refs Arrow bulk insert — the real #140/#146 bottleneck | merged |
| [!108](merge_requests/108.md) | feat(validation): validate_references raises ReferenceValidationError | merged |
| [!109](merge_requests/109.md) | feat(binding): split validate_binding into check + validate | merged |
| [!110](merge_requests/110.md) | docs: refresh examples for raising validate / split binding-drift APIs | merged |
| [!111](merge_requests/111.md) | feat(sdmx_json): streaming SDMX-JSON 1.0 / 2.0 data writers + readers | merged |
| [!112](merge_requests/112.md) | fix(sdmx_json): emit TIME_PERIOD start/end + trim values[] to observed codes | merged |
| [!113](merge_requests/113.md) | fix(sdmx_json): emit dataflow display name + TIME_PERIOD concept name | merged |
| [!114](merge_requests/114.md) | feat(sdmx_json): canonical per-Code/Concept value serializer | merged |
| [!115](merge_requests/115.md) | feat(builders): availability ContentConstraint builder + SDMX-JSON 1.0 writer | merged |
| [!116](merge_requests/116.md) | feat(builders): per-component availability slices via build_component_availability | merged |
| [!117](merge_requests/117.md) | fix(sdmx_json): FREQ-driven multi-year TIME_PERIOD start/end bounds | merged |
| [!118](merge_requests/118.md) | perf(sdmx_json): lazy per-code label lookup in ComponentTable | merged |
| [!119](merge_requests/119.md) | fix(deps): duckdb>=1.5.3 for DuckLake format 1.0 + caching strategy note | merged |
| [!120](merge_requests/120.md) | perf(storage): single-flight code_map cache to fix label thundering herd (#155) | merged |
| [!121](merge_requests/121.md) | fix(data_store): clear error on DuckLake catalog-format mismatch (#156) | merged |
| [!122](merge_requests/122.md) | feat(query): accent-insensitive name/title/text contains matching | merged |
| [!123](merge_requests/123.md) | feat(query): punctuation-insensitive name/title/text contains matching | merged |
| [!124](merge_requests/124.md) | feat(registry): reverse metadata lookup + typed section projection (refmetadata Phase A) | merged |
| [!125](merge_requests/125.md) | fix(serializers): close silent round-trip field drops (annotations, isFinal, validity, MeasureDimension) | merged |
| [!126](merge_requests/126.md) | test(serializers): generic round-trip property guard with xfail-tracked Table B gaps | merged |
| [!127](merge_requests/127.md) | fix(storage): persist facet detail, attribute relationships, contact fax/x400 (#159 Table B) | merged |
| [!128](merge_requests/128.md) | fix(sdmx-ml21): concept_role on components + <str:Contact> on orgs (#159 Table B) | merged |
| [!129](merge_requests/129.md) | fix(sdmx-json): concept roles, hierarchy formal-levels/coding-format/validity, per-mapping validity (#159 Table B) | merged |
| [!130](merge_requests/130.md) | fix(sdmx-ml30): Hierarchy Level codingFormat round-trip; mark remaining guard gaps by-design (#159) | merged |
| [!131](merge_requests/131.md) | perf(sdmx-json): fast-path istring emit on the serialize hot path | merged |
| [!132](merge_requests/132.md) | feat(data-store): public SchemaInspector/QueryPlan custom-SQL seam (#163) | merged |
| [!133](merge_requests/133.md) | refactor(data-store): tighten SchemaInspector after #163 quality audit | merged |
| [!134](merge_requests/134.md) | refactor(typing): T1 model-core checked seams (#165 #166 #167 #168) | merged |
| [!135](merge_requests/135.md) | refactor(typing): object-by-default on clean consumer sites | merged |
| [!136](merge_requests/136.md) | fix(hierarchy-query): reject bare Code in node code-field comparison (#174) | merged |
| [!137](merge_requests/137.md) | refactor(typing): narrow artefact_urn to HasUrn — completes #165 | merged |
| [!138](merge_requests/138.md) | refactor(imports): sweep bare # noqa: PLC0415 (hoist cargo-cult, document intentional) | merged |
| [!139](merge_requests/139.md) | refactor: break the three documented import cycles via module layout | merged |
| [!140](merge_requests/140.md) | refactor(typing): precise small Any narrowings (#172) | merged |
| [!141](merge_requests/141.md) | refactor(typing): parameterize generic reader helpers (#171) | merged |
| [!142](merge_requests/142.md) | refactor(typing): #170 clean writer-param typing + Supports* protocol convention | merged |
| [!143](merge_requests/143.md) | docs(design): JSON & Parsing — the typedness spectrum + parse-vs-tree rubric | merged |
| [!144](merge_requests/144.md) | refactor(typing): narrow reader resolution index to dict[str, object] (#169) | merged |
| [!145](merge_requests/145.md) | refactor(formats): witness-type reader ref resolution (#173) | merged |
| [!146](merge_requests/146.md) | refactor(duckdb): collapse scalar() helper to what it is — count() | merged |
| [!147](merge_requests/147.md) | chore(deps): swap lxml-stubs → types-lxml (dev) | merged |
| [!148](merge_requests/148.md) | fix(formats): make the reader ref-resolution witness load-bearing (#177) | merged |
| [!149](merge_requests/149.md) | fix(typing): annotate 6 empty containers that inferred as Any | merged |
| [!150](merge_requests/150.md) | fix(model): 2 real findings pyrefly caught + enable clean strict basedpyright rules | merged |
| [!151](merge_requests/151.md) | perf(serialize): decouple validation from serialize (validate=False default) | merged |
| [!152](merge_requests/152.md) | perf(sdmx-json): dedup istring emit + skip the defensive map copy | merged |
| [!153](merge_requests/153.md) | docs(perf): record why JSON serialize is dict+orjson, not msgspec/cattrs | merged |
| [!154](merge_requests/154.md) | feat(sdmx-json): msgspec migration foundation — shared DTO Structs + conventions (#178) | merged |
| [!155](merge_requests/155.md) | feat(sdmx-json): structure write via Struct DTOs — item schemes (#180 part 1) | merged |
| [!156](merge_requests/156.md) | feat(sdmx-json): structure write via Struct DTOs — DSD + dataflow (#180 part 2) | merged |
| [!157](merge_requests/157.md) | feat(sdmx-json): structure write via Struct DTOs — orgs, categorisation, PA (#180 part 3) | merged |
| [!158](merge_requests/158.md) | feat(sdmx-json): structure write via Struct DTOs — DataConstraint (#180 part 4) | merged |
| [!159](merge_requests/159.md) | feat(sdmx-json): structure write via Struct DTOs — hierarchy (#180 part 5) | merged |
| [!160](merge_requests/160.md) | feat(sdmx-json): structure write via Struct DTOs — MSD + metadataflow (#180 part 6) | merged |
| [!161](merge_requests/161.md) | feat(sdmx-json): structure write via Struct DTOs — structure maps; closes #180 | merged |
| [!162](merge_requests/162.md) | feat(sdmx-json): structure read via typed decode + ingest — codelists (#181 part 1) | merged |
| [!163](merge_requests/163.md) | feat(sdmx-json): structure read via typed decode — concept + category schemes (#181 part 2) | merged |
| [!164](merge_requests/164.md) | refactor(sdmx-json): shared resolve_urn instead of the ingest callback (#181) | merged |
| [!165](merge_requests/165.md) | feat(sdmx-json): structure read via typed decode — DSD + dataflow (#181 part 3) | merged |
| [!166](merge_requests/166.md) | refactor(sdmx-json): snake_case DTO fields + rename="camel" (drop 71 N815 noqas) | merged |
| [!167](merge_requests/167.md) | feat(sdmx-json): structure read via typed decode — orgs, categorisation, PA (#181 part 4) | merged |
| [!168](merge_requests/168.md) | feat(sdmx-json): structure read via typed decode — DataConstraint (#181 part 5) | merged |
| [!169](merge_requests/169.md) | feat(sdmx-json): structure read via typed decode — Hierarchy (#181 part 6) | merged |
| [!170](merge_requests/170.md) | feat(sdmx-json): structure read via typed decode — MSD + Metadataflow (#181 part 7) | merged |
| [!171](merge_requests/171.md) | feat(sdmx-json): structure read via typed decode — structure maps (#181 part 8, closes #181) | merged |
| [!172](merge_requests/172.md) | feat(sdmx-json): metadata message read+write via typed msgspec DTOs (#182) | merged |
| [!173](merge_requests/173.md) | perf(sdmx-json): data-message writers/readers off orjson onto msgspec (#183) | merged |
| [!174](merge_requests/174.md) | feat(sdmx-json-1.0): ContentConstraint writer via typed msgspec DTOs (#184) | merged |
| [!175](merge_requests/175.md) | chore(deps): remove orjson — SDMX-JSON is fully msgspec (#185, closes milestone #8) | merged |
| [!176](merge_requests/176.md) | perf(sdmx-json): gc=False + emit_pair on wire DTOs — JSON serialize now beats pysdmx | merged |
| [!177](merge_requests/177.md) | feat(sdmx-json): type the last codelist-extension dict island (#186) | merged |
| [!178](merge_requests/178.md) | feat(metadata): model MetadataProvisionAgreement + constrain MetadataSet.structure + fix 3.0 metadata XSD (#187) | merged |
| [!179](merge_requests/179.md) | feat(metadata): DSD↔MSD reference-metadata association — .Stat annotation shim + native SDMX 3.0 links (#162, #189, #190, #191) | merged |
| [!180](merge_requests/180.md) | chore(deps): widen uv_build pin to >=0.12.0,<0.13.0 | merged |
| [!181](merge_requests/181.md) | feat(metadata): native SDMX 3.0 rel="metadata" link in SDMX-ML 3.0 writer (closes #191) | merged |
| [!182](merge_requests/182.md) | chore(pyrefly): pin + scope pyrefly; make SdmxUrn covariant | merged |
| [!183](merge_requests/183.md) | docs(claude): note the just pyrefly implicit-any guard | merged |
| [!184](merge_requests/184.md) | chore(checkers): ruff 0.16, pyrefly preset=all, pinned versions | merged |
| [!185](merge_requests/185.md) | refactor: [S1] restore real types where a suppression stood in for one | merged |
| [!186](merge_requests/186.md) | refactor(api): [S2] type the httpx pass-through with PEP 692 Unpack | merged |
| [!187](merge_requests/187.md) | fix(api): stop HttpxOptions narrowing params/cookies below httpx | merged |
| [!188](merge_requests/188.md) | refactor(typing): type the 9 `Any` params that already had a concrete type | merged |
| [!189](merge_requests/189.md) | fix(typing): close the leaky `-> Any` returns; guard URN-text ref resolution | merged |
| [!190](merge_requests/190.md) | feat(reader)!: reject class-mismatched references at the read boundary | merged |
| [!191](merge_requests/191.md) | fix(formats): validate emitted XML in the property guard; fix 5 conformance bugs | merged |
| [!192](merge_requests/192.md) | feat(validation)!: require a name on every NameableArtefact | merged |
| [!193](merge_requests/193.md) | refactor(typing): type the duck-typed writer/storage params (#203) | merged |
| [!194](merge_requests/194.md) | refactor(typing): type 7 of #204's "justified" Any sites; document the 1 real one | merged |
| [!195](merge_requests/195.md) | docs(typing): record the measured reason for SdmxUrn's UP046 noqa (#201) | merged |
| [!196](merge_requests/196.md) | docs(storage): document the lazy facade properties; removes 20 D102 noqas | merged |
| [!197](merge_requests/197.md) | chore(typing): migrate the last 22 `type: ignore` to per-checker syntax (#195) | merged |
| [!198](merge_requests/198.md) | chore(lint): annotate alternate-constructor locals; 28 SLF001 noqas removed (#198) | merged |
| [!199](merge_requests/199.md) | docs: adopt a written public/private rule; retire SLF001 (#207) | merged |
| [!200](merge_requests/200.md) | refactor(model): route Ref resolution through a package-internal seam (#208) | merged |
| [!201](merge_requests/201.md) | refactor: name the stages in _serialise_key and _resolve_refs (#199, partial) | merged |
| [!202](merge_requests/202.md) | refactor(formats): drop the legacy helper aliases; call helpers by name (#209) | merged |
| [!203](merge_requests/203.md) | fix(typing): narrow the writer dispatch tables from Any to Callable | merged |
| [!204](merge_requests/204.md) | refactor(formats): WriterHelpers is a class, not a closure factory (#209) | merged |
| [!205](merge_requests/205.md) | refactor(formats): ReaderHelpers is a class; deletes three workaround Protocols (#209) | merged |
| [!206](merge_requests/206.md) | refactor(formats): read with lxml prefixed paths, not tag-building helpers (#209) | merged |
| [!207](merge_requests/207.md) | refactor: retriage complexity suppressions, 5 -> 1 | merged |
| [!208](merge_requests/208.md) | typing: decode the SDMX-JSON envelope into Structs | merged |
| [!209](merge_requests/209.md) | typing: replace known-shape dict[str, Any] with object / TypedDict | merged |
| [!210](merge_requests/210.md) | typing: decode the data-message envelope into Structs | merged |
| [!211](merge_requests/211.md) | typing: bind writer dispatch tables so the pair must agree | merged |
| [!212](merge_requests/212.md) | docs: the JSON parse boundary is settled, not pending | merged |
| [!213](merge_requests/213.md) | test: share the round-trip harness and fixture loaders | merged |
| [!214](merge_requests/214.md) | test: share the DSD builders, keeping the code choices visible | merged |
| [!215](merge_requests/215.md) | typing: one way to build a Ref from an artefact — Ref.to() | merged |
| [!216](merge_requests/216.md) | test: share the binding, URN, cursor and MSD builders | merged |
| [!217](merge_requests/217.md) | test: share the observation, metadataflow and catalog builders | merged |
| [!218](merge_requests/218.md) | test: repair the integration/live split and mark the slow tests | merged |
| [!219](merge_requests/219.md) | test: mutmut pilot — 807 mutants, 153 survive | merged |
| [!220](merge_requests/220.md) | test: Hypothesis foundation — strategies, profiles, first properties | merged |
| [!221](merge_requests/221.md) | fix: route messages on the envelope, not on what the content says | merged |
| [!222](merge_requests/222.md) | fix: reject XML-incompatible text at validation and the JSON read boundary | merged |
| [!223](merge_requests/223.md) | test: generative round-trip properties across every serializer | merged |
| [!224](merge_requests/224.md) | fix: escape LIKE metacharacters in query filters; add DSL differential tests | merged |
| [!225](merge_requests/225.md) | fix: eager hierarchy read dropped has_formal_levels; semantic test audit | merged |
| [!226](merge_requests/226.md) | test: schema-oracle coverage for DataStructure and Dataflow | merged |
| [!227](merge_requests/227.md) | fix: make unserialisable DataStructures fail loudly instead of silently | merged |
| [!228](merge_requests/228.md) | fix: validate() applies the id type the schema actually uses | merged |
| [!229](merge_requests/229.md) | fix: organisation scheme ids are fixed by the schema; add org + hierarchy coverage | merged |
| [!230](merge_requests/230.md) | test: check the model against constraints read out of the XSDs | merged |
| [!231](merge_requests/231.md) | test(matrix): cover five more classes; 2.1 no longer drops artefacts silently | merged |
| [!232](merge_requests/232.md) | test(matrix): complete the spec-coverage matrix; six more conformance fixes | merged |
| [!233](merge_requests/233.md) | test(matrix): cover the metadata envelope; four conformance fixes | merged |
| [!234](merge_requests/234.md) | test(conformance): gate every artefact x format on valid-or-refused | merged |
| [!235](merge_requests/235.md) | test(xsd): extract required children and required choices from the schemas | merged |
| [!236](merge_requests/236.md) | fix(conformance): close all 24 pairs that serialised to invalid documents | merged |
| [!237](merge_requests/237.md) | test(xsd): extract required and prohibited attributes; three findings | merged |
| [!238](merge_requests/238.md) | feat(sdmx-ml 2.1): write and read metadataflows | merged |
| [!239](merge_requests/239.md) | feat: lossless SDMX-ML round-trips — model the 2.1 containers, fix four losses | merged |
| [!240](merge_requests/240.md) | fix(sdmx-ml 2.1): read MetadataStructures instead of discarding them | merged |
| [!241](merge_requests/241.md) | fix: carry enumeration_format everywhere, and DataKeySet on DataConstraint | merged |
| [!242](merge_requests/242.md) | feat: convert AttachmentConstraint to DataConstraint instead of refusing | merged |
| [!243](merge_requests/243.md) | test(conformance): assert every SDMX-ML version hop; refuse two silent 3.1 losses | merged |
| [!244](merge_requests/244.md) | feat(model): model the 2.1 MSD reporting layer | merged |
| [!245](merge_requests/245.md) | feat(sdmx-ml 2.1): write StructureMaps, refusing only what cannot be derived | merged |
| [!246](merge_requests/246.md) | feat(model): carry artefact @uri through every format | merged |
| [!247](merge_requests/247.md) | test(fuzz): mutate documents until only one exception escapes parse | merged |
| [!248](merge_requests/248.md) | test(fuzz): replace a guessed coverage figure with the measured one | merged |
| [!249](merge_requests/249.md) | test(dsl): oracle-backed properties for both query DSLs (%13 phase 1) | merged |
| [!250](merge_requests/250.md) | docs(claude): record the differential-oracle testing discipline | merged |
| [!251](merge_requests/251.md) | fix(dsl): compile annotation predicates as EXISTS, not JOIN | merged |
| [!252](merge_requests/252.md) | fix(rest): an empty OR-set matches nothing, in both directions | merged |
| [!253](merge_requests/253.md) | feat(dsl)!: label folding is opt-in, and both DSLs have it | merged |
| [!254](merge_requests/254.md) | feat(dsl): add is_in — set membership, eager or lazy | merged |
| [!255](merge_requests/255.md) | feat(dsl): ordered comparisons on depth, and a docstring that runs | merged |
| [!256](merge_requests/256.md) | refactor(dsl): share the half of the two compilers that carries no types | merged |
| [!257](merge_requests/257.md) | test(bench): query-DSL benchmarks and a SQL-equivalence corpus | merged |
| [!258](merge_requests/258.md) | refactor(dsl)!: one generic predicate AST for both query DSLs | merged |
| [!259](merge_requests/259.md) | feat(dsl): ancestor_at — hierarchy mapping as a relation, with explicit self-inclusion | merged |
| [!260](merge_requests/260.md) | feat(dsl): project and aggregate structural queries into frames | merged |
| [!261](merge_requests/261.md) | feat(dsl): query the artefact spine — catalogue discovery | merged |
| [!262](merge_requests/262.md) | feat(dsl): query reported reference metadata | merged |
| [!263](merge_requests/263.md) | fix(typing): close the Any enforcement gap the docs assumed was closed | merged |
| [!264](merge_requests/264.md) | refactor(typing): replace dict[str, Any] where the shape is known | merged |
| [!265](merge_requests/265.md) | refactor(typing)!: delete the AnyRef alias, and teach the gate to follow aliases | merged |
| [!266](merge_requests/266.md) | refactor(typing): make Ref covariant — 55 of 56 Ref[Any] were variance, not gradual typing | merged |
| [!267](merge_requests/267.md) | refactor(typing): SdmxUrn[Any] — 18 were pure consumers, 5 recover the parameter | merged |
| [!268](merge_requests/268.md) | refactor(typing): list[Any] — type what can be, document what cannot | merged |
| [!269](merge_requests/269.md) | test(dsl): misuse tests — assert the wrong call fails, and says how to fix it | merged |
| [!270](merge_requests/270.md) | test(dsl): hostile filter values across every string position | merged |
| [!271](merge_requests/271.md) | test(dsl): structural AST invariants over every emitted SQL | merged |
| [!272](merge_requests/272.md) | test(dsl): differential properties for the data plane, against a Polars oracle | merged |
| [!273](merge_requests/273.md) | test(model): triage collections.py's surviving mutants, and unbreak the gate | merged |
| [!274](merge_requests/274.md) | feat(dsl)!: limit composes as a cap, plus the three missing algebraic laws | merged |
| [!275](merge_requests/275.md) | test(api): generative coverage for item(), Query[T] and the SQL seam | merged |
| [!276](merge_requests/276.md) | feat(dsl): add &, \| and ~ combinators to the query DSLs (#262) | merged |
| [!277](merge_requests/277.md) | refactor(dsl): make the query compilers total via ColumnRef (#259) | merged |
| [!278](merge_requests/278.md) | feat(dsl): add annotations[type].exists(), and fix predicate nesting (#292) | merged |
| [!279](merge_requests/279.md) | feat(dsl): add offset(n), with a window that narrows (#264) | merged |
| [!280](merge_requests/280.md) | refactor(dsl): state the refinement principle and enforce it (#293, #296) | merged |
| [!281](merge_requests/281.md) | refactor(dsl): sort_by replaces, takes several keys, and clears (#294) | merged |
| [!282](merge_requests/282.md) | refactor(dsl): rename sort_by to order_by on the SQL query DSLs (#295) | merged |
| [!283](merge_requests/283.md) | fix(dsl): reject a literal the column cannot hold (#272) | merged |
| [!284](merge_requests/284.md) | docs(design): record the IR decision for the query DSLs (#273) | merged |
| [!285](merge_requests/285.md) | docs(sql): say why each CompileContext field is dormant, and pin the live one (#268) | merged |
| [!286](merge_requests/286.md) | feat(dsl): canonical_key() — a stable key for a query'\''s meaning (#270) | merged |
| [!287](merge_requests/287.md) | refactor(api): export the types the query DSLs name in their signatures (#265) | merged |
| [!288](merge_requests/288.md) | feat(session): one execution handle per thread, with somewhere to put its state (#300) | merged |
| [!289](merge_requests/289.md) | fix(session): keep cursor_for off the public surface, and say what open_sessions counts | merged |
| [!290](merge_requests/290.md) | feat(session): value_set — register a large is_in set instead of binding it (#271) | merged |
| [!291](merge_requests/291.md) | fix(query): case is a rung on the fold ladder, not the floor of it (#222) | merged |
| [!292](merge_requests/292.md) | test(artefact-query): a single-case vocabulary made every case assertion vacuous | merged |
| [!293](merge_requests/293.md) | test: `just mutate` aborted at collection and had done so since #265 (#252) | merged |
| [!294](merge_requests/294.md) | test: the 356 mutation "timeouts" were macOS fork-safety segfaults (#252) | merged |
| [!295](merge_requests/295.md) | build: path-scoped mutation recipes, and the two traps that cost #252 a day | merged |
| [!296](merge_requests/296.md) | build: mutate one area at a time — the fix for #303's timeouts, not just a speedup | merged |
| [!297](merge_requests/297.md) | fix(validation): refuse a versionless reference on 3.0/3.1/json (#233) | merged |
| [!298](merge_requests/298.md) | fix(validation): audit every xs:unique, and close the one gap it found (#231) | merged |
| [!299](merge_requests/299.md) | fix(metadata): extend conformance coverage to MetadataMessage, and fix what it found (#232) | merged |
| [!300](merge_requests/300.md) | fix(validation): a MeasureDimension is 2.1-only and must not vanish quietly (#305) | merged |
| [!301](merge_requests/301.md) | bench(availability): the one-scan claim is false — N per-component scans win (#277) | merged |
| [!302](merge_requests/302.md) | One query language over the SDMX information model | merged |
| [!303](merge_requests/303.md) | The component representation surface, and the layout that made it drift | merged |
| [!304](merge_requests/304.md) | Mutation evidence: the SQL corpus rebuilt, and _plan.py at zero survivors | merged |
| [!305](merge_requests/305.md) | Concept.parent — the column existed, the feature never did | merged |
| [!306](merge_requests/306.md) | Contacts belong to an organisation, not an agency | merged |
| [!307](merge_requests/307.md) | perf(storage): skip the cascade for an artefact that is not stored | merged |
| [!308](merge_requests/308.md) | Pushdown: Plan, Capability, the refusal rule — and the evidence for a REST backend | merged |
| [!309](merge_requests/309.md) | Planner finds the rewrite; scannable roots and a per-root bound | merged |
| [!310](merge_requests/310.md) | docs(design): the executor is a fetch planner, and the fetch must close | merged |
| [!311](merge_requests/311.md) | feat(query): compute what a fetch must close over (§13) | merged |
| [!312](merge_requests/312.md) | feat(query): a Push as an SDMX REST request | merged |
| [!313](merge_requests/313.md) | feat(query): fetch, materialise, ask locally — the REST executor and its gate | merged |
| [!314](merge_requests/314.md) | feat(query)!: the path language over REST, and reg.query(T) retired | merged |
| [!315](merge_requests/315.md) | fix(query): order versions numerically — 1.9 before 1.10 | merged |
| [!316](merge_requests/316.md) | fix(model): classify version compliance, and refuse it at the writer | merged |
| [!317](merge_requests/317.md) | feat(formats): ask what a format cannot carry, and declare a target that refuses | merged |
| [!318](merge_requests/318.md) | feat(formats): the store is a target too — item uri is lost, and now says so | merged |
| [!319](merge_requests/319.md) | docs(cookbook): the six undocumented terminals — and a bug writing them found | merged |
| [!320](merge_requests/320.md) | feat(query): derive detail= from what a projection reads | merged |
| [!321](merge_requests/321.md) | fix(storage): refuse a bare item where a Ref is declared | merged |
| [!322](merge_requests/322.md) | test(storage): a derived round-trip guard, and the four defects it found | merged |
| [!323](merge_requests/323.md) | fix(storage): preserve the instant in a zoneless TIMESTAMP column | merged |
| [!324](merge_requests/324.md) | fix(storage): materialize() answers what the proxy already knew (#322) | merged |
| [!325](merge_requests/325.md) | fix(storage): read the spine from one place, not nineteen (#320) | merged |
| [!326](merge_requests/326.md) | fix(storage): persist Dataflow.dimension_constraint (#323) | merged |
| [!327](merge_requests/327.md) | fix(storage): persist codelist extensions, constraint schedule, level facets (#160) | merged |
| [!328](merge_requests/328.md) | fix(tools): run mutmut's parent-side test phases in a forked child (#314) | merged |
| [!329](merge_requests/329.md) | test(mutation): answer #218's properties-vs-examples question, and widen the scope (#304) | merged |
| [!330](merge_requests/330.md) | fix(query): a windowed count counts values, not rows (#304 triage) | merged |
| [!331](merge_requests/331.md) | fix(data): refuse a second statement in a composed data-plane query (#297) | merged |
| [!332](merge_requests/332.md) | docs(design): the data plane needs the language it already has (#297) | merged |
| [!333](merge_requests/333.md) | docs(design): a second consumer, and two shapes re-rooting cannot reach (#297) | merged |
| [!334](merge_requests/334.md) | feat(query): unique() — which distinct values, not how many | merged |
| [!335](merge_requests/335.md) | refactor(query): the root's schema is a property of the plan (#297 step 1) | merged |
| [!336](merge_requests/336.md) | refactor(query): a join is ON an expression or USING columns (#297 step 2) | merged |
| [!337](merge_requests/337.md) | feat(query): sum, mean, min and max (#327, #297 step 3) | merged |
| [!338](merge_requests/338.md) | refactor(query): read a root's identity from the instance, not the class | merged |
| [!339](merge_requests/339.md) | docs(design): the DSD already types the columns, and three questions settle (#297) | merged |
| [!340](merge_requests/340.md) | feat(query): a dataflow's observations as a query root (#297 step 4) | merged |
| [!341](merge_requests/341.md) | feat(query): reach observations by dataflow URN, and name columns off the query | merged |
| [!342](merge_requests/342.md) | fix(query): a column whose type nothing states, and two window bugs (#328) | merged |
| [!343](merge_requests/343.md) | feat(query): a component filter narrows a typed observation query (#329 step 2) | merged |
| [!344](merge_requests/344.md) | feat(query): stream() — Arrow batches from a typed query (#329) | merged |
| [!345](merge_requests/345.md) | perf(query): an unordered stream is bounded — 441 MB to 5 MB (#329) | merged |
| [!346](merge_requests/346.md) | feat(data): declare and write a sort order for observations (#330) | merged |
| [!347](merge_requests/347.md) | feat(query): an ordered stream at bounded memory, by partition (#331) | merged |
| [!348](merge_requests/348.md) | fix(data): labelling silently did nothing, and observations now project by id | merged |
| [!349](merge_requests/349.md) | feat(query): labels as an argument on the terminal (#329) | merged |
| [!350](merge_requests/350.md) | feat(catalog): sql() — the raw escape hatch, and why the deletion waits (#329) | merged |
| [!351](merge_requests/351.md) | feat(query): a series attribute is a column, wherever it is stored | merged |
| [!352](merge_requests/352.md) | feat(data)!: the data plane takes no SQL text | merged |
| [!353](merge_requests/353.md) | docs(design): the Registry API — Registry and Catalog are one concept | merged |
| [!354](merge_requests/354.md) | feat(query): ComponentRole and the SDMX REST data capabilities | merged |
| [!355](merge_requests/355.md) | fix(rest)!: the SDMX key's shape depends on the API version | merged |
| [!356](merge_requests/356.md) | feat(query): lower an observation query onto a /data URL | merged |
| [!357](merge_requests/357.md) | feat(query): the data scan's bound, and the provider's own refusal | merged |
| [!358](merge_requests/358.md) | feat(api): observations over HTTP — RestRegistry.observations | merged |
| [!359](merge_requests/359.md) | perf(api): a remote fetch is bounded — DuckDB reads the CSV itself | merged |
| [!360](merge_requests/360.md) | docs(design): restore hand-formatting in registry-and-catalog examples | merged |
| [!361](merge_requests/361.md) | refactor(model)!: .urn is a property; urn_for builds one | merged |
| [!362](merge_requests/362.md) | feat(query)!: an observation result is named by component id | merged |
| [!363](merge_requests/363.md) | refactor(api)!: one get(), with resolution depth as a parameter | merged |
| [!364](merge_requests/364.md) | docs(design)!: step 5's alias deletions do not survive the evidence | merged |
| [!365](merge_requests/365.md) | feat(local)!: a registry answers for observations, not only artefacts | merged |
| [!366](merge_requests/366.md) | feat!: delete FederatedCatalog — a registry is the whole surface | merged |
| [!367](merge_requests/367.md) | fix(formats)!: writers take (dsd, dataflow_ref), not a binding | merged |
| [!368](merge_requests/368.md) | feat(api): download() asks for a format, and checks it arrived | merged |
| [!369](merge_requests/369.md) | fix(api): RestRegistry states a thread-safety contract, and meets it | merged |
| [!370](merge_requests/370.md) | docs(design): §5.16 is filed as #338, and is sharper than it was written | merged |
| [!371](merge_requests/371.md) | fix(api)!: a `latest` URN is a pointer, not a cache key | merged |
| [!372](merge_requests/372.md) | refactor(api)!: sl.Registry -> sl.LocalRegistry, materialise_to= -> store= | merged |
| [!373](merge_requests/373.md) | fix(query)!: refuse a relation spliced across two databases | merged |
| [!374](merge_requests/374.md) | fix(api)!: unreachable raises and names itself; absent returns None | merged |
| [!375](merge_requests/375.md) | feat(storage): record when an artefact was fetched, not only from where | merged |
| [!376](merge_requests/376.md) | fix(tests): re-measure value_set; DuckDB binds 25x faster than documented | merged |
| [!377](merge_requests/377.md) | feat(query): wire detail=allstubs through, so a projection fetches stubs | merged |
| [!378](merge_requests/378.md) | docs(design): record §5.13's decision; guard cassettes against outliving tests | merged |
| [!379](merge_requests/379.md) | fix(local): name the unattached catalog instead of leaking a binder error | merged |
| [!380](merge_requests/380.md) | fix(query): a REST query answers from its own fetch, not from the store | merged |
| [!381](merge_requests/381.md) | docs(local): repair four docstrings the LocalRegistry rename walked through | merged |
| [!382](merge_requests/382.md) | fix(query): route a metadataset request to /metadata, not /structure | merged |
| [!383](merge_requests/383.md) | feat(api): a freshness window for the `latest` pointer, from the provider | merged |
| [!384](merge_requests/384.md) | test(integration): re-record against ECB; no skipped integration tests left | merged |
| [!385](merge_requests/385.md) | fix(query)!: n_unique counts a null as a value, as polars does | merged |
| [!386](merge_requests/386.md) | docs: an upgrade guide, and repair what 44 breaking changes left behind | merged |
| [!387](merge_requests/387.md) | test(docs): cover the ten user-facing pages that had no test | merged |
| [!388](merge_requests/388.md) | test(docs): run the code that is in the documentation, not a copy of it | merged |
| [!389](merge_requests/389.md) | test(docs): no block opts out; the six skips were hiding defects | merged |
| [!390](merge_requests/390.md) | feat(api): FederatedRegistry.get takes resolve= and partial=, translated per source | merged |
| [!391](merge_requests/391.md) | fix(api)!: the session records how completely a URN resolved, not whether | merged |
| [!392](merge_requests/392.md) | test: close the gate holes a whole-codebase review turned up | merged |
| [!393](merge_requests/393.md) | refactor(typing)!: list[object] for decoded cells, and warnings fail the gate | merged |
| [!394](merge_requests/394.md) | test(docs): one system — the pages run, and their printed claims are checked | merged |
| [!395](merge_requests/395.md) | test(bench): the benchmark suite runs again, and guards #348's four shapes | merged |
| [!396](merge_requests/396.md) | fix(query)!: the anti-join was empty, and version.max() said 1.9 > 1.10 | merged |
| [!397](merge_requests/397.md) | refactor(query)!: edges name their target, so file order is no longer API | merged |
| [!398](merge_requests/398.md) | feat(query): having(), and a projection you can name its own items in | merged |
| [!399](merge_requests/399.md) | feat(query): closed vocabularies are checked, and a colliding projection is refused | merged |
| [!400](merge_requests/400.md) | test(query): pin the invariant the vocabulary check actually depends on | merged |
| [!401](merge_requests/401.md) | feat(query): version ranges, validity, sl.union — and two refusals in our words | merged |
| [!402](merge_requests/402.md) | fix(query): a projected timestamp carries its zone, as the artefact's does | merged |
| [!403](merge_requests/403.md) | fix(query)!: union across two databases is refused, as intersect already was | merged |
| [!404](merge_requests/404.md) | fix(formats): SDMX-ML 3.1 stops dropping what it never learned to carry | merged |
| [!405](merge_requests/405.md) | fix(ml31): the 3.1 writer was dropping ten kinds of annotation | merged |
| [!406](merge_requests/406.md) | test(formats): compare every field against the model, not against a checklist | merged |
| [!407](merge_requests/407.md) | test(formats): round-trip real provider messages, not just a fixture | merged |
| [!408](merge_requests/408.md) | fix(formats): is_final — stop emitting it where forbidden, read it where it exists | merged |
| [!409](merge_requests/409.md) | test(formats): hunt real examples for the missing fields — five were not missing | merged |
| [!410](merge_requests/410.md) | fix(model): wire MetadataProvisionAgreement into StructureMessage | merged |
| [!411](merge_requests/411.md) | chore(api): drop the dead wire-parameter presets and fix a stale CLAUDE.md example | merged |
| [!412](merge_requests/412.md) | feat(query)!: project the accessor's name, and add alias() to rename one | merged |
| [!413](merge_requests/413.md) | feat(query)!: [] for an empty fan-out, eager to-many refusal, ordered aggregates | merged |
| [!414](merge_requests/414.md) | fix(json)!: SDMX-JSON no longer carries isFinal | merged |
| [!415](merge_requests/415.md) | fix(formats): validate what the census writes — 30 conformance errors, four real | merged |
| [!416](merge_requests/416.md) | feat(json): our SDMX-JSON is valid 2.1 as well as 2.0 | merged |
| [!417](merge_requests/417.md) | chore(release): group breaking changes in the changelog, and document the JSON one | merged |
| [!418](merge_requests/418.md) | fix(formats): validity dates on eight maintainables per version; declare the HierarchicalCode name | merged |
| [!419](merge_requests/419.md) | fix(sdmx-ml 2.1): annotations on hierarchies, their nodes, and the four map types | merged |
| [!420](merge_requests/420.md) | fix(formats): DimensionConstraint in 3.1, a Level's uri in JSON, MSD layers declared | merged |
| [!421](merge_requests/421.md) | test(formats): split the register into declared limits and real defects | merged |
| [!422](merge_requests/422.md) | fix(formats): a Group's and a Category's uri; declare that an Agency has no parent | merged |
| [!423](merge_requests/423.md) | fix(sdmx-ml 2.1): the last of #355 — four defects fixed, two declared | merged |
| [!424](merge_requests/424.md) | fix(api): every type in a public signature is importable — #299 | merged |
| [!425](merge_requests/425.md) | refactor(model)!: drop Attribute.related_measure — #360 | merged |
| [!426](merge_requests/426.md) | fix(json): emit a value's parent in data messages — #365 | merged |
| [!427](merge_requests/427.md) | feat(json): carry annotations in data messages — #366 | merged |
| [!428](merge_requests/428.md) | feat(model): model AnnotationValue, which was read and dropped — #368 | merged |
| [!429](merge_requests/429.md) | test(fixture): keep the <xs:choice> sweep #359 asked for | merged |
| [!430](merge_requests/430.md) | fix(formats): a <Ref> without @package is read, not dropped — #240 | merged |
| [!431](merge_requests/431.md) | fix(model)!: the collections are honestly unhashable — #290 | merged |
| [!432](merge_requests/432.md) | feat(model): model MetadataProvider, so the MPA's provider urn resolves — #315 | merged |
| [!433](merge_requests/433.md) | test(tooling): gate dead suppressions, and type-check tests/support — #347 | merged |
| [!434](merge_requests/434.md) | test(formats): say where the 3.0/3.1 fork differs, and close the drift — #346 | merged |
| [!435](merge_requests/435.md) | docs: tutorials for the query language — metadata and observations | merged |
| [!436](merge_requests/436.md) | Only public PyPI may be named in this repository | merged |
| [!437](merge_requests/437.md) | The index guard cannot scan its own fixtures | merged |
| [!438](merge_requests/438.md) | The upgrading guide's new blocks run | merged |
| [!439](merge_requests/439.md) | An association reaches the component it binds — #372 | merged |
| [!440](merge_requests/440.md) | An absent reference is NULL, not the empty string | merged |
| [!441](merge_requests/441.md) | q.hierarchy_applicability — the resolution rule, stated once — #372 | merged |
| [!442](merge_requests/442.md) | where= on every aggregate — collect some of the fan-out — #373 | merged |
| [!443](merge_requests/443.md) | chore(claude): drop dead Write() ask rules from settings | merged |
| [!444](merge_requests/444.md) | perf(query): collapse the ancestor EXISTS to a containment test — #349 | merged |
| [!445](merge_requests/445.md) | perf(storage): stop materialize building a Level per node to discard it — #154 | merged |
| [!446](merge_requests/446.md) | test(bench): benchmark against a real bundle, and make the skip audible — #379 | merged |
| [!447](merge_requests/447.md) | perf(storage): stop the row builders allocating what they discard — #377 | merged |
| [!448](merge_requests/448.md) | perf: materialise what is read — label transport and an annotation opt-out — #380 #381 | merged |
| [!449](merge_requests/449.md) | perf(model): decide once per class which fields can hold a Ref — #376 | merged |
| [!450](merge_requests/450.md) | perf(registry): resolve the item refs asked for, not their whole scheme — #378 | merged |
| [!451](merge_requests/451.md) | perf: the ref-walk and assignment pass — #383 #384 #154 | merged |
| [!452](merge_requests/452.md) | fix(local): the data plane was unreachable on a real bundle — #387 | merged |
| [!453](merge_requests/453.md) | perf(query): emit the sidecar join only where a column reads it — #388 | merged |
| [!454](merge_requests/454.md) | feat(query): order_by(None) declines the total order — #389 | merged |
| [!455](merge_requests/455.md) | test(bench): replace %18's unreachable 5x criterion — #382 | merged |
| [!456](merge_requests/456.md) | perf(local): bound the resolver cache in items, not entries — #386 | merged |
| [!457](merge_requests/457.md) | chore(storage): delete a dead istring converter — #154 | merged |
| [!458](merge_requests/458.md) | fix(validation): walk refs by class shape, not by a hand-listed descent — #148 | merged |
| [!459](merge_requests/459.md) | perf(local): parent-scheme URN cache, and %18's first criterion made reachable | merged |
| [!460](merge_requests/460.md) | docs(hierarchies): intersect two subtrees in the engine — #375 | merged |
| [!461](merge_requests/461.md) | docs(query): record why is_in still binds one placeholder per value — #301 | merged |
| [!462](merge_requests/462.md) | docs(discovery): document splicing a compiled query into your own SQL — #393 | merged |
| [!463](merge_requests/463.md) | fix(model): resolve Refs held in a plain list or tuple — #392 | merged |
| [!464](merge_requests/464.md) | test(bench): read a real bundle's observations — #391 | merged |
| [!465](merge_requests/465.md) | test(bench): put the bundle tier's assertions in `just ci` — #390 | merged |
| [!466](merge_requests/466.md) | fix(model): all_artefacts() is every slot, and storage says why it refuses — #394 | merged |
| [!467](merge_requests/467.md) | feat(storage): store the two SDMX 2.1 grouping containers — #395 | merged |
| [!468](merge_requests/468.md) | test(formats): the fork guard now watches module-level data too — #346 | merged |
| [!469](merge_requests/469.md) | test(types): basedpyright over integration, docs and bench — #347 | merged |
| [!470](merge_requests/470.md) | fix(model): the four get()s take the same two things — #370 | merged |
| [!471](merge_requests/471.md) | feat(query): collect() returns the artefact, not object — #396 | merged |
| [!472](merge_requests/472.md) | fix(local): get() declares the lazy classes it returns — #397 | merged |
| [!473](merge_requests/473.md) | test(types): basedpyright over tests/unit — #347 | merged |
| [!474](merge_requests/474.md) | feat(storage): storage kinds for the three remaining maps — #395 | merged |
| [!475](merge_requests/475.md) | perf(storage): build item URNs once per artefact, not once per item — #139 | merged |
| [!476](merge_requests/476.md) | perf(storage): the cascade runs only the deletes its type can have rows for — #144 | merged |
| [!477](merge_requests/477.md) | test(bench): the bundle tier watches the write path — #140 | merged |
| [!478](merge_requests/478.md) | perf(model): _by_type is keyed by URN, like _store — #138 | merged |
| [!479](merge_requests/479.md) | perf(storage): the cascade finds annotations by an indexed column — #145 | merged |
| [!480](merge_requests/480.md) | fix(local): refuse a store whose scans lose rows, and move to duckdb 1.5.5 — #400 | merged |
| [!481](merge_requests/481.md) | test(bench): the construction tax is a count, not a clock — #401 | merged |
| [!482](merge_requests/482.md) | fix(model): CodelistLike — one declaration for the eager and lazy codelist — #398 | merged |
| [!483](merge_requests/483.md) | test(dev): a docstring gate with three arms, all at zero — #374 | merged |
| [!484](merge_requests/484.md) | fix(model): every immutable-collection field converts what it is given — #371 | merged |
| [!485](merge_requests/485.md) | test(query): mutation installment 2 — the two discriminator clauses, and a dead runner | merged |
| [!486](merge_requests/486.md) | test: anchor every test to repo.ROOT, and guard the spelling that got past #252 — #402 | merged |
| [!487](merge_requests/487.md) | refactor(formats): one v3 reader for 3.0 and 3.1 — #346 option 1, reader half | merged |
| [!488](merge_requests/488.md) | refactor(formats): one v3 writer, and the gap 3.1 was hiding — #346 | merged |
| [!489](merge_requests/489.md) | docs: a guard has to stay able to fail, and eight that had stopped — #403 | merged |
| [!490](merge_requests/490.md) | fix(model): validate_references resolves item and version-less refs — #404 | merged |
| [!491](merge_requests/491.md) | fix(model): an item URN resolves to the item, at every layer — #405 | merged |
| [!492](merge_requests/492.md) | refactor(storage): item → owner comes off the item class, not a table — #405 | merged |
| [!493](merge_requests/493.md) | docs(typing): #399 is a checker deviation, not an overload trade-off | merged |
| [!494](merge_requests/494.md) | fix(api): a written artefact states its URN, and get() takes expect= — #406 | merged |
| [!495](merge_requests/495.md) | feat(model): ref_to on every item-bearing scheme — #407 | merged |
| [!496](merge_requests/496.md) | fix(typing): Ref is covariant — eight Any that were never needed | merged |
| [!497](merge_requests/497.md) | feat(polars): coded_as="string", so validate can actually report — #408 | merged |
| [!498](merge_requests/498.md) | feat(csv): scan_csv can use the structure the file names — #409 | merged |
| [!499](merge_requests/499.md) | fix(formats): the SDMX-JSON data writer reads the codes a message observed — #410 | merged |
| [!500](merge_requests/500.md) | docs: binding cost is pandas' absence, not DuckDB's speed — #411 | merged |
| [!501](merge_requests/501.md) | perf(query): is_in binds its set as one list parameter, = ANY(?) — #411 | merged |
| [!502](merge_requests/502.md) | fix(query): time columns compare instants, whatever zone the literal or session is in — #415 | merged |
| [!503](merge_requests/503.md) | chore(deps): pin basedpyright to 1.39.9 — pyright 1.1.412 regression | merged |
| [!504](merge_requests/504.md) | docs: cast OBS_VALUE before aggregating it — polars 2 refuses a string mean | merged |
| [!505](merge_requests/505.md) | fix(local): refuse a series-attributes sidecar that is not unique on its join keys — #412 | merged |
| [!506](merge_requests/506.md) | fix(polars): give validation_report and Dataset.pivot a stable order | merged |
| [!507](merge_requests/507.md) | fix(query): ObsQuery.col() declares Untyped, so SDMX-REST's co/nc/sw/ew are visible — #413 | merged |
| [!508](merge_requests/508.md) | feat(query): Query.agg and sl.len() — several aggregates over a whole query in one scan — #414 | merged |
| [!509](merge_requests/509.md) | fix(types): return-widening audit — two declarations corrected, and a guard for the shape | merged |
| [!510](merge_requests/510.md) | fix(types): a URN parsed from text is SdmxUrn[object]; parse on the class to type it — #399 | merged |
| [!511](merge_requests/511.md) | chore: pyrefly is the only type checker — #425 | merged |
| [!512](merge_requests/512.md) | fix: SDMX-CSV/JSON batch conversion regression (#427), metadata re-add PK violation (#428) | merged |
| [!513](merge_requests/513.md) | chore(test): run the unit tier in parallel with pytest-xdist | merged |
| [!514](merge_requests/514.md) | perf(storage): one spine read per get(), name+description together — #426 | merged |
| [!515](merge_requests/515.md) | fix: Ref fields refuse URN text (#418), JSON version grammar (#417), org-scheme version (#416) | merged |
| [!516](merge_requests/516.md) | Registry sweep: measure %20 against six live registries | merged |
| [!517](merge_requests/517.md) | Wire gate over populated artefacts: valid or refused, per #423 row | merged |
| [!518](merge_requests/518.md) | A version a format cannot carry is refused with ValidationError | merged |
| [!519](merge_requests/519.md) | A reference's version is checked against the target format (#429) | merged |
| [!520](merge_requests/520.md) | A "latest" reference is refused in SDMX-ML 2.1 too (#233) | merged |
| [!521](merge_requests/521.md) | A code whose parent SDMX-ML 3.0.0 cannot name is refused for 3.0 (#432) | merged |
| [!522](merge_requests/522.md) | Cube regions hold time ranges and component value sets (#433) | merged |
| [!523](merge_requests/523.md) | Sweep census: schema defaults and empty elements are not losses | merged |
| [!524](merge_requests/524.md) | LocalRegistry keeps a constraint's data_keys; the store joins the field census (#437) | merged |
| [!525](merge_requests/525.md) | LocalRegistry keeps a component's enumeration_format (#438) | merged |
| [!526](merge_requests/526.md) | Every model datetime is held tagged UTC, so a parsed artefact equals its stored self (#440) | merged |
| [!527](merge_requests/527.md) | The store keeps every item's uri, value-map validity, level annotations and Agency.parent (#442) | merged |
| [!528](merge_requests/528.md) | A 2.1 HierarchicalCodelist in the alias form reads whole (#434) | merged |
| [!529](merge_requests/529.md) | LazyHierarchy.materialize() keeps every node's annotations (#443) | merged |
| [!530](merge_requests/530.md) | Readers refuse per artefact; parse() reports every refusal (#435) | merged |
| [!531](merge_requests/531.md) | SDMX-ML readers refuse an artefact holding an element the schema allows nowhere (#436) | merged |
| [!532](merge_requests/532.md) | A time dimension's textType must be a time type (#431) | merged |
| [!533](merge_requests/533.md) | A reference to a class the target cannot reference is refused (#430) | merged |
| [!534](merge_requests/534.md) | chore(bench): sweep re-run over milestone 20, census counts refused writes as read | merged |
| [!535](merge_requests/535.md) | feat: nested items are keyed by path, not leaf id (#439) | merged |
| [!536](merge_requests/536.md) | Finality is derived from the version; 2.1's isFinal kept as legacy_is_final (#420) | merged |
| [!537](merge_requests/537.md) | Versions only SDMX 2.1 accepts are refused at ingest, unless rewritten (#420) | merged |
| [!538](merge_requests/538.md) | Drafts are left out of SDMX-ML 2.1, with what references them (#420) | merged |
| [!539](merge_requests/539.md) | What an SDMX-ML read cannot hold is recorded, never dropped silently (#422) | merged |
| [!540](merge_requests/540.md) | A DSD holds SDMX 3.0's component additions (#422) | merged |
| [!541](merge_requests/541.md) | SDMX-JSON records what it cannot hold; an excluding value set is refused (#422) | merged |
| [!542](merge_requests/542.md) | A cube region value set can exclude its values (#422) | merged |
| [!543](merge_requests/543.md) | A cube region's values and value sets carry their qualifiers (#422) | merged |
| [!544](merge_requests/544.md) | feat: model SDMX 3.x geographic codelists (#422) | merged |
| [!545](merge_requests/545.md) | feat!: model SDMX 3.x ValueList; a representation may enumerate by one (#422) | merged |
| [!546](merge_requests/546.md) | feat!: annotation URLs, sentinel values and time range validity are held (#422) | merged |
| [!547](merge_requests/547.md) | test: an element census finds what SDMX-ML 3.x readers drop unrecorded (#422) | merged |
| [!548](merge_requests/548.md) | feat: SDMX-JSON reports every key its structs skip; a JSON census (#422) | merged |
| [!549](merge_requests/549.md) | fix: SDMX-ML 3.x writes and reads a hierarchy's levels as a chain (#422) | merged |
| [!550](merge_requests/550.md) | fix: an SDMX-ML 2.1 element census, and the misreadings it found (#422) | merged |
| [!551](merge_requests/551.md) | feat: annotations on component lists, groups and 2.1 target objects (#422) | merged |
| [!552](merge_requests/552.md) | feat: annotations on maps, value mappings, regions and keys (#422) | merged |
| [!553](merge_requests/553.md) | test: an attribute census finds what SDMX-ML readers drop unrecorded (#422) | merged |
| [!554](merge_requests/554.md) | feat: external references and partial item schemes (#422) | merged |
| [!555](merge_requests/555.md) | feat: a format's sequence and time facets; a concept's enumeration format and ISO reference (#422) | merged |
| [!556](merge_requests/556.md) | feat: the smaller fields the attribute census found (#422) | merged |
| [!557](merge_requests/557.md) | feat: the last attribute drops the attribute census found (#422) | merged |
| [!558](merge_requests/558.md) | feat: a data key's components (#422) | merged |
| [!559](merge_requests/559.md) | feat: a structure map's date and fixed-value maps (#422) | merged |
| [!560](merge_requests/560.md) | feat!: maps are N:M (#422) | merged |
| [!561](merge_requests/561.md) | feat!: a constraint attaches to several artefacts (#422) | merged |
| [!562](merge_requests/562.md) | feat: SDMX-ML 2.1's own DSD constructs are held (#422) | merged |
| [!563](merge_requests/563.md) | feat: SDMX 3.x's com:Link is held (#422) | merged |
| [!564](merge_requests/564.md) | feat: what SDMX-JSON's links say is held (#422) | merged |
| [!565](merge_requests/565.md) | feat: SDMX-ML 2.1's representation mappings are held (#422) | merged |
| [!566](merge_requests/566.md) | fix: closed-world check refuses a child a restriction prohibits (#422) | merged |
| [!567](merge_requests/567.md) | feat!: a 2.1 metadata target holds all its target objects (#422) | merged |
| [!568](merge_requests/568.md) | docs: the last three LOSSES rows are declared losses (#422) | merged |
| [!569](merge_requests/569.md) | feat!: drop the fields no SDMX format carries (#419) | merged |
| [!570](merge_requests/570.md) | feat!: add refuses what no SDMX format can write (#419) | merged |
| [!571](merge_requests/571.md) | feat!: readers refuse what add would, and RestRegistry reads on (#419) | merged |
| [!572](merge_requests/572.md) | fix: a dimension position may skip a number, not contradict order (#449) | merged |
| [!573](merge_requests/573.md) | fix(bench): a counted finding's examples leave its sweep key (#450) | merged |
| [!574](merge_requests/574.md) | feat: a single 3.x measure is written to SDMX-ML 2.1 as OBS_VALUE (#421) | merged |
| [!575](merge_requests/575.md) | feat: an extending codelist is written to SDMX-ML 2.1 with what it inherits (#421) | merged |
| [!576](merge_requests/576.md) | feat: 2.1 hierarchies named for their codelist; HCL and HA left out where absent (#421) | merged |
| [!577](merge_requests/577.md) | feat: a 2.1 map is named for its structure set; 3.x leaves the set out (#421) | merged |
| [!578](merge_requests/578.md) | feat: 3.1 drops a release calendar as a declared gap; AttachmentConstraint conversion declared (#421) | merged |
| [!579](merge_requests/579.md) | feat: model SDMX 3.1's AvailabilityConstraint; an Actual constraint that fits is written as one (#421) | merged |
| [!580](merge_requests/580.md) | feat: a 3.x MSD is written to SDMX-ML 2.1 with a marked reporting layer (#421) | merged |
| [!581](merge_requests/581.md) | feat: a 2.1 metadataflow is given the targets 3.x requires, derived from its MSD (#421) | merged |
| [!582](merge_requests/582.md) | feat: metadata providers and provision agreements are written to SDMX-ML 2.1 as 2.1 provisioned metadata (#421) | merged |
| [!583](merge_requests/583.md) | feat: an attribute's usage and relationship convert across every format (#421) | merged |
| [!584](merge_requests/584.md) | fix: SDMX-JSON writes a wildcarded extension member as the codes it matches (#451) | merged |
| [!585](merge_requests/585.md) | feat: model OrganisationSchemeMap in every format, the store and the query language (#448) | merged |
| [!586](merge_requests/586.md) | feat: uri is a query path on every relation that stores it (#444) | merged |
| [!587](merge_requests/587.md) | bench: refresh the sweep summaries for milestone 20 | merged |
| [!588](merge_requests/588.md) | test: the accent rungs of both oracles fold case with lower, as SQL does | merged |
| [!589](merge_requests/589.md) | Milestone 20: 3.x-native model — hold all of 3.x, project to 2.1, validate at ingest | merged |
| [!590](merge_requests/590.md) | bench: a competitor benchmark — speed, agreement and output validity against pysdmx and sdmx1 | merged |
| [!591](merge_requests/591.md) | perf(sdmx_ml): read no links from 2.1, and no QName per code (#463) | merged |
| [!592](merge_requests/592.md) | perf(sdmx_json): find a read's losses when they are asked for (#461) | merged |
| [!593](merge_requests/593.md) | perf(codelist): index codelists for a wildcard only when there is one (#462) | merged |
| [!594](merge_requests/594.md) | perf(formats): work out what a format cannot hold once per write (#464) | merged |
| [!595](merge_requests/595.md) | fix(sdmx_json): name the structure schema in meta, so pysdmx reads our JSON (#465) | merged |
| [!596](merge_requests/596.md) | fix(sdmx_json): cite schemas at json.sdmx.org, where SDMX publishes them | merged |
| [!597](merge_requests/597.md) | bench: leave sdmx1 out of the scale tier | merged |
| [!598](merge_requests/598.md) | perf(sdmx_json): stop paying per item for work an item does not need (#466) | merged |
| [!599](merge_requests/599.md) | perf(sdmx_json): build nothing per item that has no link to write or check (#462) | merged |
| [!600](merge_requests/600.md) | perf(sdmx_json): read every component's codes in one batch per kind (#441) | merged |
| [!601](merge_requests/601.md) | perf(storage): read an artefact's annotations by its indexed column (#426) | merged |
| [!602](merge_requests/602.md) | docs: say what a JSON-read message holds until lost is read, and how to release it (#473) | merged |
| [!603](merge_requests/603.md) | fix(sdmx_ml21): a ContentConstraint without @type is Actual, as the schema defaults (#475) | merged |
| [!604](merge_requests/604.md) | Record what an SDMX-ML read assumes; refuse what it cannot assume (#474, part 1) | merged |
| [!605](merge_requests/605.md) | SDMX-JSON: refuse the entry, not the document; record what a read assumes (#474, part 2) | merged |
| [!606](merge_requests/606.md) | Hold a constraint's keys in key sets, each included or excluded (#479) | merged |
| [!607](merge_requests/607.md) | A 2.1 reference without a version names 1.0, as the schema defaults (#476) | merged |
| [!608](merge_requests/608.md) | A component without an id takes its concept's, as the schema says (#477) | merged |
| [!609](merge_requests/609.md) | Refuse an artefact holding an attribute its schema allows nowhere (#446) | merged |
| [!610](merge_requests/610.md) | Refuse what the frame around the artefacts holds that the schema allows nowhere (#447) | merged |
| [!611](merge_requests/611.md) | A name blank in any language is refused (#452) | merged |
| [!612](merge_requests/612.md) | A parent_id names an item of its scheme, and no chain loops (#453) | merged |
| [!613](merge_requests/613.md) | A series attachment names the DSD's dimensions, a group one its groups (#454) | merged |
| [!614](merge_requests/614.md) | A hierarchical code's level is one of its hierarchy's, never above its depth (#455) | merged |
| [!615](merge_requests/615.md) | fix(sdmx-json): carry the dataflow's annotations in data messages | merged |
| [!616](merge_requests/616.md) | fix(sdmx-json): carry the dataflow's annotations in data messages | merged |
| [!617](merge_requests/617.md) | test: an information-model rule census, guarded both ways (#456) | merged |
| [!618](merge_requests/618.md) | test: FMR 12.4.2 as an independent oracle, recorded and replayed (#457) | merged |
| [!619](merge_requests/619.md) | test: pysdmx 1.20.0 as the SDMX-ML 3.1 oracle (#457 part 2) | merged |
| [!620](merge_requests/620.md) | build: require duckdb>=1.5.6 | merged |
| [!621](merge_requests/621.md) | Mutation over gaps.py, and import-time mutants credited correctly (#458 part 1) | merged |
| [!622](merge_requests/622.md) | Mutation over the 2.1 reader: a census that never compared text, and xs:boolean read one way (#458 part 2) | merged |
| [!623](merge_requests/623.md) | test: kill the 2.1 writer's mutation survivors, triage the rest (#458) | merged |
| [!624](merge_requests/624.md) | chore(deps): widen uv_build pin to >=0.13.0,<0.14.0 | merged |
| [!625](merge_requests/625.md) | chore(tools): port mutmut_forksafe to mutmut 3.8's fork server | merged |
| [!626](merge_requests/626.md) | fix(formats): the 3.x reader reads 3.x; refuse a dangling level (#458) | merged |
| [!627](merge_requests/627.md) | fix(tools): mutate code that runs at import, and book its breakage as a kill (#458) | merged |
| [!628](merge_requests/628.md) | test: kill the 3.x writer's mutation survivors, triage the rest (#458) | merged |
| [!629](merge_requests/629.md) | test: kill sdmx_ml/_common.py's mutation survivors, triage the rest (#458) | merged |
| [!630](merge_requests/630.md) | test: halve the unit tier's cost without changing what it checks | merged |
| [!631](merge_requests/631.md) | build: require Python 3.14 | merged |
| [!632](merge_requests/632.md) | chore(deps): pyrefly 1.4, src/ and bench/ at zero | merged |
| [!633](merge_requests/633.md) | test: kill sdmx_json/_structure_ingest.py's mutation survivors, triage the rest (#458) | merged |
| [!634](merge_requests/634.md) | chore(deps): ruff 0.17 | merged |
| [!635](merge_requests/635.md) | chore(deps): pydoclint 0.11 | merged |
| [!636](merge_requests/636.md) | test: kill sdmx_json/writer.py's mutation survivors, triage the rest (#458) | merged |
| [!637](merge_requests/637.md) | fix(json)!: check SDMX-JSON formats, and write what they require — #496 | merged |
| [!638](merge_requests/638.md) | chore(bench): install pysdmx's json extra with the compete group | merged |
| [!639](merge_requests/639.md) | fix(validation): validate a message by checking its artefacts — #492 | merged |
| [!640](merge_requests/640.md) | feat(model)!: refuse a bad id, agency id or version as it is set — #492 stage 1, part 1 | merged |
| [!641](merge_requests/641.md) | feat(model)!: a name is required, and refused blank as it is set — #492 stage 1, part 2 | merged |
| [!642](merge_requests/642.md) | feat(model)!: refuse a zero facet bound or an unbounded time range as it is set — #492 stage 1, part 3 | merged |
| [!643](merge_requests/643.md) | feat(storage)!: a store read refuses an artefact add would refuse — #492 stage 1, part 4 | merged |
| [!644](merge_requests/644.md) | feat(model)!: a Ref is frozen and finds its target on access — #492 stage 2, part 1 | merged |
| [!645](merge_requests/645.md) | feat(model)!: every model class is frozen — #492 stage 2, part 2 | merged |
| [!646](merge_requests/646.md) | feat(model)!: every whole-artefact rule runs as the artefact is built — #492 stage 2, part 3 | merged |
| [!647](merge_requests/647.md) | fix(model)!: refuse an empty measure relationship as it is set — #497 | merged |
| [!648](merge_requests/648.md) | bench(compete): say what each library keeps, and label a refused write as one (#500) | merged |
| [!649](merge_requests/649.md) | fix(formats): read a node naming an undefined level with no level, and record the loss (#501) | merged |
| [!650](merge_requests/650.md) | feat(model): model DataConsumerScheme and OrganisationUnitScheme (#469) | merged |
| [!651](merge_requests/651.md) | bench(sweep): refresh the SDMX global registry's summary after #469 | merged |
| [!652](merge_requests/652.md) | fix(tests): the fold oracles keep a Hangul syllable whole, as strip_accents does (#494) | merged |
| [!653](merge_requests/653.md) | bench(sweep): refresh ESTAT's summary against #492 and #469 | merged |
| [!654](merge_requests/654.md) | fix(query): a version min/max through a fan-out answers instead of refusing (#324) | merged |
| [!655](merge_requests/655.md) | test(query): pin _window's gaps and delete its dead n_unique entries (#324) | merged |
| [!656](merge_requests/656.md) | fix(model): a later codelist extension wins a code clash (#486) | merged |
| [!657](merge_requests/657.md) | feat(model): warn on IM rules no wire schema enforces (#481, #482, #491) | merged |
| [!658](merge_requests/658.md) | feat(model): read and write a message's header (#480) | merged |
| [!659](merge_requests/659.md) | feat(formats): write the minimal spelling, and hold what a default means (#503) | merged |
| [!660](merge_requests/660.md) | perf(storage): read the spine and annotation URLs without correlated subqueries | merged |
| [!661](merge_requests/661.md) | chore(release): v0.68.0 | merged |
| [!662](merge_requests/662.md) | fix: every public signature evaluates at runtime on 3.14 | merged |
| [!663](merge_requests/663.md) | docs: rewrite llms.txt and llms-dsl.txt, and execute them | merged |
| [!664](merge_requests/664.md) | fix(query): a grouped aggregate honours where= | merged |
| [!665](merge_requests/665.md) | docs: bring every page up to date with v0.68 | merged |
| [!666](merge_requests/666.md) | fix(query)!: a predicate belongs to the relation it was built from | merged |
| [!667](merge_requests/667.md) | fix(model)!: a time dimension always has a format | merged |
| [!668](merge_requests/668.md) | fix(local): a store's constructor refuses a connection by name | merged |
| [!669](merge_requests/669.md) | fix(model)!: a time or measure dimension needs a concept | merged |
| [!670](merge_requests/670.md) | fix(model): a sentinel on an enumeration or coding format is refused as built | merged |
| [!671](merge_requests/671.md) | fix(json)!: SDMX-JSON data writers take no default language | merged |
| [!672](merge_requests/672.md) | feat(api): export the exceptions the docs tell callers to catch | merged |
| [!673](merge_requests/673.md) | fix(api): AncestorMap.ancestors() returns the public SqlRelation | merged |
| [!674](merge_requests/674.md) | fix(query): misuse is refused naming the fix, not by DuckDB | merged |
| [!675](merge_requests/675.md) | feat(data)!: a Dataset labels as a query does | merged |
| [!676](merge_requests/676.md) | refactor(query)!: Frame is Select | merged |
| [!677](merge_requests/677.md) | feat(query)!: an observation result is typed from its DSD | merged |
| [!678](merge_requests/678.md) | feat(query): an observation query exposes its structure | merged |
| [!679](merge_requests/679.md) | feat(query): an observation query sinks to Parquet, CSV and SDMX-JSON | merged |
| [!680](merge_requests/680.md) | feat(query): an observation query validates its values | merged |
| [!681](merge_requests/681.md) | fix(binding): a binding from a DSD maps the measure dimension | merged |
| [!682](merge_requests/682.md) | feat(query): an observation query pivots wide | merged |
| [!683](merge_requests/683.md) | feat(registry)!: scan_csv reads a file as an observation query | merged |
| [!684](merge_requests/684.md) | feat(registry): scan_json reads an SDMX-JSON data message as an observation query | merged |
| [!685](merge_requests/685.md) | feat!: retire reg.data(), DataQuery, sl.dim and Dataset | merged |
| [!686](merge_requests/686.md) | refactor!: observations() is obs() | merged |
| [!687](merge_requests/687.md) | fix(model)!: a node's id where its path belongs is refused | merged |
| [!688](merge_requests/688.md) | fix(registry)!: a REST filter nothing narrows is refused | merged |
| [!689](merge_requests/689.md) | fix(model)!: an attribute's attachment is required | merged |
| [!690](merge_requests/690.md) | feat(query)!: an observation column knows what it holds | merged |
| [!691](merge_requests/691.md) | fix(data)!: a measure the DSD leaves untyped is read as its values say | merged |
| [!692](merge_requests/692.md) | chore: name downstream systems by role, not by internal name | merged |
| [!693](merge_requests/693.md) | chore(release): v0.69.0 | merged |
| [!694](merge_requests/694.md) | chore: describe the lockfile's mirror generically | merged |
| [!695](merge_requests/695.md) | fix: SchemaInspector queries on the thread's own handle | merged |
| [!696](merge_requests/696.md) | chore: make GitHub the project's home | merged |
| [!697](merge_requests/697.md) | ci: move the docs workflow's actions to their Node 24 majors | merged |
| [!698](merge_requests/698.md) | ci: pin setup-uv to v10.3.0 | merged |
| [!699](merge_requests/699.md) | fix: cap duckdb below 2 | merged |
| [!700](merge_requests/700.md) | chore(release): v0.69.1 | merged |
