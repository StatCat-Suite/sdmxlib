"""What reading observations raises (GH-247).

Bytes that are not a readable data message raise `SdmxParseError`, as
`sl.parse` does for a structure message; a value that does not fit its
component's type raises the one ``ValueError`` naming `.validate()`. Both
hold for `scan_json`, `scan_csv` of bytes or of a path (a lazy view, so its
rows fail when a query runs), and a REST `obs()` response.
`sdmxlib._read_errors` has the reasoning.
"""

import json
from pathlib import Path

import duckdb
import pyarrow as pa
import pytest

import sdmxlib as sl
from sdmxlib._read_errors import csv_errors
from sdmxlib.formats.sdmx_json.data import read_observations
from tests.support import render_corpus
from tests.support.repo import ROOT
from tests.unit.test_rest_observations import _FLOW, _resolved
from tests.unit.test_scan_registry import _ARTEFACTS, _rows

pytestmark = pytest.mark.unit

_GOLDEN = ROOT / "tests" / "golden" / "render"
_CSV = (_GOLDEN / "data-2.0.0.csv").read_text()
_TYPE_MISMATCH = r"does not fit its component's type in DSD"

#: Enough rows that DuckDB's sniffer settles the columns on good ones, so a
#: bad row at the end fails when a query reads it, not when the view is made.
_MANY = [("CA", str(2000 + n), f"{n}.5") for n in range(30_000)]


@pytest.fixture
def registry():
    with sl.LocalRegistry(":memory:") as registry:
        registry.add_all(render_corpus.data_flow())
        yield registry


@pytest.fixture
def typed():
    """A registry whose structure declares `OBS_VALUE` a Double, so text there does not fit."""
    with sl.LocalRegistry(":memory:") as registry:
        registry.add_all(_ARTEFACTS)
        yield registry


def _bad_value(*, rows: list[tuple[str, str, str]] | None = None) -> bytes:
    """Rows with one measure that is not a number, last."""
    return _rows(*(rows or []), ("CA", "2021", "abc")).encode()


def _bad_bytes() -> bytes:
    """The golden file with a row that is not UTF-8."""
    return _CSV.encode() + "dataflow,AG:DF_RENDER(1.0),I,A,\xff,2022,1,A\n".encode("latin-1")


# ── SDMX-JSON ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "data",
    [b"", b"{", b"[]", b'{"data": {"dataSets": 5}}', b'{"data": null}', b"\xff\xfe"],
    ids=["empty", "truncated", "array", "wrong-shape", "no-data", "not-utf8"],
)
def test_bytes_that_are_not_a_json_data_message_are_a_parse_error(data: bytes) -> None:
    with pytest.raises(sl.SdmxParseError):
        read_observations(data)


def test_an_observation_key_outside_the_declaration_is_a_parse_error() -> None:
    message = json.loads((_GOLDEN / "data-2.0.json").read_bytes())
    observations = message["data"]["dataSets"][0]["observations"]
    key = next(iter(observations))
    observations[":".join(["99"] * len(key.split(":")))] = observations.pop(key)

    with pytest.raises(sl.SdmxParseError, match="position 99 is outside"):
        read_observations(json.dumps(message).encode())


def test_a_measure_of_integers_and_decimals_reads() -> None:
    """A JSON number written ``1`` beside ``-2.25`` is one Float64 column, not a polars TypeError."""
    message = json.loads((_GOLDEN / "data-2.0.json").read_bytes())
    observations = message["data"]["dataSets"][0]["observations"]
    first = next(iter(observations))
    observations[first][0] = 1
    frame, _ = read_observations(json.dumps(message).encode())
    values = frame["OBS_VALUE"].to_list()
    assert 1.0 in values
    assert -2.25 in values


def test_scan_json_of_a_bad_message_is_a_parse_error(registry: sl.LocalRegistry) -> None:
    with pytest.raises(sl.SdmxParseError):
        registry.scan_json(b'{"data": {"dataSets": []}}')


# ── SDMX-CSV, bytes: read in full when scanned ────────────────────────────


def test_a_csv_value_that_does_not_fit_is_the_type_error(typed: sl.LocalRegistry) -> None:
    with pytest.raises(ValueError, match=_TYPE_MISMATCH) as caught:
        typed.scan_csv(_bad_value())
    assert not isinstance(caught.value, sl.SdmxParseError)


def test_csv_bytes_that_do_not_read_are_a_parse_error(registry: sl.LocalRegistry) -> None:
    with pytest.raises(sl.SdmxParseError, match="could not be read"):
        registry.scan_csv(_bad_bytes())


def test_a_csv_without_the_measure_column_is_refused_at_scan(registry: sl.LocalRegistry) -> None:
    """An untyped measure is not forced, so only this check stops a file without it."""
    rows = [line.split(",") for line in _CSV.splitlines()]
    at = rows[0].index("OBS_VALUE")
    text = "\n".join(",".join(row[:at] + row[at + 1 :]) for row in rows) + "\n"

    with pytest.raises(sl.SdmxParseError, match="has no OBS_VALUE column"):
        registry.scan_csv(text.encode())


def test_a_csv_header_python_cannot_split_is_a_parse_error(registry: sl.LocalRegistry) -> None:
    with pytest.raises(sl.SdmxParseError, match="header could not be read"):
        registry.scan_csv(b"STRUCTURE,STRUCTURE_ID,TIME_PER\rIOD\ndataflow,AG:DF_RENDER(1.0),2020\n")


# ── SDMX-CSV, a path: a view DuckDB reads when a query runs ───────────────


def _path(tmp_path: Path, data: bytes) -> Path:
    path = tmp_path / "data.csv"
    path.write_bytes(data)
    return path


def _drain(obs: sl.ObsQuery, terminal: str) -> None:
    if terminal == "frame":
        obs.frame()
    elif terminal == "stream":
        pa.Table.from_batches(obs.stream())
    else:
        obs.count()


@pytest.mark.parametrize("terminal", ["frame", "stream"])
def test_a_scanned_path_raises_the_type_error_when_queried(
    typed: sl.LocalRegistry, tmp_path: Path, terminal: str
) -> None:
    obs = typed.scan_csv(_path(tmp_path, _bad_value(rows=_MANY)))
    with pytest.raises(ValueError, match=_TYPE_MISMATCH):
        _drain(obs, terminal)


@pytest.mark.parametrize("terminal", ["frame", "stream", "count"])
def test_a_scanned_path_raises_a_parse_error_when_queried(
    typed: sl.LocalRegistry, tmp_path: Path, terminal: str
) -> None:
    text = _rows(*_MANY) + "dataflow,AGENCY:DF(1.0.0),I,CA,2021,1,EXTRA,MORE\n"
    obs = typed.scan_csv(_path(tmp_path, text.encode()))
    with pytest.raises(sl.SdmxParseError, match="could not be read"):
        _drain(obs, terminal)


def test_a_good_scanned_path_reads(typed: sl.LocalRegistry, tmp_path: Path) -> None:
    """Discrimination: the files above differ from this one by their last row."""
    obs = typed.scan_csv(_path(tmp_path, _rows(*_MANY).encode()))
    assert obs.frame().height == len(_MANY)
    assert obs.count() == len(_MANY)


class _Terminal:
    """A stand-in query terminal that raises what it is given, as DuckDB would."""

    root: object = None

    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    @csv_errors
    def run(self) -> None:
        raise self.exc


def test_a_duckdb_error_that_is_not_a_csv_reads_keeps_its_type() -> None:
    """Discrimination: a failed cast in a query's own SQL is a ConversionException too, and passes through."""
    with pytest.raises(duckdb.ConversionException, match="Could not convert"):
        _Terminal(duckdb.ConversionException("Conversion Error: Could not convert string 'CA' to INT32")).run()


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (duckdb.ConversionException("Conversion Error: CSV Error on Line: 2"), ValueError),
        (duckdb.InvalidInputException("Invalid Input Error: CSV Error on Line: 2"), sl.SdmxParseError),
    ],
    ids=["conversion", "invalid-input"],
)
def test_a_csv_readers_error_converts(exc: Exception, expected: type[Exception]) -> None:
    with pytest.raises(expected) as caught:
        _Terminal(exc).run()
    assert caught.value.__cause__ is exc


def test_a_file_that_names_no_structure_is_a_plain_value_error(registry: sl.LocalRegistry, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="names no structure") as caught:
        registry.scan_csv(_path(tmp_path, b"A,B\n1,2\n"))
    assert not isinstance(caught.value, sl.SdmxParseError)


# ── a REST response ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("body", "expected", "match"),
    [
        (b"DATAFLOW,FREQ,CURRENCY,TIME_PERIOD,OBS_VALUE\nECB:EXR(1.0),M,USD,2021-01,abc\n", ValueError, r"fit"),
        (b"DATAFLOW,FREQ,CURRENCY,TIME_PERIOD,OBS_VALUE\nECB:EXR(1.0),M,\xff,2021-01,1\n", sl.SdmxParseError, "read"),
    ],
    ids=["value-does-not-fit", "not-utf8"],
)
def test_a_rest_response_that_does_not_read(
    monkeypatch: pytest.MonkeyPatch, body: bytes, expected: type[Exception], match: str
) -> None:
    with sl.RestRegistry("https://example.test/service", id="ECB") as registry:
        monkeypatch.setattr(registry, "_resolve_data_structure", _resolved)

        def stream(path: str, params: object, *, to: Path) -> None:
            to.write_bytes(body)

        monkeypatch.setattr(registry._client, "stream_data_at", stream)
        with pytest.raises(expected, match=match):
            registry.obs(_FLOW).frame()
