"""`group_by` — the third meaning of an aggregate, and why that is coherent.

`q.codelist.id.count()` is one expression with three readings, decided by the
clause that receives it:

* in `select()` — a correlated subquery over the fan-out its path crosses;
* after `.over(...)` — a window function over rows sharing a partition;
* in `agg()` — a plain SQL aggregate over the rows of its group.

That is not overloading by accident. The expression says *what* to compute and
the clause says *over what*, which is the split `.over()` already makes. The
tests below pin all three against the same fixture, because the only way that
design stays honest is if the three answers differ and are each checked.

`group_by` is **not a refinement**: it changes what a row is, so it returns a
different type rather than joining the chain the refinement laws govern — the
same reasoning that keeps `union` a free function.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import sdmxlib as sl
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.organisation import Agency
from sdmxlib.query._expr import RowKind
from sdmxlib.query._joins import PathError
from sdmxlib.query.accessors import q
from sdmxlib.query.query import Grouped, Select, SliceError

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit


@pytest.fixture
def reg() -> Iterator[sl.LocalRegistry]:
    """Two agencies with an uneven split, and codelists of different sizes.

    ESTAT has two codelists holding 2 and 3 codes; OECD has two holding 1 and
    0. So "codelists per agency" and "codes per agency" disagree — which is the
    only way to tell a `GROUP BY` over the right relation from one over the
    wrong one.
    """
    with sl.LocalRegistry(":memory:") as registry:
        for cid, agency, size in [("CL_A", "ESTAT", 2), ("CL_B", "OECD", 1), ("CL_C", "ESTAT", 3), ("CL_D", "OECD", 0)]:
            registry.add(
                Codelist(
                    id=cid,
                    maintainer=Agency(id=agency),
                    name={"en": cid},
                    codes=[Code(id=f"X{i}", name={"en": f"x{i}"}) for i in range(size)],
                )
            )
        yield registry


def _rows[R: RowKind, A](frame: Select[R, A]) -> list[dict[str, object]]:
    return frame.collect().to_dicts()


# ── the two ways out ──────────────────────────────────────────────────────


def test_len_counts_rows_per_group(reg: sl.LocalRegistry) -> None:
    """Named `len` because polars names it that, and a method rather than an
    aggregate because it counts *rows* — there is no column to name it after."""
    assert _rows(reg.query(q.codelist).group_by(q.codelist.agency).len()) == [
        {"agency": "ESTAT", "len": 2},
        {"agency": "OECD", "len": 2},
    ]


def test_agg_yields_the_keys_then_the_aggregates(reg: sl.LocalRegistry) -> None:
    frame = (
        reg.query(q.codelist)
        .group_by(q.codelist.agency)
        .agg(
            n=q.codelist.id.count(),
            ids=q.codelist.id.list(),
        )
    )
    rows = _rows(frame)
    assert [row["agency"] for row in rows] == ["ESTAT", "OECD"]
    assert rows[0]["n"] == 2
    ids = rows[0]["ids"]
    assert isinstance(ids, list)
    assert sorted(ids) == ["CL_A", "CL_C"]


def test_grouping_the_child_relation_answers_a_different_question(
    reg: sl.LocalRegistry,
) -> None:
    """Five ESTAT codes across two codelists, one OECD code across two. Rooting
    at the child rather than the parent is the whole difference, and a fixture
    where the two agreed would hide it."""
    assert _rows(reg.query(q.code).group_by(q.code.codelist.agency).len()) == [
        {"agency": "ESTAT", "len": 5},
        {"agency": "OECD", "len": 1},
    ]


def test_n_unique_within_a_group(reg: sl.LocalRegistry) -> None:
    """Every codelist's codes are `X0`, `X1`, … so ESTAT's five codes have only
    three distinct ids — `count(*)` and `count(DISTINCT)` disagree, which is
    what makes this test say anything."""
    frame = (
        reg.query(q.code)
        .group_by(q.code.codelist.agency)
        .agg(
            total=q.code.id.count(),
            distinct=q.code.id.n_unique(),
        )
    )
    assert _rows(frame) == [
        {"agency": "ESTAT", "total": 5, "distinct": 3},
        {"agency": "OECD", "total": 1, "distinct": 1},
    ]


def test_several_keys(reg: sl.LocalRegistry) -> None:
    rows = _rows(reg.query(q.codelist).group_by(q.codelist.agency, q.codelist.version).len())
    assert rows == [
        {"agency": "ESTAT", "version": "1.0", "len": 2},
        {"agency": "OECD", "version": "1.0", "len": 2},
    ]


def test_a_key_may_cross_a_to_one_hop(reg: sl.LocalRegistry) -> None:
    """The join for a group key has to be planned even when no predicate
    reaches that relation."""
    assert "sdmx.artefact" in reg.query(q.code).group_by(q.code.codelist.agency).len().to_sql().sql


def test_a_localised_label_is_a_group_key(reg: sl.LocalRegistry) -> None:
    """A label is a subquery, not a column, so grouping by one is where naming
    the keys twice in the `GROUP BY` would re-derive it — and its binds."""
    rows = _rows(reg.query(q.codelist).group_by(q.codelist.name["en"]).len())
    assert rows == [
        {"name_en": "CL_A", "len": 1},
        {"name_en": "CL_B", "len": 1},
        {"name_en": "CL_C", "len": 1},
        {"name_en": "CL_D", "len": 1},
    ]


# ── one expression, three readings ────────────────────────────────────────


def test_the_same_aggregate_means_three_different_things(reg: sl.LocalRegistry) -> None:
    """The design claim, checked as three different numbers.

    `q.codelist.codes.id.count()` in `select()` counts *that codelist's* codes.
    `q.codelist.id.count()` in `agg()` counts the rows of the group.
    `.over(...)` counts the rows sharing a partition, without collapsing them.
    """
    per_row = reg.query(q.codelist).select(cl=q.codelist.id, n=q.codelist.codes.count()).collect()
    assert dict(zip(per_row["cl"].to_list(), per_row["n"].to_list(), strict=True)) == {
        "CL_A": 2,
        "CL_B": 1,
        "CL_C": 3,
        "CL_D": 0,
    }

    per_group = _rows(reg.query(q.codelist).group_by(q.codelist.agency).agg(n=q.codelist.id.count()))
    assert per_group == [{"agency": "ESTAT", "n": 2}, {"agency": "OECD", "n": 2}]

    windowed = reg.query(q.codelist).select(cl=q.codelist.id, n=q.codelist.id.count().over(q.codelist.agency)).collect()
    assert windowed.height == 4, "a window does not collapse rows"
    assert set(windowed["n"].to_list()) == {2}


# ── it is a stage, not a refinement ───────────────────────────────────────


def test_group_by_returns_a_different_type(reg: sl.LocalRegistry) -> None:
    grouped = reg.query(q.codelist).group_by(q.codelist.agency)
    assert isinstance(grouped, Grouped)
    assert "lazy" in repr(grouped)
    for absent in ("filter", "limit", "offset", "collect", "ids"):
        assert not hasattr(grouped, absent), f"Grouped should not carry {absent}"


def test_a_grouped_query_is_lazy(reg: sl.LocalRegistry) -> None:
    frame = reg.query(q.codelist).group_by(q.codelist.agency).len()
    assert "GROUP BY" in frame.to_sql().sql
    assert "binds" in frame.explain()


def test_the_filter_still_applies(reg: sl.LocalRegistry) -> None:
    """Grouping follows the `WHERE`, so a filter narrows rows before they are
    grouped — which is the reading a caller wants and the only one available."""
    frame = reg.query(q.codelist).filter(q.codelist.agency == "ESTAT").group_by(q.codelist.agency).len()
    assert _rows(frame) == [{"agency": "ESTAT", "len": 2}]


def test_groups_come_back_in_a_stable_order(reg: sl.LocalRegistry) -> None:
    """A hash aggregate has no order of its own. The keys order the result, for
    the same reason the root key orders an ungrouped one."""
    frame = reg.query(q.codelist).group_by(q.codelist.agency).len()
    assert frame.to_sql().sql.endswith("ORDER BY 1 NULLS LAST")
    assert _rows(frame) == _rows(frame)


# ── refusals ──────────────────────────────────────────────────────────────


def test_group_by_after_ordering_or_slicing_is_refused(reg: sl.LocalRegistry) -> None:
    """Neither survives grouping: a row sort key that is not a group key is not
    valid SQL, and a limit *before* grouping aggregates a truncated set."""
    with pytest.raises(SliceError, match="apply to rows, not groups"):
        reg.query(q.codelist).order_by(q.codelist.id).group_by(q.codelist.agency)
    with pytest.raises(SliceError, match="apply to rows, not groups"):
        reg.query(q.codelist).limit(2).group_by(q.codelist.agency)
    with pytest.raises(SliceError, match="apply to rows, not groups"):
        reg.query(q.codelist).offset(1).group_by(q.codelist.agency)


def test_group_by_needs_a_key(reg: sl.LocalRegistry) -> None:
    with pytest.raises(TypeError, match="needs at least one key"):
        reg.query(q.codelist).group_by()


def test_agg_needs_an_aggregate(reg: sl.LocalRegistry) -> None:
    with pytest.raises(TypeError, match=r"at least one aggregate.*\.len\(\)"):
        reg.query(q.codelist).group_by(q.codelist.agency).agg()


def test_a_fan_out_group_key_is_refused(reg: sl.LocalRegistry) -> None:
    with pytest.raises(PathError, match=r"as a group key.*to-many"):
        reg.query(q.codelist).group_by(q.codelist.codes.id).len().collect()


def test_a_fan_out_inside_agg_is_refused(reg: sl.LocalRegistry) -> None:
    """A correlated subquery cannot be aggregated across a group. Refused with
    the reason rather than emitted and left to fail at the database."""
    with pytest.raises(PathError, match=r"in a grouped aggregate.*to-many"):
        reg.query(q.codelist).group_by(q.codelist.agency).agg(n=q.codelist.codes.id.n_unique()).collect()


def test_aggregates_are_keyword_only(reg: sl.LocalRegistry) -> None:
    """An aggregate has no name of its own to fall back on — `count()` over two
    columns would collide — so every one is named at the call site."""
    with pytest.raises(TypeError):
        reg.query(q.codelist).group_by(q.codelist.agency).agg(q.codelist.id.count())  # pyrefly: ignore[bad-argument-count]


# ── an aggregate's own order survives grouping (#414) ─────────────────────


@pytest.fixture
def unsorted() -> Iterator[sl.LocalRegistry]:
    """Agencies inserted out of order, so an unordered `list` cannot pass for a sorted one."""
    with sl.LocalRegistry(":memory:") as registry:
        registry.add_all(
            [
                Codelist(
                    id=f"CL{i}",
                    maintainer=agency,
                    version="1.0",
                    name={"en": "x"},
                    codes=[Code(id="A", name={"en": "a"})],
                )
                for i, agency in enumerate(["ZZ", "AA", "MM", "AA", "BB"])
            ]
        )
        yield registry


def test_a_sorted_aggregate_stays_sorted_under_group_by(unsorted: sl.LocalRegistry) -> None:
    """`.sort()` was honoured through a fan-out and silently dropped in `group_by().agg()`.

    The same `Agg` compiled to `list(DISTINCT x ORDER BY x)` in one clause and
    `list(DISTINCT x)` in the other, so a caller asking for an order got one or
    not depending on which clause carried it.
    """
    agency = q.codelist.agency
    row = (
        unsorted.query(q.codelist)
        .group_by(q.codelist.version)
        .agg(
            up=agency.sort().unique(),
            down=agency.sort(descending=True).unique(),
            all_up=agency.sort().list(),
            by_id=agency.sort_by(q.codelist.id).list(),
        )
        .collect()
        .row(0, named=True)
    )
    assert row["up"] == ["AA", "BB", "MM", "ZZ"]
    assert row["down"] == ["ZZ", "MM", "BB", "AA"]
    assert row["all_up"] == ["AA", "AA", "BB", "MM", "ZZ"]
    assert row["by_id"] == ["ZZ", "AA", "MM", "AA", "BB"], "CL0..CL4, in insertion order"
    assert row["up"] != sorted(row["up"], reverse=True), "discrimination: the data is not already in either order"
