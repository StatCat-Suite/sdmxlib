# Stage 7: critic review, language tags (GH-280)

*   **Target:** `docs/design/language-tags.md`, lines 1–167
*   **Reviewers:** `egm-design` and `egm-structural` (both against
    `feature-spec-standard.md`), and `egm-style` (with CLAUDE.md's writing
    rules); each in a fresh context
*   **GoldfishContext:** as Stages 5 and 6
*   **Date:** 2026-10-29

## Verdict

Not ready. Two central arguments rest on a misreading of the SDMX-JSON
schemas. The standard's required sections (goals and non-goals, alternatives,
risks) are missing, and several behaviours an implementer needs are
unspecified.

The tag in brackets names the reviewer: [D] design, [S] structural,
[St] style. Stage 5 and 6 cross-references give the earlier finding.

## Blockers

### B1. The SDMX-JSON evidence is wrong per version [D1, S3, St-M1; Stage 5 F3, Stage 6 M1]

Lines 34–36, 53–54, 65–68 and 77–80. Three findings:

- **SDMX-JSON 1.0 and 2.0 accept any key.** `localisedText` has only
  `patternProperties`, so a key that misses the pattern (`fr-CA`, `fr_CA`,
  `""`) is unconstrained. Only SDMX-JSON 2.1 adds
  `"unevaluatedProperties": false` and refuses it. So "both SDMX schemas
  refuse them" (line 54), "SDMX-JSON refuses it" (line 78) and "`fr-CA` is
  invalid" (line 36) are false for 1.0 and 2.0.
- **The grandfathered branch is mixed case.** It lists `en-GB-oed`,
  `sgn-BE-FR`, `sgn-BE-NL` and `sgn-CH-DE` exactly, and their lowercase
  forms match no branch. So D2's "lowercase is valid in every SDMX format"
  is false for tags D1 accepts, and SDMX-JSON 2.1 would refuse what sdmxlib
  writes for them.
- **D2 and D3 contradict each other.** D2 says no second spelling exists
  "in a written file"; D3's `lang_case="canonical"` writes one.

*Fix:* restate the evidence version by version. Refuse the four grandfathered
tags in D1, or name them as D2's exception. Narrow D2's claim or drop D3's
writer option.

### B2. No Alternatives Considered section [S1, D8]

The standard fails a spec that does not show which other designs it weighed
and why it rejected them. The design never weighs:

- the main alternative: keep tags as given and compare case-insensitively
  at lookup (statcatlib's brief frames matching as "exactly and
  case-insensitively");
- canonical-case storage, which GH-280 asks for;
- repair rather than refusal (statcatlib's decision 2);
- a `SCHEMA_VERSION` bump.

D5 lists `und` and `en` but gives no reason for rejecting `en`.

*Fix:* add the section, or a "Rejected" part in each decision.

### B3. No Risks and Mitigations section [S2, D8]

The design has no section for risks. It never mentions:

- untrusted input through the grammar regex (needs a bounded length, a
  pattern with no catastrophic backtracking, and a truncated tag in the
  `ValueError` message);
- the cost of normalising requested tags on hot lookup paths (`get`,
  `__getitem__`, `__contains__`, `code_map` over millions of items);
- one bad tag refusing a whole, possibly million-item, artefact.

*Fix:* add the section.

## Majors

### M1. No Goals and Non-Goals section [S4, D8]

The non-goals are scattered: IANA validity and deprecated subtags (D1),
lenient locale parsing (open question 1), and the projection column names,
which are statcatlib's. The problem statement (lines 5–11) has no heading.

### M2. Strict refusal goes beyond the SDMX-ML schema [D3]

D5 justifies refusal as matching the schema. But `xs:language`
(`[a-zA-Z]{1,8}(-[a-zA-Z0-9]{1,8})*`) is looser than RFC 5646: `e`, `en-a`
and `abc-12345678x` are schema-valid and not RFC 5646 well-formed. So
sdmxlib would refuse schema-valid SDMX-ML, which is a new strictness, not
"as it refuses any other schema contradiction".

*Fix:* separate schema-invalid (refuse) from schema-valid but not RFC 5646
(decide explicitly), and say whether refusal is per artefact or per text.

### M3. A collision after normalisation is not specified [D4; Stage 6 M2]

Some inputs collapse to one key: `InternationalString(**{"fr-CA": a,
"fr-ca": b})`, or two SDMX-ML texts tagged `FR` and `fr`. Today they are
distinct, and afterwards one is lost, last value winning.

*Fix:* decide between a `ValueError` (or a refusal) and a defined winner,
and list it under Breaking changes.

### M4. Old stores answer differently by path, with no signal [D5; Stage 6 M3]

Python reads rebuild through `InternationalString` and come back normalised,
while SQL lookups fed a normalised `lang=` still miss a stored `FR-CA`. A
store holding `""` or `fr_CA` keys (D5's old behaviour) would fail to read.
The "no bump" argument rests on a sweep that covers SDMX-ML only.

*Fix:* add a cheap check, on open or in `check_bundle`, for any key that is
not normalised (`lang <> lower(lang)`, plus the refused forms), which warns
or refuses; or justify not having one.

### M5. Membership and indexing change their contract [D6, S-N10; Stage 5 F6, Stage 6 M6]

`"fr_CA" in s` would raise where it returned `False`, `__contains__` takes
any `object`, and `s["bad"]` would raise `ValueError` where `try/except
KeyError` callers expect `KeyError`.

*Fix:* `__contains__` returns `False` for a malformed or non-string key, and
`__getitem__` raises `KeyError` chained from the `ValueError`. List both
under Breaking changes.

### M6. The "every way in" table is incomplete [D7, St-M5; Stage 6 M4, M5]

It misses:

- `AnnotationURL.lang` (`_common.py:534`, no default);
- the optional `RegionValue.lang` (the converters must accept `None`);
- the SDMX-ML header texts (`_header.py:224`);
- SDMX-JSON `meta.contentLanguages` (`data_v2.py:165`);
- SDMX-JSON 2.1.

The `lang=` row is an open list ("about fifteen"). The SDMX-ML "Today" cell
misquotes `SCHEMA_DEFAULT_LANG` as `"en"`.

*Fix:* enumerate the ways in, including `None`, and state the `lang=` rule
with a test that lists the public parameters.

### M7. The cross-repo section misstates and under-reports the differences [D9, S5, St-M4; Stage 5 F2, Stage 6 M8]

- **The source is GH-280, not the brief.** Line 154 attributes "canonical
  case as the stored form" to statcatlib's brief. The brief says "store tags
  as given" (its goals) and "case, `_` and whitespace are fixed" (decision
  2); GH-280's body says "set canonical case per RFC 5646 §2.1.1". Cite
  GH-280.
- **Two differences go unreported.** D1 refuses `_` and whitespace, which
  the brief repairs, and the brief leaves `LocalizedText`'s JSON form and its
  `str` boundary to this design.
- **One stored form must hold across both repos.** statcatlib writes its
  own maps into the same database, so the contract has to be one stored form
  (lowercase) across both repositories.

### M8. `LocalizedText` is under-specified [S7, D9; Stage 5 F9, Stage 6 M7]

D6 does not give:

- its JSON form or `repr`;
- whether it subclasses `str`;
- what a malformed `lang` does;
- the module and public export of `LocalizedText`, `normalize_tag` and
  `display_tag`, which statcatlib imports.

### M9. `display_tag` is decided in D3 and reopened in open question 2 [S6, D10, St-M2; Stage 5 F1, Stage 6 M9]

Stage 5 recommends dropping the writer option and `display_tag`, since no
sdmxlib caller exists.

### M10. "Nothing is repaired" contradicts D2's lowercasing [St-M3]

Line 53 is also a short emphatic sentence. Suggested: "Only case changes.
Whitespace, an underscore for a hyphen and an empty string are refused."

### M11. Line 11 cites the brief's "§13–14" [St-M6, S12; Stage 6 N6]

These are decisions 13 and 14, not section numbers.

## Nits

- **D8 is implementation detail** [D11, St-N13; Stage 5 F5, Stage 6 N5]:
  state the property and include the lookup paths; "only if a profile
  shows" has no threshold.
- **The registry evidence is SDMX-ML only** [D12]: narrow line 150's "the
  registries send nothing these refuse or change" to SDMX-ML, or check
  SDMX-JSON responses.
- **Headings and structure** [S11, St-N9]: Evidence uses run-in labels where
  the decisions use H3s; D6–D8 name topics where D1–D5 state decisions.
- **Metadata and links** [S12]: the document has no author or date, and its
  repository paths are not relative links.
- **Undefined terms** [S13, Stage 6 N7]: `msg.refused`, "bundle overlay",
  `SCHEMA_VERSION`, "the registry sweep", "their converters".
- **Wording** [St-N1–N8, N10–N12, N14, N15]:
  - "about fifteen" (give the number);
  - "makes a tag one string";
  - the Global Registry phrasing;
  - state the 1.34 million figure;
  - the `xml.xsd` path, and where the `xs:language` pattern comes from;
  - name SDMX-JSON 2.1;
  - cite the sdmx1 and pysdmx versions and modules;
  - three contrasts by negation;
  - the column name "Then";
  - "which is then normalised";
  - "a store built before this";
  - the issue sentence with no owner;
  - the RFC §2.1.1 paraphrase.

## What holds up

All three reviewers accept the core direction:

- one stored form, set where text enters;
- normalisation owned by sdmxlib;
- `LocalizedText` and `pick` as the smallest addition statcatlib needs;
- no IANA registry dependency.

The design reviewer judges the `[a-z]{2,3}` fast path sound.
