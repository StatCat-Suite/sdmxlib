# Language tags

**Status:** proposed for
[GH-280](https://github.com/StatCat-Suite/sdmxlib/issues/280); not built.
Revised after an EGM review whose reports are in
[`egm/language-tags/`](egm/language-tags/).
**Date:** 2026-10-29

## Problem

sdmxlib holds every multilingual text under a language tag: an
`InternationalString` maps tags to texts, and the store keeps them in DuckDB
maps and in `sdmx.localized_text` rows. Tags are stored as they arrive and
compared case-sensitively, so a store holding `fr-ca` misses a request for
`fr-CA`. statcatlib writes its own maps into the same database and is moving to
exact tag matching. Its brief (`StatCat-Suite/statcatlib`,
`docs/design/language-tags.md`, decisions 13 and 14) asks sdmxlib to own tag
normalisation and to add a value type that pairs a text with its tag.

## Goals

- Every tag in sdmxlib has one spelling, set where the tag enters: in a model
  object, a store, a bundle and a written file alike.
- A tag that is not well-formed is refused where it enters.
- A lookup can return the text together with the tag that answered.
- The same normalisation serves statcatlib, so both libraries write one
  spelling into the shared database.

## Non-goals

- **Validity.** The check is for well-formedness. sdmxlib carries no copy of
  the IANA subtag registry, so `qq-ZZ` passes and a deprecated subtag (`iw`)
  is not replaced by its preferred value.
- **Locale repair.** Turning `fr_CA` or a padded tag into a tag is the job of
  the source's reader, which knows what it meant. For CKAN text that is
  statcatlib's.
- **Display case.** Showing `fr-CA` to a person is statcatlib's, the layer
  that shows tags to people.
- **`LocalizedText`'s JSON form.** statcatlib, which serialises labels,
  defines it.

## Evidence

### What the registries send

The registry sweep (`bench/sweep`) caches the structure dumps of six
registries, BIS, ECB, Eurostat, IMF, OECD and the SDMX Global Registry, in ten
SDMX-ML 2.1 and 3.0 snapshots. They hold 37,269,892 `xml:lang` values.

- Every value is a lowercase two-letter ISO 639-1 code: `en`, `fr` and `de`
  throughout; IMF adds `ru`, `ja`, `es`, `ar`, `zh` and `pt`; OECD adds `ar`
  and `he`; the Global Registry sends 26 EU-language codes, one value each.
- No value has a region, a script or an uppercase letter.
- No value is empty, and no text element omits `xml:lang`.

GH-280's survey of 14 registries found the same in about 1.34 million values.
Both surveys read SDMX-ML; SDMX-JSON responses are not surveyed.

### What the formats allow

- **SDMX-ML.** `TextType` refers to `xml:lang`, which the vendored
  `tests/schemas/sdmx_ml30/xml.xsd` types `xs:language`. XML Schema defines
  that type by the pattern `[a-zA-Z]{1,8}(-[a-zA-Z0-9]{1,8})*`, which accepts
  either case and needs at least one character, so `xml:lang=""` is invalid.
  `TextType` gives an absent `xml:lang` the default `en`.
- **SDMX-JSON.** A language key is a `patternProperties` key of
  `localisedText`, whose pattern is the RFC 5646 grammar:
  - in 1.0 and 2.0, written in lowercase. Neither schema sets
    `additionalProperties`, so a key outside the pattern is left unchecked;
  - in 2.1, written case-insensitively (`[A-Za-z]`), and
    `"unevaluatedProperties": false` refuses a key outside it;
  - in all three, the grandfathered branch lists four irregular tags in
    mixed case: `en-GB-oed`, `sgn-BE-FR`, `sgn-BE-NL` and `sgn-CH-DE`. Their
    lowercase forms match no branch.
- **RFC 5646 §2.1.1.** Tags are case-insensitive; the canonical case
  (`fr-CA`) is a formatting convention. BCP 47 is the name for RFC 5646 with
  RFC 4647.

### What the other libraries do

sdmx1 2.28 keys a text by the locale string as given
(`sdmx/model/internationalstring.py`). pysdmx 1.20's SDMX-ML reader keeps the
`en` text alone (`pysdmx/io/xml/__structure_aux_reader.py`). Neither
normalises.

## Decisions

### D1. A tag is well-formed, or it is refused

`normalize_tag(tag: str) -> str` lowercases `tag` and returns it if it matches
the SDMX-JSON 2.1 `localisedText` pattern. Otherwise it raises `ValueError`
naming the tag, truncated to 64 characters. The pattern is held in `src/` as a
constant whose comment cites
`tests/schemas/sdmx_json/2.1/sdmx-json-structure-schema.json`. It is never
read from `tests/`.

- A tag longer than 256 characters is refused before the match.
- Input that is not a `str` raises `TypeError`.
- The pattern is the RFC 5646 grammar. Only case changes. Whitespace, an
  underscore for a hyphen and an empty string are refused.
- The four mixed-case grandfathered tags are refused, since their lowercase
  forms do not match. The lowercase grandfathered tags (`i-klingon`,
  `zh-min-nan`) pass.
- The rule is stricter than `xs:language`. `e`, `en-a` and `abc-12345678x`
  are valid in SDMX-ML and refused here. The registries send none of them.

`normalize_tag` lives in `sdmxlib.model.istring` and is exported as
`sl.normalize_tag`. statcatlib imports it.

### D2. The one stored form is lowercase

What `normalize_tag` returns is the only spelling sdmxlib holds or writes.
Lowercase matches every SDMX-JSON version's pattern and `xs:language`, so no
writer transforms a tag. For every tag the registries send, lowercase and RFC
canonical case are the same string, so the choice changes no stored key.

### D3. Every way in normalises, and every refusal is explicit

The rule: every public entry point that takes a tag normalises it through
`normalize_tag`. A unit test enumerates the public functions and methods with
a parameter named `lang` or `langs` (about 19 in 10 modules today) and asserts
that each refuses a malformed tag.

| Way in | Today | After GH-280 |
|---|---|---|
| `InternationalString(**kw)`, `InternationalString.of(map)` | keys as given; two equal keys keep the last | each key through `normalize_tag`; a malformed key raises `ValueError`; two keys that normalise to one tag raise `ValueError` (D4) |
| SDMX-ML texts (`_common.read_istring`, metadata, annotation texts) | `child.get(XML_LANG, SCHEMA_DEFAULT_LANG)` | an absent `xml:lang` is `en`; a present one is normalised; an empty or malformed one, or two texts of one tag, refuses the artefact |
| SDMX-ML header texts (`sdmx_ml/_header.py`) | `child.get(XML_LANG, "en")` | as above. A header is no artefact, so a refused tag fails the read with `SdmxParseError` |
| `AnnotationURL.lang`, `RegionValue.lang` (both may be absent) | as given, or `None` | `None` stays `None`; a present tag is normalised; a malformed one refuses the artefact |
| SDMX-JSON `names`, `descriptions`, annotation `texts`, metadata values | map keys as given | each key normalised; a malformed one, or two keys of one tag, refuses the artefact |
| SDMX-JSON `meta.contentLanguages` | as given | each normalised; a malformed one fails the read with `SdmxParseError` |
| Bundle overlay `lang` | as given | normalised; a malformed one is refused as the overlay is read |
| Store readers (`storage/readers.py`, `lazy.py`, `_projections.py`) | rows as stored | built through the internal `InternationalString._from_normalized`, which skips the check: what the store holds was normalised as it was written (D6) |
| Every `lang=` / `langs=` parameter (the rule above) | compared as given | normalised before use; a malformed one raises `ValueError` |
| `InternationalString.get(*langs)`, `pick(*langs)` | exact key | each requested tag normalised, then matched exactly; a malformed one raises `ValueError` |
| `InternationalString.__getitem__(lang)` | exact key | normalised; a malformed tag raises `KeyError`, chained from the `ValueError`, so `try/except KeyError` callers keep working |
| `InternationalString.__contains__(lang)` | exact key | `False` for a malformed tag or a non-`str`, as a predicate should be |

"Refuses the artefact" is the readers' existing per-artefact refusal: the
reader records a `Refusal` naming the artefact and the tag in `msg.refused`,
reads the rest of the message, and `parse` raises `SdmxConformanceError`
unless called with `strict=False`.

### D4. Two texts of one tag are refused

When two keys of one `InternationalString`, or two texts of one SDMX-ML
element or SDMX-JSON object, normalise to the same tag, the source has given
two texts for one language. Building it in code raises `ValueError`, and a
reader refuses the artefact. This covers exact duplicates (`xml:lang="fr"`
twice) as well as case variants (`FR` and `fr`), so sdmxlib never chooses one
of two texts. Today two equal keys keep the last value silently.

### D5. `xml:lang=""` refuses the artefact

`xs:language` needs a character, so an empty `xml:lang` is invalid SDMX-ML,
and the reader refuses the artefact. XML 1.0 §2.12 gives the empty value a
meaning ("no language information"), whose BCP 47 counterpart is `und`, but
reading it as `und`, or as the `en` that `TextType` gives an absent
attribute, would accept a schema-invalid document. sdmxlib never produces
`und` itself. A caller or source that does not know a text's language passes
`und`, which is a well-formed tag.

### D6. Stores keep no check on read, and `check_bundle` reports stale ones

The store writers (`storage/writers.py`, the `localized_text` rows and the
DuckDB name maps) write what `InternationalString` holds, which is normalised.
The store readers therefore trust their rows (D3) and pay no grammar check.

No `SCHEMA_VERSION` bump: normalisation changes no tag the registries send,
so a store built from them already holds normalised keys. A store built before
GH-280 from a source that sent another case, `""` or a malformed tag would
answer Python reads and SQL lookups differently. `check_bundle` reports any
stored tag that `normalize_tag` would change or refuse, scanning
`localized_text` and the name-map keys once per check, and
`docs/upgrading.md` says to rebuild such a store.

### D7. `LocalizedText`

`LocalizedText(text: str, lang: str)` is one text and the stored tag it was
found under, in `sdmxlib.model.istring`, exported as `sl.LocalizedText`.

- It is an attrs `@frozen` class, not a `str` subclass: it compares and
  hashes by both fields, and its `repr` is `LocalizedText(text=…, lang=…)`.
- `lang` goes through `normalize_tag`, so a malformed one raises `ValueError`.
- `InternationalString.pick(*langs) -> LocalizedText | None` works as `get`
  does (each requested tag normalised, the first one present wins) and
  returns the tag that answered. So a caller asking for `("fr-ca", "fr",
  "en")` learns which tag answered.

The name avoids the query language's `Label` (`query/_expr.py`).

## Alternatives considered

- **Keep tags as given and compare case-insensitively at lookup.** Rejected.
  The lookups include SQL over DuckDB maps and `localized_text` in two
  libraries sharing one database, so every SQL path in both would need its own
  case-folding, and an exact comparison would stay wrong by default.
- **Store RFC canonical case (`fr-CA`)**, as GH-280 proposed. Rejected. It
  fails the SDMX-JSON 1.0 and 2.0 patterns, needs a case table for the
  grandfathered tags, and gains nothing for any tag the registries send.
- **Repair `_` and whitespace**, as statcatlib's decision 2 says. Rejected
  for sdmxlib: both are outside RFC 5646, and a source that sends a locale
  repairs it at its own boundary.
- **Read `xml:lang=""` as `und` or `en`.** Rejected (D5).
- **Keep the last of two texts of one tag**, as today. Rejected (D4).
- **A canonical-case output flag (`display_tag`, `lang_case` on the
  writers).** Rejected: nothing in sdmxlib calls it, and it would put a second
  spelling in written files. Either can be added later without breaking
  anything.
- **Bump `SCHEMA_VERSION`.** Rejected (D6): it would rebuild every bundle to
  change no key.

## Risks and mitigations

- **Untrusted input.** Registries and callers supply tags to a regular
  expression. Mitigation: tags over 256 characters are refused before the
  match. The pattern has no nested unbounded repetition over overlapping
  characters, so a match costs time linear in the tag. A test times the
  pattern on adversarial 256-character inputs and asserts the time stays
  bounded.
- **Cost on the hot path.** One census codelist read builds 1,159,902
  `InternationalString`s (`istring.py`). Mitigation: store reads skip the
  check (D6), so only parsing a document pays it. Whether parsing needs a memo
  is decided by a profile, not assumed. If one is added, it goes through
  `SingleFlight`, as `docs/design/caching.md` requires, and a test asserts the
  count of grammar checks.
- **One bad tag refuses a large artefact.** This is the existing refusal
  mechanism, and `strict=False` keeps the rest of the message. No surveyed
  registry sends such a tag.
- **Unsurveyed SDMX-JSON sources.** Both surveys read SDMX-ML. Mitigation:
  before this ships, the sweep counts tag spellings in cached SDMX-JSON
  responses too, and the result is recorded here.

## Breaking changes

- `InternationalString`, `LocalizedText` and every `lang=` refuse a malformed
  tag with `ValueError`, and two keys of one tag likewise.
- `InternationalString.__getitem__` raises `KeyError` for a malformed tag,
  where it raised nothing for an absent one before; `__contains__` returns
  `False` for one.
- A read refuses an artefact carrying an empty `xml:lang`, a malformed tag,
  one of the four mixed-case grandfathered tags, or two texts of one tag. It
  read before, keeping the key `""`, the tag as given, or the last text.
- Keys and returned tags are lowercase: `InternationalString(**{"fr-CA":
  "…"}).langs` is `["fr-ca"]`.

`docs/upgrading.md` gets an entry when this is built. No SDMX-ML registry in
either survey sends anything these refuse or change.

## Cross-repo contract

statcatlib writes its own maps into the database sdmxlib writes, so "one
spelling" holds only if both libraries store the same form through the same
function. Before this is built, statcatlib agrees, on its tracker, to:

1. store every tag in the form `sl.normalize_tag` returns, lowercase. That
   differs from GH-280's request for RFC canonical case;
2. refuse `_` and whitespace in sdmxlib and repair them at its own CKAN
   boundary, where its decision 2 says it fixes them;
3. define `LocalizedText`'s JSON form, and show tags in canonical case to
   people if it wants to.
