"""Projection: `select()` collects fan-outs rather than multiplying rows.

The nested shape is the one #280 opened with — "every dataflow x dimension x
codelist as one frame" — and the reason it is nested by default is that nesting
cannot silently change anyone's row count while exploding is one polars call
away.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import duckdb
import pytest

import sdmxlib as sl
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.datastructure import DataStructure, Dimension
from sdmxlib.model.organisation import Agency
from sdmxlib.model.representation import Facet
from sdmxlib.query._expr import Agg, Col
from sdmxlib.query._joins import PathError
from sdmxlib.query.accessors import CodelistRow, q
from tests.support.factories import concept_ref

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit


@pytest.fixture
def reg() -> Iterator[sl.LocalRegistry]:
    with sl.LocalRegistry(":memory:") as registry:
        registry.add(
            Codelist(
                id="CL_AREA",
                maintainer=Agency(id="ESTAT"),
                name={"en": "Areas"},
                # CA and US share a parent, so `parent_id` repeats — without a
                # repeating column `n_unique` is indistinguishable from
                # `count(*)` and its test proves nothing.
                codes=[
                    Code(id="W", name={"en": "World"}),
                    Code(id="CA", name={"en": "CA"}, parent_id="W"),
                    Code(id="US", name={"en": "US"}, parent_id="W"),
                ],
            )
        )
        registry.add(
            Codelist(
                id="CL_FREQ",
                maintainer=Agency(id="OECD"),
                name={"en": "Frequency"},
                codes=[Code(id="A", name={"en": "Annual"})],
            )
        )
        registry.add(Codelist(id="CL_EMPTY", maintainer=Agency(id="ESTAT"), name={"en": "Empty"}))
        yield registry


# ── flat projection ───────────────────────────────────────────────────────


def test_positional_columns_take_their_own_name(reg: sl.LocalRegistry) -> None:
    """The accessor's spelling, not the storage column — `id`, not `resource_id`.

    A caller who wrote `q.codelist.id` and got back a frame keyed
    `resource_id` has to learn the schema the path language exists to hide,
    and `df["id"]` raises `KeyError` (#363).
    """
    frame = reg.query(q.codelist).select(q.codelist.id, q.codelist.agency).collect()
    assert frame.columns == ["id", "agency"]
    assert frame.height == 3


def test_keywords_name_their_column(reg: sl.LocalRegistry) -> None:
    frame = reg.query(q.codelist).select(cl=q.codelist.id).collect()
    assert frame.columns == ["cl"]


def test_a_projection_honours_the_filter_and_the_discriminator(reg: sl.LocalRegistry) -> None:
    frame = reg.query(q.codelist).filter(q.codelist.agency == "ESTAT").select(cl=q.codelist.id).collect()
    assert sorted(frame["cl"].to_list()) == ["CL_AREA", "CL_EMPTY"]


def test_select_needs_something_to_project(reg: sl.LocalRegistry) -> None:
    with pytest.raises(TypeError, match="at least one column"):
        reg.query(q.codelist).select()


# ── fan-out: nested by default ────────────────────────────────────────────


def test_a_collected_fan_out_keeps_one_row_per_parent(reg: sl.LocalRegistry) -> None:
    """The shape #280 opened with. Three codes, still one row."""
    frame = reg.query(q.codelist).select(cl=q.codelist.id, codes=q.codelist.codes.id.list()).collect()
    assert frame.height == 3
    row = frame.filter(frame["cl"] == "CL_AREA")
    assert sorted(row["codes"].to_list()[0]) == ["CA", "US", "W"]


def test_count_over_a_fan_out(reg: sl.LocalRegistry) -> None:
    frame = reg.query(q.codelist).select(cl=q.codelist.id, n=q.codelist.codes.count()).collect()
    counts = dict(zip(frame["cl"].to_list(), frame["n"].to_list(), strict=True))
    assert counts == {"CL_AREA": 3, "CL_FREQ": 1, "CL_EMPTY": 0}


def test_n_unique_counts_distinct_values(reg: sl.LocalRegistry) -> None:
    """Over a column that repeats, or the test cannot tell a distinct count
    from `count(*)` — `CL_AREA` has three codes and two distinct parent ids,
    one of which is `NULL` (its root code has no parent).

    Two, not one: a null is a value here, as it is in polars (#326).
    """
    frame = (
        reg.query(q.codelist)
        .select(
            cl=q.codelist.id,
            total=q.codelist.codes.count(),
            parents=q.codelist.codes.parent.id.n_unique(),
        )
        .collect()
    )
    row = frame.filter(frame["cl"] == "CL_AREA")
    assert row["total"].to_list()[0] == 3
    assert row["parents"].to_list()[0] == 2, "'W' and the root's NULL"


def test_a_parent_with_no_children_collects_to_empty_not_missing(
    reg: sl.LocalRegistry,
) -> None:
    """A correlated subquery preserves the row; it does not drop it the way an
    inner join would. And what it collects to is `[]`.

    The assertion used to read `in ([], None)` — an open question written down,
    with the test's own name claiming "empty not missing" while permitting
    missing. #341 settled it: `[]`, as polars answers.
    """
    frame = reg.query(q.codelist).select(cl=q.codelist.id, codes=q.codelist.codes.id.list()).collect()
    empty = frame.filter(frame["cl"] == "CL_EMPTY")
    assert empty.height == 1
    assert empty["codes"].to_list()[0] == []


def test_two_aggregates_do_not_multiply_rows(reg: sl.LocalRegistry) -> None:
    """Correlated subqueries, not joins — which is why several compose."""
    frame = (
        reg.query(q.codelist)
        .select(
            cl=q.codelist.id,
            codes=q.codelist.codes.id.list(),
            n=q.codelist.codes.count(),
            distinct=q.codelist.codes.id.n_unique(),
        )
        .collect()
    )
    assert frame.height == 3


# ── structs: alignment by construction ────────────────────────────────────


def test_multi_column_list_returns_structs(reg: sl.LocalRegistry) -> None:
    """Two separate `list()` calls align only incidentally; one struct list
    cannot misalign. See `query-language-examples.md` §4."""
    frame = (
        reg.query(q.codelist)
        .filter(q.codelist.id == "CL_AREA")
        .select(
            cl=q.codelist.id,
            # `.alias` because both project as `id` and a struct cannot hold
            # two fields of one name — the escape #363 added for exactly this,
            # since `list` is positional and `select(**named)` cannot reach
            # inside it.
            codes=q.codelist.codes.list(q.codelist.codes.id, q.codelist.codes.parent.id.alias("parent")),
        )
        .collect()
    )
    collected = frame["codes"].to_list()[0]
    assert {c["id"] for c in collected} == {"CA", "US", "W"}
    assert all("parent" in c for c in collected)


def test_list_needs_a_column(reg: sl.LocalRegistry) -> None:
    with pytest.raises(TypeError, match="at least one column"):
        q.codelist.codes.list()


# ── refusals ──────────────────────────────────────────────────────────────


def test_projecting_a_fan_out_without_collecting_is_refused(reg: sl.LocalRegistry) -> None:
    """It has no single value per row, and the message names every way out."""
    with pytest.raises(PathError, match=r"crosses a to-many edge.*\.list\(\).*\.count\(\)"):
        reg.query(q.codelist).select(codes=q.codelist.codes.id).collect()


def test_aggregating_a_flat_path_is_refused(reg: sl.LocalRegistry) -> None:
    with pytest.raises(PathError, match="never fans out"):
        reg.query(q.codelist).select(n=q.codelist.id.count()).collect()


def test_one_aggregate_spans_one_edge(reg: sl.LocalRegistry) -> None:
    """Collecting from two different fan-outs into one list is refused rather
    than silently zipping unrelated children."""
    with pytest.raises(PathError, match="different fan-outs"):
        reg.query(q.dsd).select(x=q.dsd.dimensions.list(q.dsd.dimensions.id, q.dsd.attributes.id)).collect()


def test_a_frame_cannot_be_filtered(reg: sl.LocalRegistry) -> None:
    """`select` is not a row refinement — it changes what a row is."""
    frame = reg.query(q.codelist).select(cl=q.codelist.id)
    with pytest.raises(TypeError, match="Filter first, then select"):
        frame.filter(q.codelist.agency == "ESTAT")


# ── laziness ──────────────────────────────────────────────────────────────


def test_a_frame_is_lazy(reg: sl.LocalRegistry) -> None:
    frame = reg.query(q.codelist).select(cl=q.codelist.id)
    assert "lazy" in repr(frame)
    assert frame.to_sql().sql
    assert "binds" in frame.explain()


def test_a_frame_honours_a_slice(reg: sl.LocalRegistry) -> None:
    assert reg.query(q.codelist).limit(2).select(cl=q.codelist.id).collect().height == 2


# ── unique: which distinct values, not how many ───────────────────────────


def _ordered(values: list[object]) -> list[object]:
    """Sort a collected list, nulls last. DuckDB does not order a DISTINCT."""
    return sorted(values, key=lambda value: (value is None, value))


def test_unique_collects_each_value_once(reg: sl.LocalRegistry) -> None:
    """The third corner of the aggregate set.

    `list` keeps duplicates and `n_unique` counts them away to a number;
    neither answers *which* values a fan-out holds, which was the only one of
    the three a caller had to leave the language for — collect everything, then
    dedupe in polars afterwards.

    `parent.id` is the discriminating column in this fixture: CA and US share
    `W`, so a `unique` that quietly compiled to `list` would return three
    entries here rather than two.
    """
    frame = (
        reg.query(q.codelist)
        .filter(q.codelist.id == "CL_AREA")
        .select(
            cl=q.codelist.id,
            all_parents=q.codelist.codes.parent.id.list(),
            distinct_parents=q.codelist.codes.parent.id.unique(),
        )
        .collect()
    )
    assert _ordered(frame["all_parents"].to_list()[0]) == ["W", "W", None]
    assert _ordered(frame["distinct_parents"].to_list()[0]) == ["W", None]


def test_unique_and_n_unique_agree_about_null(reg: sl.LocalRegistry) -> None:
    """`len(unique()) == n_unique()`, always — the invariant #326 restored.

    They used to differ by exactly one whenever the fan-out held a null, and
    this test asserted the gap "so the day it changes this test says so".
    This is that day.

    `unique` compiles to `list(DISTINCT x)`, which keeps a null; `n_unique`
    was `count(DISTINCT x)`, which does not count one — **DuckDB's two
    functions disagree with each other** and the language inherited both. Both
    names are polars', where a null *is* a value and the two agree always, and
    `list()` already keeps nulls here too, so `n_unique` was the odd one out
    on both counts. CLAUDE.md's rule is to check a DSL decision against polars
    where there is a counterpart; this was the case where the name was taken
    and the semantics were not.
    """
    frame = (
        reg.query(q.codelist)
        .filter(q.codelist.id == "CL_AREA")
        .select(
            distinct_parents=q.codelist.codes.parent.id.unique(),
            how_many=q.codelist.codes.parent.id.n_unique(),
        )
        .collect()
    )
    distinct = _ordered(frame["distinct_parents"].to_list()[0])
    assert distinct == ["W", None]
    assert frame["how_many"].to_list()[0] == len(distinct), "the invariant, not just the number"
    assert frame["how_many"].to_list()[0] == 2


def test_unique_over_several_columns_dedupes_the_struct() -> None:
    """The multi-column form, matching `list`'s — and its own registry.

    Deduplication is over the whole struct, so two children agreeing on every
    collected column collapse into one. The shared fixture cannot show it: its
    code ids are unique, and any struct including `id` is distinct by
    construction. Two codes sharing a name *and* a parent can.
    """
    with sl.LocalRegistry(":memory:") as registry:
        registry.add(
            Codelist(
                id="CL_TWIN",
                maintainer=Agency(id="ESTAT"),
                name={"en": "Twins"},
                codes=[
                    Code(id="W", name={"en": "World"}),
                    Code(id="X", name={"en": "same"}, parent_id="W"),
                    Code(id="Y", name={"en": "same"}, parent_id="W"),
                ],
            )
        )
        frame = (
            registry.query(q.codelist)
            .select(
                pairs=q.codelist.codes.unique(q.codelist.codes.name["en"], q.codelist.codes.parent.id),
                all_pairs=q.codelist.codes.list(q.codelist.codes.name["en"], q.codelist.codes.parent.id),
            )
            .collect()
        )
    assert len(frame["all_pairs"].to_list()[0]) == 3, "one struct per code"
    assert len(frame["pairs"].to_list()[0]) == 2, "X and Y agree on both columns and collapse"


def test_unique_of_one_column_is_a_plain_list(reg: sl.LocalRegistry) -> None:
    """Single column, no struct wrapper — the same rule `list` follows.

    Worth pinning: the multi-column form exists, so a reader could reasonably
    expect a one-field struct back from the one-column call.
    """
    frame = (
        reg.query(q.codelist)
        .filter(q.codelist.id == "CL_AREA")
        .select(parents=q.codelist.codes.unique(q.codelist.codes.parent.id))
        .collect()
    )
    assert _ordered(frame["parents"].to_list()[0]) == ["W", None]


def test_unique_windows(reg: sl.LocalRegistry) -> None:
    """`unique` is an aggregate, so `.over()` frames it like the others.

    It takes no window `ORDER BY` — an aggregate given one acquires a running
    meaning, and this would return a growing prefix per row rather than the
    partition's distinct set.
    """
    frame = (
        reg.query(q.code)
        .order_by(q.code.id)
        .select(code=q.code.id, parents_in_list=q.code.parent.id.unique().over(q.code.codelist.id))
        .collect()
    )
    per_code = {
        code: _ordered(parents)
        for code, parents in zip(frame["code"].to_list(), frame["parents_in_list"].to_list(), strict=True)
    }
    assert per_code["CA"] == ["W", None], "every code of CL_AREA sees the same set"
    assert per_code["W"] == per_code["CA"]
    assert per_code["A"] == [None], "CL_FREQ's only code is parentless"


def test_the_two_uniques_are_different_operations(reg: sl.LocalRegistry) -> None:
    """`Projection.unique()` dedupes rows; `Col.unique()` dedupes values.

    They read alike and are not interchangeable, so the distinction is
    asserted rather than left to a docstring. Both names are polars', where
    `DataFrame.unique()` and `Expr.unique()` divide exactly the same way — so
    the pair is imported, not invented.
    """
    rows = reg.query(q.code).select(parent=q.code.parent.id).unique().collect()
    assert rows.height == 2, "one row per distinct parent, the null included"
    assert _ordered(rows["parent"].to_list()) == ["W", None]

    values = (
        reg.query(q.codelist)
        .filter(q.codelist.id == "CL_AREA")
        .select(parents=q.codelist.codes.parent.id.unique())
        .collect()
    )
    assert values.height == 1, "one row, whose single cell holds the distinct values"
    assert _ordered(values["parents"].to_list()[0]) == ["W", None]


# ── the numeric aggregates (#327) ──────────────────────────────────────────


def test_sum_mean_min_and_max_measure_a_fan_out(reg: sl.LocalRegistry) -> None:
    """Four aggregates the set was missing, over the depths of a codelist.

    `depth` is the only numeric column this fixture offers, and the cookbook is
    right that an average of it says more about how someone drew the codelist
    than about the data. They exist for the data plane (#297), where the column
    is an observation value — but they are ordinary aggregates and work here.
    """
    frame = (
        reg.query(q.codelist)
        .filter(q.codelist.id == "CL_AREA")
        .select(
            total=q.codelist.codes.depth.sum(),
            average=q.codelist.codes.depth.mean(),
            shallowest=q.codelist.codes.depth.min(),
            deepest=q.codelist.codes.depth.max(),
        )
        .collect()
    )
    # W at depth 0, CA and US at depth 1.
    assert int(frame["total"][0]) == 2
    assert frame["average"][0] == pytest.approx(2 / 3)
    assert frame["shallowest"][0] == 0
    assert frame["deepest"][0] == 1


def test_min_and_max_order_text_too(reg: sl.LocalRegistry) -> None:
    """They are not numeric aggregates, which is why they are usable more often.

    SQL and polars both order strings, so `min` over a code id is its
    lexicographic first. `sum` and `mean` have no such reading.
    """
    frame = (
        reg.query(q.codelist)
        .filter(q.codelist.id == "CL_AREA")
        .select(first=q.codelist.codes.id.min(), last=q.codelist.codes.id.max())
        .collect()
    )
    assert frame["first"][0] == "CA"
    assert frame["last"][0] == "W"


def test_mean_skips_nulls_rather_than_counting_them_as_zero() -> None:
    """`mean` of `[10, NULL]` is 10, not 5.

    SQL and polars agree here, which is worth an assertion precisely because
    #326 was a case where they did not — so "they agree" is a fact about this
    operation rather than a general rule to lean on.

    The two rules sit side by side here on one column, which is the clearest
    place to see that they are different rules rather than one: `mean` skips
    the null, and `n_unique` counts it as a value. Both match polars.

    A facet's `max_length` is the nullable numeric column: a dimension
    represented by a codelist has no facet at all.
    """
    with sl.LocalRegistry(":memory:") as registry:
        registry.add(
            DataStructure(
                id="DSD_FACET",
                maintainer=Agency(id="ESTAT"),
                name={"en": "faceted"},
                dimensions=[
                    Dimension(
                        id="TEXTY", concept=concept_ref("TEXTY"), representation=Facet(type="String", max_length=10)
                    ),
                    Dimension(id="PLAIN", concept=concept_ref("PLAIN")),
                ],
            )
        )
        frame = (
            registry.query(q.dsd)
            .select(
                average=q.dsd.dimensions.facet.max_length.mean(),
                counted=q.dsd.dimensions.facet.max_length.n_unique(),
                rows=q.dsd.dimensions.id.count(),
            )
            .collect()
        )
    assert frame["rows"][0] == 2, "both dimensions are there"
    assert frame["counted"][0] == 2, "10 and the other dimension's NULL — a null is a value (#326)"
    assert frame["average"][0] == pytest.approx(10.0), "the null is skipped, not averaged in"


def test_a_windowed_aggregate_totals_its_partition(reg: sl.LocalRegistry) -> None:
    """`.over()` frames, it does not accumulate — and percent-of-total needs that.

    `sum(v) OVER (PARTITION BY g ORDER BY v)` is a *running* total, and this
    language means the partition's total by `.over()` everywhere else. So the
    numeric aggregates join `count`, `list` and the rest in emitting no window
    `ORDER BY`; `x.sum() / x.sum().over()` would otherwise be a share of a
    prefix.
    """
    frame = (
        reg.query(q.code)
        .order_by(q.code.id)
        .select(code=q.code.id, depth=q.code.depth, in_codelist=q.code.depth.sum().over(q.code.codelist.id))
        .collect()
    )
    per_code = dict(zip(frame["code"].to_list(), [int(v) for v in frame["in_codelist"].to_list()], strict=True))
    assert per_code == {"CA": 2, "US": 2, "W": 2, "A": 0}, "every row sees its whole partition"


def test_summing_text_is_refused_by_the_database(reg: sl.LocalRegistry) -> None:
    """Where the refusal comes from, stated rather than implied.

    The compiler carries no column types, so it cannot reject this at build
    time. DuckDB's message names the function and the type, which is enough to
    act on — and #327 holds the question of whether we should know better.
    """
    with pytest.raises(duckdb.BinderException, match="sum"):
        reg.query(q.codelist).select(x=q.codelist.codes.id.sum()).collect()


def test_the_window_form_counts_null_the_same_way(reg: sl.LocalRegistry) -> None:
    """`n_unique().over()` and `n_unique()` must agree about null.

    Two compile sites spell this aggregate — the plain one and the windowed
    one — and #326 changed the first. A rule that held in a `SELECT` and not
    in an `OVER` would be worse than the original disagreement, because the
    caller would have no name to distinguish them by.
    """
    frame = (
        reg.query(q.code)
        .filter(q.code.codelist.id == "CL_AREA")
        .select(
            code=q.code.id,
            windowed=q.code.parent.id.n_unique().over(q.code.codelist.id),
        )
        .collect()
    )
    assert set(frame["windowed"].to_list()) == {2}, "'W' and the root's NULL, in every row"


def test_n_unique_matches_the_length_of_unique_on_every_codelist(reg: sl.LocalRegistry) -> None:
    """The invariant, over whatever the fixture holds rather than one case.

    polars guarantees `len(s.unique()) == s.n_unique()` for any series. That
    is the property #326 restored, and stating it as a comparison rather than
    as two numbers means a fixture change cannot quietly stop testing it.

    Held without normalising since #341: an empty fan-out collects to `[]`,
    so `len(...)` needs no `or []` to be defined. That `or []` was here, and
    was hiding the fact that the invariant did not actually hold for an empty
    group.
    """
    frame = (
        reg.query(q.codelist)
        .select(
            cl=q.codelist.id,
            parents=q.codelist.codes.parent.id.unique(),
            how_many=q.codelist.codes.parent.id.n_unique(),
            names=q.codelist.codes.name["en"].unique(),
            how_many_names=q.codelist.codes.name["en"].n_unique(),
        )
        .collect()
    )
    assert frame.height > 1, "more than one codelist, or the property is one example"
    for row in frame.to_dicts():
        assert len(row["parents"]) == row["how_many"], f"{row['cl']}: parents"
        assert len(row["names"]) == row["how_many_names"], f"{row['cl']}: names"


def test_order_by_on_a_projection_refuses_a_column_it_dropped(reg: sl.LocalRegistry) -> None:
    """A projected row has only its own columns, so sorting by one it dropped
    cannot be compiled — and the refusal names the way out.

    Pinned here rather than in a documentation driver, which is where it used
    to be tested from: this is the language's contract, not the page's.
    """
    projection = reg.query(sl.q.codelist).select(name=sl.q.codelist.name["en"])

    with pytest.raises(TypeError) as caught:
        projection.order_by(sl.q.codelist.id)

    message = str(caught.value)
    assert "order_by()" in message
    assert "name" in message, "the message lists what the projection does carry"
    assert "sort before select()" in message, "and names the fix"


def test_order_by_on_a_projection_accepts_one_of_its_own(reg: sl.LocalRegistry) -> None:
    """The other half — without it the refusal above could be unconditional."""
    frame = reg.query(sl.q.codelist).select(name=sl.q.codelist.name["en"]).order_by(sl.q.codelist.name["en"])
    assert frame.collect().height > 0


# ── naming a projected item: identity, alias, or structure ───────────────────


def test_order_by_an_aggregate_written_twice(reg: sl.LocalRegistry) -> None:
    """Two textually identical programs behaved differently.

    `Select._position_of` matched columns structurally and aggregates by `is`,
    so holding the aggregate in a variable worked and writing it out twice was
    a `TypeError`. `Agg.__canonical__` closes it — `__eq__` was spoken for
    (it builds a predicate) so an aggregate had no other way to say it means
    the same thing.
    """
    held = q.codelist.codes.count()
    with_variable = reg.query(q.codelist).select(q.codelist.id, n=held).order_by(held)
    written_twice = (
        reg.query(q.codelist).select(q.codelist.id, n=q.codelist.codes.count()).order_by(q.codelist.codes.count())
    )
    assert written_twice.collect().to_dicts() == with_variable.collect().to_dicts()


def test_order_by_an_aggregate_by_its_output_name(reg: sl.LocalRegistry) -> None:
    """`order_by("n")` — what SQL and polars both accept, and the only spelling
    available when the projection was built somewhere else."""
    by_name = reg.query(q.codelist).select(q.codelist.id, n=q.codelist.codes.count()).order_by("n")
    by_value = (
        reg.query(q.codelist).select(q.codelist.id, n=q.codelist.codes.count()).order_by(q.codelist.codes.count())
    )
    assert by_name.collect().to_dicts() == by_value.collect().to_dicts()


def test_order_by_a_column_by_its_output_name(reg: sl.LocalRegistry) -> None:
    """The same for a plain column, including one the caller renamed."""
    rows = reg.query(q.codelist).select(which=q.codelist.id).order_by("which").collect()
    assert rows["which"].to_list() == sorted(rows["which"].to_list())


def test_an_unknown_output_name_is_refused_and_lists_the_real_ones(reg: sl.LocalRegistry) -> None:
    """A string that names nothing must not be mistaken for a column."""
    with pytest.raises(TypeError) as caught:
        reg.query(q.codelist).select(q.codelist.id, n=q.codelist.codes.count()).order_by("nope")

    message = str(caught.value)
    assert "'nope'" in message, "the message quotes the name that was not found"
    assert "n" in message, "and lists what is actually projected"


def test_two_different_aggregates_do_not_collide(reg: sl.LocalRegistry) -> None:
    """Structural matching must distinguish aggregates that differ only in
    kind — otherwise `order_by(count())` could sort by an `n_unique` column."""
    frame = reg.query(q.codelist).select(q.codelist.id, n=q.codelist.codes.count(), u=q.codelist.codes.id.n_unique())
    assert frame.order_by(q.codelist.codes.count()).order == ((2, True),)
    assert frame.order_by(q.codelist.codes.id.n_unique()).order == ((3, True),)


def test_an_unframed_window_says_to_frame_it(reg: sl.LocalRegistry) -> None:
    """A window function in `select` is a *missing call*, not a wrong kind of
    thing, and the generic "takes a column" message sent people looking for a
    column they already had.

    Framed windows have always worked here — this is only about the refusal
    reading correctly.
    """
    with pytest.raises(TypeError) as caught:
        # The refusal is the subject: a bare window function has to be written
        # into `select` for the runtime message to be asserted.
        reg.query(q.codelist).select(rn=q.codelist.id.row_number())  # pyrefly: ignore[bad-argument-type]

    message = str(caught.value)
    assert ".over(" in message, "the message names the call that is missing"

    framed = reg.query(q.codelist).select(q.codelist.id, rn=q.codelist.id.row_number().over()).collect()
    assert framed["rn"].to_list() == list(range(1, framed.height + 1))


def test_two_columns_with_the_same_output_name_are_refused(reg: sl.LocalRegistry) -> None:
    """Two `id`s in one projection are two columns called `id`.

    polars would silently rename the second to `id_1` — a frame whose columns
    do not mean what the query said, and whose second is named after an
    implementation detail of the collision. Refused rather than auto-renamed,
    because only the caller knows which is which.

    The collision is *more* reachable since #363: `q.dataflow.id` and
    `q.dataflow.dsd.id` used to differ only by both being `resource_id`, and
    now both spell `id`. Distinct storage names never made them distinct —
    they collided before too — but a caller now sees why at a glance.
    """
    with pytest.raises(TypeError) as caught:
        reg.query(q.dataflow).select(q.dataflow.id, q.dataflow.dsd.id)

    message = str(caught.value)
    assert "'id'" in message, "the message names the column that collides"
    assert "select(flow=" in message, "and shows the keyword form that fixes it"


def test_naming_them_resolves_it(reg: sl.LocalRegistry) -> None:
    """The fix the message names, working — so the refusal is a redirection."""
    frame = reg.query(q.dataflow).select(flow=q.dataflow.id, dsd=q.dataflow.dsd.id).collect()
    assert frame.columns == ["flow", "dsd"]


def test_distinct_names_are_untouched(reg: sl.LocalRegistry) -> None:
    """The check must not fire on an ordinary projection."""
    assert reg.query(q.codelist).select(q.codelist.id, q.codelist.agency).columns == ("id", "agency")


def test_a_keyword_colliding_with_a_positional_is_refused(reg: sl.LocalRegistry) -> None:
    """The collision can be half-explicit, and that half is the confusing one:
    the caller named one of them and still got two columns alike."""
    with pytest.raises(TypeError, match="'id'"):
        reg.query(q.codelist).select(q.codelist.id, id=q.codelist.agency)


# ── alias: naming the expression rather than the call site ────────────────


def test_alias_names_a_positional_column(reg: sl.LocalRegistry) -> None:
    assert reg.query(q.codelist).select(q.codelist.id.alias("cl")).collect().columns == ["cl"]


def test_alias_resolves_a_collision_select_would_refuse(reg: sl.LocalRegistry) -> None:
    """The same fix the refusal names, spelled on the expression."""
    frame = reg.query(q.dataflow).select(q.dataflow.id, q.dataflow.dsd.id.alias("dsd")).collect()
    assert frame.columns == ["id", "dsd"]


def test_alias_reaches_inside_list_where_keywords_cannot(reg: sl.LocalRegistry) -> None:
    """Why `alias` exists at all, rather than only `select(**named)`.

    `Many.list` is positional and builds a struct from its columns' projected
    names, so two of them called `id` is a database error with no call-site
    keyword to reach them (#363).
    """
    with pytest.raises(duckdb.BinderException, match="Duplicate struct entry"):
        reg.query(q.codelist).select(
            codes=q.codelist.codes.list(q.codelist.codes.id, q.codelist.codes.parent.id)
        ).collect()

    frame = (
        reg.query(q.codelist)
        .select(codes=q.codelist.codes.list(q.codelist.codes.id, q.codelist.codes.parent.id.alias("parent")))
        .collect()
    )
    assert set(frame["codes"].to_list()[0][0]) == {"id", "parent"}


def test_alias_does_not_mutate_the_declaration(reg: sl.LocalRegistry) -> None:
    """`alias` copies. The accessor is a shared class attribute — seventeen
    relations read the same `id` declaration — so renaming in place would
    rename it for every query in the process."""
    aliased = q.codelist.id.alias("x")
    assert aliased.projected_name == "x"
    assert q.codelist.id.projected_name == "id"
    assert reg.query(q.codelist).select(q.codelist.id).collect().columns == ["id"]


# ── #342: a to-many projection refuses where it is written ────────────────


def test_a_to_many_projection_refuses_at_the_call_not_at_collect(reg: sl.LocalRegistry) -> None:
    """Both halves of "what gets refused" now refuse in the same place.

    `select(...).filter(...)` always refused at the call — `Select` has no
    `filter`, so Python catches the composition. A to-many projection returned
    a `Select` and raised only when the query ran, so a caller building one
    interactively got one refusal immediately and the other later, with no rule
    to predict which (#342).
    """
    with pytest.raises(PathError, match="crosses a to-many edge"):
        reg.query(q.code).select(q.code.annotations.title)


def test_the_eager_refusal_runs_no_query(reg: sl.LocalRegistry) -> None:
    """It is a lookup, not an execution.

    `resolve_path` reads the declared edge graph and touches no connection, so
    the check costs nothing at the database and the laziness contract is
    untouched — `select()` still runs no SQL, refusing or not.
    """
    executed: list[object] = []
    connection = reg.connection
    original = type(connection).execute

    def recording(
        self: duckdb.DuckDBPyConnection, query: duckdb.Statement | str, parameters: object = None
    ) -> duckdb.DuckDBPyConnection:
        executed.append(query)
        return original(self, query, parameters)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(type(connection), "execute", recording)
        with pytest.raises(PathError):
            reg.query(q.code).select(q.code.annotations.title)
        assert executed == [], "the refusal executed SQL"
        reg.query(q.code).select(q.code.id)
        assert executed == [], "a valid select executed SQL"


def test_an_aggregate_over_a_to_many_path_is_still_accepted(reg: sl.LocalRegistry) -> None:
    """The refusal is about projecting a fan-out, not about crossing one."""
    rows = reg.query(q.codelist).select(n=q.codelist.codes.id.count()).collect()
    assert rows.height > 0


# ── #327: ordered aggregates ──────────────────────────────────────────────


def _codes_of(reg: sl.LocalRegistry, **projection: Col[CodelistRow] | Agg[CodelistRow]) -> dict[str, Any]:
    """The one projected row, as a plain dict.

    `-> object` before, which made every `row["k"]` below an index error, and
    the `**projection: object` it went with needed a suppression to reach
    `select`. Both say what they are now (#347).
    """
    frame = reg.query(q.codelist).filter(q.codelist.id == "CL_AREA").select(**projection).collect()
    return frame.to_dicts()[0]


def test_sort_orders_an_aggregate_by_the_column_itself(reg: sl.LocalRegistry) -> None:
    row = _codes_of(reg, k=q.codelist.codes.id.sort().list())
    assert row["k"] == sorted(row["k"]), "sort() should order the collected values"


def test_sort_takes_a_direction(reg: sl.LocalRegistry) -> None:
    ascending = _codes_of(reg, k=q.codelist.codes.id.sort().list())["k"]
    descending = _codes_of(reg, k=q.codelist.codes.id.sort(descending=True).list())["k"]
    assert descending == list(reversed(ascending))


def test_sort_by_orders_by_another_column_of_the_same_fan_out(reg: sl.LocalRegistry) -> None:
    row = _codes_of(reg, k=q.codelist.codes.id.sort_by(q.codelist.codes.id, descending=True).list())
    assert row["k"] == sorted(row["k"], reverse=True)


def test_an_unsorted_aggregate_is_unchanged(reg: sl.LocalRegistry) -> None:
    """The default is deliberately not ordered: an `ORDER BY` inside every
    aggregate is a sort nobody asked for. `sort()` is opt-in."""
    plain = _codes_of(reg, k=q.codelist.codes.id.list())["k"]
    # Exact, not `> 1`: the point is that `list()` returns the codes in
    # *insertion* order untouched, and a bound would pass on a plan that
    # dropped one (#403).
    assert len(plain) == 3, f"the fixture codelist has three codes; got {plain}"


def test_unique_can_only_be_ordered_by_itself(reg: sl.LocalRegistry) -> None:
    """A DISTINCT aggregate collapses the rows another key would come from, so
    there is no one key per collected value. SQL says so; the message says
    which method to use instead."""
    with pytest.raises(PathError, match="ordered by itself"):
        reg.query(q.codelist).select(k=q.codelist.codes.id.sort_by(q.codelist.codes.position).unique()).collect()

    row = _codes_of(reg, k=q.codelist.codes.id.sort().unique())
    assert row["k"] == sorted(row["k"])


def test_an_order_is_part_of_the_query_identity(reg: sl.LocalRegistry) -> None:
    """Two aggregates differing only in order are different questions, and a
    cache keyed alike would serve one for the other — the reason a label's
    language is canonical too. An *alias* is not: renaming a column does not
    change which rows come back."""
    plain = q.codelist.codes.id
    assert plain.__canonical__() != plain.sort().__canonical__()
    assert plain.sort().__canonical__() != plain.sort(descending=True).__canonical__()
    assert plain.alias("x").__canonical__() == plain.__canonical__()
