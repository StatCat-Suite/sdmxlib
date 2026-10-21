"""Tests for the TIME_PERIOD value-entry ``start`` / ``end`` emission.

Two layers:

1. Unit tests for `_period_bounds` covering every supported period
   format (annual, monthly, daily, quarterly, semester) and the
   non-handled cases that must return ``None``.
2. End-to-end writer tests that build a DSD with a formal
   ``TimeDimension``, encode observations, and assert the
   ``structure.dimensions.observation[TIME_PERIOD].values`` entries
   carry ISO 8601 ``start`` / ``end`` strings parseable by
   ``datetime.fromisoformat`` — the closest stdlib analogue to
   ``date-fns.parseISO`` that downstream consumers like .Stat use.
3. A .Stat-shape semantic regression test that mimics
   ``refineTimePeriod``'s control flow in Python and asserts no
   ``Invalid Date``-equivalent failures for any value the writer
   emitted. Catches the schema-valid-but-broken class of bug that
   plain JSON-Schema validation missed in production.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import msgspec
import pyarrow as pa
import pytest

from sdmxlib.formats.sdmx_json._data_common import _period_bounds
from sdmxlib.formats.sdmx_json.data_v2 import write_sdmx_json_data
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure, Dimension, Measure, TimeDimension
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.ref import Ref
from tests.support.factories import concept_ref, dsd_binding

if TYPE_CHECKING:
    from collections.abc import Iterable

pytestmark = pytest.mark.unit


# ── _period_bounds unit coverage ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("period_id", "start", "end"),
    [
        ("2021", "2021-01-01T00:00:00", "2021-12-31T23:59:59"),
        ("2021-07", "2021-07-01T00:00:00", "2021-07-31T23:59:59"),
        ("2024-02", "2024-02-01T00:00:00", "2024-02-29T23:59:59"),  # leap year
        ("2023-02", "2023-02-01T00:00:00", "2023-02-28T23:59:59"),  # non-leap
        ("2021-07-15", "2021-07-15T00:00:00", "2021-07-15T23:59:59"),
        ("2021-Q1", "2021-01-01T00:00:00", "2021-03-31T23:59:59"),
        ("2021-Q3", "2021-07-01T00:00:00", "2021-09-30T23:59:59"),
        ("2021-Q4", "2021-10-01T00:00:00", "2021-12-31T23:59:59"),
        ("2021-S1", "2021-01-01T00:00:00", "2021-06-30T23:59:59"),
        ("2021-S2", "2021-07-01T00:00:00", "2021-12-31T23:59:59"),
    ],
)
def test_period_bounds_supported_shapes(period_id: str, start: str, end: str) -> None:
    assert _period_bounds(period_id) == (start, end)


@pytest.mark.parametrize(
    "period_id",
    [
        "2021-01-01/P5Y",  # SDMX <start>/<duration> form — consumer parses it
        "nonsense",
        "21",  # too few digits
        "2021-13",  # invalid month
        "",
    ],
)
def test_period_bounds_unsupported_returns_none(period_id: str) -> None:
    """Unsupported shapes return None so the writer doesn't fabricate
    bogus dates. ``<start>/<duration>`` is intentionally None too —
    downstream parsers like ``.Stat`` handle that shape natively."""
    assert _period_bounds(period_id) is None


# ── writer integration ───────────────────────────────────────────────────────


def _build_dsd_with_time() -> tuple[DataStructure, Dataflow]:
    """DSD with a formal TimeDimension — exercises the dsd.time_dimension
    path that data_v2's writer uses to identify which dim gets bounds."""
    cl_freq = Codelist(
        id="CL_FREQ",
        maintainer="TEST",
        version="1.0",
        name=InternationalString(en="Frequency"),
        codes=[Code(name={"en": "A"}, id="A")],
    )
    dsd = DataStructure(
        id="DSD_TIME",
        maintainer="TEST",
        version="1.0",
        name=InternationalString(en="DSD with TIME_PERIOD"),
        dimensions=[Dimension(id="FREQ", representation=Ref[Codelist].to(cl_freq), concept=concept_ref("FREQ"))],
        time_dimension=TimeDimension(id="TIME_PERIOD", concept=concept_ref("TIME_PERIOD")),
        measures=[Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )
    flow = Dataflow(
        id="DF_TIME",
        maintainer="TEST",
        version="1.0",
        name=InternationalString(en="Time dataflow"),
        structure=Ref[DataStructure].to(dsd),
    )
    return dsd, flow


def _drain(gen: Iterable[bytes]) -> dict[str, Any]:
    return msgspec.json.decode(b"".join(gen), type=dict[str, Any])


def test_writer_emits_start_end_for_time_dimension() -> None:
    """The production bug — .Stat needs ``start`` / ``end`` on every TIME_PERIOD value or
    ``refineTimePeriod`` crashes. Writer must populate them."""
    dsd, flow = _build_dsd_with_time()
    binding = dsd_binding(dsd, flow)
    batch = pa.record_batch(
        {
            "FREQ": ["A", "A", "A"],
            "TIME_PERIOD": ["2021", "2022", "2023"],
            "OBS_VALUE": [1.0, 2.0, 3.0],
        }
    )
    doc = _drain(
        write_sdmx_json_data(
            [batch],
            dsd,
            binding.dataflow,
            message_id="m",
            prepared=datetime(2026, 6, 16, tzinfo=UTC),
            sender_id="S",
            lang="en",
        )
    )
    dims = doc["data"]["structures"][0]["dimensions"]["observation"]
    time_dim = next(d for d in dims if d["id"] == "TIME_PERIOD")
    values_by_id = {v["id"]: v for v in time_dim["values"]}
    assert values_by_id["2021"]["start"] == "2021-01-01T00:00:00"
    assert values_by_id["2021"]["end"] == "2021-12-31T23:59:59"
    assert values_by_id["2022"]["start"] == "2022-01-01T00:00:00"
    assert values_by_id["2023"]["end"] == "2023-12-31T23:59:59"

    # Non-time dim never carries bounds (would confuse consumers and
    # has no semantic meaning for FREQ codes).
    freq_dim = next(d for d in dims if d["id"] == "FREQ")
    assert "start" not in freq_dim["values"][0]


def test_writer_time_bounds_no_dsd_time_dimension_falls_back_to_id() -> None:
    """Some legacy DSDs declare TIME_PERIOD as a regular Dimension
    (not a formal <TimeDimension>). The writer recognises the
    well-known id and still emits bounds."""
    cl_freq = Codelist(
        id="CL_FREQ",
        maintainer="TEST",
        version="1.0",
        name=InternationalString(en="Frequency"),
        codes=[Code(name={"en": "A"}, id="A")],
    )
    dsd = DataStructure(
        id="DSD_LEGACY_TIME",
        maintainer="TEST",
        version="1.0",
        name=InternationalString(en="Legacy DSD"),
        dimensions=[
            Dimension(id="FREQ", representation=Ref[Codelist].to(cl_freq), concept=concept_ref("FREQ")),
            Dimension(id="TIME_PERIOD", concept=concept_ref("TIME_PERIOD")),  # no formal TimeDimension
        ],
        measures=[Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )
    flow = Dataflow(
        id="DF_LEG",
        maintainer="TEST",
        version="1.0",
        name=InternationalString(en="Legacy"),
        structure=Ref[DataStructure].to(dsd),
    )
    binding = dsd_binding(dsd, flow)
    batch = pa.record_batch({"FREQ": ["A"], "TIME_PERIOD": ["2021"], "OBS_VALUE": [1.0]})
    doc = _drain(
        write_sdmx_json_data(
            [batch],
            dsd,
            binding.dataflow,
            message_id="m",
            prepared=datetime(2026, 6, 16, tzinfo=UTC),
            sender_id="S",
            lang="en",
        )
    )
    dims = doc["data"]["structures"][0]["dimensions"]["observation"]
    time_dim = next(d for d in dims if d["id"] == "TIME_PERIOD")
    assert time_dim["values"][0]["start"] == "2021-01-01T00:00:00"


# ── .Stat-shape semantic regression ──────────────────────────────────────────


def _refine_time_period_python(period_value: dict) -> None:  # type: ignore[type-arg]
    """Python clone of ``rules2.refineTimePeriod`` from dotstatsuite-components.

    Asserts the same control-flow invariants the .Stat code relies on:

    1. If ``id`` contains ``/``, the value is in ``<start>/<duration>`` form
       — the consumer parses it itself, no requirements on ``start`` / ``end``.
    2. Otherwise ``start`` and ``end`` must be ISO-parseable date-time
       strings (``datetime.fromisoformat`` is the stdlib analogue of
       ``date-fns.parseISO`` for the format we emit).

    Raises ``AssertionError`` on the JS-equivalent of
    ``RangeError: Invalid time value`` — i.e. exactly the failure seen in
    production.
    """
    period_id = period_value.get("id", "")
    if "/" in period_id:
        return  # .Stat parses <start>/<duration> internally
    start = period_value.get("start")
    end = period_value.get("end")
    assert start is not None, f"TIME_PERIOD value {period_id!r} missing 'start' — .Stat would crash"
    assert end is not None, f"TIME_PERIOD value {period_id!r} missing 'end' — .Stat would crash"
    # parseISO equivalent — raises ValueError on a malformed timestamp.
    datetime.fromisoformat(start)
    datetime.fromisoformat(end)


def test_dot_stat_shape_regression() -> None:
    """Drain a writer output through the Python refineTimePeriod clone.

    No assertion needed beyond ``_refine_time_period_python`` succeeding
    for every TIME_PERIOD value the writer emitted. This is the safety
    net the JSON-Schema check failed to provide: ``start`` / ``end``
    are *optional* properties per the SDMX-JSON schema, so a missing-
    bounds emitter still validates clean — but breaks every .Stat-class
    consumer. Catch that here.
    """
    dsd, flow = _build_dsd_with_time()
    binding = dsd_binding(dsd, flow)
    batch = pa.record_batch(
        {
            "FREQ": ["A"] * 6,
            "TIME_PERIOD": [
                "2021",
                "2021-07",
                "2021-07-15",
                "2021-Q3",
                "2021-S2",
                "2021-01-01/P5Y",
            ],
            "OBS_VALUE": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        }
    )
    doc = _drain(
        write_sdmx_json_data(
            [batch],
            dsd,
            binding.dataflow,
            message_id="m",
            prepared=datetime(2026, 6, 16, tzinfo=UTC),
            sender_id="S",
            lang="en",
        )
    )
    dims = doc["data"]["structures"][0]["dimensions"]["observation"]
    time_dim = next(d for d in dims if d["id"] == "TIME_PERIOD")
    for value in time_dim["values"]:
        _refine_time_period_python(value)
