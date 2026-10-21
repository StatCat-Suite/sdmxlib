"""`~col.is_in(subquery)` — the anti-join, and the NULL that emptied it (#350).

`NOT IN` over a set containing one NULL is UNKNOWN for *every* row, so the
predicate selected nothing. Not wrong by a few rows: empty. And silently — the
positive form was right on the same data, so nothing looked broken.

It matters more than the shape suggests. `is_in(subquery)` is how you ask the
questions the edge graph exposes in only one direction — "codelists nothing
uses", "concepts no component cites" — and those run against nullable foreign
keys by nature, so the trap is not an edge case here, it is the common case.

`Query.difference()` was right all along because `_row_in` already emitted
`NOT EXISTS`. Every test below therefore checks the two against each other:
they are each other's oracle, and a fix to one that misses the other is a fix
that has not converged.
"""

from __future__ import annotations

import pytest

import sdmxlib as sl
from sdmxlib.model.ref import Ref
from sdmxlib.query.accessors import q
from tests.support.factories import concept_ref

pytestmark = pytest.mark.unit


@pytest.fixture
def registry():
    """Two codelists used by a dimension, one used by nothing.

    `TIME_PERIOD` is the load-bearing part: a dimension with no codelist, so
    `dsd_component.codelist_urn` is NULL for that row. Without it the subquery
    has no NULL and every assertion here passes against the bug.
    """
    freq = sl.Codelist(
        id="CL_FREQ", maintainer="AG", version="1.0", name={"en": "f"}, codes=[sl.Code(id="A", name={"en": "a"})]
    )
    geo = sl.Codelist(
        id="CL_GEO", maintainer="AG", version="1.0", name={"en": "g"}, codes=[sl.Code(id="ON", name={"en": "o"})]
    )
    unused = sl.Codelist(
        id="CL_UNUSED", maintainer="AG", version="1.0", name={"en": "u"}, codes=[sl.Code(id="X", name={"en": "x"})]
    )
    dsd = sl.DataStructure(
        id="D",
        maintainer="AG",
        version="1.0",
        name={"en": "d"},
        dimensions=[
            sl.Dimension(id="FREQ", concept=concept_ref("FREQ"), representation=Ref.to(freq)),
            sl.Dimension(id="GEO", concept=concept_ref("GEO"), representation=Ref.to(geo)),
            sl.Dimension(id="TIME_PERIOD", concept=concept_ref("TIME_PERIOD")),
        ],
        measures=[sl.Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )
    with sl.LocalRegistry(":memory:") as reg:
        reg.add_all([freq, geo, unused, dsd])
        yield reg


def _used(reg):
    return reg.query(q.component).select(urn=q.component.codelist.urn)


def test_the_subquery_really_does_contain_a_null(registry) -> None:
    """The fixture's discriminating power, asserted rather than assumed —
    without a NULL every other test here would pass against the defect."""
    urns = registry.query(q.component).select(urn=q.component.codelist.urn).collect()["urn"].to_list()
    assert None in urns, "no NULL in the subquery, so these tests prove nothing"


def test_the_anti_join_finds_the_unused_row(registry) -> None:
    """The bug: this returned `[]`."""
    assert registry.query(q.codelist).filter(~q.codelist.urn.is_in(_used(registry))).ids() == ["CL_UNUSED"]


def test_the_positive_form_is_unchanged(registry) -> None:
    """`IN` was always right, and must stay so — the fix touches only negation."""
    assert registry.query(q.codelist).filter(q.codelist.urn.is_in(_used(registry))).ids() == ["CL_FREQ", "CL_GEO"]


def test_the_anti_join_agrees_with_difference(registry) -> None:
    """The two spellings of the same question, which disagreed.

    `difference` reached `_row_in`'s `NOT EXISTS` and was correct; `~is_in`
    reached `NOT (… IN …)` and was not.
    """
    anti = registry.query(q.codelist).filter(~q.codelist.urn.is_in(_used(registry))).ids()
    positive = registry.query(q.codelist).filter(q.codelist.urn.is_in(_used(registry)))
    assert anti == registry.query(q.codelist).difference(positive).ids()


def test_the_two_forms_partition_the_rows(registry) -> None:
    """`p` and `~p` must not overlap, and together must not exceed the whole.

    The property that actually failed: the bug made `~p` empty, so the two did
    not partition. Stated as a law rather than as two expected lists, because
    it is the law a caller relies on when they write the negation.
    """
    everything = set(registry.query(q.codelist).ids())
    inside = set(registry.query(q.codelist).filter(q.codelist.urn.is_in(_used(registry))).ids())
    outside = set(registry.query(q.codelist).filter(~q.codelist.urn.is_in(_used(registry))).ids())

    assert not (inside & outside), "a row cannot be both in and not in the set"
    assert inside | outside == everything
    assert inside, "a fixture with nothing inside the set proves nothing"
    assert outside, "a fixture with nothing outside the set proves nothing"


def test_a_null_on_the_left_satisfies_neither_side(registry) -> None:
    """The cookbook's documented rule, applied to a relation rather than a
    literal: *a row whose column is NULL satisfies neither `p` nor `~p`*.

    Deliberate, not incidental. `NOT EXISTS` alone would let a NULL-valued row
    through the negation while `IN` excludes it from the positive — the two
    would stop partitioning in the other direction. Checked on
    `component.codelist.urn`, which is NULL for `TIME_PERIOD`.
    """
    codelists = registry.query(q.codelist).select(urn=q.codelist.urn)
    inside = registry.query(q.component).filter(q.component.codelist.urn.is_in(codelists)).ids()
    outside = registry.query(q.component).filter(~q.component.codelist.urn.is_in(codelists)).ids()

    assert "TIME_PERIOD" not in inside
    assert "TIME_PERIOD" not in outside, "a NULL row must satisfy neither side"


def test_an_empty_subquery_excludes_nothing(registry) -> None:
    """The degenerate case, which `NOT IN` also gets right and a hand-rolled
    `NOT EXISTS` could get wrong by correlating on the empty set."""
    nothing = registry.query(q.codelist).filter(q.codelist.id == "NO_SUCH").select(urn=q.codelist.urn)
    assert registry.query(q.codelist).filter(~q.codelist.urn.is_in(nothing)).ids() == [
        "CL_FREQ",
        "CL_GEO",
        "CL_UNUSED",
    ]


def test_the_anti_join_composes_under_boolean_operators(registry) -> None:
    """A negation nested under `&` must still reach the null-safe form — the
    compiler matches on `Not(InRelation())`, and a boolean wrapper is where a
    pattern-match fix most easily fails to apply."""
    used = _used(registry)
    both = registry.query(q.codelist).filter(~q.codelist.urn.is_in(used) & (q.codelist.agency == "AG")).ids()
    assert both == ["CL_UNUSED"]
