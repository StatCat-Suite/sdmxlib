"""SDMX-CSV data holding a delete row, an unknown action or a second structure is refused (GH-284).

SDMX-CSV 2.0 carries each row's action in `ACTION`: `I`, `A`, `R` or `D`, and
2.1 adds `M`. A `D` row says what to remove. Read as an observation it reported
deleted data as data, and the frame dropped `ACTION`, so nothing told the two
apart; a SDMX-JSON `Delete` data set is refused the same way. Any other value,
a lowercase `d` or an empty cell, is refused too, since it could be a delete.
Each row names its structure, and a reader reads every row against the first
row's, so rows naming a second structure are refused. Each path that reads
SDMX-CSV checks as the relation is made: `scan_csv` over bytes (a table) and
over a path (a view), and a REST `obs()` (a table).
"""

from pathlib import Path

import pytest

import sdmxlib as sl
from tests.unit import test_rest_registry_concurrency as rest
from tests.unit import test_scan_registry as scan

pytestmark = pytest.mark.unit

_ROWS = "dataflow,AGENCY:DF(1.0.0),{action},{area},2020,1.5\n"


def _csv(*rows: tuple[str, str]) -> str:
    return scan._HEADER + "".join(_ROWS.format(action=action, area=area) for action, area in rows)


@pytest.fixture
def registry():
    with sl.LocalRegistry(":memory:") as registry:
        registry.add_all(scan._ARTEFACTS)
        yield registry


def _scan_relations(registry: sl.LocalRegistry) -> int:
    """How many tables and views the scans' catalog holds."""
    rows = registry.connection.execute(
        "SELECT table_name FROM duckdb_tables() WHERE database_name = 'sdmx_scans' "
        "UNION ALL SELECT view_name FROM duckdb_views() WHERE database_name = 'sdmx_scans' AND NOT internal"
    ).fetchall()
    return len(rows)


@pytest.mark.parametrize("as_bytes", [True, False], ids=["bytes", "path"])
def test_a_scanned_file_with_a_delete_row_is_refused(
    registry: sl.LocalRegistry, tmp_path: Path, as_bytes: bool
) -> None:
    text = _csv(("I", "CA"), ("D", "US"), ("D", "007"))
    path = tmp_path / "data.csv"
    path.write_text(text, encoding="utf-8")
    before = _scan_relations(registry)
    with pytest.raises(sl.SdmxParseError, match=r"holds 2 delete rows \(ACTION D\)"):
        registry.scan_csv(text.encode() if as_bytes else path)
    assert _scan_relations(registry) == before, "the refused relation is dropped"


@pytest.mark.parametrize("action", ["I", "A", "M", "R"])
def test_rows_that_add_or_replace_are_read(registry: sl.LocalRegistry, action: str) -> None:
    frame = registry.scan_csv(_csv((action, "CA"), (action, "US")).encode()).frame()
    assert sorted(frame["AREA"].to_list()) == ["CA", "US"]


@pytest.mark.parametrize("as_bytes", [True, False], ids=["bytes", "path"])
def test_an_action_sdmx_csv_does_not_define_is_refused(
    registry: sl.LocalRegistry, tmp_path: Path, as_bytes: bool
) -> None:
    text = _csv(("I", "CA"), ("d", "US"), ("", "FR"), ("d", "007"))
    path = tmp_path / "data.csv"
    path.write_text(text, encoding="utf-8")
    before = _scan_relations(registry)
    with pytest.raises(
        sl.SdmxParseError,
        match=r"holds ACTION values SDMX-CSV does not define: an empty cell \(1 row\), 'd' \(2 rows\); "
        r"an action is one of I, A, M, R, D",
    ):
        registry.scan_csv(text.encode() if as_bytes else path)
    assert _scan_relations(registry) == before, "the refused relation is dropped"


@pytest.mark.parametrize("as_bytes", [True, False], ids=["bytes", "path"])
def test_rows_naming_a_second_structure_are_refused(registry: sl.LocalRegistry, tmp_path: Path, as_bytes: bool) -> None:
    text = _csv(("I", "CA")) + "dataflow,OTHER:XX(9.0),I,US,2020,1.5\n"
    path = tmp_path / "data.csv"
    path.write_text(text, encoding="utf-8")
    before = _scan_relations(registry)
    with pytest.raises(
        sl.SdmxParseError,
        match=r"names 2 structures \(dataflow AGENCY:DF\(1.0.0\), dataflow OTHER:XX\(9.0\)\); rows following",
    ):
        registry.scan_csv(text.encode() if as_bytes else path)
    assert _scan_relations(registry) == before, "the refused relation is dropped"


def test_a_rest_response_with_a_delete_row_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    body = (
        b"STRUCTURE,STRUCTURE_ID,ACTION,FREQ,CURRENCY,TIME_PERIOD,OBS_VALUE\n"
        b"dataflow,ECB:EXR(1.0),I,M,USD,2021-01,1.2\n"
        b"dataflow,ECB:EXR(1.0),D,M,GBP,2021-01,0.9\n"
    )
    with sl.RestRegistry("https://example.test/service", id="ECB") as reg:
        monkeypatch.setattr(reg, "_resolve_data_structure", rest._resolved)
        monkeypatch.setattr(reg._client, "stream_data_at", lambda _path, _params, *, to: to.write_bytes(body))
        obs = reg.obs(rest._FLOW)
        with pytest.raises(sl.SdmxParseError, match=r"holds 1 delete row \(ACTION D\)"):
            obs.frame()
