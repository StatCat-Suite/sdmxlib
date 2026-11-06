# Sharded catalog of products and editions: implementation plan

*   **Author:** Gabriel Gellner
*   **Status:** Draft, third revision: Phases 4 to 9 re-planned against the specification's sixth revision (GH-333), after the EGM review in [`egm/sharded-catalog-replan/`](egm/sharded-catalog-replan/README.md)
*   **Date:** 2026-11-02
*   **Read against:** sdmxlib `9b4e933b` (0.82.0), statcatlib `27d022b` (0.12.0), sdmxlib-rest `1ce79f1` (0.9.0), the hub at `2fce9f2`
*   **Tracker:** [StatCat-Suite/statcat-suite.github.io#19](https://github.com/StatCat-Suite/statcat-suite.github.io/issues/19) (Phases 4 to 9)

--------------------------------------------------------------------------------

## Overview

StatCat-Suite publishes SDMX data as **products**. A product is one directory holding a sequence of sealed **editions** over a shared `data/` directory. Each edition is an immutable directory named by its `edition_id`, with the structures and catalog records (`metadata.duckdb`), the observations (`tables.ducklake` over the shared Parquet files) and a `manifest.json` that lists every file and fingerprint. The **Inventory**, a chain of immutable versions, records which edition of each product is live.

Today a laptop catalog holds products on local disk, and one sdmxlib-rest server serves one edition. The target is a catalog the size of Statistics Canada's CODR (Canadian Open Data Repository, the store behind its data tables) served through the .Stat Suite. That catalog holds about 7,000 tables. Tens to a few hundred of them are released each weekday at 08:30 and are under embargo until then. Kubernetes pods serve it, and each pod holds only the editions its routing slice needs.

[`sharded-bundle-catalog.md`](sharded-bundle-catalog.md) (the specification, sixth revision) defines the formats and behaviour: manifest 3.0, fingerprints, the Inventory, routing, pools, cache keys, release and garbage collection. This plan orders the remaining work into phases so that every repository stays releasable at the end of each phase. It states the contracts and gating tests of Phases 3 to 6 and outlines Phases 7 to 9, which are re-planned once Phase 6 is merged. Each phase's issue is restated against this plan before the phase starts, and the repository's own design settles what this plan leaves to it.

Built so far:

*   **Phase 1** (sdmxlib 0.76.0): the closure builder, partials and their `partial_of` fingerprint.
*   **Phase 2a** (shipped 2026-11-02 as sdmxlib 0.81.0, sdmxlib-rest 0.9.0 and statcatlib 0.12.0), as the statcatlib RFC defines it. sdmxlib 0.80.0 renamed the format's units to product, edition and `edition_id` (manifest 3.0) and added the Inventory's schemas, rules and reader. statcatlib writes the Inventory on local disk (`Catalog`, `WorkingCopy`, `CatalogAdmin`). sdmxlib-rest serves one edition and reads manifest 3.0.
*   **sdmxlib 0.82.0** (main `9b4e933b`), the Phase 2a follow-ups: `sdmxlib.storage.create_exclusive`, `LocalRegistry.edition(manifest_sha256=)`, and readers that accept an unknown `event.kind` within Inventory format 1.x.
*   **Phase 3** (shipped 2026-11-03 as sdmxlib 0.83.0 and 0.84.0, statcatlib 0.13.0 and sdmxlib-rest 0.9.2): structure products split by change rate, partial codelists in data products checked against them, and the writer's cross-product rules (specification §6.5 items 4, 7 and 8).

Phases 4 to 9 are open.

## The Problem

A serving pod opens one edition. At catalog scale that fails in four ways (specification §2.2): every pod's cold start pulls the whole catalog; a republish of one table redistributes everything; one backup unit spans thousands of release cadences; and one DuckDB storage version binds every product. The edition format and the Inventory already supply the shard and its record: immutable edition directories, shared `data/`, fingerprints, `check_edition` profiles, a shared `EditionEngine`, and a local Inventory writer. Five pieces remain to build:

1.  structure products, which hold the full artefacts every data product's partials are cut from;
2.  the catalog on blob storage: the Inventory writer over object storage, upload and the `pending/` inbox;
3.  a release job that makes many embargoed editions live in one Inventory version, and a GC job that deletes what no recorded edition references;
4.  a router that sends each request to the pod holding the product's live edition, with results cached by fingerprint;
5.  catalog-level search, the Kubernetes deployment and the rollout and restore drills.

## Terms

The specification defines every term (§1, §4 to §11); these are the ones this plan leans on.

*   **Product**: the unit of release, routing and lifecycle, one directory `products/<product_id>/` (specification §4.0).
*   **Edition**: one sealed, immutable directory `products/<product_id>/<edition_id>/`. A republish seals a new edition of the same product.
*   **Inventory version**: one root `inventory/<seq>.json` naming one leaf per product (`inventory/leaves/<sha256>.json`). Each leaf entry gives an edition one of four states: `embargoed`, `live`, `superseded` or `withdrawn`. `CURRENT` names the newest version as a hint (specification §6).
*   **Live view**: sdmxlib's `LiveInventory`, which holds only `live` entries at one `schema_version`. The public catalog and the pods read it. `FullInventory` holds every state; only statcatlib's `CatalogAdmin` and `EmbargoReader` and the release job construct it (specification §6.4).
*   **Tier**: where a file lives when served. `required` (manifest, `metadata.duckdb`, `tables.ducklake`) is pulled before a pod is ready; `lazy` (`search/`) is pulled on first use; `remote` (Parquet data files) is read from object storage by byte range.
*   **Pinned**: a product a pool attaches before its pods report ready.
*   **Partial**: a codelist vendored with only the codes a product uses and their ancestors (`isPartial="true"`). Its manifest entry records `partial_of`, the fingerprint of the full codelist it was cut from (specification §4.2).
*   **Structure product**: a product of `kind: structure` holding full codelists, concept schemes, category schemes and hierarchies, with no observations (specification §8).
*   **Release**: the Inventory version that moves due `embargoed` entries to `live` and the editions they replace to `superseded` (specification §11.2 step 4).
*   **Pre-materialisation**: computing a release's most-requested results into the result store before 08:30, in a job that runs the pods' image (specification §11.2 step 3).
*   **FMR**: the Fusion Metadata Registry, the SDMX structure registry StatCan's producers edit. Version 12.4.2 serves SDMX REST 2.1 and 3.0.

## Goals

1.  A catalog of 7,000 synthetic CODR-shaped tables, requested with a Zipf-distributed load, is recorded in the Inventory, routed and served by pods that each hold only their routing slice. On the reference node (4 vCPU, 16 GB, object storage in the same region), a cold pod with one pinned product is ready in under 60 s, and the p99 first attach of an unpinned product is under 3 s. Phase 6's bench tests measure both.
2.  A release of 200 tables is visible at the origin in the Inventory version that releases them, and at the CDN within 90 s of it (specification §7.4), with zero failed requests, and no embargoed result is readable anywhere before it. Phase 5 tests the release version without pods; Phase 6 tests it under load.
3.  A delta to one dataflow changes that dataflow's result keys only; every other dataflow's CDN revalidation answers 304 (Phase 6).
4.  Garbage collection deletes exactly the data files that are listed by no edition recorded, or tombstoned, within the retention window, and no file younger than the upload grace period. It refuses to start unless backup retention ≤ the `data/` GC window ≤ the tombstone window (specification §11.6). Phase 5 tests both bounds and the refusal.
5.  A store schema bump rolls through the fleet with two schema versions live at once and zero non-2xx responses (Phase 9).
6.  A producer builds a data product from an FMR's structures and observations held in a DuckLake table, and the edition holds the dataflow's incoming constraints and hierarchy associations (Phase 1, CUJ 6; done).
7.  Each repository's `just ci` is green at the end of every phase, and each phase's producer is released before a consumer's phase pins it.

## Non-Goals

*   Changing the edition or Inventory formats beyond the specification. A format change is a PR to `sharded-bundle-catalog.md` first, as the sixth revision was (GH-249). Where this plan needs one, it says so.
*   A Postgres DuckLake catalog on the serving path (specification §14). Spike M1(c), StatCat-Suite/statcatlib#53, is optional and blocks nothing.
*   The `labels.duckdb` split, shelved after GH-139 measured labels at 31 MB of the census `metadata.duckdb`.
*   Cross-product queries in sdmxlib-rest. A service that composes products calls sdmxlib-rest once per product (specification §7.6).
*   Detached manifest signatures (specification open question 3).
*   Producing the 7,000 real CODR products. That is the producers' work; this program builds and tests on synthetic ones.
*   Choosing the workflow engine that runs the upload, release, pre-materialisation and GC jobs. The jobs are CLI commands any orchestrator can call.
*   Migrating a .Stat Suite installation. It needs its own design, StatCat-Suite/statcatlib#99, and can start only after pods read the Inventory (Phase 6).

## Open Questions

Each question names the phase that must answer it. A decision is recorded in a PR to the specification or to this plan. The specification's open question 6, which GH-333 calls Q6, is this plan's Q1; the specification's question 5 is Q6 here, and its question 8 is Q2. Q1, Q2, Q4, Q8, Q9 and Q10 are settled and kept for their record.

*   **Q1. Product granularity** (specification open question 6): decided by the owner on 2026-11-03 ([statcat-suite.github.io#19](https://github.com/StatCat-Suite/statcat-suite.github.io/issues/19#issuecomment-6617482207)). Option C: a catalog has one product per table by default, and a producer may group tables that always release together into one product. The section [Product granularity](#product-granularity-q1) below records the options weighed and the corpus shape.
*   **Q2. The structure-product split** (specification open question 8): decided by the owner on 2026-11-02. StatCan splits its structure products by change rate, as specification §8's example does: `structure-geo` (the full geography), `structure-classifications` (NAICS, NOC, SGC) and `structure-concepts` (concept schemes). Invariant 7 (at most one home per full artefact) is unchanged by the split.
*   **Q3. Code search scope** (specification open question 4): whether geography-first agent queries need catalog-level code search tables that cover partials. Decide when Phase 7 starts.
*   **Q4. Block cache** (GH-185): settled. DuckDB's in-memory external file cache serves a repeated read in one process with no request, so no disk cache is built (specification §7.1).
*   **Q5. Ingress resolution.** The ingress hashes on `product_id`, which an SDMX REST path does not contain. The ingress maps the path's dataflow, or a structure request's artefact, to its product through the live Inventory (specification §7.5). Phase 6 chooses where that map runs: an ingress filter reading a map the router publishes, or a resolver in front of the ingress. Forwarding from any pod to the owner is a leaf-to-leaf call, which specification §7.6 rules out.
*   **Q6. Structure versioning at the FMR** (specification open question 5): StatCan edits legacy `1.0` artefacts in place. The fingerprints record each edit, and invariant 8 holds back every product cut from an edited full artefact until it is republished; its live edition stays live and stale meanwhile (specification §6.5 item 4). Whether StatCan moves to semantic versions, which FMR freezes once final, is an FMR governance decision outside this program.
*   **Q7. The routing fields** (specification §6.3): decided by the owner on 2026-11-02 and 2026-11-04 (GH-348). Each `LeafEntry` carries `dataflows` and `pull_bytes`, keyed on their presence and numbered Inventory 1.1. A router finds a `/structure` request's home from a structure entry's `artefacts`, which Phase 4 fills for structure products. `pool` and `pinned` wait for Phase 6, and `location` stays in `Shard` only.
*   **Q8. The serving package**: decided by the owner on 2026-11-03. The serving code moves to a new package, statcat-serve (repository StatCat-Suite/statcat-serve, import `statcat_serve`), which depends on sdmxlib, statcatlib and sdmxlib-rest. It holds the router, tier pulls, pools, the result store and its GC; pre-materialisation, which uses statcatlib's `EmbargoReader` directly; Q9's preview wiring; and `deploy/`, with reference Kubernetes manifests and the drills that operators adapt. Phases 6, 8 and 9 land there. sdmxlib-rest stays the generic SDMX-REST surface and adds the four seams Phase 6 lists. The repository is created when Phase 6 starts.
*   **Q9. The `EmbargoReader` preview** (statcatlib RFC §12, specification §10.1): decided by the owner on 2026-11-03. The operator runs the authenticated preview. The suite ships `statcatlib.EmbargoReader` in Phase 5. statcat-serve (Q8) holds the wiring that serves it through sdmxlib-rest, since sdmxlib-rest never imports statcatlib. The operator deploys that wiring as a separate service, with its own workload identity (the §10.1 preview principal) and its own authentication at the ingress, isolated from the public pods. The suite builds no authentication layer. Phase 9's drills check that the preview identity is on the §10.1 allowlist.
*   **Q10. The seal throughput target** (specification §9.2): decided by the owner on 2026-11-03. A full republish of every live product takes at most 8 h on 4 workers. At the measured 1.6 s per CODR seal, a full republish of 7,000 tables on 4 workers takes about 5 h. Phase 9's schema-bump drill fails above 8 h. Phase 5 adds no index for the no-home comparison (owner, 2026-11-04). It fills the optional `LeafEntry.artefacts` field, and the cross-product rules read artefact records from leaves instead of every live manifest. The Phase 4 bench (StatCat-Suite/statcatlib#126) measured cold rules at 74 ms plus 117 µs per live product, with about 6,300 manifest reads per seal at 7,000 products.

### Product granularity (Q1)

Specification §4.0 sets the rule (a product groups dataflows released together with one lifecycle) and the CODR default of one product per table. Three groupings meet the rule.

The owner chose option C on 2026-11-03: one product per table by default, and a producer may group tables that always release together into one product. The synthetic corpus for Phases 6 to 9 has about 7,000 tables: about 6,500 one-table products and about 50 products of 5 to 20 tables each. StatCat-Suite/statcatlib#124 builds the corpus generator and the no-home measurement, which uses one-table products up to 7,000. The table below keeps the options weighed:

| option | products in the synthetic corpus | effect |
|---|---|---|
| A. one product per table | 7,000 | every table releases, rolls back and attaches alone; the most leaves and pinned units |
| B. one product per subject, where its tables always release together | fewer, each with several bindings | shared partials vendored once; a member's republish rebuilds and re-pulls the whole product |
| C. A by default, B where a producer asks | a mix | both shapes in one catalog |

What each phase needs from the answer:

*   **Phase 3** needs none. Its CODR generator builds one table per product by default, and a test with two tables in one product covers B.
*   **Phase 4** generates Inventory documents only. Its 7,000-leaf read is timed at 7,000 one-table products, the most leaves any grouping gives.
*   **Phase 5** tests a release of 200 tables that mixes both shapes: one-table products, and grouped products where each per-product refusal of `release_due` holds back several tables.
*   **Phase 6** builds the full corpus in the shape above. It routes, pins, evicts and checks readiness per product, so the attach unit, the LRU's `max_attached` and Goal 1's first-attach target are measured on both one-table products and products of 5 to 20 tables.
*   **Phase 8** measures the remap bound over product keys.

The full corpus is the gating fixture of Phases 6 to 9; Phase 5 uses its own 200-table synthetic release. The generator takes the mix of product sizes as a parameter.

## Critical User Journeys (CUJs)

### CUJ 1: A daily release under embargo

*   **Given**: 200 tables have new editions, uploaded and folded into the Inventory as `embargoed` with a `release_at` of 08:30, and so do the structure editions they were cut from. The live view holds the previous editions.
*   **When**: the release job runs at 08:30 by the storage server's clock.
*   **Then**: one new Inventory version makes all 200 `live` and, by invariant 8, makes their structure editions live in the same version. Requests in flight finish on the old registry. Before that version, every pod answers 404 for a dataflow that only an embargoed edition holds, and no pod has attached an embargoed edition. Within 90 s the CDN serves the new results.

### CUJ 2: A pod joins the hot pool

*   **Given**: an Inventory whose live view pins 12 products in the `hot` pool; a new pod starts.
*   **When**: it reads `CURRENT`, the root it names and the leaves.
*   **Then**: it pulls the required tier of each pinned live edition, verifies each against the leaf's `manifest_sha256` and with `check_on_open`, attaches them into one `EditionEngine`, and only then reports ready. Remote-tier data files stay in object storage.

### CUJ 3: A request for an unpinned table

*   **Given**: a `base`-pool pod, and a data request for a dataflow whose product is not attached.
*   **When**: the ingress routes the request by consistent hash on `product_id`.
*   **Then**: the router resolves the dataflow to its product through the live view, pulls and verifies the required tier, attaches the edition once however many requests arrive together, answers, and evicts the least recently used editions when more than `max_attached` are attached.

### CUJ 4: A cached result

*   **Given**: a result for (dataflow, query, format, language) in `results/` under its key, and a CDN copy whose TTL has expired.
*   **When**: the CDN revalidates with `If-None-Match`.
*   **Then**: the pod computes the key from the live edition's fingerprints, the applied overlay digests, the hook ids and the render versions. If the key matches, it answers 304 without reading `results/`; otherwise it streams the new body with the new key as ETag.

### CUJ 5: A schema bump

*   **Given**: sdmxlib raises the store `schema_version`.
*   **When**: every live product is republished at the new schema and the pods roll.
*   **Then**: `check_edition(at="publish", expect=<previous edition>)` stops the job if any fingerprint moved. Each product has one live edition per schema version; old pods serve old entries and new pods new ones; no request gets a non-2xx response.

### CUJ 6: A data product built from an FMR and a DuckLake table (done)

*   **Given**: a dataflow whose structures an FMR holds, with a data constraint and a hierarchy association that reference it; its observations in a DuckLake table the producer maintains.
*   **When**: the producer calls `sdmxlib.edition.build_edition` with a `RestRegistry` over the FMR's 3.0 API and that table.
*   **Then**: the edition holds the dataflow's closure, including the constraint, the hierarchy and the association. Each codelist is cut to the codes the observations use and their ancestors, with `partial_of` recorded. `check_edition` passes. After the constraint is edited in the FMR, the next edition's structural fingerprint for that dataflow differs and its other fingerprints are equal.

## Technical Plan (Architecture)

The specification's architecture (§3) stands. The plan fixes four placements:

*   **sdmxlib owns the closure walk and the partial cut** (`build_edition`, Phase 1, built). `fetch_edition` and CUJ 6 call it today; Phase 3 makes the same walk and cut usable on an open working copy, so statcatlib's ingest calls it too.
*   **sdmxlib owns the Inventory's schemas, rules and reader; statcatlib owns the writer** (specification §6, statcatlib RFC decision 8). sdmxlib-rest reads the live view through its sdmxlib dependency, so it declares no Inventory types of its own. A format change, such as Phase 4's routing fields, ships in an sdmxlib release first, and every reader deploys it before statcatlib writes it.
*   **On a server, only the release job writes Inventory versions** (specification §11.2 step 4). The upload, release and GC jobs are statcatlib commands (`statcat upload`, `statcat release`, `statcat gc`), each a thin CLI over a library call, as `statcat prune` is today. GC deletes and never writes a version.
*   **Pre-materialisation renders with the pods' code**, so it is a command of statcat-serve (Q8). It runs as its own job with its own identity, between the fold and the release; the orchestrator sequences the jobs.

Phases, in build order:

| Phase | Builds | Repository | Spec §16 | Depends on | Status |
|---|---|---|---|---|---|
| 0 | Specification v5, then v6 | sdmxlib | — | — | merged (GH-222; GH-249) |
| M | Remote-tier measurement | sdmxlib | — | — | done, sdmxlib 0.76.0 (GH-185); no cache built |
| 1 | Closure builder, partials, `partial_of` | sdmxlib | M6 (sdmxlib part) | 0 | done, sdmxlib 0.76.0 |
| 2a | Products, editions, the Inventory on local disk | all three | M8 (core and local writer) | 1 | done: sdmxlib 0.80.0 to 0.82.0, sdmxlib-rest 0.9.0, statcatlib 0.12.0 |
| 3 | Structure products; partials checked against them; the CODR generator | sdmxlib, then statcatlib | M6 | 2a | done: sdmxlib 0.83.0 and 0.84.0, statcatlib 0.13.0, sdmxlib-rest 0.9.2 |
| 4 | The Inventory on blob storage, upload and `pending/`; remote catalogs; the 7,000-leaf read measurement | statcatlib | M8 (Inventory documents), M9 (upload) | 2a and Q1 | open |
| 5 | Fold, release, `release_due`, GC of `products/` | statcatlib | M9 | 4 | open |
| 6 | Router, tiers, pools, result store and its GC, pre-materialisation | statcat-serve, with four seams in sdmxlib-rest | M10 | 4, 5 | open |
| 7 | Catalog-level search and the search pool | statcatlib, sdmxlib-rest | M13 | 3, 4 and 5 (statcatlib half), 6 (serving half), Q3 | outline |
| 8 | Kubernetes | statcat-serve | M11 | 6, 7 | outline |
| 9 | Rollout and restore drills | statcat-serve | M12 | 8 | outline |

Specification §16 numbers its milestones by risk and was written before this order; the phase is the build order. M13, catalog-level search, is built as Phase 7, before M11 and M12, because Phase 8 deploys the search pool beside `hot` and `base`. statcatlib's half of Phase 7 needs structure products, blob storage and the release job, so it may run beside Phase 6.

Phase 5's release job is tested against the Inventory alone, with no pods: the fold, a stub pre-materialisation job and the release version. Phase 6 tests the same release under load with pods and the real job, so neither phase waits on a later one.

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| An embargoed result is readable before 08:30 | The catalog's container and `results/` have no public access (specification §10.1); pods read only `LiveInventory`, so they never pull or attach an embargoed edition; catalog-level search reads only the live view; pre-materialisation runs in its own job. Phase 5 checks the reader-principal allowlist before each release; Phase 6 tests the rest (CUJ 1). |
| The CDN serves stale bytes after a release or delta | Short TTL with ETag revalidation (specification §7.4); Phase 6 tests visibility within 90 s and the 304 path. A rollback also purges the product from the CDN. |
| A same-URN edit at the FMR changes a full codelist under its products | Invariant 8 holds back every product cut from the old fingerprint until it is republished; Phase 3 tests the refusal in the local writer, Phase 5 in the release job. |
| GC deletes a data file that an upload in progress wrote | GC skips files younger than the upload grace period (Phase 5); a test races an upload against GC. |
| A rollback targets an edition whose files GC deleted | GC keeps every file listed by an edition recorded, or tombstoned, within the window, and `rollback` targets only a `superseded` entry (specification §6.4). Phase 5 tests GC after a rollback. |
| Two writers race for one Inventory version on blob storage | Exclusive creation with a single-shot `Put Blob` and `If-None-Match: *` (specification §6.6); Phase 4 runs sdmxlib's conformance suite against the blob writer. |
| The hot pool goes unready at 08:30, when the release adds pinned editions no pod could pull before | A pod adopts a new Inventory version only after attaching the version's new pinned editions, and stays ready on the old version meanwhile (Phase 6). |
| statcatlib or sdmxlib-rest cannot adopt a breaking sdmxlib release in one step | Each phase pins one minor and lists the moves in `docs/upgrading.md`. |
| Reading 7,000 leaves at pod start or at each release is slow | Readers fetch only the leaves whose hash changed. Phase 4 measured a cold read of every leaf at 7,003 GETs, about 70 s in sequence by estimate at an assumed 10 ms a request, and the owner decided to build no baked summary now (specification §6.5, Future Improvements, `docs/design/caching.md`). |

## Alternatives Considered

The specification records the design alternatives and their rejections (§6.6, §7.4, §7.5, §7.6, §8, §12, §14). These are about how the remaining work is built.

### Alternative A: statcatlib owns the closure walk and the partial cut

*   **Pros**: the ingest that vendors closures is statcatlib's, and Phase 3 would need no sdmxlib change.
*   **Cons**: `fetch_edition` and any producer outside statcatlib (cpt-builder, CUJ 6) would each rebuild the walk and the cut. The first revision of this plan had two such implementations, cutting partials by two rules.
*   **Reason for Rejection**: the field the cut records belongs to sdmxlib's manifest, and the walk already exists there (`References.closure`). Phase 3 extends it for open working copies.

### Alternative B: Route by reading every live manifest

*   **Pros**: no Inventory format change; a manifest already lists each edition's bindings, artefacts and file sizes.
*   **Cons**: a pod resolves a dataflow or an artefact to its product only after fetching the manifest of every live edition, 7,000 at start and again for each changed product, and the ingress (Q5) would need the same reads.
*   **Reason for Rejection**: specification §6.3 adds the routing fields to the leaf as 1.x optional fields, and leaves are reused by hash, so a reader fetches only what changed (Phase 4).

### Alternative C: The CODR generator under `tests/support/`

*   **Pros**: test code stays out of the package.
*   **Cons**: Phases 6 to 9 build their corpus from scripts and benches outside statcatlib's test suite, and those cannot import from `tests/`. The second revision of this plan put the generator there.
*   **Reason for Rejection**: the generator goes in `statcatlib.testing`, beside `synthetic_catalog` (Phase 3).

### Alternative D: Plan all phases to implementation level now

*   **Pros**: one document covers the program.
*   **Cons**: Phases 7 to 9 depend on choices Phase 6 makes (the ingress, the pool sizes, the attach budget) and on Q8, so detail written now would be rewritten then.
*   **Reason for Rejection**: the plan specifies what the next phases need and re-plans the rest after Phase 6.

--------------------------------------------------------------------------------

## Non-Functional Requirements

*   **Security and privacy**: the embargo is the critical property (Risks, first row). The catalog's container is the boundary (specification §10.1): only the jobs, the pods and the preview deployment hold a role on it, and `results/` is on a separate account. `EditionRegistry` exposes no `sql()`, so a request cannot reach another attached edition through raw SQL.
*   **Performance**: Goal 1's targets, on the reference node. Every cache this program adds follows `docs/design/caching.md`: measured first, single-flight, bounded, invalidated by a lifecycle, and guarded by a test that counts the expensive calls.
*   **Rollout and rollback**: sdmxlib, statcatlib, DuckDB and DuckLake are pinned in one image for the jobs and the pods (specification §9.2). Rollback by change class:

    | change | rollback |
    |---|---|
    | a data or structure edition | `CatalogAdmin.rollback(product, to=, if_live=, reason=)` writes a new Inventory version making the superseded edition live again, and the product is purged from the CDN (specification §7.4); `CURRENT` only moves forward |
    | search tables | per-edition tables live in their edition's directory, so a rolled-back edition brings its own; catalog-level tables are named by Inventory `seq` (Phase 7) |
    | a library release | redeploy the previous image; a store schema change follows the two-version rollout of CUJ 5 |

*   **Idempotency**:
    *   `statcat upload`, re-run for an edition already in `pending/` or recorded in the Inventory, uploads nothing and writes no second `pending/` entry (the entry is put-if-absent).
    *   `statcat release`, re-run when every due edition is already `live`, writes no Inventory version.
    *   `statcat gc` deletes only what is unreferenced, so a re-run deletes nothing.

## Additional Resources

*   [`sharded-bundle-catalog.md`](sharded-bundle-catalog.md): the specification this plan builds.
*   [`../edition.md`](../edition.md): the edition format, manifest 3.0.
*   [`caching.md`](caching.md): the rules every cache in this program follows.
*   [StatCat-Suite/statcat-suite.github.io#19](https://github.com/StatCat-Suite/statcat-suite.github.io/issues/19): the cross-repo tracker for Phases 4 to 9. [StatCat-Suite/statcat-suite.github.io#15](https://github.com/StatCat-Suite/statcat-suite.github.io/issues/15) tracked Phase 2a and is closed.
*   The statcatlib RFC [Catalog, Products, Editions and the Inventory](https://github.com/StatCat-Suite/statcatlib/blob/main/docs/design/products-editions-inventory.md) and its [Phase 2a design](https://github.com/StatCat-Suite/statcatlib/blob/main/docs/design/phase-2a-catalog-editions.md): statcatlib's API and the scope Phases 4 to 6 inherit.
*   The hub's [Architecture](https://statcat-suite.github.io/architecture/) and [Storage and deployment](https://statcat-suite.github.io/storage/) pages: the suite as built.
*   [GH-221](https://github.com/StatCat-Suite/sdmxlib/issues/221): the incoming-reference closure, with the FMR measurement.
*   [statcatlib's catalog format](https://statcat-suite.github.io/statcatlib/catalog-format/): the format of the statcat section in each manifest.

## Future Improvements

*   **Shared structure caches across editions** (GH-160): re-measured once a deployment holds many distinct editions; GH-133 measured 2.2 MB per registry, too small to share across 16 registries. Phase 6's load bench is the first such deployment.
*   **Signed manifests**: `manifest.json.sig` is reserved (specification §4.3).
*   **A baked Inventory summary**: the owner decided on 2026-11-06 to build none for now, on Phase 4's measurement; specification §6.5 holds the numbers and the reasons. The results of statcatlib's real-account checklist, which tests a thread pool against real storage (StatCat-Suite/statcatlib#134), can reopen it.

--------------------------------------------------------------------------------

## Detailed Implementation Plan

Each phase lists its repository, issues, contracts and gating tests, and ends with that repository's `just ci` green and, where a later phase pins it, a release. statcatlib and sdmxlib-rest are changed by their maintainers. From sdmxlib, each of their phases is an issue on their tracker that links this plan; line anchors into those repositories go in those issues. Issues are named `GH-N` in sdmxlib and `StatCat-Suite/<repo>#N` elsewhere.

### Built

#### Phase 1: the closure builder and partials (sdmxlib 0.76.0)

GH-221 (the incoming-reference closure), GH-223 (`build_edition`), GH-224 (`partial_of`, recorded since manifest 2.1), GH-228 (sibling dataflows left out of a structural fingerprint) and GH-185 (the remote-tier measurement). `build_edition(source, dataflows, path, *, partials=True, ...)` builds a product's first edition: it fetches each dataflow with `resolve="all"` and its DSD with `resolve="parents"`, keeps the union of the closures, writes the observations, cuts each `Codelist` by specification §8's rule and seals. `check_edition(structures=)` checks each partial against the full artefact. Specification §4.4 and §8 describe the result; §18.2 maps the names Phase 1 shipped under to today's.

#### Phase 2a: products, editions and the Inventory on local disk (shipped 2026-11-02)

StatCat-Suite/statcatlib#97 folded this plan's earlier Phases 2a and 2b into the statcatlib RFC's Phase 2a: StatCat-Suite/statcatlib#46 (open an edition without writing), StatCat-Suite/statcatlib#47 (search tables named per edition) and StatCat-Suite/statcatlib#48 (the overlay field a server reads).

*   **sdmxlib 0.80.0** (2026-10-31): GH-248 measured the seal cost first; GH-250 (the rename and manifest 3.0), GH-251 (the Inventory schemas, rules, `LiveInventory`, `FullInventory`, the `CURRENT` protocol), GH-252 (the conformance suite, run also against a Delta-backed prototype) and GH-253 to GH-259 (specification §18.3).
*   **sdmxlib 0.81.0** (2026-11-02): GH-334, the `describe` event kind.
*   **sdmxlib-rest 0.9.0** (2026-11-02): StatCat-Suite/sdmxlib-rest#81 (serve one edition, read manifest 3.0) and StatCat-Suite/sdmxlib-rest#84 (require sdmxlib 0.81).
*   **statcatlib 0.12.0** (2026-11-02): StatCat-Suite/statcatlib#70 in slices StatCat-Suite/statcatlib#101 to StatCat-Suite/statcatlib#105 under its [Phase 2a design](https://github.com/StatCat-Suite/statcatlib/blob/main/docs/design/phase-2a-catalog-editions.md): `Catalog`, `Product`, `WorkingCopy`, `EditionView`, the Inventory writer on local disk with a batched seal, automatic release with embargo off, `CatalogAdmin` (`rollback`, `release`, `cancel`, `set_embargo`, `describe`, the narrow `prune`), `statcat verify-catalog` and `statcat doctor`. It met the adoption gate in specification §18.1.

#### Phase 2a follow-ups

sdmxlib 0.82.0 (2026-11-02) shipped GH-335 (`sdmxlib.storage.create_exclusive`), GH-336 (`LocalRegistry.edition(manifest_sha256=)`), GH-338 (readers accept an unknown `event.kind` within format 1.x) and GH-339 (`Rule` and `JsonValue` exported). sdmxlib-rest 0.9.1 shipped StatCat-Suite/sdmxlib-rest#87 and statcatlib 0.12.1 shipped StatCat-Suite/statcatlib#115 (adopt sdmxlib 0.82). sdmxlib 0.83.0 (2026-11-03) shipped GH-343 (name the hard-link requirement when a seal fails) and GH-344 (fsync what `copy_edition` copies before its manifest), both via GH-352. Open:

*   GH-341: the specification page keeps its `sharded-bundle-catalog` file name until the owner decides on a rename.
*   StatCat-Suite/statcatlib#108 (keep `build-index` options across processes) and StatCat-Suite/statcatlib#110 (the `model_name` warning).

Phases 3 and 4 start in statcatlib after StatCat-Suite/statcatlib#115, since the blob writer relies on `create_exclusive`'s rule and on the `manifest_sha256=` check.

#### Phase 3: structure products (shipped 2026-11-03)

GH-349 decided Q2 by splitting StatCan's structure products by change rate into `structure-geo`, `structure-classifications` and `structure-concepts`. GH-355 (issue GH-354) amended specification §6.5 items 4 and 8. The [Phase 3 section](#phase-3-structure-products-sdmxlib-then-statcatlib-statcat-suitestatcatlib51-and-an-sdmxlib-issue-to-file-spec-m6) below keeps the contracts and gating tests the phase was built to.

*   **sdmxlib 0.83.0** (2026-11-03): GH-347 via GH-350 (`fill_edition`, `fill_structures` and `check_edition(structures=[...])` over several structure editions) and GH-351 via GH-352 (references between structure products).
*   **sdmxlib 0.84.0** (2026-11-03): GH-356 (a quoted catalog alias for a product id that starts with a digit) and GH-357 (`check_edition(fingerprints=False)`), both via GH-358.
*   **statcatlib 0.13.0** (2026-11-03): StatCat-Suite/statcatlib#51 in slices StatCat-Suite/statcatlib#119 to StatCat-Suite/statcatlib#122: structure products, partial codelists in data products, the cross-product rules (specification §6.5 items 4, 7 and 8), stale data editions and the `statcat doctor` topics.
*   **sdmxlib-rest 0.9.2** (2026-11-03): StatCat-Suite/sdmxlib-rest#91 (require sdmxlib 0.84).

### Phase 3: structure products (sdmxlib, then statcatlib; StatCat-Suite/statcatlib#51 and an sdmxlib issue to file; spec M6)

Built (2026-11-03); [Built](#phase-3-structure-products-shipped-2026-11-03) records the releases. Q2 is decided (2026-11-02): StatCan's structure products follow the change-rate split. statcatlib's design for the phase settles what this section leaves open.

*   **sdmxlib: the walk and the cut on an open builder.** `build_edition` builds a product's first edition, seals it itself, gathers structures only from dataflow closures and refuses a non-empty path, so neither a republish nor a structure product can use it. sdmxlib adds:
    *   a function that fills an open builder from `LocalRegistry.build(product_dir, previous=)` with the dataflows' closures, the observations and the partial cut, and leaves the seal to the caller, so a `WorkingCopy` can call it on any edition;
    *   a function that fills an open builder with the full artefacts of one agency in the named families (codelists, concept schemes, category schemes, hierarchies), or with an explicit list of artefacts, with no dataflow. The change-rate split puts all of one family in some products and part of a family in others (the classification codelists in `structure-classifications`, the geography codelist in `structure-geo`), so a selection by family alone cannot fill every product;
    *   `check_edition(structures=)` over several structure editions, since the change-rate split puts one agency's full artefacts in three products.

    These ship in an sdmxlib minor that statcatlib pins.
*   **statcatlib: structure products.** A product of `kind: "structure"` in its leaf and in the statcat section holds the full artefacts of one agency that the change-rate split assigns it, with no bindings. For StatCan these are `structure-geo` (the full geography), `structure-classifications` (NAICS, NOC, SGC) and `structure-concepts` (the concept schemes). `Product.working_copy` gains the product's kind. statcat section format 3 already reserves the kind (`PRODUCT_KINDS` in `src/statcatlib/bundle.py`), and `check_catalog` already reports a structure edition that binds a dataflow, so no format changes.
*   **statcatlib: ingest with partials.** `src/statcatlib/sources/sdmx.py` fills each data product's working copy through the sdmxlib function above with partials cut, and the seal checks it with `check_edition(at="publish", structures=<the live structure editions>)`.
*   **statcatlib: the writer's cross-product rules.** When it records a version, the local Inventory writer enforces specification §6.5 item 4 (full artefacts agree across live and embargoed editions), item 7 (at most one structure home per full artefact) and item 8 (a data edition goes `live` only once the structure edition its partials were cut from, and whose full artefacts it holds, is `live`). It reads the manifests of the editions involved. Each refusal names the item, the URN and both editions. sdmxlib's `inventory/_rules.py` checks one product's leaf; these rules read several products, so the writer holds them. statcatlib's design settles three cases: an item-8 refusal under embargo off, a structure edition and its data editions sealed in one `Catalog.seal`, and which edition item 4 refuses when a data product holds a full concept scheme that a structure edit changes. Item 4 is amended for the third case (GH-354): the structure home wins, so the structure edit is accepted and the data editions holding the old copy become stale until republished, and a full artefact with no structure home is compared at release among the editions that will be live, so the data products holding a changed one release in one Inventory version. Item 8 extends to full artefacts with a structure home (owner decision of 2026-11-03).
*   **statcatlib: the CODR generator** (new in `src/statcatlib/testing.py`). It builds one CODR-shaped data product: a DSD with a 5-level geography codelist of 6,000 codes, 3 classification codelists and 4 dimensions, and a 2,000-row observation frame per table over a random subset of the codes. It takes a seed and the tables per product, and builds the shared structure product the partials are cut from on first use. StatCat-Suite/statcatlib#51 names `tests/support/codr.py`; Alternative C moves it.
*   **Gating tests**
    *   A generated data product's `metadata.duckdb` is under 10 MB.
    *   A partial holding a code its full artefact lacks is a `check_edition` finding naming the code.
    *   A partial whose full artefact is in a second structure product passes `check_edition` given both.
    *   A data edition holding a full artefact whose fingerprint differs from its structure home's is refused, naming the URN and both editions; a structure edition that edits that artefact is accepted, and the data edition holding the old copy stays live and stale (item 4).
    *   A data edition holding a full artefact with no structure home is refused `live` when another product's live edition, live after the same version, holds it with a different fingerprint, naming the URN and both editions; recorded `embargoed` it is accepted, and released in one version with the other product's republished edition it goes live (item 4).
    *   A full artefact with two structure homes is refused (item 7).
    *   A data edition is refused `live` while its structure edition is not live, and released once it is (item 8).
    *   A data edition holding a full concept scheme that only an embargoed structure edition carries is refused `live`, and released once that structure edition is live (item 8).
    *   A republish of a data product through a working copy keeps its partials cut.
    *   A product generated with two tables vendors each shared partial once.

### Phase 4: the catalog on blob storage (statcatlib; StatCat-Suite/statcatlib#98; spec M8, M9)

StatCat-Suite/statcatlib#98, restated on 2026-11-04, holds the current scope and the full acceptance list. statcatlib RFC §7 and the Phase 2a design's non-goals define the phase: everything a catalog needs on object storage short of the release job. statcatlib drafts a local design with `/egm-draft` before any code, and that design settles the questions the issue lists. Four parts, which can land separately: the blob writer, upload, remote catalogs and the read measurement.

`CatalogAdmin.correct` and `reinstate` (the Phase 2a design's P6) land in StatCat-Suite/statcatlib#130 before Phase 9's restore drill, which is their first real use. A catalog-wide `correct` needs a rule table for head state, so `sdmxlib.inventory` gains no `correct` rule in this phase. Neither gates Phase 5 or Phase 6.

Upload and remote catalogs need GH-371's `references=False`, because neither can name the structure editions that an edition with no bindings may reference.

Built: sdmxlib 0.85.0 (GH-348, GH-367) and statcatlib 0.14.0 (StatCat-Suite/statcatlib#128) record the routing fields `dataflows`, `pull_bytes` and, for structure products, `artefacts`; StatCat-Suite/statcatlib#126 shipped the corpus generator and recorded the no-home measurement.

*   **Object storage** (statcatlib). The writer and upload use `obstore`'s stores directly (`AzureStore`, `S3Store`, `MemoryStore` for tests, since `LocalStore` has no update mode for `CURRENT`); the Inventory reader's `fetch(path) -> bytes | None` is a small adapter over `get`.
*   **The Inventory writer on blob storage.** Leaves, roots and `CURRENT` follow specification §6.6 as the Inventory history log design amends it: a single-shot `put(mode="create")` (`Put Blob` with `If-None-Match: *`) for every root, an unconditional single-shot `Put Blob` of the same bytes for every leaf the version changes, never ADLS DFS create plus append, and a conditional, forward-only `CURRENT`. The writer rewrites no carried-forward leaf. It skips no changed leaf it finds present, so a leaf whose bytes reappear survives an expiry run's `If-Match` delete: a Put before the delete changes the ETag and the delete fails, and a Put after it recreates the leaf. `event.actor` comes from the writer's storage identity (the Phase 2a design's risk table). Roots carry the history fields, computed from root `seq - 1` and recomputed after a lost race. The writer refuses a 2.x `catalog.json` until StatCat-Suite/statcatlib#125 stage 2 adds the `HISTORY` lease.
*   **Upload** (`statcat upload`, specification §11.2 step 1). It supports Azure only in this phase and refuses an `S3Store`, because `obstore` cannot set or read CRC32C; the full-object CRC32C on S3 waits for a deployment on S3. It copies the data files with the `Content-MD5` Azure keeps for a single-shot Put, then the edition directory with `manifest.json` last, verifies every listed file with `check_edition(at="receive", lookup=)` and `verify_remote`, and writes `pending/<product_id>/<edition_id>.json` (`edition_id`, `release_at`) put-if-absent. It never writes `CURRENT` or `inventory/`.
*   **Remote catalogs.** `Catalog.open` and `EditionView` read an edition with its data files in object storage through `LocalRegistry.edition(path, remote=, engine=, manifest_sha256=)` and an `EditionEngine` holding the store's secret. statcatlib's design gives the signature and where the required tier lands on local disk.
*   **The Inventory corpus and read measurement**: the corpus holds Inventory documents only (owner, 2026-11-04), since a full corpus needs about 117 GB (statcatlib's `bench/corpus-results.md`) and moves to Phase 6. A `statcatlib.testing` function and a script under `bench/` generate 7,000 leaves and their roots with sdmxlib's rules, with both live and embargoed entries and no data files, and write them through the blob writer. The read touches only `CURRENT`, roots and leaves. Azurite gives the fetch count and bytes. Its wall time excludes real storage latency, so the report states that and estimates the wall time per GET.
*   **Test rig.** statcatlib adds a `stores` tier following sdmxlib's `tests/stores/` and runs it in `just ci`. The tier fails when no container runtime exists, because the pre-push hook is statcatlib's only gate.
*   **Gating tests**
    *   `sdmxlib.inventory.conformance.run` passes against the blob writer over Azurite.
    *   The blob writer refuses a 2.x `catalog.json` and names the reason.
    *   A writer killed between the upload of a leaf and of its root leaves the previous head readable and writable.
    *   An upload writes nothing under `inventory/` or `CURRENT`, checked with a store that records every write.
    *   A second upload of the same edition writes no second `pending/` entry.
    *   A remote edition opened with a wrong `manifest_sha256` is refused before any other file is read.
    *   `Catalog.open` over a blob catalog with embargoed entries never returns one.
    *   A 7,000-leaf Inventory read records its wall time, its fetch count and the bytes fetched. The measurement goes into specification §6.5, with the owner's decision on the baked summary.

### Phase 5: release and garbage collection (statcatlib; StatCat-Suite/statcatlib#52; spec M9)

Starts once Phase 4 is done; Q9 and Q10 are decided (2026-11-03). statcatlib's design for the phase settles the shape of `statcat release` (one run that folds and later releases, or separate runs), the retention configuration's schema, and where `release_due`'s refusals are persisted.

*   **Fold.** `statcat release` folds every `pending/` registration into one Inventory version as `embargoed` entries (specification §11.2 step 2). Two pending editions of one product built on the same live edition are both refused and reported; a pending edition built on the live edition while another is embargoed goes through `supersede_pending`. A pending entry whose `edition_id` is older than the tombstone window is refused. The release job keeps read-only access to `pending/` (specification §10.1). The fold skips a registration whose edition the Inventory already records, so a rerun or a crash after the Inventory write is a no-op, and the GC job removes folded registrations after the retention window. A specification PR changes §11.2 step 2 and §11.4 to match (decided 2026-11-03).
*   **Release.** At release time it writes one Inventory version through sdmxlib's `release_due(leaves, now)`, with `now` from the storage response's `Date` header, refusing to run when its own clock is ahead by more than the skew bound (specification §6.4). Structure editions go live with or before the data editions cut from them (item 8). `release_due`'s per-product refusals are persisted and reported, and the batch goes on.
*   **Release invariants.** The release job enforces specification §6.5 items 2 (`manifest_sha256` matches), 3 (known `canonical_form`), 4, 5 (final means final) and 6 (chain continuity), with items 7 and 8 from Phase 3, reading each manifest it verifies.
*   **Artefact records in leaves.** The Inventory writer fills the optional `LeafEntry.artefacts` field (specification §6.3, as GH-361 specifies it) from the manifest when it records an entry, and the cross-product rules (§6.5 items 4, 7 and 8) read artefact records from leaves instead of every live manifest (Q10).
*   **Access precondition.** Before each release it lists the principals that hold Data Reader on the catalog's container, nested groups and eligible assignments included, compares them with a reviewed allowlist (specification §10.1), and refuses to release on a mismatch. The listing is a function the tests replace, since Azurite has no role assignments.
*   **Retirement.** The release job writes the `retire` versions that tombstone `superseded` and `withdrawn` entries past their window, so it stays the only writer. The 2a prune's local `retire` path is unchanged.
*   **GC** (`statcat gc`, specification §11.4). It reads the Inventory chain back through the retention window and deletes edition directories tombstoned longer than the window and `data/` files no recorded or tombstoned manifest lists. It skips data files younger than the upload grace period (default 24 h, longer than the longest upload), refuses to start when the §11.6 inequalities fail, has a dry run, and logs every deletion. It writes no Inventory version. GC never touches `inventory/`, and stops at `expired_through`. Old Inventory versions expire as the suite design [Inventory history log](https://statcat-suite.github.io/design/inventory-history-log/) (StatCat-Suite/statcat-suite.github.io#21) sets out, in two stages that StatCat-Suite/sdmxlib#361 specifies. Stage 1 ships before this phase: Inventory 1.2 gives each root `changes` and `previous_root_sha256`, every rule keys on their presence, 1.2 also adds the `reinstate` kind, and the reader falls back to `inventory/HISTORY` when `CURRENT` is missing. Stage 2 ships here. A catalog opts in through `catalog.json` 2.0. The release job, after its release step, moves expired roots into append-only segments under `history/<restore_epoch>/`, indexed by `inventory/HISTORY`, and deletes leaves no retained root names, holding the catalog lock and a lease on `inventory/HISTORY`. Each run first checks that the §9.2 rollback window of the last pre-stage-1 pod image has closed, and reads every pod's `sdmxlib_version`. Server expiry in stage 2 runs on Azure only; S3 and GCS wait for a design of their own. The Phase 2a design's P7 (keep roots, leaves and tombstones) ends with stage 2. GC of `results/` is Phase 6's, which defines the key.
*   **`EmbargoReader`** arrives here, with its first caller (statcatlib RFC, decision list): Q9's preview deployment, which the operator runs as a separate service. statcat-serve holds the wiring that serves it through sdmxlib-rest (Q8). It is a separately constructed object over `FullInventory`, with its own engine, and no attribute of `Catalog` returns one.
*   **Repair and alerts.** The server sweep of sealed but unrecorded editions reports each for an owner's discard-or-reinstate decision (specification §6.7, §11.6). `advance-current` brings `CURRENT` up to the newest root. The jobs log each §11.6 alert they detect as a structured event with a fixed name; Phase 8 wires them to alerting.
*   **Gating tests**
    *   A 200-table synthetic release becomes visible in one Inventory version.
    *   The fold runs `check_edition(at="receive", remote=, structures=)` on an edition with no bindings before it records it, since upload and `view` defer those references.
    *   A release interrupted before that version leaves every entry `embargoed`, and a re-run releases them.
    *   The fold refuses two pending editions on the same live edition and a pending entry older than the tombstone window.
    *   A stale automatic release after embargo was turned on is refused.
    *   A non-fast-forward edition is refused, naming both editions, and the rest of the batch goes live.
    *   A data edition whose structure edition is not due stays `embargoed` (item 8).
    *   A new edition of a `final` product with a different `content_fingerprint` is refused.
    *   A release run with its clock ahead of the storage server's is refused.
    *   A principal outside the allowlist stops the release.
    *   GC deletes exactly the unreferenced files; it keeps a file a racing upload wrote inside the grace period, and everything a rolled-back-to edition lists.
    *   GC refuses to start with backup retention longer than the `data/` GC window.
    *   `advance-current` moves a lagging `CURRENT` forward and never back.
    *   A re-run of each job changes nothing.

### Phase 6: serving a catalog (statcat-serve and sdmxlib-rest; StatCat-Suite/sdmxlib-rest#46, StatCat-Suite/sdmxlib-rest#47, StatCat-Suite/sdmxlib-rest#48, StatCat-Suite/sdmxlib-rest#52, StatCat-Suite/sdmxlib-rest#10, StatCat-Suite/sdmxlib-rest#69, StatCat-Suite/sdmxlib-rest#70; spec M10)

Q8 is decided (2026-11-03): sdmxlib-rest adds four seams (a per-request catalog resolver, the ETag set by the route, routing refusals mapped to 404 and 503, and a published `RENDER_VERSION`) and statcat-serve holds the rest. sdmxlib-rest's tests write Inventory versions with sdmxlib's rules (`next_root`), as `tests/test_inventory_describe.py` does, since sdmxlib-rest never imports statcatlib; the benches read the full corpus as data. Phase 6 builds that corpus with the shipped `codr_corpus`, about 117 GB and about 4 hours (statcatlib's `bench/corpus-results.md`), and copies it through the blob writer and upload.

*   **Pin**: the sdmxlib release that carries Phase 4's routing fields.
*   **Partial references** (StatCat-Suite/sdmxlib-rest#69): `detail=referencepartial` returns the edition's stored partials and, for a codelist the edition holds in full, cuts a partial with `Codelist.partial`, in place of the `referencestubs` answer sdmxlib-rest returns today.
*   **Inventory polling**: a `LiveInventory` over a `fetch` that conditional-GETs `CURRENT`, probes forward and fetches only the leaves whose hash changed (specification §7.3), on an interval this phase sets (5 s proposed).
*   **Pull** (StatCat-Suite/sdmxlib-rest#48): the required tier through `edition_files(manifest, tiers=("required",))` and any copy tool, checked with `check_on_open(stamps=, remote=)`; the remote tier read through `LocalRegistry.edition(path, remote=, engine=, manifest_sha256=)`.
*   **Router** (StatCat-Suite/sdmxlib-rest#46): `ShardRouter` over one `EditionEngine`, as specification §7.3 declares.
    *   Attaches are single-flight per `product_id`.
    *   Eviction is LRU over attached editions, bounded by `max_attached`, with `DETACH` once the evicted edition's in-flight requests finish.
    *   In-flight requests finish on the registry they started on.
    *   It refuses a manifest whose SHA-256 differs from the leaf entry's.
    *   It resolves a dataflow through the leaves' `dataflows` and a full artefact through the structure entries' `artefacts` (specification §7.3), and an entry without them through its manifest.
    *   It decides how a `/structure` request for a URN with no structure home is answered.
    *   For the 503 of a dataflow live only at another schema, it reads a `LiveInventory` per schema version it knows, or an sdmxlib accessor this phase asks for.
    *   It adopts a new Inventory version only after attaching that version's new pinned editions, and stays on the old version meanwhile.
*   **Pools and readiness** (StatCat-Suite/sdmxlib-rest#47): `pool` and `pinned` come from pool configuration or from leaf fields (Q7). `/ready` answers ready once every pinned live edition is attached and the Inventory version is current; `/live` is the liveness probe; `preStop` stops accepting, finishes in-flight requests and detaches.
*   **Serve-time overlay refusal**: an overlay entry acting on an annotation type in the statcat section's `search.annotations.types` is refused, with a message naming the entries.
*   **Result store and freshness** (StatCat-Suite/sdmxlib-rest#52, StatCat-Suite/sdmxlib-rest#10)
    *   The key is specification §7.4's: the binding's two fingerprints, the applied `overlay_digest`s, the hook ids, sdmxlib's `render_version(media_type)` and a new sdmxlib-rest `RENDER_VERSION` for its envelope, with a golden-file test that enforces the bump.
    *   That key replaces the ETag sdmxlib-rest's middleware computes today from the request and the edition's identity.
    *   Public responses carry `max-age=60, stale-while-revalidate=30`; an `If-None-Match` that matches gets a 304 without a read of `results/`; a 404 or 503 carries no ETag.
    *   A rollback purges the product from the CDN.
    *   GC of `results/` deletes keys no live entry reaches, after a grace period, through an index of keys by product and fingerprints that the result store writes.
*   **Pre-materialisation** (StatCat-Suite/sdmxlib-rest#70): a command that opens each embargoed edition from its own files, in its own job with its own identity, renders the release set and writes `results/`. It lives in statcat-serve and reads embargoed editions through statcatlib's `EmbargoReader`, as specification §11.2 step 3 states. The release-set file's format and its writer are this phase's to define.
*   **Ingress resolution**: Q5 is decided here. The remap bound of consistent hashing is tested in-process over the hash ring, without Kubernetes; Phase 8 repeats it on a cluster.
*   **Load driver**: `bench/load.py` replays a Zipf-distributed request mix over Phase 6's full corpus.
*   **Gating tests**
    *   A release version under the load driver fails no request.
    *   A dataflow live only at another schema answers 503 naming both schema versions.
    *   A `/structure` request for a full artefact routes to its structure product.
    *   No pod pulls or attaches an embargoed edition.
    *   A dataflow held only by an embargoed edition answers 404 from every pod before the release version and 200 after it, at the origin.
    *   A release that adds pinned editions keeps every pod ready across the switch.
    *   A delta to one dataflow changes its key, and the other dataflows keep answering 304.
    *   Readiness stays false until every pinned edition is attached.
    *   Many concurrent first requests for one product attach it once, by a counted attach.
    *   A render-version bump yields new keys.
    *   Goal 1's cold-start and first-attach targets run as bench tests.

### Phase 7: catalog-level search (StatCat-Suite/statcatlib#49, StatCat-Suite/sdmxlib-rest#53; spec M13). Outline

statcatlib builds per-language LanceDB tables from the `statcat.dataset` rows of every live edition and the full codelists of the live structure products, read through `LiveInventory`, stored under `search/<seq>/` and recorded in the Inventory root's `extensions.statcat` (specification §12). On a server the release job records them, in a new `search` event kind, written in a version of its own after the tables are built (decided 2026-11-03). It is an additive 1.x change, since readers from sdmxlib 0.82 accept unknown kinds (GH-338). Search lags a release by the build time, and the release version never waits on a search build. `Catalog.search` and `Catalog.facets`, which raise `NotImplementedError` today, answer from the tables. The search pool serves them. When the phase starts, it decides Q3 and whether the search tables are rebuilt for every Inventory version or a root may name an earlier version's tables, since a laptop writes a root at every seal. The gating test checks that a holdings-wide query answers from one table and never returns an embargoed edition's datasets, and that the getdata pools never read `search/`.

### Phase 8: Kubernetes (statcat-serve's `deploy/`; StatCat-Suite/sdmxlib-rest#50; spec M11). Outline

Deployments for `hot`, `base` and `search`; KEDA scaling; probes and `preStop` per specification §7.5; PodDisruptionBudgets; topology spread; workload identities per §10.1, each job with its own; the ingress Phase 6 chose; the §11.6 alerts and the pods' staleness metric wired to alerting; and the job, with its own identity, that promotes the day's pinned hot products to node-local disk before 08:30. Per Q4, pods use DuckDB's in-memory file cache only. Pool sizes, KEDA thresholds, probe timeouts and `max_attached` come from Phase 6's measurements. The gating test runs on a local kind (Kubernetes-in-Docker) cluster: scaling `hot` from 4 to 20 pods remaps at most 1/N of keys per added pod, where N is the pool's size after the addition, with zero failed requests.

### Phase 9: rollout and restore drills (statcat-serve's `deploy/`; StatCat-Suite/sdmxlib-rest#51; spec M12). Outline

The §9.2 schema bump with mixed pods and zero non-2xx responses, which fails if the republish takes more than Q10's 8 h on 4 workers; a rollback through `CatalogAdmin.rollback`, which writes a new Inventory version and purges the CDN; the restore of a deleted edition directory from the storage account's replica, followed by `reinstate`; `expunge` of a confidential release (specification §11.3); and a whole-catalog restore that bumps `restore_epoch` (specification §10.3), with `verify-catalog --find-newest-consistent`, `rebuild-inventory` (§6.7) and `CatalogAdmin.export(live_only=True)` (§10.1). Each drill ends with `statcat verify-catalog` and `check_edition(at="receive")` on every live edition. The commands a drill needs and that no earlier phase builds are this phase's work.

### Issues outside the phases

*   GH-160 (shared structure caches across editions): re-measured on Phase 6's load bench; built only if the duplicate exceeds the 35 MB GH-160 measured at 16 registries by an order of magnitude.
*   StatCat-Suite/statcatlib#53: spike M1(c), optional.
*   StatCat-Suite/statcatlib#99: the .Stat Suite migration design, drafted any time and built after Phase 6.

### Verification Plan

*   **Gate**: `just ci` in each repository a phase changes, run locally, read to its exit line before each commit, with `just docs-build` (`mkdocs build --strict`) where docs change.
*   **Test coverage**: every invariant, check and refusal a phase adds has a test that fails without it; a guard that starts at zero findings gets a sabotage test.
*   **Integration**:
    *   sdmxlib's `just test-integration` replays Phase 1's FMR cassettes with no network.
    *   The blob tests of Phases 4 and 5 run against Azurite, as sdmxlib's `just test-stores` does.
    *   Phase 6's full corpus serves Phases 6 to 9. Phase 4 uses Inventory documents only, and Phase 5 its own 200-table synthetic release.
    *   The kind cluster carries Phases 8 and 9.

--------------------------------------------------------------------------------

<!-- Provenance: Indicates initial structural drafting via EGM. -->

*Drafted with EGM*
