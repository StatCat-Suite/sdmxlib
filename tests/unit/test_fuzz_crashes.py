"""Replay every crash input the fuzzer found, and keep the fuzz targets honest (GH-241).

A crash input lives in `tests/fuzz/crashes/<target>/`, saved from the hosted
job's artifact when its fix lands, and is replayed here through the same
`check` the fuzzer ran, so `just ci` holds the fix without Atheris.
"""

from pathlib import Path

import msgspec
import polars as pl
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

    monkeypatch.setitem(targets.TARGETS, "ml21", (broken, (), targets.TARGETS["ml21"][2]))
    with pytest.raises(targets.UnexpectedReaderError, match=r"KeyError escaped the reader"):
        targets.check("ml21", b"<x/>")


def test_check_accepts_a_parse_error() -> None:
    targets.check("detect", b"not sdmx")


# pysdmx's write-back of the shape fixture (`3.1-shape/back.xml`) drops a
# representation map's codelist endpoints and keeps its two-valued mappings,
# which IM §10.1.2.1 warns on (GH-26). A seed that warns still reads.
@pytest.mark.filterwarnings("ignore::sdmxlib.ImRuleWarning")
@pytest.mark.parametrize("target", sorted(targets.TARGETS))
def test_every_seed_reads_to_something(target: str) -> None:
    """A seed that reads to an empty message starts the fuzzer outside the reader.

    `sl.parse` returns an empty message for JSON that is not SDMX-JSON, so a
    seed that only fails to raise proves nothing; it must yield an artefact or
    a metadata set.
    """
    read, _, _ = targets.TARGETS[target]
    for seed in targets.seeds(target):
        result = read(seed.read_bytes())
        if isinstance(result, sl.StructureMessage):
            assert list(result.all_artefacts()), seed
        elif isinstance(result, sl.MetadataMessage):
            assert result.metadata_sets, seed
        elif isinstance(result, tuple):  # data_json: (frame, dataflow URN)
            frame, urn = result
            assert isinstance(frame, pl.DataFrame)
            assert frame.height, seed
            assert urn, seed
        else:  # data_csv
            assert isinstance(result, pl.DataFrame)
            assert result.height, seed


def test_the_seed_counts() -> None:
    counts = {target: len(targets.seeds(target)) for target in targets.TARGETS}
    assert counts == {"ml21": 6, "ml30": 12, "ml31": 12, "json": 9, "detect": 3, "data_json": 2, "data_csv": 1}


def test_data_csv_allows_a_plain_value_error_and_no_subclass_of_one() -> None:
    """The documented ``ValueError`` passes; msgspec's, which subclasses it, is a finding."""
    _, _, allowed = targets.TARGETS["data_csv"]
    assert allowed(ValueError("names no dataflow"))
    assert allowed(sl.SdmxParseError("unreadable"))
    assert not allowed(msgspec.DecodeError("truncated"))
    assert not allowed(KeyError("x"))
