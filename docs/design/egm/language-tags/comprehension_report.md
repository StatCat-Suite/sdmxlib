# Stage 6: comprehension test, language tags (GH-280)

*   **Target:** `docs/design/language-tags.md`, lines 1–167 (feature range; no
    detailed range)
*   **Reviewer:** `egm-comprehension`, fresh context,
    `re-implementation-standard.md`
*   **GoldfishContext:** as Stage 5
*   **Date:** 2026-10-29

## Verdict

Needs revision: 0 blockers, 9 majors, 9 nits. The argument holds together,
but an engineer new to the codebase could not write the same code from it on
the first try. The main gaps:

- what happens when two keys normalise to the same tag;
- what happens to an old store, or to header text, holding a tag the new rules
  refuse;
- a claim about SDMX-JSON that is false for some legal tags;
- several "every X" lists left open.

## Majors

**M1. D2 and D3 (lines 65–80): "lowercase is valid in every SDMX format" and
"SDMX-JSON refuses canonical case" are both false for some tags.**

The `localisedText` pattern in the vendored SDMX-JSON schema lists the
grandfathered tags in mixed case: `en-GB-oed`, `sgn-BE-FR`, `sgn-BE-NL` and
`sgn-CH-DE`. Their lowercase forms match neither that list nor the general
branch of the pattern:

- in `sgn-be-fr`, `fr` is neither a variant (5–8 characters) nor an
  extension;
- in `en-gb-oed`, `oed` is too short to be a variant.

So D1 accepts a tag that is then written to SDMX-JSON in a form the schema
refuses, and for those tags SDMX-JSON requires the case D3 says it refuses.
D3's own example is `sgn-BE-FR`. D2's "nothing that writes a tag transforms
it" also contradicts D3's SDMX-ML option `lang_case="canonical"`. (Stage 5,
finding 3.)

**M2. D4, the `InternationalString` row (line 86): a collision after
normalisation is not specified.**

`InternationalString.of({"EN": "a", "en": "b"})` is one case. Two SDMX-ML
`<com:Name>` elements with `xml:lang="FR-CA"` and `xml:lang="fr-ca"` are
another. Both give duplicate keys once normalised. Today the dict keeps the
last value silently (`istring.py:46`, `:65`). The design does not say whether
that stays, raises `ValueError`, or refuses the artefact, and this is the
constructor's core contract.

**M3. D7 (lines 122–129): an old store holding a refused tag.**

"Breaking changes" says an empty `xml:lang` used to read under the key `""`,
so an existing store can hold `""` keys, or `fr_CA` keys if a source sent
them. D4 rebuilds store rows through `InternationalString`, which under D1
raises `ValueError` for those keys. So such a store may stop reading, with no
`SCHEMA_VERSION` check to say why. D7 covers only the mixed-case lookup. It
needs to say what a store reader does with a refused stored key, and whether
`docs/upgrading.md` covers that case too.

**M4. D4 (lines 84–92): the table misses ways in that exist in the code.**

- **The message header's texts.** `formats/sdmx_ml/_header.py:224` reads
  `child.get(XML_LANG, "en")`. A header is not an artefact, so "refuses the
  artefact (`msg.refused`)" does not say what happens there.
- **`AnnotationURL.lang`.** `_common.py:534` reads it with no default, so it
  may be `None`.
- **`RegionValue.lang`.** It is optional (`_common.py:1414`), and the design
  does not say whether `None` passes through or is defaulted.
- **`storage/resolve.py` and `local/registry.py`.** Both take `lang`
  parameters, and the table names neither.

**M5. D4's `lang=` row (line 91) and line 7: the list is left open.**

"The polars helpers", "`ObsQuery` labels" and "about fifteen" leave the
implementer to find the set. The design needs an exact list or a rule that
decides membership, for example every public function or method with a
parameter named `lang` or `langs`. (Stage 5, finding 7.)

**M6. D4's lookup row (line 92): the errors for a malformed request tag are
not stated.**

The design does not say:

- whether `s["fr_CA"]` raises `ValueError` or `KeyError`;
- whether `"fr_CA" in s` raises or returns `False` (raising from
  `__contains__` is unusual in Python, and its parameter is `object`, so
  input that is not a `str` needs a rule too);
- whether `get("fr_CA", "en")` raises, or skips the bad tag and falls back to
  `en`.

(Stage 5, finding 6.)

**M7. D1, D3 and D6: where the new names live, and part of how they behave,
is not stated.**

- `normalize_tag`, `display_tag` and `LocalizedText` have no module path or
  public export. statcatlib imports `normalize_tag`, so its import path is an
  external contract.
- `display_tag` does not say whether it checks its input or accepts a tag not
  yet normalised.
- `LocalizedText` has no JSON form and no repr, which statcatlib's brief lists
  as open for this design (its lines 202–204). (Stage 5, finding 9.)
- Building a `LocalizedText` with a malformed `lang` presumably raises
  `ValueError`, but the design does not say so.

**M8. Cross-repo (lines 154–158): only one difference from statcatlib's brief
is recorded.**

statcatlib's decision 2 says "case, `_` and whitespace are fixed", which D1
refuses, a second difference beside the stored case. The section says "that
difference", singular. The statcatlib issue it calls for should list both.
(Stage 5, finding 2.)

**M9. Open question 2 (lines 165–166) contradicts D3.**

D3 decides that `display_tag` exists and that the SDMX-ML writers take
`lang_case="canonical"`; open question 2 asks whether `display_tag` is public
at all. A reader cannot tell whether D3 is decided. If `display_tag` is
internal, the design must say the writer option uses an internal function.
(Stage 5, finding 1.)

## Nits

- **N1.** D4's SDMX-ML row: `msg.refused` and "refuses the artefact" are not
  defined. Say that the reader records a `Refusal`, the rest of the message is
  still read, and what reason text a bad tag's refusal carries.
- **N2.** D1:
  - say that the RFC 5646 ABNF is matched case-insensitively, which D2's
    lowercasing relies on;
  - give the `ValueError` message;
  - say what input that is not a `str` does.
- **N3.** D3: canonical case has edge cases (subtags after a singleton stay
  lowercase; grandfathered tags take their registry form). Naming them
  prevents a naive implementation that titlecases and uppercases by subtag
  length.
- **N4.** D3: "the SDMX-ML writers" is not specific. Do the structure, data,
  metadata and header writers all take `lang_case`, and does it apply to
  annotation URL and `RegionValue` tags?
- **N5.** D8:
  - "the store readers take that path for every row" overstates it, since a
    stored `fr-ca` does not match `[a-z]{2,3}`;
  - "a bounded memo only if a profile shows" leaves a choice open;
  - "the count of checks asserted" should say a test asserts how many grammar
    checks run.
- **N6.** Line 11: "§13–14" refers to statcatlib's decisions 13 and 14, not to
  section numbers.
- **N7.** Undefined terms:
  - "the store", "bundle" and "bundle overlay";
  - `SCHEMA_VERSION` (which constant, in which file);
  - "the registry sweep";
  - "their converters", meaning attrs converters.
- **N8.** Lines 28–38: relate BCP 47 (statcatlib's term) to RFC 5646 (this
  design's) in one sentence.
- **N9.** D4 and D5: D5 refuses an empty `xml:lang` "as it refuses any other
  schema contradiction". The SDMX-JSON reader, though, accepts and lowercases
  `fr-CA`, which the 2.1 pattern also refuses. One sentence saying that case
  folding is allowed and other contradictions are refused would settle it.

## Overlap with Stage 5

M1 repeats Stage 5's finding 3, M5 its finding 7, M6 its finding 6, M7 its
finding 9, M8 its finding 2 and M9 its finding 1. M2 (collisions), M3 (old
stores holding refused tags) and M4 (the missed ways in: the header, optional
`lang` attributes, other modules) are new.
