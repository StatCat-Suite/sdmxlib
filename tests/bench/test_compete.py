"""Competitive parse + serialize benchmarks: sdmxlib vs sdmx1 vs pysdmx.

Apples-to-apples on real-world SDMX-ML 2.1 fixtures across three Python
SDMX libraries. Skips automatically if sdmx1 / pysdmx are not installed
— install them via ``uv sync --group bench-compete``.

Fixtures:

- ``ecb_data_structure.xml`` (~490 KB) — ECB DataStructure.
- ``estat_hicp_descendants.xml.gz`` (~3.8 MB uncompressed) — ESTAT PRC_HICP_MIDX
  dataflow with full ``references=descendants``: DSD + codelists +
  concept schemes + dataflow + agency schemes. Sourced from
  https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1, CC-BY 4.0.

Fixture-choice constraints across the three libs:

- ``minimal.xml`` uses ``<ReleaseCalendar>`` and other 2.1 features
  ``sdmx1`` does not implement; ``with_hierarchy.xml`` likewise.
- ``ecb_dataflow.xml`` carries a ``ContentConstraint type=…`` attribute
  ``pysdmx`` does not accept.
- The two fixtures above round-trip cleanly through all three libs.
"""

import gzip
import io
from pathlib import Path

import pytest

# sdmxlib (always available)
from sdmxlib.formats import parse as sdmxlib_parse
from sdmxlib.formats import serialize as sdmxlib_serialize

# Optional competitors — auto-skip the whole module if absent.
sdmx = pytest.importorskip("sdmx", reason="install via uv sync --group bench-compete")
pysdmx_io = pytest.importorskip("pysdmx.io", reason="install via uv sync --group bench-compete")
PysdmxFormat = pytest.importorskip("pysdmx.io.format", reason="install via uv sync --group bench-compete").Format
pysdmx_write = pytest.importorskip("pysdmx.io.writer", reason="install via uv sync --group bench-compete").write_sdmx

_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "sdmx_ml21"

_FIXTURES = [
    pytest.param("ecb_data_structure.xml", id="ecb-490k"),
    pytest.param("estat_hicp_descendants.xml.gz", id="estat-hicp-3.8m"),
]


def _load_fixture(name: str) -> bytes:
    path = _FIXTURES_DIR / name
    if name.endswith(".gz"):
        with gzip.open(path, "rb") as f:
            return f.read()
    return path.read_bytes()


@pytest.fixture(scope="session", params=_FIXTURES)
def fixture_bytes(request: pytest.FixtureRequest) -> bytes:
    return _load_fixture(request.param)


@pytest.fixture(scope="session")
def fixture_str(fixture_bytes: bytes) -> str:
    return fixture_bytes.decode()


# ── Parse ────────────────────────────────────────────────────────────────────


def test_compete_parse_sdmxlib(benchmark, fixture_bytes: bytes) -> None:
    benchmark(sdmxlib_parse, fixture_bytes)


def test_compete_parse_sdmx1(benchmark, fixture_bytes: bytes) -> None:
    """sdmx1.read_sdmx wants a file-like; BytesIO wrap is unavoidable."""

    def run() -> object:
        message: object = sdmx.read_sdmx(io.BytesIO(fixture_bytes))
        return message

    benchmark(run)


def test_compete_parse_pysdmx(benchmark, fixture_str: str) -> None:
    benchmark(pysdmx_io.read_sdmx, fixture_str)


# ── Serialize ────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def sdmxlib_msg(fixture_bytes: bytes):
    return sdmxlib_parse(fixture_bytes)


@pytest.fixture(scope="session")
def sdmx1_msg(fixture_bytes: bytes):
    return sdmx.read_sdmx(io.BytesIO(fixture_bytes))


@pytest.fixture(scope="session")
def pysdmx_maintainables(fixture_str: str) -> list[object]:
    """pysdmx writes lists of maintainables, not Message objects."""
    msg = pysdmx_io.read_sdmx(fixture_str)
    return [
        *msg.get_codelists(),
        *msg.get_concept_schemes(),
        *msg.get_data_structure_definitions(),
        *msg.get_agency_schemes(),
        *msg.get_category_schemes(),
        *msg.get_categorisations(),
        *msg.get_data_constraints(),
        *msg.get_hierarchies(),
        *msg.get_representation_maps(),
        *msg.get_dataflows(),
    ]


def test_compete_serialize_sdmxlib(benchmark, sdmxlib_msg) -> None:
    benchmark(sdmxlib_serialize, sdmxlib_msg, version="2.1")


def test_compete_serialize_sdmx1(benchmark, sdmx1_msg) -> None:
    benchmark(sdmx.to_xml, sdmx1_msg)


def test_compete_serialize_pysdmx(benchmark, pysdmx_maintainables: list[object]) -> None:
    benchmark(pysdmx_write, pysdmx_maintainables, PysdmxFormat.STRUCTURE_SDMX_ML_2_1)


# ── Serialize to SDMX-JSON 2.1 (sdmxlib vs pysdmx only — sdmx1 has no
#    JSON writer) ────────────────────────────────────────────────────────────


def test_compete_serialize_json_sdmxlib(benchmark, sdmxlib_msg) -> None:
    benchmark(sdmxlib_serialize, sdmxlib_msg, version="json")


def test_compete_serialize_json_pysdmx(benchmark, pysdmx_maintainables: list[object], request) -> None:
    """Skipped on the ECB DSD fixture — pysdmx's SDMX-JSON 2.1 writer crashes
    with ``ValueError: substring not found`` on that input (their bug, not
    ours)."""
    fixture_id = request.node.callspec.id
    if fixture_id.startswith("ecb"):
        pytest.skip("pysdmx STRUCTURE_SDMX_JSON_2_1_0 writer crashes on the ECB DSD fixture")
    benchmark(pysdmx_write, pysdmx_maintainables, PysdmxFormat.STRUCTURE_SDMX_JSON_2_1_0)
