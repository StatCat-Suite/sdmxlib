"""A data file read through a registry is an `ObsQuery` (#516).

`reg.scan_csv(source)` and `reg.scan_json(source)` answer with the query a store's own observations do,
so a file gets `pivot`, `validate`, the sinks and labels through one surface
rather than through `Dataset`. A path is a view, read on every query; bytes are
loaded once, as an SDMX-JSON message always is. All live in an attached `:memory:` catalog, so a file-backed
store is never written to. `sdmxlib.local._scan` has the reasoning.
"""

import json
from pathlib import Path
from typing import Literal

import duckdb
import httpx
import polars as pl
import pytest

import sdmxlib as sl
from sdmxlib.formats.sdmx_ml21.writer import write as write21

pytestmark = pytest.mark.unit

A, V = "AGENCY", "1.0.0"
_HEADER = "STRUCTURE,STRUCTURE_ID,ACTION,AREA,TIME_PERIOD,OBS_VALUE\n"

_CONCEPTS = sl.ConceptScheme(
    id="CS",
    maintainer=A,
    version=V,
    name={"en": "C"},
    concepts=[sl.Concept(id=i, name={"en": i}) for i in ("AREA", "TIME_PERIOD", "OBS_VALUE")],
)
_AREAS = sl.Codelist(
    id="CL_AREA",
    maintainer=A,
    version=V,
    name={"en": "Area"},
    codes=[sl.Code(id=c, name={"en": c}) for c in ("CA", "US", "007", "10")],
)
_DSD = sl.DataStructure(
    id="DSD",
    maintainer=A,
    version=V,
    name={"en": "DSD"},
    dimensions=[sl.Dimension(id="AREA", concept=_CONCEPTS.ref_to("AREA"), representation=sl.Ref.to(_AREAS))],
    time_dimension=sl.TimeDimension(concept=_CONCEPTS.ref_to("TIME_PERIOD")),
    measures=[
        sl.Measure(id="OBS_VALUE", concept=_CONCEPTS.ref_to("OBS_VALUE"), representation=sl.Facet(type="Double"))
    ],
)
_FLOW = sl.Dataflow(id="DF", maintainer=A, version=V, name={"en": "f"}, structure=sl.Ref.to(_DSD))
_AGREEMENT = sl.ProvisionAgreement(id="PA", maintainer=A, version=V, name={"en": "pa"}, dataflow=sl.Ref.to(_FLOW))
_ARTEFACTS = [_CONCEPTS, _AREAS, _DSD, _FLOW, _AGREEMENT]


def _rows(*rows: tuple[str, str, str], kind: str = "dataflow", identifier: str = "AGENCY:DF(1.0.0)") -> str:
    return _HEADER + "".join(f"{kind},{identifier},I,{area},{time},{value}\n" for area, time, value in rows)


_TWO = _rows(("CA", "2020", "1.5"), ("US", "2020", "2.5"))


@pytest.fixture
def registry():
    with sl.LocalRegistry(":memory:") as registry:
        registry.add_all(_ARTEFACTS)
        yield registry


def _write(tmp_path: Path, text: str, name: str = "data.csv") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_a_file_is_an_observation_query(registry: sl.LocalRegistry, tmp_path: Path) -> None:
    obs = registry.scan_csv(_write(tmp_path, _TWO))
    assert isinstance(obs, sl.ObsQuery)
    assert obs.structure == _DSD
    frame = obs.frame()
    assert frame.columns == ["AREA", "TIME_PERIOD", "OBS_VALUE"], "the SDMX-CSV envelope is not a component"
    area = frame.schema["AREA"]
    assert isinstance(area, pl.Enum)
    assert set(area.categories) == {"CA", "US", "007", "10"}
    assert frame.get_column("OBS_VALUE").to_list() == [1.5, 2.5]


def test_a_measure_compares_as_a_number(registry: sl.LocalRegistry, tmp_path: Path) -> None:
    """Typed in the view, not only in the result: `'10' > '9'` is false as text."""
    obs = registry.scan_csv(_write(tmp_path, _rows(("CA", "2020", "9"), ("US", "2020", "10"))))
    assert obs.filter(obs.col("OBS_VALUE") > 9).frame().get_column("AREA").cast(str).to_list() == ["US"]


@pytest.mark.parametrize("as_bytes", [False, True], ids=["path", "bytes"])
def test_a_code_that_reads_as_a_number_stays_a_code(registry: sl.LocalRegistry, tmp_path: Path, as_bytes: bool) -> None:
    """DuckDB would read `10` as a BIGINT; a scan types every code column as text.

    `'10' < '2'` holds as text and not as a number, so the comparison says which.
    """
    text = _rows(("10", "2020", "1"))
    obs = registry.scan_csv(text.encode() if as_bytes else _write(tmp_path, text))
    assert obs.filter(obs.col("AREA") < "2").frame().get_column("AREA").cast(str).to_list() == ["10"]


def test_a_path_is_read_in_place(registry: sl.LocalRegistry, tmp_path: Path) -> None:
    """A view, not a copy: the next query reads what the file holds then."""
    path = _write(tmp_path, _TWO)
    obs = registry.scan_csv(path)
    assert obs.frame().height == 2
    path.write_text(_rows(("CA", "2020", "1")), encoding="utf-8")
    assert obs.frame().height == 1


def test_bytes_read_as_the_path_does(registry: sl.LocalRegistry, tmp_path: Path) -> None:
    from_path = registry.scan_csv(_write(tmp_path, _TWO)).frame()
    assert registry.scan_csv(_TWO.encode()).frame().equals(from_path)


def test_two_files_of_one_dataflow_are_two_queries(registry: sl.LocalRegistry, tmp_path: Path) -> None:
    first = registry.scan_csv(_write(tmp_path, _TWO, "a.csv"))
    second = registry.scan_csv(_write(tmp_path, _rows(("CA", "2021", "9")), "b.csv"))
    assert (first.frame().height, second.frame().height) == (2, 1)


def test_a_path_that_needs_quoting_is_read(registry: sl.LocalRegistry, tmp_path: Path) -> None:
    """The path is the one literal in the view's DDL."""
    assert registry.scan_csv(_write(tmp_path, _TWO, "it's here.csv")).frame().height == 2


def test_a_file_backed_store_is_not_written_to(tmp_path: Path) -> None:
    store = tmp_path / "store.duckdb"
    with sl.LocalRegistry(store) as registry:
        registry.add_all(_ARTEFACTS)
        before = registry.connection.execute("SELECT count(*) FROM duckdb_views() WHERE NOT internal").fetchone()
        assert registry.scan_csv(_write(tmp_path, _TWO)).frame().height == 2
    conn = duckdb.connect(str(store), read_only=True)
    try:
        after = conn.execute("SELECT count(*) FROM duckdb_views() WHERE NOT internal").fetchone()
        assert after == before
    finally:
        conn.close()


def test_the_terminals_work_on_a_file(registry: sl.LocalRegistry, tmp_path: Path) -> None:
    obs = registry.scan_csv(_write(tmp_path, _rows(("CA", "2020", "1"), ("ZZ", "2020", "2"))))
    assert obs.validate().rows() == [("AREA", "ZZ", 1)]
    assert obs.filter(obs.col("AREA") == "CA").pivot(on="AREA").columns == ["TIME_PERIOD", "CA", "US", "007", "10"]


@pytest.mark.parametrize(
    ("kind", "identifier"), [("dataflow", "AGENCY:DF(1.0.0)"), ("dataprovision", "AGENCY:PA(1.0.0)")]
)
def test_a_dataflow_or_its_agreement_names_the_rows(
    registry: sl.LocalRegistry, tmp_path: Path, kind: str, identifier: str
) -> None:
    obs = registry.scan_csv(_write(tmp_path, _rows(("CA", "2020", "1"), kind=kind, identifier=identifier)))
    assert obs.frame().height == 1


def test_sdmx_csv_1_0_names_its_dataflow_in_another_column(registry: sl.LocalRegistry, tmp_path: Path) -> None:
    obs = registry.scan_csv(_write(tmp_path, "DATAFLOW,AREA,TIME_PERIOD,OBS_VALUE\nAGENCY:DF(1.0.0),CA,2020,1\n"))
    assert obs.frame().columns == ["AREA", "TIME_PERIOD", "OBS_VALUE"]


def test_a_data_structure_alone_is_refused(registry: sl.LocalRegistry, tmp_path: Path) -> None:
    """A binding is over a dataflow, and a structure does not say which — so no guess."""
    path = _write(tmp_path, _rows(("CA", "2020", "1"), kind="datastructure", identifier="AGENCY:DSD(1.0.0)"))
    with pytest.raises(ValueError, match="names the data structure DSD, not a dataflow"):
        registry.scan_csv(path)


def test_a_dataflow_this_registry_lacks_is_refused(registry: sl.LocalRegistry, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not in this registry"):
        registry.scan_csv(_write(tmp_path, _rows(("CA", "2020", "1"), identifier="AGENCY:GONE(1.0.0)")))


def test_a_rest_registry_scans_with_the_structure_it_fetches(tmp_path: Path) -> None:
    """One request — the dataflow with its descendants — and no data fetch: the rows are the file's."""
    requests: list[httpx.Request] = []
    body = write21(
        sl.StructureMessage(dataflows=[_FLOW], data_structures=[_DSD], codelists=[_AREAS], concept_schemes=[_CONCEPTS])
    )

    def serve(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=body)

    with sl.RestRegistry("https://example.test/service", id="T", transport=httpx.MockTransport(serve)) as reg:
        obs = reg.scan_csv(_write(tmp_path, _TWO))
        assert obs.structure == _DSD
        assert obs.frame().get_column("OBS_VALUE").to_list() == [1.5, 2.5]
        obs.sink_json(tmp_path / "m.json", lang="en")
        assert reg.scan_json(tmp_path / "m.json").frame().equals(obs.frame())
    assert [r.url.path for r in requests] == ["/service/dataflow/AGENCY/DF/1.0.0"], "the second scan reuses the flow"


# ── SDMX-JSON: decoded, then loaded once ──────────────────────────────────────


type _Version = Literal["1.0", "2.0"]
_VERSIONS: list[_Version] = ["2.0", "1.0"]


def _object(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return value


def _message(registry: sl.LocalRegistry, version: _Version, tmp_path: Path) -> dict[str, object]:
    """What this library writes for `_TWO`, as JSON to edit."""
    path = tmp_path / f"m{version}.json"
    registry.scan_csv(_TWO.encode()).sink_json(path, lang="en", version=version)
    return _object(json.loads(path.read_text()))


def _data(message: dict[str, object], version: _Version) -> dict[str, object]:
    """Where 2.0 nests the payload, and 1.0 does not."""
    return _object(message["data"]) if version == "2.0" else message


def _data_set(message: dict[str, object], version: _Version) -> dict[str, object]:
    data_sets = _data(message, version)["dataSets"]
    assert isinstance(data_sets, list)
    return _object(data_sets[0])


def _structure(message: dict[str, object], version: _Version) -> dict[str, object]:
    data = _data(message, version)
    if version == "1.0":
        return _object(data["structure"])
    structures = data["structures"]
    assert isinstance(structures, list)
    return _object(structures[0])


@pytest.mark.parametrize("version", _VERSIONS)
@pytest.mark.parametrize("as_bytes", [False, True], ids=["path", "bytes"])
def test_a_json_message_reads_as_its_csv_does(
    registry: sl.LocalRegistry, tmp_path: Path, version: _Version, as_bytes: bool
) -> None:
    text = json.dumps(_message(registry, version, tmp_path))
    source = text.encode() if as_bytes else _write(tmp_path, text, "in.json")
    obs = registry.scan_json(source)
    assert obs.structure == _DSD
    assert obs.frame().equals(registry.scan_csv(_TWO.encode()).frame())


@pytest.mark.parametrize("version", _VERSIONS)
def test_the_dataflow_may_be_linked_from_the_structure_block(
    registry: sl.LocalRegistry, tmp_path: Path, version: _Version
) -> None:
    """Where some providers put it; this library's writers put it on the dataSet."""
    message = _message(registry, version, tmp_path)
    _structure(message, version)["links"] = _data_set(message, version).pop("links")
    assert registry.scan_json(json.dumps(message).encode()).frame().height == 2


def test_a_measure_sent_as_text_compares_as_a_number(registry: sl.LocalRegistry, tmp_path: Path) -> None:
    """Typed when loaded, as a CSV's view is: `'10' > '9'` is false as text."""
    message = _message(registry, "2.0", tmp_path)
    _data_set(message, "2.0")["observations"] = {"0:0": ["9"], "1:0": ["10"]}
    obs = registry.scan_json(json.dumps(message).encode())
    assert obs.filter(obs.col("OBS_VALUE") > 9).frame().get_column("AREA").cast(str).to_list() == ["US"]


def test_a_message_that_names_no_dataflow_is_refused(registry: sl.LocalRegistry, tmp_path: Path) -> None:
    message = _message(registry, "2.0", tmp_path)
    _data_set(message, "2.0").pop("links")
    with pytest.raises(ValueError, match='names no dataflow: no dataSet or structure link has rel="dataflow"'):
        registry.scan_json(json.dumps(message).encode())


def test_a_dataflow_this_registry_lacks_is_refused_for_json_too(registry: sl.LocalRegistry, tmp_path: Path) -> None:
    message = _message(registry, "2.0", tmp_path)
    _data_set(message, "2.0")["links"] = [{"rel": "dataflow", "urn": str(sl.Dataflow.urn_for(agency=A, id="GONE"))}]
    with pytest.raises(ValueError, match=r"GONE.*is not in this registry"):
        registry.scan_json(json.dumps(message).encode())
