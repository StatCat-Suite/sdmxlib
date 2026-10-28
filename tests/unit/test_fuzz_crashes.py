"""Replay every crash input the fuzzer found, and keep the fuzz targets honest (GH-241).

A crash input lives in `tests/fuzz/crashes/<target>/`, saved from the hosted
job's artifact when its fix lands, and is replayed here through the same
`check` the fuzzer ran, so `just ci` holds the fix without Atheris.
"""

from pathlib import Path

import pytest

import sdmxlib as sl
from tests.fuzz import targets

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(("target", "path"), targets.crashes(), ids=lambda v: v.name if isinstance(v, Path) else v)
def test_a_saved_crash_input_is_read_or_refused(target: str, path: Path) -> None:
    targets.check(target, path.read_bytes())


def test_every_crash_directory_names_a_target() -> None:
    names = {path.name for path in targets.CRASHES.iterdir() if path.is_dir()} if targets.CRASHES.exists() else set()
    assert names <= set(targets.TARGETS)


def test_check_reports_an_exception_other_than_a_parse_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sabotage: the replay starts with no crash inputs, so prove `check` can fail."""

    def broken(data: bytes) -> object:
        raise KeyError(data)

    monkeypatch.setitem(targets.TARGETS, "ml21", (broken, ()))
    with pytest.raises(targets.UnexpectedReaderError, match=r"KeyError escaped sl\.parse"):
        targets.check("ml21", b"<x/>")


def test_check_accepts_a_parse_error() -> None:
    targets.check("detect", b"not sdmx")


@pytest.mark.parametrize("target", sorted(targets.TARGETS))
def test_every_seed_reads_to_something(target: str) -> None:
    """A seed that reads to an empty message starts the fuzzer outside the reader.

    `sl.parse` returns an empty message for JSON that is not SDMX-JSON, so a
    seed that only fails to raise proves nothing; it must yield an artefact or
    a metadata set.
    """
    read, _ = targets.TARGETS[target]
    for seed in targets.seeds(target):
        message = read(seed.read_bytes())
        if isinstance(message, sl.StructureMessage):
            assert list(message.all_artefacts()), seed
        else:
            assert isinstance(message, sl.MetadataMessage)
            assert message.metadata_sets, seed


def test_the_seed_counts() -> None:
    counts = {target: len(targets.seeds(target)) for target in targets.TARGETS}
    assert counts == {"ml21": 6, "ml30": 12, "ml31": 12, "json": 9, "detect": 3}
