"""What each fuzz target feeds its bytes to, and what counts as a finding (GH-241).

`sl.parse` promises one exception type for input it cannot read:
`SdmxParseError` and its subclasses (`SdmxConformanceError`, `LossyReadError`,
`SdmxErrorMessage`). Any other exception escaping it is a finding, and so is a
hang or unbounded memory, which libFuzzer reports itself. Each target pins the
`version` hint so the fuzzer reaches one reader rather than spending its time
in format detection; `detect` covers the detection.

`fuzz/run.py` drives these under Atheris on Linux. `tests/unit/test_fuzz_crashes.py`
replays every saved crash input through `check`, so a fix is checked by
`just ci` without Atheris.
"""

from collections.abc import Callable
from pathlib import Path

import sdmxlib as sl
from tests.support.repo import ROOT

#: Where a crash input the hosted job found is kept, one directory per target.
CRASHES = ROOT / "tests" / "fuzz" / "crashes"

#: A seed larger than this is left out, and libFuzzer keeps inputs under it.
MAX_LEN = 65536


class UnexpectedReaderError(AssertionError):
    """A reader raised something other than `SdmxParseError`."""


def _parse(version: str | None) -> Callable[[bytes], object]:
    def read(data: bytes) -> object:
        return sl.parse(data, version=version, strict=False)

    return read


#: Each target: the call it makes, and the fixtures it starts from.
TARGETS: dict[str, tuple[Callable[[bytes], object], tuple[str, ...]]] = {
    "ml21": (_parse("2.1"), ("tests/fixtures/sdmx_ml21/*.xml", "tests/integration/fmr/12.4.2/2.1-*/*.xml")),
    "ml30": (
        _parse("3.0"),
        (
            "tests/fixtures/sdmx_ml30/*.xml",
            "tests/integration/fmr/12.4.2/3.0-*/*.xml",
            "tests/golden/render/metadata-3.0.xml",
        ),
    ),
    "ml31": (
        _parse("3.1"),
        (
            "tests/fixtures/sdmx_ml31/*.xml",
            "tests/integration/pysdmx/1.20.0/3.1-*/*.xml",
        ),
    ),
    "json": (
        _parse("json"),
        (
            "tests/fixtures/sdmx_json/*.json",
            "tests/fixtures/sdmx_metadata/*.json",
            "tests/integration/fmr/12.4.2/*-shape/back/AG-json*.json",
            "tests/golden/render/metadata-2.0.json",
        ),
    ),
    "detect": (
        _parse(None),
        (
            "tests/fixtures/sdmx_ml21/minimal.xml",
            "tests/fixtures/sdmx_ml30/minimal.xml",
            "tests/fixtures/sdmx_json/minimal.json",
        ),
    ),
}


def check(target: str, data: bytes) -> None:
    """Feed `data` to `target`'s reader.

    Args:
        target: A key of `TARGETS`.
        data: The input bytes.

    Raises:
        UnexpectedReaderError: The reader raised an exception other than `SdmxParseError`;
            the original is its ``__cause__``.
    """
    read, _ = TARGETS[target]
    try:
        read(data)
    except sl.SdmxParseError:
        pass
    except Exception as exc:
        msg = f"{target}: {type(exc).__name__} escaped sl.parse: {exc}"
        raise UnexpectedReaderError(msg) from exc


def seeds(target: str) -> list[Path]:
    """The fixture files `target` starts from, each no larger than `MAX_LEN`.

    Args:
        target: A key of `TARGETS`.

    Returns:
        The files, sorted.
    """
    _, patterns = TARGETS[target]
    found = {path for pattern in patterns for path in ROOT.glob(pattern) if path.is_file()}
    return sorted(path for path in found if path.stat().st_size <= MAX_LEN)


def crashes() -> list[tuple[str, Path]]:
    """Every saved crash input, with the target that found it.

    Returns:
        ``(target, path)`` pairs, sorted.
    """
    return sorted((path.parent.name, path) for path in CRASHES.glob("*/*") if path.is_file())
