"""`period_bounds` and `period_range`: the calendar extent of SDMX time periods (GH-392)."""

import pytest

import sdmxlib as sl
from sdmxlib.model.constraint import TimeBound, TimeRange
from sdmxlib.model.period import period_bounds, period_range

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("period", "start", "end"),
    [
        ("2021", "2021-01-01T00:00:00", "2021-12-31T23:59:59"),
        ("2021-A1", "2021-01-01T00:00:00", "2021-12-31T23:59:59"),
        ("2021-S1", "2021-01-01T00:00:00", "2021-06-30T23:59:59"),
        ("2021-S2", "2021-07-01T00:00:00", "2021-12-31T23:59:59"),
        ("2021-T1", "2021-01-01T00:00:00", "2021-04-30T23:59:59"),
        ("2021-T2", "2021-05-01T00:00:00", "2021-08-31T23:59:59"),
        ("2021-T3", "2021-09-01T00:00:00", "2021-12-31T23:59:59"),
        ("2021-Q2", "2021-04-01T00:00:00", "2021-06-30T23:59:59"),
        ("2021-06", "2021-06-01T00:00:00", "2021-06-30T23:59:59"),
        ("2021-M06", "2021-06-01T00:00:00", "2021-06-30T23:59:59"),
        ("2024-M02", "2024-02-01T00:00:00", "2024-02-29T23:59:59"),
        ("2021-06-30", "2021-06-30T00:00:00", "2021-06-30T23:59:59"),
        # ISO week 1 of 2021 starts on Monday 4 January.
        ("2021-W01", "2021-01-04T00:00:00", "2021-01-10T23:59:59"),
        # 2020 has an ISO week 53, which ends in 2021.
        ("2020-W53", "2020-12-28T00:00:00", "2021-01-03T23:59:59"),
        ("2021-D001", "2021-01-01T00:00:00", "2021-01-01T23:59:59"),
        ("2024-D366", "2024-12-31T00:00:00", "2024-12-31T23:59:59"),
    ],
)
def test_period_bounds_reads_each_shape(period: str, start: str, end: str) -> None:
    assert period_bounds(period) == (start, end)


@pytest.mark.parametrize(
    "period",
    [
        "",
        "21",
        "2021-13",
        "2021-M13",
        "2021-02-30",
        "2021-Q5",
        "2021-S3",
        "2021-T4",
        "2021-A2",
        "2021-W00",
        "2021-W53",  # 2021 has 52 ISO weeks
        "2021-D000",
        "2021-D366",  # 2021 is not a leap year
        "2021-01-01T00:00:00",
        "2021-01-01/P5Y",
        "2021-Q1 ",
        "2021\n",
    ],
)
def test_period_bounds_returns_none_for_any_other_shape(period: str) -> None:
    assert period_bounds(period) is None


def test_period_range_spans_the_earliest_start_to_the_latest_end() -> None:
    assert period_range(["2021", "2019-Q3", "2020-M02"]) == TimeRange(
        start=TimeBound(period="2019-07-01T00:00:00"),
        end=TimeBound(period="2021-12-31T23:59:59"),
    )


def test_period_range_of_one_period_is_its_bounds() -> None:
    assert period_range(["2021"]) == TimeRange(
        start=TimeBound(period="2021-01-01T00:00:00"),
        end=TimeBound(period="2021-12-31T23:59:59"),
    )


def test_period_range_is_none_when_any_period_is_unread() -> None:
    assert period_range(["2021"]) is not None
    assert period_range(["2021", "2021-01-01/P5Y"]) is None


def test_period_range_is_none_for_no_periods() -> None:
    assert period_range([]) is None


def test_both_are_exported_at_the_top_level() -> None:
    assert sl.period_bounds is period_bounds
    assert sl.period_range is period_range
