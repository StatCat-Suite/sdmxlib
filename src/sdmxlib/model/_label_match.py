"""String-matching SQL shared by the two query compilers (#255).

Package-internal: plain names in a private module, per CLAUDE.md rule 4. It is
deliberately *not* in `sdmxlib.sql`, which is the public cross-library seam —
these fragments are an implementation detail of `code_query` and
`hierarchy_query`, not part of the contract statcatlib composes against.

Four matching strengths, exposed on the DSLs as the plain string ops plus
three namespaces:

===============  ==============================  ===========================
`Fold`           DSL spelling                    Collapses
===============  ==============================  ===========================
``NONE``         ``id.contains(x)``              nothing (``LIKE``)
``CASE``         ``id.caseless...``              case (``ILIKE``)
``ACCENTS``      ``name["en"].unaccented...``    case + diacritics
``FULL``         ``name["en"].folded...``        case + diacritics + punctuation
===============  ==============================  ===========================

The rungs are totally ordered and each is a superset of the one above it, a
property `tests/unit/test_properties_code_query.py` asserts directly. ``CASE``
is available on every string ref; ``ACCENTS`` and ``FULL`` only on label refs,
because an id is not prose (#255).

``NONE`` folded case until #222 — it was `ILIKE`, so the floor of the ladder
was not actually a floor and its name was a lie. That made ``id == "cl_freq"``
find nothing while ``id.contains("cl_freq")`` found ``CL_FREQ``, with no way to
ask for either behaviour explicitly. Every comparable library defaults to
case-sensitive and offers the fold by name — polars (``(?i)``), SQLAlchemy
(``icontains`` / ``.collate("NOCASE")``), Django (``__icontains``), pandas
(``case=``) — and none of them folds silently. The fold was also the expensive
choice: on 500k labels ``ILIKE`` measured 8.66 ms against ``LIKE``'s 0.97 ms.

``ACCENTS`` is the standard, safe strength — it is what Postgres ``unaccent``
and DuckDB ``COLLATE NOACCENT`` do, and it changes the result only for needles
that differ by a diacritic. ``FULL`` additionally collapses runs of Unicode
punctuation and separators to one space, so "Trois Rivieres" finds
"Trois-Rivières" and "St John" finds "St. John's".

``FULL`` is materially more aggressive and that is why it has to be asked for
by name. Measured against a small fixture: ``"REF_AREA"`` also matches
``REF AREA`` (``_`` is connector punctuation), ``"50%"`` also matches
``50 cents``, and before the guard below a needle of ``"..."`` matched every
row. Those are acceptable in an opt-in search affordance and were not
acceptable as the silent default, which is what #255 changed.

Punctuation-only folding is deliberately not offered. It carries every one of
those surprises while failing to deliver its own motivating case — matching
"Trois Rivieres" against "Trois-Rivières" needs the accent fold too — so no
caller should ever select it.
"""

from enum import Enum

#: Collapses runs of Unicode punctuation and separators to a single space.
#: ``\p{P}\p{Z}`` rather than a naive ``[^a-zA-Z0-9]`` so letters of every
#: script survive — Inuktitut syllabics pass through unchanged, which a
#: character-class negation would destroy.
_PUNCT_RUN = r"[\p{P}\p{Z}\s]+"

#: Escape character for ``ILIKE``. SQL ``LIKE`` has no default one, so it must
#: be stated or the backslashes `ilike_pattern` writes match literally — the
#: #222 defect, where every filter containing ``_`` returned nothing.
ESCAPE = r"ESCAPE '\'"


#: Case-insensitive *equality*. Deliberately not ``ILIKE``: a value used as an
#: equality operand is not a pattern, so ``ILIKE`` would make ``_`` and ``%``
#: in a caller's id behave as wildcards unless escaped — ``id.caseless ==
#: "A_B"`` would match ``axb``. A collation compares without a pattern at all.
#: Measured equivalent to ``ILIKE`` and to ``lower(a) = lower(b)`` on every
#: case pair tried, including the ones where all three differ from Python's
#: ``str.casefold`` (``Straße``/``STRASSE``, ``ﬁle``/``FILE``, ``İstanbul``).
NOCASE = "COLLATE NOCASE"


class Fold(Enum):
    """How much of a string difference a match should ignore.

    Ordered: each member ignores everything the one before it does. `NONE` is
    the floor and ignores nothing.
    """

    NONE = "none"
    CASE = "case"
    ACCENTS = "accents"
    FULL = "full"


def ilike_pattern(op: str, value: str) -> str:
    """Wrap *value* in ``%`` for *op*, escaping the wildcards it contains.

    Only for `Fold.NONE`, `Fold.CASE` and `Fold.ACCENTS`. `Fold.FULL` needs no escaping
    because ``%`` and ``_`` are punctuation and the fold removes them, and it
    wraps in SQL rather than here so both sides go through the same normaliser.
    """
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    if op == "contains":
        return f"%{escaped}%"
    if op == "starts_with":
        return f"{escaped}%"
    if op == "ends_with":
        return f"%{escaped}"
    msg = f"Unsupported string op: {op}"
    raise NotImplementedError(msg)


def eq_sql(col_expr: str, fold: Fold, value: object, *, negate: bool) -> tuple[str, list[object]]:
    """Build an equality fragment and its binds for one column and strength.

    Only `Fold.NONE` and `Fold.CASE` reach here: `FoldedOps` carries the string
    ops alone, so ``.unaccented ==`` does not exist. That is #255's decision
    left standing — an accent-insensitive *equality* on a label is a question
    nobody has asked, whereas a case-insensitive id lookup is the complaint
    #222 opens with.

    Args:
        col_expr: The column, already qualified.
        fold: `Fold.NONE` for exact, `Fold.CASE` for case-insensitive.
        value: The raw user value; never interpolated, always bound.
        negate: Emit ``!=`` rather than ``=``.

    Returns:
        ``(fragment, params)``.
    """
    op = "!=" if negate else "="
    if fold is Fold.NONE:
        return f"{col_expr} {op} ?", [value]
    if fold is Fold.CASE:
        return f"{col_expr} {NOCASE} {op} ?", [value]
    msg = f"Equality is not offered at {fold} — only the string ops fold accents or punctuation."
    raise NotImplementedError(msg)


def norm_sql(expr: str, fold: Fold) -> str:
    """Wrap a SQL expression in the normaliser for *fold*."""
    if fold in (Fold.NONE, Fold.CASE):
        return expr
    accented = f"strip_accents({expr})"
    if fold is Fold.ACCENTS:
        return accented
    return f"trim(regexp_replace({accented}, '{_PUNCT_RUN}', ' ', 'g'))"


def match_sql(col_expr: str, op: str, fold: Fold, value: str) -> tuple[str, list[object]]:
    """Build a string-match fragment and its binds for one column and strength.

    Args:
        col_expr: The column, already qualified — may be a bare identifier or
            an expression such as ``hn.name[?]``.
        op: ``"contains"`` / ``"starts_with"`` / ``"ends_with"``.
        fold: Matching strength.
        value: The raw user value; never interpolated, always bound.

    Returns:
        ``(fragment, params)``. For a folded match the value is bound twice —
        once for the comparison and once for the empty-needle guard.
    """
    if "\x00" in value:
        # DuckDB rejects a NUL-bearing LIKE pattern when the *left* operand is a
        # non-trivial expression — which every folded match is, since the column
        # goes through `strip_accents`. Isolated on 1.5.3:
        #
        #     SELECT trim(regexp_replace(strip_accents(s), ...)) ILIKE '%' || ?
        #     -- ["\x00"] -> Syntax Error: Like pattern must not end with escape character!
        #
        # The message names the wrong cause, and the same value against a plain
        # `ILIKE` succeeds — so the behaviour is inconsistent as well as
        # misleading. #250 allows a driver-level rejection *if it is a clear
        # error*; this is not one, so the rejection happens here instead, at the
        # one place every string match is built, and applies to every strength
        # so that plain and folded do not disagree about the same value.
        msg = (
            "A filter value cannot contain a NUL character (\\x00) — it is not a "
            "meaningful value in an SDMX identifier or label, and DuckDB rejects "
            "the resulting LIKE pattern with an unrelated error message."
        )
        raise ValueError(msg)
    if fold in (Fold.NONE, Fold.CASE):
        # The one place the ladder's floor is distinguishable from its second
        # rung: `LIKE` against `ILIKE`, same pattern, same escape.
        like = "ILIKE" if fold is Fold.CASE else "LIKE"
        return f"{col_expr} {like} ? {ESCAPE}", [ilike_pattern(op, value)]

    col = norm_sql(col_expr, fold)
    val = norm_sql("?", fold)
    # The guard below exists for a needle the fold *destroys* — "..." becoming
    # "". A needle that was already empty is ordinary substring semantics and
    # matches everything, exactly as plain `contains("")` does; guarding it too
    # would make a folded match *less* permissive than an unfolded one and
    # break `plain ⊆ unaccented ⊆ folded`, which is how the property caught it.
    # Keyed on the raw value, so no second Python-side fold can drift from SQL.
    guarded = bool(value)
    if fold is Fold.ACCENTS:
        # Diacritics do not touch ``%`` or ``_``, so wildcards survive the fold
        # and still need escaping. Wrap in Python as for an unfolded match.
        # Guarded like FULL: a needle of nothing but combining marks folds away
        # too, and would otherwise leave ``ILIKE '%%'``.
        if not guarded:
            return f"{col} ILIKE {val} {ESCAPE}", [ilike_pattern(op, value)]
        return f"({col} ILIKE {val} {ESCAPE} AND {norm_sql('?', fold)} <> '')", [ilike_pattern(op, value), value]

    wrapped = {"contains": f"'%' || {val} || '%'", "starts_with": f"{val} || '%'", "ends_with": f"'%' || {val}"}
    if op not in wrapped:
        msg = f"Unsupported string op: {op}"
        raise NotImplementedError(msg)
    if not guarded:
        return f"{col} ILIKE {wrapped[op]}", [value]
    # A needle that folds away must match nothing rather than everything: "..."
    # normalises to the empty string, and ``ILIKE '%%'`` is every non-null row.
    # Guarded in SQL against the same normaliser so no second, Python-side fold
    # can drift from it.
    guard = f"{norm_sql('?', fold)} <> ''"
    return f"({col} ILIKE {wrapped[op]} AND {guard})", [value, value]
