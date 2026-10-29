# EGM review of the sharded bundle catalog plan

An Elephant-and-Goldfish Model review, run on 2026-10-28 in consolidated mode
over all five stages, of the first draft of
[`sharded-bundle-catalog-plan.md`](../sharded-bundle-catalog-plan.md). That
draft was never committed: the line numbers in these reports refer to it, and
the plan on `main` is the second revision the review led to.

| stage | report |
|---|---|
| 5, initial zoom out | [`zoom_out_report.md`](zoom_out_report.md) |
| 6, comprehension | [`comprehension_report.md`](comprehension_report.md) |
| 7, critic (design, structural, style) | [`critic_report.md`](critic_report.md) |
| 8, intermediate zoom out | [`intermediate_zoom_out_report.md`](intermediate_zoom_out_report.md) |
| 9, implementation readiness (readiness, codebase details, style) | [`implementation_readiness_report.md`](implementation_readiness_report.md) |
| all five, merged into 5 blockers, 10 majors and 6 nit groups | [`consolidated_review_report.md`](consolidated_review_report.md) |

What came of it:

- The specification's fifth revision settled the contradictions the review
  found (PR #222): CDN revalidation on a short TTL, each partial's
  `cut_from`, an index builder that reads every manifest, and an interrupted
  release leaving the staged index current.
- The plan was rewritten at a smaller horizon: Phases 1 to 3 in full, 4 to 6
  as contracts and gating tests, 7 to 9 as outlines (PR #225).
- Phase 1 was built and released in sdmxlib 0.76.0: GH-221, GH-223, GH-224
  and GH-228, with the tier-3 measurement (GH-185).

The reports are kept as written, so their findings describe the first draft.

## Drift report on the EGM contracts

[`2026-10-28_DRIFT_REPORT.md`](2026-10-28_DRIFT_REPORT.md) compares the 23
`EGM.md` contracts the EGM bootstrap wrote with the code, before their first
commit. Six reviewers found about 110 differences; the contracts were
corrected from them, and four findings that point at the code were filed as
GH-262, GH-263, GH-264 and GH-265.
