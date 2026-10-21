"""Results whose row or column order polars does not promise are given one.

`group_by`, `unique` and a lazy `pivot` without `maintain_order` return rows in
whatever order the engine produces, and that order is not stable: at 20,000
rows each of the three came back in a different order on every one of eight
runs — on polars 1.39 *and* 2.0. Polars 2.0 made it more likely to show, by
running lazy queries on the streaming engine by default, but it never was a
promise.

Three places handed that order straight to a caller:

- `validation_report`'s rows (one `group_by` per component);
- `pivot()`'s value columns, when the axis' enumeration is unresolved and the
  values are discovered from the data (`unique`);
- `pivot()`'s rows (a lazy `pivot`, default `maintain_order=False`).

`pivot` was `Dataset`'s and is now `ObsQuery`'s (#516); the guarantees moved
with it.

Each test feeds enough rows, in a deliberately unsorted order, that the
unordered result differs from the promised one — `_the_engine_really_reorders`
checks that premise rather than trusting it, since a test over three rows
would pass for an implementation that sorted nothing.
"""

import random

import polars as pl
import pytest

import sdmxlib as sl
import sdmxlib.polars as slpl
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.collections import ItemList
from sdmxlib.model.datastructure import DataStructure, Dimension, Measure, TimeDimension
from sdmxlib.model.ref import Ref
from sdmxlib.model.representation import Facet
from tests.support.factories import concept_ref

pytestmark = pytest.mark.unit

N_KEYS = 3000
SEED = 412


def _shuffled(values: list[str]) -> list[str]:
    out = list(values)
    random.Random(SEED).shuffle(out)
    return out


def test_the_engine_really_reorders() -> None:
    """The premise: at this size an unordered `group_by` does not come back sorted."""
    lf = pl.LazyFrame({"k": _shuffled([f"v{i:05d}" for i in range(N_KEYS)] * 3)})
    got = lf.group_by("k").agg(pl.len()).collect()["k"].to_list()
    assert got != sorted(got)


# ── validation_report ─────────────────────────────────────────────────────────


def _enumerated(dim_id: str, *codes: str) -> Dimension:
    cl = Codelist(
        id=f"CL_{dim_id}", maintainer="T", name={"en": dim_id}, codes=[Code(id=c, name={"en": c}) for c in codes]
    )
    return Dimension(id=dim_id, representation=Ref[Codelist].to(cl), concept=concept_ref("X"))


def test_validation_report_rows_are_in_dsd_then_value_order() -> None:
    """Components in DSD order, each one's invalid values sorted."""
    dsd = DataStructure(
        name={"en": "D"},
        id="D",
        maintainer="T",
        dimensions=ItemList([_enumerated("FREQ", "A"), _enumerated("REF_AREA", "CA")]),
        measures=ItemList([Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))]),
    )
    bad_freq = [f"F{i:05d}" for i in range(N_KEYS)]
    bad_area = [f"R{i:05d}" for i in range(N_KEYS)]
    df = pl.DataFrame({"FREQ": _shuffled(bad_freq), "REF_AREA": _shuffled(bad_area), "OBS_VALUE": ["1"] * N_KEYS})

    report = slpl.validation_report(dsd, df)

    assert report["column"].to_list() == ["FREQ"] * N_KEYS + ["REF_AREA"] * N_KEYS
    assert report["invalid_value"].to_list() == bad_freq + bad_area


# ── ObsQuery.pivot ────────────────────────────────────────────────────────────


@pytest.fixture
def pivoted():
    """`pivot(on="MEASURE")` over a store, where MEASURE has no resolved codelist: its codes come from the data."""
    registries: list[sl.LocalRegistry] = []

    def build(area_order: list[str], measures: list[str]) -> pl.DataFrame:
        dsd = DataStructure(
            name={"en": "D"},
            id="D",
            maintainer="T",
            dimensions=ItemList(
                [
                    Dimension(id="REF_AREA", representation=Facet(type="String"), concept=concept_ref("REF_AREA")),
                    Dimension(id="MEASURE", concept=concept_ref("MEASURE")),
                ]
            ),
            time_dimension=TimeDimension(id="TIME_PERIOD", concept=concept_ref("TIME_PERIOD")),
            measures=ItemList([Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))]),
        )
        flow = sl.Dataflow(id="F", maintainer="T", name={"en": "f"}, structure=Ref.to(dsd))
        registry = sl.LocalRegistry(":memory:").open()
        registries.append(registry)
        rows = [(area, m, float(i)) for i, area in enumerate(area_order) for m in measures]
        frame = pl.DataFrame(rows, schema=["REF_AREA", "MEASURE", "OBS_VALUE"], orient="row").with_columns(
            TIME_PERIOD=pl.lit("2020")
        )
        registry.connection.register("long_rows", frame.to_arrow())
        registry.connection.execute("CREATE SCHEMA data")
        registry.connection.execute("CREATE TABLE data.obs AS SELECT * FROM long_rows")
        binding = sl.DataflowBinding.from_dsd(
            dsd, dataflow=Ref.to(flow), schema="data", table="obs", database_role="memory"
        )
        return sl.DuckDBDataStore.from_registry(registry).obs(binding, dsd=dsd).pivot(on="MEASURE")

    yield build
    for registry in registries:
        registry.close()


def test_pivot_value_columns_are_sorted_when_discovered_from_the_data(pivoted) -> None:
    measures = [f"M{i:04d}" for i in range(200)]
    wide = pivoted(["CA", "US"], _shuffled(measures))
    assert wide.columns == ["REF_AREA", "TIME_PERIOD", *measures]


def test_pivot_rows_keep_the_order_their_series_first_appear_in(pivoted) -> None:
    """`DataFrame.pivot`'s contract, kept by the lazy one too — over `frame()`'s key order."""
    areas = _shuffled([f"A{i:05d}" for i in range(N_KEYS)])
    wide = pivoted(areas, ["EMP", "UNE"])
    assert wide["REF_AREA"].to_list() == sorted(areas)
