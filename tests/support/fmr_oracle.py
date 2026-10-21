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

#: The FMR version the recordings were made against; one directory per version.
FMR_VERSION = "12.4.2"
RECORDINGS = find_root() / "tests" / "integration" / "fmr" / FMR_VERSION

CASES: tuple[str, ...] = ("2.1-maximal", "2.1-shape", "3.0-maximal", "3.0-shape")
FORMATS: dict[str, str] = {"ml21": "xml", "ml30": "xml", "json10": "json", "json20": "json"}


def case_dir(case: str) -> Path:
    """One case's recordings."""
    return RECORDINGS / case


@functools.cache
def served(case: str, fmt: str) -> tuple[dict[str, object], dict[str, str]]:
    """What FMR served for the test agencies in one format, read by our reader.

    Returns:
        The artefacts read, by `key`, and the reason our reader gave for each
        one it refused, by `identity`.
    """
    statuses = load_json(case_dir(case) / "back" / "status.json")
    assert isinstance(statuses, dict)
    read: dict[str, object] = {}
    refused: dict[str, str] = {}
    for name, status in sorted(statuses.items()):
        if name.endswith(f"-{fmt}") and status == HTTPStatus.OK:
            document = (case_dir(case) / "back" / f"{name}.{FORMATS[fmt]}").read_bytes()
            read |= artefacts(document)
            refused |= refusals(document)
    return read, refused


@functools.cache
def sent(case: str) -> dict[str, object]:
    """What was sent, read by our reader."""
    return artefacts((case_dir(case) / "sent.xml").read_bytes())


#: Which `formats.gaps` target describes each format FMR serves.
TARGETS: dict[str, str] = {"ml21": "2.1", "ml30": "3.0", "json10": "json", "json20": "json"}


@functools.cache
def differences(case: str, fmt: str) -> set[str]:
    """What FMR's copy, served in `fmt`, does not carry of what was sent (`interop.compare`)."""
    theirs, refused = served(case, fmt)
    return compare(sent(case), theirs, refused, TARGETS[fmt])


def every_difference() -> Iterable[tuple[str, str, str]]:
    """(case, format, entry) for every difference in every recording."""
    for case in CASES:
        for fmt in FORMATS:
            for entry in sorted(differences(case, fmt)):
                yield case, fmt, entry
