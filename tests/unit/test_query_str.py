"""The `.str` folding ladder, and localised labels.

A **must-keep**: `caseless` / `unaccented` / `folded` exist on the current DSLs
and were paid for by #222 and #255. The SQL is not reimplemented here — it
comes from `model._label_match`, so both compilers fold identically and the
differential oracles cover this too.

Two rules are checked directly because losing either would be silent:

* `caseless` carries `==`; `unaccented` and `folded` carry only the string ops.
  An accent-insensitive *equality* is a question nobody has asked, whereas a
  case-insensitive id lookup is the complaint #222 opens with.
* `unaccented` and `folded` are **label-only**. An id is not prose, and folding
  punctuation out of `CL_FREQ` is not a question anyone means to ask.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import sdmxlib as sl
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.organisation import Agency
from sdmxlib.query._expr import Pred
from sdmxlib.query.accessors import CodeRow, q

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit


@pytest.fixture
def reg() -> Iterator[sl.LocalRegistry]:
    """Labels chosen so each rung of the ladder changes the answer."""
    with sl.LocalRegistry(":memory:") as registry:
        registry.add(
            Codelist(
                id="CL_AREA",
                maintainer=Agency(id="ESTAT"),
                name={"en": "Areas"},
                description={"en": "Every area"},
                codes=[
                    Code(id="MTL", name={"en": "Montréal", "fr": "Montréal"}),
                    Code(id="TR", name={"en": "Trois-Rivières"}),
                    Code(id="SJ", name={"en": "St. John's"}),
                    Code(id="PLAIN", name={"en": "Ottawa"}),
                ],
            )
        )
        # A second maintainable with a *different* label, so a correlated
        # lookup that dropped its correlation would return the wrong one rather
        # than still passing. Verified by mutation: without this the whole file
        # was green with `WHERE lt.owner_urn IS NOT NULL`.
        registry.add(
            Codelist(
                id="CL_FREQ",
                maintainer=Agency(id="OECD"),
                name={"en": "Frequencies"},
                codes=[Code(id="A", name={"en": "Annual"})],
            )
        )
        yield registry


def _codes(reg: sl.LocalRegistry, predicate: Pred[CodeRow]) -> list[str]:
    return reg.query(q.code).filter(predicate).ids()


# ── the floor is exact ────────────────────────────────────────────────────


def test_plain_matching_is_case_sensitive(reg: sl.LocalRegistry) -> None:
    """`NONE` folded case until #222 — the floor of the ladder was not a floor
    and its name was a lie."""
    assert _codes(reg, q.code.id.str.contains("MTL")) == ["MTL"]
    assert _codes(reg, q.code.id.str.contains("mtl")) == []


def test_plain_equality_is_case_sensitive(reg: sl.LocalRegistry) -> None:
    assert _codes(reg, q.code.id == "MTL") == ["MTL"]
    assert _codes(reg, q.code.id == "mtl") == []


def test_like_metacharacters_are_escaped(reg: sl.LocalRegistry) -> None:
    """#222: an unescaped `_` or `%` in a needle silently matches anything."""
    assert _codes(reg, q.code.id.str.contains("M_L")) == []


# ── caseless: every string ref, and the only rung with equality ───────────


def test_caseless_matches_ignoring_case(reg: sl.LocalRegistry) -> None:
    assert _codes(reg, q.code.id.str.caseless.contains("mtl")) == ["MTL"]


def test_caseless_carries_equality(reg: sl.LocalRegistry) -> None:
    """The case #222 opens with: a caller who typed an id in the wrong case."""
    assert _codes(reg, q.code.id.str.caseless == "mtl") == ["MTL"]
    assert set(_codes(reg, q.code.id.str.caseless != "mtl")) == {"TR", "SJ", "PLAIN", "A"}


def test_caseless_applies_to_ids_not_only_labels(reg: sl.LocalRegistry) -> None:
    """SDMX `IDType` treats `F` and `f` as distinct codes that can coexist, so
    an id lookup is exactly where the fold is wanted."""
    assert hasattr(q.code.id.str, "caseless")
    assert _codes(reg, q.code.id.str.caseless.starts_with("mt")) == ["MTL"]


# ── the prose rungs are label-only ───────────────────────────────────────


def test_unaccented_and_folded_are_absent_on_an_id() -> None:
    """An id is not prose (#255)."""
    assert not hasattr(q.code.id.str, "unaccented")
    assert not hasattr(q.code.id.str, "folded")


def test_unaccented_and_folded_are_present_on_a_label() -> None:
    assert hasattr(q.code.name["en"].str, "unaccented")
    assert hasattr(q.code.name["en"].str, "folded")


def test_unaccented_ignores_diacritics(reg: sl.LocalRegistry) -> None:
    assert _codes(reg, q.code.name["en"].str.contains("Montreal")) == []
    assert _codes(reg, q.code.name["en"].str.unaccented.contains("Montreal")) == ["MTL"]


def test_folded_also_ignores_punctuation(reg: sl.LocalRegistry) -> None:
    """ "Trois Rivieres" finds "Trois-Rivières"; `unaccented` alone does not."""
    assert _codes(reg, q.code.name["en"].str.unaccented.contains("Trois Rivieres")) == []
    assert _codes(reg, q.code.name["en"].str.folded.contains("Trois Rivieres")) == ["TR"]


def test_folded_finds_st_john(reg: sl.LocalRegistry) -> None:
    assert _codes(reg, q.code.name["en"].str.folded.contains("St John")) == ["SJ"]


def test_the_rungs_are_ordered(reg: sl.LocalRegistry) -> None:
    """Each rung is a superset of the one above it."""
    label = q.code.name["en"].str
    exact = set(_codes(reg, label.contains("montreal")))
    caseless = set(_codes(reg, label.caseless.contains("montreal")))
    unaccented = set(_codes(reg, label.unaccented.contains("montreal")))
    folded = set(_codes(reg, label.folded.contains("montreal")))
    assert exact <= caseless <= unaccented <= folded


def test_folded_refuses_a_punctuation_only_needle(reg: sl.LocalRegistry) -> None:
    """A needle of `"..."` folds to the empty string and would match every row
    — the guard `_label_match` carries for exactly this."""
    assert _codes(reg, q.code.name["en"].str.folded.contains("...")) == []


# ── localised labels ──────────────────────────────────────────────────────


def test_a_label_needs_a_language(reg: sl.LocalRegistry) -> None:
    """No API here picks one, so `name` alone is not a column."""
    assert not hasattr(q.code.name, "str")
    assert hasattr(q.code.name["en"], "str")


def test_the_language_selects_the_text(reg: sl.LocalRegistry) -> None:
    assert _codes(reg, q.code.name["fr"].str.contains("Montréal")) == ["MTL"]
    assert _codes(reg, q.code.name["fr"].str.contains("Ottawa")) == []


def test_a_label_projects(reg: sl.LocalRegistry) -> None:
    frame = reg.query(q.code).filter(q.code.id == "MTL").select(code=q.code.id, label=q.code.name["en"]).collect()
    assert frame["label"].to_list() == ["Montréal"]


def test_a_label_projects_through_a_fan_out(reg: sl.LocalRegistry) -> None:
    frame = reg.query(q.codelist).select(cl=q.codelist.id, labels=q.codelist.codes.name["en"].list()).collect()
    assert "Montréal" in frame["labels"].to_list()[0]


# ── two storage shapes, one spelling ──────────────────────────────────────


def test_a_maintainable_label_reads_the_side_table(reg: sl.LocalRegistry) -> None:
    """`code.name` is an inline `MAP`; a maintainable's labels are rows in
    `sdmx.localized_text`. A caller writes the same thing for both."""
    assert reg.query(q.codelist).filter(q.codelist.name["en"] == "Areas").ids() == ["CL_AREA"]


def test_a_missing_language_is_null_not_a_dropped_row(reg: sl.LocalRegistry) -> None:
    """A correlated scalar subquery preserves the row, exactly as a missing map
    key would — the two shapes have to be indistinguishable or the spelling is
    a lie."""
    frame = reg.query(q.codelist).select(cl=q.codelist.id, label=q.codelist.name["de"]).collect()
    assert frame.height == 2
    assert frame["label"].to_list() == [None, None]


def test_each_owner_gets_its_own_label(reg: sl.LocalRegistry) -> None:
    """The correlation itself, which nothing else pins down.

    Without `lt.owner_urn = <outer>.urn` the subquery returns some other
    artefact's label — or errors on multiple rows — and every other test here
    still passes, because they only ever look at one owner.
    """
    frame = reg.query(q.codelist).select(cl=q.codelist.id, label=q.codelist.name["en"]).collect()
    labels = dict(zip(frame["cl"].to_list(), frame["label"].to_list(), strict=True))
    assert labels == {"CL_AREA": "Areas", "CL_FREQ": "Frequencies"}


def test_a_maintainable_label_folds_like_any_other(reg: sl.LocalRegistry) -> None:
    assert reg.query(q.codelist).filter(q.codelist.name["en"].str.caseless == "areas").ids() == ["CL_AREA"]
    assert reg.query(q.codelist).filter(q.codelist.name["en"].str.folded.contains("area")).ids() == ["CL_AREA"]


def test_description_is_a_separate_field(reg: sl.LocalRegistry) -> None:
    """Both live in one table, told apart by `localized_text.field`."""
    assert reg.query(q.codelist).filter(q.codelist.description["en"] == "Areas").ids() == []


def test_a_maintainable_label_projects(reg: sl.LocalRegistry) -> None:
    frame = (
        reg.query(q.codelist)
        .filter(q.codelist.id == "CL_AREA")
        .select(cl=q.codelist.id, label=q.codelist.name["en"])
        .collect()
    )
    assert frame["label"].to_list() == ["Areas"]
