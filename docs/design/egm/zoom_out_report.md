# Goldfish Review Report

**Target:** `/Users/gabrielgellner/dev/sdmxlib/docs/design/sharded-bundle-catalog-plan.md` (`1-261`)
**Stage:** 5, Initial Zoom Out. **Reviewer:** `egm-zoom-out` (one instance). The reviewer read the spec through §13 and did not read §14 onward.

**Verdict:** the overall shape holds up. The plan defers to the spec, orders its phases by milestone dependency, and keeps the block cache behind a measurement. Counts: 0 Blocker, 4 Major, 6 Nit.

## Prioritized Findings (Blockers -> Major -> Nits)

### Duplicate closure-and-partials builder - Severity: Major
* **Reviewer Lens:** `zoom-out`
* **Affected Scope:** Lines 182-184 (Phase 1 item 6, `bundle/_build.py`), 202-203 (Phase 3 `vendor_closure`), 83-87 (CUJ 6)
* **Discrepancy Description**: Two components walk a dataflow's closure (GH-221) and cut partials with `Codelist.partial`, one in sdmxlib and one in statcatlib. Item 6 is a new public builder API with no filed issue. Only CUJ 6 justifies it, and no Goal (lines 23-30) measures CUJ 6.
* **Rationale & Recommendation**: Keep GH-221 in Phase 1 and have one closure-and-partials implementation. Either Phase 3's `vendor_closure` is that implementation, or sdmxlib owns it and statcatlib calls it. Drop item 6 or move it to Future Improvements. If CUJ 6 stays, add a Goal that measures it.

### Three separate storage abstractions - Severity: Major
* **Reviewer Lens:** `zoom-out`
* **Affected Scope:** Lines 219, 227, 230
* **Discrepancy Description**: The plan adds three storage protocols: `ObjectStore` (Phase 5, statcatlib), and `Fetcher` and `ResultStore` (Phase 6, sdmxlib-rest). Each has its own local-directory implementation. Spec §11.5 holds no blob client and has pods pull with the deployment's own tool.
* **Rationale & Recommendation**: Define one minimal blob protocol on the producer side (statcatlib, which sdmxlib-rest already depends on, line 225) and reuse it. If pod pulls need no protocol, say why. Name the cloud SDK explicitly (fsspec or obstore).

### Zero-failed-requests test is assigned to the wrong phase - Severity: Major
* **Reviewer Lens:** `zoom-out`
* **Affected Scope:** Lines 26, 131, 221, 231
* **Discrepancy Description**: Goal 2 (line 26) and Alternative C (line 131) say M9 tests a flip with zero failed requests. Phase 5 (M9, line 221) runs before the router exists, and its tests only check that `current` was written once. The load test of the flip is in Phase 6 (line 231).
* **Rationale & Recommendation**: Reword Goal 2 and Alternative C so they match the phases. Phase 5 tests the single write and the embargo at the store level. Phase 6 tests zero failed requests.

### Synthetic corpus has no owner - Severity: Major
* **Reviewer Lens:** `zoom-out`
* **Affected Scope:** Line 255, Phase 4 (line 213)
* **Discrepancy Description**: Verification and Goal 1 depend on a synthetic 7,000-bundle catalog with Zipf traffic. No phase lists the corpus generator or the load driver as a file to create, and Phase 4's test uses a counting fetch stub.
* **Rationale & Recommendation**: Add one item that builds the corpus and the load generator. Check first whether `synthetic_bundle` (spec §4.3) can be looped to produce it.

### sdmxlib-rest takes on all of statcatlib - Severity: Nit
* **Reviewer Lens:** `zoom-out`
* **Affected Scope:** Lines 105, 225
* **Discrepancy Description**: sdmxlib-rest depends on all of statcatlib to get `read_index` and a few msgspec types.
* **Rationale & Recommendation**: Confirm statcatlib's install footprint. If it is heavy (for example, LanceDB), ship the index types as a small module or extra with few dependencies.

### No Goal justifies search - Severity: Nit
* **Reviewer Lens:** `zoom-out`
* **Affected Scope:** Lines 233-237 (Phase 7)
* **Discrepancy Description**: No Goal measures catalog-level search, so nothing in the plan justifies Phase 7.
* **Rationale & Recommendation**: Add a Goal for search, or state that Phase 7 can slip without blocking Phases 8 and 9.

### Two serving paths - Severity: Nit
* **Reviewer Lens:** `zoom-out`
* **Affected Scope:** Line 229
* **Discrepancy Description**: `RoutedCatalogProvider` sits beside the single-bundle `CatalogProvider`, so two serving paths need maintenance.
* **Rationale & Recommendation**: Serve a single bundle as a one-entry index through the router, or state why both paths stay.

### Line anchors and signatures drop the plan's altitude - Severity: Nit
* **Reviewer Lens:** `zoom-out`
* **Affected Scope:** Lines 169-197, 211
* **Discrepancy Description**: The plan cites many `file.py:NNN` anchors (`bundle.py:405`, `lance.py:1280`, `cli/__init__.py:125`, `structure.py:244` and others). Line 194 spells out an f-string format, and line 211 gives the full `build_index` signature. Anchors into sibling repos go stale when those repos change.
* **Rationale & Recommendation**: Name the component and its behaviour. Move line numbers and signatures to the issues.

### Cache mode Literal is not tied to a measured outcome - Severity: Nit
* **Reviewer Lens:** `zoom-out`
* **Affected Scope:** Lines 172-175 (Phase 1 items 2-3)
* **Discrepancy Description**: The plan does not say which benchmark result selects `"memory"` and which selects `"spill"`.
* **Rationale & Recommendation**: State the deciding measurement for each mode. If the benchmark justifies only one mode, expose a bool.

### Spec status edit sits inside a code phase - Severity: Nit
* **Reviewer Lens:** `zoom-out`
* **Affected Scope:** Line 178 (Phase 1 item 4)
* **Discrepancy Description**: A status-line edit to the spec is listed as a code item.
* **Rationale & Recommendation**: Make it a separate docs commit.
