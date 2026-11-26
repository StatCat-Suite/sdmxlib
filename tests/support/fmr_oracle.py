"""Reading the FMR oracle's recordings: what we sent, and what FMR served back (#457).

`bench/fmr` records against a live Fusion Metadata Registry; this reads the
recordings, offline, for `tests/integration/test_fmr_oracle.py`, and compares
them as `tests.support.interop` does every external oracle.
"""

import functools
from collections.abc import Iterable
from http import HTTPStatus
from pathlib import Path

from tests.support.interop import artefacts, compare, load_json, refusals
from tests.support.repo import find_root

#: The FMR versions recorded, oldest first; one directory per version.
FMR_VERSIONS: tuple[str, ...] = ("12.4.2", "12.5.0")
RECORDINGS = find_root() / "tests" / "integration" / "fmr"


def recordings(fmr: str) -> Path:
    """The recordings made against FMR `fmr`."""
    return RECORDINGS / fmr


CASES: tuple[str, ...] = ("2.1-maximal", "2.1-shape", "3.0-maximal", "3.0-shape")
FORMATS: dict[str, str] = {"ml21": "xml", "ml30": "xml", "json10": "json", "json20": "json"}


def case_dir(fmr: str, case: str) -> Path:
    """One case's recordings against FMR `fmr`."""
    return recordings(fmr) / case


@functools.cache
def served(fmr: str, case: str, fmt: str) -> tuple[dict[str, object], dict[str, str]]:
    """What FMR served for the test agencies in one format, read by our reader.

    Returns:
        The artefacts read, by `key`, and the reason our reader gave for each
        one it refused, by `identity`.
    """
    statuses = load_json(case_dir(fmr, case) / "back" / "status.json")
    assert isinstance(statuses, dict)
    read: dict[str, object] = {}
    refused: dict[str, str] = {}
    for name, status in sorted(statuses.items()):
        if name.endswith(f"-{fmt}") and status == HTTPStatus.OK:
            document = (case_dir(fmr, case) / "back" / f"{name}.{FORMATS[fmt]}").read_bytes()
            read |= artefacts(document)
            refused |= refusals(document)
    return read, refused


@functools.cache
def sent(fmr: str, case: str) -> dict[str, object]:
    """What was sent to FMR `fmr`, read by our reader."""
    return artefacts((case_dir(fmr, case) / "sent.xml").read_bytes())


#: Which `formats.gaps` target describes each format FMR serves.
TARGETS: dict[str, str] = {"ml21": "2.1", "ml30": "3.0", "json10": "json", "json20": "json"}


@functools.cache
def differences(fmr: str, case: str, fmt: str) -> set[str]:
    """What FMR's copy, served in `fmt`, does not carry of what was sent (`interop.compare`)."""
    theirs, refused = served(fmr, case, fmt)
    return compare(sent(fmr, case), theirs, refused, TARGETS[fmt])


def every_difference(fmr: str) -> Iterable[tuple[str, str, str]]:
    """(case, format, entry) for every difference in FMR `fmr`'s recordings."""
    for case in CASES:
        for fmt in FORMATS:
            for entry in sorted(differences(fmr, case, fmt)):
                yield case, fmt, entry
