"""What a `Dataset` offered, asked of an `ObsQuery` instead (#516).

`reg.data(...)` and `reg.obs(flow)` read the same observations and
answered different questions. This file is the `ObsQuery` side of each one
as it moves over: the structure first, then sinks, validation and pivot.
"""

import json
from pathlib import Path

import attrs
import polars as pl
import pytest

import sdmxlib as sl
import sdmxlib.polars as slpl

pytestmark = pytest.mark.unit

_AREAS = sl.Codelist(
    id="CL_AREA",
    maintainer="AG",
    name={"en": "Areas"},
    codes=[sl.Code(id="CA", name={"en": "Canada"}), sl.Code(id="US", name={"en": "United States"})],
)
_CONCEPTS = sl.ConceptScheme(
    id="CS",
    maintainer="AG",
    name={"en": "c"},
    concepts=[sl.Concept(id=c, name={"en": c}) for c in ("REF_AREA", "OBS_VALUE")],
)
_DSD = sl.DataStructure(
    id="DSD",
    maintainer="AG",
    name={"en": "d"},
    dimensions=[sl.Dimension(id="REF_AREA", concept=_CONCEPTS.ref_to("REF_AREA"), representation=sl.Ref.to(_AREAS))],
    measures=[
        sl.Measure(id="OBS_VALUE", concept=_CONCEPTS.ref_to("OBS_VALUE"), representation=sl.Facet(type="Double"))
    ],
)
_FLOW = sl.Dataflow(id="F", maintainer="AG", name={"en": "f"}, structure=sl.Ref.to(_DSD))
_BINDING = sl.DataflowBinding.from_dsd(
    _DSD, dataflow=sl.Ref.to(_FLOW), schema="data", table="obs", database_role="memory"
)


def _store(rows: str) -> sl.LocalRegistry:
    """A store whose values are text: `'1.50'` reads back as `1.5` only if it was typed."""
    registry = sl.LocalRegistry(":memory:").open()
    registry.add_all([_AREAS, _CONCEPTS, _DSD, _FLOW])
    registry.connection.execute("CREATE SCHEMA data")
    registry.connection.execute(f"CREATE TABLE data.obs AS SELECT * FROM (VALUES {rows}) t(REF_AREA, OBS_VALUE)")
    registry.add(_BINDING)
    return registry


@pytest.fixture
def registry():
    registry = _store("('CA', '1.50'), ('US', '2.50')")
    try:
        yield registry
    finally:
        registry.close()


@pytest.fixture
def obs(registry: sl.LocalRegistry) -> sl.ObsQuery:
    return registry.obs(_FLOW)


def test_a_registry_query_carries_its_structure(registry: sl.LocalRegistry) -> None:
    structure = registry.obs(_FLOW).structure
    assert structure == _DSD
    assert structure is not None
    assert structure.dimensions.ids() == ["REF_AREA"]


def test_a_refined_query_keeps_it(registry: sl.LocalRegistry) -> None:
    obs = registry.obs(_FLOW)
    assert obs.filter(obs.col("REF_AREA") == "CA").limit(1).structure == _DSD


def test_a_store_query_carries_the_structure_it_was_given(registry: sl.LocalRegistry) -> None:
    store = sl.DuckDBDataStore.from_registry(registry)
    assert store.obs(_BINDING, dsd=_DSD).structure == _DSD


def test_a_store_query_built_without_one_has_none(registry: sl.LocalRegistry) -> None:
    """The discrimination check: `structure` reads the root, it does not invent one."""
    store = sl.DuckDBDataStore.from_registry(registry)
    assert store.obs(_BINDING).structure is None


# ── sinks: typed per batch, as frame() is ──────────────────────────────────────


def test_parquet_holds_what_a_frame_would(obs: sl.ObsQuery, tmp_path: Path) -> None:
    obs.sink_parquet(tmp_path / "o.parquet", chunk_rows=1)
    written = pl.read_parquet(tmp_path / "o.parquet")
    assert written.schema["OBS_VALUE"] == pl.Float64
    assert written.with_columns(pl.col("REF_AREA").cast(pl.String)).equals(
        obs.frame().with_columns(pl.col("REF_AREA").cast(pl.String))
    )


def test_csv_holds_what_a_frame_would(obs: sl.ObsQuery, tmp_path: Path) -> None:
    """One header across batches, and `1.5` rather than the stored `1.50`."""
    obs.sink_csv(tmp_path / "o.csv", chunk_rows=1)
    assert (tmp_path / "o.csv").read_text() == obs.frame().write_csv()
    assert (tmp_path / "o.csv").read_text() == "REF_AREA,OBS_VALUE\nCA,1.5\nUS,2.5\n"


@pytest.mark.parametrize(
    ("version", "data_sets"), [("2.0", lambda m: m["data"]["dataSets"]), ("1.0", lambda m: m["dataSets"])]
)
def test_json_carries_typed_values(obs: sl.ObsQuery, tmp_path: Path, version, data_sets) -> None:
    obs.sink_json(tmp_path / "o.json", lang="en", version=version, chunk_rows=1)
    message = json.loads((tmp_path / "o.json").read_text())
    (data_set,) = data_sets(message)
    assert data_set["observations"] == {"0": [1.5], "1": [2.5]}
    assert data_set["links"][0]["urn"] == str(_FLOW.urn)


def test_an_empty_result_still_writes_its_columns(obs: sl.ObsQuery, tmp_path: Path) -> None:
    empty = obs.filter(obs.col("REF_AREA") == "XX")
    empty.sink_csv(tmp_path / "o.csv")
    empty.sink_parquet(tmp_path / "o.parquet")
    assert (tmp_path / "o.csv").read_text() == "REF_AREA,OBS_VALUE\n"
    assert pl.read_parquet(tmp_path / "o.parquet").schema["OBS_VALUE"] == pl.Float64


@pytest.mark.parametrize("sink", ["sink_parquet", "sink_csv", "sink_json"])
def test_a_value_that_does_not_fit_fails_as_a_frame_does(sink: str, tmp_path: Path) -> None:
    registry = _store("('ZZ', '1.0')")
    try:
        obs = registry.obs(_FLOW)
        kwargs = {"lang": "en"} if sink == "sink_json" else {}
        with pytest.raises(ValueError, match=r"(?s)does not fit its component's type.*\.stream\(\)"):
            getattr(obs, sink)(tmp_path / "out", **kwargs)
    finally:
        registry.close()


def test_json_needs_a_structure(registry: sl.LocalRegistry, tmp_path: Path) -> None:
    store = sl.DuckDBDataStore.from_registry(registry)
    with pytest.raises(ValueError, match="dsd="):
        store.obs(_BINDING).sink_json(tmp_path / "o.json", lang="en")


# ── validate: the report a typed read cannot give ─────────────────────────────


@pytest.fixture
def bad():
    registry = _store("('ZZ', '1'), ('CA', '2'), ('ZZ', '3'), ('AA', NULL), (NULL, '4')")
    try:
        yield registry.obs(_FLOW)
    finally:
        registry.close()


def test_validate_names_every_bad_value(bad: sl.ObsQuery) -> None:
    assert bad.validate().rows() == [("REF_AREA", "AA", 1), ("REF_AREA", "ZZ", 2)]


@pytest.mark.parametrize("chunk_rows", [1, 2, 100_000])
def test_validate_reports_as_one_pass_over_the_raw_rows_would(bad: sl.ObsQuery, chunk_rows: int) -> None:
    """Batches add up: `ZZ` is split across batches at 1 and 2 and still counts 2."""
    raw = pl.from_arrow(bad.stream().read_all())
    assert isinstance(raw, pl.DataFrame)
    assert bad.validate(chunk_rows=chunk_rows).equals(slpl.validation_report(_DSD, raw))


def test_validate_is_empty_when_every_value_fits(obs: sl.ObsQuery) -> None:
    report = obs.validate()
    assert report.is_empty()
    assert report.schema == pl.Schema({"column": pl.String, "invalid_value": pl.String, "count": pl.UInt32})


def test_validate_needs_a_structure(registry: sl.LocalRegistry) -> None:
    store = sl.DuckDBDataStore.from_registry(registry)
    with pytest.raises(ValueError, match="dsd="):
        store.obs(_BINDING).validate()


# ── pivot: wide on the measure dimension, or on= ──────────────────────────────

_MEASURES = sl.ConceptScheme(
    id="CS_MEASURES",
    maintainer="AG",
    name={"en": "Measures"},
    concepts=[sl.Concept(id=c, name={"en": c}) for c in ("EMP", "UNEMP")],
)
_XS_CONCEPTS = sl.ConceptScheme(
    id="CS_XS",
    maintainer="AG",
    name={"en": "c"},
    concepts=[sl.Concept(id=c, name={"en": c}) for c in ("REF_AREA", "MEASURE", "TIME_PERIOD", "OBS_VALUE")],
)
#: SDMX 2.1 cross-sectional: one OBS_VALUE per measure per row.
_XS_DSD = sl.DataStructure(
    id="XS",
    maintainer="AG",
    name={"en": "x"},
    dimensions=[sl.Dimension(id="REF_AREA", concept=_XS_CONCEPTS.ref_to("REF_AREA"), representation=sl.Ref.to(_AREAS))],
    measure_dimension=sl.MeasureDimension(
        id="MEASURE", concept=_XS_CONCEPTS.ref_to("MEASURE"), representation=sl.Ref.to(_MEASURES)
    ),
    time_dimension=sl.TimeDimension(concept=_XS_CONCEPTS.ref_to("TIME_PERIOD")),
    measures=[
        sl.Measure(id="OBS_VALUE", concept=_XS_CONCEPTS.ref_to("OBS_VALUE"), representation=sl.Facet(type="Double"))
    ],
)
_XS_FLOW = sl.Dataflow(id="XF", maintainer="AG", name={"en": "x"}, structure=sl.Ref.to(_XS_DSD))
_XS_BINDING = sl.DataflowBinding.from_dsd(
    _XS_DSD, dataflow=sl.Ref.to(_XS_FLOW), schema="data", table="xs", database_role="memory"
)


@pytest.fixture
def xs(registry: sl.LocalRegistry) -> sl.LocalRegistry:
    registry.add_all([_MEASURES, _XS_CONCEPTS, _XS_DSD, _XS_FLOW])
    registry.connection.execute(
        "CREATE TABLE data.xs AS SELECT * FROM (VALUES ('CA', 'EMP', '2020', '1'), ('CA', 'UNEMP', '2020', '2'),"
        " ('US', 'EMP', '2020', '3'), ('CA', 'EMP', '2021', '4')) t(REF_AREA, MEASURE, TIME_PERIOD, OBS_VALUE)"
    )
    registry.add(_XS_BINDING)
    return registry


def _plain(frame: pl.DataFrame) -> list[tuple[object, ...]]:
    return frame.with_columns(pl.col(pl.Enum).cast(pl.String)).rows()


def test_pivot_spreads_the_measure_dimension(xs: sl.LocalRegistry) -> None:
    wide = xs.obs(_XS_FLOW).pivot()
    assert wide.columns == ["REF_AREA", "TIME_PERIOD", "EMP", "UNEMP"]
    assert _plain(wide) == [("CA", "2020", 1.0, 2.0), ("CA", "2021", 4.0, None), ("US", "2020", 3.0, None)]


def test_pivot_on_a_dimension_keeps_the_measure_dimension_in_the_index(xs: sl.LocalRegistry) -> None:
    """Without `MEASURE` in the index, EMP and UNEMP of one series would collide."""
    wide = xs.obs(_XS_FLOW).pivot(on="REF_AREA")
    assert wide.columns == ["MEASURE", "TIME_PERIOD", "CA", "US"]
    assert _plain(wide) == [("EMP", "2020", 1.0, 3.0), ("EMP", "2021", 4.0, None), ("UNEMP", "2020", 2.0, None)]


def test_pivot_lists_columns_as_the_structure_does_even_when_the_data_lacks_one(obs: sl.ObsQuery) -> None:
    """`US` has no row after the filter and is still a column: the codelist decides, not the data."""
    assert obs.filter(obs.col("REF_AREA") == "CA").pivot(on="REF_AREA").columns == ["CA", "US"]


def test_pivot_reads_columns_from_the_data_when_the_structure_cannot_list_them(xs: sl.LocalRegistry) -> None:
    unresolved = attrs.evolve(
        _XS_DSD,
        measure_dimension=attrs.evolve(
            _XS_DSD.measure_dimension, representation=sl.Ref[sl.ConceptScheme].unresolved(_MEASURES.urn)
        ),
    )
    obs = sl.DuckDBDataStore.from_registry(xs).obs(_XS_BINDING, dsd=unresolved)
    assert obs.filter(obs.col("MEASURE") == "UNEMP").pivot().columns == ["REF_AREA", "TIME_PERIOD", "UNEMP"]


def test_pivot_without_an_axis_names_the_candidates(obs: sl.ObsQuery) -> None:
    with pytest.raises(ValueError, match=r"DSD declares no measure dimension\. Pass on= .*: REF_AREA"):
        obs.pivot()


def test_pivot_on_something_that_is_not_a_dimension_says_so(obs: sl.ObsQuery) -> None:
    with pytest.raises(ValueError, match=r"'OBS_VALUE' is not a dimension of DSD"):
        obs.pivot(on="OBS_VALUE")


def test_pivot_needs_a_structure(registry: sl.LocalRegistry) -> None:
    with pytest.raises(ValueError, match="dsd="):
        sl.DuckDBDataStore.from_registry(registry).obs(_BINDING).pivot(on="REF_AREA")
