# Goldfish Review Report (Stage 8, Intermediate Zoom Out)

**Target:** `/Users/gabrielgellner/dev/sdmxlib/docs/design/sharded-bundle-catalog-plan.md` (`1-261`)
**Reviewer lens:** `egm-zoom-out` (one instance), using the earlier stage reports from stages 5, 6, 7 and 9.

**Verdict:** The plan's phase structure does not hold up against the earlier findings.
- Phases 1-3 are sound once corrected.
- Phases 4-6 depend on spec points that are unsettled or contradictory.
- Phases 7-9 have no goals behind them.

Counts: 3 Blocker, 6 Major, 3 Nit.

## Prioritized Findings

### Z1. Spec contradictions must be settled in a spec PR (Phase 0) - Severity: Blocker
* **Reviewer Lens:** `egm-zoom-out`
* **Affected Scope:** Plan lines 26-27, 75, 140, 211-213, 219-221, 230. Spec §7.4 (lines 419-428), §11.2 step 2 (line 530), §6.3/§6.4.
* **Discrepancy Description:** Five findings share one root cause, which is that the spec is wrong or silent:
  1. **CDN rule (critic B1).** §7.4 gives the CDN `immutable`, no invalidation, and a long TTL under URLs that stay the same across a release. Goal 2 (one-write visibility), Goal 3 (per-dataflow invalidation) and rollback (line 140) therefore hold only at the origin. A 404 that the CDN cached for a new dataflow stays after the flip.
  2. **Interrupted release (readiness B6).** §11.2 step 2 flips `current` to the staged index. The Phase 5 test (line 221) asserts that `current` stays on the old index.
  3. **Manifest skip (readiness B4).** Skipping manifests already parsed conflicts with invariants 4 and 10, which need artefact fingerprints and partial code ids that the index does not store.
  4. **One fingerprint (readiness M17).** `IndexEntry.dataflows` carries one fingerprint. The §7.4 key needs two: structural and table.
  5. **Q7 refusal (readiness B7).** The same-URN refusal blocks CUJ 6 and line 186.
* **Rationale & Recommendation:**
  - Add a **Phase 0: spec v5** docs PR that settles items 1-5, and make Phases 4-6 depend on it.
  - For the CDN: put `immutable` only on content-addressed URLs. Public SDMX URLs get a short TTL with ETag revalidation (the origin answers 304 by recomputing the key). State a TTL for cached 404s.
  - Restate Goals 2 and 3 and the rollback line as "visible at the origin at the put, and through the CDN within T".

### Z2. Phase 1 names a release that has shipped and bundles unrelated work - Severity: Blocker
* **Reviewer Lens:** `egm-zoom-out`
* **Affected Scope:** Lines 15, 99, 165-186, 190, 225.
* **Discrepancy Description:**
  - 0.75.0 has already shipped (PR #202). Schema 2.51, GH-12 and GH-213 landed after it, so the plan's release is 0.76, and the `<0.76` pins are wrong.
  - Phase 1 also ties the release to work it does not need:
    - the GH-185 bench and cache, which no consumer needs before Phase 6 or 8;
    - an unspecified `_build.py`, which duplicates Phase 3's `vendor_closure`.
* **Rationale & Recommendation:**
  - Make Phase 1 "sdmxlib 0.76": the current `main` plus GH-221, with GH-221's API decided in the plan (readiness B2 notes `_fetch_one` may be enough).
  - Pin Phases 2, 3 and 6 to `>=0.76,<0.77`.
  - Run GH-185 as its own track, gating only Phase 8's `hostPath` item.
  - Move `_build.py` to Future Improvements, or make `vendor_closure` the single closure implementation.
  - Pick one rule for cutting partials.

### Z3. Components the goals depend on are owned by no phase - Severity: Blocker
* **Reviewer Lens:** `egm-zoom-out`
* **Affected Scope:** Lines 25-26, 68, 138, 255.
* **Discrepancy Description:** Four components have no owning phase:
  - the synthetic 7,000-bundle corpus and Zipf load driver;
  - the pre-materialisation job, which needs sdmxlib-rest's render code;
  - the ingress resolver that maps an SDMX path to `bundle_id` (critic M10);
  - the serving list's store and `ServingEntry` (readiness B5).
* **Rationale & Recommendation:**
  - Put the corpus generator at the start of Phase 4, or in a Phase 3.5 that statcatlib owns.
  - Put pre-materialisation in Phase 6.
  - Make the ingress resolver a Phase 8 item, and state its behaviour during a flip.
  - Define the serving list in Phase 0 (spec §6.6) and build it in Phase 5.

### Z4. Shorten the planning horizon - Severity: Major
* **Reviewer Lens:** `egm-zoom-out`
* **Affected Scope:** Lines 159-247.
* **Discrepancy Description:** Phases 7-9 are not actionable, and Phase 7 has no goal (critic M7). The shape of Phases 4-6 depends on what Phase 0 decides.
* **Rationale & Recommendation:**
  - Specify Phases 0-3 in full.
  - For Phases 4-6, keep only the contract each hands the next, plus their gating tests.
  - Move Phases 7-9 to "Later phases (outline)", to be re-planned after Phase 6 measures cold start and first attach.
  - State that Phase 7 can slip without blocking Phases 8-9.

### Z5. Decide where the index types live before Phase 4 - Severity: Major
* **Reviewer Lens:** `egm-zoom-out`
* **Affected Scope:** Lines 105, 109, 116-120, 225.
* **Discrepancy Description:** Making sdmxlib-rest depend on statcatlib:
  - reverses sdmxlib-rest's recorded decision not to depend on statcatlib;
  - pulls lancedb into the serving image;
  - chains every sdmxlib version bump through a statcatlib release.
* **Rationale & Recommendation:**
  - Choose among a dependency-light `statcatlib[index]` or separate package, a JSON Schema contract checked against a shared fixture, or the full dependency.
  - Record the rejected options as Alternatives.
  - State the rule for propagating version pins across the three repositories.

### Z6. Phase ordering and Alternative C rest on a false deadlock - Severity: Major
* **Reviewer Lens:** `egm-zoom-out`
* **Affected Scope:** Lines 26, 128-132, 221, 231.
* **Discrepancy Description:**
  - The zero-failure flip test is already in Phase 6, so a per-repository order also works. Alternative C's rejection reason is therefore wrong.
  - The goals cite milestones whose tests live in other phases.
* **Rationale & Recommendation:**
  - Add a phase/milestone/dependency table.
  - Attribute each goal to the phase that measures it.
  - State Alternative C's real cost: the first end-to-end loop comes later.

### Z7. Phase 2 is larger than "small" - Severity: Major
* **Reviewer Lens:** `egm-zoom-out`
* **Affected Scope:** Lines 47, 126, 188-198.
* **Discrepancy Description:** Phase 2 also needs:
  - the `Catalog` construction in `store.py`;
  - the `check_bundle` `at=` API break;
  - removal of the data-path surface;
  - a redesigned version guard;
  - a token source for the search tables;
  - `docs/upgrading.md`, which is a new file.

  Alternative B's reasoning depends on Phase 2 being small.
* **Rationale & Recommendation:**
  - Split Phase 2:
    - **2a:** adopt 0.76, read-only open, and the `check_bundle` API.
    - **2b:** token-named search tables, which only Phase 5 needs.
  - Close Q5 as decided: break cleanly.

### Z8. Consolidate the storage abstractions - Severity: Major
* **Reviewer Lens:** `egm-zoom-out`
* **Affected Scope:** Lines 219, 227, 230.
* **Discrepancy Description:**
  - The plan adds three protocols (`ObjectStore`, `Fetcher`, `ResultStore`). Each has a local-directory implementation and none has a production implementation.
  - The spec's §11.5 has pods pull with the deployment's own tool.
  - sdmxlib already has `RemoteFile`, `verify_remote`, `content_md5`/`content_crc32c` and `bundle_files`.
* **Rationale & Recommendation:**
  - Define one minimal blob protocol in Phase 5 and name its production backend (obstore or fsspec).
  - Drop `Fetcher`, or state why the plan departs from §11.5.
  - Test conditional put against Azurite or MinIO.

### Z9. Rollback, GC and idempotency need definitions in Phase 0 - Severity: Major
* **Reviewer Lens:** `egm-zoom-out`
* **Affected Scope:** Lines 28, 29, 110, 140.
* **Discrepancy Description:**
  - Rollback covers data only. Schema and contract changes have no rollback.
  - The rollback put races a release's conditional put.
  - GC conflates the retention window with the backup window, and can delete publications after a rollback.
  - "Idempotent" (line 110) is false while each run mints a new token.
* **Rationale & Recommendation:** Settle these in Phase 0:
  - rollback for each change class;
  - both GC windows;
  - idempotency by content fingerprint or a caller-supplied token;
  - a writer lease per `bundle_id`.

### Z10. Detail sits at the wrong level - Severity: Nit
* **Reviewer Lens:** `egm-zoom-out`
* **Affected Scope:** Lines 169-197, 211.
* **Discrepancy Description:** The plan pins line anchors into sibling repositories, some already wrong (readiness M8 and M9). The contracts the next phase needs stay undefined: `ServingEntry`, the record shape and the search routes.
* **Rationale & Recommendation:** Move the anchors into the issues and put the contracts in the plan.

### Z11. Spec housekeeping is in Phase 1 - Severity: Nit
* **Reviewer Lens:** `egm-zoom-out`
* **Affected Scope:** Line 178.
* **Rationale & Recommendation:** Fold the Status-line edit and the §4.0 renumbering into the Phase 0 spec PR.

### Z12. Goal 5 measures the wrong failure - Severity: Nit
* **Reviewer Lens:** `egm-zoom-out`
* **Affected Scope:** Line 29.
* **Discrepancy Description:** A fleet running mixed schema versions fails with 503 responses, not 404 (critic M12).
* **Rationale & Recommendation:** Measure zero non-2xx responses, and state the §9.2 precondition that no old pod remains.

## Proposed restructure
- **P0:** spec v5, settling the CDN rule, the staged flip in §11.2, the index's data for invariants 4 and 10, the second fingerprint, the scope of the Q7 refusal, the serving list, rollback classes and the GC windows.
- **P1:** sdmxlib 0.76 with GH-221 decided. GH-185 runs as a separate track.
- **P2a / P2b:** statcatlib adoption, then token-named search tables.
- **P3:** partials and structures bundles, with one closure implementation.
- **P4-P6:** contracts only. The corpus generator comes early in P4 and pre-materialisation in P6.
- **P7-P9:** outline only, re-planned after P6.
