# Consolidated EGM review: sharded bundle catalog plan

*   **Target:** `docs/design/sharded-bundle-catalog-plan.md` (261 lines; FeatureRange 1..158, DetailedRange 159..261)
*   **Mode:** consolidated report, all five stages, no checkpoints
*   **GoldfishContext:** none beyond the files the plan links
*   **Date:** 2026-10-28

## Verdict

The plan cannot be implemented as written. It needs a rewrite in its later phases, and its first step has to be a change to the specification. Edits alone will not fix it.

Five problems block it:

- The specification's CDN rule defeats three of the plan's goals.
- The plan releases a version number that is already taken.
- The two GH-221 items in Phase 1 have no API.
- Two phases build the same closure.
- Four components that the goals depend on belong to no phase.

Phases 1 to 3 are mostly concrete. Phases 4 to 6 contradict the specification in three places. Phases 7 to 9 are outlines.

| Stage | Report | Blocker | Major | Nit |
|---|---|---|---|---|
| 5 Initial zoom out | `zoom_out_report.md` | 0 | 4 | 6 |
| 6 Comprehension | `comprehension_report.md` | 2 | 20 | 13 |
| 7 Critic (design, structural, style) | `critic_report.md` | 1 | 19 | 12 |
| 8 Intermediate zoom out | `intermediate_zoom_out_report.md` | 3 | 6 | 3 |
| 9 Implementation readiness (readiness, codebase, style) | `implementation_readiness_report.md` | 9 | 20 | 30 |

The 135 raw findings consolidate into 5 blockers, 10 majors and 6 nit groups below.

**Checked by the orchestrator against the source:**

- **The CDN rule.** Spec §7.4, lines 419 to 428, says what the reviewers quoted.
- **The 0.75.0 release.** Its release commit `47a0c38b` merged in PR #202. 24 commits followed it, carrying store schema 2.51 (GH-205), `Codelist.partial` (GH-12) and GH-213. Neither a `v0.75.0` tag nor a GitHub release exists.
- **The flip in spec §11.2.** Step 2 flips `current` to the staged index (`sharded-bundle-catalog.md:530`).
- **The bench selector.** `just bench-bundle-assertions` selects `bench and bundle and not recompute` (`justfile:204`).

**Coverage limits:**

- Stage 5 read the specification only through §13.
- Stage 6 did not read `docs/bundle.md` or `docs/design/caching.md`.
- Stage 9 could not check the FMR 12.4.2 behaviour or the DuckDB setting names against code. The FMR claims come from this session's probe and are true for that server.

## 🔴 Blockers

### A1. Settle the specification first, in a new Phase 0 "spec v5" PR

Five contradictions sit between the plan and the specification. Phases 4 to 6 cannot be planned until each one is settled.

1. **The CDN keeps stale responses.** §7.4 caches each response under its public URL with `Cache-Control: immutable` and no invalidation. A public URL is the same before and after a release, so the CDN keeps serving its pre-08:30 copy. That breaks three things:
   - Goal 2's one-write visibility (line 26).
   - Goal 3's per-dataflow invalidation (line 27).
   - Rollback (line 140).

   A 404 the CDN cached for a dataflow that did not exist yet also stays after the flip. Options:
   - Use `immutable` only on content-addressed URLs.
   - Give public URLs a short TTL with ETag revalidation.
   - Purge the CDN on release.

   Whichever is chosen, the specification also needs a stated TTL for 404s.
2. **The interrupted-release test contradicts §11.2.** Phase 5's test (line 221) expects an interrupted release to leave `current` on the old index. §11.2 step 2 flips `current` to the staged index. One of the two changes.
3. **The manifest skip conflicts with invariants 4 and 10.** Phase 4's builder skips manifests it has already parsed (line 211). Those invariants need artefact fingerprints and partial code sets, and neither the manifest summary nor the index carries them.
4. **`IndexEntry` carries one fingerprint.** The §7.4 result-store key needs two, the dataflow's structural fingerprint and the binding's table fingerprint.
5. **Q7 contradicts CUJ 6.** Q7's same-URN refusal (line 219) refuses exactly the in-place FMR constraint edit that CUJ 6 expects to publish with a new structural fingerprint. Choose one:
   - Refuse a same-URN change to a structure that is versioned and final.
   - Allow a same-URN change to a legacy version, and record it.

The same PR takes the specification housekeeping now listed as Phase 1 item 4: the Status line, the §6.3 citation of §7.4 for `pool` (pools are in §7.5), and the §4.0 citation of open question 9 (the question is 6).

### A2. Release 0.76, and land GH-221 before the tag

0.75.0 is already on `main`. Phase 1 should release 0.76.0, and every downstream pin should read `>=0.76,<0.77` (lines 165 to 169, 190 and 225).

The plan also has to say that the GH-221 work (items 5 to 7) lands before the tag. Phase 3's `vendor_closure` depends on it, under the pin Phase 2 sets.

Separately, the 0.75.0 release appears unfinished: there is no tag, no GitHub release, and a PyPI upload that could not be confirmed.

### A3. Put GH-221's API in the plan

Two things are deferred to the issue: line 181 leaves the call's shape to it, and nothing defines `fetch_bundle`'s call into it. The plan has to state four things:

- **The direction parameter**, and how `resolve` interacts with it.
- **The session cache is keyed by completeness and has no direction** (`_already_held`, `registry.py:535`). A cached `descendants` fetch would satisfy a later `all` request.
- **Sibling dataflows.** `references=parents` on the DSD returns them, and the plan has to say they are dropped or kept.
- **The `detail` value the fetch uses.**

Fix the citation too: the `descendants` literal is at `registry.py:125`, and the call to change is `_fetch.py:95`.

### A4. Keep one closure implementation

Phase 1 item 6 (`bundle/_build.py`) and Phase 3's `vendor_closure` both walk a dataflow's closure and cut partials, by different rules:

- Phase 1 cuts from stored availability.
- Phase 3 cuts from constraint codes.

Item 6 has no name, no signature and no issue. Its claim "any `RegistryReader`" cannot hold, because that protocol offers no incoming-reference walk.

Make one implementation that reuses `References.closure` (`bundle/_fingerprint.py`), with a single partial rule. Then either keep it in sdmxlib, so CUJ 6 works without statcatlib, or leave it in statcatlib and drop item 6. CUJ 6 also needs a goal, or it moves to Future Improvements.

### A5. Give every component a phase

The goals and tests depend on five components no phase builds:

- **The synthetic 7,000-bundle CODR-shaped corpus and its Zipf load driver.** Goal 1 and the Phase 5 to 9 verification use them (lines 25 and 255).
- **The pre-materialisation job.** The embargo argument at line 138 relies on it (spec §11.2 step 3).
- **The ingress resolver** that maps an SDMX request path to `bundle_id` for consistent hashing.
- **The serving list and the `ServingEntry` type**, including where the list is stored. Phase 4 consumes them (line 211).
- **The staged-key 404 test of CUJ 1.**

## 🟡 Majors

### B1. Plan Phases 0 to 3 in full, and outline Phases 4 to 9

Detailed items for Phases 7 to 9 have no endpoints, values or file formats. Specify Phases 0 to 3 to implementation level. For Phases 4 to 6 keep the contracts (types, documents, invariants) and the tests that gate each phase. Reduce Phases 7 to 9 to an outline, and re-plan them after Phase 6.

### B2. Add a phase, milestone and dependency table, and fix Alternative C

- **Milestone names.** M0 to M13 are cited before they are mapped to phases, and Phase 7 (M13) comes before Phase 8 (M11).
- **Alternative C.** Its deadlock argument does not hold. The chosen order has the same M9/M10 dependency and resolves it by moving the zero-failure test to Phase 6.
- **Goal 2.** It attributes the test of zero failed requests to M9, which runs before the router exists.

### B3. Decide where the index types live

The plan has sdmxlib-rest import statcatlib for `read_index`. That reverses the decision recorded at sdmxlib-rest `pyproject.toml:25-27`, and the plan does not say so. Two options:

- Publish the index types as a small package, or as JSON Schema with generated structs.
- Keep the statcatlib dependency, and record that reversal.

### B4. Use one blob protocol

The plan adds three storage protocols for one job of reading and writing objects by key:

- `ObjectStore` (Phase 5)
- `Fetcher` (Phase 6)
- `ResultStore` (Phase 6)

Spec §11.5 has pods pull with the deployment's own tool. Define one protocol, owned by one repository, that reuses sdmxlib's remote helpers (`bundle/_remote.py`). Otherwise, say why each protocol is separate.

### B5. Split Phase 2 and add its missing items

- **Contradictions to resolve:**
  - Q5 leaves `open_bundle(data=Auto())` open, but Phase 2 removes it.
  - statcatlib's `check_bundle` call passes `references=` (`bundle.py:883`), and sdmxlib's `check_bundle` now requires `at=`. Phase 2's `just ci` cannot pass without a fix.
  - The token-named search tables contradict the version guard at `lance.py:1126` and its test.
- **Missing contracts and files:**
  - The `open_bundle` and `publish_bundle` replacement contract.
  - `store/store.py`, which calls `from_connection`, is missing from the file list.
  - Removing `DataPolicy` leaves related functions and CLI commands behind.
- **Undefined behaviour:**
  - The serve-time overlay refusal has no point in the code to run from, and "folding" is not defined.
  - Nothing says whether `build_index` drops the search tables of a `draining` publication that pods still serve. This is a correctness risk.
  - The rule for reading an old statcat section format is missing.
- **Docs.** statcatlib has no `docs/upgrading.md`, so the item creates it.

Split the phase into 2a (adopt sdmxlib, the read-only open, the `check_bundle` fix) and 2b (token-named search tables, the overlay refusal).

### B6. Make the Phase 1 benchmark and cache decision runnable

- **The new benchmark would run in `just ci`.** `just bench-bundle-assertions` selects `bench and bundle and not recompute`, so `tests/bench/test_bundle_tier3.py` with `bench and bundle and remote` runs there. The `remote` marker is also unregistered, which fails collection under strict markers.
- **The measurement is not pinned down.** The plan has to state:
  - How the cold dataflow is chosen.
  - Where results are recorded.
  - The numeric threshold that triggers item 3.
  - Which DuckDB setting each of `"memory"`, `"spill"` and `None` sets.
- **The fixture recording is not described.** The steps that put FMR's probe artefacts into `tests/integration/fmr/12.4.2/` and record the cassettes are missing.

### B7. Define rollback, GC windows and idempotency

- **Rollback covers data only.** Pointing `current` back also has to restore structures, the search tables and the CDN.
- **Goal 5 measures the wrong failure.** It counts 404s, but a mixed-schema fleet fails with 503. Measure zero non-2xx responses.
- **GC folds two windows into one.** Goal 4 names a retention window and a backup window. GC takes one, and nothing tests either of these:
  - The race with an upload in progress.
  - GC after a rollback.
- **"Idempotent over its inputs" (line 110) is not shown.** Each run mints a new token. State per job what a re-run does.

### B8. Make the router and the result store follow the repository's rules

- **The router's LRU breaks the caching rules line 139 claims for every cache.** It is bounded by a count of bundles, where `docs/design/caching.md` asks for a size bound and single-flight attach.
- **The result-store key repeats code that exists.** It recomputes the overlay fingerprinting, and the key conflicts with the ETag sdmxlib-rest's middleware already sets.
- **sdmxlib-rest's own render version** has no name, location or bump rule.
- **Two targets do not exist.** sdmxlib-rest has no single-bundle `CatalogProvider` and no `/health` route for the new pieces to sit beside.

### B9. Fill the gaps in the Phase 3 and 4 contracts

- **Phase 3 is undefined in five places:**
  - Which constraint types count.
  - The rule for a hierarchy node whose parent the partial drops.
  - The type and values of `families`.
  - The errors each function raises.
  - The source of the CODR-shaped fixture.
- **Phase 4 is undefined in five places:**
  - The `fetch` callable's signature.
  - The degree of parallelism.
  - The `--serving` file format.
  - How the `--out` layout matches spec §6.2.
  - A release item.
- **Neither phase reuses existing code.** No public sdmxlib API parses manifest bytes. Phase 5 does not reuse `copy_bundle` or `bundle_files`.

### B10. Fix the structure and writing

- **Structure:**
  - Add a Risks & Mitigations section.
  - Promote Additional Resources and Future Improvements to H2.
- **Terms and naming:**
  - Define CODR, FMR, the tiers, pinned, partials, SAS and pre-materialisation at first use.
  - Use one term each for bundle, shard and publication, and for the release flip.
- **Overview.** Fix its inventory of remaining work (line 15 against line 154, for GH-160).
- **Writing style.** Rewrite the constructions CLAUDE.md bans, at lines 19 and 39 and in the Nits.

## 🟢 Nits

- **N1. Citations.**
  - The concrete `Codelist.partial` is at `codelist.py:668`, not `:239`.
  - `registry.py:118` should be `:125`.
  - Move line anchors into sibling repositories out of the plan and into the issues, where they can be kept current.
- **N2. Cross-repository issue references.** Write them as `StatCat-Suite/<repo>#N`. A bare `#46` reads as a GitLab sdmxlib issue under CLAUDE.md's rules.
- **N3. Altitude.** Full signatures and `Literal` choices belong in the issues. The plan keeps the contracts between phases.
- **N4. The diagram.** It labels built work as plan phases and leaves out the contracts.
- **N5. Undefined terms and values.**
  - Phase 6 and Phase 8 name no test conditions: no load, no N in "1/N of keys", no hit-rate threshold.
  - The `~` version syntax, "kind cluster" (it collides with `kind: structures`) and "account-level backup" are undefined.
- **N6. Search scope.**
  - Phase 7 has no goal.
  - Catalog-level search is not said to exclude staged entries.
  - Its tables are named by an index token that the index records, which is a circular dependency.

## Traceability matrix

Raw finding IDs:

- **S5** is `zoom_out_report.md`, with majors M1 to M4 and nits N1 to N6 in order.
- **S6** is `comprehension_report.md`, with blockers B1 and B2, majors M1 to M20 and nits N1 to N13.
- **S7** is `critic_report.md`, with its own B, M and N numbers.
- **S8** is `intermediate_zoom_out_report.md`, Z1 to Z12.
- **S9** is `implementation_readiness_report.md`, with its own B and M numbers. Its nits are by section.

| Item | Raw findings |
|---|---|
| A1 | S7-B1, S8-Z1, S8-Z11, S9-B4, S9-B6, S9-B7, S5-N6, S6-N13, S7-N6 |
| A2 | S9-B1, S7-M8, S6-M2, S8-Z2 |
| A3 | S6-B1, S9-B2 |
| A4 | S6-B2, S9-B3, S5-M1, S7-M7, S8-Z2 |
| A5 | S5-M4, S6-M11, S6-M15, S6-M20, S7-M5, S7-M6, S7-M10, S8-Z3, S9-B5, S9-B9 |
| B1 | S8-Z4, S9-B8, S9-M19, S9-M20, S6-M16, S6-M18, S6-M19 |
| B2 | S5-M3, S6-M1, S7-M3, S7-M4, S8-Z6 |
| B3 | S5-N1, S7-M9, S8-Z5, S9-M15 |
| B4 | S5-M2, S8-Z8 |
| B5 | S6-M3, S6-M7, S6-M8, S7-N3, S8-Z7, S9-M4, S9-M5, S9-M6, S9-M7, S9-M8, S9-M10, S9-M11 |
| B6 | S5-N5, S6-M4, S6-M5, S6-M6, S9-M1, S9-M2, S9-M3 |
| B7 | S6-M14, S7-M11, S7-M12, S7-M14, S8-Z9, S8-Z12 |
| B8 | S5-N3, S6-M16, S6-M17, S7-M13, S9-M16, S9-M17 |
| B9 | S6-M9, S6-M10, S6-M12, S6-M13, S9-M9, S9-M12, S9-M13, S9-M14, S9-M18 |
| B10 | S7-M1, S7-M2, S7-M15, S7-M16, S7-M17, S7-M18, S7-M19, S7-N7, S7-N8, S7-N9, S7-N10, S7-N11, S6-N1 to N4, S6-N7 |
| N1 | S9 accuracy nits, S5-N4, S8-Z10 |
| N2 | S7-N12, S9-M20 (bare numbers) |
| N3 | S5-N4, S8-Z10 |
| N4 | S7-N4, S6-N3 |
| N5 | S6-N5, S6-N8, S6-N10, S6-N11, S6-N12, S6-M13, S6-M19 |
| N6 | S5-N2, S6-M18, S7-M6, S9-M19 |

Findings not carried forward:

- S7-N1, N2 and N5 (wording in Alternatives A and B, link style) go into B10's rewrite.
- S5-N3 (two serving paths) is covered by B8's note that no single-bundle `CatalogProvider` exists.

## Suggested order of work

1. **A1 (a spec PR)** decides the CDN rule, the staged flip, the index contents and the same-URN rule.
2. **A2, A3 and A4 rebuild Phase 1** as the 0.76 release with GH-221's API decided and one closure implementation.
3. **Rewrite the plan at the smaller horizon of B1,** with the table from B2 and the missing owners from A5.
4. **Run a second Stage 9 pass** on Phases 0 to 3 only.
