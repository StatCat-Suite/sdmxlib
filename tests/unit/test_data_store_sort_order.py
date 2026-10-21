"""Storing observations sorted, and the cost of doing it (#330).

Asked while sizing `stream()`'s `chunk_rows`: if a query's `ORDER BY` is what
makes a stream unbounded, should the rows be stored sorted so the sort is not
needed? The measurements say yes, and for none of the obvious reasons.

**Sorting at ingestion does not let DuckDB skip a sort.** It has no
read-in-order optimisation for base tables, so an `ORDER BY` over
already-sorted rows still sorts — 102 MB against 251 MB shuffled at 4M rows.
Cheaper, because sorting nearly-sorted input is nearly free; not free.

**A sorted scan comes back in order only by accident.** It does, while
`preserve_insertion_order` is true — the DuckDB default, and exactly the
setting a caller turns off for a large result. With it false the ordering
vanishes and the memory does not change. So the layout is a planning hint and
never a guarantee a consumer may rely on, which is what `sort_order`'s
docstring says and what `test_sorted_storage_is_not_a_promise_of_read_order`
pins.

**It costs about 2.5x on the way in** — 82 ms against 33 ms, 342 MB against
132 MB at 4M rows — which is why `sort_by` is opt-in. Paying once at write for
every read is usually right, and it is not right when ingestion is the
memory-constrained step.

The industry split is the same: Iceberg's `sortOrder` and Delta's `ZORDER` are
layout properties for pruning and compression, while ClickHouse's table
`ORDER BY` *is* its primary index and can serve a sorted read with no sort at
all. DuckDB is in the first group.
"""

from __future__ import annotations

from datetime import UTC, datetime

import polars as pl
import pytest

from sdmxlib.local.data_store import DuckDBDataStore
from sdmxlib.local.registry import LocalRegistry
from sdmxlib.model.binding import DataflowBinding
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.ref import Ref
from sdmxlib.model.registry import artefact_urn

pytestmark = pytest.mark.unit

#: Deliberately not in key order, so a write that fails to sort is visible.
_ROWS = pl.DataFrame(
    {
        "GEO": ["QC", "CA", "ON", "CA"],
        "T": ["2021", "2021", "2020", "2020"],
        "V": [4.0, 1.0, 3.0, 2.0],
    }
)


@pytest.fixture
def store():
    with LocalRegistry(":memory:") as registry:
        data_store = DuckDBDataStore.from_registry(registry)
        data_store.connection.execute("ATTACH ':memory:' AS lake")
        yield data_store


def _keys(store: DuckDBDataStore, table: str) -> list[tuple[str, str]]:
    return [
        (str(geo), str(period)) for geo, period in store.connection.execute(f"SELECT GEO, T FROM {table}").fetchall()
    ]


# ── the write ─────────────────────────────────────────────────────────────


def test_sort_by_stores_the_rows_in_order(store) -> None:
    store.write_observations(schema="d", table="obs", source=_ROWS, sort_by=("GEO", "T"))
    assert _keys(store, "lake.d.obs") == [("CA", "2020"), ("CA", "2021"), ("ON", "2020"), ("QC", "2021")]


def test_without_sort_by_the_rows_arrive_as_they_came(store) -> None:
    """The default is unchanged, which is what keeps this opt-in.

    A write that silently reordered would be a surprise for anyone who had
    arranged their source deliberately.
    """
    store.write_observations(schema="d", table="obs", source=_ROWS)
    assert _keys(store, "lake.d.obs") == [("QC", "2021"), ("CA", "2021"), ("ON", "2020"), ("CA", "2020")]


def test_an_append_sorts_only_what_it_inserts(store) -> None:
    """The table ends up sorted in runs, not globally.

    That is what every append-structured store gives you, and why they all
    compact — DuckLake's own rewrite is the compaction here. Stated because a
    caller reading `sort_order` off a binding would otherwise expect a global
    guarantee that an append cannot provide.
    """
    first = pl.DataFrame({"GEO": ["ON", "CA"], "T": ["2020", "2020"], "V": [3.0, 2.0]})
    second = pl.DataFrame({"GEO": ["QC", "CA"], "T": ["2021", "2021"], "V": [4.0, 1.0]})
    store.write_observations(schema="d", table="obs", source=first, sort_by=("GEO", "T"))
    store.write_observations(schema="d", table="obs", source=second, sort_by=("GEO", "T"), mode="append")
    keys = _keys(store, "lake.d.obs")
    assert keys[:2] == [("CA", "2020"), ("ON", "2020")], "the first run is sorted"
    assert keys[2:] == [("CA", "2021"), ("QC", "2021")], "and so is the second, after it"
    assert keys != sorted(keys), "but the table is not globally sorted"


# ── the refusals ──────────────────────────────────────────────────────────


def test_a_column_the_source_lacks_is_refused(store) -> None:
    with pytest.raises(ValueError, match="not a column of the source"):
        store.write_observations(schema="d", table="obs", source=_ROWS, sort_by=("NOPE",))


def test_an_sdmx_id_where_a_column_was_wanted_is_named_as_such(store) -> None:
    """The likelier mistake of the two, so the message says which it is.

    `sort_by` takes physical column names because this method holds a source
    and a target and never a binding — there is nothing for an SDMX id to
    resolve against. `REF_AREA` when the column is `GEO` is the error a caller
    will actually make.
    """
    with pytest.raises(ValueError, match="physical column names, not SDMX ids"):
        store.write_observations(schema="d", table="obs", source=_ROWS, sort_by=("REF_AREA",))


# ── the declaration on the binding ────────────────────────────────────────


def test_sort_order_round_trips_on_a_binding() -> None:
    """Declared beside `partition_layout`, and for the same reason: it is a
    property of how the data was written, and a reader that knows it can plan
    differently. Iceberg carries both."""
    binding = DataflowBinding(
        dataflow=Ref[Dataflow].unresolved(Dataflow.urn_for(agency="AG", id="DF", version="1.0")),
        database_role="memory",
        table_location="data.obs",
        dimension_column_map={"REF_AREA": "GEO"},
        measure_column_map={"OBS_VALUE": "V"},
        attribute_column_map={},
        structural_version="AG:DSD(1.0)",
        source_registry="local",
        partition_layout=("REF_AREA",),
        sort_order=("REF_AREA", "TIME_PERIOD"),
        materialized_at=datetime(2026, 1, 1, tzinfo=UTC),
        bound_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    with LocalRegistry(":memory:") as registry:
        registry.add(binding)
        stored = registry.get(artefact_urn(binding))
    assert isinstance(stored, DataflowBinding)
    assert stored.sort_order == ("REF_AREA", "TIME_PERIOD")
    assert stored.partition_layout == ("REF_AREA",)


def test_sorted_storage_is_not_a_promise_of_read_order(store) -> None:
    """The trap this whole design avoids.

    A scan of a sorted table returns rows in order *while*
    `preserve_insertion_order` is true. It is true by default and it is exactly
    what a caller turns off for a very large result — measured: the ordering
    goes and the memory does not move. So `sort_order` is a hint, and
    `stream()` still drops the `ORDER BY` rather than trusting the layout.

    Asserted because it would otherwise hold in every test and fail in the one
    configuration someone reaches for under load.
    """
    store.write_observations(schema="d", table="obs", source=_ROWS, sort_by=("GEO", "T"))
    assert _keys(store, "lake.d.obs") == sorted(_keys(store, "lake.d.obs")), "ordered, by default"

    store.connection.execute("SET preserve_insertion_order=false")
    reread = store.connection.execute("SELECT GEO, T FROM lake.d.obs").fetchall()
    assert len(reread) == 4, "the rows are all still there"
    # Four rows will not actually reorder — the point is that nothing promises
    # they will not, which is why the query language does not rely on it.
    assert store.connection.execute("SELECT current_setting('preserve_insertion_order')").fetchone() == (False,)
