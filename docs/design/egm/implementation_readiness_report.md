# Goldfish Review Report: Stage 9, Implementation Readiness Test

**Target:** `/Users/gabrielgellner/dev/sdmxlib/docs/design/sharded-bundle-catalog-plan.md` (lines 159-261; lines 1-158 read for context)

**Reviewers:** `egm-implementation-readiness` (IR), `egm-codebase-details` (CB), `egm-style` (ST), one instance each.
**Standard:** `/Users/gabrielgellner/.claude/plugins/cache/egm/egm/0.1.0/resources/document_standards/implementation-readiness-standard.md`; the "Writing style" section of `/Users/gabrielgellner/dev/sdmxlib/CLAUDE.md`.

**Verdict:** not ready. 9 Blockers, 20 Majors, 30 Nits.

- Phases 1-3 are mostly concrete. They have two explicit deferrals (GH-221's API shape, `_build.py`) and a release target that has already shipped.
- Phases 4-6 contradict the specification and depend on data the index cannot carry.
- Phases 7-9 are outlines.

**Synthesizer spot-checks (confirmed):**
- `CHANGELOG.md:5` is `## [0.75.0] - 2026-10-26`.
- `justfile:204` selects `-m "bench and bundle and not recompute"`.
- Spec §11.2 step 2 (`sharded-bundle-catalog.md:530`) flips `current` to the staged index.
- statcatlib `check_bundle` (`bundle.py:867`, `:883`) still passes `references=`, while sdmxlib's `check_bundle` (`_check.py:83`) requires `at=`.
- `model/codelist.py:239` is the protocol-level `partial`, and the concrete one is at `:668`.

---

## Blockers

### B1. Phase 1 releases a version that already exists; downstream pins resolve to the wrong release
* **Reviewer Lens:** CB (Blocker), IR (Major)
* **Affected Scope:** lines 165, 167-169, 188, 190, 225
* **Discrepancy Description:** "release 0.75.0 as it stands on `main`" cannot be done:
  - `pyproject.toml:3` is already `0.75.0`.
  - `CHANGELOG.md:5` already has `[0.75.0] - 2026-10-26`, from commit 47a0c38 `chore(release): v0.75.0`.
  - Everything the item credits to the release landed after that commit: schema 2.51 (GH-197), `Codelist.partial` (GH-12), and the breaking GH-213 order change (`docs/upgrading.md:22`).
  - Phase 1 items 5-6 (GH-221, `_build.py`) also land after it.
  - As a result, `sdmxlib>=0.75,<0.76` in Phases 2 and 6 resolves to a release without schema 2.51, `Codelist.partial` or the GH-221 closure that Phase 3's `vendor_closure` needs.
  - No local `v0.75.0` tag exists (packed-refs has only v0.73.0 and v0.74.0). Confirm whether 0.75.0 was published; if it was not, regenerate its CHANGELOG section.
* **Rationale & Recommendation:** Make Phase 1's release 0.76.0 and include GH-197, GH-12, GH-213, GH-221 and the optional cache. Pin Phases 2 and 6 to `>=0.76,<0.77`. Alternatively, split Phase 1 into 1a (0.75, released) and 1b (0.76 with GH-221), and name which statcatlib phase consumes which.

### B2. The GH-221 API shape is deferred ("its shape is decided in GH-221")
* **Reviewer Lens:** IR (Blocker), CB (Major)
* **Affected Scope:** line 181
* **Discrepancy Description:** The text leaves the `RestRegistry.get` signature to the implementer, which the Zero-Decision Rule forbids. Specific gaps:
  - **Session cache.** `completeness_of` (`api/session.py:46-58`) records completeness, not direction. A session that already holds a `descendants` fetch would satisfy an `all` request and drop the incoming constraints without any error.
  - **Sibling dataflows.** `references=parents` on the DSD also returns sibling dataflows that share the DSD. The plan does not say to drop them. If they stay, an edit to an unrelated dataflow moves this dataflow's structure fingerprint, which contradicts line 186.
  - **`detail` parameter.** `partial=True` is the default (`registry.py:787`) and sends `detail=referencepartial` (`registry.py:850`). Line 184 says FMR does not serve it, which implies `detail=full`, but the plan never says so.
  - **Wrong citation.** `registry.py:118` is the `_references_for` def. The `"descendants"` literal is at `registry.py:125-126`, and the call that changes is `bundle/_fetch.py:95` (`remote.get(flow, resolve=True)`).
  - **API default.** `fetch_bundle` defaults to `api="2.1"` (`_fetch.py:47`), and the plan does not say whether it changes.
* **Rationale & Recommendation:**
  - Fix the signature in the plan, either `resolve: bool | Sequence[type] | Literal["all"]` or a new `references: Literal["descendants", "parents", "all"] | None` keyword.
  - State the Session change for reference direction.
  - List the parent types kept (DataConstraint, HierarchyAssociation, MetadataConstraint) and say that sibling Dataflows are dropped.
  - State `detail` and the default `api`.
  - CB notes that `RestRegistry._fetch_one(..., detail, references)` (`registry.py:488-533`) already accepts any `references=` string, and `fetch_bundle` already uses private members (`_fetch.py:106`). GH-221 may not need a public API change at all, and the plan should decide that.

### B3. `bundle/_build.py` has no name, signature or input types, and contradicts Phase 3's partial rule
* **Reviewer Lens:** IR (Blocker), CB (Major)
* **Affected Scope:** lines 182-184, 203
* **Discrepancy Description:** Item 6 is "an issue to file after GH-221", with no function name, signature or return type. It also leaves open:
  - the type of the "observation source per dataflow";
  - how `bundle_id` is chosen.

  Two further problems:
  - **Partial-cutting rules.** Phase 1 cuts partials from stored availability (GH-194). Phase 3 (line 203) cuts by "codes its content constraints use". These are two rules for the same artefact.
  - **`RegistryReader`.** "Any `RegistryReader`" cannot supply incoming references: the protocol has only `get` and `__contains__` (`model/registry.py:102-123`).
  - **Closure reuse.** sdmxlib already defines the closure as `References.closure` (`bundle/_fingerprint.py:140-148`), and `structure_fingerprint` is computed over it. If `_build.py` or `vendor_closure` computes a different closure, the "different fingerprint for EXR only" property (lines 87, 186) no longer holds.
* **Rationale & Recommendation:**
  - Give the full signature, e.g. `build_bundle(registry, path, *, flows: Mapping[SdmxUrn[Dataflow], Callable[[], pa.RecordBatchReader]], bundle_id: str, previous: str | None) -> Manifest`.
  - Name the protocol that can answer incoming references, or require `RestRegistry`.
  - Require both `_build.py` and `vendor_closure` to use `References.closure`.
  - Pick one partial-cutting rule and use it in Phases 1, 3 and 6.

### B4. The index builder's manifest-skip cannot coexist with invariants 4 and 10
* **Reviewer Lens:** IR
* **Affected Scope:** lines 211, 213
* **Discrepancy Description:** `build_index` skips manifests whose sha it parsed for `previous`, but the checks still need data from them:
  - **Invariant 4** needs every entry's artefact fingerprints. The index keeps only an artefact count, and the structures document keeps only URN to (bundle_id, token).
  - **Partial-subset half of invariant 4** needs code ids and parents. These are in no manifest: `ArtefactEntry` holds urn, stability, partial and fingerprint (`storage/_manifest.py:280-295`), and spec §6.1 says the builder reads manifests only.
  - **Invariant 10** needs the previous partial's code set, which is not stored either.

  The test "7,000 entries, 200 changed, fetches exactly 200 manifests" therefore cannot pass with these invariants checked.
* **Rationale & Recommendation:** State where skipped entries' artefact fingerprints come from: a `previous_artefacts` parameter, a cache file beside the index, or a re-fetch. State where partial code ids come from, for example a `STATCAT_SECTION_FORMAT` 3 field defined in Phase 3. If either needs a format change, add the specification PR as a Phase 4 item, as the Non-Goals require.

### B5. The serving list and `ServingEntry` are undefined
* **Reviewer Lens:** IR
* **Affected Scope:** lines 211-212, 219, 221
* **Discrepancy Description:** `publish` ends by "record `staged`", and `release(store, tokens)` builds an index from the serving list. The plan never says:
  - where the serving list (spec §6.6) lives: a statcatlib DuckDB table, an object-store JSON file, or the `--serving <file>`;
  - what format the `--serving <file>` has;
  - what `ServingEntry` is (fields, struct type, module).
* **Rationale & Recommendation:** Define `ServingEntry` in `catalog_index.py` with the §6.6 fields. Fix its store, for example `catalog/serving.json` written with an ETag-conditional put. Specify how `publish` appends to it and how `release` reads it.

### B6. The interrupted-release test contradicts spec §11.2
* **Reviewer Lens:** IR (synthesizer confirmed at `sharded-bundle-catalog.md:530`)
* **Affected Scope:** line 221
* **Discrepancy Description:** The test says a release interrupted between the staged and serving index "leaves `current` on the old index". Spec §11.2 step 2 flips `current` to the staged index, so after an interruption `current` points to the staged index.
* **Rationale & Recommendation:** Pick one:
  - Assert that `current` is on the staged index and that no staged entry is routable.
  - Change the specification first so the staged index is written without flipping `current`.

### B7. The Q7 same-URN refusal blocks CUJ 6's expected publication
* **Reviewer Lens:** IR
* **Affected Scope:** lines 219, 221 (against CUJ 6 at line 87 and Phase 1 item 7 at line 186)
* **Discrepancy Description:** Phase 5 refuses a publication with an artefact whose fingerprint differs from the serving publication's at the same URN. CUJ 6 and line 186 require exactly that case to publish: a constraint edited in place in the FMR (same legacy `1.0` URN) gives the next publication a different fingerprint.
* **Rationale & Recommendation:** Scope the refusal, for example to full artefacts homed in a structures bundle and shared across entries (invariant 4's domain), so a constraint or association vendored into one product is exempt. Add a test that the allowed edit publishes.

### B8. Phase 7's search endpoints are undefined
* **Reviewer Lens:** IR
* **Affected Scope:** line 236
* **Discrepancy Description:** "the search pool's endpoints" names no paths, parameters, response schema, ranking contract or error codes. It also does not say how a request chooses between a bundle's tier-2 tables and the catalog-level tables.
* **Rationale & Recommendation:** List each route (method, path, query parameters, response model), the table-selection rule and the error codes.

### B9. No item builds the synthetic 7,000-bundle corpus
* **Reviewer Lens:** IR
* **Affected Scope:** lines 231, 255 (and Goals, line 25)
* **Discrepancy Description:** Phase 6's bench tests and the Verification Plan rely on "the synthetic 7,000-bundle catalog from Phase 4 on", but no Phase 4 item creates it. The plan gives no generator, bundle shape, Zipf parameters, size or location.
* **Rationale & Recommendation:** Add a Phase 4 item, e.g. `tests/fixtures/synthetic_catalog.py` or a `statcat` dev command. Specify the CODR-shaped dimensions, codelist sizes, Zipf exponent and output location, and a size parameter so CI does not build 7,000 bundles.

---

## Majors

### M1. The Phase 1 bench test would run under `just ci` and fails collection
* **Reviewer Lens:** CB, IR
* **Affected Scope:** line 172
* **Discrepancy Description:**
  - `just ci` runs `bench-bundle-assertions` (`justfile:231`), which selects `bench and bundle and not recompute` (`justfile:204`). A test marked `bench and bundle and remote` would be selected, so "stays out of `just ci`" is false.
  - `remote` is not a registered marker (`pyproject.toml:86-102`), and `strict = true` (`:74`) makes an unregistered marker a collection error.
  - The plan names no environment variable for "<object-store root>".
  - The tier's skip summary reports only skips whose reason names `$SDMXLIB_BENCH_BUNDLE` (`tests/bench/conftest.py:57`, `tests/bench/_bundle.py:63`), so a remote-root skip would print nothing.
* **Rationale & Recommendation:** Register `remote` and add `and not remote` to `justfile:204`, or say the test belongs in `just ci`. Name the variable (e.g. `SDMXLIB_BENCH_REMOTE`) and extend the skip summary to report it. Reuse the `tests/stores` emulators (`tests/stores/conftest.py:29`), `data_plane_registry` (`tests/bench/conftest.py:127`) and `discover_observations` (`tests/bench/_bundle.py:359`).

### M2. The block-cache build trigger and its reach are vague
* **Reviewer Lens:** IR, CB
* **Affected Scope:** lines 173-175
* **Discrepancy Description:**
  - "only if item 2 shows repeated reads re-fetch" has no threshold.
  - The DuckDB setting names are not verified anywhere in the repo.
  - A bundle opened without an engine gets its own engine (`local/registry.py:1489-1493`), so `cache=` must reach that path too.
  - Spec §7.1 (`sharded-bundle-catalog.md:378`) also names `cache_httpfs`, which item 3 does not consider.
* **Rationale & Recommendation:** State the threshold, e.g. "build it if the repeated read's GET count is above 0 with the cache on". Confirm the setting names against the pinned DuckDB. Cover the default-engine path. Say why `cache_httpfs` is or is not used.

### M3. Recording the FMR probe fixtures is unspecified and mixes two systems
* **Reviewer Lens:** IR, CB
* **Affected Scope:** lines 185-186
* **Discrepancy Description:**
  - `tests/integration/fmr/12.4.2/` already holds the FMR oracle's recordings (sent.xml, accepted.json, back/status.json), read by `tests/support/fmr_oracle.py:17-20`.
  - `fetch_bundle`'s VCR cassettes live in `tests/integration/cassettes/test_bundle_fetch_live/*.yaml`.
  - The plan does not say:
    - which system records the probes (`just record-cassette` or `just fmr-record`);
    - where the probe artefact definitions live;
    - what edit produces the second cassette;
    - what the tests are named.
  - The 3.0 case must pass `api="3.0"`.
  - Item 7 has no Create/Modify tag and no Type / Scope line.
* **Rationale & Recommendation:** Add a recording item that covers the probe SDMX-ML files, the recipe, the exact constraint edit and the cassette paths.

### M4. Phase 2's `open_bundle` and `publish_bundle` replacement contract is missing, and `store.py` is omitted
* **Reviewer Lens:** IR, CB
* **Affected Scope:** line 192 (also Q5, line 47)
* **Discrepancy Description:**
  - **Catalog construction.** `open_bundle` yields a `Catalog` that composes `LocalRegistry.from_connection(conn)` in `statcatlib/src/statcatlib/store/store.py:2928, 2994-3014, 3193`. Moving to `LocalRegistry.bundle` therefore changes `store.py`, which is not listed. The plan also does not say how `Catalog` is built over a `BundleRegistry`, or which connection runs the `statcat.*` SQL.
  - **`publish_bundle`.** It (`bundle.py:675-718`) never calls `LocalRegistry.build`; it takes a built directory and calls `write_manifest`. So "passes `previous=` to `LocalRegistry.build` ... as today" describes behaviour that does not exist.
  - **`azure_credentials`.** The parameter (`bundle.py:533`) maps to `BundleEngine(secrets=[StoreSecret])` (`local/engine.py:67`), and the plan does not say so.
  - **`bundle_uri`.** The plan does not say whether it now names `<bundle>/<token>/`.
* **Rationale & Recommendation:** Give the new `open_bundle` and `publish_bundle` signatures, the `Catalog` construction path, the place where the build step is added, and the credential mapping. Add `store/store.py` to the file list.

### M5. Phase 2 removes `DataPolicy` but leaves the rest of the data-path surface
* **Reviewer Lens:** CB
* **Affected Scope:** lines 192, 195
* **Discrepancy Description:**
  - These go with `DataPolicy` and are not listed: `inspect_bundle` (`bundle.py:342`), `read_current_data_path` (`:266`), `BundleInfo` (`:200`), `_resolve_policy` (`:492`), `__all__` (`:74-93`), and in the CLI `bundle explain` (`cli/__init__.py:187`) and `bundle attach-cmd` (`:261`).
  - `_require_bundle` (`:107-121`) and the build-index check (`:486-491`) require `meta.duckdb` at the bundle's top level, which is wrong once publications live in token directories.
* **Rationale & Recommendation:** List each removal or change.

### M6. statcatlib's `check_bundle` breaks against sdmxlib's current `check_bundle` API
* **Reviewer Lens:** CB (synthesizer confirmed)
* **Affected Scope:** Phase 2, lines 191-197
* **Discrepancy Description:**
  - statcatlib `check_bundle(path, *, references=True)` (`bundle.py:867`, `:883`) passes `references=`. sdmxlib's `check_bundle` (`bundle/_check.py:83-93`) requires `at=` and has no `references` parameter.
  - `check_catalog(root, manifest, reg)` (`bundle.py:817`) takes 3 arguments, while `Check` is `Callable[[Path, Manifest, BundleRegistry, Profile], ...]` (`_check.py:54`).
  - The CLI's `bundle check --references` (`cli/__init__.py:228-258`) is affected too.

  Phase 2 cannot pass `just ci` without these changes.
* **Rationale & Recommendation:** Add a Phase 2 item covering the new `at=` profile, the 4-argument `Check`, and the CLI flag.

### M7. The serve-time overlay refusal has nowhere to run
* **Reviewer Lens:** CB, IR
* **Affected Scope:** line 192
* **Discrepancy Description:** `open_bundle` takes no overlays. Serve-time overlays are applied by sdmxlib-rest's `OverlayAnnotator` (`sdmxlib-rest/src/sdmxlib_rest/overlays.py`). An index built with non-default annotations must pass `cat.index_annotations["types"]` (`bundle.py:897-900`). The plan names no exception type and no message.
* **Rationale & Recommendation:** Name the hook point, either an `overlays: Sequence[Overlay] = ()` parameter or a call in sdmxlib-rest. Give the `types` argument and the exception.

### M8. The token-named search tables contradict the version guard and cite the wrong lines
* **Reviewer Lens:** CB, IR
* **Affected Scope:** lines 194, 195, 197
* **Discrepancy Description:**
  - **Version guard.** `_MANIFEST_VERSION` is documented to read every version from 1 upward (`lance.py:70-76`), `unreadable_version` accepts `1 <= v <= _MANIFEST_VERSION` (`:1126`), and `test_version_1_is_still_read` (`tests/test_index_version_guard.py:62`) asserts it. Bumping to 3 therefore still reads version 2, but line 197 expects a refusal.
  - **Line citations.** `lance.py:1280` is the add-a-language path. The rebuild names its tables at `:1342` (`add_bulk`). The generation is read at `:1042`, written at `:1146` and `:1168`, and incremented at `:1331` and `:1356`.
  - **Drop step.** "drops tables named by any other token" already exists in `add_bulk` (`:1357-1359`) and `orphan_tables`/`discard_orphans` (`:1367-1388`).
  - **`build_index` and the CLI.** `_LanguageIndex.build_index` (`:913`) has no token parameter, and the CLI command `build-index` (`cli/__init__.py:378-379`) opens a directory with no token. The plan does not say whether the token is a new option or read from `sdmx._meta.publication_token` (`local/registry.py:2544`).
  - **Rebuild collision.** The plan does not say how a rebuild under the same token avoids colliding with the live tables, which today's generation swap prevents.
  - **`search/` under a new token.** `LocalRegistry.build(previous=)` copies only `meta.duckdb` and `lake.ducklake` (`local/registry.py:2482-2484`), so a new token's `search/` starts empty.
* **Rationale & Recommendation:**
  - State the new guard semantics, the change to `unreadable_version`, and the rewritten test.
  - Correct the line citations.
  - Name the token source and the rebuild rule.
  - Drop the redundant "drops" clause.

### M9. Phase 3's structures functions and refusals are undefined
* **Reviewer Lens:** IR, CB
* **Affected Scope:** lines 203-206
* **Discrepancy Description:**
  - **`structures_bundle`.** `families` has no type, and "the full artefacts" names no container. The plan does not say who builds and publishes the structures bundle, or how `kind` reaches `publish_bundle`.
  - **Hierarchy pruning.** The rule does not say what happens to a kept node whose parent is dropped. `Hierarchy` has no pruning operation (`model/hierarchy.py:240-257`); it arguably belongs in sdmxlib beside `Codelist.partial`.
  - **"Codes its content constraints use".** The rule does not cover include/exclude, wildcards or the absence of any constraint.
  - **Untested refusal.** No item implements the refusal that "a partial that lacks an ancestor is refused at build" tests: neither the function nor the exception is named.
  - **10 MB limit.** The plan does not say what is measured.
  - **Ingest citation.** `sdmx.py:210` is a docstring line. `ingest_sdmx_registry` is at `:203` and `ingest_sdmx` at `:182`; both call `local.ingest(registry)`. The plan does not say which changes or where `dataflows` comes from.
* **Rationale & Recommendation:** Specify each point, and add the refusal and where it raises to item 1.

### M10. The rule for reading an old statcat section is missing
* **Reviewer Lens:** IR
* **Affected Scope:** line 204
* **Discrepancy Description:** `STATCAT_SECTION_FORMAT` goes from 2 to 3. The plan does not say whether a format-2 section is read as `kind: product` or refused.
* **Rationale & Recommendation:** State the rule and its upgrading note.

### M11. statcatlib and sdmxlib-rest have no `docs/upgrading.md`
* **Reviewer Lens:** CB
* **Affected Scope:** line 198 (also NFR line 140, Q5)
* **Discrepancy Description:** `statcatlib/docs/` holds only `index.md`, `bundle-format.md`, `tutorial.md` and `search.md`, so the item is a Create, not a Modify. sdmxlib-rest has no upgrading page either, yet Phase 6 breaks its runtime API.
* **Rationale & Recommendation:** Mark the item Create, and add an upgrading page item to Phase 6.

### M12. Phase 4's builder inputs and outputs are underspecified, and Phase 4 has no release item
* **Reviewer Lens:** IR
* **Affected Scope:** lines 211-214 (and line 225)
* **Discrepancy Description:**
  - **Inputs.** The plan gives no `fetch` signature, no parallelism model, and no store-root argument from which `location` and `data_path` are resolved.
  - **Index metadata.** It does not say who mints the index token, `producer`, `created_at` or `previous_token`.
  - **Missing sources.** It does not name the source of `products` or of `dataflows[].stability`; the latter is absent from `BindingCount`.
  - **Formats.** `--serving <file>` has no format, and the source of invariant 3's `canonical_form` (`sdmxlib.bundle.CANONICAL_FORM`) is not named.
  - **Release.** Phase 6 (line 225) pins "the release Phase 4 produced", but Phase 4 has no release item.
* **Rationale & Recommendation:** Specify each input and add a Phase 4 release item with its version.

### M13. No public API parses manifest bytes
* **Reviewer Lens:** CB
* **Affected Scope:** lines 211, 227
* **Discrepancy Description:** The index builder and the pod's pull step get manifests as bytes. sdmxlib's only reader is `read_manifest(root: Path)` (`storage/_manifest.py:413-452`), and it contains the format-version gate.
* **Rationale & Recommendation:** Choose one:
  - Add a Phase 1 item that exports a bytes decoder that applies the same gate.
  - Say that statcatlib reimplements the gate.

### M14. Phase 5's publish, release and GC mechanics are incomplete, and existing helpers are not reused
* **Reviewer Lens:** IR, CB
* **Affected Scope:** lines 219-220
* **Discrepancy Description:**
  - **`publish` steps.** §11.1 steps 1-4 (copy the previous lake, ingest, build the index, publish the bundle) cannot run from a finished `bundle_dir`.
  - **Step order.** The parenthetical puts the upload before `check_bundle`, but §11.1 runs the check at step 5, before the upload at step 6 (`sharded-bundle-catalog.md:523-524`).
  - **`ObjectStore`.** The plan gives no method signatures, no `put_if_match` failure behaviour, no `rename` behaviour without HNS, and no production implementation.
  - **`release`.** It does not say whether staging and flipping are one call or two (§11.2 puts pre-materialisation and the 08:30 wait between them). "Pre-materialisation hand-off" names no mechanism.
  - **`gc`.** `retention` has no type, the plan gives no backup window although Goals line 28 names one, and the `results/` grace period has no value.
  - **CLI.** The arguments of `statcat publish`, `statcat release` and `statcat gc` are not listed.
  - **Reuse.** sdmxlib already has `content_md5`, `content_crc32c`, `RemoteFile` and `verify_remote` (`bundle/_remote.py:45-97`), plus `bundle_files` (`bundle/_copy.py:87`), for the "digest the store keeps".
* **Rationale & Recommendation:** Specify each point and reuse the `_remote.py` helpers.

### M15. Adding statcatlib to sdmxlib-rest reverses a recorded decision
* **Reviewer Lens:** CB, IR
* **Affected Scope:** line 225
* **Discrepancy Description:** `sdmxlib-rest/pyproject.toml:25-27` says "no statcatlib (the catalog is reached through the CatalogProvider seam)". The plan does not acknowledge this, or the lancedb dependencies the change would pull in.
* **Rationale & Recommendation:** Acknowledge the reversal, or define an import boundary or extra so that `read_index` imports without lancedb.

### M16. The router's runtime contract is incomplete, and two named targets do not exist
* **Reviewer Lens:** CB, IR
* **Affected Scope:** lines 227-229
* **Discrepancy Description:**
  - **Targets that do not exist.**
    - There is no "single-bundle `CatalogProvider`". `CatalogProvider` (`provider.py:73-86`) is a Protocol the consumer implements.
    - sdmxlib-rest serves no `/health`; `provider.py:25` says the consumer owns it.
  - **Per-request routing.** `get_runtime` (`runtime.py:211-215`) returns one app-wide runtime. Per-request routing also touches:
    - `get_registry` (`:218`), `get_meta_conn` (`:223`), `get_token` (`:238`) and `get_effective_token` (`:243`);
    - the `CatalogRuntime` token properties (`:118-184`);
    - the ETag middleware (`middleware/etag.py:70-88`);
    - the structure cache.
  - **Undecided behaviour.** The plan does not say how a request is mapped to a shard before dependency resolution, what `meta_conn` is in a shared engine (an embargo hazard), or how listings such as `/structure/dataflow/all/all/latest` behave.
  - **Pull and verify contract.**
    - `Fetcher` has no signature and no production implementation. Spec §11.5 (`sharded-bundle-catalog.md:552`) has pods pull with the deployment's tool and `sdmxlib bundle files`, and the plan departs from that without saying so.
    - The plan gives no node-local directory layout, no `stamps=` directory, and no poll interval.
    - It does not say what "verifies a new index" checks, and gives no pool or schema configuration.
    - The router item omits the prefetch of staged pinned entries that line 231 tests.
* **Rationale & Recommendation:** Specify each point, including how a request is mapped to a shard and how `all` listings behave, and correct the `CatalogProvider` and `/health` references.

### M17. The result-store key is incompletely defined and duplicates existing code
* **Reviewer Lens:** IR, CB
* **Affected Scope:** line 230
* **Discrepancy Description:**
  - **`RENDER_VERSION`.** It is a Mapping keyed by media type (`formats/render.py:27`), so the key must take the entry for the response's media type.
  - **"sdmxlib-rest's own render version".** It does not exist yet.
  - **Undefined inputs.** "Canonical query" has no normalisation rule, and "hook ids" names no seams.
  - **Write path.** The plan gives no write-on-miss path and no single-flight behaviour, both of which `caching.md` requires.
  - **Fingerprints.** The key needs both a structural and a table fingerprint, but §6.3's `IndexEntry.dataflows` has one `fingerprint`, and Phase 4 matches §6.3 field for field.
  - **Duplicated overlay code.** sdmxlib-rest already folds hooks and overlay digests per audience (`overlays.py:51` `_fingerprint`, `runtime.py:143` `annotator_fingerprint`), and sdmxlib has `overlay_digest` and `applied` (`bundle/_overlay.py:224-240`). If the digests are not taken of `applied(overlay, audience)`, keys collide across audiences.
  - **ETag clash.** `ConditionalGetMiddleware` already sets an ETag (`middleware/etag.py:88`), which conflicts with "the key as ETag".
* **Rationale & Recommendation:** Add `structure_fingerprint` to `IndexEntry.dataflows` through a spec PR, or read it from the pulled manifest. Name the render-version constant, its location and its bump rule. Reuse the existing overlay fingerprinting. Resolve the ETag precedence.

### M18. Phase 6's `referencepartial` change names no code source, and its citations are imprecise
* **Reviewer Lens:** IR, CB
* **Affected Scope:** line 226
* **Discrepancy Description:**
  - The plan does not say which code ids `Codelist.partial` is called with (see B3).
  - **Citations.** `:256` is the Literal member and `:259-260` the buckets. The `hydrate` trigger at `structure.py:527` also keys on `referencepartial`, and `structure.py:376-378` documents a 501.
  - **`routes/availability.py`.** It has no `detail` parameter (it uses `references`, `:345`), so the change there is unclear.
* **Rationale & Recommendation:** State the code source and each file's change.

### M19. Catalog-level search creates a circular dependency on the index token
* **Reviewer Lens:** IR
* **Affected Scope:** line 235
* **Discrepancy Description:**
  - The tables live under `catalog/search/<index token>/` and are recorded in that index's `extensions.statcat`, so the token must exist before the index is written.
  - The function signature, its caller and the record schema are missing.
  - The item opens structures bundles' codelists, which goes beyond "reads manifests only".
* **Rationale & Recommendation:** Specify the order: mint the token, build the search tables, then write the index. Give the signature and the record schema.

### M20. Phases 7-9 are not actionable, and the Phase 1 title and bare issue numbers mislead
* **Reviewer Lens:** IR (Phases 7-9), ST (title, issue numbers)
* **Affected Scope:** lines 165, 188, 192, 208, 223, 226-230, 237, 242, 247-248
* **Discrepancy Description:**
  - **Phases 7-9.**
    - No test files are named.
    - `deploy/` lists no files, replica counts, KEDA thresholds, probe timings or image.
    - The kind harness names no load tool and no remap metric, and the drills name no script language.
    - "Hit rate recorded" has no pass criterion.
    - An account-level backup restore cannot run on kind.
  - **Issue numbers.** Bare `#47`, `#48`, `#10` and similar mean GitLab sdmxlib issues in this tree (CLAUDE.md, "Issue references"). The repo prefix on the first number of a list does not carry to the rest.
  - **Phase 1 title.** It omits GH-221 (items 5-7).
* **Rationale & Recommendation:**
  - Enumerate the Phase 7-9 files and parameters, and state how the backup is emulated (e.g. a copied directory).
  - Prefix every number, e.g. `statcatlib#47` or `StatCat-Suite/statcatlib#47` (the form GitHub links), consistently, including line 35.
  - Retitle Phase 1 as "sdmxlib 0.76, the tier-3 measurement (GH-185) and incoming references (GH-221)".

---

## Nits

### Accuracy and reference fixes (CB, IR)
1. **Line 169.** `model/codelist.py:239` is the protocol-level `partial`; the concrete `Codelist.partial` is at `:668` (helper `partial_ids` at `:829`; lazy version at `storage/lazy.py:708`).
2. **Line 169.** `just release-check-upgrading` takes a `VERSION` argument (`justfile:639`); name it.
3. **Line 178.** Name the exact Status-line sentence to replace. The Status line (`sharded-bundle-catalog.md:3`) also says "released in 0.73.0" and "Written against sdmxlib 0.72.1", and both are stale.
4. **Line 188.** Map statcatlib#46, #47 and #48 to the items they cover.
5. **Line 195.** The CLI command is `build-index` (`cli/__init__.py:378`); `build_index` is the function. `bundle rebind`'s decorator is at `:124` and its def at `:125`.
6. **Line 211.** `build_index` already names `Catalog.build_index`, `LanceIndex.build_index` (`lance.py:1607`) and `_LanguageIndex.build_index` (`:913`). Choose a distinct name for the catalog index builder.
7. **Line 214.** `docs/bundle.md` is sdmxlib's page; statcatlib's equivalent is `docs/bundle-format.md`.
8. **Line 228.** `BundleEngine.release` already detaches `__lake` and `__meta` when the last holder goes (`local/engine.py:163-174`). On eviction, close the registry instead of issuing `DETACH`.
9. **Line 230.** `overlays.py:1` cites sdmxlib-rest GH-10 as the OverlayAnnotator issue; confirm that #10 is the result-store issue.
10. **Line 255.** sdmxlib-rest has no `just test-integration`; its `ci` is `check test docs-build` (`justfile:146`).
11. **Lines 251-255.** sdmxlib's `just ci` also runs `test-stores` (container emulators). State whether Phase 1 needs it.

### Style (ST)
12. **Line 161.** "Nine phases." is a fragment, and "their tracker" has an unclear referent. Write "The plan has nine phases", and name the tracker.
13. **Line 172.** "skipped with a printed reason without a remote root" should be "skipped, with a printed reason, when no remote root is set".
14. **Line 178.** "State them built" should be "mark them built".
15. **Lines 185-186.** Item 7's sentence has no main verb, and the item lacks a Type / Scope line.
16. **Line 186.** "elsewhere" should be "for every other dataflow".
17. **Line 192.** "...default go" should say "are removed".
18. **Line 192.** "folding stays the fix" alludes to a mechanism without naming it; state it.
19. **Line 198.** The noun phrase has no action; write "entries for the removed `DataPolicy`, `bundle rebind` and generation-named search tables".
20. **Line 203.** `Codelist.partial(codes its content constraints use)` puts English inside a code span; write `Codelist.partial(code_ids)` and define `code_ids`.
21. **Line 219.** A sentence of about 130 words covers three operations; split it into one sentence each for `publish`, `release`, `gc` and `ObjectStore`.
22. **Line 231.** "leaves the others'" drops its noun; write "leaves every other dataflow's key unchanged".
23. **Line 237.** "(no fan-out)" frames the test by absence; write "with one query against the catalog-level table".
24. **Line 255.** "from Phase 4 on" can be read two ways; write "built in Phase 4 is the fixture for Phases 5 to 9".
25. **Line 251.** "where docs change" should be "in each repository whose `docs/` a phase changes".
26. **Lines 259-261.** The provenance comment and the italic line say the same thing, and "EGM" is never spelled out; keep one line.

### Remaining minor observations
27. **Line 175.** The cache is "keyed by object path". Confirm that DuckDB's external file cache keys by path and not by path plus etag, or the no-invalidation claim needs a caveat. (Synthesizer, derived from CB's "setting names unverified".)
28. **Line 181.** The FMR 12.4.2 behaviour claims cannot be checked from code. Cite the probe recording once M3 adds it. (CB)
29. **Line 225.** Name the statcatlib version that replaces "the release Phase 4 produced", tied to M12. (IR)
30. **Line 227.** `check_on_open(path, stamps=...)` needs a named stamps directory, tied to M16. (CB)

---

## Notes
- CB verified most references as correct. Those include `fetch_bundle` (`_fetch.py:38`), `BundleEngine` (`engine.py:55`), `LocalRegistry.bundle` and `LocalRegistry.build`, `check_on_open`, `bundle_files`, `RENDER_VERSION`, `SCHEMA_VERSION = "2.51"`, and the manifest 2.0 fields. In statcatlib they include `open_bundle:528`, `_write_data_path:405`, `normalize_lake_data_path:417`, `publish_bundle:675`, `overlay_affects_search:886`, `STATCAT_SECTION_FORMAT = 2`, `_TABLE_PREFIX`, `generation:318` and `_MANIFEST_VERSION = 2 (:76)`. The spec cross-references (§6.4's ten invariants, §11.1's seven steps, §7.3, §7.4) also check out.
- Items 27-30 are cross-references from the synthesizer's triage of the Major findings. They carry no new evidence.
