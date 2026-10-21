r"""Ordering SDMX version strings, which text ordering gets wrong.

`artefact.version` is text, so every ordering over it is lexicographic and
`1.9` sorts *after* `1.10` — meaning "the latest version", one of the most
common catalogue questions, returns the wrong artefact for any codelist whose
minor version reaches double digits (#311).

The fix is a sortable key baked at write time, which is what
`docs/design/caching.md` prescribes: the input is immutable within an artefact
version, so it is a bake rather than a runtime cost, and it is the same shape
as `code.depth` / `code.ancestor_ids`.

**The grammar is the vendored XSD's, not a guess.** SDMX-ML 3.0's
`SDMXCommonReferences.xsd` defines `VersionType` as a union:

    LegacyVersionNumberType     (0|[1-9]\d*)(\.(0|[1-9]\d*))?
    SemanticVersionNumberType   (0|[1-9]\d*)(\.(0|[1-9]\d*)){2}(-<prerelease>)?

So: one or two numeric parts (legacy), or exactly three plus an optional
prerelease (semantic).

The **2.1** schema is looser — `[0-9]+(\.[0-9]+)*`, any number of parts and
leading zeros permitted — and carries the note that makes the case for parsing
rather than comparing text: *"a version of 1.3 and 1.03 would be equivalent,
as both parse to an integer value of 3"*. 3.0 tightened the grammar and
dropped the note; this module follows the 2.1 reading, since a key that mis-
sorts a version some registry still publishes is worse than one that is
generous. (The two grammars disagree in both directions, which is #319.)
"""

import re

#: Numeric parts, then an optional `-prerelease`. Deliberately more permissive
#: than the XSD: this runs over whatever a registry actually sent, and a
#: version that fails to parse must still sort *somewhere* rather than raise
#: during a write. `_UNPARSED` is where those go.
_VERSION = re.compile(r"^(\d+(?:\.\d+)*)(?:-(.+))?$")

#: Width each numeric part is padded to. Nine digits holds any version a
#: registry will plausibly publish and keeps the key comparable as text, which
#: is what lets DuckDB sort it with an ordinary index.
_WIDTH = 9

#: Numeric parts every key carries, padded with zeros. Fixed rather than
#: variable so `1.0` and `1.0.0` produce the *same* key — the 2.1 schema says
#: they are the same version — which a variable-length key cannot do while
#: also sorting `1` before `1.0.1`.
#:
#: Eight rather than three. Three was the first cut and was wrong: 2.1 permits
#: unlimited parts and `_check_version` accepts them, so `1.2.3.4` and
#: `1.2.3.9` truncated to the same key and sorted *equal* — a silent tie in
#: the middle of the ordering this module exists to get right. Eight covers
#: every shape either schema describes (3.0 caps at three) with room to spare;
#: beyond it versions collide again, which
#: `test_more_parts_than_the_key_holds_collide` states rather than leaves to
#: be discovered.
_PARTS = 8

#: Sorts above every character a prerelease may contain (`[A-Za-z0-9.-]`), so
#: a release sorts *after* its own prereleases — `1.0.0-draft` then `1.0.0`,
#: which is SemVer's rule and the opposite of what plain text gives.
_RELEASE = "~"

#: Sorts below every digit, so anything unparseable orders before every real
#: version rather than landing arbitrarily among them. A registry sending
#: something the XSD does not permit should look *old*, not newest — "the
#: latest version" silently picking the malformed one is the failure this
#: whole module exists to prevent, arriving by another route.
_UNPARSED = "!"


def sort_key(version: str) -> str:
    """A text key ordering `version` the way SDMX means it.

    Text rather than a tuple because it is stored in a column and compared by
    the database: DuckDB orders and indexes it without any per-row work, which
    is the point of baking it.

    Numeric parts are padded and compared numerically, so `1.10` sorts after
    `1.9`. A missing part is zero, so `1` and `1.0` and `1.0.0` are equal. A
    prerelease sorts *before* the release it precedes. Up to `_PARTS` numeric
    parts are significant — 2.1 permits more than 3.0's three, and both are
    covered.

    Ordering *among* prereleases is lexical, not full SemVer precedence: SemVer
    compares dot-separated identifiers, numeric ones numerically and numeric
    before alphanumeric, so `1.0.0-alpha.9` and `1.0.0-alpha.10` are the one
    case this gets wrong. Left lexical deliberately — the prerelease field is
    rare in published SDMX, and the cost of the full rule is a second parser
    for a case no registry in the fixtures uses. `test_prerelease_ordering_is_
    lexical_not_semver` pins it so the limit is stated rather than assumed.

    Args:
        version: A version string as a registry sent it.

    Returns:
        A key to sort by. Opaque — compare it, do not parse it back.

    Example:
        sorted(["1.10", "1.9"], key=sort_key)   # ["1.9", "1.10"]
    """
    matched = _VERSION.match(version.strip())
    if matched is None:
        return _UNPARSED + version
    numbers, prerelease = matched.groups()
    parts = [int(part) for part in numbers.split(".")]
    parts = (parts + [0] * _PARTS)[:_PARTS]
    padded = ".".join(f"{part:0{_WIDTH}d}" for part in parts)
    return f"{padded}-{prerelease}" if prerelease else padded + _RELEASE


# ── Finality is a property of the version (#420) ─────────────────────────

#: A final semantic version: three numeric parts, no prerelease, and a major
#: of at least 1. The major bound is semver's own rule — "major version zero
#: is for initial development; anything may change at any time" — and is the
#: one pysdmx applies (`pysdmx.util.is_final`).
_FINAL = re.compile(r"^[1-9]\d*\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def is_final(version: str) -> bool:
    """Whether `version` names a final, immutable artefact, as SDMX 3.0 reads it.

    SDMX 3.0 removed `isFinal` from every maintainable and moved the fact into
    the version (Section 6): a semantic version with no extension is final, one
    with an extension (`1.0.0-draft`) is a draft, and a legacy one- or two-part
    version (`1.0`) is "considered draft or unstable". So `1.0` is never final
    here, whatever a 2.1 document said — that is what `legacy_is_final` keeps
    (#424).

    Args:
        version: The artefact's version.

    Returns:
        True for `1.0.0` and `2.3.1`; False for `1.0`, `1.0.0-draft`, `0.1.0`,
        and anything only 2.1 accepts (`1.03`, `1.2.3.4`).
    """
    return bool(_FINAL.match(version))


# ── Which grammar accepts a version (#319) ───────────────────────────────

#: SDMX-ML 2.1's `VersionType`. Unlimited numeric parts, leading zeros
#: permitted, no prerelease.
_GRAMMAR_21 = re.compile(r"^[0-9]+(\.[0-9]+)*$")

#: SDMX-ML 3.0's `VersionType`, a union of `LegacyVersionNumberType` (one or
#: two parts, no leading zeros) and `SemanticVersionNumberType` (exactly three
#: plus an optional prerelease). 3.1 defines it identically.
_GRAMMAR_30 = re.compile(
    r"^(0|[1-9]\d*)(\.(0|[1-9]\d*))?$"
    r"|"
    r"^(0|[1-9]\d*)(\.(0|[1-9]\d*)){2}"
    r"(-(([A-Za-z-]|([A-Za-z-][A-Za-z0-9-]+)|([A-Za-z0-9-]+[A-Za-z-][A-Za-z0-9-]*))|(0|[1-9][0-9]*))"
    r"(\.(([A-Za-z-]|([A-Za-z-][A-Za-z-][A-Za-z0-9-]*)|([A-Za-z0-9-]+[A-Za-z-][A-Za-z0-9-]*))|(0|[1-9][0-9]*)))*)?$"
)

#: Grammar name to its pattern. The names are the ones a user sees in an
#: error, so they are the SDMX-ML versions rather than internal labels.
_GRAMMARS = {"2.1": _GRAMMAR_21, "3.0": _GRAMMAR_30}

#: Target formats to the grammar each uses and the name a user sees.
#: SDMX-ML 3.1 defines `VersionType` identically to 3.0. SDMX-JSON's
#: *structure* schema (2.0 and 2.1 alike) defines it as the same two patterns
#: as SDMX-ML 3.0 — its `description` still carries the 2.1 prose about
#: unlimited parts and `1.03`, but the patterns forbid both (#417). The
#: *metadata* schema is stricter again (semver only), and is checked in
#: `validation`, #234.
_FORMATS = {
    "2.1": ("2.1", "SDMX-ML 2.1"),
    "3.0": ("3.0", "SDMX-ML 3.0"),
    "3.1": ("3.0", "SDMX-ML 3.1"),
    "json": ("3.0", "SDMX-JSON"),
}


def compliance(version: str) -> frozenset[str]:
    """Which SDMX-ML grammars accept `version` — `"2.1"`, `"3.0"`, both, or neither.

    **Neither grammar contains the other**, which is the fact this exists to
    make usable. 3.0 tightened the numeric form (one or two legacy parts, or
    exactly three, no leading zeros) *and* added prereleases, so:

    | version | 2.1 | 3.0 |
    | --- | --- | --- |
    | `1.0`, `1.10`, `1.0.0` | yes | yes |
    | `1.03`, `1.2.3.4` | yes | no |
    | `1.0.0-draft` | no | yes |

    So "convert 2.1 to 3.0" is not always possible, and neither is the
    reverse. A single widest-grammar check would accept `1.2.3.4-draft`, which
    conforms to nothing.

    Args:
        version: A version string as a registry sent it.

    Returns:
        The names of the grammars that accept it. Empty when neither does.
    """
    return frozenset(name for name, pattern in _GRAMMARS.items() if pattern.match(version))


def unwritable(version: str, target: str) -> str | None:
    """Why `version` cannot be written as `target`, or None if it can.

    A question about the *conversion*, not about the artefact: the store is
    format-agnostic and holds anything either grammar accepts, and only
    writing picks a format. `validation.check_serialisable` asks it of every
    maintainable before any bytes are written, and the writers ask it again
    for a caller that writes without `serialize` (#319).

    Args:
        version: The artefact's version.
        target: `"2.1"`, `"3.0"`, `"3.1"` or `"json"` — the format being
            written. `"json"` is the SDMX-JSON structure format.

    Returns:
        A reason naming the other grammar when there is one, because
        "re-version or write the other format" is the whole set of fixes.
    """
    rule, label = _FORMATS[target]
    accepted = compliance(version)
    if rule in accepted:
        return None
    if accepted:
        other = ", ".join(sorted(accepted))
        fix = f"it conforms to SDMX-ML {other}; write that format instead, or re-version the artefact"
    else:
        fix = "it conforms to no SDMX-ML version; re-version the artefact"
    return f"cannot be written as {label}: version {version!r} is not valid there — {fix}"


# ── Which formats can carry a reference's version (#429) ─────────────────

#: SDMX 3.0's late binding: a semantic version with `+` on one part, "this or
#: any later compatible". SDMX-ML 3.1 and SDMX-JSON allow exactly these three
#: (`UrnReferenceVersionPart`; the reference types' `pattern` in JSON).
_WILDCARD = re.compile(
    r"^((0|[1-9]\d*)\+\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"|(0|[1-9]\d*)\.(0|[1-9]\d*)\+\.(0|[1-9]\d*)"
    r"|(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\+)$"
)

#: One more that only SDMX-ML 3.0 allows: `+` on the major and the patch at
#: once. 3.1 narrowed the pattern that admitted it to the patch alone.
_WILDCARD_30_ONLY = re.compile(r"^(0|[1-9]\d*)\+\.(0|[1-9]\d*)\.(0|[1-9]\d*)\+$")


def _referenceable(version: str, target: str) -> bool:
    rule, _label = _FORMATS[target]
    if rule == "2.1":
        return bool(_GRAMMAR_21.match(version))
    return bool(
        _GRAMMAR_30.match(version) or _WILDCARD.match(version) or (target == "3.0" and _WILDCARD_30_ONLY.match(version))
    )


def unreferenceable(version: str, target: str) -> str | None:
    """Why a *reference* to `version` cannot be written as `target`, or None.

    A reference is not an artefact: besides any version an artefact may have,
    SDMX 3.0 lets it late-bind (`1.0+.0`, "1.0.0 or any later minor"), which
    SDMX-ML 2.1 cannot say, and 3.1 and SDMX-JSON say in fewer ways than 3.0.
    Every writer copied the version through unchecked, so IMF's wildcards
    became 20,983 schema-invalid 2.1 attributes (#429).

    The `"latest"` sentinel is not a version and is not judged here: it is
    `_check_latest_refs`' question (#233).

    Args:
        version: The version the reference names.
        target: `"2.1"`, `"3.0"`, `"3.1"` or `"json"`.

    Returns:
        A reason naming the formats that can carry it, when any can.
    """
    if _referenceable(version, target):
        return None
    _rule, label = _FORMATS[target]
    others = [_FORMATS[name][1] for name in _FORMATS if name != target and _referenceable(version, name)]
    if others:
        fix = f"write {', '.join(others)} instead, or pin the reference to a version {label} can name"
    else:
        fix = "no SDMX format accepts it in a reference"
    return f"a reference to version {version!r} cannot be written as {label} — {fix}"
