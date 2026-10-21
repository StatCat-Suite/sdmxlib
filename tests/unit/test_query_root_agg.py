"""Several aggregates over a whole query in one scan — `Query.agg` (#414).

SDMX-REST's `/availability` needs the distinct values each dimension takes
under a filter, plus the observation count. The DSL could express that as a
loop — one `select(col).unique()` per dimension and a `count()` — but not as
one scan, and which wins depends on the filter: on the census bundle the single scan
lost 2.8x unfiltered and won 1.1-4.8x under a filter. So it is an option, and
the loop stays.

That makes the loop the oracle here: every test of what `agg` returns compares
it with the loop over the same filter, which the DSL already answered and is
tested elsewhere. A wrong `agg` would have to be wrong in the same way as two
different compile paths to pass.

The compiler change it needed: whether items compile as plain aggregates was
keyed on having group keys, so zero keys fell into the per-row projection path
— the one that correlates an aggregate over a fan-out and refused at the root
with "never fans out". It is now its own flag.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest

import sdmxlib as sl
from sdmxlib.local import LocalRegistry
from sdmxlib.local.data_store import DuckDBDataStore
from sdmxlib.model.binding import DataflowBinding
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.ref import Ref
from sdmxlib.query.accessors import q
from sdmxlib.query.query import SliceError

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sdmxlib.query._expr import Agg, Pred
    from sdmxlib.query.observations import ObsQuery, ObsRow

pytestmark = pytest.mark.unit

DIMS = ("SEX", "AGE", "TIME_PERIOD")

#: (SEX, AGE, TIME_PERIOD, GEO_LEVEL on the sidecar). One NULL SEX, so the
#: comparison also covers what `unique()` does with a missing value.
ROWS = [
    ("M", "Y15", "2020"),
    ("F", "Y15", "2020"),
    ("M", "Y65", "2021"),
    ("F", "Y65", "2021"),
    ("M", "Y15", "2022"),
    (None, "Y65", "2022"),
]


@pytest.fixture(scope="module")
def obs() -> Iterator[ObsQuery]:
    with LocalRegistry(":memory:") as registry:
        cur = registry.connection.cursor()
        cur.execute("CREATE SCHEMA data")
        cur.execute("CREATE TABLE data.obs (SEX TEXT, AGE TEXT, TIME_PERIOD TEXT, OBS_VALUE DOUBLE)")
        cur.executemany("INSERT INTO data.obs VALUES (?, ?, ?, 1.0)", [list(r) for r in ROWS])
        cur.execute("CREATE TABLE data.series (AGE TEXT, AGE_BAND TEXT)")
        cur.execute("INSERT INTO data.series VALUES ('Y15', 'young'), ('Y65', 'old')")
        binding = DataflowBinding(
            dataflow=Ref[Dataflow].unresolved(Dataflow.urn_for(agency="AG", id="DF", version="1.0")),
            database_role="memory",
            table_location="data.obs",
            series_attributes_location="data.series",
            dimension_column_map={d: d for d in DIMS},
            measure_column_map={"OBS_VALUE": "OBS_VALUE"},
            attribute_column_map={"AGE_BAND": "AGE_BAND"},
            structural_version="AG:DSD(1.0)",
            source_registry="local",
            materialized_at=datetime(2026, 1, 1, tzinfo=UTC),
            bound_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        yield DuckDBDataStore.from_registry(registry).obs(binding)


def _filters(obs: ObsQuery) -> dict[str, list[Pred[ObsRow]]]:
    return {
        "none": [],
        "one dimension": [obs.col("SEX") == "M"],
        "two dimensions": [obs.col("SEX") == "M", obs.col("AGE") == "Y15"],
        "across the sidecar": [obs.col("AGE_BAND") == "old"],
        "matches nothing": [obs.col("SEX") == "X"],
    }


@pytest.mark.parametrize("which", ["none", "one dimension", "two dimensions", "across the sidecar", "matches nothing"])
def test_one_scan_agrees_with_the_loop(obs: ObsQuery, which: str) -> None:
    filtered = obs.filter(*_filters(obs)[which])
    row = filtered.agg(**{d: obs.col(d).unique() for d in DIMS}, n=sl.len()).collect().row(0, named=True)

    for d in DIMS:
        loop = filtered.select(v=obs.col(d)).unique().collect()["v"].to_list()
        assert sorted(row[d], key=str) == sorted(loop, key=str), d
    assert row["n"] == filtered.count()


def test_the_comparison_can_disagree(obs: ObsQuery) -> None:
    """Discrimination: the filters really do narrow, so agreement means something."""
    full = obs.agg(SEX=obs.col("SEX").unique(), n=sl.len()).collect().row(0, named=True)
    narrow = obs.filter(obs.col("SEX") == "M").agg(SEX=obs.col("SEX").unique(), n=sl.len()).collect().row(0)
    assert full["n"] == len(ROWS)
    assert narrow == (["M"], 3)


def test_it_is_one_statement_with_no_group_by_and_no_order(obs: ObsQuery) -> None:
    sql = obs.filter(obs.col("SEX") == "M").agg(AGE=obs.col("AGE").unique(), n=sl.len()).to_sql().sql
    assert sql.count("SELECT") == 1
    assert "GROUP BY" not in sql
    assert "ORDER BY" not in sql
    assert "count(*)" in sql


def test_a_query_matching_nothing_still_returns_one_row(obs: ObsQuery) -> None:
    """An aggregate over no rows is one row of empty answers, not no row — as SQL and polars both have it."""
    frame = obs.filter(obs.col("SEX") == "X").agg(SEX=obs.col("SEX").unique(), n=sl.len()).collect()
    assert frame.rows() == [([], 0)]


def test_unique_keeps_a_missing_value_as_unique_does_elsewhere(obs: ObsQuery) -> None:
    values = obs.agg(SEX=obs.col("SEX").unique()).collect()["SEX"][0].to_list()
    assert sorted(values, key=str) == sorted(["F", "M", None], key=str)


def test_a_sorted_unique_comes_back_sorted(obs: ObsQuery) -> None:
    """Unordered by default, as polars' `unique()` is; `.sort()` first asks for an order."""
    values = obs.agg(T=obs.col("TIME_PERIOD").sort().unique()).collect()["T"][0].to_list()
    assert values == ["2020", "2021", "2022"]


def test_the_sidecar_is_joined_only_when_an_aggregate_reads_it(obs: ObsQuery) -> None:
    """#388's join elision holds here too: the join is the expensive part of the scan."""
    assert "JOIN" not in obs.agg(SEX=obs.col("SEX").unique(), n=sl.len()).to_sql().sql
    assert "LEFT JOIN" in obs.agg(band=obs.col("AGE_BAND").unique()).to_sql().sql


def test_it_works_on_a_structural_root_too() -> None:
    cls = [
        sl.Codelist(
            id=f"CL{i}", maintainer=agency, version="1.0", name={"en": "x"}, codes=[sl.Code(id="A", name={"en": "a"})]
        )
        for i, agency in enumerate(["AG", "AG", "BB"])
    ]
    with sl.LocalRegistry(":memory:") as reg:
        reg.add_all(cls)
        row = reg.query(q.codelist).agg(agencies=q.codelist.agency.unique(), n=sl.len()).collect().row(0)
    assert sorted(row[0]) == ["AG", "BB"]
    assert row[1] == 3


# ── refusals ─────────────────────────────────────────────────────────────────


def test_no_aggregates_is_refused(obs: ObsQuery) -> None:
    with pytest.raises(TypeError, match=r"needs at least one aggregate.*\.count\(\)"):
        obs.agg()


def test_a_plain_column_is_refused_naming_the_alternatives(obs: ObsQuery) -> None:
    """The signature already refuses this statically; the runtime check is for an
    untyped caller, which the `cast` stands in for — it is the lie being tested."""
    column = cast("Agg[ObsRow]", obs.col("SEX"))
    with pytest.raises(TypeError, match=r"'SEX' is a column, not an aggregate.*select\(\).*group_by\(\)"):
        obs.agg(SEX=column)


def test_an_ordered_query_is_refused(obs: ObsQuery) -> None:
    with pytest.raises(SliceError, match="ordered or sliced"):
        obs.order_by(obs.col("SEX")).agg(n=sl.len())


def test_a_sliced_query_is_refused(obs: ObsQuery) -> None:
    with pytest.raises(SliceError, match="ordered or sliced"):
        obs.limit(2).agg(n=sl.len())


def test_having_is_refused_on_a_root_aggregate(obs: ObsQuery) -> None:
    with pytest.raises(TypeError, match=r"having\(\) needs a group_by\(\)"):
        obs.agg(n=sl.len()).having(obs.col("SEX").count() > 2)


def test_group_by_with_no_keys_points_at_agg(obs: ObsQuery) -> None:
    with pytest.raises(TypeError, match=r"needs at least one key.*\.agg\("):
        obs.group_by()
