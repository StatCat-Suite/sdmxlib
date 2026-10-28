"""What each fuzz target feeds its bytes to, and what counts as a finding (GH-241, GH-247).

`sl.parse` promises one exception type for input it cannot read:
`SdmxParseError` and its subclasses (`SdmxConformanceError`, `LossyReadError`,
`SdmxErrorMessage`). The structure targets pin the `version` hint so the
fuzzer reaches one reader rather than spending its time in format detection;
`detect` covers the detection.

The data targets read observations. `data_json` decodes an SDMX-JSON data
message and allows `SdmxParseError` alone. `data_csv` scans an SDMX-CSV file
through a registry and allows a plain ``ValueError`` too: a file that names no
dataflow the registry holds, or a value that does not fit its component's
type, raises one by contract. The check is on the exact type, because
msgspec's and polars' errors subclass ``ValueError`` and are findings.

Any other exception escaping is a finding, and so is a hang or unbounded
memory, which libFuzzer reports itself.

`fuzz/run.py` drives these under Atheris on Linux. `tests/unit/test_fuzz_crashes.py`
replays every saved crash input through `check`, so a fix is checked by
`just ci` without Atheris.
"""

import functools
from collections.abc import Callable
from pathlib import Path

import sdmxlib as sl
from sdmxlib.formats.sdmx_json.data import read_observations
from tests.support import render_corpus
from tests.support.repo import ROOT

#: Where a crash input the hosted job found is kept, one directory per target.
CRASHES = ROOT / "tests" / "fuzz" / "crashes"

#: A seed larger than this is left out, and libFuzzer keeps inputs under it.
MAX_LEN = 65536


class UnexpectedReaderError(AssertionError):
    """A reader raised an exception its target does not allow."""


def _parse_error(exc: BaseException) -> bool:
    return isinstance(exc, sl.SdmxParseError)


def _parse_or_value_error(exc: BaseException) -> bool:
    return isinstance(exc, sl.SdmxParseError) or type(exc) is ValueError


@functools.cache
def _registry() -> sl.LocalRegistry:
    """The registry `data_csv` scans through: `DF_RENDER`, which the golden data files observe.

    Opened once and left open for the life of the process, as a fuzzer runs.
    """
    registry = sl.LocalRegistry(":memory:")
    registry.open()
    registry.add_all(render_corpus.data_flow())
    return registry


def _scan_csv(data: bytes) -> object:
    return _registry().scan_csv(data).frame()


def _parse(version: str | None) -> Callable[[bytes], object]:
    def read(data: bytes) -> object:
        return sl.parse(data, version=version, strict=False)

    return read


#: Each target: the call it makes, the fixtures it starts from, and the exceptions it allows.
TARGETS: dict[str, tuple[Callable[[bytes], object], tuple[str, ...], Callable[[BaseException], bool]]] = {
    "ml21": (
        _parse("2.1"),
        ("tests/fixtures/sdmx_ml21/*.xml", "tests/integration/fmr/12.4.2/2.1-*/*.xml"),
        _parse_error,
    ),
    "ml30": (
        _parse("3.0"),
        (
            "tests/fixtures/sdmx_ml30/*.xml",
            "tests/integration/fmr/12.4.2/3.0-*/*.xml",
            "tests/golden/render/metadata-3.0.xml",
        ),
        _parse_error,
    ),
    "ml31": (
        _parse("3.1"),
        ("tests/fixtures/sdmx_ml31/*.xml", "tests/integration/pysdmx/1.20.0/3.1-*/*.xml"),
        _parse_error,
    ),
    "json": (
        _parse("json"),
        (
            "tests/fixtures/sdmx_json/*.json",
            "tests/fixtures/sdmx_metadata/*.json",
            "tests/integration/fmr/12.4.2/*-shape/back/AG-json*.json",
            "tests/golden/render/metadata-2.0.json",
        ),
        _parse_error,
    ),
    "detect": (
        _parse(None),
        (
            "tests/fixtures/sdmx_ml21/minimal.xml",
            "tests/fixtures/sdmx_ml30/minimal.xml",
            "tests/fixtures/sdmx_json/minimal.json",
        ),
        _parse_error,
    ),
    "data_json": (read_observations, ("tests/golden/render/data-*.json",), _parse_error),
    "data_csv": (_scan_csv, ("tests/golden/render/data-*.csv",), _parse_or_value_error),
}


def check(target: str, data: bytes) -> None:
    """Feed `data` to `target`'s reader.

    Args:
        target: A key of `TARGETS`.
        data: The input bytes.

    Raises:
        UnexpectedReaderError: The reader raised an exception the target does
            not allow; the original is its ``__cause__``.
    """
    read, _, allowed = TARGETS[target]
    try:
        read(data)
    except Exception as exc:
        if allowed(exc):
            return
        msg = f"{target}: {type(exc).__name__} escaped the reader: {exc}"
        raise UnexpectedReaderError(msg) from exc


def seeds(target: str) -> list[Path]:
    """The fixture files `target` starts from, each no larger than `MAX_LEN`.

    Args:
        target: A key of `TARGETS`.

    Returns:
        The files, sorted.
    """
    _, patterns, _ = TARGETS[target]
    found = {path for pattern in patterns for path in ROOT.glob(pattern) if path.is_file()}
    return sorted(path for path in found if path.stat().st_size <= MAX_LEN)


def crashes() -> list[tuple[str, Path]]:
    """Every saved crash input, with the target that found it.

    Returns:
        ``(target, path)`` pairs, sorted.
    """
    return sorted((path.parent.name, path) for path in CRASHES.glob("*/*") if path.is_file())
