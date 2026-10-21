"""A binding from a DSD maps the 2.1 measure dimension (#516).

`DataflowBinding.from_dsd` mapped `dimensions` and the time dimension, and
`dimensions` does not hold a `MeasureDimension` — so a cross-sectional 2.1
dataflow, the shape an LFS-style flow arrives in, lost its measure column:
absent from every frame, from the key, and so from paging's tiebreak, with
one row per measure per series reading as duplicates. `RestRegistry.obs`
binds the table it fetches this way, so every such flow read over REST did.
"""

import pytest

import sdmxlib as sl

pytestmark = pytest.mark.unit

_AREAS = sl.Codelist(
    id="CL_AREA", maintainer="AG", name={"en": "Areas"}, codes=[sl.Code(id="CA", name={"en": "Canada"})]
)
_MEASURES = sl.ConceptScheme(
    id="CS_MEASURES",
    maintainer="AG",
    name={"en": "Measures"},
    concepts=[sl.Concept(id=c, name={"en": c}) for c in ("EMP", "UNEMP")],
)
_CONCEPTS = sl.ConceptScheme(
    id="CS",
    maintainer="AG",
    name={"en": "c"},
    concepts=[sl.Concept(id=c, name={"en": c}) for c in ("REF_AREA", "MEASURE", "TIME_PERIOD", "OBS_VALUE")],
)
_DSD = sl.DataStructure(
    id="DSD",
    maintainer="AG",
    name={"en": "d"},
    dimensions=[sl.Dimension(id="REF_AREA", concept=_CONCEPTS.ref_to("REF_AREA"), representation=sl.Ref.to(_AREAS))],
    measure_dimension=sl.MeasureDimension(
        id="MEASURE", concept=_CONCEPTS.ref_to("MEASURE"), representation=sl.Ref.to(_MEASURES)
    ),
    time_dimension=sl.TimeDimension(concept=_CONCEPTS.ref_to("TIME_PERIOD")),
    measures=[
        sl.Measure(id="OBS_VALUE", concept=_CONCEPTS.ref_to("OBS_VALUE"), representation=sl.Facet(type="Double"))
    ],
)
_FLOW = sl.Dataflow(id="F", maintainer="AG", name={"en": "f"}, structure=sl.Ref.to(_DSD))


def _bind(dsd: sl.DataStructure) -> sl.DataflowBinding:
    return sl.DataflowBinding.from_dsd(dsd, dataflow=sl.Ref.to(_FLOW), schema="data", table="obs")


def test_the_measure_dimension_is_mapped_in_key_order() -> None:
    """After the dimensions, before time — the 2.1 DimensionList's order, and `key_slot_ids`'."""
    assert list(_bind(_DSD).dimension_column_map) == ["REF_AREA", "MEASURE", "TIME_PERIOD"]


def test_a_structure_without_one_maps_as_before() -> None:
    """The discrimination check: nothing is added where there is nothing to add."""
    plain = sl.DataStructure(
        id="DSD",
        maintainer="AG",
        name={"en": "d"},
        dimensions=_DSD.dimensions,
        time_dimension=_DSD.time_dimension,
        measures=_DSD.measures,
    )
    assert list(_bind(plain).dimension_column_map) == ["REF_AREA", "TIME_PERIOD"]


def test_an_observation_frame_carries_the_measure_column() -> None:
    registry = sl.LocalRegistry(":memory:").open()
    try:
        registry.add_all([_AREAS, _MEASURES, _CONCEPTS, _DSD, _FLOW])
        registry.connection.execute("CREATE SCHEMA data")
        registry.connection.execute(
            "CREATE TABLE data.obs AS SELECT * FROM (VALUES ('CA', 'EMP', '2020', 1.0), ('CA', 'UNEMP', '2020', 2.0))"
            " t(REF_AREA, MEASURE, TIME_PERIOD, OBS_VALUE)"
        )
        registry.add(
            sl.DataflowBinding.from_dsd(
                _DSD, dataflow=sl.Ref.to(_FLOW), schema="data", table="obs", database_role="memory"
            )
        )
        obs = registry.obs(_FLOW)
        frame = obs.frame()
        assert frame.columns == ["REF_AREA", "MEASURE", "TIME_PERIOD", "OBS_VALUE"]
        assert frame.get_column("MEASURE").cast(str).to_list() == ["EMP", "UNEMP"]
        assert obs.filter(obs.col("MEASURE") == "UNEMP").frame().get_column("OBS_VALUE").to_list() == [2.0]
    finally:
        registry.close()
