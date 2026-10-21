"""Reading the pysdmx oracle's recordings: what we sent, and what pysdmx wrote back (#457).

`bench/pysdmx_oracle` had pysdmx read our SDMX-ML 3.1 documents and write its
reading back as 3.1; this reads the recordings, offline, for
`tests/integration/test_pysdmx_oracle.py`, and compares them as
`tests.support.interop` does every external oracle.
"""

import functools
from collections.abc import Iterable
from pathlib import Path

from tests.support.interop import artefacts, compare, refusals
from tests.support.repo import find_root

#: The pysdmx version the recordings were made with; one directory per version.
PYSDMX_VERSION = "1.20.0"
RECORDINGS = find_root() / "tests" / "integration" / "pysdmx" / PYSDMX_VERSION

CASES: tuple[str, ...] = ("3.1-maximal", "3.1-shape")
#: pysdmx is asked for one format, SDMX-ML 3.1; named as `fmr_oracle` names its formats.
FORMAT = "ml31"


def case_dir(case: str) -> Path:
    """One case's recordings."""
    return RECORDINGS / case


@functools.cache
def sent(case: str) -> dict[str, object]:
    """What was sent, read by our reader."""
    return artefacts((case_dir(case) / "sent.xml").read_bytes())


@functools.cache
def differences(case: str) -> set[str]:
    """What pysdmx's copy does not carry of what was sent (`interop.compare`)."""
    back = (case_dir(case) / "back.xml").read_bytes()
    return compare(sent(case), artefacts(back), refusals(back), "3.1")


def every_difference() -> Iterable[tuple[str, str, str]]:
    """(case, format, entry) for every difference in every recording."""
    for case in CASES:
        for entry in sorted(differences(case)):
            yield case, FORMAT, entry
