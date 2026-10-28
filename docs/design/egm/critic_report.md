# Goldfish Review Report

**Target:** `/Users/gabrielgellner/dev/sdmxlib/docs/design/sharded-bundle-catalog-plan.md` (lines 1-158)
**Stage:** 7, Critic Review
**Reviewers:** `egm-design`, `egm-structural`, `egm-style` (one instance each, run in parallel)
**Standards:** `resources/document_standards/feature-spec-standard.md` (design, structural); the "Writing style" section of `/Users/gabrielgellner/dev/sdmxlib/CLAUDE.md` (style)

**Counts:** 🔴 1 Blocker · 🟡 19 Major · 🟢 12 Nit (after deduplication: 36 style, 17 structural and 14 design raw findings)

Synthesizer notes:
- I checked the Blocker against the specification. `sharded-bundle-catalog.md` line 424 gives the CDN "`Cache-Control: immutable`; no invalidation", and line 428 puts the CDN under the public URL with a long TTL.
- `egm-design` states that the plan "has ... Risks". It does not: the plan has no Risks section, which is what `egm-structural` found (M1). I kept the structural finding.
- Findings that cite lines 159 and later are included only where they check a claim made in lines 1-158.

---

## 🔴 Blockers

### B1. The CDN cache breaks the release, delta and rollback goals - Severity: Blocker
* **Reviewer Lens:** `egm-design`
* **Affected Scope:** lines 26, 27, 75, 140 (specification §7.4, lines 424 and 428)
* **Discrepancy Description**:
  - The specification caches each response at the CDN under its public URL, with `Cache-Control: immutable`, a long TTL and no invalidation.
  - A getdata public URL is the same before and after a release, while the result key behind it changes.
  - A response cached before 08:30 is therefore still served after the `catalog/current` put. Goal 2 (one-write visibility) holds at the origin only.
  - Goal 3 has the same gap, because a delta leaves the dataflow's old bytes in the CDN.
  - Rollback (line 140) has the same gap, because pointing `current` back does not reach responses the CDN already holds.
  - No phase tests through a CDN.
* **Rationale & Recommendation**:
  - Settle this in the specification first, as line 34 requires for a format change. Options are a short TTL with ETag revalidation (the origin answers 304 from the key), versioned public URLs, or a purge on release.
  - Then restate Goals 2 and 3 and the rollback line as "visible at the origin and through the CDN within T", and add a test.

---

## 🟡 Major

### M1. No Risks & Mitigations section - Severity: Major
* **Reviewer Lens:** `egm-structural`
* **Affected Scope:** whole document
* **Discrepancy Description**: The standard requires failure modes, security implications, scalability limits and their mitigations. The plan has none of these as a section, and parts of them are scattered through the NFRs (line 138) and Q7 (line 49). These program risks are unlisted:
  - the M9/M10 test dependency (M4);
  - the undecided shape of GH-221's reference-direction API;
  - statcatlib's breaking move from 0.72 to 0.75;
  - the conditional block cache, and what Phase 8 does without it;
  - the hand-off of work to the sibling repositories' own maintainers (line 161).
* **Rationale & Recommendation**: Add an H2 "Risks & Mitigations" before the Non-Functional Requirements. Give each risk a row with its failure mode, its mitigation and the phase that owns it.

### M2. Additional Resources and Future Improvements are nested under the NFRs - Severity: Major
* **Reviewer Lens:** `egm-structural`
* **Affected Scope:** lines 144 and 152
* **Discrepancy Description**: Both are H3 headings under the H2 at line 136, so the mkdocs TOC nests them under the Non-Functional Requirements. The horizontal rules at lines 142 and 157 do not change the nesting.
* **Rationale & Recommendation**: Promote both to H2.

### M3. Phase numbers and milestone numbers are used without a mapping - Severity: Major
* **Reviewer Lens:** `egm-structural`, `egm-style`
* **Affected Scope:** lines 15, 25-30, 43-49, 96-101, 126, 128-132, 138-140
* **Discrepancy Description**:
  - The Goals and NFRs cite milestones (M9, M10, M11, M12). The Open Questions and Alternatives cite phases, and the diagram uses P1 to P9.
  - The mapping appears only in the phase headings from line 165 on, and it is not monotonic: P3=M6, P4=M8, P5=M9, P6=M10, P7=M13, P8=M11, P9=M12.
  - Phases 1 and 2 have no milestone.
  - Alternative C is titled "per milestone", but the phases do not follow the milestones one to one.
* **Rationale & Recommendation**:
  - Add a table after the diagram with the columns Phase, Repository, Milestone, Issues and Depends on.
  - Explain why M13 comes before M11.
  - Cite phases throughout, with the milestone in brackets.

### M4. Alternative C's deadlock argument does not hold, and the chosen order has the same dependency - Severity: Major
* **Reviewer Lens:** `egm-design`, `egm-structural`
* **Affected Scope:** lines 128-132; line 26; checked against lines 221 and 231
* **Discrepancy Description**:
  - Alternative C rejects a per-repository order because statcatlib's release job (M9) needs sdmxlib-rest's pods to test a zero-failure flip.
  - The chosen order also puts M9 (Phase 5) before M10 (Phase 6). It resolves the dependency by moving the zero-failure test to Phase 6 (line 231).
  - With that move, a per-repository order also works: statcatlib's Phases 2-5 and its half of Phase 7, then Phase 6 and sdmxlib-rest's half of Phase 7, then the deploy phases. The stated rejection reason is therefore false.
  - The move also hides that Goal 2's "zero failed requests (M9's tests)" is measured in Phase 6.
* **Rationale & Recommendation**: Give C's real cost, such as a later first end-to-end loop or less parallel work. State how the M9/M10 dependency is broken, and make Goal 2 cite Phase 6.

### M5. Goals are attributed to tests that no phase writes, or that lack conditions - Severity: Major
* **Reviewer Lens:** `egm-design`, `egm-structural`
* **Affected Scope:** lines 25, 26, 29, 139; checked against lines 213, 221, 231 and 255
* **Discrepancy Description**:
  - **Goal 1:** no phase builds the 7,000 synthetic CODR-shaped bundles or the Zipf load driver. Phase 4's 7,000-entry test is a counting fetch stub over index entries, yet line 255 assumes the corpus exists.
  - **Goal 1's targets have no conditions:** the 60 s cold start and the p99 first attach under 3 s do not state bundle or tier-1 size (MB up to the census's 501 MB), network, cold or warm node, or concurrency.
  - **"Holding only its routing slice"** has no test.
  - **Goal 2** cites M9, but the zero-failure and staged-never-attached tests are in Phase 6. The specification's M9 property "a staged key returns 404 from every pod" (CUJ 1, line 57) has no phase.
* **Rationale & Recommendation**:
  - Add a deliverable for the corpus and load generator, with an owner and a phase.
  - State the conditions for each target.
  - Add a routing-slice test and put the staged-key 404 test in a named phase.

### M6. The embargo claims rest on a job and tests no phase delivers, and two embargo paths are open - Severity: Major
* **Reviewer Lens:** `egm-design`, `egm-structural`
* **Affected Scope:** lines 27, 57, 138 (specification §11.2 step 2/3, §6.3, §10.1, §12)
* **Discrepancy Description**:
  - Line 138 says "Phase 6's tests and Phase 9's drills assert each of these". The record does not support this:
    - No phase builds the pre-materialisation job, which Phase 5 mentions only as a "hand-off". It must run sdmxlib-rest's render code on the pods' image.
    - Nothing tests that `results/` and `bundles/` are not publicly readable. That property is enforced by Phase 8's identities, and Phase 8 has no such test.
  - Two embargo paths are not addressed:
    - **The structures document:** nothing says the document built at release step 2 excludes URNs held only by staged publications. `shard_for_artefact` resolves through that document.
    - **Catalog-level search:** Phase 7 builds it from the union of `statcat.dataset` rows under the index token, which could include embargoed records.
* **Rationale & Recommendation**:
  - Assign the pre-materialisation job to a phase and a repository.
  - Require, and test, that the structures document and catalog search contain serving entries only.
  - Add an anonymous-read refusal test to Phase 8 or 9.
  - Correct the attribution on line 138.

### M7. CUJ 6 and Phase 7 have no goal - Severity: Major
* **Reviewer Lens:** `egm-structural`, `egm-design`
* **Affected Scope:** lines 23-30 against lines 83-87
* **Discrepancy Description**:
  - CUJ 6 (a bundle built from an FMR and a DuckLake) has no goal, so GH-221 and the `_build.py` entry point have no success criterion.
  - Phase 7 (catalog-level search and the search pool) also has no goal.
* **Rationale & Recommendation**: Add a goal for each with its measuring phase. For CUJ 6, the goal could be: the incoming constraint, hierarchy and association are present, and an edit to the constraint changes only that dataflow's structural fingerprint.

### M8. Phase 1 cuts the release before its own library changes land, against the `<0.76` pins - Severity: Major
* **Reviewer Lens:** `egm-design`
* **Affected Scope:** lines 15, 47, 99, 105 (checked against lines 169-184, 190, 203, 225)
* **Discrepancy Description**:
  - 0.75.0 is released "as it stands on main".
  - GH-185's cache, GH-221's `fetch_bundle` and `RestRegistry.get` change, and the new `_build.py` all follow the release, so they ship in 0.76.
  - Phases 2 and 6 pin `<0.76`, while Phase 3's `vendor_closure` and CUJ 6 need GH-221.
  - This contradicts line 105, "a consumer moves to a contract only after its producer has released it".
  - The diagram omits GH-221.
* **Rationale & Recommendation**: Either land GH-221 before the tag, or split Phase 1 into 1a (0.75 now) and 1b (0.76 with GH-221). Pin Phase 3 to the 1b release and show it in the diagram.

### M9. The index-reader placement reverses sdmxlib-rest's recorded "no statcatlib" dependency - Severity: Major
* **Reviewer Lens:** `egm-design`
* **Affected Scope:** lines 105, 109, 116-120 (checked against line 225 and `/Users/gabrielgellner/dev/sdmxlib-rest/pyproject.toml` lines 25-27)
* **Discrepancy Description**:
  - sdmxlib-rest's pyproject records that it has no statcatlib dependency, and that the catalog is reached through `CatalogProvider`. The plan adds statcatlib for `read_index` without acknowledging the change.
  - Both libraries cap sdmxlib at the next minor, so every sdmxlib bump needs a statcatlib release before sdmxlib-rest can move.
  - Phase 7's LanceDB routes pull `statcatlib[search]` into the single image (§9.2).
  - Alternative A considers only "put it in sdmxlib". It leaves out three options:
    - a thin index-types package;
    - a statcatlib extra;
    - a JSON Schema contract that sdmxlib-rest decodes itself, checked against a shared fixture.
* **Rationale & Recommendation**: State the reversal, argue against the thin-package and schema alternatives, and spell out the pin-propagation rule across the three repositories.

### M10. Ingress hashing on `bundle_id` needs a component no phase builds - Severity: Major
* **Reviewer Lens:** `egm-design`
* **Affected Scope:** lines 68, 139 (specification §7.5; Phase 8)
* **Discrepancy Description**:
  - Envoy Gateway cannot natively parse an SDMX path and look up a 7,000-entry index to find `bundle_id`. That needs ext_proc, Lua or WASM, or a front router that itself polls `current`.
  - During a flip, that router can route by the old index while the pods hold the new one.
* **Rationale & Recommendation**: Name the component and its repository. Define its refresh behaviour during a flip and its failure mode, and give it a phase and a test.

### M11. GC collapses two windows into one, and leaves an upload race and the rollback case untested - Severity: Major
* **Reviewer Lens:** `egm-design`, `egm-structural`, `egm-style`
* **Affected Scope:** line 28, line 140 (checked against lines 219-221; specification §10.2, §10.3, §11.4)
* **Discrepancy Description**:
  - Goal 4 names a retention window and a backup window, but `gc(store, *, retention)` takes one, and its test covers retention only.
  - Files uploaded at §11.1 step 6 are not yet recorded at step 7, so they appear in no indexed manifest. Only the age rule protects them.
  - After a rollback, publications referenced only by the abandoned newer indexes become unreferenced and eligible for deletion.
  - Line 28's wording can be read as "deletes nothing" (`egm-style`).
* **Rationale & Recommendation**:
  - Give `gc` both windows, and test that an uploaded but unrecorded file survives GC. Test GC after a rollback.
  - Rewrite line 28 as: "Garbage collection deletes a data file only if no publication in the retention window references it and the file is older than the backup window."

### M12. The rollback reasoning covers data only, and Goal 5 measures the wrong failure - Severity: Major
* **Reviewer Lens:** `egm-design`
* **Affected Scope:** lines 29, 77-81, 140
* **Discrepancy Description**:
  - "Point `catalog/current` back" covers neither a schema rollback nor a released contract format.
    - After the pods roll, old-schema entries answer 503, so the image must roll back too.
    - Rolling back index 1.0 or statcat section 3 means republishing.
  - A rollback put races a release's conditional put.
  - The CDN gap from B1 applies here too.
  - Phase 9 verifies restores with `check_bundle` only, and does not check that the old content is served.
  - Goal 5 counts 404s, while the failure mode of a mixed-schema fleet is a 503.
  - The §9.2 step 5 precondition, that no old pod remains, is not stated.
* **Rationale & Recommendation**: Define rollback separately for data, schema and library-contract changes. Measure Goal 5 as "zero non-2xx", and state the §9.2 precondition.

### M13. The router's LRU does not meet the caching rules line 139 claims for every cache - Severity: Major
* **Reviewer Lens:** `egm-design`
* **Affected Scope:** lines 69, 139 (checked against line 228; `docs/design/caching.md` rule 3)
* **Discrepancy Description**:
  - Rule 3 requires a bound in the unit being bounded. The LRU counts attached catalogs, while tier-1 sizes range from MB to 501 MB.
  - Evicted files stay on node-local disk with no bound.
  - CUJ 3 says "when the budget is reached" without naming the budget (`egm-style`).
* **Rationale & Recommendation**: Bound attached bytes against `memory_limit`, bound the node-local disk with its own eviction, and add a counting test. Name the budget in CUJ 3.

### M14. "Idempotent over its inputs" is unsupported - Severity: Major
* **Reviewer Lens:** `egm-design`
* **Affected Scope:** line 110; line 48
* **Discrepancy Description**:
  - `publish` and `index build` mint a new UUIDv7 token on each run, so a retry produces a new artefact.
  - Two concurrent publishes of one `bundle_id` fork the lake chain, which invariant 6 catches only at index build.
  - The `ObjectStore` protocol is exercised only by a local-directory implementation. No test runs the store-kept digests, `rename` or conditional-put semantics against a real store.
* **Rationale & Recommendation**:
  - Make the token an input, or define idempotency by content fingerprint.
  - Add a per-`bundle_id` lease or conditional write.
  - Name the real-store implementations and test them against Azurite or MinIO.

### M15. Terms and abbreviations are used before they are defined - Severity: Major
* **Reviewer Lens:** `egm-style`
* **Affected Scope:** lines 11, 25, 46, 47, 49, 57, 114, 138
* **Discrepancy Description**:
  - Line 11: CODR, .Stat Suite, "routing slice".
  - Line 25: "pinned", "first attach", "CODR-shaped", "Zipf traffic".
  - Tiers: line 46 says "tier-3" before any tier is defined. CUJ 2 defines tier 1 in a parenthesis, tier 3 is never defined, and the hyphenation varies.
  - Line 46: "partials", and "geography-first agent queries" (the "agent" is ambiguous).
  - Line 49: FMR and "legacy-versioned".
  - Line 57: "ETag-conditional put", and "staged key" (key of what).
  - Line 114: SAS.
  - Line 138: "pre-materialisation job". The sentence "runs in its own job" is circular.
* **Rationale & Recommendation**: Add a short terms list after the Overview, or gloss each term at first use. The style report has a rewrite for each. Use "tier 1" as the noun and "tier-1" as the adjective throughout.

### M16. Inconsistent terminology for core objects and actions - Severity: Major
* **Reviewer Lens:** `egm-style`
* **Affected Scope:** whole range; lines 26, 57, 69, 101, 131, 138
* **Discrepancy Description**:
  - "Bundle", "shard" and "publication" are used interchangeably, and "token" means both a publication token and an index token.
  - One action has five names: "`catalog/current` write", "put", "release flip", "the flip" and "`current` write".
  - Line 138 uses "attached catalog" for a DuckDB attached database. Everywhere else, "catalog" means the product catalog.
* **Rationale & Recommendation**:
  - Define the terms once in the Overview: a bundle is identified by `bundle_id`, a publication is one immutable build named by a token, and a shard is the publication the router routes to.
  - Pick one name for the flip.
  - On line 138, write "an attached database is readable by its alias".

### M17. The Overview's inventory of remaining work is inconsistent - Severity: Major
* **Reviewer Lens:** `egm-style`, `egm-structural`, `egm-design`
* **Affected Scope:** line 15; line 154
* **Discrepancy Description**:
  - "sdmxlib's part is built" conflicts with the three sdmxlib issues listed as remaining in the same paragraph.
  - GH-160 is remaining work on line 15 but deferred in Future Improvements (line 154), and no phase handles it.
  - The list leaves out Phase 1 (the release) and Phase 2 (statcatlib's adoption).
  - "GH-180 to GH-184 after [0.73.0]" is loose. CHANGELOG puts GH-180 to GH-183 under the untagged 0.75.0, and the specification §7.1 says GH-184 shelved most derived tables.
  - The paragraph packs about nine items into one sentence and ends on a colon reveal ("and GH-221: ...").
* **Rationale & Recommendation**: Say "sdmxlib's format and engine work" and list the remaining work as bullets by repository. Keep GH-160 in one place only, and state the release status accurately.

### M18. Ambiguous statements in the Open Questions and NFRs - Severity: Major
* **Reviewer Lens:** `egm-style`, `egm-design`
* **Affected Scope:** lines 48, 139
* **Discrepancy Description**:
  - Line 48, "names a workflow engine ... but no engine", reads as a contradiction.
  - Line 139, "remaps no more than 1/N of keys per added pod", leaves N undefined.
    - The bound is a ring-hash property. Bounded-load hashing deliberately spills keys under the load the test applies (`egm-design`).
    - The colon chain "M10's: ...; and M11's: ..." is hard to read.
* **Rationale & Recommendation**:
  - Rewrite line 48 as "requires a workflow engine with one writer per `bundle_id` (§7.6) and leaves the choice open".
  - Define N and state a tolerance for bounded-load spill, and split the sentence in two.

### M19. Constructions the repository's writing-style rules ban - Severity: Major
* **Reviewer Lens:** `egm-style`
* **Affected Scope:** lines 19, 39
* **Discrepancy Description**:
  - Line 19, "Nothing yet lists the shards, routes requests to them, ...", frames the work by what is missing.
  - Line 39, "is the producers' work, not this program's", is contrast by negation and ends the list on a twist.
* **Rationale & Recommendation**:
  - Line 19: "This program adds the four pieces that use the shard: a catalog index, a router, a batch release job and garbage collection of unreferenced files."
  - Line 39: "CODR ingestion. The CODR producers build the 7,000 real bundles."

---

## 🟢 Nits

### N1. Alternative B misstates the index builder's inputs and `IndexEntry`'s fields - Severity: Nit
* **Reviewer Lens:** `egm-structural`, `egm-design`
* **Affected Scope:** lines 124-126
* **Discrepancy Description**:
  - "Reads manifests only (§6.1)" contradicts §6.1 and Alternative A (line 119), which say the builder also reads `statcat.dataset` rows.
  - `IndexEntry` has no `tier` field, and `artefacts` is a count. "Copies" is therefore inaccurate.
  - "It would be built twice" is a punchline fragment with an unclear "It".
  - Phase 2 is called "small", although it is a breaking statcatlib release.
  - The alternative is a strawman, because building the index against manifest-2.0 JSON in parallel with Phase 2 is not considered.
* **Rationale & Recommendation**: Correct the inputs and fields, quantify Phase 2 (three issues), and consider the parallel option.

### N2. Alternative A lists a rejection reason as a con, and uses a judgement as a con - Severity: Nit
* **Reviewer Lens:** `egm-structural`, `egm-style`
* **Affected Scope:** line 119
* **Discrepancy Description**:
  - "sdmxlib-rest is the only router" is a reason for rejection, not a cost.
  - "sdmxlib becomes a serving framework" is a judgement.
* **Rationale & Recommendation**: Move the first to the rejection reason. State the second's concrete cost, such as "sdmxlib gains object-store polling and HTTP routing code".

### N3. Q5 is already decided by Phase 2 - Severity: Nit
* **Reviewer Lens:** `egm-structural`
* **Affected Scope:** line 47 (against line 192)
* **Discrepancy Description**: Phase 2 already removes `DataPolicy`/`Auto()`. In the same question, "moves it to the release Phase 1 produces in one step" has an ambiguous attachment (`egm-style`).
* **Rationale & Recommendation**: Record Q5 as decided, or make Phase 2 conditional on it. Write "moves statcatlib to sdmxlib 0.75 in one step".

### N4. The diagram mislabels the built work and omits contracts - Severity: Nit
* **Reviewer Lens:** `egm-structural`
* **Affected Scope:** lines 93-103
* **Discrepancy Description**:
  - "P1 0.75 release, GH-185" sits under the built features, so the built work reads as Phase 1.
  - GH-221 is missing.
  - The index 1.0 contract has no arrow from statcatlib to sdmxlib-rest.
* **Rationale & Recommendation**: Separate the built work from P1, add GH-221, and draw the index-contract arrow.

### N5. Inconsistent link style in Additional Resources - Severity: Nit
* **Reviewer Lens:** `egm-structural`
* **Affected Scope:** lines 146-150
* **Discrepancy Description**: GH-136 and statcatlib's `docs/bundle-format.md` are plain text, while the other entries are links.
* **Rationale & Recommendation**: Link GH-136, the published statcatlib page (https://statcat-suite.github.io/statcatlib/bundle-format/) and the milestone.

### N6. The specification's §4.0 cites a nonexistent open question - Severity: Nit
* **Reviewer Lens:** `egm-structural`, `egm-design`
* **Affected Scope:** line 43; specification line 99
* **Discrepancy Description**: The specification's §4.0 says "open question 9", but its §17 numbers that question 6. The plan's Q1 correctly cites 6.
* **Rationale & Recommendation**: Fix the specification in Phase 1 item 4's edit.

### N7. Further undefined terms - Severity: Nit
* **Reviewer Lens:** `egm-style`
* **Affected Scope:** lines 19, 74-75, 85, 97-99
* **Discrepancy Description**: These terms have no gloss: KEDA, Envoy Gateway, "applied overlays", "hook ids", "render versions", "closure", "token directories" and "check profiles".
* **Rationale & Recommendation**: Expand each at first prose use, or cite the specification section that defines it.

### N8. Minor negative-form and colon-reveal sentences - Severity: Nit
* **Reviewer Lens:** `egm-style`
* **Affected Scope:** lines 13, 21, 75, 138
* **Discrepancy Description**:
  - Line 13, "This document does not restate it", can be dropped.
  - Line 21, "The proposed solution is the specification's:", is a colon reveal.
  - Line 75, "the client never sees a storage URL", is negative form.
  - Line 138, "are not publicly readable", is negative form.
* **Rationale & Recommendation**: Restate each positively. For line 138: "`results/` and `bundles/` are readable only by the publish job's and the pods' identities (§10.1)".

### N9. Vague or unquantified wording - Severity: Nit
* **Reviewer Lens:** `egm-style`
* **Affected Scope:** lines 36, 81, 154, 155
* **Discrepancy Description**:
  - Line 36: "31 MB of the census `meta.duckdb`" needs the total.
  - Line 81: "moved" should be "changed".
  - Line 154: "many distinct bundles" needs a threshold.
  - Line 155: "becomes concrete" needs a named trigger.
* **Rationale & Recommendation**: Give the numbers and the triggers.

### N10. Hard-to-parse phrasing - Severity: Nit
* **Reviewer Lens:** `egm-style`
* **Affected Scope:** lines 45, 85
* **Discrepancy Description**:
  - Line 45, "builds the `kind: structures` mechanism with any split", is unclear.
  - Line 85, "a dataflow whose structures an FMR holds", puts the object before the subject.
* **Rationale & Recommendation**: Line 45: "builds `kind: structures` bundles so that any split is a configuration choice". Line 85: "a dataflow whose structures are held in an FMR".

### N11. Title, self-reference and abbreviation - Severity: Nit
* **Reviewer Lens:** `egm-style`
* **Affected Scope:** lines 1, 13, 110
* **Discrepancy Description**:
  - The title "the remaining program" does not name the deliverable, which the CLAUDE.md title rule requires.
  - The document calls itself "program", "plan" and "this document".
  - "GC" is used without being introduced.
* **Rationale & Recommendation**: Retitle to "Sharded bundle catalog: implementation plan for the remaining phases". Use one self-name, and write "garbage collection (GC)" at first use.

### N12. Bare cross-repository issue numbers (outside the range, for information) - Severity: Nit
* **Reviewer Lens:** `egm-style`
* **Affected Scope:** lines 188, 192, 223-230 (all outside 1-158)
* **Discrepancy Description**: Lines 1-158 follow the GH-N and `statcatlib#N` convention. The phase sections use bare `#47`, `#48`, `#69` and so on, which the CLAUDE.md convention reads as GitLab issues.
* **Rationale & Recommendation**: Prefix each with its repository, such as `statcatlib#47` or `sdmxlib-rest#69`.
