"""The data writers convert each batch with `pl.from_arrow`, not the constructor (#427).

4f65fd7 swapped `pl.from_arrow(batch)` for `pl.DataFrame(batch)` in both data
writers for its return type. The frames are identical, so every output test
stayed green while SDMX-CSV encoding got 1.33x slower per batch and a
consumer's bulk downloads ~17% slower end to end. A timing assertion would be
flaky, so this counts the conversions instead: each writer must go through
`from_arrow` exactly once per batch. Revert either writer to the constructor
and its count drops to zero.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import polars as pl
import pyarrow as pa
import pytest

from sdmxlib.formats._arrow import frame_of
from sdmxlib.formats.sdmx_csv import write_sdmx_csv
from sdmxlib.formats.sdmx_json.data_v2 import write_sdmx_json_data
from tests.support.factories import FREQ_CODES_NAMED, data_dsd, dsd_binding

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.unit

_BATCHES = [
    pa.record_batch({"FREQ": ["A"], "REF_AREA": ["CA"], "OBS_VALUE": [1.0]}),
    pa.record_batch({"FREQ": ["Q", "M"], "REF_AREA": ["US", "FR"], "OBS_VALUE": [2.0, 3.0]}),
    pa.record_batch({"FREQ": ["A"], "REF_AREA": ["MX"], "OBS_VALUE": [4.0]}),
]


@pytest.fixture
def from_arrow_calls(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Every argument `pl.from_arrow` is called with while the test runs."""
    calls: list[object] = []
    real: Callable[..., pl.DataFrame | pl.Series] = pl.from_arrow

    def spy(data: object, *args: object, **kwargs: object) -> pl.DataFrame | pl.Series:
        calls.append(data)
        return real(data, *args, **kwargs)

    monkeypatch.setattr(pl, "from_arrow", spy)
    return calls


def test_frame_of_is_the_same_frame_as_the_constructor() -> None:
    for batch in _BATCHES:
        assert frame_of(batch).equals(pl.DataFrame(batch))


def test_frame_of_takes_the_fast_path_for_a_record_batch(from_arrow_calls: list[object]) -> None:
    df = frame_of(_BATCHES[0])
    assert from_arrow_calls == [_BATCHES[0]]
    assert isinstance(df, pl.DataFrame)


def test_the_csv_writer_converts_each_batch_with_from_arrow(from_arrow_calls: list[object]) -> None:
    dsd, _ = data_dsd(freq_codes=FREQ_CODES_NAMED)
    chunks = list(write_sdmx_csv(_BATCHES, dsd, structure_id="TEST:DF_TEST(1.0)"))
    assert len(chunks) == len(_BATCHES)
    assert from_arrow_calls == _BATCHES


def test_the_json_writer_converts_each_batch_with_from_arrow(from_arrow_calls: list[object]) -> None:
    dsd, flow = data_dsd(freq_codes=FREQ_CODES_NAMED)
    binding = dsd_binding(dsd, flow)
    out = b"".join(
        write_sdmx_json_data(
            _BATCHES,
            dsd,
            binding.dataflow,
            message_id="m",
            prepared=datetime(2026, 6, 16, tzinfo=UTC),
            sender_id="S",
            lang="en",
        )
    )
    assert out
    assert from_arrow_calls == _BATCHES
