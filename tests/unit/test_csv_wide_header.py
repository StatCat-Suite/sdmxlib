"""An SDMX-CSV file names its structure however wide its header is (GH-303).

The readers peeked a fixed 4096-character prefix for the header and the first
data row. A header wider than that ended inside the prefix, no data row was
reached, and the file read as naming no structure. That needs about 200
columns with long names, as a wide census table has, which the EGM drift
report's note on the 4096-character head led to.
"""

from pathlib import Path

import pytest

import sdmxlib as sl
from sdmxlib.formats.sdmx_csv.reader import _peek_dataflow_id, _peek_structure
from tests.unit import test_scan_registry as scan

pytestmark = pytest.mark.unit

_EXTRA = [f"ATTRIBUTE_WITH_A_LONG_NAME_{i:03d}" for i in range(150)]


def _wide(*, extra: list[str] = _EXTRA) -> str:
    header = scan._HEADER.rstrip("\n") + "".join(f",{c}" for c in extra) + "\n"
    row = "dataflow,AGENCY:DF(1.0.0),I,CA,2020,1.5" + "".join(",x" for _ in extra) + "\n"
    return header + row


def test_the_header_is_wider_than_the_old_prefix() -> None:
    """The discrimination check: the file below did not fit the 4096 characters."""
    assert len(_wide().splitlines()[0]) > 4096


@pytest.mark.parametrize("as_bytes", [True, False], ids=["bytes", "path"])
def test_a_wide_header_still_names_its_structure(tmp_path: Path, as_bytes: bool) -> None:
    path = tmp_path / "wide.csv"
    path.write_text(_wide(), encoding="utf-8")
    source: bytes | Path = path.read_bytes() if as_bytes else path
    assert _peek_structure(source) == ("dataflow", "AGENCY:DF(1.0.0)")
    assert _peek_dataflow_id(source) == "AGENCY:DF(1.0.0)"


def test_a_wide_file_scans() -> None:
    with sl.LocalRegistry(":memory:") as reg:
        reg.add_all(scan._ARTEFACTS)
        frame = reg.scan_csv(_wide().encode()).frame()
    assert frame["AREA"].to_list() == ["CA"]


def test_a_quoted_newline_in_the_header_is_one_record() -> None:
    """Records, not lines: a quoted field may hold a newline."""
    text = _wide(extra=['"A NOTE\nON TWO LINES"'])
    assert _peek_structure(text.encode()) == ("dataflow", "AGENCY:DF(1.0.0)")


def test_crlf_line_endings_read() -> None:
    """Lines end at a line feed alone, so a carriage return and line feed stay whole."""
    assert _peek_structure(_wide().replace("\n", "\r\n").encode()) == ("dataflow", "AGENCY:DF(1.0.0)")
