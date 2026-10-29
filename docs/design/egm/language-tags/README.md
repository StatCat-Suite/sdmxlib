# EGM review of the language-tags design

An Elephant-and-Goldfish Model review, run on 2026-10-29 in interactive mode,
of the first draft of [`language-tags.md`](../../language-tags.md), the design
for [GH-280](https://github.com/StatCat-Suite/sdmxlib/issues/280). The draft
has no implementation plan, so Stages 5 to 8 ran and Stage 9 did not.

| stage | report |
|---|---|
| 5, initial zoom out | [`zoom_out_report.md`](zoom_out_report.md) |
| 6, comprehension | [`comprehension_report.md`](comprehension_report.md) |
| 7, critic (design, structural, style) | [`critic_report.md`](critic_report.md) |
| 8, intermediate zoom out | [`intermediate_zoom_out_report.md`](intermediate_zoom_out_report.md) |

What came of it, in the revised design:

- The SDMX-JSON evidence is stated per version. 1.0 and 2.0 leave a key
  outside the pattern unchecked; 2.1 refuses one, and its pattern accepts
  either case. Stage 7 said 2.1 refuses `fr-CA`. The revision found that
  wrong, because 2.1's pattern is written `[A-Za-z]`.
- The tag grammar is the SDMX-JSON 2.1 pattern, so the four mixed-case
  grandfathered tags are refused.
- `display_tag` and the writers' `lang_case` are cut, and two texts of one
  tag are refused. Both are the owner's decisions; Stage 8 had suggested
  keeping the last of two texts.
- Store reads skip the check, and `check_bundle` reports a stored tag that
  is not normalised.
- The sections the standard requires are added: goals and non-goals,
  alternatives considered, risks and mitigations.
- The contract with statcatlib is stated as a precondition.

The reports are kept as written, so their findings and line numbers describe
the first draft.
