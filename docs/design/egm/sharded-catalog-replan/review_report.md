# Consolidated review: the plan's third revision

Five reviewers read the first draft on 2026-11-02. Line numbers refer to that
draft, which was never committed. Each finding is listed once, merged across
reviewers, with what the revision did. Severity is the highest any reviewer
gave.

## Blockers

| # | finding | reviewers | outcome |
|---|---|---|---|
| B1 | Phase 3 builds structure products and republishes data products with `build_edition`, which builds a first edition only (`edition/_build.py:103-105`), seals it itself, and gathers structures only from dataflow closures | readiness, codebase | Fixed. Phase 3 starts with sdmxlib work: a walk and cut over an open builder, a structures-only fill, and a multi-edition `structures=` check. Alternative A restated. |
| B2 | The routing fields map dataflows to products and nothing maps a full artefact to its structure product, so `shard_for_artefact` and `/structure` routing have nothing to read | design | Fixed. The Q7 proposal adds `full_artefacts` on structure-product entries; Phase 6 tests `/structure` routing. |
| B3 | Phase 4's corpus and its tests need embargoed entries on blob storage, which nothing in Phase 4 writes (the fold is Phase 5) | design, readiness | Fixed. The corpus is sealed locally with embargo on and copied, `inventory/` included, through the blob writer; it is built once outside `just ci`. |

## Major

| # | finding | reviewers | outcome |
|---|---|---|---|
| M1 | The `place` event kind breaks the single-writer rule, needs `EventKind` and the root schema extended, and moves fields the spec places on `LeafEntry`; dropping `location` contradicts `Shard.location` (§7.3) | design, zoom out, readiness, codebase | Fixed. `pool` and `pinned` wait for Phase 6's choice; if they go in the leaf, the release job writes them. No event kind proposed. `location` stays unless the spec PR changes §6.3 and §7.3 together. |
| M2 | GC expiring roots, leaves and tombstones conflicts with §10.1 (GC reads `inventory/`) and with the single writer | design | Fixed. The release job writes `retire` versions; GC writes nothing; roots, leaves and tombstones are kept until a spec PR grants a delete. |
| M3 | Pre-materialisation as a step of the release job: the release job has no `results/` role; `FullInventory` in place of `EmbargoReader` is an unmarked spec deviation | design, codebase | Fixed. Its own job and identity, sequenced by the orchestrator; the deviation is marked and a spec PR amends §6.4, §10.1 and §11.2; under Q8 the serving package may use `EmbargoReader`. |
| M4 | Phase 7 needs blob storage and the release job, and an event kind to record its tables | design | Fixed. Depends on 3, 4 and 5; the kind is a spec PR. |
| M5 | Readiness drops across the hot pool at 08:30 when the pinned set changes | design | Fixed. The router adopts a version after attaching its new pinned editions; Risks row and Phase 6 test added. |
| M6 | Spec work in no phase: `supersede_pending`, `expunge`, CDN purge, `export(live_only)`, `rebuild-inventory`, `--find-newest-consistent`, the unrecorded-edition sweep, alerts, the staleness metric, §6.5 items 2 and 6 | design | Fixed. Each placed in Phase 5, 6, 8 or 9. |
| M7 | Phase 4 waits on all of Phase 3 and Q2, though only the corpus needs it; the 7,000-leaf timing is tied to Q1 | design | Fixed. Only the corpus waits; the read is timed at 7,000 products. |
| M8 | `check_edition(structures=)` takes one path, so any Q2 split breaks ingest | readiness | Fixed. Phase 3's sdmxlib work. |
| M9 | Phase 3 writer rules left implicit: no `kind` on `working_copy`, item-8 behaviour under embargo off, a batched seal, an item-4 deadlock | readiness | Fixed in part. `working_copy` gains the kind; the three cases are named for statcatlib's Phase 3 design to settle. |
| M10 | The 503 for another schema needs more than one `LiveInventory` | readiness | Fixed. One reader per schema, or an sdmxlib accessor Phase 6 asks for. |
| M11 | sdmxlib-rest tests cannot use statcatlib's writer | readiness | Fixed. They write versions with sdmxlib's rules. |
| M12 | `results/` GC in Phase 5 depends on Phase 6's key | readiness | Fixed. Moved to Phase 6, with a key index. |
| M13 | The Phase 5/6 seam is undefined: release-set format, the command's configuration, the shape of `statcat release`, refusal persistence, the retention schema, a testable access precondition | readiness | Fixed in part. The access check takes a replaceable listing and has a test; the rest is named for the phases' own designs. |
| M14 | `correct` has no signature or test; remote `Catalog.open` has no signature | readiness | Fixed in part. `correct(from_seq=, reason=)` from the RFC and a test; the remote signature is left to statcatlib's design. |
| M15 | The issues contradict the plan (statcatlib#51's `tests/support/codr.py`, sdmxlib-rest#70's flags, statcatlib#52's pod test) | readiness | Fixed in the plan, which says each issue is restated before its phase. The issue texts are the orchestrator's to change. |
| M16 | sdmxlib-rest has no `RENDER_VERSION`; its seams are new work | codebase | Fixed. "adds four seams"; a new constant with a golden-file test. |
| M17 | The field a server reads is `search.annotations.types` | codebase | Fixed. |
| M18 | `EmbargoReader` has no caller in Phase 4 | zoom out | Fixed. It arrives with its first caller, in Phase 5 (Q9) or Phase 6 (Q8). |
| M19 | A bespoke `ObjectStore` protocol duplicates `obstore`'s stores | zoom out | Fixed. `obstore` used directly, with a `fetch` adapter. |
| M20 | `statcat dev synthetic-catalog` is an invented command | zoom out | Fixed. A `statcatlib.testing` function and a `bench/` script. |
| M21 | Phase headings are not plain noun phrases; several sentences are garden paths; spec §18.1 is in the future tense though the gate was met; spec line 5 is overlong | style | Fixed. |

## Minor and nits

Fixed: `release_due(leaves, now)`; `render_version(media_type)`; `reason=` on
`rollback`; the CDN purge on rollback; Q5's leaf-to-leaf option dropped and an
in-process remap test added; the poll interval marked as proposed; the duplicate
statcatlib#47 reference; the question-number mapping to the spec and GH-333; the
`testing.py` file marked as modified; Phase 4's scope split into four parts;
GH-160's threshold; the style nits on sentence order, fragments and vague
nouns.

## Not fixed

| finding | reviewer | reason |
|---|---|---|
| Cut Terms, CUJs, Risks and the NFR rollback table as repeats of the spec | zoom out | The plan template has these sections and readers start from the plan. Terms now points at the spec and lists only what the plan leans on. |
| Merge Phases 8 and 9 | zoom out | They are separate issues (StatCat-Suite/sdmxlib-rest#50 and StatCat-Suite/sdmxlib-rest#51) and spec milestones (M11, M12), and Phase 9 drills commands Phase 8 does not build. |
| Renumber the open questions to the spec's numbers | zoom out | A mapping sentence carries it with less churn. |
| Update the hub's Q1 deadline ("before the re-plan") | zoom out | StatCat-Suite/statcat-suite.github.io#15 is closed, and the plan now holds the decision; the hub is outside this repository. |
| Drop the CODR generator's shape and the release-set detail | zoom out | The shape comes from StatCat-Suite/statcatlib#51's acceptance; command flags were dropped. |
| File the sdmxlib issues (Phase 3's builder, Phase 4's routing fields) and split StatCat-Suite/statcatlib#98 | design, readiness | Filing and editing issues waits for owner sign-off; the proposed texts go to the orchestrator. |
| Amend §10.1 (release job deletes `pending/`), §6.4, §11.2 and the `inventory/` delete role | design | Spec contract changes need their own PR; the plan names each where its phase needs it. |
| List the four `correct` defects | readiness | They are in statcatlib's Phase 2a review; the plan points at them. |
| Spec §6.4's `release_due(now=None)` signature drifts from `_rules.py:283` | codebase | Outside GH-332's passages; left for a spec fix. |
| Spec line 5's GH-184 release and §18.1's `0.80.0rc1` cannot be checked against the CHANGELOG | codebase | Outside GH-332's passages. |
