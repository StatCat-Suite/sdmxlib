"""An observation column refuses a literal of the wrong kind, as it is built (#511).

`obs.col(id)` is `Untyped` statically — one signature for every id cannot
promise a type — and checked at runtime: what the column holds comes from the
DSD (an enumerated component holds codes; a `Facet` its declared type) and,
where the DSD says nothing, from the store's column type. A number compared
with text was a DuckDB binder error naming neither the component nor the fix
— or, for ``=``, no rows at all, silently.

`obs.text(id)` / `obs.num(id)` are the static half (design §6.3): exact
`Str` / `Num`, with the same kind checked when they are asked for.
"""

from datetime import UTC, datetime

import polars as pl
import pytest

import sdmxlib as sl
from sdmxlib.local.data_store import column_types

pytestmark = pytest.mark.unit

_AREAS = sl.Codelist(
    id="CL_AREA",
    maintainer="AG",
    name={"en": "Areas"},
    codes=[sl.Code(id=c, name={"en": c}) for c in ("CA", "US")],
)
_CONCEPTS = sl.ConceptScheme(
    id="CS",
    maintainer="AG",
    name={"en": "c"},
    concepts=[sl.Concept(id=c, name={"en": c}) for c in ("REF_AREA", "TIME_PERIOD", "OBS_VALUE", "WEIGHT", "SCORE")],
)
_DSD = sl.DataStructure(
    id="DSD",
    maintainer="AG",
    name={"en": "d"},
    dimensions=[sl.Dimension(id="REF_AREA", concept=_CONCEPTS.ref_to("REF_AREA"), representation=sl.Ref.to(_AREAS))],
    time_dimension=sl.TimeDimension(concept=_CONCEPTS.ref_to("TIME_PERIOD")),
    measures=[
        # No type in the DSD, stored as text: the issue's case.
        sl.Measure(id="OBS_VALUE", concept=_CONCEPTS.ref_to("OBS_VALUE")),
        # No type in the DSD, stored as DOUBLE — and under another column name.
        sl.Measure(id="WEIGHT", concept=_CONCEPTS.ref_to("WEIGHT")),
        # Declared a number.
        sl.Measure(id="SCORE", concept=_CONCEPTS.ref_to("SCORE"), representation=sl.Facet(type="Double")),
    ],
)
_FLOW = sl.Dataflow(id="F", maintainer="AG", name={"en": "f"}, structure=sl.Ref.to(_DSD))
_STAMP = datetime(2026, 1, 1, tzinfo=UTC)


def _binding(table: str) -> sl.DataflowBinding:
    return sl.DataflowBinding(
        dataflow=sl.Ref.to(_FLOW),
        database_role="memory",
        table_location=f"data.{table}",
        dimension_column_map={"REF_AREA": "REF_AREA", "TIME_PERIOD": "TIME_PERIOD"},
        measure_column_map={"OBS_VALUE": "OBS_VALUE", "WEIGHT": "W", "SCORE": "SCORE"},
        attribute_column_map={},
        structural_version="AG:DSD(1.0)",
        source_registry="local",
        materialized_at=_STAMP,
        bound_at=_STAMP,
    )


@pytest.fixture
def registry():
    with sl.LocalRegistry(":memory:") as registry:
        registry.add_all([_AREAS, _CONCEPTS, _DSD, _FLOW])
        registry.connection.execute("CREATE SCHEMA data")
        registry.connection.execute(
            "CREATE TABLE data.obs AS SELECT * FROM (VALUES ('CA', '2020', '1.0', 2.0::DOUBLE, 3.0::DOUBLE),"
            " ('US', '2021', '2.0', 5.0::DOUBLE, 1.0::DOUBLE)) t(REF_AREA, TIME_PERIOD, OBS_VALUE, W, SCORE)"
        )
        registry.add(_binding("obs"))
        yield registry


@pytest.fixture
def obs(registry: sl.LocalRegistry) -> sl.ObsQuery:
    return registry.obs(_FLOW)


# ── obs.col: refused as the predicate is built ───────────────────────────────


@pytest.mark.parametrize(
    ("component", "build", "says"),
    [
        pytest.param(
            "REF_AREA", lambda c: c == 1, r"REF_AREA is enumerated by CL_AREA, so its values are text", id="code-eq"
        ),
        pytest.param("REF_AREA", lambda c: c.is_in([1, 2]), r"REF_AREA is enumerated by CL_AREA", id="code-in"),
        pytest.param("TIME_PERIOD", lambda c: c >= 2020, r"TIME_PERIOD is declared ObservationalTimePeriod", id="time"),
        pytest.param(
            "OBS_VALUE",
            lambda c: c > 1.5,
            r"OBS_VALUE has no type in its DSD and is stored as VARCHAR.*declare its Facet \(e\.g\. Double\)",
            id="stored-text-gt",
        ),
        pytest.param("OBS_VALUE", lambda c: c == 1, r"stored as VARCHAR", id="stored-text-eq-the-silent-one"),
        pytest.param("SCORE", lambda c: c == "3", r"SCORE is declared Double, so its values are numbers", id="number"),
    ],
)
def test_a_literal_of_the_wrong_kind_is_refused(obs: sl.ObsQuery, component: str, build, says: str) -> None:
    with pytest.raises(TypeError, match=says):
        build(obs.col(component))


@pytest.mark.parametrize(
    ("component", "build", "areas"),
    [
        pytest.param("REF_AREA", lambda c: c == "CA", ["CA"], id="code"),
        pytest.param("TIME_PERIOD", lambda c: c >= "2021", ["US"], id="time"),
        pytest.param("OBS_VALUE", lambda c: c == "2.0", ["US"], id="stored-text"),
        pytest.param("WEIGHT", lambda c: c > 3, ["US"], id="stored-double"),
        pytest.param("SCORE", lambda c: c > 2.5, ["CA"], id="declared-double"),
    ],
)
def test_a_literal_of_the_right_kind_runs(obs: sl.ObsQuery, component: str, build, areas: list[str]) -> None:
    """The discrimination check: each kind still answers when compared as it should be."""
    assert obs.filter(build(obs.col(component))).frame().get_column("REF_AREA").cast(pl.String).to_list() == areas


def test_a_store_table_not_yet_created_is_typed_as_its_fetch_will_create_it(registry: sl.LocalRegistry) -> None:
    """A remote table exists only after its first fetch; `column_types` says what it will force.

    Codes are forced to text, so a number against one is refused before the
    fetch. A measure the DSD leaves untyped is not forced — its fetch infers it
    (#518) — so nothing is known of it yet, and nothing is refused.
    """
    store = sl.DuckDBDataStore.from_registry(registry)
    obs = store.obs(_binding("not_fetched_yet"), dsd=_DSD)
    with pytest.raises(TypeError, match=r"REF_AREA is enumerated by CL_AREA"):
        obs.filter(obs.col("REF_AREA") == 1)
    assert obs.col("OBS_VALUE").kind is None
    assert obs.num("OBS_VALUE") is not None


def test_an_untyped_measure_scanned_from_numbers_compares_as_one(registry: sl.LocalRegistry) -> None:
    """The #518 case: an untyped ``OBS_VALUE`` read from a file of numbers is a number, not text."""
    csv = "STRUCTURE,STRUCTURE_ID,ACTION,REF_AREA,TIME_PERIOD,OBS_VALUE,WEIGHT,SCORE\n" + "".join(
        f"dataflow,AG:F(1.0),I,{area},2020,{value},1,1\n" for area, value in (("CA", "9"), ("US", "10"))
    )
    obs = registry.scan_csv(csv.encode())
    kind = obs.col("OBS_VALUE").kind
    assert kind is not None
    assert kind.holds == "num", f"inferred from the values: {kind.because}"
    assert obs.filter(obs.num("OBS_VALUE") > 9).frame().get_column("REF_AREA").cast(pl.String).to_list() == ["US"]


def test_with_no_dsd_the_store_decides_alone(registry: sl.LocalRegistry) -> None:
    obs = sl.DuckDBDataStore.from_registry(registry).obs(_binding("obs"))
    with pytest.raises(TypeError, match=r"REF_AREA has no type in its DSD and is stored as VARCHAR"):
        _ = obs.col("REF_AREA") == 1
    assert obs.filter(obs.col("WEIGHT") > 3).frame().height == 1


# ── obs.text / obs.num: exact static types, the same kind checked ────────────


def test_num_and_text_answer_as_col_does(obs: sl.ObsQuery) -> None:
    by_num = obs.filter(obs.num("WEIGHT") > 3).frame()
    by_text = obs.filter(obs.text("REF_AREA") == "US").frame()
    assert by_num.equals(by_text)
    assert by_num.get_column("REF_AREA").cast(pl.String).to_list() == ["US"]


def test_a_typed_column_projects_under_its_component_id(obs: sl.ObsQuery) -> None:
    """`WEIGHT` is stored as `W`; a frame names components, whichever accessor was used."""
    assert obs.select(obs.num("WEIGHT"), obs.text("REF_AREA")).collect().columns == ["WEIGHT", "REF_AREA"]
    assert obs.agg(total=obs.num("WEIGHT").sum()).collect().item() == 7.0


@pytest.mark.parametrize(
    ("ask", "says"),
    [
        pytest.param(
            lambda o: o.num("REF_AREA"), r"REF_AREA is enumerated by CL_AREA.*obs\.text\('REF_AREA'\)", id="num-code"
        ),
        pytest.param(
            lambda o: o.num("OBS_VALUE"), r"stored as VARCHAR.*obs\.text\('OBS_VALUE'\)", id="num-stored-text"
        ),
        pytest.param(lambda o: o.text("SCORE"), r"SCORE is declared Double.*obs\.num\('SCORE'\)", id="text-number"),
    ],
)
def test_asking_for_the_wrong_kind_says_which_to_ask_for(obs: sl.ObsQuery, ask, says: str) -> None:
    with pytest.raises(TypeError, match=says):
        ask(obs)


def test_an_unknown_component_is_refused_by_name(obs: sl.ObsQuery) -> None:
    with pytest.raises(KeyError, match="NOPE"):
        obs.num("NOPE")


def test_a_refetch_that_changes_an_inferred_type_is_seen(registry: sl.LocalRegistry, tmp_path) -> None:
    """An inferred column's type can change when a fetch replaces its table, so the cached one is dropped."""
    store = sl.DuckDBDataStore.from_registry(registry)
    binding = _binding("refetched")
    header = "REF_AREA,TIME_PERIOD,OBS_VALUE,W,SCORE\n"
    path = tmp_path / "data.csv"
    for values, holds in (("1.5", "num"), ("n/a", "str")):
        path.write_text(header + f"CA,2020,{values},1,1\n", encoding="utf-8")
        store.ingest_csv(
            schema="data", table="refetched", path=path, types=column_types(binding, _DSD), lake_alias="memory"
        )
        kind = store.obs(binding, dsd=_DSD).col("OBS_VALUE").kind
        assert kind is not None
        assert kind.holds == holds
