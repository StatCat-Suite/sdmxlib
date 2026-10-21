"""An observation result is typed from the DSD, as a `Dataset` is (#516).

The query compiler is structure-agnostic, so `ObsQuery.frame()` returned
whatever DuckDB held — `VARCHAR` codes where a `Dataset` of the same data had
the codelist's `pl.Enum`. Now both type a component from what the DSD
declares. What it does not declare keeps its stored type: `schema`'s
fallback, `pl.String`, would turn a stored `DOUBLE` into text, which is the
case `test_an_undeclared_component_keeps_its_stored_type` pins.

The store below holds every column as something other than its DSD type, so
each assertion can only pass if the cast happened.
"""

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
    concepts=[sl.Concept(id=c, name={"en": c}) for c in ("REF_AREA", "OBS_VALUE", "WEIGHT")],
)
_DSD = sl.DataStructure(
    id="DSD",
    maintainer="AG",
    name={"en": "d"},
    dimensions=[sl.Dimension(id="REF_AREA", concept=_CONCEPTS.ref_to("REF_AREA"), representation=sl.Ref.to(_AREAS))],
    measures=[
        sl.Measure(id="OBS_VALUE", concept=_CONCEPTS.ref_to("OBS_VALUE"), representation=sl.Facet(type="Double")),
        # Declares nothing: its stored DOUBLE must survive.
        sl.Measure(id="WEIGHT", concept=_CONCEPTS.ref_to("WEIGHT")),
    ],
)
_FLOW = sl.Dataflow(id="F", maintainer="AG", name={"en": "f"}, structure=sl.Ref.to(_DSD))


def _store(rows: str) -> sl.LocalRegistry:
    registry = sl.LocalRegistry(":memory:").open()
    registry.add_all([_AREAS, _CONCEPTS, _DSD, _FLOW])
    registry.connection.execute("CREATE SCHEMA data")
    # Codes and the Double measure stored as text; the undeclared one as DOUBLE.
    registry.connection.execute(
        f"CREATE TABLE data.obs AS SELECT * FROM (VALUES {rows}) t(REF_AREA, OBS_VALUE, WEIGHT)"
    )
    registry.add(
        sl.DataflowBinding.from_dsd(_DSD, dataflow=sl.Ref.to(_FLOW), schema="data", table="obs", database_role="memory")
    )
    return registry


@pytest.fixture
def obs():
    registry = _store("('CA', '1.5', 2.0::DOUBLE), ('US', '2.5', 3.0::DOUBLE)")
    try:
        yield registry.obs(_FLOW)
    finally:
        registry.close()


def test_a_frame_is_typed_from_the_dsd(obs) -> None:
    schema = obs.frame().schema
    assert schema["REF_AREA"] == pl.Enum(["CA", "US"])
    assert schema["OBS_VALUE"] == pl.Float64


def test_an_undeclared_component_keeps_its_stored_type(obs) -> None:
    """`WEIGHT` declares nothing; `schema`'s String fallback must not reach it."""
    assert obs.frame().schema["WEIGHT"] == pl.Float64


def test_a_frame_types_as_a_dataset_reads(obs) -> None:
    """The point: one rule, whichever surface — compared with `sdmxlib.polars.schema` itself."""
    expected = slpl.schema(_DSD)
    schema = obs.frame().schema
    assert {c: schema[c] for c in ("REF_AREA", "OBS_VALUE")} == {c: expected[c] for c in ("REF_AREA", "OBS_VALUE")}


def test_a_projection_is_typed_under_its_alias(obs) -> None:
    frame = obs.select(area=obs.col("REF_AREA"), v=obs.col("OBS_VALUE")).collect()
    assert (frame.schema["area"], frame.schema["v"]) == (pl.Enum(["CA", "US"]), pl.Float64)


def test_an_aggregate_is_not_a_component(obs) -> None:
    frame = obs.group_by(obs.col("REF_AREA")).agg(n=sl.len()).collect()
    assert frame.schema["REF_AREA"] == pl.Enum(["CA", "US"])
    assert frame.schema["n"] == pl.Int64


def test_a_code_the_codelist_does_not_list_fails_fast_naming_the_way_out() -> None:
    registry = _store("('ZZ', '1.0', 1.0::DOUBLE)")
    try:
        with pytest.raises(ValueError, match=r"does not fit its component's type") as caught:
            registry.obs(_FLOW).frame()
        assert ".stream()" in str(caught.value)
        assert "validation_report" in str(caught.value)
    finally:
        registry.close()


def test_a_structural_query_is_untouched() -> None:
    """The discrimination check: a root with no DSD is not typed."""
    registry = _store("('CA', '1.0', 1.0::DOUBLE)")
    try:
        assert registry.query(sl.q.dataflow).frame().schema["resource_id"] == pl.String
    finally:
        registry.close()
