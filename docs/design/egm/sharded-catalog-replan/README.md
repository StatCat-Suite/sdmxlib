# EGM review of the plan's third revision

An Elephant-and-Goldfish Model review, run on 2026-11-02, of the first draft of
the third revision of
[`sharded-bundle-catalog-plan.md`](../../sharded-bundle-catalog-plan.md), which
re-plans Phases 4 to 9 against the specification's sixth revision
([GH-333](https://github.com/StatCat-Suite/sdmxlib/issues/333)), with the
specification's tracker and adoption-gate passages
([GH-332](https://github.com/StatCat-Suite/sdmxlib/issues/332)). Five reviewers
read the draft in parallel, against sdmxlib `9b4e933b`, statcatlib `27d022b`,
sdmxlib-rest `1ce79f1` and the hub at `2fce9f2`: design, implementation
readiness, codebase details, zoom out and style.
[`review_report.md`](review_report.md) merges their findings and records what
the revision did with each.

What came of it, in the revised plan:

- Phase 3 gains sdmxlib work. `build_edition` builds a product's first edition
  only and gathers structures from dataflow closures, so sdmxlib adds a walk
  and cut over an open builder, a structures-only fill by agency and family,
  and `check_edition(structures=)` over several structure editions.
- Phase 4's routing-field proposal gains `full_artefacts`, so a `/structure`
  request routes to its structure product; `pool` and `pinned` wait for Phase
  6, and no new event kind is proposed.
- The release job stays the only Inventory writer on a server: it writes the
  `retire` versions, GC only deletes, and pre-materialisation runs as its own
  job.
- Spec work no phase held is placed: `supersede_pending`, items 2 and 6 of
  §6.5, the unrecorded-edition sweep and the alerts in Phase 5; result-store GC
  and the CDN purge in Phase 6; `expunge`, `export(live_only=True)`,
  `rebuild-inventory` and the restore commands in Phase 9.
- Phase 4's corpus is sealed locally with embargo on and copied through the
  blob writer, so Phase 4 tests embargoed entries without the Phase 5 fold.
