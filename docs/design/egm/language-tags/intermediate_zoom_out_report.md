# Stage 8: intermediate zoom-out, language tags (GH-280)

*   **Target:** `docs/design/language-tags.md`, lines 1–167, unchanged since
    Stage 5
*   **Reviewer:** `egm-zoom-out`, fresh context, `zoom-out-standard.md`, with
    the Stage 5, 6 and 7 reports
*   **Date:** 2026-10-29

## Verdict

The core approach holds and keeps its shape: normalise once on ingest to one
lowercase stored form, refuse what is not a well-formed tag, and look tags up
with `LocalizedText`. The accumulated findings fall into three groups:

- corrections to the evidence: SDMX-JSON per version, and the mixed-case
  grandfathered tags;
- scope to cut: `display_tag`, `lang_case`, `normalize_locale`, and D8's
  mechanism;
- contract detail to add: collisions, tags that may be `None`, the header,
  error types and module paths.

Two changes alter the architecture rather than the wording:

- derive the well-formedness rule from the vendored SDMX-JSON 2.1 pattern;
- give store reads a trusted construction path.

Together they retire about half of the Stage 6 and 7 majors. This stage
raised no blockers: four majors and four nits.

## Why the shape holds

The unweighed alternative (keep tags as given and compare case-insensitively
at lookup) loses on the system's own terms. The lookups that matter include
SQL over DuckDB maps and `sdmx.localized_text`, in both sdmxlib and
statcatlib, which share one database. Case-insensitive matching would have to
be rebuilt in every SQL path, in two repositories. Normalising once keeps
every comparison an exact string match.

Lowercase is the case the SDMX-JSON pattern accepts, changes no swept
registry's key, and needs no case table. `LocalizedText` and `pick` are the
smallest addition that meets statcatlib's decision 14. A paragraph recording
these rejected alternatives meets the standard; a long section is not
needed.

## Findings

### 1. Major: take the grammar from the format

*Scope:* D1, D2, Evidence. Settles Stage 5 F3 and F4, Stage 6 M1, and
Stage 7 B1 and M2.

The vendored `localisedText` `patternProperties` regex is a complete RFC 5646
well-formedness pattern, in lowercase, the same in all eight
`tests/schemas/sdmx_json/*` files. Only four irregular grandfathered tags are
mixed case.

The rule is one sentence: a tag is accepted when its lowercase form matches
the SDMX-JSON 2.1 `localisedText` pattern, and is stored in that form. Then:

- no second grammar exists, and anything sdmxlib stores is a valid 2.1 key
  by construction;
- the four mixed-case grandfathered tags are refused (no registry sends
  them), which ends the D2 contradiction;
- the rule is knowingly stricter than `xs:language` (`e`, `en-a`), so say so
  in D1 and D5, and drop "as it refuses any other schema contradiction".

The pattern lives in `src/` as a constant citing the schema file, never read
from `tests/`. A length cap before the match covers untrusted input
(Stage 7 B3).

### 2. Major: cut `display_tag`, `lang_case` and `normalize_locale`; close the open questions

*Scope:* D3, Cross-repo, open questions 1–3. Settles Stage 5 F1 and F8,
Stage 6 M9, N3 and N4, Stage 7 M9 and part of B1.

These are scope, not missing detail. `display_tag` has no sdmxlib caller.
`lang_case` contradicts D2 and is the only source of Stage 6's questions about
canonical-case edge cases and which writers take it. Either can be added
later without breaking anything.

- Delete D3, and leave display case to statcatlib.
- Close open question 1: locale repair stays at statcatlib's CKAN boundary.
- Close open question 3: D5 already says sdmxlib never produces `und`.

### 3. Major: store reads take a trusted path, and stale stores are detected once

*Scope:* D4's store row, D7, D8. Settles Stage 6 M3, Stage 7 M4, B3's
hot-path cost, and Stage 5 F5.

Rebuilding store rows through the validating constructor causes three later
findings:

- an old store holding `""` or `fr_CA` fails to read;
- Python reads and SQL lookups disagree on an old mixed-case store;
- D8 needs a fast path.

Writes are already normalised, so store readers should build through an
internal constructor that skips the check. Then `check_bundle` reports any
stored tag that is not normalised (`lang <> lower(lang)`, or a key outside
the pattern), pointing to the rebuild note in `docs/upgrading.md`. That keeps
D7's "no `SCHEMA_VERSION` bump" honest without a check on every open. D8
shrinks to a property: building from a parsed document pays about one
grammar check per distinct tag. The reviewer suggests memoising
`normalize_tag` with `functools.lru_cache` and dropping the `[a-z]{2,3}` fast
path.

*Elephant note.* A memo is a new runtime cache. CLAUDE.md requires a new one
to go through `SingleFlight` and to be justified by a profile.
`model/urn.py`'s `functools` caches are listed in `docs/design/caching.md` as
predating that rule, so they are no precedent. If the trusted store path
removes the hot cost, building from a parsed document may need no memo at
all; measure before adding one.

### 4. Major: agree one stored form with statcatlib before building

*Scope:* Cross-repo. Settles Stage 5 F2, Stage 6 M8, Stage 7 M7.

Both libraries write map keys into one database, so "no two spellings
anywhere" holds only if statcatlib stores the same lowercase form through the
same `normalize_tag`.

- State the contract.
- Cite GH-280 (not the brief) as the source of the canonical-case request.
- List both differences: lowercase rather than canonical case, and refusing
  `_` and whitespace rather than repairing them.

statcatlib's agreement is a precondition, not a follow-up. With D3 cut, the
contract is one function, one form, two repositories.

### 5. Nit: the rest of Stages 6 and 7 is contract detail, one line each

*Scope:* D4, D6, Breaking changes.

- **Collisions** (`FR` and `fr`): keep the last value, as two texts with one
  `xml:lang` behave today, and list it under Breaking changes.
- **`__contains__`** returns `False` for a malformed or non-`str` key.
- **`__getitem__`** raises `KeyError` chained from the `ValueError`.
- **`get` and `pick`** raise for a malformed request tag.
- **`AnnotationURL.lang` and `RegionValue.lang`**: `None` passes through,
  and only a present tag is normalised.
- **The header's texts and SDMX-JSON `meta.contentLanguages`** normalise the
  same way. A bad header tag fails the message, since a header cannot be
  refused on its own.
- **`normalize_tag` and `LocalizedText`** get a named module (for example
  `sdmxlib.model.istring`) and are exported from `sdmxlib`.
- **`LocalizedText`'s JSON form** is statcatlib's to define.

*Elephant note.* The owner asked for strictness unless forced otherwise. Two
texts that normalise to one tag are a contradiction in the source, so
refusing the artefact fits that rule better than keeping the last value. This
is a decision for the owner.

### 6. Nit: replace the `lang=` list with a rule

*Scope:* D4's `lang=` row, line 7.

About 19 `lang`/`langs` signatures in 10 modules already exceed "about
fifteen". State the rule (every public parameter named `lang` or `langs`
normalises through `normalize_tag`) and enforce it with a test that
enumerates them.

### 7. Nit: keep the Goals, Risks and Alternatives sections short

*Scope:* whole document. Settles Stage 7 B2, B3 and M1.

After findings 1–3, Risks is three lines:

- the length cap;
- the per-distinct-tag cost;
- a large artefact refused for one bad tag, accepted because it is the
  existing refusal mechanism and no registry sends one.

Alternatives is the paragraph above. Goals and Non-Goals gathers what D1
already says: no IANA registry, no replacement of deprecated subtags, no
locale repair, no display case.

### 8. Nit: narrow the evidence to SDMX-ML

*Scope:* line 150 and the sweep paragraph.

The sweep counted `xml:lang`, so it covers SDMX-ML only. Say so, or check
SDMX-JSON responses too.

## The smallest design that meets the objective

- `normalize_tag`: cap the length, lowercase, match the SDMX-JSON 2.1
  `localisedText` pattern, or raise `ValueError`.
- Applied in `InternationalString`'s public constructors, the SDMX-ML and
  SDMX-JSON readers (including the header and `contentLanguages`), the attrs
  converters for optional tags, and every public `lang`/`langs` parameter.
  Store readers take a trusted path.
- `xml:lang=""` refuses the artefact.
- `LocalizedText` and `pick`.
- No schema bump; `check_bundle` reports stored keys that are not normalised.
- One lowercase stored form in both repositories, agreed with statcatlib
  first.

D1 (refuse rather than repair), D5, D6 and D7 stand.
