"""A pure-Python evaluator for the subset of `code_query` under differential test.

This is a **specification, not an implementation**. It exists to disagree with
`sdmxlib.model.code_query`, which compiles the same DSL to SQL and runs it in
DuckDB. When the two disagree, one of them is wrong and the failure says which
inputs to look at — a much stronger check than asserting an expected result set,
because the expectation is derived independently rather than read off the code.

Two rules keep it honest:

1. **It shares no code with `code_query`.** It reads `Code` objects directly and
   re-derives depth and ancestry from ``parent_id``. In particular it does not
   consult the ``depth`` / ``ancestor_ids`` columns the storage writer bakes —
   so a property comparing the two also checks that the baked values agree with
   a fresh traversal, which is the invariant `docs/design/caching.md` implies
   for baked data but nothing currently enforces.

2. **Every function stays small enough to read in one sitting.** Originally
   this said the *module* did. #249 grew it from nine predicates to twenty-four
   — the DSL has around twenty-five operations and modelling eight of them was
   never a defensible stopping point — so the rule now attaches where it
   actually bites. What must be readable at a glance is the predicate a failing
   property names; the module being long is fine, a single clever predicate is
   not. Nothing here shares a helper with `sdmxlib.model.code_query`, and
   `tests.support.hierarchy_oracle` deliberately restates rather than imports
   the parts that look alike: two oracles that share code can be wrong the same
   way, which is the one failure mode differential testing cannot catch.

Test-only; not packaged with sdmxlib.
"""

import unicodedata
from collections.abc import Callable, Collection, Mapping, Sequence
from collections.abc import Set as AbstractSet

from sdmxlib.model.codelist import Code

#: A predicate over a single code, given the full list for context.
Pred = Callable[[Code, Sequence[Code]], bool]


def parent_of(codes: Sequence[Code]) -> dict[str, str | None]:
    """Map each code id to its parent id."""
    return {c.id: c.parent_id for c in codes}


def ancestors(code_id: str, codes: Sequence[Code]) -> list[str]:
    """Ancestor ids of *code_id*, root first.

    Re-derived by walking ``parent_id`` rather than read from the baked
    ``ancestor_ids`` column, so a comparison against the query layer also
    verifies the bake.

    Args:
        code_id: The code to walk up from.
        codes: All codes in the list.

    Returns:
        Ancestor ids, outermost first. Empty for a root.
    """
    parents = parent_of(codes)
    chain: list[str] = []
    seen: set[str] = set()
    current = parents.get(code_id)
    while current is not None and current not in seen:
        seen.add(current)
        chain.append(current)
        current = parents.get(current)
    chain.reverse()
    return chain


def depth(code_id: str, codes: Sequence[Code]) -> int:
    """Distance from the root — 0 for a code with no parent."""
    return len(ancestors(code_id, codes))


def descendants(code_id: str, codes: Sequence[Code]) -> set[str]:
    """Ids of every code having *code_id* among its ancestors."""
    return {c.id for c in codes if code_id in ancestors(c.id, codes)}


def evaluate(pred: Pred, codes: Sequence[Code]) -> list[str]:
    """Ids of the codes satisfying *pred*, in list order.

    Order matters: the query layer promises to preserve codelist order unless
    an explicit sort is applied, so the comparison is on sequences, not sets.

    Args:
        pred: Predicate to apply.
        codes: The codes to filter.

    Returns:
        Matching ids, in input order.
    """
    return [c.id for c in codes if pred(c, codes)]


# ── Boolean combinators (#262) ───────────────────────────────────────────────
#
# A `Pred` returns `bool`, so this side is two-valued while SQL is three-valued.
# That does *not* make the comparison unsound, and the reason is worth stating
# because it decides which operands each combinator may be given.
#
# ``WHERE`` selects a row only when the clause is TRUE, so what the oracle
# models is the collapse ``unknown -> false``. Every predicate builder below
# already bakes its own NULL behaviour in under that collapse — `parent_eq`
# with ``negate`` returns false for a root, matching ``NULL != ?``.
#
# The collapse commutes with AND and OR:
#
#   NULL AND true -> NULL -> not selected;  false and true -> false. agree.
#   NULL OR  true -> true -> selected;      false or  true -> true.  agree.
#   NULL OR false -> NULL -> not selected;  false or  false -> false. agree.
#
# It does **not** commute with NOT: ``NOT NULL`` is NULL (not selected) while
# ``not False`` is True (selected). So `and_` / `or_` take any operands, and
# `not_` is only equivalent to the DSL's ``~`` over operands that cannot be
# NULL. The properties respect that split, and the divergence itself is pinned
# as an example rather than smoothed away.


def and_(*preds: Pred) -> Pred:
    """All of *preds* hold."""
    return lambda c, cs: all(p(c, cs) for p in preds)


def or_(*preds: Pred) -> Pred:
    """At least one of *preds* holds."""
    return lambda c, cs: any(p(c, cs) for p in preds)


def not_(pred: Pred) -> Pred:
    """*pred* does not hold."""
    return lambda c, cs: not pred(c, cs)


# ── Field readers ────────────────────────────────────────────────────────────


def _annotation_titles(c: Code, ann_type: str) -> list[str | None]:
    """Titles of every annotation on *c* of type *ann_type*."""
    return [a.title for a in c.annotations if a.type == ann_type]


def _annotation_texts(c: Code, ann_type: str, lang: str) -> list[str | None]:
    """Localised texts of every annotation on *c* of type *ann_type*."""
    return [a.text.get(lang) for a in c.annotations if a.type == ann_type]


def _apply_op(haystack: str, op: str, fragment: str) -> bool:
    if op == "contains":
        return fragment in haystack
    if op == "starts_with":
        return haystack.startswith(fragment)
    if op == "ends_with":
        return haystack.endswith(fragment)
    msg = f"Unsupported string op: {op}"
    raise ValueError(msg)


def _matches(value: str | None, op: str, needle: str) -> bool:
    """``LIKE`` semantics for the three string ops at `Fold.NONE`.

    Exact. Until #222 this folded case, because the compiler emitted ``ILIKE``
    at every strength — so ``id == "cl_freq"`` found nothing while
    ``id.contains("cl_freq")`` found ``CL_FREQ``, and the floor of the fold
    ladder was not a floor. `caseless_matches` is the rung that behaviour
    moved to.

    ``None`` never matches: SQL ``NULL LIKE ?`` is ``NULL``, which a ``WHERE``
    clause treats as false.
    """
    if value is None:
        return False
    return _apply_op(value, op, needle)


def caseless_matches(value: str | None, op: str, needle: str) -> bool:
    """``ILIKE`` semantics — the `Fold.CASE` rung, ``code.id.caseless`` (#222).

    `str.lower` rather than `str.casefold`, which is what this oracle used
    before #222 and was **wrong**: DuckDB's ``ILIKE``, ``COLLATE NOCASE`` and
    ``lower()`` agree with each other and all three disagree with `casefold`
    on ``Straße``/``STRASSE`` and ``ﬁle``/``FILE``, where casefold matches and
    SQL does not. It never failed because a generated needle is a slice of the
    generated value, so both sides fold identically and the fold cancels.

    `str.lower` is closer but not exact either — DuckDB lowercases ``İ`` to
    ``i`` via ICU where Python produces ``i``+U+0307. Unreachable from the
    strategies for the same reason, and pinned directly instead by
    `tests/unit/test_case_ladder.py` rather than approximated
    here.
    """
    if value is None:
        return False
    return _apply_op(value.lower(), op, needle.lower())


def caseless_eq(value: str | None, other: str, *, negate: bool) -> bool:
    """``COLLATE NOCASE =`` — ``code.id.caseless == x`` (#222).

    ``None`` is never equal *and* never unequal: SQL ``NULL = ?`` and
    ``NULL != ?`` are both ``NULL``. Modelling ``negate`` as ``not`` would make
    the oracle claim a NULL row satisfies ``!=``, which it does not.
    """
    if value is None:
        return False
    return (value.lower() != other.lower()) if negate else (value.lower() == other.lower())


#: Exactly what ``[\p{P}\p{Z}\s]`` matches in DuckDB, which uses RE2.
#:
#: RE2's ``\s`` is ``[\t\n\f\r ]`` — **narrower** than Python's
#: `str.isspace`, which also returns True for U+000B (vertical tab) and U+0085
#: (NEL). Modelling it with `str.isspace` made the oracle fold two characters
#: the SQL leaves alone; the differential property found it on a generated
#: label containing U+0085. Spelled out here rather than approximated.
_RE2_SPACE = frozenset("\t\n\f\r ")


def _is_punct_run_char(ch: str) -> bool:
    return unicodedata.category(ch)[0] in {"P", "Z"} or ch in _RE2_SPACE


#: What DuckDB's `strip_accents` leaves exactly as it is because its Unicode
#: tables predate the character: the 60 code points Unicode 16.0 assigned that
#: this fold would otherwise change — combining marks it would drop, and letters
#: it would decompose into a base and a mark. Python 3.14's `unicodedata` is
#: 16.0; DuckDB 1.5.6's is older, so it neither decomposes nor drops them.
#: Measured over every printable code point; a Unicode bump on either side
#: makes `test_oracle_fold` say so.
_UNICODE_16_ONLY = frozenset(
    map(
        chr,
        (
            0x0897,
            0x105C9,
            0x105E4,
            *range(0x10D69, 0x10D6E),
            0x10EFC,
            0x11383,
            0x11385,
            0x1138E,
            0x11391,
            *range(0x113B8, 0x113C1),
            0x113C2,
            0x113C5,
            *range(0x113C7, 0x113CB),
            *range(0x113CC, 0x113D1),
            0x113D2,
            *range(0x113E1, 0x113E3),
            0x11F5A,
            *range(0x1611E, 0x16130),
            *range(0x16D68, 0x16D6B),
            *range(0x1E5EE, 0x1E5F0),
        ),
    )
)


def _kept_whole(ch: str) -> bool:
    """A character `strip_accents` leaves exactly as it is, though NFD would change it.

    The Unicode 16.0 additions its tables predate, and the 11,172 precomposed
    Hangul syllables, U+AC00 to U+D7A3 (#494). NFD splits a syllable into its
    conjoining jamo and DuckDB does not, so a needle of one syllable's leading
    jamo matched inside another here and nowhere in SQL.
    """
    return ch in _UNICODE_16_ONLY or "\uac00" <= ch <= "\ud7a3"


def _label_norm(value: str, *, punctuation: bool) -> str:
    """The fold `sdmxlib.model._label_match.norm_sql` applies in SQL.

    ``strip_accents(x)`` restated in Python as decompose-and-drop-**marks**;
    with *punctuation*, additionally
    ``trim(regexp_replace(..., '[\\p{P}\\p{Z}\\s]+', ' ', 'g'))`` — every run
    of Unicode punctuation or separator collapsed to one space, then trimmed.
    """
    decomposed = "".join(ch if _kept_whole(ch) else unicodedata.normalize("NFD", ch) for ch in value)
    # Category, not combining *class*. `unicodedata.combining()` returns the
    # canonical combining class, which is 0 for every spacing mark (Mc) and
    # many non-spacing ones — DuckDB drops those, Python kept them, and the
    # two disagreed on 527 of the 8,025 characters in U+0020..U+2FFF (#353).
    # Measured: this predicate disagrees on none of them.
    stripped = "".join(ch for ch in decomposed if ch in _UNICODE_16_ONLY or unicodedata.category(ch)[0] != "M")
    if not punctuation:
        return stripped
    out: list[str] = []
    for ch in stripped:
        if _is_punct_run_char(ch):
            if out and out[-1] != " ":
                out.append(" ")
        else:
            out.append(ch)
    return "".join(out).strip(" ")


def _folded_matches(value: str | None, op: str, needle: str, *, punctuation: bool) -> bool:
    """String matching at one of the two folded strengths (#255).

    ``punctuation=False`` models ``.unaccented`` — diacritics only, the safe
    strength both Postgres ``unaccent`` and DuckDB ``COLLATE NOACCENT``
    provide. ``punctuation=True`` models ``.folded``, which additionally
    collapses runs of punctuation and separators.

    Both are opt-in, and both fold case as well — the ladder is cumulative, so
    each rung ignores everything the ones below it ignore. Since #222 the rung
    below is `caseless_matches`, not the plain ops: plain ``contains`` folds
    nothing at all.

    A needle that folds away matches **nothing**. The compiler guards this in
    SQL; modelled here so the property agrees rather than encoding the old
    match-everything behaviour.

    The case fold is `str.lower`, as in `caseless_matches` — the SQL is
    ``strip_accents(x) ILIKE ?``. This rung kept `str.casefold` after #222
    fixed the one below it, and the deep profile found it on ``Sß``, which
    casefolds to ``sss`` and so "ends with" ``S`` where SQL says it does not.
    """
    if value is None:
        return False
    folded_needle = _label_norm(needle, punctuation=punctuation)
    if needle and not folded_needle:
        # The fold destroyed a non-empty needle ("..." -> ""). The compiler
        # guards this so it matches nothing instead of everything. An already
        # empty needle is ordinary substring semantics and is not guarded.
        return False
    return _apply_op(
        _label_norm(value, punctuation=punctuation).lower(),
        op,
        folded_needle.lower(),
    )


# ── Predicate builders, mirroring the DSL's surface ──────────────────────────


def id_eq(value: str, *, negate: bool = False) -> Pred:
    """``code.id ==`` / ``!=`` — case-sensitive."""
    return lambda c, _codes: (c.id != value) if negate else (c.id == value)


def id_match(op: str, value: str) -> Pred:
    """``code.id.contains`` / ``starts_with`` / ``ends_with`` — exact since #222."""
    return lambda c, _codes: _matches(c.id, op, value)


def uri_eq(value: str, *, negate: bool = False) -> Pred:
    """``code.uri ==`` / ``!=`` (#444). Under ``!=`` a code with no uri drops out: ``NULL != ?`` is NULL."""
    if negate:
        return lambda c, _codes: c.uri is not None and c.uri != value
    return lambda c, _codes: c.uri == value


def uri_match(op: str, value: str) -> Pred:
    """``code.uri.contains`` / ``starts_with`` / ``ends_with``, as for the id."""
    return lambda c, _codes: _matches(c.uri, op, value)


def uri_is_null(*, negate: bool = False) -> Pred:
    """``code.uri.is_null()``."""
    return lambda c, _codes: (c.uri is not None) if negate else (c.uri is None)


def parent_is_null(*, negate: bool = False) -> Pred:
    """``code.parent_id.is_null()`` — the roots (or, negated, everything else)."""
    return lambda c, _codes: (c.parent_id is not None) if negate else (c.parent_id is None)


def parent_eq(value: str, *, negate: bool = False) -> Pred:
    """``code.parent_id ==`` / ``!=``.

    Under ``!=`` a root drops out: its ``parent_id`` is ``NULL`` and
    ``NULL != ?`` is ``NULL``, not true.
    """
    if negate:
        return lambda c, _codes: c.parent_id is not None and c.parent_id != value
    return lambda c, _codes: c.parent_id == value


def parent_match(op: str, value: str) -> Pred:
    """``code.parent_id.contains`` / ``starts_with`` / ``ends_with``."""
    return lambda c, _codes: _matches(c.parent_id, op, value)


def depth_eq(value: int, *, negate: bool = False) -> Pred:
    """``code.depth ==`` / ``!=``, against a freshly walked depth."""
    if negate:
        return lambda c, codes: depth(c.id, codes) != value
    return lambda c, codes: depth(c.id, codes) == value


def has_ancestor(ancestor_id: str, *, include_self: bool = False) -> Pred:
    """``descendants_of(ancestor_id)`` over the intrinsic parent chain.

    ``include_self`` adds the anchor itself. Defaults to False, matching the
    SKOS-aligned closure semantics the DSL uses (#274).
    """
    return lambda c, codes: ancestor_id in ancestors(c.id, codes) or (include_self and c.id == ancestor_id)


def is_ancestor_of(descendant_id: str, *, include_self: bool = False) -> Pred:
    """The code is on *descendant_id*'s ancestor chain — `broader_transitive`."""
    return lambda c, codes: c.id in ancestors(descendant_id, codes) or (include_self and c.id == descendant_id)


def has_no_narrower() -> Pred:
    """``code.has_no_narrower()`` — the leaves."""
    return lambda c, codes: not any(other.parent_id == c.id for other in codes)


def name_eq(lang: str, value: str, *, negate: bool = False) -> Pred:
    """``code.name[lang] ==`` / ``!=``. An absent language fails both."""
    if negate:
        return lambda c, _codes: c.name.get(lang) is not None and c.name.get(lang) != value
    return lambda c, _codes: c.name.get(lang) == value


def name_match(lang: str, op: str, value: str) -> Pred:
    """``code.name[lang].contains`` / ``starts_with`` / ``ends_with`` — plain ILIKE."""
    return lambda c, _codes: _matches(c.name.get(lang), op, value)


def name_folded_match(lang: str, op: str, value: str, *, punctuation: bool) -> Pred:
    """``code.name[lang].unaccented`` / ``.folded`` — see `_folded_matches`."""
    return lambda c, _codes: _folded_matches(c.name.get(lang), op, value, punctuation=punctuation)


def annotation_eq(ann_type: str, value: str, *, negate: bool = False) -> Pred:
    """``code.annotations[type] ==`` / ``!=``, against ``Annotation.title``.

    Existence-scoped: the compiler reaches annotations through an inner
    ``JOIN``, so a code carrying no annotation of this type never matches,
    whichever way the comparison runs. (That same join also duplicates a row
    when one code carries two matching annotations of a type — #254. The
    duplication is not modelled here; it is pinned by its own test.)
    """
    if negate:
        return lambda c, _codes: any(t is not None and t != value for t in _annotation_titles(c, ann_type))
    return lambda c, _codes: any(t == value for t in _annotation_titles(c, ann_type))


def annotation_present(ann_type: str, lang: str | None = None) -> Pred:
    """``annotations[type].exists()`` — and, with *lang*, the text variant.

    The only annotation predicate that reads no value. Every other one here
    filters `_annotation_titles` or `_annotation_texts` and so is implicitly
    scoped to an annotation that exists *and* has the value asked about; this
    one stops at the type. That difference is the whole of #292, and modelling
    it separately is what lets the property show the two are not the same.

    With *lang*, presence of localised text in that language — a note carrying
    only French text does not satisfy ``exists("en")``.
    """
    if lang is None:
        return lambda c, _codes: any(a.type == ann_type for a in c.annotations)
    return lambda c, _codes: any(a.type == ann_type and a.text.get(lang) is not None for a in c.annotations)


def annotation_match(ann_type: str, op: str, value: str) -> Pred:
    """``code.annotations[type].contains`` / ``starts_with`` / ``ends_with`` — a
    label field, so accent- and punctuation-insensitive."""
    return lambda c, _codes: any(_matches(t, op, value) for t in _annotation_titles(c, ann_type))


def annotation_folded_match(ann_type: str, op: str, value: str, *, punctuation: bool) -> Pred:
    """``code.annotations[type].unaccented`` / ``.folded``."""
    return lambda c, _codes: any(
        _folded_matches(t, op, value, punctuation=punctuation) for t in _annotation_titles(c, ann_type)
    )


def annotation_text_eq(ann_type: str, lang: str, value: str, *, negate: bool = False) -> Pred:
    """``code.annotations[type][lang] ==`` / ``!=``, against the localised text."""
    if negate:
        return lambda c, _codes: any(t is not None and t != value for t in _annotation_texts(c, ann_type, lang))
    return lambda c, _codes: any(t == value for t in _annotation_texts(c, ann_type, lang))


def annotation_text_match(ann_type: str, lang: str, op: str, value: str) -> Pred:
    """``code.annotations[type][lang].contains`` / ``starts_with`` / ``ends_with`` —
    plain ILIKE since #255."""
    return lambda c, _codes: any(_matches(t, op, value) for t in _annotation_texts(c, ann_type, lang))


def any_ancestor_matches(sub: Pred, *, hops: int | None = None) -> Pred:
    """``code.broader_transitive(hops).any(sub)`` — some ancestor satisfies *sub*.

    ``hops`` bounds the distance *upward from this code*, so ``hops=1`` means
    the direct parent only. Unbounded when ``None``.
    """

    def pred(c: Code, codes: Sequence[Code]) -> bool:
        by_id = {other.id: other for other in codes}
        chain = ancestors(c.id, codes)  # root first
        reachable = chain if hops is None else chain[max(0, len(chain) - hops) :]
        return any(sub(by_id[a], codes) for a in reachable if a in by_id)

    return pred


def any_descendant_matches(sub: Pred, *, hops: int | None = None) -> Pred:
    """``code.narrower_transitive(hops).any(sub)`` — some descendant satisfies *sub*.

    ``hops`` bounds the distance *downward from this code*, so ``hops=1`` means
    the direct children only. Unbounded when ``None``.
    """

    def pred(c: Code, codes: Sequence[Code]) -> bool:
        by_id = {other.id: other for other in codes}
        here = depth(c.id, codes)
        below = descendants(c.id, codes)
        reachable = below if hops is None else {d for d in below if depth(d, codes) - here <= hops}
        return any(sub(by_id[d], codes) for d in reachable if d in by_id)

    return pred


# ── Cross-artefact: predicates that reach into a Hierarchy ───────────────────


def in_hierarchy(
    node_code_ids: AbstractSet[str],
    level_of: Mapping[str, str | None],
    level_id: str | None = None,
) -> Pred:
    """``code.hierarchy(h)``, optionally ``.level(level_id)``.

    The DSL resolves membership by matching the node's ``code_urn`` against the
    code's, never by assuming ``node_id == code_id``. The caller therefore
    passes the *code ids the hierarchy's nodes point at*, resolved from the
    URNs, rather than the node ids.

    Args:
        node_code_ids: Ids of codes some node in the hierarchy references.
        level_of: Code id to the level id of the node referencing it, or None.
        level_id: When given, narrow to codes at that level.

    Returns:
        The predicate.
    """
    if level_id is None:
        return lambda c, _codes: c.id in node_code_ids
    return lambda c, _codes: c.id in node_code_ids and level_of.get(c.id) == level_id


def is_descendant_of_in_hierarchy(ancestor_code_id: str, hierarchy_descendants: Mapping[str, AbstractSet[str]]) -> Pred:
    """``code.descendants_of(id, hierarchy=h)`` — ancestry through the Hierarchy.

    A code's *intrinsic* parent chain and its position in a named Hierarchy are
    independent graphs; this walks the second. The caller supplies the mapping
    from an ancestor code id to the code ids beneath it in that hierarchy.
    """
    below = hierarchy_descendants.get(ancestor_code_id, frozenset())
    return lambda c, _codes: c.id in below


def in_set(values: Collection[object]) -> Pred:
    """``id.is_in(values)`` — membership in an eager set.

    An empty set matches nothing (#256, #261): the permitted set is empty, so
    no value satisfies it. The compiler emits ``FALSE`` rather than ``IN ()``,
    which is not valid SQL.
    """
    permitted = set(values)
    return lambda c, _rows: c.id in permitted


def depth_cmp(op: str, value: int) -> Pred:
    """``depth <`` / ``<=`` / ``>`` / ``>=`` — against a freshly walked depth (#263)."""
    ops = {
        "<": lambda d: d < value,
        "<=": lambda d: d <= value,
        ">": lambda d: d > value,
        ">=": lambda d: d >= value,
    }
    if op not in ops:
        msg = f"Unsupported comparison: {op}"
        raise ValueError(msg)
    return lambda c, rows: ops[op](depth(c.id, rows))
