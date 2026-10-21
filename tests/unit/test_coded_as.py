"""Every bad code, with a count — the report #408 asked for, two ways.

An Enum-typed read refuses a value its codelist does not list, and it refuses
on the *first* one: a file with 400 bad codes across three columns reports one.
Two questions, two answers, and #408 was right that both are wanted:

* *Is this file usable at all?* — the `pl.Enum` schema. Cheap, fails fast, and
  the right default for a pipeline. `ObsQuery.frame()` types this way.
* *What exactly is wrong with it, and how much?* — a read that lets the bad
  values in, so a report can name every one with a count. `obs.validate()`
  reads the raw rows for this (#516); a frame read with polars directly gets
  the same from `schema(dsd, coded_as="string")` and `validation_report`.

The report's row order is not what these compare — they compare sets.
"""

from pathlib import Path

import polars as pl
import pytest

import sdmxlib as sl
import sdmxlib.polars as slpl

pytestmark = pytest.mark.unit

A, V = "AGENCY", "1.0.0"

_CSV = (
    "STRUCTURE,STRUCTURE_ID,ACTION,AREA,TIME_PERIOD,OBS_VALUE\n"
    "dataflow,AGENCY:DF(1.0.0),I,CA,2020-01,1.0\n"
    "dataflow,AGENCY:DF(1.0.0),I,XX,2020-02,2.0\n"
    "dataflow,AGENCY:DF(1.0.0),I,YY,2020-03,3.0\n"
    "dataflow,AGENCY:DF(1.0.0),I,XX,2020-04,4.0\n"
)


@pytest.fixture
def structures() -> list[sl.MaintainableArtefact]:
    concepts = sl.ConceptScheme(
        id="CS",
        maintainer=A,
        version=V,
        name={"en": "C"},
        concepts=[sl.Concept(id=i, name={"en": i}) for i in ("AREA", "TIME_PERIOD", "OBS_VALUE")],
    )
    codelist = sl.Codelist(
        id="CL_AREA", maintainer=A, version=V, name={"en": "Area"}, codes=[sl.Code(id="CA", name={"en": "Canada"})]
    )
    dsd = sl.DataStructure(
        id="DSD",
        maintainer=A,
        version=V,
        name={"en": "DSD"},
        dimensions=[sl.Dimension(id="AREA", concept=concepts.ref_to("AREA"), representation=sl.Ref.to(codelist))],
        time_dimension=sl.TimeDimension(concept=concepts.ref_to("TIME_PERIOD")),
        measures=[sl.Measure(id="OBS_VALUE", concept=concepts.ref_to("OBS_VALUE"))],
    )
    flow = sl.Dataflow(id="DF", maintainer=A, version=V, name={"en": "f"}, structure=sl.Ref.to(dsd))
    return [concepts, codelist, dsd, flow]


@pytest.fixture
def dsd(structures: list[sl.MaintainableArtefact]) -> sl.DataStructure:
    (found,) = (s for s in structures if isinstance(s, sl.DataStructure))
    return found


@pytest.fixture
def registry(structures: list[sl.MaintainableArtefact]):
    with sl.LocalRegistry(":memory:") as registry:
        registry.add_all(structures)
        yield registry


@pytest.fixture
def csv_path(tmp_path: Path) -> Path:
    path = tmp_path / "data.csv"
    _ = path.write_text(_CSV, encoding="utf-8")
    return path


def _violations(report: pl.DataFrame) -> set[tuple[str, str, int]]:
    return {(r["column"], r["invalid_value"], r["count"]) for r in report.to_dicts()}


# ── the default fails fast ────────────────────────────────────────────────


def test_a_typed_read_fails_fast_on_a_bad_code(registry: sl.LocalRegistry, csv_path: Path) -> None:
    """The behaviour #408 calls correct for a pipeline: the read refuses."""
    with pytest.raises(ValueError, match="does not fit its component's type"):
        registry.scan_csv(csv_path).frame()


def test_the_default_schema_is_enum_typed(dsd: sl.DataStructure) -> None:
    assert slpl.schema(dsd)["AREA"] == pl.Enum(["CA"])
    assert isinstance(slpl.schema(dsd, coded_as="string")["AREA"], pl.String)


# ── the report #408 asked for ─────────────────────────────────────────────


@pytest.mark.parametrize("as_bytes", [False, True], ids=["path", "bytes"])
def test_validate_reports_every_bad_code_with_a_count(
    registry: sl.LocalRegistry, csv_path: Path, as_bytes: bool
) -> None:
    """Three bad rows over two distinct values, both named — where the typed read raised on the first."""
    report = registry.scan_csv(csv_path.read_bytes() if as_bytes else csv_path).validate()
    assert _violations(report) == {("AREA", "XX", 2), ("AREA", "YY", 1)}


def test_a_polars_read_reports_the_same(dsd: sl.DataStructure, csv_path: Path) -> None:
    """`coded_as="string"` lets the bad values into a frame read directly, so its report agrees."""
    frame = pl.read_csv(csv_path, schema_overrides=slpl.schema(dsd, coded_as="string"))
    assert _violations(slpl.validation_report(dsd, frame)) == {("AREA", "XX", 2), ("AREA", "YY", 1)}
    assert frame["AREA"].to_list() == ["CA", "XX", "YY", "XX"], "the good rows survive"


def test_a_clean_file_reports_nothing(registry: sl.LocalRegistry, tmp_path: Path) -> None:
    """The discrimination check: the report is the bad codes, not every code."""
    clean = tmp_path / "clean.csv"
    clean.write_text(_CSV.splitlines()[0] + "\ndataflow,AGENCY:DF(1.0.0),I,CA,2020-01,1.0\n", encoding="utf-8")
    assert registry.scan_csv(clean).validate().is_empty()
