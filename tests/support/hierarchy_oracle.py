"""A pure-Python evaluator for `hierarchy_query`, mirroring `tests.support.oracle`.

Same contract as the codelist oracle: a **specification, not an
implementation**, whose job is to disagree with
`sdmxlib.model.hierarchy_query` — which compiles the same DSL to SQL and runs
it in DuckDB. It shares no code with the module under test, re-derives depth
and ancestry by walking the tree, and stays small enough to read in one
sitting.

Why it is a separate module rather than a generalisation of `oracle`: a
hierarchy node is not a code. It has an id of its own *and* a reference to a
code, the same code may appear at several nodes, levels are a parallel axis to
depth, and parentage lives in the tree structure rather than in a ``parent_id``
field. An oracle that assumed codelist semantics would be quietly wrong in each
of those places.

Two semantics are modelled deliberately rather than smoothed over, because the
oracle's job is to state what the library does — whether it *should* is a
separate question, and one the properties exist to raise:

- **String matching is case-insensitive** (the compiler emits ``ILIKE``) while
  ``==`` is case-sensitive. Same asymmetry as the codelist side; see #222.
- **Annotation predicates are existence-scoped.** The compiler reaches
  annotations through an inner ``JOIN``, so a node carrying no annotation of
  the requested type is absent from the result — including under ``!=``, where
  a set-complement reading would expect it. Modelled here as "the node has such
  an annotation *and* its value matches".

Test-only; not packaged with sdmxlib.
"""

import unicodedata
from collections.abc import Callable, Collection, Sequence

import attrs

from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy


@attrs.frozen
class Row:
    """One flattened hierarchy node, with the parentage the tree implies.

    A node is identified by its **path** — its id may repeat in another branch
    (#439) — and ``path`` / ``parent_path`` are derived by walking
    `HierarchicalCode.children`, not read from the columns the storage writer
    fills, so any comparison against the query layer also checks the
    flattening and the bake.
    """

    id: str
    path: str
    parent_path: str | None
    #: The parent's own id — what ``parent.id`` compares.
    parent_id: str | None
    node: HierarchicalCode


#: A predicate over a single row, given every row for context.
Pred = Callable[[Row, Sequence[Row]], bool]


def flatten(h: Hierarchy) -> list[Row]:
    """Every node in *h*, depth-first, each with its path and its parent's."""
    rows: list[Row] = []

    def walk(nodes: Sequence[HierarchicalCode], parent: Row | None) -> None:
        for n in nodes:
            path = n.id if parent is None else f"{parent.path}.{n.id}"
            row = Row(
                id=n.id,
                path=path,
                parent_path=None if parent is None else parent.path,
                parent_id=None if parent is None else parent.id,
                node=n,
            )
            rows.append(row)
            walk(list(n.children), row)

    walk(list(h.tree), None)
    return rows


def parent_of(rows: Sequence[Row]) -> dict[str, str | None]:
    """Map each node's path to its parent's path."""
    return {r.path: r.parent_path for r in rows}


def ancestors(path: str, rows: Sequence[Row]) -> list[str]:
    """Ancestor paths of the node at *path*, root first. Empty for a root."""
    parents = parent_of(rows)
    chain: list[str] = []
    seen: set[str] = set()
    current = parents.get(path)
    while current is not None and current not in seen:
        seen.add(current)
        chain.append(current)
        current = parents.get(current)
    chain.reverse()
    return chain


def ancestor_ids(path: str, rows: Sequence[Row]) -> list[str]:
    """The ancestors' own ids, root first — what ``ancestors.id`` compares."""
    by_path = {r.path: r for r in rows}
    return [by_path[p].id for p in ancestors(path, rows)]


def depth(path: str, rows: Sequence[Row]) -> int:
    """Distance from the root — 0 for a node with no parent."""
    return len(ancestors(path, rows))


def descendants(path: str, rows: Sequence[Row]) -> set[str]:
    """Paths of every node having *path* among its ancestors."""
    return {r.path for r in rows if path in ancestors(r.path, rows)}


def evaluate(pred: Pred, rows: Sequence[Row]) -> list[str]:
    """Ids of the rows satisfying *pred*, ordered by node id.

    Node id, not tree order: `HierarchyNodeQuery` appends ``hn.node_id`` to
    every ``ORDER BY`` it builds, so an unsorted query is ordered by id. That
    differs from the codelist DSL, which preserves codelist order — one of the
    reasons this oracle is not a generalisation of that one.
    """
    return sorted(r.id for r in rows if pred(r, rows))


# ── Field readers ────────────────────────────────────────────────────────────


def _level_id(r: Row) -> str | None:
    return r.node.level.id if r.node.level is not None else None


def _code_urn(r: Row) -> str | None:
    return str(r.node.code.urn) if r.node.code is not None else None


def _annotation_titles(r: Row, ann_type: str) -> list[str | None]:
    """Titles of every annotation on *r* of type *ann_type*, join-order agnostic."""
    return [a.title for a in r.node.annotations if a.type == ann_type]


def _annotation_texts(r: Row, ann_type: str, lang: str) -> list[str | None]:
    """Localised texts of every annotation on *r* of type *ann_type*."""
    return [a.text.get(lang) for a in r.node.annotations if a.type == ann_type]


# ── String matching, as the compiler emits it ────────────────────────────────


def _matches(value: str | None, op: str, needle: str) -> bool:
    """``LIKE`` semantics for one of the three string ops — no fold at all.

    Case-sensitive since #222. ``node_id``, ``level_id`` and especially
    ``code_urn`` are case-significant identifiers, so the old ``ILIKE`` was
    hardest to defend precisely here: a URN differing only in case names a
    different artefact.

    ``None`` never matches: SQL ``NULL LIKE ?`` is ``NULL``, which a ``WHERE``
    clause treats as false.
    """
    if value is None:
        return False
    return _apply_op(value, op, needle)


def caseless_matches(value: str | None, op: str, needle: str) -> bool:
    """The `Fold.CASE` rung — ``node.id.caseless.contains(...)`` (#222).

    Restated rather than imported from `tests.support.oracle`, per the rule
    that two oracles sharing a helper can be wrong the same way. The
    lower-vs-casefold trap is written out there: SQL
    agrees with `str.lower`, not with `str.casefold`, and this oracle used
    casefold until #222 without ever failing for it.
    """
    if value is None:
        return False
    return _apply_op(value.lower(), op, needle.lower())


def caseless_eq(value: str | None, other: str, *, negate: bool) -> bool:
    """``COLLATE NOCASE =`` / ``!=`` — ``node.id.caseless == x`` (#222).

    A ``None`` value satisfies neither, because both comparisons are ``NULL``
    in SQL and ``WHERE NULL`` keeps no row.
    """
    if value is None:
        return False
    lowered, wanted = value.lower(), other.lower()
    return lowered != wanted if negate else lowered == wanted


def _apply_op(haystack: str, op: str, fragment: str) -> bool:
    if op == "contains":
        return fragment in haystack
    if op == "starts_with":
        return haystack.startswith(fragment)
    if op == "ends_with":
        return haystack.endswith(fragment)
    msg = f"Unsupported string op: {op}"
    raise ValueError(msg)


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


def _whole(ch: str) -> bool:
    """Whether `strip_accents` keeps `ch` as it is where NFD would split it.

    Restated, not imported (rule 1), and derived differently from
    `tests.support.oracle`: a Hangul syllable by its Unicode name rather than
    its code-point range (#494). DuckDB leaves a precomposed syllable whole;
    NFD gives its jamo.
    """
    return ch in _UNICODE_16_ONLY or unicodedata.name(ch, "").startswith("HANGUL SYLLABLE ")


def _label_norm(value: str, *, punctuation: bool) -> str:
    """The fold `sdmxlib.model._label_match.norm_sql` applies in SQL.

    Restated here rather than imported from `tests.support.oracle`, for the
    same reason the rest of this module is: two oracles that share a helper can
    be wrong the same way, and that is the one failure mode differential
    testing cannot catch.
    """
    decomposed = "".join(ch if _whole(ch) else unicodedata.normalize("NFD", ch) for ch in value)
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
    """``.unaccented`` (punctuation=False) / ``.folded`` (True) semantics.

    `hierarchy_query` had neither before #255 — the same query against a
    hierarchy holding the same labels as a codelist returned nothing, because
    only `code_query` folded. Both DSLs now expose both strengths explicitly.

    A needle that folds away matches nothing, matching the compiler's guard.

    Case folds with `str.lower`, not `str.casefold` — SQL is ``ILIKE``, and
    ``ß`` casefolds to ``ss`` where ``ILIKE`` leaves it alone. This rung kept
    `str.casefold` after #222 fixed the caseless one below it.
    """
    if value is None:
        return False
    folded_needle = _label_norm(needle, punctuation=punctuation)
    if needle and not folded_needle:
        # The fold destroyed a non-empty needle ("..." -> ""). The compiler
        # guards this so it matches nothing instead of everything. An already
        # empty needle is ordinary substring semantics and is not guarded.
        return False
    haystack = _label_norm(value, punctuation=punctuation).lower()
    return _apply_op(haystack, op, folded_needle.lower())


# ── Predicate builders, mirroring the DSL's surface ──────────────────────────


# ── Boolean combinators (#262) ───────────────────────────────────────────────
#
# Restated rather than imported from `tests.support.oracle`, per rule 1 in
# CLAUDE.md: two oracles sharing a helper can be wrong the same way, which is
# the one failure mode differential testing cannot catch. Three lambdas is a
# cheap price for keeping them independent.
#
# A `Pred` is two-valued and SQL is three-valued. `WHERE` keeps a row only when
# the clause is TRUE, so the model is the collapse ``unknown -> false``, which
# commutes with AND and OR but not with NOT — ``NOT NULL`` is NULL (row
# dropped) where ``not False`` is True (row kept). So `and_` / `or_` take any
# operands and `not_` is faithful only over fields that cannot be NULL. Of the
# node fields, ``level``, ``code`` and ``parent`` are all nullable; ``id`` and
# ``depth`` are not.


def and_(*preds: Pred) -> Pred:
    """All of *preds* hold."""
    return lambda r, rs: all(p(r, rs) for p in preds)


def or_(*preds: Pred) -> Pred:
    """At least one of *preds* holds."""
    return lambda r, rs: any(p(r, rs) for p in preds)


def not_(pred: Pred) -> Pred:
    """*pred* does not hold."""
    return lambda r, rs: not pred(r, rs)


def id_eq(value: str, *, negate: bool = False) -> Pred:
    """``node.id ==`` / ``!=`` — case-sensitive."""
    return lambda r, _rows: (r.id != value) if negate else (r.id == value)


def id_match(op: str, value: str) -> Pred:
    """``node.id.contains`` / ``starts_with`` / ``ends_with`` — case-INsensitive."""
    return lambda r, _rows: _matches(r.id, op, value)


def uri_eq(value: str, *, negate: bool = False) -> Pred:
    """``node.uri ==`` / ``!=`` (#444). Under ``!=`` a node with no uri drops out: ``NULL != ?`` is NULL."""
    if negate:
        return lambda r, _rows: r.node.uri is not None and r.node.uri != value
    return lambda r, _rows: r.node.uri == value


def uri_match(op: str, value: str) -> Pred:
    """``node.uri.contains`` / ``starts_with`` / ``ends_with``, as for the id."""
    return lambda r, _rows: _matches(r.node.uri, op, value)


def uri_is_null(*, negate: bool = False) -> Pred:
    """``node.uri.is_null()``."""
    return lambda r, _rows: (r.node.uri is not None) if negate else (r.node.uri is None)


def parent_eq(value: str, *, negate: bool = False) -> Pred:
    """``node.parent_id ==`` / ``!=``.

    Under ``!=`` a root is excluded: its ``parent_node_id`` is ``NULL`` and
    ``NULL != ?`` is ``NULL``, not true.
    """
    if negate:
        return lambda r, _rows: r.parent_id is not None and r.parent_id != value
    return lambda r, _rows: r.parent_id == value


def parent_is_null(*, negate: bool = False) -> Pred:
    """``node.parent_id.is_null()`` — the roots (or, negated, everything else)."""
    return lambda r, _rows: (r.parent_id is not None) if negate else (r.parent_id is None)


def depth_eq(value: int, *, negate: bool = False) -> Pred:
    """``node.depth ==`` / ``!=``, against a freshly walked depth."""
    if negate:
        return lambda r, rows: depth(r.path, rows) != value
    return lambda r, rows: depth(r.path, rows) == value


def level_eq(value: str, *, negate: bool = False) -> Pred:
    """``node.level ==`` / ``!=``. A level-less node fails both, as SQL ``NULL`` does."""
    if negate:
        return lambda r, _rows: _level_id(r) is not None and _level_id(r) != value
    return lambda r, _rows: _level_id(r) == value


def level_match(op: str, value: str) -> Pred:
    """``node.level.contains`` / ``starts_with`` / ``ends_with``."""
    return lambda r, _rows: _matches(_level_id(r), op, value)


def level_is_null(*, negate: bool = False) -> Pred:
    """``node.level.is_null()`` — nodes below the deepest declared level."""
    return lambda r, _rows: (_level_id(r) is not None) if negate else (_level_id(r) is None)


def code_eq(value: str, *, negate: bool = False) -> Pred:
    """``node.code ==`` / ``!=``, comparing the referenced Code's URN string."""
    if negate:
        return lambda r, _rows: _code_urn(r) is not None and _code_urn(r) != value
    return lambda r, _rows: _code_urn(r) == value


def code_match(op: str, value: str) -> Pred:
    """``node.code.contains`` / ``starts_with`` / ``ends_with`` over the URN string."""
    return lambda r, _rows: _matches(_code_urn(r), op, value)


def code_is_null(*, negate: bool = False) -> Pred:
    """``node.code.is_null()`` — nodes that reference no Code."""
    return lambda r, _rows: (_code_urn(r) is not None) if negate else (_code_urn(r) is None)


def annotation_eq(ann_type: str, value: str, *, negate: bool = False) -> Pred:
    """``node.annotations[type] ==`` / ``!=``, against ``Annotation.title``.

    Existence-scoped: see the module docstring. A node with no annotation of
    this type never matches, whichever way the comparison runs.
    """
    if negate:
        return lambda r, _rows: any(t is not None and t != value for t in _annotation_titles(r, ann_type))
    return lambda r, _rows: any(t == value for t in _annotation_titles(r, ann_type))


def annotation_present(ann_type: str, lang: str | None = None) -> Pred:
    """``annotations[type].exists()`` — presence of the type, no value read.

    Restated rather than imported from `tests.support.oracle`, per rule 1 in
    CLAUDE.md. It is four lines, and two oracles that share a helper can be
    wrong the same way.
    """
    if lang is None:
        return lambda r, _rows: any(a.type == ann_type for a in r.node.annotations)
    return lambda r, _rows: any(a.type == ann_type and a.text.get(lang) is not None for a in r.node.annotations)


def annotation_match(ann_type: str, op: str, value: str) -> Pred:
    """``node.annotations[type].contains`` / ``starts_with`` / ``ends_with``."""
    return lambda r, _rows: any(_matches(t, op, value) for t in _annotation_titles(r, ann_type))


def annotation_text_eq(ann_type: str, lang: str, value: str, *, negate: bool = False) -> Pred:
    """``node.annotations[type][lang] ==`` / ``!=``, against the localised text."""
    if negate:
        return lambda r, _rows: any(t is not None and t != value for t in _annotation_texts(r, ann_type, lang))
    return lambda r, _rows: any(t == value for t in _annotation_texts(r, ann_type, lang))


def annotation_text_match(ann_type: str, lang: str, op: str, value: str) -> Pred:
    """``node.annotations[type][lang].contains`` / ``starts_with`` / ``ends_with``."""
    return lambda r, _rows: any(_matches(t, op, value) for t in _annotation_texts(r, ann_type, lang))


def has_ancestor(ancestor_path: str, *, max_absolute_depth: int | None = None, include_self: bool = False) -> Pred:
    """A node under *ancestor_path*, optionally no deeper than an absolute depth.

    The depth bound is absolute — measured from the root, not from
    *ancestor_path* — because that is what `LazyHierarchy.narrower_transitive`
    computes before handing the predicate to the compiler.
    """

    def pred(r: Row, rows: Sequence[Row]) -> bool:
        reachable = ancestor_path in ancestors(r.path, rows) or (include_self and r.path == ancestor_path)
        if not reachable:
            return False
        return max_absolute_depth is None or depth(r.path, rows) <= max_absolute_depth

    return pred


def is_ancestor_of(descendant_path: str, *, min_absolute_depth: int | None = None, include_self: bool = False) -> Pred:
    """A node above *descendant_path*, optionally no shallower than an absolute depth."""

    def pred(r: Row, rows: Sequence[Row]) -> bool:
        reachable = r.path in ancestors(descendant_path, rows) or (include_self and r.path == descendant_path)
        if not reachable:
            return False
        return min_absolute_depth is None or depth(r.path, rows) >= min_absolute_depth

    return pred


def has_no_narrower() -> Pred:
    """``leaves()`` — no other node names this one as parent."""
    return lambda r, rows: not any(other.parent_path == r.path for other in rows)


def any_ancestor_matches(sub: Pred, *, hops: int | None = None) -> Pred:
    """``node.broader_transitive(hops).any(sub)`` — some ancestor satisfies *sub*.

    ``hops`` bounds the distance *upward from this node*, so ``hops=1`` means
    the direct parent only. Unbounded when ``None``.
    """

    def pred(r: Row, rows: Sequence[Row]) -> bool:
        by_path = {row.path: row for row in rows}
        chain = ancestors(r.path, rows)  # root first
        reachable = chain if hops is None else chain[max(0, len(chain) - hops) :]
        return any(sub(by_path[a], rows) for a in reachable if a in by_path)

    return pred


def any_descendant_matches(sub: Pred, *, hops: int | None = None) -> Pred:
    """``node.narrower_transitive(hops).any(sub)`` — some descendant satisfies *sub*.

    ``hops`` bounds the distance *downward from this node*, so ``hops=1`` means
    the direct children only. Unbounded when ``None``.
    """

    def pred(r: Row, rows: Sequence[Row]) -> bool:
        by_path = {row.path: row for row in rows}
        here = depth(r.path, rows)
        below = descendants(r.path, rows)
        reachable = below if hops is None else {d for d in below if depth(d, rows) - here <= hops}
        return any(sub(by_path[d], rows) for d in reachable if d in by_path)

    return pred


def annotation_folded_match(ann_type: str, op: str, value: str, *, punctuation: bool) -> Pred:
    """``node.annotations[type].unaccented`` / ``.folded``."""
    return lambda r, _rows: any(
        _folded_matches(t, op, value, punctuation=punctuation) for t in _annotation_titles(r, ann_type)
    )


def in_set(values: Collection[object]) -> Pred:
    """``id.is_in(values)`` — membership in an eager set.

    An empty set matches nothing (#256, #261): the permitted set is empty, so
    no value satisfies it. The compiler emits ``FALSE`` rather than ``IN ()``,
    which is not valid SQL.
    """
    permitted = set(values)
    return lambda r, _rows: r.id in permitted


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
    return lambda r, rows: ops[op](depth(r.id, rows))
