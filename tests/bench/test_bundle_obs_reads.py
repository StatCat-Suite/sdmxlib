"""Wall time of the observation reads a consumer makes, against a real bundle (GH-205).

`test_bundle_observations.py` asserts clauses and two ratios; this file
measures the paths themselves, so a change to how `obs()` plans can be
compared before and after with pytest-benchmark (`just bench-bundle`, or
`--benchmark-save` / `--benchmark-compare` across two checkouts). It asserts
only that each query returns the rows the bundle says it holds.

The queries are chosen from the bundle, as `_bundle.discover_observations`
chooses its partition: the dataflow's stored availability names the two
dimensions with the most values, and each read takes the first of them in
sorted order. On the 2021 Census Profile those are `REF_AREA` (78,487
geographies) and `CHARACTERISTIC` (2,631).

Measured on that bundle when GH-205 moved the partition layout from the
binding into DuckLake's catalog, best of 3 per process, 3 interleaved
processes per side, DuckDB on 4 threads; the two sides agreed within noise:

| Read | Before | After |
| --- | --- | --- |
| open the bundle and plan the first `obs()` | 152-173 ms | 152-171 ms |
| `obs()` on an open bundle | 5.2-5.3 ms | 5.2-5.4 ms |
| one geography, `frame()`, 14,545 rows | 156-160 ms | 157-160 ms |
| one characteristic, `frame()`, 78,487 rows | 616-630 ms | 623-629 ms |
| one `GEO_LEVEL` partition, `count()` | 8.9-9.4 ms | 9.2-9.4 ms |
| one characteristic, streamed in partition order | 668-684 ms | 673-681 ms |

The `test_level_*` reads run inside the largest partition, where the table's
layout decides the cost: DisseminationArea, 842 M of the census's 1.14 B rows.
They cover the kinds of read a layout trades against each other:

- one member of each of the two widest dimensions;
- one cell, the two together;
- ten members at once;
- a member within one value of a sidecar column, which joins;
- every row grouped, and the partition's count.

Each takes the middle value in sort order, since the first would favour a
layout that happens to start with it. On the census bundle as published at
store schema 2.51 (rows in geography-major order, one median of 3), in s:

| read | s |
| --- | --- |
| one geography | 0.24 |
| one characteristic | 1.88 |
| one cell | 0.25 |
| ten characteristics | 2.58 |
| one characteristic in one province | 1.78 |
| grouped by characteristic | 1.41 |
| count | 0.22 |
"""

from pathlib import Path

import attrs
import pytest

import sdmxlib as sl
from sdmxlib.local.registry import BundleRegistry
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.query.observations import ObsQuery
from tests.bench import _bundle

pytestmark = pytest.mark.bundle


def _bench(benchmark, fn):
    """Three rounds, no calibration: these reads take up to a second each."""
    return benchmark.pedantic(fn, rounds=3, iterations=1, warmup_rounds=1)


@pytest.fixture(scope="module")
def flow(obs_shapes) -> SdmxUrn[sl.Dataflow]:
    return sl.Dataflow.parse_urn(obs_shapes.dataflow_urn)


@pytest.fixture(scope="module")
def members(data_plane_registry, flow) -> list[tuple[str, str, int]]:
    """The two dimensions with the most values, widest first: (id, first value, value count)."""
    availability = data_plane_registry.availability(flow)
    if availability is None:
        pytest.skip("this bundle stores no availability to choose the queried values from")
    dimensions = sorted(
        (key for key in availability.region.keys if len(key.values) > 1),
        key=lambda key: len(key.values),
        reverse=True,
    )
    if len(dimensions) < 2:
        pytest.skip("this bundle's dataflow has fewer than two dimensions with more than one value")
    return [(key.dimension_id, min(key.codes()), len(key.values)) for key in dimensions[:2]]


def _one(reg: BundleRegistry, flow: SdmxUrn[sl.Dataflow], dimension: str, value: str) -> ObsQuery:
    obs = reg.obs(flow)
    return obs.filter(obs.col(dimension) == value)


def test_open_and_plan_the_first_obs(benchmark, data_plane_bundle: Path, flow):
    """Opening attaches the lake on first need; the first `obs()` also reads the table's partitions."""

    def read() -> tuple[str, ...]:
        engine = sl.BundleEngine(threads=_bundle.threads())
        try:
            with sl.LocalRegistry.bundle(data_plane_bundle, engine=engine) as reg:
                return reg.obs(flow).plan.root.__partitions__
        finally:
            engine.close()

    partitions = _bench(benchmark, read)
    benchmark.extra_info["partitions"] = list(partitions)


def test_obs_on_an_open_bundle(benchmark, data_plane_registry, flow):
    query = _bench(benchmark, lambda: data_plane_registry.obs(flow))
    assert query.plan.root.__partitions__ == data_plane_registry.obs(flow).plan.root.__partitions__


@pytest.mark.parametrize("which", [0, 1], ids=["widest_dimension", "second_dimension"])
def test_one_member_as_a_frame(benchmark, data_plane_registry, flow, members, which):
    dimension, value, _ = members[which]
    benchmark.extra_info["filter"] = f"{dimension} == {value}"
    frame = _bench(benchmark, lambda: _one(data_plane_registry, flow, dimension, value).frame())
    benchmark.extra_info["rows"] = frame.height
    assert frame.height > 0, f"no observation has {dimension} == {value}, which availability lists"


def test_one_partition_count(benchmark, data_plane_registry, flow, obs_shapes):
    if obs_shapes.partition is None or obs_shapes.big_partition is None:
        pytest.skip("this bundle's lake does not partition the dataflow's table")
    root = data_plane_registry.obs(flow).plan.root
    component = root.binding.component_of(obs_shapes.partition)
    count = _bench(benchmark, lambda: _one(data_plane_registry, flow, component, obs_shapes.big_partition).count())
    benchmark.extra_info["partition"] = f"{component} == {obs_shapes.big_partition}"
    assert count == obs_shapes.big_partition_rows


def test_one_member_streamed_in_partition_order(benchmark, data_plane_registry, flow, members, obs_shapes):
    """The per-partition stream plan, which reads the partitions `obs()` found."""
    if obs_shapes.partition is None:
        pytest.skip("this bundle's lake does not partition the dataflow's table")
    widest, _, _ = members[0]
    dimension, value, _ = members[1]
    component = data_plane_registry.obs(flow).plan.root.binding.component_of(obs_shapes.partition)

    def stream() -> int:
        obs = _one(data_plane_registry, flow, dimension, value)
        ordered = obs.order_by(obs.col(component), obs.col(widest)).select(p=obs.col(component), k=obs.col(widest))
        rows = 0
        with ordered.stream(100_000) as batches:
            for batch in batches:
                rows += batch.num_rows
        return rows

    rows = _bench(benchmark, stream)
    benchmark.extra_info["rows"] = rows
    assert rows > 0
    assert rows == _one(data_plane_registry, flow, dimension, value).count(), "the stream returns every row"


# ── the largest partition: where a layout decides the cost ────────────────────


@attrs.frozen
class Level:
    """The largest partition and the values each read in it filters on, all found in the bundle."""

    #: The partition column's component, and the largest partition's value.
    component: str
    value: str
    rows: int
    #: The middle value, in sort order, of the widest dimension and of the second widest in the
    #: partition. The first would favour a layout that happens to start with it.
    widest: tuple[str, str]
    second: tuple[str, str]
    #: Ten consecutive values of the second widest dimension, from its middle.
    several: tuple[str, ...]
    #: A sidecar column and the chosen geography's value of it, or `None` without a sidecar.
    sidecar: tuple[str, object] | None
    #: The measure an aggregate averages.
    measure: str


@pytest.fixture(scope="module")
def level(data_plane_registry, flow, members, obs_shapes) -> Level:
    """The partition with the most rows: DisseminationArea, 842 M of the census's 1.14 B."""
    if obs_shapes.partition is None:
        pytest.skip("this bundle's lake does not partition the dataflow's table")
    obs = data_plane_registry.obs(flow)
    component = obs.plan.root.binding.component_of(obs_shapes.partition)
    sizes = obs.group_by(obs.col(component)).agg(n=sl.len()).collect()
    value, rows = max(sizes.iter_rows(), key=lambda row: row[1])
    inside = obs.filter(obs.col(component) == value)
    (widest, _, _), (second, _, _) = members

    def present(dimension: str) -> list[str]:
        return sorted(str(v) for v in inside.select(v=obs.col(dimension)).unique().collect()["v"])

    geographies, characteristics = present(widest), present(second)
    geography = geographies[len(geographies) // 2]
    middle = len(characteristics) // 2
    several = characteristics[middle : middle + 10]
    sidecar = None
    if obs_shapes.sidecar_component is not None:
        column = obs_shapes.sidecar_component
        frame = inside.filter(obs.col(widest) == geography).select(v=obs.col(column)).limit(1).collect()
        # The value as stored: a numeric column compares with a number.
        sidecar = (column, frame["v"][0])
    return Level(
        component=component,
        value=str(value),
        rows=int(rows),
        widest=(widest, geography),
        second=(second, several[0]),
        several=tuple(several),
        sidecar=sidecar,
        measure=next(iter(obs.plan.root.binding.measure_column_map)),
    )


def _in_level(reg: BundleRegistry, flow: SdmxUrn[sl.Dataflow], level: Level) -> ObsQuery:
    obs = reg.obs(flow)
    return obs.filter(obs.col(level.component) == level.value)


@pytest.mark.parametrize("which", ["widest", "second"])
def test_level_one_member_as_a_frame(benchmark, data_plane_registry, flow, level, which):
    """One geography and one characteristic: the two reads one sort order trades against each other."""
    dimension, value = getattr(level, which)
    benchmark.extra_info["filter"] = f"{level.component} == {level.value}, {dimension} == {value}"

    def read() -> int:
        obs = _in_level(data_plane_registry, flow, level)
        return obs.filter(obs.col(dimension) == value).frame().height

    rows = _bench(benchmark, read)
    benchmark.extra_info["rows"] = rows
    assert rows > 0


def test_level_one_cell(benchmark, data_plane_registry, flow, level):
    """One characteristic at one geography: the point lookup a table cell makes."""
    (widest, geography), (second, characteristic) = level.widest, level.second

    def read() -> int:
        obs = _in_level(data_plane_registry, flow, level)
        return obs.filter(obs.col(widest) == geography, obs.col(second) == characteristic).frame().height

    assert _bench(benchmark, read) > 0


def test_level_several_members(benchmark, data_plane_registry, flow, level):
    """Ten characteristics at once (`is_in`), which a table of related rows asks for."""
    dimension = level.second[0]

    def read() -> int:
        obs = _in_level(data_plane_registry, flow, level)
        return obs.filter(obs.col(dimension).is_in(level.several)).frame().height

    rows = _bench(benchmark, read)
    benchmark.extra_info["rows"] = rows
    assert rows > 0


def test_level_one_member_within_a_sidecar_value(benchmark, data_plane_registry, flow, level):
    """One characteristic across one province: a sidecar filter, so the read joins."""
    if level.sidecar is None:
        pytest.skip("this bundle's binding has no series-attributes sidecar")
    column, value = level.sidecar
    dimension, member = level.second
    benchmark.extra_info["filter"] = f"{column} == {value}, {dimension} == {member}"

    def read() -> int:
        obs = _in_level(data_plane_registry, flow, level)
        return obs.filter(obs.col(column) == value, obs.col(dimension) == member).frame().height

    rows = _bench(benchmark, read)
    benchmark.extra_info["rows"] = rows
    assert rows > 0


def test_level_aggregate(benchmark, data_plane_registry, flow, level):
    """Every row of the partition, grouped by characteristic: the read a curve layout slows."""
    dimension = level.second[0]

    def read() -> int:
        obs = _in_level(data_plane_registry, flow, level)
        return obs.group_by(obs.col(dimension)).agg(n=sl.len(), mean=obs.num(level.measure).mean()).collect().height

    groups = _bench(benchmark, read)
    benchmark.extra_info["groups"] = groups
    assert groups >= len(level.several)


def test_level_count(benchmark, data_plane_registry, flow, level):
    count = _bench(benchmark, lambda: _in_level(data_plane_registry, flow, level).count())
    assert count == level.rows
