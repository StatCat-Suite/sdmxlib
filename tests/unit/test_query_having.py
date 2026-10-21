"""`having()` — predicates over groups, which `filter()` cannot be.

`filter` asks about a *row*, and `group_by` has replaced rows with groups, so
a row predicate stops being meaningful there — which is why it refuses, and
should keep refusing. A predicate about the *group* is a different question,
and it had no spelling at all: "agencies with more than one dataflow" was
unexpressible, because the root is a dataflow and the group is an agency.

An aggregate over the root's own fan-out is not this. `filter(q.codelist.codes
.count() > 3)` already works at row grain and compiles to a correlated
subquery; the same `AggCompare` after `group_by` means the plain SQL aggregate
over the group's rows. Same object, two readings, decided by the clause — the
split `.over()` already makes for windows.
"""

from __future__ import annotations

import pytest

import sdmxlib as sl
from sdmxlib.query.accessors import q

pytestmark = pytest.mark.unit


@pytest.fixture
def registry():
    """Three flows under AG, one under BG — so a `> 1` predicate keeps one
    group and drops one. A fixture where every group survived would pass
    against a `having` that compiled to nothing."""
    flows = [
        sl.Dataflow(id=f"DF{i}", maintainer=agency, version="1.0", name={"en": f"f{i}"})
        for i, agency in enumerate(["AG", "AG", "AG", "BG"])
    ]
    with sl.LocalRegistry(":memory:") as reg:
        reg.add_all(flows)
        yield reg


def _grouped(reg):
    return reg.query(q.dataflow).group_by(q.dataflow.agency)


def test_the_fixture_has_a_group_to_drop(registry) -> None:
    """Discriminating power, asserted rather than assumed."""
    counts = {r["agency"]: r["n"] for r in _grouped(registry).agg(n=q.dataflow.id.count()).collect().to_dicts()}
    assert counts == {"AG": 3, "BG": 1}


def test_having_keeps_only_the_matching_groups(registry) -> None:
    """The query that had no spelling."""
    kept = _grouped(registry).agg(n=q.dataflow.id.count()).having(q.dataflow.id.count() > 1).collect().to_dicts()
    assert kept == [{"agency": "AG", "n": 3}]


def test_having_works_on_len(registry) -> None:
    """`len()` is the other way to reach a grouped frame, and builds its
    aggregate itself — so it needs its own case or the two could diverge."""
    kept = _grouped(registry).len().having(q.dataflow.id.count() > 1).collect().to_dicts()
    assert kept == [{"agency": "AG", "len": 3}]


def test_terms_accumulate_and_are_anded(registry) -> None:
    """As `Query.filter` does, so the two clauses compose the same way."""
    both = _grouped(registry).len().having(q.dataflow.id.count() > 1, q.dataflow.id.count() < 5)
    chained = _grouped(registry).len().having(q.dataflow.id.count() > 1).having(q.dataflow.id.count() < 5)
    assert both.collect().to_dicts() == chained.collect().to_dicts() == [{"agency": "AG", "len": 3}]


def test_having_composes_with_order_by(registry) -> None:
    """The binds land in three clauses — SELECT, HAVING, ORDER BY — and DuckDB
    matches `?` positionally, so a `having` whose parameters were appended in
    the wrong place binds every value to the wrong slot. It did, and the
    symptom was a cast error naming an unrelated column."""
    rows = (
        _grouped(registry)
        .agg(n=q.dataflow.id.count())
        .having(q.dataflow.id.count() >= 1)
        .order_by("n")
        .collect()
        .to_dicts()
    )
    assert rows == [{"agency": "BG", "n": 1}, {"agency": "AG", "n": 3}]


def test_a_predicate_that_matches_no_group_returns_nothing(registry) -> None:
    assert _grouped(registry).len().having(q.dataflow.id.count() > 99).collect().to_dicts() == []


def test_having_without_group_by_is_refused(registry) -> None:
    """An ungrouped projection has one row per root row, so there are no
    groups to ask about — and the aggregate the caller wants is already a row
    predicate `filter` accepts. Refused rather than silently meaning that,
    because two spellings of one thing is a way to get it wrong."""
    projection = registry.query(q.dataflow).select(q.dataflow.id)
    with pytest.raises(TypeError) as caught:
        projection.having(q.dataflow.id.count() > 1)

    message = str(caught.value)
    assert "group_by()" in message
    assert "filter(" in message, "the refusal names where a row-grain aggregate does belong"


def test_a_row_predicate_in_having_is_refused(registry) -> None:
    """`having(q.dataflow.id == "DF0")` is a row question wearing the wrong
    clause. Caught at the call with a message naming `filter`, rather than
    reaching DuckDB as "column must appear in the GROUP BY clause"."""
    with pytest.raises(TypeError, match="filter"):
        _grouped(registry).len().having(q.dataflow.id == "DF0")


def test_filter_still_refuses_and_now_points_at_having(registry) -> None:
    """The other half of the pair: `filter` must keep refusing, because a row
    predicate after `select` really is meaningless."""
    with pytest.raises(TypeError, match="filter"):
        _grouped(registry).len().filter(q.dataflow.id == "DF0")


def test_a_row_grain_aggregate_still_belongs_in_filter() -> None:
    """The case `having` deliberately does *not* take, shown working where it
    does belong — so the refusal above is a redirection, not a dead end."""
    cl = sl.Codelist(
        id="CL_BIG",
        maintainer="AG",
        version="1.0",
        name={"en": "b"},
        codes=[sl.Code(id=c, name={"en": c}) for c in "ABCD"],
    )
    small = sl.Codelist(
        id="CL_SMALL", maintainer="AG", version="1.0", name={"en": "s"}, codes=[sl.Code(id="A", name={"en": "a"})]
    )
    with sl.LocalRegistry(":memory:") as reg:
        reg.add_all([cl, small])
        assert reg.query(q.codelist).filter(q.codelist.codes.count() > 3).ids() == ["CL_BIG"]
