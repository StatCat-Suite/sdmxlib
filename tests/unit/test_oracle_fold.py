"""The oracles' accent fold, checked against the function it models (#353).

`oracle._label_norm` exists to restate DuckDB's `strip_accents` in Python so a
property can compare the compiler's SQL against an independent implementation.
It was restating it *wrongly*: `unicodedata.combining()` returns the canonical
combining **class**, which is `0` for every spacing mark and many non-spacing
ones, so Python kept 527 characters DuckDB drops.

That produced a property failure only at 20x width, because Hypothesis has to
draw one of those characters — and it drew `U+0ABE GUJARATI VOWEL SIGN AA`.
A defect that needs a rare character to surface is one a fixed sweep finds
immediately, which is what this file is.

**Both oracles carried the same line.** That is the failure mode CLAUDE.md
names as the one differential testing cannot catch: rule 1 keeps them from
*sharing code*, but they independently restated the same wrong idea, so they
agreed with each other and disagreed with the database. Checking each against
`strip_accents` rather than against each other is the mitigation.
"""

from __future__ import annotations

import duckdb
import pytest

from tests.support import hierarchy_oracle, oracle

pytestmark = pytest.mark.unit

#: Every printable code point, all of Unicode (#494). It stopped at U+2FFF:
#: wide enough for the marks #353 found, and so never reached the 11,172 Hangul
#: syllables at U+AC00, which the oracles decomposed and DuckDB keeps whole. A
#: sweep is only total if it is total; this one costs about a second.
_SWEEP = [chr(i) for i in range(0x20, 0x110000) if chr(i).isprintable()]


@pytest.fixture(scope="module")
def duckdb_folded() -> dict[str, str]:
    """`strip_accents` over the sweep, in one round-trip."""
    with duckdb.connect() as conn:
        rows = conn.execute("SELECT x, strip_accents(x) FROM (SELECT unnest(?) AS x)", [_SWEEP]).fetchall()
    return dict(rows)


@pytest.mark.parametrize(
    "fold",
    [oracle._label_norm, hierarchy_oracle._label_norm],
    ids=["oracle", "hierarchy_oracle"],
)
def test_the_fold_agrees_with_strip_accents(fold, duckdb_folded: dict[str, str]) -> None:
    """Character by character, against the function itself.

    Parametrised over *both* oracles rather than testing one and trusting the
    other to match: they restate this independently, and independent restatement
    is precisely how they both came to be wrong.
    """
    disagree = {
        ch: (fold(ch, punctuation=False), got)
        for ch, got in duckdb_folded.items()
        if fold(ch, punctuation=False) != got
    }
    assert not disagree, f"{len(disagree)} characters fold differently from strip_accents, e.g. " + ", ".join(
        f"U+{ord(c):04X} oracle={a!r} duckdb={b!r}" for c, (a, b) in list(disagree.items())[:5]
    )


def test_the_sweep_contains_the_marks_that_broke_it(duckdb_folded: dict[str, str]) -> None:
    """The fixture's discriminating power. A sweep with no character that
    `combining()` calls 0 and `strip_accents` drops would pass against the
    original defect and prove nothing."""
    import unicodedata

    tricky = [ch for ch in _SWEEP if not unicodedata.combining(ch) and duckdb_folded[ch] != ch]
    assert len(tricky) > 100, f"only {len(tricky)} discriminating characters in the sweep"
    assert "ા" in tricky, "the character Hypothesis found is not in the sweep"


def test_the_two_oracles_still_agree_with_each_other(duckdb_folded: dict[str, str]) -> None:
    """Weaker than the checks above and kept anyway: if they ever diverge, one
    of them has been edited without the other, which is worth knowing even when
    both still match the database."""
    for ch in _SWEEP:
        assert oracle._label_norm(ch, punctuation=False) == hierarchy_oracle._label_norm(ch, punctuation=False), ch
