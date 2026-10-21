"""Window functions and `qualify` — a filter the `WHERE` cannot express.

"The latest version of each artefact" is the query SDMX clients write most and
the one this DSL could not say. It needs a rank computed per (agency, id) and
then a filter *on that rank*, which a `WHERE` cannot do: the `WHERE` runs before
the window exists.

So `qualify` is a separate clause, and separate for a reason the third
composition law states — a windowed test does not commute with an ordinary one.
`filter(agency == "ESTAT").qualify(rank == 1)` ranks *within* ESTAT;
`qualify(rank == 1).filter(agency == "ESTAT")` ranks across everything and then
keeps the ESTAT winners. Both are meaningful, they differ, so the DSL names the
stage rather than leaving it to call order.

**The separation is the type.** `.over()` returns `Win`, whose comparisons
produce `WinPred` rather than `Pred`, so neither clause can be handed the
other's predicate. `tests/typing/probe_query_row_kinds.py` holds that half;
what is testable at runtime is here.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import pytest

import sdmxlib as sl
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.organisation import Agency
from sdmxlib.query._expr import WinPred
from sdmxlib.query._joins import PathError
from sdmxlib.query.accessors import CodelistRow, q
from sdmxlib.query.query import Query, SliceError
from sdmxlib.storage.lazy import LazyCodelist

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit


@pytest.fixture
def reg() -> Iterator[sl.LocalRegistry]:
    """Two artefact ids, versioned unevenly, with a tie inside one of them.

    `CL_A` has three versions and `CL_B` two, so a rank that ignored its
    partition would keep one row overall rather than one per id. `CL_C` shares
    `2.0` with nothing — the tie is between the two agencies at `1.0`, which is
    what separates `rank` from `row_number`.
    """
    with sl.LocalRegistry(":memory:") as registry:
        for cid, agency, version in [
            ("CL_A", "ESTAT", "1.0"),
            ("CL_A", "ESTAT", "2.0"),
            ("CL_A", "ESTAT", "1.5"),
            ("CL_B", "OECD", "1.0"),
            ("CL_B", "OECD", "3.0"),
            ("CL_C", "ESTAT", "1.0"),
        ]:
            registry.add(
                Codelist(
                    id=cid,
                    maintainer=Agency(id=agency),
                    version=version,
                    name={"en": f"{cid} {version}"},
                    codes=[Code(id="X", name={"en": "x"})],
                )
            )
        yield registry


def _latest() -> WinPred[CodelistRow]:
    return q.codelist.version.rank(descending=True).over(q.codelist.agency, q.codelist.id) == 1


def _rows(reg: sl.LocalRegistry, query: Query[CodelistRow, LazyCodelist]) -> list[tuple[str, str]]:
    frame = query.frame()
    return sorted(zip(frame["resource_id"].to_list(), frame["version"].to_list(), strict=True))


# ── the motivating query ──────────────────────────────────────────────────


def test_the_latest_version_of_each_artefact(reg: sl.LocalRegistry) -> None:
    """What the milestone opened with, and what a `WHERE` cannot say."""
    assert _rows(reg, reg.query(q.codelist).qualify(_latest())) == [
        ("CL_A", "2.0"),
        ("CL_B", "3.0"),
        ("CL_C", "1.0"),
    ]


def test_without_the_partition_only_one_row_survives(reg: sl.LocalRegistry) -> None:
    """`over()` with no arguments is one window over every row — SQL's own
    meaning for an empty `PARTITION BY`, and the contrast that shows the
    partition above is doing work rather than decorating."""
    overall = q.codelist.version.rank(descending=True).over() == 1
    assert _rows(reg, reg.query(q.codelist).qualify(overall)) == [("CL_B", "3.0")]


def test_ascending_rank_takes_the_oldest(reg: sl.LocalRegistry) -> None:
    oldest = q.codelist.version.rank().over(q.codelist.agency, q.codelist.id) == 1
    assert _rows(reg, reg.query(q.codelist).qualify(oldest)) == [
        ("CL_A", "1.0"),
        ("CL_B", "1.0"),
        ("CL_C", "1.0"),
    ]


def test_the_two_newest_of_each(reg: sl.LocalRegistry) -> None:
    """`.le()` rather than `==`, which is why the comparison surface is more
    than equality."""
    top_two = q.codelist.version.rank(descending=True).over(q.codelist.agency, q.codelist.id).le(2)
    assert _rows(reg, reg.query(q.codelist).qualify(top_two)) == [
        ("CL_A", "1.5"),
        ("CL_A", "2.0"),
        ("CL_B", "1.0"),
        ("CL_B", "3.0"),
        ("CL_C", "1.0"),
    ]


def test_rank_shares_a_number_across_a_tie(reg: sl.LocalRegistry) -> None:
    """Partitioned by agency alone, ESTAT's `CL_A 1.0` and `CL_C 1.0` tie.
    `rank` keeps both; `row_number` picks one."""
    tied = q.codelist.version.rank().over(q.codelist.agency) == 1
    numbered = q.codelist.version.row_number().over(q.codelist.agency) == 1
    assert len(_rows(reg, reg.query(q.codelist).qualify(tied))) == 3
    assert len(_rows(reg, reg.query(q.codelist).qualify(numbered))) == 2


def test_dense_rank_leaves_no_gap(reg: sl.LocalRegistry) -> None:
    """ESTAT ties two rows at 1, so `rank` resumes at 3 while `dense_rank`
    resumes at 2. Asking for 2 therefore finds ESTAT's `CL_A 1.5` only under
    `dense_rank`; OECD has no tie and answers the same either way, which is
    what makes the pair a difference of one row rather than of everything.
    """
    sparse = q.codelist.version.rank().over(q.codelist.agency) == 2
    dense = q.codelist.version.dense_rank().over(q.codelist.agency) == 2
    assert _rows(reg, reg.query(q.codelist).qualify(sparse)) == [("CL_B", "3.0")]
    assert _rows(reg, reg.query(q.codelist).qualify(dense)) == [("CL_A", "1.5"), ("CL_B", "3.0")]


# ── it composes with the other clauses ────────────────────────────────────


def test_filter_then_qualify_ranks_within_the_filter(reg: sl.LocalRegistry) -> None:
    """Half of why `qualify` is a separate clause. `CL_B` is OECD's, so
    excluding it before ranking changes nothing here — the point is the pair
    below."""
    found = reg.query(q.codelist).filter(q.codelist.agency == "ESTAT").qualify(_latest())
    assert _rows(reg, found) == [("CL_A", "2.0"), ("CL_C", "1.0")]


def test_the_two_stages_are_not_the_same_question(reg: sl.LocalRegistry) -> None:
    """The measurement behind naming the stage.

    Ranking by version across *all* agencies and then keeping ESTAT's winners
    answers differently from ranking within ESTAT: `CL_A 2.0` loses to
    `CL_B 3.0` in the global window.
    """
    within = q.codelist.version.rank(descending=True).over() == 1
    ranked_first = reg.query(q.codelist).qualify(within)
    assert _rows(reg, ranked_first) == [("CL_B", "3.0")]
    filtered_first = reg.query(q.codelist).filter(q.codelist.agency == "ESTAT").qualify(within)
    assert _rows(reg, filtered_first) == [("CL_A", "2.0")]


def test_qualify_ands(reg: sl.LocalRegistry) -> None:
    both = reg.query(q.codelist).qualify(
        q.codelist.version.rank(descending=True).over(q.codelist.agency, q.codelist.id).le(2),
        q.codelist.version.rank().over(q.codelist.agency, q.codelist.id).le(2),
    )
    # `CL_A` has three versions, so only its middle one is within two of both
    # ends; `CL_B` has two and `CL_C` one, so every row of theirs qualifies.
    assert _rows(reg, both) == [
        ("CL_A", "1.5"),
        ("CL_B", "1.0"),
        ("CL_B", "3.0"),
        ("CL_C", "1.0"),
    ]


def test_qualify_composes_with_order_and_slice(reg: sl.LocalRegistry) -> None:
    found = reg.query(q.codelist).qualify(_latest()).order_by(q.codelist.id.desc()).limit(2)
    assert found.ids() == ["CL_C", "CL_B"]


def test_qualify_composes_with_terminals(reg: sl.LocalRegistry) -> None:
    latest = reg.query(q.codelist).qualify(_latest())
    assert latest.count() == 3
    assert latest.exists() is True
    assert sorted(latest.ids()) == ["CL_A", "CL_B", "CL_C"]
    assert len(latest.collect()) == 3


def test_qualify_after_a_slice_is_refused(reg: sl.LocalRegistry) -> None:
    with pytest.raises(SliceError, match=r"cannot qualify\(\)"):
        reg.query(q.codelist).limit(2).qualify(_latest())


def test_qualify_stays_lazy(reg: sl.LocalRegistry) -> None:
    latest = reg.query(q.codelist).qualify(_latest())
    assert "qualifier" in repr(latest)
    assert "QUALIFY" in latest.to_sql().sql


def test_the_window_reaches_across_a_to_one_hop(reg: sl.LocalRegistry) -> None:
    """A partition column needs its join planned even when nothing else
    mentions that relation."""
    per_codelist = q.code.id.row_number().over(q.code.codelist.agency) == 1
    assert reg.query(q.code).qualify(per_codelist).count() == 2


# ── projection ────────────────────────────────────────────────────────────


def test_a_windowed_column_projects(reg: sl.LocalRegistry) -> None:
    """`Win` is a `Col`, so only the *filter* position is restricted."""
    frame = (
        reg.query(q.codelist)
        .order_by(q.codelist.id, q.codelist.version)
        .select(
            cl=q.codelist.id,
            v=q.codelist.version,
            n=q.codelist.version.rank(descending=True).over(q.codelist.agency, q.codelist.id),
        )
        .collect()
    )
    ranks = dict(zip(frame["v"].to_list(), frame["n"].to_list(), strict=True))
    assert ranks["2.0"] == 1
    assert ranks["1.5"] == 2


def test_an_aggregate_becomes_a_window_with_over(reg: sl.LocalRegistry) -> None:
    """`count()` counts a fan-out's children; `.over()` counts the outer rows
    sharing a partition. Same function, a different question."""
    frame = reg.query(q.codelist).select(cl=q.codelist.id, n=q.codelist.id.count().over(q.codelist.agency)).collect()
    counts = dict(zip(frame["cl"].to_list(), frame["n"].to_list(), strict=True))
    assert counts["CL_A"] == 4
    assert counts["CL_B"] == 2


def test_the_fan_out_aggregate_still_means_the_children(reg: sl.LocalRegistry) -> None:
    """The contrast that makes the previous test mean something: without
    `.over()`, `count()` is still the correlated subquery over codes."""
    frame = reg.query(q.codelist).select(cl=q.codelist.id, n=q.codelist.codes.count()).collect()
    assert set(frame["n"].to_list()) == {1}


def test_lag_reads_the_previous_row(reg: sl.LocalRegistry) -> None:
    frame = (
        reg.query(q.codelist)
        .filter(q.codelist.id == "CL_A")
        .order_by(q.codelist.version)
        .select(v=q.codelist.version, previous=q.codelist.version.lag().over(q.codelist.id))
        .collect()
    )
    pairs = dict(zip(frame["v"].to_list(), frame["previous"].to_list(), strict=True))
    assert pairs == {"1.0": None, "1.5": "1.0", "2.0": "1.5"}


def test_lead_reads_the_next_row(reg: sl.LocalRegistry) -> None:
    frame = (
        reg.query(q.codelist)
        .filter(q.codelist.id == "CL_A")
        .order_by(q.codelist.version)
        .select(v=q.codelist.version, following=q.codelist.version.lead().over(q.codelist.id))
        .collect()
    )
    pairs = dict(zip(frame["v"].to_list(), frame["following"].to_list(), strict=True))
    assert pairs == {"1.0": "1.5", "1.5": "2.0", "2.0": None}


def test_lag_takes_an_offset(reg: sl.LocalRegistry) -> None:
    frame = (
        reg.query(q.codelist)
        .filter(q.codelist.id == "CL_A")
        .order_by(q.codelist.version)
        .select(v=q.codelist.version, two_back=q.codelist.version.lag(2).over(q.codelist.id))
        .collect()
    )
    pairs = dict(zip(frame["v"].to_list(), frame["two_back"].to_list(), strict=True))
    assert pairs == {"1.0": None, "1.5": None, "2.0": "1.0"}


def test_a_window_over_a_label_subscripts_it(reg: sl.LocalRegistry) -> None:
    """The `MAP`/side-table reference rule applies inside a window too —
    ranking by the whole `localized_text` subquery, not by a raw column."""
    newest_label = q.codelist.name["en"].rank(descending=True).over(q.codelist.agency) == 1
    assert _rows(reg, reg.query(q.codelist).qualify(newest_label)) == [("CL_B", "3.0"), ("CL_C", "1.0")]


# ── refusals ──────────────────────────────────────────────────────────────


def test_a_window_over_a_fan_out_is_refused(reg: sl.LocalRegistry) -> None:
    bad = q.codelist.codes.id.rank().over(q.codelist.agency) == 1
    with pytest.raises(PathError, match=r"cannot use .* in a window.*to-many"):
        reg.query(q.codelist).qualify(bad).ids()


def test_a_partition_over_a_fan_out_is_refused(reg: sl.LocalRegistry) -> None:
    bad = q.codelist.version.rank().over(q.codelist.codes.id) == 1
    with pytest.raises(PathError, match=r"cannot use .* in a window.*to-many"):
        reg.query(q.codelist).qualify(bad).ids()


def test_a_window_function_is_unusable_before_over(reg: sl.LocalRegistry) -> None:
    """Splitting the two stages makes "a window function with no partition"
    unspellable rather than merely discouraged."""
    unframed = q.codelist.version.rank()
    assert not hasattr(unframed, "path")
    assert hasattr(unframed, "over")


# ── the windowed forms the #304 mutation triage found unexercised ──────────


def test_a_windowed_count_counts_values_not_rows(reg: sl.LocalRegistry) -> None:
    """`count()` over a column skips that column's NULLs.

    This emitted `count(*)` for every windowed count until #304's triage,
    which dropped the counted column from the SQL entirely while still
    emitting its bind parameters — so counting a *label* raised `Parameter
    argument/count mismatch`, and counting any nullable column silently
    included its NULLs.

    Nothing caught it because the only test counted `id`, where `count(*)`
    and `count(id)` agree. No codelist in this fixture has a description, so
    here they cannot: `count(*)` is four and two, `count(description)` zero.
    """
    frame = (
        reg.query(q.codelist)
        .order_by(q.codelist.id, q.codelist.version)
        .select(
            cl=q.codelist.id,
            rows=q.codelist.id.count().over(q.codelist.agency),
            described=q.codelist.description["en"].count().over(q.codelist.agency),
        )
        .collect()
    )
    # `rows` is the count(*) answer and pins the partitions; `described`
    # is the count(column) one, and a compiler emitting count(*) for both
    # would make the second column equal the first.
    assert dict(zip(frame["rows"].to_list(), frame["described"].to_list(), strict=True)) == {4: 0, 2: 0}


def test_n_unique_windows_as_a_distinct_count(reg: sl.LocalRegistry) -> None:
    """`n_unique` is spelled `count(DISTINCT ...)` and renamed on the way.

    Two things only this exercises: the `DISTINCT` argument form, and the
    rename to `count` — DuckDB has no `n_unique`, so a mutation to either the
    key or the value of that mapping produces SQL it rejects.
    """
    frame = (
        reg.query(q.codelist)
        .order_by(q.codelist.id, q.codelist.version)
        .select(
            cl=q.codelist.id,
            rows=q.codelist.id.count().over(q.codelist.agency),
            distinct_versions=q.codelist.version.n_unique().over(q.codelist.agency),
        )
        .collect()
    )
    # ESTAT has four rows over three distinct versions (CL_C repeats `1.0`),
    # so a distinct count that had collapsed into a plain count would read 4.
    assert dict(zip(frame["rows"].to_list(), frame["distinct_versions"].to_list(), strict=True)) == {4: 3, 2: 2}


def test_a_windowed_list_gathers_the_whole_partition(reg: sl.LocalRegistry) -> None:
    """`list` takes no window ORDER BY, which is what keeps it whole.

    `count`, `n_unique` and `list` are excluded from the frame's own
    `ORDER BY` because an aggregate given one acquires a *running* meaning.
    Ordering this window would return a growing prefix per row instead of the
    partition's full list, so the assertion is on every row, not the last.
    """
    frame = (
        reg.query(q.codelist)
        .filter(q.codelist.agency == "OECD")
        .order_by(q.codelist.version)
        .select(v=q.codelist.version, all_versions=q.codelist.version.list().over(q.codelist.agency))
        .collect()
    )
    assert [sorted(row) for row in frame["all_versions"].to_list()] == [["1.0", "3.0"], ["1.0", "3.0"]]


def test_lead_takes_an_offset(reg: sl.LocalRegistry) -> None:
    """The mirror of `test_lag_takes_an_offset`, and not redundant with it.

    Without an offset the two branches are indistinguishable: DuckDB's
    `lead(x)` already means `lead(x, 1)`, so dropping `lead` from the pair
    that renders an offset changes nothing at all — only a second step apart
    can tell them apart.
    """
    frame = (
        reg.query(q.codelist)
        .filter(q.codelist.id == "CL_A")
        .order_by(q.codelist.version)
        .select(v=q.codelist.version, two_ahead=q.codelist.version.lead(2).over(q.codelist.id))
        .collect()
    )
    assert dict(zip(frame["v"].to_list(), frame["two_ahead"].to_list(), strict=True)) == {
        "1.0": "2.0",
        "1.5": None,
        "2.0": None,
    }


# ── a window inside a computed expression (#328) ───────────────────────────


def test_a_windowed_value_divides_correctly(reg: sl.LocalRegistry) -> None:
    """`x / x.sum().over(…)` — a share, and it was silently `1` for every row.

    `Win` is a `Col`, so a windowed operand inside a computed expression went
    to the plain column renderer and came out as its own **source column**:
    `depth / depth.sum().over(codelist)` compiled to `(s0.depth / s0.depth)`.
    Valid SQL, wrong answer, no error anywhere — the failure shape this
    project's differential tests exist to catch, reached here by a route
    nothing had exercised.

    Found while enabling percent-of-total for observations (#297), but it is a
    structural bug and shipped as one, which is why the regression test lives
    here rather than beside the data plane.
    """
    query = reg.query(q.code).select(share=q.code.depth / q.code.depth.sum().over(q.code.codelist.id))
    sql = query.to_sql().sql
    assert "OVER (PARTITION BY" in sql, "the window must survive into the expression"
    assert "(s0.depth / s0.depth)" not in sql, "the operand must not collapse to its source column"

    # Every code in this fixture sits at depth 0, so each partition totals 0
    # and the share is `0/0` — NaN, which is DuckDB's answer and not something
    # to assert around. The *value* semantics are pinned in
    # `test_query_observations.py`, whose fixture has real measures; what is
    # unique here is that the bug was structural, so the SQL is the assertion.
    shares = query.collect()["share"].to_list()
    assert len(shares) == 6, "one row per code in the fixture"
    assert all(math.isnan(value) for value in shares), "0/0 is NaN, once per code"


def test_a_windowed_partition_is_joined_even_inside_an_expression(reg: sl.LocalRegistry) -> None:
    """The second half of the same bug, and the reason the first fix was not enough.

    A window's partition columns are reached through nothing else, so the join
    planner has to collect them. It did — for a *top-level* window. One nested
    in a computed expression went uncollected and died on
    `KeyError: ('codelist',)` the moment the render was corrected.
    """
    sql = reg.query(q.code).select(share=q.code.depth / q.code.depth.sum().over(q.code.codelist.id)).to_sql().sql
    assert "JOIN sdmx.artefact" in sql, "the partition's relation must be joined"
