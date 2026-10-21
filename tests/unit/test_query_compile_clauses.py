"""`_facet_clause` and `_constraint_clause`, the two discriminator emitters (#324).

Both turn "this relation holds several kinds and I want one" into SQL —
`_facet_clause` for a pre-filtered fan-out (`dsd.dimensions` over
`dsd_component`), `_constraint_clause` for a root sharing `sdmx.artefact` with
twenty others. Same three branches each: absent, one value, several.

## Why direct unit tests, when the rest of the compiler is tested end to end

Mutation triage. `_facet_clause` held 12 survivors and `_constraint_clause` 12,
the two largest adjacent clusters in `_compile.py`, and reading them explains
why no end-to-end test could have killed them:

**Every declared facet and every declared root constraint carries exactly one
value.** All three entries in `_edges.FACETS` are single — `("Dimension",)`,
`("Attribute",)`, `("Measure",)` — and all twenty `__constraint__` class
attributes in `accessors.py` are `("artefact_type", ("Something",))`. So the
multi-value `IN (...)` branch cannot be reached through `q.*` at all, and nine
mutants in each function sat in code no query can execute.

That branch is not a mistake. Both docstrings state the capability — a facet
"may cover several stored kinds even when the domain exposes one name" — and a
facet spanning `Dimension` and `TimeDimension` is an obvious future one. It is
declared, unreachable today, and until now unpinned: whoever writes that facet
would have been the first to find out whether the branch works.

The remaining three survivors per function are reachable and produce SQL that
*runs*, which is why an end-to-end test passes over them. `len(values) == 1`
mutated to `!= 1` sends a single value down the `IN` branch and emits
`IN (?)` — different SQL, same rows. Only an assertion on the emitted text can
tell those apart, so that is what these are.
"""

from __future__ import annotations

import pytest

from sdmxlib.query._compile import _conjoin, _constraint_clause, _facet_clause
from sdmxlib.query._edges import EDGES, FACETS, Edge, Facet
from sdmxlib.query._joins import Hop

pytestmark = pytest.mark.unit

ALIAS = "s3"


def _hop(facet: Facet | None) -> Hop:
    """A hop over any real edge — only `hop.facet` is read."""
    edge = next(e for e in EDGES if e.inverse == "components")
    return Hop(name="dimensions", edge=edge, forward=False, facet=facet)


# ── the premise: why the multi-value branch needs a direct test ───────────


def test_no_declared_facet_or_constraint_carries_more_than_one_value() -> None:
    """The reason nine mutants per function are unreachable end to end.

    Asserted rather than asserted-in-prose, so that the day a multi-value facet
    is declared this fails and whoever added it learns the branch below is now
    live — and that these tests are no longer the only thing exercising it.
    """
    multi = [f for f in FACETS if len(f.values) > 1]
    assert not multi, f"{multi} now reaches the IN branch through a real query"


# ── _facet_clause ────────────────────────────────────────────────────────


def test_a_facet_with_one_value_emits_equality() -> None:
    """`= ?`, not `IN (?)`.

    Both run and both return the same rows, which is why this needs the text:
    `len(values) == 1` mutated to `!= 1` or `== 2` routes the single-value case
    through the `IN` branch and nothing downstream notices.
    """
    clause = _facet_clause(_hop(Facet("dimensions", "components", "component_type", ("Dimension",))), ALIAS)
    assert clause.text == f"{ALIAS}.component_type = ?"
    assert clause.params == ("Dimension",)


def test_a_facet_with_several_values_emits_in_with_one_mark_each() -> None:
    """The declared-but-unreached branch, pinned.

    One `?` per value, comma-separated, and the values as params in order —
    a mark count that disagrees with the param count is a bind mismatch at
    execution, which is how the equivalent defect in `_window` surfaced (!330).
    """
    facet = Facet("dimensions", "components", "component_type", ("Dimension", "TimeDimension"))
    clause = _facet_clause(_hop(facet), ALIAS)
    assert clause.text == f"{ALIAS}.component_type IN (?, ?)"
    assert clause.params == ("Dimension", "TimeDimension")


def test_a_facet_of_three_keeps_the_marks_and_params_in_step() -> None:
    facet = Facet("x", "components", "component_type", ("A", "B", "C"))
    clause = _facet_clause(_hop(facet), ALIAS)
    assert clause.text.count("?") == len(clause.params) == 3
    assert clause.text == f"{ALIAS}.component_type IN (?, ?, ?)"


def test_no_facet_emits_the_empty_string_not_none() -> None:
    """`_conjoin` filters on `if p.text`, so `None` would behave the same.

    Pinned as `""` anyway: the type says `str`, and a `None` reaching any
    caller that formats rather than filters would print "None" into a WHERE
    clause.
    """
    clause = _facet_clause(_hop(None), ALIAS)
    assert clause.text == ""
    assert clause.params == ()
    assert _conjoin([clause]).text == "TRUE", "and it is dropped, not rendered"


# ── _constraint_clause ───────────────────────────────────────────────────


def test_a_root_constraint_with_one_value_emits_equality() -> None:
    clause = _constraint_clause(("artefact_type", ("Codelist",)), ALIAS)
    assert clause.text == f"{ALIAS}.artefact_type = ?"
    assert clause.params == ("Codelist",)


def test_a_root_constraint_with_several_values_emits_in() -> None:
    """The other unreached branch. Every `__constraint__` in `accessors.py` is
    single-valued, so this is the only thing exercising it."""
    clause = _constraint_clause(("artefact_type", ("Codelist", "ConceptScheme")), ALIAS)
    assert clause.text == f"{ALIAS}.artefact_type IN (?, ?)"
    assert clause.params == ("Codelist", "ConceptScheme")


def test_no_root_constraint_emits_the_empty_string_not_none() -> None:
    clause = _constraint_clause(None, ALIAS)
    assert clause.text == ""
    assert clause.params == ()


def test_the_constraint_column_is_taken_from_the_pair_not_assumed() -> None:
    """`artefact_type` is the only column used today; the signature takes any.

    A mutant replacing `column` with a literal would pass every test that only
    ever passes `artefact_type`.
    """
    clause = _constraint_clause(("kind", ("X",)), ALIAS)
    assert clause.text == f"{ALIAS}.kind = ?"


def test_the_alias_is_the_one_passed_in() -> None:
    """Both functions qualify with the caller's alias, and the caller is the
    only thing that knows which scope this clause lands in."""
    assert _constraint_clause(("artefact_type", ("Codelist",)), "s7").text.startswith("s7.")
    facet = Facet("dimensions", "components", "component_type", ("Dimension",))
    assert _facet_clause(_hop(facet), "s9").text.startswith("s9.")


# ── and the end-to-end behaviour still holds ─────────────────────────────


def test_a_real_declared_facet_round_trips_through_the_clause() -> None:
    """Against `FACETS` itself rather than a fixture, so a change to the
    declared set is exercised here rather than only in a query."""
    for facet in FACETS:
        clause = _facet_clause(_hop(facet), ALIAS)
        assert clause.params == facet.values
        assert clause.text.count("?") == len(facet.values)


def test_the_hop_edge_is_a_real_one() -> None:
    """`_hop` picks a live edge by inverse name; if that name changes, these
    tests should fail loudly rather than construct a hop over nothing."""
    assert isinstance(_hop(None).edge, Edge)
