"""The path language, rooted at a dataflow's observations (#297 step 4).

Everything the compiler served until now was structural and declared: `q.code`
names `sdmx.code`, whose columns are known at import. A dataflow's table comes
from a binding and its columns from a DSD, so this is the first root that is
built rather than declared — and the first that lives outside the `sdmx`
schema.

`docs/design/data-plane-query.md` is why it is the *same* language: reading
both consumers' hand-written SQL found that most of what they build is already
compiled here. These tests are that claim, checked one shape at a time against
the SQL those consumers write today.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import polars as pl
import pytest

from sdmxlib.local.data_store import DuckDBDataStore
from sdmxlib.local.registry import LocalRegistry
from sdmxlib.model.binding import DataflowBinding
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.concept import Concept
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure, Dimension, Measure
from sdmxlib.model.ref import Ref
from sdmxlib.query._compile import _Namespace, _Scope
from sdmxlib.query._joins import PathError
from sdmxlib.query.observations import Observations, Sidecar
from sdmxlib.rest import ComponentFilter
from tests.support.factories import concept_ref

if TYPE_CHECKING:
    from sdmxlib.sql import SqlRelation

pytestmark = pytest.mark.unit


def _binding(
    *,
    table_location: str = "data.obs",
    series_attributes_location: str | None = None,
    dimension_column_map: dict[str, str] | None = None,
    measure_column_map: dict[str, str] | None = None,
    attribute_column_map: dict[str, str] | None = None,
    database_role: str = "memory",
) -> DataflowBinding:
    """A binding whose SDMX ids differ from its physical columns.

    `REF_AREA -> GEO` on purpose: a root that ignored the map and used the id
    as the column name would still pass every test where the two agree, which
    is most real bindings and none of these.
    """
    return DataflowBinding(
        dataflow=Ref[Dataflow].unresolved(Dataflow.urn_for(agency="AG", id="DF", version="1.0")),
        database_role=database_role,
        table_location=table_location,
        dimension_column_map=(
            {"REF_AREA": "GEO", "TIME_PERIOD": "TIME_PERIOD"} if dimension_column_map is None else dimension_column_map
        ),
        measure_column_map={"OBS_VALUE": "OBS_VALUE"} if measure_column_map is None else measure_column_map,
        attribute_column_map={"OBS_STATUS": "STATUS"} if attribute_column_map is None else attribute_column_map,
        structural_version="AG:DSD(1.0)",
        source_registry="local",
        materialized_at=datetime(2026, 1, 1, tzinfo=UTC),
        bound_at=datetime(2026, 1, 1, tzinfo=UTC),
        series_attributes_location=series_attributes_location,
    )


@pytest.fixture
def plane():
    """A registry, a small observation table, a store and its binding."""
    frame = pl.DataFrame(
        {
            "GEO": ["CA", "CA", "ON", "QC"],
            "TIME_PERIOD": ["2020", "2021", "2020", "2021"],
            "OBS_VALUE": [1.0, 2.0, 3.0, 4.0],
            "STATUS": ["A", "A", None, "P"],
        }
    )
    with LocalRegistry(":memory:") as registry:
        conn = registry.connection
        conn.register("obs_frame", frame)
        conn.execute("CREATE SCHEMA IF NOT EXISTS data")
        conn.execute("CREATE OR REPLACE TABLE data.obs AS SELECT * FROM obs_frame")
        binding = _binding()
        yield DuckDBDataStore.from_registry(registry), binding, Observations(binding)


# ── the root's identity ───────────────────────────────────────────────────


def test_the_root_names_the_binding_s_table() -> None:
    """Two-part already, so it declares no schema of its own.

    The compiler qualifies a structural root with `sdmx`; this one would
    become `sdmx.memory.data.obs`, which parses and names nothing.
    """
    obs = Observations(_binding())
    assert obs.__table__ == "memory.data.obs"
    assert obs.__schema__ is None


def test_the_key_is_the_dimensions_in_order() -> None:
    """The information model identifies an observation by its dimension values.

    So paging and the `ORDER BY` tiebreak work here for the same reason they
    work over a structural relation, rather than by special case — and the
    columns are the *physical* ones, since that is what the statement uses.
    """
    assert Observations(_binding()).__key__ == ("GEO", "TIME_PERIOD")


def test_a_column_is_named_by_component_id_not_by_physical_column() -> None:
    """`REF_AREA` is what a DSD talks about; `GEO` is where it is stored."""
    obs = Observations(_binding())
    assert "GEO" in repr(obs["REF_AREA"]), "the physical column reaches the SQL"
    assert obs.component_ids == ("REF_AREA", "TIME_PERIOD", "OBS_VALUE", "OBS_STATUS")


def test_an_unknown_component_lists_the_real_ones() -> None:
    """The check a downstream SDMX REST service hand-writes as an HTTP 400, moved to where the
    map already lives."""
    obs = Observations(_binding())
    with pytest.raises(KeyError, match="REF_AREA"):
        obs["REF_ARIA"]


# ── the language over it ──────────────────────────────────────────────────


def test_counting_and_filtering(plane) -> None:
    store, binding, obs = plane
    query = store.obs(binding)
    assert query.count() == 4
    assert query.filter(obs["REF_AREA"] == "CA").count() == 2


def test_the_statement_reads_the_bound_table_and_columns(plane) -> None:
    """SDMX ids in, physical SQL out, in one assertion."""
    store, binding, obs = plane
    compiled = store.obs(binding).filter(obs["REF_AREA"] == "CA").to_sql()
    assert "memory.data.obs" in compiled.sql
    assert "GEO = ?" in compiled.sql, "the id was mapped to its column"
    assert compiled.params == ["CA"], "the value is bound, never spliced"


def test_projection_names_columns_by_component(plane) -> None:
    store, binding, obs = plane
    frame = (
        store.obs(binding)
        .filter(obs["REF_AREA"] == "CA")
        .select(area=obs["REF_AREA"], value=obs["OBS_VALUE"])
        .collect()
    )
    assert frame.columns == ["area", "value"]
    assert frame["value"].to_list() == [1.0, 2.0]


def test_group_by_a_dimension_and_aggregate_the_measure(plane) -> None:
    """The shape both consumers write as `f"{agg}(OBS_VALUE)"` with a GROUP BY.

    The key comes back as `REF_AREA`, its **component id**, though the binding
    stores it as `GEO`. Grouping was the last terminal still naming outputs by
    the producer's physical column, which made it the odd one out beside
    `select()` and an unprojected `frame()`.
    """
    store, binding, obs = plane
    frame = store.obs(binding).group_by(obs["REF_AREA"]).agg(total=obs["OBS_VALUE"].sum()).collect()
    assert dict(zip(frame["REF_AREA"].to_list(), frame["total"].to_list(), strict=True)) == {
        "CA": 3.0,
        "ON": 3.0,
        "QC": 4.0,
    }


def test_per_group_top_n_is_qualify_and_a_window(plane) -> None:
    """An LLM agent built on sdmxlib wrote this as a CTE with `ROW_NUMBER() OVER (…)` and
    `WHERE _rn <= ?`. It is one clause here, over the same compiler the
    structural side uses."""
    store, binding, obs = plane
    best = obs["OBS_VALUE"].rank(descending=True).over(obs["REF_AREA"]) == 1
    frame = store.obs(binding).qualify(best).select(area=obs["REF_AREA"], v=obs["OBS_VALUE"]).collect()
    assert dict(zip(frame["area"].to_list(), frame["v"].to_list(), strict=True)) == {
        "CA": 2.0,
        "ON": 3.0,
        "QC": 4.0,
    }


def test_paging_is_ordered_by_the_dimension_key(plane) -> None:
    """`limit` is only meaningful over a total order, and §6.1's key supplies
    one — so a page boundary does not move between calls."""
    store, binding, obs = plane
    query = store.obs(binding).select(a=obs["REF_AREA"], t=obs["TIME_PERIOD"])
    assert query.limit(2).collect().rows() == [("CA", "2020"), ("CA", "2021")]


def test_two_dataflows_are_two_roots_of_one_class(plane) -> None:
    """A root's identity comes from its instance (!338), which is what lets a
    per-binding root exist at all."""
    _store, binding, _obs = plane
    other = Observations(_binding(table_location="data.other"))
    assert Observations(binding).__table__ != other.__table__


# ── the filters a data query is actually made of (#328) ───────────────────


def test_a_range_filter_compiles(plane) -> None:
    """The half-open period range — %15's shape, and what both consumers write.

    It did not compile when the root landed: ordering lived on `Num`, so a
    column whose type nothing declares could not offer it. `Untyped` does,
    without widening `Col` and so without giving `Version` a text `>=` that
    would make `version >= "1.10"` true for `1.9` (#311).
    """
    store, binding, obs = plane
    assert store.obs(binding).filter(obs["TIME_PERIOD"] >= "2021").count() == 2
    assert store.obs(binding).filter(obs["TIME_PERIOD"] < "2021").count() == 2


def test_set_membership_compiles(plane) -> None:
    """`is_in` — the most common data-plane filter, and what `ComponentFilter`
    exists to express."""
    store, binding, obs = plane
    assert store.obs(binding).filter(obs["REF_AREA"].is_in(["ON", "QC"])).count() == 2


def test_text_matching_is_asked_for_by_name(plane) -> None:
    """`.str` on an untyped column is the caller stating the reading.

    That is the difference from an inherited operator: `contains` is asked for,
    where `+` would have been picked silently between addition and
    concatenation.
    """
    store, binding, obs = plane
    assert store.obs(binding).filter(obs["REF_AREA"].str.contains("O")).count() == 1


def test_a_share_of_the_total(plane) -> None:
    """`value / value.sum().over()` — percent-of-total, %15's other shape.

    Two fixes met here: arithmetic on an untyped column, and the pre-existing
    bug that rendered a windowed operand as its own source column, so every
    share was `1`.
    """
    store, binding, obs = plane
    frame = (
        store.obs(binding).select(v=obs["OBS_VALUE"], share=obs["OBS_VALUE"] / obs["OBS_VALUE"].sum().over()).collect()
    )
    total = sum(frame["v"].to_list())
    assert frame["share"].to_list() == pytest.approx([value / total for value in frame["v"].to_list()])


def test_addition_is_absent_because_it_would_have_to_guess(plane) -> None:
    """`+` means addition on `Num` and concatenation on `Str`.

    With no declared type there is nothing to pick by, and inheriting `Str`'s
    would compile `a + b` to `||` — silently concatenating two measures into
    `"12"`. Absent, it is a plain `TypeError` at the call.
    """
    _store, _binding, obs = plane
    with pytest.raises(TypeError, match="unsupported operand"):
        _ = obs["OBS_VALUE"] + obs["OBS_VALUE"]


# ── the SDMX REST key, structurally (#329) ────────────────────────────────


def _filter(*, attributes: dict[str, SqlRelation | list[str]] | None = None, **by: list[str]) -> ComponentFilter:
    """A `ComponentFilter` over this fixture's two dimensions."""
    return ComponentFilter(
        dimension_ids=["REF_AREA", "TIME_PERIOD"],
        dimensions=[by.get("REF_AREA"), by.get("TIME_PERIOD")],
        attributes={} if attributes is None else attributes,
    )


def test_a_component_filter_narrows_the_query(plane) -> None:
    """The one *typed* thing the SQL-text API took, so it has to survive it.

    A dotted key plus `c[DIM]=…` parameters parse into a `ComponentFilter`;
    each constrained component becomes `is_in`, which is what its own SQL
    rendering did.
    """
    store, binding, _obs = plane
    assert store.obs(binding).filter_components(_filter(REF_AREA=["CA", "ON"])).count() == 3
    assert store.obs(binding).filter_components(_filter(REF_AREA=["CA"], TIME_PERIOD=["2021"])).count() == 1


def test_an_unconstrained_component_contributes_nothing(plane) -> None:
    """`None` is "no constraint", and a wildcard filter must not narrow."""
    store, binding, _obs = plane
    assert store.obs(binding).filter_components(_filter()).count() == 4


def test_an_empty_permitted_set_matches_no_row(plane) -> None:
    """`[]` is "the permitted set is empty", not "unconstrained" (#256).

    The distinction is the whole reason `ComponentFilter` keeps `None` and `[]`
    apart: a dynamically-computed code list that came back empty must return
    nothing rather than the entire dataset. `is_in([])` compiles to `1 = 0`,
    which is polars' and SQLAlchemy's reading too.
    """
    store, binding, _obs = plane
    query = store.obs(binding).filter_components(_filter(REF_AREA=[]))
    assert query.count() == 0
    assert "1 = 0" in query.to_sql().sql


def test_an_attribute_constraint_applies_too(plane) -> None:
    """Attributes are a flat map in a `ComponentFilter`, and AND across."""
    store, binding, _obs = plane
    assert store.obs(binding).filter_components(_filter(attributes={"OBS_STATUS": ["A"]})).count() == 2


def test_a_filter_naming_a_foreign_component_says_so(plane) -> None:
    """A filter is parsed against a DSD and applied against a binding.

    A mismatch between the two surfaces here, naming the components this
    dataflow actually has, rather than as a missing column at the database.
    """
    store, binding, _obs = plane
    stray = ComponentFilter(dimension_ids=["NOT_A_DIMENSION"], dimensions=[["x"]])
    with pytest.raises(KeyError, match="REF_AREA"):
        store.obs(binding).filter_components(stray)


# ── streaming (#329) ──────────────────────────────────────────────────────


def test_a_stream_yields_batches_that_add_up(plane) -> None:
    """The typed counterpart of `query_stream`, so that API can go.

    Arrow batches rather than polars frames because that is what a writer
    wants — CSV, parquet, an HTTP body — and `pl.DataFrame(batch)` is a cheap
    step for a caller who wants the other thing.
    """
    store, binding, obs = plane
    query = store.obs(binding).select(area=obs["REF_AREA"], value=obs["OBS_VALUE"])
    with query.stream(chunk_rows=3) as batches:
        sizes = [batch.num_rows for batch in batches]
    assert sizes == [3, 1], "chunked, with a short final batch"
    assert sum(sizes) == query.collect().height, "the same rows either way"


def test_a_stream_iterates_without_a_context_manager(plane) -> None:
    """`pa.RecordBatchReader` is an iterator *and* a context manager, so both
    spellings work and neither was chosen here."""
    store, binding, obs = plane
    query = store.obs(binding).select(v=obs["OBS_VALUE"])
    assert sum(batch.num_rows for batch in query.stream(2)) == 4


def test_a_stream_carries_the_projection_and_the_filter(plane) -> None:
    """A stream is a terminal on the same query, not a second way to build one."""
    store, binding, obs = plane
    query = store.obs(binding).filter(obs["REF_AREA"] == "CA").select(area=obs["REF_AREA"], value=obs["OBS_VALUE"])
    with query.stream() as batches:
        collected = list(batches)
    assert collected, "one batch is enough for four rows"
    assert collected[0].schema.names == ["area", "value"]
    assert sum(batch.num_rows for batch in collected) == 2


def test_an_unprojected_stream_gives_every_column(plane) -> None:
    """`Query.stream` is to `frame` what `Select.stream` is to `collect`.

    Named by **component id**, not by the binding's physical columns — this
    binding maps `REF_AREA` to `GEO` and `OBS_STATUS` to `STATUS`, and neither
    physical name appears. Every filter, projection and label in this language
    names a component, so a result that did not would be the one place the
    producer's column names leak into a caller.
    """
    store, binding, _obs = plane
    with store.obs(binding).stream() as batches:
        first = next(iter(batches))
    assert set(first.schema.names) == {"REF_AREA", "TIME_PERIOD", "OBS_VALUE", "OBS_STATUS"}


def test_two_streams_do_not_share_a_row_buffer(plane) -> None:
    """Each stream takes its own cursor.

    Two concurrent readers on one connection would overwrite each other's
    default-cursor buffer (#75) — interleaving them here is the cheap check
    that they do not.
    """
    store, binding, obs = plane
    first = store.obs(binding).select(v=obs["OBS_VALUE"]).stream(1)
    second = store.obs(binding).select(v=obs["REF_AREA"]).stream(1)
    with first, second:
        interleaved = [next(iter(first)).num_rows, next(iter(second)).num_rows]
    assert interleaved == [1, 1]


def test_a_stream_does_not_sort_unless_asked(plane) -> None:
    """The whole of `stream`'s memory story, in one assertion.

    Sorting materialises the result before the first batch can leave, so an
    ordered stream is unbounded. Measured on 4M rows of three columns: 441 MB
    peak RSS sorted, 5 MB unsorted, and the unsorted figure does not move at
    8M. `chunk_rows` is almost irrelevant beside it — 1,000 and 50,000 both
    gave 210 MB while the sort was still there.

    The tiebreak this drops exists so `limit`/`offset` paging is stable, and a
    stream reads everything once.
    """
    store, binding, obs = plane
    query = store.obs(binding).select(v=obs["OBS_VALUE"])
    assert "ORDER BY" in query.to_sql().sql, "collect() still gets a total order"
    assert "ORDER BY" not in query.to_sql(ordered=False).sql


def test_an_asked_for_order_survives_a_stream(plane) -> None:
    """Dropping the *default* order is not dropping the caller's."""
    store, binding, obs = plane
    query = store.obs(binding).order_by(obs["OBS_VALUE"]).select(v=obs["OBS_VALUE"])
    with query.stream(2) as batches:
        values = [value for batch in batches for value in batch.column("v").to_pylist()]
    assert values == sorted(values), "and in the order asked for"


def test_a_sliced_stream_keeps_its_order(plane) -> None:
    """A `limit` needs the order for *correctness*, not stability: which rows
    come back depends on it."""
    store, binding, obs = plane
    query = store.obs(binding).limit(2).select(a=obs["REF_AREA"])
    with query.stream() as batches:
        rows = [value for batch in batches for value in batch.column("a").to_pylist()]
    assert rows == ["CA", "CA"], "the first two by the key, deterministically"


def test_a_stream_returns_the_same_rows_either_way(plane) -> None:
    """Unordered is not "different rows" — it is the same set, unsorted."""
    store, binding, obs = plane
    query = store.obs(binding).select(a=obs["REF_AREA"], v=obs["OBS_VALUE"])
    materialised = {tuple(row) for row in query.collect().rows()}
    with query.stream(2) as batches:
        streamed = {
            row
            for batch in batches
            for row in zip(batch.column("a").to_pylist(), batch.column("v").to_pylist(), strict=True)
        }
    assert streamed == materialised


# ── an ordered stream at bounded memory (#331) ────────────────────────────


@pytest.fixture
def partitioned(tmp_path):
    """A DuckLake table partitioned on REF_AREA's column, with rows deliberately out of order."""
    frame = pl.DataFrame(
        {
            "GEO": ["QC", "CA", "ON", "CA", "ON"],
            "TIME_PERIOD": ["2021", "2021", "2020", "2020", "2021"],
            "OBS_VALUE": [5.0, 1.0, 3.0, 2.0, 4.0],
            "STATUS": [None, "A", None, "A", None],
        }
    )
    with LocalRegistry(":memory:") as registry:
        store = DuckDBDataStore.from_registry(registry)
        store.connection.execute(f"ATTACH 'ducklake:{tmp_path / 'lake.ducklake'}' AS lake (DATA_PATH '{tmp_path}/')")
        store.write_observations(schema="data", table="obs", source=frame, partition_by=("GEO",))
        binding = _binding(database_role="lake")
        yield store, binding, Observations(binding, partitions=("GEO",))


def _streamed(query, chunk_rows: int = 2) -> list[tuple[object, ...]]:
    """Every row a stream yields, as tuples in the order they arrived."""
    rows: list[tuple[object, ...]] = []
    with query.stream(chunk_rows) as batches:
        for batch in batches:
            columns = [batch.column(name).to_pylist() for name in batch.schema.names]
            rows.extend(zip(*columns, strict=True))
    return rows


def test_an_ordered_stream_over_partitions_matches_collect(partitioned) -> None:
    """The prize: ordered *and* bounded, which a global sort cannot be.

    Partitions are disjoint on the columns the order starts with, so visiting
    them in key order and sorting inside each yields exactly the rows one
    global sort would — while each sort covers one partition. Measured through
    this API on 4M rows: 323 MB unpartitioned against 37.6 MB over 20
    partitions and 18.2 MB over 200.
    """
    store, binding, obs = partitioned
    query = (
        store.obs(binding).order_by(obs["REF_AREA"], obs["TIME_PERIOD"]).select(g=obs["REF_AREA"], t=obs["TIME_PERIOD"])
    )
    assert _streamed(query) == [tuple(row) for row in query.collect().rows()]
    assert _streamed(query) == sorted(_streamed(query)), "and globally ordered"


def test_an_order_the_partitions_cannot_serve_falls_back(partitioned) -> None:
    """`ORDER BY TIME_PERIOD` interleaves partitions, so no per-partition plan
    reproduces it — the query sorts globally, and is still correct.

    The fallback matters more than the optimisation: a plan that silently
    returned partition-ordered rows for this would be wrong.
    """
    store, binding, obs = partitioned
    query = store.obs(binding).order_by(obs["TIME_PERIOD"]).select(t=obs["TIME_PERIOD"])
    streamed = _streamed(query)
    assert streamed == [tuple(row) for row in query.collect().rows()]
    values = [str(value) for (value,) in streamed]
    assert values == sorted(values)


def test_the_store_reads_the_partitions_from_the_lake(partitioned) -> None:
    """DuckLake's catalog says how the table is partitioned; the binding says nothing."""
    store, binding, _ = partitioned
    assert store.inspect().partition_columns("lake", "data.obs") == ("GEO",)
    assert store.obs(binding).plan.root.__partitions__ == ("GEO",)


def test_an_unpartitioned_table_takes_the_global_sort(plane) -> None:
    """Nothing in a catalog, nothing planned — and the answer is unchanged."""
    store, binding, obs = plane
    assert Observations(binding).__partitions__ == ()
    query = store.obs(binding).order_by(obs["REF_AREA"]).select(g=obs["REF_AREA"])
    assert _streamed(query) == [tuple(row) for row in query.collect().rows()]


def test_a_partition_column_the_binding_does_not_map_is_dropped() -> None:
    """A partition column no component reads plans nothing.

    The per-partition plan visits partitions of the columns an `ORDER BY`
    starts with, and an order names components; a column the binding leaves
    out can never lead one.
    """
    binding = _binding()
    assert Observations(binding, partitions=("GEO", "GEO_LEVEL")).__partitions__ == ("GEO",)


def test_an_empty_table_still_streams(partitioned) -> None:
    """No partition values, so there is nothing to plan and no schema to give
    a chained reader — it falls back rather than returning an empty stream
    with no columns."""
    store, binding, obs = partitioned
    query = store.obs(binding).filter(obs["REF_AREA"] == "NOWHERE").order_by(obs["REF_AREA"]).select(g=obs["REF_AREA"])
    assert _streamed(query) == []


# ── projections carry the caller's SDMX name, which is what labels need ───


def test_a_projected_column_takes_its_component_id(plane) -> None:
    """`obs["REF_AREA"]` projects as `REF_AREA`, not as the binding's `GEO`.

    A structural column projects as the physical column it reads, so a caller
    replacing a hand-written `SELECT code_id …` needs no renaming. The same
    rule points the other way here: the caller wrote a *component id*, and the
    physical name is an implementation detail of the binding. Returning `GEO`
    leaks the layout.

    It is also what makes labelling possible at all — `sdmxlib.polars.label`
    matches components by id, so a frame of physical names matches nothing.
    """
    store, binding, obs = plane
    frame = store.obs(binding).select(obs["REF_AREA"], obs["OBS_VALUE"]).collect()
    assert frame.columns == ["REF_AREA", "OBS_VALUE"]


def test_an_explicit_alias_still_wins(plane) -> None:
    """Naming a column is the caller's to override, as everywhere else."""
    store, binding, obs = plane
    frame = store.obs(binding).select(area=obs["REF_AREA"]).collect()
    assert frame.columns == ["area"]


def test_a_projected_frame_can_be_labelled(plane) -> None:
    """Both changes, end to end.

    Before: the frame came back with `GEO`, `label()` matched no component and
    silently returned it unchanged, and `catalog.query(with_labels=True)` was a
    no-op for any binding whose columns are not already SDMX ids.
    """
    import sdmxlib.polars as slpl

    store, binding, obs = plane
    frame = store.obs(binding).select(obs["REF_AREA"], obs["OBS_VALUE"]).collect()
    labelled = slpl.label(frame.lazy(), _dsd(), lang="en").collect()
    assert "REF_AREA_label_en" in labelled.columns
    assert set(labelled["REF_AREA_label_en"].to_list()) == {"Canada", "Ontario", "Quebec"}


def _geo_codelist():
    """The codelist the DSD's REF_AREA is enumerated by.

    Registered separately in the fixture, and that is not incidental: a DSD
    whose `representation: Ref[Codelist]` cannot be resolved is *skipped* by
    `label`, so forgetting it produces an unlabelled frame rather than an
    error — the exact silent failure this work is about.
    """
    from sdmxlib.model.codelist import Code, Codelist
    from sdmxlib.model.organisation import Agency

    return Codelist(
        id="CL_GEO",
        maintainer=Agency(id="AG"),
        name={"en": "geo"},
        codes=[
            Code(id="CA", name={"en": "Canada", "fr": "Canada"}),
            Code(id="ON", name={"en": "Ontario", "fr": "Ontario"}),
            Code(id="QC", name={"en": "Quebec", "fr": "Québec"}),
        ],
    )


def _dsd():
    """A DSD matching the `plane` fixture's binding, with labelled areas."""
    from sdmxlib.model.datastructure import DataStructure, Dimension, Measure
    from sdmxlib.model.organisation import Agency

    agency = Agency(id="AG")
    codelist = _geo_codelist()
    return DataStructure(
        id="DSD",
        maintainer=agency,
        name={"en": "d"},
        dimensions=[
            Dimension(id="REF_AREA", concept=concept_ref("REF_AREA"), representation=Ref.to(codelist)),
            Dimension(id="TIME_PERIOD", concept=concept_ref("TIME_PERIOD")),
        ],
        measures=[Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )


def test_the_catalog_resolves_a_dataflow_s_dsd(plane) -> None:
    """`catalog.dsd(flow_urn)` — the DSD labelling needs, without hand-built URNs.

    The catalog already resolved this internally for `query`'s labelling stage
    and gave a caller no way to ask, so obtaining one meant building a URN by
    hand or resolving the dataflow and unwrapping `.structure()`.

    It resolves *by URN* rather than returning the reference's target, because
    `label` skips any component whose codelist is unresolved — a DSD fetched
    the shorter way labels nothing.
    """
    from sdmxlib.model.datastructure import DataStructure

    store, binding, _obs = plane
    registry = store.registry
    assert registry is not None
    dsd = _dsd()
    registry.add(_geo_codelist())
    registry.add(dsd)
    registry.add(Dataflow(id="DF", maintainer="AG", version="1.0", name={"en": "flow"}, structure=Ref.to(dsd)))
    registry.add(binding)
    catalog = registry

    resolved = catalog.dsd(binding.dataflow.urn)
    assert isinstance(resolved, DataStructure)
    assert resolved.id == "DSD"
    assert resolved.dimensions["REF_AREA"].is_enumerated, "and its codelists are resolved, or labelling is a no-op"


def test_the_catalog_returns_no_dsd_for_an_unbound_dataflow(plane) -> None:
    """Best-effort, like `query`'s own resolution: a catalog federating a
    remote registry cannot promise the artefact is reachable."""

    store, _binding, _obs = plane
    registry = store.registry
    assert registry is not None
    catalog = registry
    assert catalog.dsd(Dataflow.urn_for(agency="AG", id="NOPE", version="1.0")) is None


# ── labels, as an argument on the terminal (#329) ─────────────────────────


@pytest.fixture
def labelled_plane(plane):
    """The `plane` store, plus the structural artefacts a label needs."""

    store, binding, obs = plane
    registry = store.registry
    assert registry is not None
    dsd = _dsd()
    registry.add(_geo_codelist())
    registry.add(dsd)
    registry.add(Dataflow(id="DF", maintainer="AG", version="1.0", name={"en": "flow"}, structure=Ref.to(dsd)))
    registry.add(binding)
    catalog = registry
    return catalog, binding.dataflow.urn, obs


def test_labels_attach_on_collect(labelled_plane) -> None:
    """An argument on the terminal, not a clause.

    Every other clause compiles to SQL; labelling runs over the materialised
    frame. Putting it in the signature of the call that materialises says so,
    where a `.labelled()` refinement would have been the first link in the
    chain that does not compile.
    """
    catalog, flow_urn, obs = labelled_plane
    frame = catalog.obs(flow_urn).select(obs["REF_AREA"], obs["OBS_VALUE"]).collect(labels="en")
    assert frame.columns == ["REF_AREA", "OBS_VALUE", "REF_AREA_label_en"]
    assert dict(zip(frame["REF_AREA"].to_list(), frame["REF_AREA_label_en"].to_list(), strict=True)) == {
        "CA": "Canada",
        "ON": "Ontario",
        "QC": "Quebec",
    }


def test_several_languages_in_one_pass(labelled_plane) -> None:
    """One call, one column each — and no repeated `.labelled()` chain."""
    catalog, flow_urn, obs = labelled_plane
    frame = catalog.obs(flow_urn).select(obs["REF_AREA"]).collect(labels=["en", "fr"])
    assert frame.columns == ["REF_AREA", "REF_AREA_label_en", "REF_AREA_label_fr"]


def test_the_language_is_always_in_the_name(labelled_plane) -> None:
    """So adding one does not rename what is already there.

    `labels="en"` giving `REF_AREA_label`, then `labels=["en", "fr"]` giving
    `REF_AREA_label_en`, would break every reader of the first column on a
    change that only adds.
    """
    catalog, flow_urn, obs = labelled_plane
    query = catalog.obs(flow_urn).select(obs["REF_AREA"])
    assert "REF_AREA_label_en" in query.collect(labels="en").columns
    assert "REF_AREA_label" not in query.collect(labels="en").columns
    assert query.collect(labels="en").columns == [
        c for c in query.collect(labels=["en", "fr"]).columns if c[-3:] != "_fr"
    ]


def test_the_suffix_is_the_callers(labelled_plane) -> None:
    catalog, flow_urn, obs = labelled_plane
    frame = catalog.obs(flow_urn).select(obs["REF_AREA"]).collect(labels="en", label_suffix="_name")
    assert frame.columns == ["REF_AREA", "REF_AREA_name_en"]


def test_an_aliased_column_is_labelled_under_its_alias(labelled_plane) -> None:
    """The name in the frame is the name a caller reads, so it is the one the
    label hangs off — `area` gives `area_label_en`, not `REF_AREA_label_en`."""
    catalog, flow_urn, obs = labelled_plane
    frame = catalog.obs(flow_urn).select(area=obs["REF_AREA"]).collect(labels="en")
    assert frame.columns == ["area", "area_label_en"]


def test_labels_need_a_registry_built_query(plane) -> None:
    """A store cannot resolve a DSD, so a store-built query says so, naming both fixes.

    Better than silently returning unlabelled rows, which is precisely the
    failure this whole change came from.
    """
    store, binding, obs = plane
    with pytest.raises(TypeError, match=r"reg\.obs\(flow\).*dsd=dsd"):
        store.obs(binding).select(obs["REF_AREA"]).collect(labels="en")


def test_labelling_nothing_is_refused(labelled_plane) -> None:
    """A projection of no enumerated column produces no label, and saying so
    is the same rule `label` itself now follows."""
    catalog, flow_urn, obs = labelled_plane
    with pytest.raises(ValueError, match="nothing to label"):
        catalog.obs(flow_urn).select(obs["TIME_PERIOD"]).collect(labels="en")


def test_no_labels_asked_for_changes_nothing(labelled_plane) -> None:
    catalog, flow_urn, obs = labelled_plane
    frame = catalog.obs(flow_urn).select(obs["REF_AREA"]).collect()
    assert frame.columns == ["REF_AREA"]


# ── a binding whose series attributes live in a sidecar (#332) ────────────


@pytest.fixture
def with_sidecar():
    """A fact table and a series-attributes sidecar sharing the series key.

    `GEO_NAME` is stored once per series and reaches every observation of it —
    which is the whole reason a producer splits the tables.
    """
    with LocalRegistry(":memory:") as registry:
        conn = registry.connection.cursor()
        conn.execute("CREATE SCHEMA IF NOT EXISTS data")
        conn.execute("""
            CREATE OR REPLACE TABLE data.obs AS
            SELECT 'A' AS FREQ, area AS GEO, period AS TIME_PERIOD, 1.5 AS OBS_VALUE
            FROM (SELECT unnest(['CA', 'US', 'MX']) AS area), (SELECT unnest(['2019', '2020']) AS period)
        """)
        conn.execute("""
            CREATE OR REPLACE TABLE data.series AS
            SELECT 'A' AS FREQ, area AS GEO, name AS GEO_NAME
            FROM (SELECT unnest(['CA', 'US', 'MX']) AS area,
                         unnest(['Canada', 'United States', 'Mexico']) AS name)
        """)
        binding = _binding(
            table_location="data.obs",
            series_attributes_location="data.series",
            dimension_column_map={"FREQ": "FREQ", "REF_AREA": "GEO", "TIME_PERIOD": "TIME_PERIOD"},
            attribute_column_map={"GEO_NAME": "GEO_NAME"},
        )
        store = DuckDBDataStore.from_registry(registry)
        # The *accessor*, not the query: `obs[...]` on a query is `Query`'s
        # look-up-by-id, which an observation root refuses. `ObsQuery.col()` is
        # the spelling there — see `test_columns_are_col_not_getitem`.
        yield store, binding, store.obs(binding).plan.root


def test_a_sidecar_binding_is_no_longer_refused(with_sidecar) -> None:
    """It used to raise at construction, because a query silently missing its
    attribute columns is worse than one that says so."""
    store, binding, _query = with_sidecar
    root = store.obs(binding).plan.root
    assert root.sidecar is not None
    assert root.sidecar.join_keys == ("FREQ", "GEO"), "the series key, derived from the two tables"


def test_a_series_attribute_reaches_every_observation_of_its_series(with_sidecar) -> None:
    """One stored value, two rows — the point of the split."""
    store, binding, obs = with_sidecar
    frame = (
        store.obs(binding)
        .filter(obs["REF_AREA"] == "CA")
        .select(area=obs["REF_AREA"], period=obs["TIME_PERIOD"], name=obs["GEO_NAME"])
        .collect()
    )
    assert frame["name"].to_list() == ["Canada", "Canada"]
    assert sorted(frame["period"].to_list()) == ["2019", "2020"]


def test_the_sidecar_column_reads_through_the_join_s_alias(with_sidecar) -> None:
    """`USING` merges only the join *keys*.

    So a non-key sidecar column read through the root alias — `s0.GEO_NAME` —
    is a binder error rather than a wrong answer. That is why a column carries
    the table it lives on, and why the scope aliases by table.
    """
    store, binding, obs = with_sidecar
    sql = store.obs(binding).select(name=obs["GEO_NAME"], area=obs["REF_AREA"]).to_sql().sql
    assert "LEFT JOIN memory.data.series" in sql
    assert "USING (FREQ, GEO)" in sql
    assert "s0_0.GEO_NAME" in sql, "the sidecar's own alias"
    assert "s0.GEO" in sql, "and the main table's for a key"


def test_a_join_key_reads_through_the_root(with_sidecar) -> None:
    """A key lives on both sides and `USING` merges them, so the root alias is
    correct — and reading it through the sidecar would be needless noise."""
    store, binding, obs = with_sidecar
    sql = store.obs(binding).select(area=obs["REF_AREA"]).to_sql().sql
    assert "s0.GEO AS area" in sql


def test_filtering_on_a_series_attribute(with_sidecar) -> None:
    """The filter crosses the join, and selects the whole series."""
    store, binding, obs = with_sidecar
    matched = store.obs(binding).filter(obs["GEO_NAME"] == "Mexico").count()
    assert matched == 2, "both observations of that series"


def test_a_sidecar_binding_still_streams_and_aggregates(with_sidecar) -> None:
    """The join is part of the plan, so every terminal keeps working."""
    store, binding, obs = with_sidecar
    grouped = store.obs(binding).group_by(obs["GEO_NAME"]).agg(n=obs["OBS_VALUE"].count()).collect()
    assert dict(zip(grouped["GEO_NAME"].to_list(), grouped["n"].to_list(), strict=True)) == {
        "Canada": 2,
        "United States": 2,
        "Mexico": 2,
    }


def test_a_binding_without_a_sidecar_plans_no_join(plane) -> None:
    """The common case is untouched: no sidecar, no join, same SQL as before."""
    store, binding, obs = plane
    assert store.obs(binding).plan.root.sidecar is None
    assert "JOIN" not in store.obs(binding).select(a=obs["REF_AREA"]).to_sql().sql


@pytest.fixture
def plane_with_dsd():
    """A renamed binding plus the DSD that gives its codes labels."""
    codelist = Codelist(
        id="CL_GEO",
        maintainer="AG",
        version="1.0",
        name={"en": "geo"},
        codes=[Code(id="ON", name={"en": "Ontario"}), Code(id="QC", name={"en": "Quebec"})],
    )
    concept = Ref[Concept].unresolved(Concept.urn_for(agency="AG", scheme="CS", id="REF_AREA", version="1.0"))
    dsd = DataStructure(
        id="D",
        maintainer="AG",
        version="1.0",
        name={"en": "d"},
        dimensions=[Dimension(id="REF_AREA", concept=concept, representation=Ref.to(codelist))],
        measures=[Measure(id="OBS_VALUE", concept=concept)],
    )
    with LocalRegistry(":memory:") as registry:
        registry.add(codelist)
        cursor = registry.connection.cursor()
        cursor.execute("CREATE SCHEMA IF NOT EXISTS data")
        cursor.execute("CREATE TABLE data.renamed (geo TEXT, v DOUBLE)")
        cursor.execute("INSERT INTO data.renamed VALUES ('ON', 1.0), ('QC', 2.0)")
        binding = _binding(
            table_location="data.renamed",
            dimension_column_map={"REF_AREA": "geo"},
            measure_column_map={"OBS_VALUE": "v"},
            attribute_column_map={},
        )
        yield DuckDBDataStore.from_registry(registry), binding, dsd


# ── results are named by component id (§6 step 3) ─────────────────────────


def test_an_unprojected_frame_is_named_by_component_id(plane) -> None:
    """The binding maps `REF_AREA` to `GEO` and `OBS_STATUS` to `STATUS`.

    Neither physical name reaches the caller. Every filter, projection and
    label in this language names a component, so a result that used the
    producer's column names was the one place they leaked.
    """
    store, binding, _obs = plane
    assert set(store.obs(binding).frame().columns) == {
        "REF_AREA",
        "TIME_PERIOD",
        "OBS_VALUE",
        "OBS_STATUS",
    }


def test_a_sidecar_column_is_named_by_component_id_too(with_sidecar) -> None:
    """Which side a column is stored on is a layout choice, so it must not
    change the name a caller sees."""
    store, binding, _obs = with_sidecar
    assert "GEO_NAME" in store.obs(binding).frame().columns


def test_an_explicit_projection_still_wins(plane) -> None:
    """`select()` names its own outputs; the default projection applies only
    where a caller named nothing."""
    store, binding, obs = plane
    frame = store.obs(binding).select(area=obs["REF_AREA"]).collect()
    assert frame.columns == ["area"]


def test_labels_now_work_on_a_renamed_binding(plane_with_dsd) -> None:
    """The payoff, and the bug this closes.

    `sdmxlib.polars.label` matches components **by id**. While an unprojected
    frame carried physical names, labelling a binding whose columns are not
    already ids matched nothing — silently at first, and by raising since that
    was found. Naming the projection fixes the cause rather than the symptom.
    """
    store, binding, dsd = plane_with_dsd
    frame = store.obs(binding, dsd=dsd).frame(labels="en")
    assert "REF_AREA_label_en" in frame.columns
    assert set(frame["REF_AREA_label_en"].to_list()) == {"Ontario", "Quebec"}


# ── the sidecar join is emitted only where something reads it (#388) ───────


@pytest.fixture
def with_orphan_sidecar():
    """A sidecar that has no row for one of the fact table's series.

    `ZZ` is stored as an observation and nowhere in `data.series`, which is
    what pins the `LEFT`ness: pruning a `LEFT JOIN` is only sound because it
    cannot drop rows, and a fixture whose every row matches would pass for an
    `INNER` too.
    """
    with LocalRegistry(":memory:") as registry:
        conn = registry.connection.cursor()
        conn.execute("CREATE SCHEMA IF NOT EXISTS data")
        conn.execute("""
            CREATE OR REPLACE TABLE data.obs AS
            SELECT 'A' AS FREQ, area AS GEO, period AS TIME_PERIOD, 1.5 AS OBS_VALUE
            FROM (SELECT unnest(['CA', 'US', 'ZZ']) AS area), (SELECT unnest(['2019', '2020']) AS period)
        """)
        conn.execute("""
            CREATE OR REPLACE TABLE data.series AS
            SELECT 'A' AS FREQ, area AS GEO, name AS GEO_NAME
            FROM (SELECT unnest(['CA', 'US']) AS area, unnest(['Canada', 'United States']) AS name)
        """)
        binding = _binding(
            table_location="data.obs",
            series_attributes_location="data.series",
            dimension_column_map={"FREQ": "FREQ", "REF_AREA": "GEO", "TIME_PERIOD": "TIME_PERIOD"},
            attribute_column_map={"GEO_NAME": "GEO_NAME"},
        )
        store = DuckDBDataStore.from_registry(registry)
        yield store, binding, store.obs(binding).plan.root


def test_a_count_over_the_whole_flow_plans_no_join(with_sidecar) -> None:
    """`count()` names no column at all, and used to get the join anyway.

    On the census bundle that was the entire cost of the aggregate: 2460 ms against
    1 ms for the same count without it, because the join scales with rows
    scanned and a count is supposed never to touch them. Asserted on the SQL
    text rather than on wall time — the defect is a clause.
    """
    store, binding, _obs = with_sidecar
    assert "JOIN" not in store.obs(binding).to_sql(columns="count(*)", ordered=False).sql


def test_a_projection_of_base_columns_plans_no_join(with_sidecar) -> None:
    """Narrowing the projection was never the issue — the join was unconditional.

    So this is the check that pruning keys on what is *referenced* rather than
    on how much is projected.
    """
    store, binding, obs = with_sidecar
    sql = (
        store.obs(binding).filter(obs["REF_AREA"] == "CA").select(area=obs["REF_AREA"], v=obs["OBS_VALUE"]).to_sql().sql
    )
    assert "JOIN" not in sql


def test_pruning_the_join_does_not_drop_the_unmatched_rows(with_orphan_sidecar) -> None:
    """The soundness condition, stated as a count.

    `ZZ` has observations and no sidecar row. A pruned query must still see
    them — which it does because the join it drops is `LEFT` — and the two
    counts must agree.
    """
    store, binding, obs = with_orphan_sidecar
    pruned = store.obs(binding)
    assert "JOIN" not in pruned.to_sql(columns="count(*)", ordered=False).sql
    assert pruned.count() == 6, "three series, two periods each, one of them unmatched"
    joined = pruned.filter(obs["GEO_NAME"].is_null() | obs["GEO_NAME"].is_not_null())
    assert "LEFT JOIN memory.data.series" in joined.to_sql(columns="count(*)", ordered=False).sql
    assert joined.count() == 6, "the same rows, the join present"


def test_an_unmatched_series_reads_as_null_rather_than_vanishing(with_orphan_sidecar) -> None:
    """An `INNER` join here would return four rows, not six."""
    store, binding, _obs = with_orphan_sidecar
    frame = store.obs(binding).frame()
    assert frame.height == 6
    assert frame.filter(pl.col("REF_AREA") == "ZZ")["GEO_NAME"].to_list() == [None, None]


def test_a_count_filtered_on_a_sidecar_column_keeps_the_join(with_sidecar) -> None:
    """Pruning is driven by what is referenced, not by the terminal.

    `count()` projects nothing, so a rule reading the projection alone would
    drop the join this predicate needs and count every row of the flow.
    """
    store, binding, obs = with_sidecar
    query = store.obs(binding).filter(obs["GEO_NAME"] == "Mexico")
    assert "LEFT JOIN memory.data.series" in query.to_sql(columns="count(*)", ordered=False).sql
    assert query.count() == 2


def test_ordering_by_a_sidecar_column_keeps_the_join(with_sidecar) -> None:
    """`ORDER BY` is compiled after the projection and the `WHERE`, so it is the
    clause a pruning pass that ran too early would miss."""
    store, binding, obs = with_sidecar
    sql = store.obs(binding).order_by(obs["GEO_NAME"]).select(area=obs["REF_AREA"]).to_sql().sql
    assert "LEFT JOIN memory.data.series" in sql


def test_grouping_by_a_sidecar_column_keeps_the_join(with_sidecar) -> None:
    """The `GROUP BY` case, where the reference is a key rather than a filter."""
    store, binding, obs = with_sidecar
    grouped = store.obs(binding).group_by(obs["GEO_NAME"]).agg(n=obs["OBS_VALUE"].count())
    assert "LEFT JOIN memory.data.series" in grouped.to_sql().sql
    frame = grouped.collect()
    assert dict(zip(frame["GEO_NAME"].to_list(), frame["n"].to_list(), strict=True)) == {
        "Canada": 2,
        "United States": 2,
        "Mexico": 2,
    }


def test_an_unprojected_read_still_joins(with_sidecar) -> None:
    """The default projection names every component, the sidecar's included —
    so a whole-row read references the join and keeps it."""
    store, binding, _obs = with_sidecar
    assert "LEFT JOIN memory.data.series" in store.obs(binding).to_sql().sql
    assert "GEO_NAME" in store.obs(binding).frame().columns


def test_a_sidecar_column_compiled_after_the_from_clause_is_refused() -> None:
    """The guard behind `from_sql`'s "call this last".

    Rendering the `FROM` fixes which root joins the statement has, so a clause
    compiled afterwards would name an alias nothing joined. DuckDB would refuse
    it — `Referenced table "s0_0" not found` — which is loud but says nothing
    about why. This says why, and it is what keeps the ordering in the three
    entry points a checked property rather than a comment.
    """
    binding = _binding(
        table_location="data.obs",
        series_attributes_location="data.series",
        attribute_column_map={"GEO_NAME": "GEO_NAME"},
    )
    root = Observations(binding, sidecar=Sidecar("data.series", ("GEO",), frozenset({"GEO", "GEO_NAME"})))
    scope = _Scope(root.__table__, (), _Namespace(), schema=root.__schema__, joins=root.__joins__)
    assert "JOIN" not in scope.from_sql(), "nothing referenced it"
    with pytest.raises(PathError, match="had already been pruned"):
        scope.reference(root["GEO_NAME"])


# ── declining the key order, which is what makes it affordable (#389) ─────


def test_an_observation_query_can_decline_the_key_order(plane) -> None:
    """The clause #389 measured, and the spelling that turns it off.

    Every observation query sorts on all of the dataflow's dimensions. That is
    the root-key tiebreak, not a `default_order` — this root sets none — and it
    exists so `limit`/`offset` paging is stable. On the census bundle's 4.26 M-row
    `CensusDivision` partition it was **337 ms against 62 ms**, 5.4x the scan it
    decorated, and `stream()` was the only terminal that could decline it.
    """
    store, binding, obs = plane
    ordered = store.obs(binding).select(a=obs["REF_AREA"], v=obs["OBS_VALUE"])
    assert "ORDER BY" in ordered.to_sql().sql
    unordered = store.obs(binding).order_by(None).select(a=obs["REF_AREA"], v=obs["OBS_VALUE"])
    assert "ORDER BY" not in unordered.to_sql().sql
    assert sorted(unordered.collect()["a"].to_list()) == sorted(ordered.collect()["a"].to_list())


def test_an_unordered_observation_frame_carries_every_row(plane) -> None:
    """The terminal the issue is about: a large frame without the sort, and
    without dropping to `reg.sql()`."""
    store, binding, _obs = plane
    whole = store.obs(binding).frame()
    assert store.obs(binding).order_by(None).frame().height == whole.height
    assert "ORDER BY" not in store.obs(binding).order_by(None).to_sql().sql


def test_declining_the_order_drops_the_grouped_tiebreak_too(plane) -> None:
    """A grouped result is tie-broken by its group keys' ordinals so the groups
    come back in a stable order. That is the same promise for the same reason,
    and this is the caller withdrawing the request — so it goes with the rest
    rather than surviving as a clause nothing asked for."""
    store, binding, obs = plane
    grouped = store.obs(binding).order_by(None).group_by(obs["REF_AREA"])
    assert "ORDER BY" not in grouped.agg(n=obs["OBS_VALUE"].count()).to_sql().sql
    ordered = store.obs(binding).group_by(obs["REF_AREA"])
    assert "ORDER BY" in ordered.agg(n=obs["OBS_VALUE"].count()).to_sql().sql
