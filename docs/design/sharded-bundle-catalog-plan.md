# Sharded bundle catalog: implementation plan

*   **Author:** Gabriel Gellner
*   **Status:** Draft, second revision (after the EGM review of 2026-10-28)
*   **Date:** 2026-10-28

--------------------------------------------------------------------------------

## Overview

StatCat-Suite publishes SDMX products as sdmxlib bundles. A bundle is one directory per product holding a sequence of publications. Each publication is an immutable token directory with the structures (`meta.duckdb`), the observations (`lake.ducklake` over shared Parquet data files) and a manifest that lists every file and fingerprint. Today one sdmxlib-rest server opens one bundle. The target is a catalog the size of Statistics Canada's CODR (Canadian Open Data Repository, the store behind its data tables) served through the .Stat Suite. That is about 7,000 tables, with tens to a few hundred of them released each weekday at 08:30, under embargo until then, served by Kubernetes pods that each hold only the bundles their routing slice needs.

[`sharded-bundle-catalog.md`](sharded-bundle-catalog.md) (the specification, fifth revision) defines the formats and behaviour: manifest 2.x, fingerprints, the catalog index, routing, pools, cache keys, publication and garbage collection. This plan orders the remaining work into phases so that every repository stays releasable at the end of each phase. It specifies Phases 1 to 3 to implementation level, states the contracts and gating tests of Phases 4 to 6, and outlines Phases 7 to 9, which are re-planned once Phase 6 is merged.

Phase 0, the specification's fifth revision, is merged (PR #222). sdmxlib 0.75.0 is released with tier-3 reads, store-neutral verification, tiered pulls, the fingerprint comparison and the derived tables (GH-180 to GH-184).

## The Problem

A serving pod opens one bundle. At catalog scale that fails in four ways (specification §2.2): every pod's cold start pulls the whole catalog; a republish of one table redistributes everything; one backup unit spans thousands of release cadences; and one DuckDB storage version binds every product. The bundle format already supplies the shard: immutable token directories, shared `data/`, fingerprints, `check_bundle` profiles and a shared `BundleEngine`. Five pieces remain to build: an index that lists the shards, a router that sends each request to one, a job that releases many at once, a job that deletes what no publication references, and a builder that brings a dataflow's whole closure into a bundle, including the constraints and hierarchy associations that point at it (GH-221).

## Terms

*   **Publication**: one immutable token directory, `<bundle_id>/<token>/`. A republish makes a new publication of the same bundle.
*   **Shard**: what the router attaches, one publication of one bundle. The plan says *bundle* for the product and *publication* for one version of it.
*   **Tier**: where a file lives when served. Tier 1 (manifest, `meta.duckdb`, `lake.ducklake`) is pulled before a pod is ready; tier 2 (search tables) is pulled on first use; tier 3 (Parquet data files) is read from object storage by byte range.
*   **Pinned**: a bundle a pool attaches before its pods report ready.
*   **Partial**: a codelist or scheme vendored with only the codes a product uses and their ancestors (`isPartial="true"`). It records the fingerprint of the full artefact it was cut from (`cut_from`, specification §4.2).
*   **Structures bundle**: a bundle of `kind: structures` holding full codelists, concept schemes, category schemes and hierarchies, with no observations (specification §8).
*   **Release**: the 08:30 step that moves staged publications to `serving` with one conditional write of `catalog/current` (specification §11.2). The plan calls that write *the flip*.
*   **Pre-materialisation**: computing a release's most-requested results ahead of 08:30 into the result store, in a job that runs the pods' image (specification §11.2 step 3).
*   **FMR**: the Fusion Metadata Registry, the SDMX structure registry StatCan's producers edit. Version 12.4.2 serves SDMX REST 2.1 and 3.0.

## Goals

1.  A catalog of 7,000 synthetic CODR-shaped bundles, requested with a Zipf-distributed load, is indexed, routed and served by sdmxlib-rest pods that each hold only their routing slice. On the reference node (4 vCPU, 16 GB, object storage in the same region), a cold pod with one pinned bundle is ready in under 60 s, and the p99 first attach of an unpinned bundle is under 3 s. Phase 6's bench tests measure both.
2.  A release of 200 tables is visible at the origin on the flip and at the CDN within 90 s of it (specification §7.4), with zero failed requests during the flip, and no embargoed result is readable anywhere before it. Phase 5 tests the flip's index sequence; Phase 6 tests it under load.
3.  A delta to one dataflow changes that dataflow's result keys only; every other dataflow's CDN revalidation answers 304 (Phase 6).
4.  Garbage collection deletes exactly the data files that no publication within the index retention window references, and no file younger than the publish grace period. Index retention is shorter than backup retention (specification §10.3). Phase 5 tests both bounds.
5.  A store schema bump rolls through the fleet with two schema versions serving at once and zero non-2xx responses (Phase 9).
6.  A producer builds a bundle from an FMR's structures and observations held in a DuckLake table, and the bundle holds the dataflow's incoming constraints and hierarchy associations (Phase 1, CUJ 6).
7.  Each repository's `just ci` is green at the end of every phase, and each phase's producer is released before a consumer's phase pins it.

## Non-Goals

*   Changing the bundle or index formats beyond the specification. A format change is a PR to `sharded-bundle-catalog.md` first, as Phase 0 was.
*   A Postgres DuckLake catalog on the serving path (specification §14).
*   The `labels.duckdb` split, shelved after GH-139 measured labels at 31 MB of the census `meta.duckdb`.
*   Cross-bundle queries in sdmxlib-rest. A service that composes bundles calls sdmxlib-rest once per bundle (specification §7.6).
*   Renaming `token` to `publication` (specification open question 2) and detached manifest signatures (open question 3).
*   Producing the 7,000 real CODR bundles. That is the producers' work; this program builds and tests on synthetic ones.
*   Choosing the workflow engine that runs the publish and release jobs. The jobs are CLI commands any orchestrator can call.

## Open Questions

*   **Q1. CODR bundle granularity** (specification open question 6): one bundle per table by default. The synthetic corpus uses one per table; the real split is decided with the CODR producers.
*   **Q2. StatCan's structures-bundle split** (open question 8): by artefact family, by change rate, or both. Phase 3 builds `kind: structures` with any split.
*   **Q3. Code search scope** (open question 4): whether geography-first agent queries need the catalog-level code index to cover partials. Phase 7 builds it over full codelists only.
*   **Q4. Block cache** (GH-185): whether tier-3 reads need a disk cache beyond DuckDB's in-memory external file cache. Measurement track M decides; it gates no phase.
*   **Q5. Ingress resolution**: the ingress hashes on `bundle_id`, which an SDMX REST path does not contain. Phase 6 chooses between an ingress filter that reads a path-to-bundle map the router publishes, and routing every request to any pod, which forwards it to the owner. Both are tested against Phase 8's remap bound.
*   **Q6. Structure versioning at the FMR** (specification open question 5): StatCan edits legacy `1.0` artefacts in place. The fingerprints record each edit, and invariant 10 holds back every product cut from an edited full artefact until it is republished. Whether StatCan moves to semantic versions, which FMR freezes once final, is an FMR governance decision outside this program.

## Critical User Journeys (CUJs)

### CUJ 1: A daily release under embargo

*   **Given**: 200 tables have new publications, uploaded and recorded `staged`, and so do the structures publications they were cut from. The current index serves the previous publications.
*   **When**: the release step runs at 08:30.
*   **Then**: one ETag-conditional write of `catalog/current` makes all 200 routable. Requests in flight finish on the old registry. Structures publications serve before the products cut from them. Before the write, every pod answers 404 for a staged dataflow and none has attached a staged bundle. Within 90 s the CDN serves the new results.

### CUJ 2: A pod joins the hot pool

*   **Given**: an index whose `hot` pool pins 12 bundles; a new pod starts.
*   **When**: it reads `catalog/current` and the index.
*   **Then**: it pulls tier 1 of each pinned bundle, verifies each with `check_on_open`, attaches them into one `BundleEngine`, and only then reports ready. Tier-3 data files stay in object storage.

### CUJ 3: A request for an unpinned table

*   **Given**: a `base`-pool pod, and a data request for a dataflow whose bundle is not attached.
*   **When**: the ingress routes the request by consistent hash on `bundle_id`.
*   **Then**: the router resolves the dataflow through the index, pulls and verifies tier 1, attaches the bundle once however many requests arrive together, answers, and evicts the least-recently-used bundles when the attached `pull_bytes` exceed the pod's budget.

### CUJ 4: A cached result

*   **Given**: a result for (dataflow, query, format, language) in `results/` under its key, and a CDN copy whose TTL has expired.
*   **When**: the CDN revalidates with `If-None-Match`.
*   **Then**: the pod computes the key from the serving entry's fingerprints, the applied overlays, the hook ids and the render versions. If the key matches, it answers 304 without reading `results/`; otherwise it streams the new body with the new key as ETag.

### CUJ 5: A schema bump

*   **Given**: sdmxlib raises the store `schema_version`.
*   **When**: every serving bundle is republished at the new schema and the pods roll.
*   **Then**: `check_bundle(at="publish", expect=<previous>)` stops the job if any fingerprint moved. The index holds both schema versions; old pods serve old entries and new pods new ones; no request gets a non-2xx response.

### CUJ 6: A bundle built from an FMR and a DuckLake table

*   **Given**: a dataflow whose structures an FMR holds, with a data constraint and a hierarchy association that reference it; its observations in a DuckLake table the producer maintains.
*   **When**: the producer calls `sdmxlib.bundle.build_bundle` with a `RestRegistry` over the FMR's 3.0 API and that table.
*   **Then**: the bundle holds the dataflow's closure, the constraint, the hierarchy and the association included. Each codelist is cut to the codes the observations use and their ancestors, with `cut_from` recorded. `check_bundle` passes. After the constraint is edited in the FMR, the next publication's structural fingerprint for that dataflow differs and its other fingerprints are equal.

## Technical Plan (Architecture)

The specification's architecture (§3) stands. The plan fixes three placements the specification leaves open:

*   **sdmxlib owns the closure builder.** One implementation fetches a dataflow's closure with its incoming references, selects it with `References.closure` (`bundle/_fingerprint.py`), cuts partials with `Codelist.partial` and records `cut_from`. `fetch_bundle`, CUJ 6 and statcatlib's ingest all call it. `cut_from` is a manifest field, so the code that writes it lives beside `write_manifest`.
*   **The index format is a JSON Schema contract.** statcatlib builds the index and publishes `catalog-index.schema.json` with its format page, as sdmxlib publishes `manifest.schema.json`. sdmxlib-rest declares its own msgspec structs for the fields routing reads and tests them against the schema. That keeps sdmxlib-rest's recorded decision to take no statcatlib dependency (`pyproject.toml:25-27`), so no pod installs LanceDB.
*   **The publish, release and GC jobs are statcatlib CLI commands** (`statcat publish`, `statcat release`, `statcat gc`, `statcat index build`). Pre-materialisation is an sdmxlib-rest command (`sdmxlib-rest prematerialise`), because it must render with the pods' code.

| Phase | Repository | Milestone | Depends on | Releases |
|---|---|---|---|---|
| 0 Specification v5 | sdmxlib | — | — | merged (PR #222) |
| 1 Closure builder, `cut_from`, 0.76 | sdmxlib | M6 (sdmxlib part) | 0 | sdmxlib 0.76.0 |
| 2a Adopt sdmxlib 0.76 | statcatlib | M8 prerequisite | 1 | statcatlib minor |
| 2b Token-named search tables | statcatlib | M8 prerequisite | 2a | statcatlib minor |
| 3 Structures bundles | statcatlib | M6 | 1, 2a | statcatlib minor |
| 4 Catalog index, serving list, synthetic corpus | statcatlib | M8 | 3 | statcatlib minor, index format 1.0 |
| 5 Publish, release, GC | statcatlib | M9 | 4 | statcatlib minor |
| 6 Router, tiers, pools, results, pre-materialisation | sdmxlib-rest | M10 | 1, 4, 5 | sdmxlib-rest minor |
| 7 Catalog-level search | statcatlib, sdmxlib-rest | M13 | 4, 6 | outline |
| 8 Kubernetes | sdmxlib-rest | M11 | 6 | outline |
| 9 Drills | sdmxlib-rest | M12 | 8 | outline |
| M Tier-3 measurement (GH-185) | sdmxlib | — | — | a release only if it builds a cache |

Phase 5's release job is tested against the index sequence alone, with no pods: the staged index, the serving index and the ETag-conditional write. Phase 6 tests the same flip under load with pods. No phase waits on a later one.

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| An embargoed result is readable before 08:30 | No public access to `results/`; staged bundles are pulled and never attached in a serving pod; catalog-level search and `catalog/structures/` list only `serving` entries; pre-materialisation runs in its own job. Phase 6 tests each (CUJ 1). |
| The CDN serves stale bytes after a release, delta or rollback | Short TTL with ETag revalidation (specification §7.4); Phase 6 tests visibility within 90 s and the 304 path. |
| A same-URN edit at the FMR changes a full codelist under its products | Invariant 10 holds back every product cut from the old fingerprint until it is republished; Phase 4 tests the refusal. |
| GC deletes a data file that a publish still uploading wrote | GC skips files younger than the publish grace period (Phase 5); a test races an upload against GC. |
| A rollback points `current` at an index whose files GC deleted | GC keeps every file that a publication within index retention references, and rollback goes only to an index within retention. Phase 5 tests GC after a rollback. |
| statcatlib or sdmxlib-rest cannot adopt a breaking sdmxlib release in one step | Each phase pins one minor and lists the moves in `docs/upgrading.md`; Phase 2 is split so the pin moves before the search-table change. |
| Building the index from 7,000 manifests is slow | The builder reads every manifest; Phase 4 measures a 7,000-entry build, and a baked summary is added only if the measurement shows the cost (`docs/design/caching.md`). |

## Alternatives Considered

The specification records the design alternatives and their rejections (§7.4, §7.5, §7.6, §8, §12, §14). These are about how the remaining work is built.

### Alternative A: statcatlib owns the closure builder

*   **Pros**: the ingest that vendors closures is statcatlib's; sdmxlib ships only GH-221 and the `cut_from` field.
*   **Cons**: `fetch_bundle` and any producer outside statcatlib (cpt-builder, CUJ 6) would each rebuild the closure walk and the partial cut. The first revision of this plan had two such implementations, cutting partials by two rules.
*   **Reason for Rejection**: the field the cut records belongs to sdmxlib's manifest, and the walk already exists there (`References.closure`).

### Alternative B: sdmxlib-rest imports the index types from statcatlib

*   **Pros**: one definition of the index structs.
*   **Cons**: reverses sdmxlib-rest's recorded decision, and every serving pod installs statcatlib's dependencies, LanceDB included.
*   **Reason for Rejection**: a JSON Schema and a validation test give one definition of the format with no shared code.

### Alternative C: Plan all nine phases to implementation level now

*   **Pros**: one document covers the program.
*   **Cons**: Phases 7 to 9 depend on choices Phase 6 makes (the ingress, the pool sizes, the router's budget), so detail written now would be rewritten then. The first revision's Phase 7 to 9 items had no endpoints, values or file formats.
*   **Reason for Rejection**: the plan specifies what the next phases need and re-plans the rest after Phase 6.

--------------------------------------------------------------------------------

## Non-Functional Requirements

*   **Security and privacy**: the embargo is the critical property (Risks, first row). `results/` and `bundles/` are not publicly readable (specification §10.1); `BundleRegistry` has no `sql()`.
*   **Performance**: Goal 1's targets, on the reference node. Every cache this program adds follows `docs/design/caching.md`: measured first, single-flight, bounded by bytes, invalidated by a lifecycle, and guarded by a test that counts the expensive calls.
*   **Rollout and rollback**: sdmxlib, statcatlib, DuckDB and DuckLake are pinned in one image for the publish job and the pods (specification §9.2). Rollback by change class:

    | change | rollback |
    |---|---|
    | data or structures publication | point `catalog/current` at an earlier index token within retention; the CDN follows within 90 s |
    | search tables | per-bundle tables are named by publication token and catalog-level ones by index token, so the earlier index's tables are present within retention |
    | library release | redeploy the previous image; a store schema change follows the two-version rollout of CUJ 5 |

*   **Idempotency**:
    *   `statcat publish`, re-run with inputs whose content fingerprint equals a `staged` or `serving` publication of the same bundle, returns that publication and mints no token.
    *   `statcat release`, re-run with tokens already `serving`, writes nothing.
    *   `statcat gc` deletes only what is unreferenced, so a re-run deletes nothing.
    *   `statcat index build` with an unchanged serving list writes an index equal to the previous one apart from its token and `created_at`.

## Additional Resources

*   [`sharded-bundle-catalog.md`](sharded-bundle-catalog.md): the specification this plan builds.
*   [`../bundle.md`](../bundle.md): the bundle format, manifest 2.0.
*   [`caching.md`](caching.md): the rules every cache in this program follows.
*   [GH-136](https://github.com/StatCat-Suite/sdmxlib/issues/136): the cross-repo tracking issue.
*   [GH-221](https://github.com/StatCat-Suite/sdmxlib/issues/221): the incoming-reference closure, with the FMR measurement.
*   [statcatlib's bundle format](https://statcat-suite.github.io/statcatlib/bundle-format/): the statcat section.

## Future Improvements

*   **Shared structure caches across bundles** (GH-160): re-measured once a deployment holds many distinct bundles; GH-133 measured 2.2 MB per registry, too small to share at 16 bundles.
*   **Signed manifests**: `bundle.manifest.json.sig` is reserved (specification §4.3).
*   **A baked index-builder summary**, if Phase 4's measurement shows that reading every manifest is slow.

--------------------------------------------------------------------------------

## Detailed Implementation Plan

Each phase lists its repository, issues, files and tests, and ends with that repository's `just ci` green and, where a later phase pins it, a release. statcatlib and sdmxlib-rest are changed by their maintainers. From sdmxlib, each of their phases is an issue on their tracker that links this plan; line anchors into those repositories go in those issues.

### Target Files to Create/Modify

#### Phase 1: sdmxlib closure builder, `cut_from` and the 0.76 release

Issues: [GH-221](https://github.com/StatCat-Suite/sdmxlib/issues/221) (reference direction), [GH-223](https://github.com/StatCat-Suite/sdmxlib/issues/223) (`build_bundle`), [GH-224](https://github.com/StatCat-Suite/sdmxlib/issues/224) (`cut_from`, manifest 2.1).

1.  **`src/sdmxlib/api/session.py`, `src/sdmxlib/api/registry.py`** (Modify; GH-221)
    *   `resolve` becomes `bool | Literal["all", "parents"] | Sequence[type]`. `_references_for` maps `"all"` to `references=all` and `"parents"` to `references=parents`; `True` stays `descendants`.
    *   The session records direction as well as completeness. `Completeness` gains `CLOSED` above `FULL`, set by an `all` fetch with `detail=full`. The session also records, per URN, whether its parents were fetched. `_already_held` answers a `resolve="all"` request from the session only at `CLOSED`, and a `resolve="parents"` request only when parents were fetched, so an earlier `descendants` fetch satisfies neither.
    *   Artefacts a `parents` fetch brings in, such as sibling dataflows of a shared DSD, are stored like any other. A bundle's closure leaves them out because `References.closure` starts from the requested dataflow.
    *   The docstring states that over SDMX 2.1 no hierarchy association arrives.
2.  **`src/sdmxlib/bundle/_build.py`** (Create), **`src/sdmxlib/bundle/__init__.py`** (Modify; GH-223)
    *   The signature:

        ```python
        def build_bundle(
            source: RestRegistry | BundleRegistry,
            dataflows: Sequence[SdmxUrn[Dataflow]],
            path: str | Path,
            *,
            observations: Mapping[str, WriteSource] | None = None,
            partials: bool = True,
            producer: Producer,
            provenance: Provenance | None = None,
            lifecycle: Lifecycle = "live",
        ) -> Manifest
        ```

    *   **Fetch.** For a `RestRegistry`, it fetches each dataflow with `resolve="all"` and its DSD with `resolve="parents"` into a staging `LocalRegistry`. For a `BundleRegistry` (a published product or structures bundle), it reads the store directly, since the store's `artefact_ref` already holds the incoming edges.
    *   **Select.** It takes the union of each dataflow's `References.closure` and adds those artefacts to `LocalRegistry.build(path)`.
    *   **Observations.** `observations` maps a dataflow URN to anything `LocalRegistry.write_observations` reads: a Polars frame, an Arrow reader, or a DuckDB relation over a DuckLake table. Each is written to its own table and bound with `DataflowBinding.from_dsd`. `None` builds a structures-only bundle.
    *   **Partials.** With `partials=True`, each enumerated codelist is cut before `write_manifest`. It keeps the codes the dataflow's observations use in that dimension (a `SELECT DISTINCT` over the written table) together with the codes its allowed content constraints name, and `Codelist.partial` (`model/codelist.py:668`) adds their ancestors. The cut records `cut_from`, the artefact fingerprint of the full codelist. A dataflow with no observations keeps full codelists.
    *   **Publish.** It calls `write_manifest(..., cut_from=...)` and then `check_bundle(at="publish")`.
    *   **`fetch_bundle`.** `fetch_bundle` (`bundle/_fetch.py`) becomes `build_bundle` with `observations` from the source's narrowed `obs()` stream and `partials=False`. Its manifests keep their current artefacts and gain the incoming ones.
    *   **Errors.**
        *   `TypeError` when a URN is not a dataflow at the source.
        *   `ValueError` naming the dataflow when its DSD is absent.
        *   `ValueError` naming the dimension and the codes when `observations` holds a coded value outside the full codelist.
3.  **`src/sdmxlib/bundle/_write.py`, `src/sdmxlib/bundle/_check.py`, `src/sdmxlib/bundle/manifest.schema.json`, `docs/bundle.md`** (Modify; GH-224)
    *   `write_manifest` takes `cut_from: Mapping[str, str] | None` (partial URN to full fingerprint) and writes `ArtefactEntry.cut_from`. The manifest's `format_version` becomes `"2.1"`. A 2.0 reader reads it and ignores the field, by `docs/bundle.md`'s minor-version rule.
    *   `check_bundle(at="publish", structures=<publication path> | None)`: when `structures` is given, each partial with `cut_from` needs a full artefact at its URN in that publication whose fingerprint equals `cut_from`. The partial's code ids and parents must also be a subset of that artefact's. Each mismatch is a finding naming the URN and the first missing code.
    *   `docs/bundle.md` documents `cut_from` and the `structures` check.
4.  **Tests**
    *   `tests/integration/test_bundle_fetch_live.py` (Modify): three cassettes recorded from the local FMR (Colima, `sdmxio/fmr-mysql:12.4.2` on `localhost:8080`). The probe artefacts `ECB:CC_EXR_PROBE`, `ECB:H_CURRENCY_PROBE` and `ECB:HA_EXR_CURRENCY_PROBE` load from `tests/integration/fmr/12.4.2/incoming.xml`.
        *   The 3.0 fetch holds all three.
        *   With the constraint edited, `EXR`'s structural fingerprint differs and every other artefact's fingerprint is equal.
        *   The 2.1 fetch holds the constraint and the hierarchy, and no association.

        The module docstring records the steps that load the probes and record the cassettes.
    *   `tests/unit/test_api_session.py` (Modify): with a counting transport, a `descendants` fetch followed by `resolve="all"` on the same URN sends two requests, and a second `resolve="all"` sends none.
    *   `tests/unit/test_bundle_build.py` (Create):
        *   A `BundleRegistry` source with a constraint stored beside its dataflow gives a closure that holds the constraint.
        *   Observations using three codes of a 20-code hierarchical codelist give a partial holding those codes and their ancestors, with `cut_from` equal to the full codelist's fingerprint.
        *   A coded value outside the codelist is refused with the dimension named.
    *   `tests/unit/test_bundle_check.py` (Modify): a partial with a code the full artefact lacks is a finding naming that code, and a `cut_from` that matches no artefact in `structures` is a finding naming the URN.
5.  **Release 0.76.0**: `just release-1-preview` through `just release-5-pypi`. The `docs/upgrading.md` section covers store schema 2.51, GH-213's observation order, `resolve`'s new values, `fetch_bundle`'s larger closure and manifest 2.1.

#### Phase 2a: statcatlib adopts sdmxlib 0.76 (StatCat-Suite/statcatlib#46, StatCat-Suite/statcatlib#47)

1.  **`pyproject.toml`**: `sdmxlib>=0.76,<0.77`.
2.  **`src/statcatlib/bundle.py`**
    *   `open_bundle(path) -> BundleRegistry` opens a publication read-only, through `LocalRegistry.bundle(path)`.
    *   `open_build(path, *, previous) -> LocalRegistry` opens a writable one, through `LocalRegistry.build(path, previous=...)`.
    *   `DataPolicy`, `Auto()`, `normalize_lake_data_path`, `_write_data_path` and the `access_mode="read_write"` default are removed, since manifest 2.0's relative `data_path` replaces them. Under the suite's pre-1.0 rule they are not kept as deprecated paths.
    *   `check_bundle` passes `at=` in place of `references=`: `at="publish"` from `publish_bundle` and `at="receive"` from the open path.
3.  **`src/statcatlib/store/store.py`**: `from_connection` takes the registry `open_bundle` or `open_build` returns.
4.  **`src/statcatlib/cli/__init__.py`**: `bundle rebind`, and every other command that sets a data policy, is removed.
5.  **Tests**
    *   Two processes open one published bundle, and both answer.
    *   Opening a published bundle leaves the directory's file set and mtimes unchanged.
    *   `publish_bundle`'s output passes `check_bundle(at="publish")`.
6.  **`docs/upgrading.md`** (Create): the removed data policy and `rebind`, and the split into `open_bundle` and `open_build`.

#### Phase 2b: statcatlib token-named search tables (StatCat-Suite/statcatlib#48)

1.  **`src/statcatlib/store/lance.py`**
    *   Search tables are named `f"{_TABLE_PREFIX}{lang}__{token}"` with the publication token, in place of the integer generation, and the search manifest records `token`.
    *   `_MANIFEST_VERSION` becomes 3. The version guard accepts 1 to 3, so a version-2 manifest still opens, and it refuses a higher version with its existing message.
    *   `build_index` drops no tables, because a draining publication's pods may still read them. `statcat gc` deletes the tables of retired publications past retention (Phase 5).
2.  **`src/statcatlib/bundle.py`**: the statcat section records `search.annotation_types`, the annotation types whose overlays change search (`SEARCH_ANNOTATION_TYPES`). A server that applies a serve-time overlay can then refuse one acting on those types without importing statcatlib (Phase 6).
3.  **Tests**
    *   Two publications of one bundle hold two token-named table sets, and `build_index` on the second leaves the first's in place.
    *   A version-4 search manifest is refused, naming version 3.
    *   The section's `annotation_types` equals `SEARCH_ANNOTATION_TYPES`.
4.  **`docs/upgrading.md`**: generation-named tables become token-named.

#### Phase 3: statcatlib structures bundles (StatCat-Suite/statcatlib#51; M6)

1.  **`src/statcatlib/structures.py`** (Create)
    *   `structures_bundle(source, agency, path, *, families: Collection[Literal["codelist", "conceptscheme", "categoryscheme", "hierarchy"]]) -> Manifest` calls `sdmxlib.bundle.build_bundle` with `partials=False` and no observations. It covers every full artefact of `agency` in those families at `source`, and writes the statcat section with `kind: "structures"`.
    *   It raises `ValueError` naming the URN when the source holds an artefact of those families only as a partial.
2.  **`src/statcatlib/bundle.py`**
    *   The statcat section gains `kind: "product" | "structures"`, and `STATCAT_SECTION_FORMAT` becomes 3.
    *   A reader refuses format 2 with a message naming the republish, under the pre-1.0 rule.
    *   `check_catalog` asserts that a structures bundle has no bindings.
3.  **`src/statcatlib/sources/sdmx.py`**: ingest builds each product through `build_bundle(..., partials=True)` and passes the structures publication to `check_bundle(at="publish", structures=...)`.
4.  **`tests/support/codr.py`** (Create): a generator of one CODR-shaped table. It makes a DSD with a 5-level geography codelist of 6,000 codes, 3 classification codelists and 4 dimensions, and a 2,000-row observation frame over a random subset of the codes. Phase 4 scales it to 7,000 tables with one seed per table.
5.  **`tests/test_structures.py`** (Create)
    *   A generated product's `meta.duckdb` is under 10 MB.
    *   Every partial in it is a subset of the structures bundle's artefact with the fingerprint it records.
    *   A structures bundle round-trips its full artefacts.
    *   A structures bundle with a binding fails `check_catalog`.

#### Phase 4: catalog index, serving list and synthetic corpus (StatCat-Suite/statcatlib#44, StatCat-Suite/statcatlib#45; M8). Contracts and gating tests

*   **Serving list**: a `serving_entry` table in statcatlib's store, with `ServingEntry` as its msgspec struct and the fields of specification §6.6, `(bundle_id, token, manifest_sha256, manifest_location, lifecycle, pool, pinned, state, kind)`. `statcat publish` writes a row and `statcat release` changes its state. `statcat index build --serving <store> --out <blob root>` reads the table.
*   **Builder**: `build_index(serving: Sequence[ServingEntry], *, fetch: Callable[[str], bytes], concurrency: int = 32, previous: CatalogIndex | None) -> tuple[CatalogIndex, StructuresIndex]`.
    *   It fetches and parses every manifest.
    *   It runs one check per invariant of specification §6.4. Each raises `IndexInvariantError` naming the invariant's number and the entries.
    *   It writes `catalog/index/<token>.json` and `catalog/structures/<token>.json` per §6.2.
    *   `IndexEntry.dataflows` carries both `fingerprint` and `structure_fingerprint`.
*   **Contract**: `docs/catalog-index.md` and `catalog-index.schema.json` for index format 1.0, published with statcatlib's docs and released before Phase 6 pins them.
*   **Synthetic corpus**: `statcat dev synthetic-catalog --tables 7000 --seed <n>` writes 7,000 CODR-shaped bundles from `tests/support/codr.py`, and one structures bundle per family.
*   **Gating tests**
    *   Each of the ten invariants is refused by number, given a minimal failing list. One case is invariant 10 refusing a product whose `cut_from` matches no serving structures entry.
    *   A 7,000-entry build records its wall time, its fetch count (7,000) and the bytes fetched. The measurement goes into specification §6.6 and decides whether the baked summary is built.
    *   `latest`, `~` (the SDMX REST 3.0 wildcard for the latest stable version) and unversioned requests resolve through `versions`, then `urns`.
    *   Neither document lists a `staged` entry's artefacts.
    *   The schema validates every index the tests build.

#### Phase 5: publish, release and GC (StatCat-Suite/statcatlib#52; M9). Contracts and gating tests

*   **Blob access**: one `ObjectStore` protocol in statcatlib (`get`, `get_range`, `put`, `put_if_match`, `list`, `delete`, `rename`), with a local-directory implementation for tests.
    *   Uploads verify with sdmxlib's store-neutral digests (`bundle/_remote.py`).
    *   Copies use `copy_bundle` and `bundle_files`.
    *   sdmxlib-rest reads through its own `Fetcher` (Phase 6), since a pod only reads.
*   **`publish`** runs specification §11.1 steps 1 to 7.
    *   It uploads under `.staging-<token>/`. On a store with atomic rename (ADLS Gen2 with hierarchical namespace) it then renames; elsewhere it writes the manifest last.
    *   It records a `staged` `ServingEntry`.
    *   It is idempotent as the NFRs state.
*   **`release`** runs §11.2. It builds the staged index and flips `current` to it. It then runs `sdmxlib-rest prematerialise` as a job (Phase 6), builds the serving index, and flips with one `put_if_match`.
*   **`gc`** runs §11.4 over the index chain within index retention.
    *   It skips data files younger than the publish grace period (default 24 h, longer than the longest publish).
    *   It deletes the search tables of retired publications past retention.
*   **Gating tests**
    *   A 200-table synthetic release writes `current` twice, first to the staged index and then to the serving one.
    *   An interruption after the first write leaves the staged index current, and that index routes as the one before it did.
    *   GC deletes exactly the unreferenced data files. It keeps a file uploaded inside the grace period by a publish racing it, and keeps everything an index within retention references after `current` is pointed back.
    *   A re-run of each job changes nothing.

#### Phase 6: sdmxlib-rest router, tiers, pools, results and pre-materialisation (StatCat-Suite/sdmxlib-rest#69, #48, #46, #47, #10, #52; M10). Contracts and gating tests

*   **Pin and index contract**: `sdmxlib>=0.76,<0.77`. Index structs for the fields routing reads, tested against statcatlib's `catalog-index.schema.json`.
*   **Partial references**: `detail=referencepartial` returns the bundle's partials, which `build_bundle` cut, in place of the `referencestubs` approximation sdmxlib-rest returns today.
*   **Pull**: a `Fetcher` protocol (`get`, `get_range`). Tier 1 is pulled through `bundle_files(manifest, tiers=(1,))` and checked with `check_on_open`; tier 3 is opened through `LocalRegistry.bundle(path, remote=..., engine=...)`.
*   **Router**: `ShardRouter` over one `sl.BundleEngine`, as specification §7.3 declares.
    *   Attaches are single-flight per `bundle_id`.
    *   Eviction is LRU, bounded by the attached `pull_bytes` against a per-pod budget, with `DETACH` on eviction.
    *   The router polls `catalog/current` every 5 s, and in-flight requests finish on the registry they started on.
    *   It refuses a manifest whose sha256 differs from the index entry's.
    *   `/ready` answers ready once every pinned bundle is attached and the index token is current; `/live` is the liveness probe.
*   **Serve-time overlay refusal**: an overlay entry acting on a type in the statcat section's `search.annotation_types` is refused with a message naming the entries.
*   **Result store and freshness**
    *   The key is specification §7.4's: the binding's two fingerprints, the existing `overlay_digest`s, the hook ids, `sdmxlib.formats.render.RENDER_VERSION`, and sdmxlib-rest's own `RENDER_VERSION`. sdmxlib-rest keeps its version in `sdmxlib_rest/render.py` and bumps it by the golden-file rule.
    *   That key replaces the ETag sdmxlib-rest's middleware computes today.
    *   Public responses carry `max-age=60, stale-while-revalidate=30`, and an `If-None-Match` that matches gets a 304 without a read of `results/`.
*   **Pre-materialisation**: `sdmxlib-rest prematerialise --index <staged token> --set <release set file>` opens each staged publication from its own files, in a job process, and writes `results/`.
*   **Ingress resolution**: Q5 is decided here, with a test of each candidate against the remap bound.
*   **Load driver**: `bench/load.py` replays a Zipf-distributed request mix over the synthetic corpus.
*   **Gating tests**
    *   A flip under the load driver fails no request.
    *   A dataflow routed only to another schema answers 503 naming both schema versions.
    *   A staged entry is pulled and never attached.
    *   A staged dataflow answers 404 before the flip and 200 after it, at the origin.
    *   A delta to one dataflow changes its key, and the other dataflows keep answering 304.
    *   Readiness stays false until every pinned bundle is attached.
    *   Many concurrent first requests for one bundle attach it once, by a counted attach.
    *   Goal 1's cold-start and first-attach targets run as bench tests.

#### Phase 7: catalog-level search (StatCat-Suite/statcatlib#49, StatCat-Suite/sdmxlib-rest#53; M13). Outline

statcatlib builds per-language LanceDB tables from the `serving` entries' `statcat.dataset` rows and the structures bundles' full codelists, under `catalog/search/<index token>/`. The location follows from the index token, so the index records only the languages. sdmxlib-rest's search pool serves the tables. Endpoints, parameters and readiness are planned after Phase 6. The gating property: a holdings-wide query answers from one table and never returns a staged entry.

#### Phase 8: Kubernetes (StatCat-Suite/sdmxlib-rest#50; M11). Outline

Deployments for `hot`, `base` and `search`; KEDA scaling; probes and `preStop` per specification §7.5; PodDisruptionBudgets; topology spread; identities per §10.1; and the ingress Phase 6 chose. Values are set from Phase 6's measurements. The gating test runs on a local kind (Kubernetes-in-Docker) cluster: scaling `hot` from 4 to 20 pods remaps at most 1/N of keys per added pod, where N is the pool's size after the addition, with zero failed requests.

#### Phase 9: drills (StatCat-Suite/sdmxlib-rest#51; M12). Outline

The §9.2 schema bump with mixed pods and zero non-2xx responses; restore by pointing `current` back; restore of a deleted token directory from the storage account's replica. Each restore ends with `check_bundle(at="receive")` on every entry.

#### Measurement track M: tier-3 reads (GH-185)

1.  **`tests/stores/test_tier3_reads.py`** (Create), marked `stores` so that `just test-stores` runs it and `just ci` does not.
    *   It reads the census publication's dataflow with the fewest observations from the Azurite container, through `LocalRegistry.bundle(path, remote=...)`.
    *   It runs with DuckDB's external file cache off, on, and on with spill. For a first and a repeated `obs()`, and again after a process restart, it records GET count, bytes, wall time, and retained and peak memory.
    *   It writes the results to `bench/baselines/tier3.json`.
2.  **`src/sdmxlib/local/engine.py`** (Modify) only if, with the cache on, a repeated read in the same process issues more than 10% of the first read's GETs.
    *   `BundleEngine(cache: Literal["memory", "spill"] | None = None)`: `"memory"` sets `enable_external_file_cache`, and `"spill"` also sets `external_file_cache_spill`.
    *   A unit test counts the GETs of a repeated read.
3.  **Specification §7.1**: the measurement and its decision.

### Verification Plan

*   **Build**: each repository's `uv sync` at the pinned versions; `just docs-build` (`mkdocs build --strict`) where docs change.
*   **Gate**: `just ci` in each repository a phase changes, run locally, read to its exit line before each commit.
*   **Static analysis**: `just check` at zero findings (ruff at `select = ["ALL"]`, pyrefly, the docstring and prose guards, the `Any` ratchet).
*   **Test coverage**: every invariant, check and refusal a phase adds has a test that fails without it; a guard that starts at zero findings gets a sabotage test.
*   **Integration**:
    *   sdmxlib's `just test-integration` replays Phase 1's FMR cassettes with no network.
    *   `just bench-bundle-assertions` runs on the census publication after Phase 1.
    *   The synthetic corpus from Phase 4 serves Phases 5 to 9.
    *   The kind cluster carries Phases 8 and 9.

--------------------------------------------------------------------------------

<!-- Provenance: Indicates initial structural drafting via EGM. -->

*Drafted with EGM*
