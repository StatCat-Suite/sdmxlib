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
"""

from pathlib import Path

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
