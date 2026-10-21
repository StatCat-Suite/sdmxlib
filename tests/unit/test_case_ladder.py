"""Case is a rung on the fold ladder, not the floor of it (#222).

`==` compiled to `=` and `contains` to `ILIKE`, so within one accessor they
disagreed about case and neither was documented:

    q.code.id == "cl_freq"           -> []            case-sensitive
    q.code.id.str.contains("cl_freq")    -> ["CL_FREQ"]    case-INsensitive

Both readings are defensible; having both, silently, is not. SDMX `IDType`
treats `F` and `f` as distinct codes that can legitimately coexist, so
`contains("F")` returning both was a wrong answer rather than a convenience.

## Why this is #255's rung rather than a new axis

#255 built a ladder — `NONE` / `ACCENTS` / `FULL`, spelled as the plain ops
plus `.unaccented` / `.folded` — and made the accent and punctuation folds
opt-in. It left case folded at *every* strength, including `NONE`, so the floor
of the ladder was not a floor and its name was a lie. #222 inserts `CASE`
where it belongs and the floor becomes exact.

Measured before choosing, because every peer had already answered it and the
answers agree: polars `str.contains` is case-sensitive with an inline `(?i)`
opt-in, SQLAlchemy has `contains`/`icontains` and `.collate("NOCASE")`, Django
`__contains`/`__icontains`, pandas `case=True`. None folds silently. The fold
was also the expensive default — 8.66 ms against `LIKE`'s 0.97 ms on 500k
labels.

## Why `==` gets the rung and `.unaccented` does not

`.collate("NOCASE") == x` is ordinary SQLAlchemy and `__iexact` ordinary
Django, so equality-under-a-collation is precedented. An accent-insensitive
*equality* is neither precedented nor asked for, so `FoldedOps` still stops at
the string ops and only `CaselessOps` adds `==`.
"""

import duckdb
import pytest

from sdmxlib.local import LocalRegistry
from sdmxlib.model._label_match import Fold
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.query.accessors import q
from tests.support.narrow import present

pytestmark = pytest.mark.unit


def _one(cur: duckdb.DuckDBPyConnection, sql: str, params: list[object] | None = None) -> object:
    """The first column of the single row `sql` returns.

    `fetchone()` is `tuple | None` because a statement may return no rows;
    every use here is a scalar `SELECT` that always does.
    """
    row = cur.execute(sql, params or []).fetchone()
    assert row is not None, f"expected a row from: {sql}"
    first: object = row[0]
    return first


_CODES = [("F", "Freq"), ("f", "freq"), ("CL_FREQ", "CL Freq"), ("cl_freq", "cl freq")]


@pytest.fixture
def codes():
    with LocalRegistry(":memory:") as reg:
        reg.add(
            Codelist(
                id="CL",
                maintainer="AG",
                version="1.0",
                name={"en": "Codes"},
                codes=[Code(id=i, name={"en": n}) for i, n in _CODES],
            )
        )
        yield present(reg.get(Codelist.urn_for(agency="AG", id="CL", version="1.0"))).codes


# ── The asymmetry the issue reported ─────────────────────────────────────────


def test_equality_and_substring_now_agree_about_case(codes) -> None:
    """The defect, stated as the agreement that was missing.

    Not "contains is case-sensitive" but "the two ops answer the same question
    about case" — which is what a caller was entitled to assume and could not.
    """
    assert codes.filter(q.code.id == "cl_freq").ids() == ["cl_freq"]
    assert codes.filter(q.code.id.str.contains("cl_freq")).ids() == ["cl_freq"]
    assert codes.filter(q.code.id.str.caseless == "cl_freq").ids() == ["CL_FREQ", "cl_freq"]
    assert codes.filter(q.code.id.str.caseless.contains("cl_freq")).ids() == ["CL_FREQ", "cl_freq"]


def test_two_codes_differing_only_in_case_stay_distinct(codes) -> None:
    """The spec case. `F` and `f` are different codes, and the default says so."""
    assert codes.filter(q.code.id == "F").ids() == ["F"]
    assert codes.filter(q.code.id.str.contains("F")).ids() == ["F", "CL_FREQ"]
    assert codes.filter(q.code.id.str.caseless.contains("F")).ids() == [i for i, _ in _CODES]


def test_labels_get_the_same_ladder_as_ids(codes) -> None:
    """Not an id-field quirk.

    The issue's "Not affected: label fields" was already stale when it was
    read: #255 had removed the implicit label folds, leaving labels exactly as
    case-insensitive as ids and for the same reason.
    """
    assert codes.filter(q.code.name["en"].str.contains("Freq")).ids() == ["F", "CL_FREQ"]
    assert codes.filter(q.code.name["en"].str.contains("freq")).ids() == ["f", "cl_freq"]
    assert codes.filter(q.code.name["en"].str.caseless.contains("freq")).ids() == [i for i, _ in _CODES]


# ── The ladder ───────────────────────────────────────────────────────────────


def test_the_rungs_are_ordered_and_the_floor_folds_nothing(codes) -> None:
    """One example of the law the properties assert over generated input.

    Pinned here as well because the law alone cannot catch a *missing* fold:
    if `.caseless` silently stopped folding, `plain <= caseless` would still
    hold. This example fails.
    """
    plain = set(codes.filter(q.code.name["en"].str.contains("freq")).ids())
    caseless = set(codes.filter(q.code.name["en"].str.caseless.contains("freq")).ids())
    assert plain < caseless, "the caseless rung must be strictly more permissive here"
    assert list(Fold) == [Fold.NONE, Fold.CASE, Fold.ACCENTS, Fold.FULL]


def test_the_accent_rungs_still_refuse_equality(codes) -> None:
    """#255's decision, left standing rather than swept along.

    `.caseless` gained `==` because there is a real question behind it. There
    is no equivalent question for accents, so `FoldedOps` is unchanged and the
    asymmetry is deliberate.
    """
    assert hasattr(q.code.name["en"].str.caseless, "contains")
    assert (q.code.name["en"].str.caseless == "x") is not None
    from sdmxlib.query import _expr

    ops = q.code.name["en"].str.unaccented
    # `==` on a plain object is identity comparison, not a predicate — the
    # point is that it does not *build* one.
    assert not isinstance(ops == "x", _expr.AnyPred)


def test_caseless_ops_hash_by_identity_like_every_other_expression(codes) -> None:
    """The answer reversed with #306, and it reversed for consistency.

    `FieldRef` refused to hash at all: an `__eq__` that returns a predicate
    makes an object useless as a dict key, and a silently-hashable one
    compares by identity while `==` does something else. True — but it is true
    of *every* expression in the path language, where `==` builds a `Compare`
    on a column, a label and an aggregate alike, and all of them hash by
    identity. Refusing on this one rung would make `.caseless` the single
    expression that cannot go in a set.

    What made refusal affordable before was that only two classes had the
    predicate-returning `__eq__`. Query identity is `canonical_key()`, which is
    structural and is what a cache should key on; `hash()` here is identity and
    says so.
    """
    ops = q.code.id.str.caseless
    assert hash(ops) == hash(ops)
    assert len({ops, ops, q.code.id.str.caseless}) == 2, "identity, not structure"


# ── Why equality uses a collation and not ILIKE ──────────────────────────────


def test_a_wildcard_in_a_caseless_equality_stays_literal(codes) -> None:
    """The reason `eq_sql` emits `COLLATE NOCASE =` rather than `ILIKE`.

    An equality operand is not a pattern. Implementing `.caseless ==` as
    `ILIKE` is the obvious shortcut and is silently wrong: `_` and `%` in a
    caller's id would become wildcards, so `id.caseless == "CL_FREQ"` would
    also match `CLxFREQ`. SDMX ids contain `_` constantly, so this is the
    common case, not an edge one.
    """
    assert codes.filter(q.code.id.str.caseless == "cl%").ids() == []
    assert codes.filter(q.code.id.str.caseless == "cl_freq").ids() == ["CL_FREQ", "cl_freq"]
    sql, _ = codes.filter(q.code.id.str.caseless == "cl_freq").to_sql()
    assert "COLLATE NOCASE" in sql
    assert "ILIKE" not in sql


def test_a_null_satisfies_neither_caseless_equality_nor_its_negation() -> None:
    """`NULL = ?` and `NULL != ?` are both NULL, so a WHERE keeps neither.

    A collation does not change three-valued logic, and modelling `negate` as
    a Python `not` in the oracle would have claimed it did.
    """
    with LocalRegistry(":memory:") as reg:
        reg.add(
            Codelist(
                id="CL2",
                maintainer="AG",
                version="1.0",
                name={"en": "c"},
                codes=[Code(id="A", name={"en": "a"}), Code(id="B", name={"en": "b"}, parent_id="A")],
            )
        )
        codes = present(reg.get(Codelist.urn_for(agency="AG", id="CL2", version="1.0"))).codes
        assert codes.filter(q.code.parent.id.str.caseless == "a").ids() == ["B"]
        assert codes.filter(q.code.parent.id.str.caseless != "a").ids() == [], (
            "the root's NULL parent satisfies neither"
        )


# ── What deliberately did not change ─────────────────────────────────────────


def test_is_in_stays_exact(codes) -> None:
    """No peer offers a caseless membership test.

    polars `is_in`, SQLAlchemy `in_` and Django `__in` are all exact, and a
    caseless one would need either a collation on every element or a fold on
    both sides of a join. Left out on purpose rather than overlooked; #222's
    resolution names it.
    """
    assert codes.filter(q.code.id.is_in(["cl_freq"])).ids() == ["cl_freq"]
    assert codes.filter(q.code.id.is_in(["CL_FREQ", "cl_freq"])).ids() == ["CL_FREQ", "cl_freq"]


def test_caseless_is_on_every_string_ref_not_only_labels() -> None:
    """Unlike `.unaccented` / `.folded`, which stay label-only.

    #255 withheld the accent rungs from ids deliberately — an id is not prose,
    and folding `REF_AREA` into `REF AREA` helps nobody. Case is different:
    the id fields are where the complaint came from.
    """
    for ref in (q.code.id, q.code.parent.id, q.code.name["en"], q.hcode.id):
        assert hasattr(ref.str, "caseless"), ref
    assert not hasattr(q.code.id.str, "unaccented"), "ids still get no accent rung"
    assert hasattr(q.code.name["en"].str, "unaccented")


# ── Where Python and DuckDB disagree about case ──────────────────────────────


def test_sql_case_folding_is_not_python_case_folding() -> None:
    """Pinned as examples because Hypothesis cannot reach them.

    The oracles model `.caseless` with `str.lower`. That is close but not
    exact, and the gap is invisible to the properties: a generated needle is a
    slice of the generated value, so both sides fold identically and the fold
    cancels. Only a deliberately-chosen case *pair* discriminates, which is
    exactly the "argument-shape errors belong in a static test" rule applied to
    a data question.

    The row that matters most is the first: the oracles used `str.casefold`
    until #222, which claims `Straße` matches `STRASSE`. DuckDB says it does
    not, by all three of its mechanisms. That was a latent oracle bug the whole
    time.
    """
    con = duckdb.connect()
    # (a, b, does SQL consider them case-equal?)
    cases = [
        ("Straße", "STRASSE", False),
        ("ﬁle", "FILE", False),
        ("Montréal", "MONTRÉAL", True),
        ("Ω", "ω", True),
        ("İstanbul", "istanbul", True),
    ]
    for a, b, expected in cases:
        ilike = _one(con, "SELECT ? ILIKE ?", [a, b])
        collate = _one(con, "SELECT ? COLLATE NOCASE = ?", [a, b])
        lowered = _one(con, "SELECT lower(?) = lower(?)", [a, b])
        assert ilike == collate == lowered == expected, (a, b)

    # The oracles' model agrees on four of the five...
    assert [a.lower() == b.lower() for a, b, _ in cases] == [False, False, True, True, False]
    # ...and differs on `İ`, where DuckDB folds via ICU and Python emits a
    # combining dot. Unreachable from `sdmx_ids`, so recorded rather than
    # modelled — smoothing it into the oracle would hide it.
    assert "İ".lower() != "i"
    # `casefold`, the pre-#222 model, is wrong on three of the five.
    assert [a.casefold() == b.casefold() for a, b, _ in cases] == [True, True, True, True, False]


def test_the_accent_rungs_fold_case_as_sql_does_too() -> None:
    """The rungs above `.caseless` kept `str.casefold` in both oracles after #222.

    The deep profile found it, not the default one: a German name `Sß`, a
    fragment `S`, and `ends_with`. `Sß` casefolds to `sss`, so the oracles said
    it ends with `S`; `strip_accents(x) ILIKE '%S'` says it does not. Pinned
    here because it took 20x the examples to reach.
    """
    from tests.support import hierarchy_oracle, oracle

    with LocalRegistry(":memory:") as reg:
        reg.add(
            Codelist(
                id="CL3", maintainer="AG", version="1.0", name={"en": "c"}, codes=[Code(id="0", name={"de": "Sß"})]
            )
        )
        codes = present(reg.get(Codelist.urn_for(agency="AG", id="CL3", version="1.0"))).codes
        assert codes.filter(q.code.name["de"].str.unaccented.ends_with("S")).ids() == []
        assert codes.filter(q.code.name["de"].str.folded.ends_with("S")).ids() == []
    for punctuation in (False, True):
        assert not oracle._folded_matches("Sß", "ends_with", "S", punctuation=punctuation)
        assert not hierarchy_oracle._folded_matches("Sß", "ends_with", "S", punctuation=punctuation)
    assert "Sß".casefold().endswith("s"), "casefold, the old model, would have matched"
