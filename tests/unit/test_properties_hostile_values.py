"""Hostile filter values across every string position in both DSLs (#250).

Document fuzzing covers the readers (`test_fuzz_readers.py`). The analogue for
a query compiler is not a mutated document — it is a **hostile predicate
value**, and nothing exercised that.

We had one confirmed instance: #222, an unescaped ``_`` reaching SQL ``LIKE``
and behaving as a single-character wildcard, so ``code.id.contains("REF_AREA")``
silently found nothing. It was fixed with one targeted test covering one
metacharacter in one predicate. The two DSLs have roughly a dozen string
positions between them; this covers all of them, systematically.

## Two independent assertions

Each position is checked twice, because the two failure modes are different
and neither implies the other:

**Semantic** — the hostile value must select the rows literal Python matching
selects, compared against `tests/support/oracle`. This catches wildcard
leakage without anyone enumerating which characters DuckDB's ``LIKE`` treats
as special. Had it existed, it would have caught #222 on the first run.

**Structural** — the emitted SQL must be *byte-identical* to the same query
built with a benign value, with the hostile value appearing only in the binds.
This catches the injection shape directly: if a value can change the shape of
the statement, it does not matter whether the rows happen to come out right.

## What the data side has to look like

SDMX ids are validated to ``A-Za-z0-9_@$-``, so an id can only ever hold one
LIKE metacharacter: ``_``. That is exactly why #222 was a live defect rather
than a theoretical one, and it means an id-only fixture cannot exercise the
rest of the class. Labels are unconstrained on both sides, so the hostile
*data* lives there — see `hostile_labelled_codelists`.

## Standing findings

Running this suite when written found **no new defects**: escaping and binding
were already correct at every position, including the folded variants. That is
the outcome worth having from a fuzz campaign, and the properties stay as the
guard — the class has now bitten twice (#222 in `code_query`, and again in
`artefact.has_metadata` during #281, caught only because the author had just
read #222).
"""

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

import sdmxlib as sl
from sdmxlib.local.registry import LocalRegistry
from sdmxlib.model.codelist import Codelist
from sdmxlib.query.accessors import q
from tests.support import oracle
from tests.support.narrow import present
from tests.support.strategies import hostile_labelled_codelists, hostile_strings

pytestmark = pytest.mark.unit

#: Each example persists a codelist into a fresh in-memory DuckDB, so these run
#: slower than a pure-model property. Same scaling as the other query
#: properties, so `HYPOTHESIS_PROFILE=deep` still explores hard.
_EXAMPLES = max(5, present(settings.default).max_examples // 5)

_STRING_OPS = ["contains", "starts_with", "ends_with"]


def _lazy(reg: LocalRegistry, cl: Codelist):
    return reg.get(Codelist.urn_for(agency=cl.agency_id, id=cl.id, version=cl.version))


# ── Semantic: hostile values select the rows Python would ────────────────────


@settings(max_examples=_EXAMPLES, deadline=None)
@given(cl=hostile_labelled_codelists(), needle=hostile_strings(), data=st.data())
def test_id_positions_agree_with_the_oracle(cl: Codelist, needle: str, data: st.DataObject):
    """``==``, ``!=`` and the three string ops on ``id``, under hostile needles.

    The id *data* can only hold ``_`` from the hostile set — the validator
    rejects the rest — but the *needle* is arbitrary user input, so a `%` that
    reached SQL unescaped would match every row instead of none.
    """
    assume("\x00" not in needle)  # rejected outright; see the named regression below
    op = data.draw(st.sampled_from(_STRING_OPS), label="op")
    with LocalRegistry(":memory:") as reg:
        reg.add(cl)
        lazy = _lazy(reg, cl)
        codes = list(cl.codes)

        assert present(lazy).codes.filter(q.code.id == needle).ids() == oracle.evaluate(oracle.id_eq(needle), codes)
        assert present(lazy).codes.filter(q.code.id != needle).ids() == oracle.evaluate(
            oracle.id_eq(needle, negate=True), codes
        )
        built = {
            "contains": q.code.id.str.contains,
            "starts_with": q.code.id.str.starts_with,
            "ends_with": q.code.id.str.ends_with,
        }
        assert present(lazy).codes.filter(built[op](needle)).ids() == oracle.evaluate(
            oracle.id_match(op, needle), codes
        )


@settings(max_examples=_EXAMPLES, deadline=None)
@given(cl=hostile_labelled_codelists(), needle=hostile_strings(), data=st.data())
def test_label_positions_agree_with_the_oracle(cl: Codelist, needle: str, data: st.DataObject):
    """Labels hold the hostile shapes on *both* sides — needle and data."""
    assume("\x00" not in needle)  # rejected outright; see the named regression below
    op = data.draw(st.sampled_from(_STRING_OPS), label="op")
    with LocalRegistry(":memory:") as reg:
        reg.add(cl)
        lazy = _lazy(reg, cl)
        codes = list(cl.codes)

        assert present(lazy).codes.filter(q.code.name["en"] == needle).ids() == oracle.evaluate(
            oracle.name_eq("en", needle), codes
        )
        built = {
            "contains": q.code.name["en"].str.contains,
            "starts_with": q.code.name["en"].str.starts_with,
            "ends_with": q.code.name["en"].str.ends_with,
        }
        assert present(lazy).codes.filter(built[op](needle)).ids() == oracle.evaluate(
            oracle.name_match("en", op, needle), codes
        )


@settings(max_examples=_EXAMPLES, deadline=None)
@given(cl=hostile_labelled_codelists(), needle=hostile_strings(), data=st.data())
def test_folded_positions_agree_with_the_oracle(cl: Codelist, needle: str, data: st.DataObject):
    """The folded strengths, where the escaping interacts with normalisation.

    `.folded` strips punctuation, so a punctuation-only needle folds to the
    empty string and would match everything — #255 added a guard for exactly
    that, and this is where a regression in it would show.
    """
    assume("\x00" not in needle)  # rejected outright; see the named regression below
    op = data.draw(st.sampled_from(_STRING_OPS), label="op")
    with LocalRegistry(":memory:") as reg:
        reg.add(cl)
        lazy = _lazy(reg, cl)
        codes = list(cl.codes)

        for ref, punctuation in ((q.code.name["en"].str.unaccented, False), (q.code.name["en"].str.folded, True)):
            got = (
                present(lazy)
                .codes.filter(
                    {"contains": ref.contains, "starts_with": ref.starts_with, "ends_with": ref.ends_with}[op](needle)
                )
                .ids()
            )
            want = oracle.evaluate(oracle.name_folded_match("en", op, needle, punctuation=punctuation), codes)
            assert got == want, f"fold(punctuation={punctuation}).{op} needle={needle!r}"


@settings(max_examples=_EXAMPLES, deadline=None)
@given(cl=hostile_labelled_codelists(), needle=hostile_strings())
def test_is_in_agrees_with_the_oracle(cl: Codelist, needle: str):
    """Set membership is equality, so a hostile value must be inert here."""
    with LocalRegistry(":memory:") as reg:
        reg.add(cl)
        lazy = _lazy(reg, cl)
        codes = list(cl.codes)
        assert present(lazy).codes.filter(q.code.id.is_in([needle])).ids() == oracle.evaluate(
            oracle.in_set([needle]), codes
        )


# ── Structural: folded to `test_sql_ast_invariants` (#257) ───────────────────
#
# This file used to carry a byte-identity check — build the query with a benign
# value and again with a hostile one, require the SQL to match — over fifteen
# positions. #257 asked for that to be *folded in* rather than duplicated, and
# the AST form is strictly stronger: byte-identity proves the one value under
# test did not leak, while `assert_no_literals_in_predicates` asks whether any
# user value is in the text at all. A leak that happened to preserve the byte
# count would satisfy the old check and fail the new one.
#
# The `.folded` empty-needle exception the old check had to special-case is
# gone with it: the AST form does not care that #255 emits a different clause
# for an empty needle, only that no value was spliced.
#
# What stays here is the semantic half, which the AST says nothing about: a
# hostile value must still match the right rows.


# ── Degenerate shapes ────────────────────────────────────────────────────────


@settings(max_examples=_EXAMPLES, deadline=None)
@given(needle=hostile_strings())
def test_an_empty_codelist_matches_nothing_whatever_the_needle(needle: str):
    """No hostile value can conjure a row out of an empty relation."""
    assume("\x00" not in needle)  # rejected outright; see the named regression below
    cl = Codelist(id="CL_EMPTY", maintainer="AG", version="1.0", name={"en": "e"})
    with LocalRegistry(":memory:") as reg:
        reg.add(cl)
        lazy = _lazy(reg, cl)
        assert present(lazy).codes.filter(q.code.id.str.contains(needle)).ids() == []
        assert present(lazy).codes.filter(q.code.id == needle).ids() == []
        assert present(lazy).codes.count() == 0


@settings(max_examples=_EXAMPLES, deadline=None)
@given(cl=hostile_labelled_codelists(), needle=hostile_strings())
def test_limit_zero_matches_nothing_and_limit_all_matches_the_filter(cl: Codelist, needle: str):
    """`limit(0)` is a cap, not an absence of one — an easy off-by-one."""
    assume("\x00" not in needle)  # rejected outright; see the named regression below
    with LocalRegistry(":memory:") as reg:
        reg.add(cl)
        lazy = _lazy(reg, cl)
        unlimited = present(lazy).codes.filter(q.code.id.str.contains(needle)).ids()
        assert present(lazy).codes.filter(q.code.id.str.contains(needle)).limit(0).ids() == []
        assert present(lazy).codes.filter(q.code.id.str.contains(needle)).limit(len(cl.codes) + 1).ids() == unlimited


@settings(max_examples=_EXAMPLES, deadline=None)
@given(cl=hostile_labelled_codelists())
def test_match_everything_and_match_nothing_are_complements(cl: Codelist):
    """A filter and its negation must partition the codelist exactly."""
    with LocalRegistry(":memory:") as reg:
        reg.add(cl)
        lazy = _lazy(reg, cl)
        target = next(iter(cl.codes)).id
        matched = set(present(lazy).codes.filter(q.code.id == target).ids())
        rest = set(present(lazy).codes.filter(q.code.id != target).ids())
        assert matched | rest == {c.id for c in cl.codes}
        assert matched & rest == set()


# ── Regression: NUL in a filter value (#250) ─────────────────────────────────


@pytest.mark.parametrize("needle", ["\x00", "A\x00", "\x00B", "A\x00B"])
@pytest.mark.parametrize("strength", ["plain", "unaccented", "folded"])
def test_nul_in_a_filter_value_is_a_clear_error(needle: str, strength: str) -> None:
    """A NUL is refused here rather than misreported by DuckDB.

    Found by the folded property above at 20x width, on `ends_with("\\x00")`.
    DuckDB rejects a NUL-bearing LIKE pattern when the *left* operand is a
    non-trivial expression — which every folded match is, the column going
    through `strip_accents` — and reports:

        Syntax Error: Like pattern must not end with escape character!

    which names the wrong cause. The same value against a plain `ILIKE`
    succeeded, so the behaviour was inconsistent as well as misleading.

    #250 allows a driver-level rejection *if it is a clear error rather than
    silent truncation*. This was neither, so `match_sql` rejects it at the one
    place every string match is built — and for every strength, so plain and
    folded do not disagree about the same value.
    """
    cl = Codelist(
        id="CL_NUL",
        maintainer="AG",
        version="1.0",
        name={"en": "n"},
        codes=[sl.Code(id="C_x", name={"en": "L_x"})],
    )
    refs = {
        "plain": q.code.name["en"].str,
        "unaccented": q.code.name["en"].str.unaccented,
        "folded": q.code.name["en"].str.folded,
    }
    with LocalRegistry(":memory:") as reg:
        reg.add(cl)
        lazy = _lazy(reg, cl)
        for op in _STRING_OPS:
            with pytest.raises(ValueError, match=r"NUL character"):
                present(lazy).codes.filter(getattr(refs[strength], op)(needle)).ids()
