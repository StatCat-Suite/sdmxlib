# Stage 5: initial zoom-out review, language tags (GH-280)

*   **Target:** `docs/design/language-tags.md`, lines 1–167 (no implementation
    plan section, so the whole document is the feature range)
*   **Reviewer:** `egm-zoom-out`, fresh context, `zoom-out-standard.md`
*   **GoldfishContext:** `src/sdmxlib/model/istring.py`,
    `src/sdmxlib/formats/sdmx_ml/_common.py`,
    `tests/schemas/sdmx_ml30/xml.xsd`,
    `tests/schemas/sdmx_json/2.0/sdmx-json-structure-schema.json`,
    `../statcatlib/docs/design/language-tags.md`
*   **Date:** 2026-10-29

## Verdict

The design passes the prerequisite check, with no blockers and four majors.
The objective is clear: tags are compared case-sensitively today and
statcatlib needs exact matching, so one normalised form has to be fixed where
text enters sdmxlib. Owning normalisation in sdmxlib, in
`InternationalString` and at the reader boundaries, fixes the root cause.

The four majors:

- a speculative display and writer feature;
- a cross-repo claim that is false as written;
- a misread of the SDMX-JSON schemas;
- a missed chance to reuse a grammar sdmxlib already vendors.

## Findings

### 1. Major: `display_tag` and the writers' `lang_case` flag have no consumer

*Scope:* D3 (lines 73–80); open question 2 (lines 165–166).

The design's own evidence says no registry sends a region, a script or an
uppercase letter, and that no SDMX format needs canonical case. So the SDMX-ML
writers' `lang_case: Literal["lower", "canonical"]` is an option nobody uses,
and it adds a code path and a test case to every writer. `display_tag` has no
caller in sdmxlib, and open question 2 already doubts that it belongs there.

*Recommendation:* drop the writer flag. Drop `display_tag`, or leave it to
statcatlib, the layer that shows tags to people. Adding either later breaks
nothing.

### 2. Major: "its lookups match either way" is false unless statcatlib also stores lowercase

*Scope:* Cross-repo (lines 152–158).

statcatlib writes its own `statcat.*` map columns into the same DuckDB
database. Its brief points to canonical case for its own keys (its decision 2
and its goal of "lossless canonicalisation"). Suppose statcatlib stores
`fr-CA` but normalises request tags with sdmxlib's lowercasing
`normalize_tag`. Its requests then become `fr-ca` and miss its own tables, and
one database holds two spellings, which is the problem this design exists to
remove.

The section also leaves out two other differences:

- statcatlib's decision 2 says `_` and whitespace "are fixed", which D1
  refuses;
- statcatlib's goals say "store tags as given", which D2 changes.

*Recommendation:* state the contract outright: one stored form, lowercase,
across both repositories, through one function. Make statcatlib's agreement a
precondition, not a follow-up issue. Close open question 1 the same way:
lenient locale repair (`normalize_locale`) stays at statcatlib's CKAN boundary,
as D1's own reasoning says.

### 3. Major: the SDMX-JSON evidence is misread

*Scope:* Evidence, "What the formats allow" (lines 34–36); D2 (lines 65–68);
D3 (lines 77–78).

This contradicts D2's "valid in every SDMX format" and D3's "SDMX-JSON
refuses it". Three details, checked by the Elephant against the vendored
schemas:

- **SDMX-JSON 1.0 and 2.0 accept any key.** `localisedText` has only
  `patternProperties`, with no `additionalProperties` and no
  `unevaluatedProperties`. A key that fails the pattern, such as `fr-CA`, is
  still allowed: the pattern constrains only the values of keys it matches.
- **SDMX-JSON 2.1 refuses one.** The vendored 2.1 schema, which the design
  does not mention, adds `"unevaluatedProperties": false`. Only there is
  `fr-CA` invalid.
- **The grandfathered branch is mixed case.** It lists `en-GB-oed`,
  `sgn-BE-FR`, `sgn-BE-NL` and `sgn-CH-DE` exactly. A lowercase `sgn-be-fr`
  matches neither branch, so for these tags lowercase is the form SDMX-JSON
  2.1 refuses. D3's own example, `sgn-BE-FR`, is one of them.

*Recommendation:* keep lowercase as the stored form, since every tag the
registries send is unchanged by it. Correct the evidence (1.0 and 2.0 allow
any key; 2.1 refuses a key outside the pattern). Then either refuse the
grandfathered tags in D1, the simplest course since no registry sends one, or
name them as D2's one exception.

### 4. Major: reuse the RFC 5646 regex sdmxlib already vendors

*Scope:* D1 (lines 47–51).

D1 proposes a new well-formedness check covering:

- extlangs, script, region and variants;
- extensions and private use;
- grandfathered tags.

`src/` has no tag helper today, so the function is new. But every vendored
SDMX-JSON schema already holds a complete RFC 5646 well-formedness regex, the
`localisedText` `patternProperties` key. That regex, applied to the lowercased
tag, with the grandfathered tags handled as in finding 3, is the check. It
makes "anything sdmxlib writes is valid SDMX-JSON" true by construction, where
a second grammar would have to be kept in step. A third-party package such as
`langcodes` is not justified: the design refuses the IANA registry, and the
regex needs no dependency.

*Recommendation:* have D1 reuse the SDMX-JSON 2.1 pattern, cited by file, in
place of a separate ABNF implementation.

### 5. Nit: D8 is implementation detail, and the store path normalises twice

*Scope:* D4, store-reader row (line 89); D8 (lines 131–139).

The `[a-z]{2,3}` early return is a mechanism. The architectural point is
narrower: construction is a hot path, and the store's keys are already
normalised. `InternationalString` already calls `sys.intern` on each key, and
a small dict keyed by the raw tag, reached on that same lookup, would replace
both the regex fast path and the "memo only if profiled" step.

*Recommendation:* state the property ("a store read pays no grammar check per
key") and leave the mechanism to the plan.

### 6. Nit: `__contains__` raising `ValueError` changes what membership means

*Scope:* D4, last row (line 92).

`"" in s` or `"fr_CA" in s` would raise where a predicate returns a boolean.
statcatlib's brief treats a missing tag in a predicate as no match (its
decision 8).

*Recommendation:* have `__contains__` return `False` for a malformed tag, and
keep the raise in `get`, `pick`, `__getitem__` and the `lang=` parameters.

### 7. Nit: D4's list of `lang=` parameters will drift

*Scope:* D4, the `lang=` row (line 91).

About 27 source files take a `lang`. A hand-kept list in a design document
goes stale.

*Recommendation:* state the rule (every public entry point that takes a tag
normalises it through `normalize_tag`) and have a test enumerate the public
`lang` parameters.

### 8. Nit: open question 3 is already answered

*Scope:* Open questions (line 167).

D5 already says sdmxlib never produces `und`: an empty `xml:lang` is refused,
an absent one is `en`, and `und` is only accepted from callers.

*Recommendation:* close the question.

### 9. Nit: `LocalizedText`'s JSON form is not specified

*Scope:* D6 (lines 110–118).

statcatlib's brief asks this design for `LocalizedText`'s equality, hashing and
JSON form (its finding 12, and its list of questions left for the design
document). D6 settles equality and hashing, not the JSON form.

*Recommendation:* add one sentence, or say that serialisation is statcatlib's.

## What holds up

- **D1**, refusing a tag rather than repairing it, matches the SDMX schemas
  and puts locale repair at the source's boundary.
- **D5**, refusing `xml:lang=""`, matches how the readers already refuse other
  schema contradictions (`msg.refused`).
- **D7**, no `SCHEMA_VERSION` bump, is the minimal choice and is justified by
  the sweep evidence; the upgrade note covers the remaining case.
- **D6**, `LocalizedText` and `pick`, is the smallest addition that meets
  statcatlib's decision 14, and the name avoids the query language's `Label`.
