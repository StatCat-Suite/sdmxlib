"""The calendar extent of an SDMX time period, and the time range a set of periods covers.

`period_bounds` reads one `TIME_PERIOD` value and returns its first and last
second as zoneless ISO 8601 date-times. `period_range` turns a set of
periods into the `TimeRange` from the earliest start to the latest end, both
inclusive: the shape the .Stat Suite Data Explorer reads a time dimension's
availability in. The constraint builders (`sdmxlib.builders`) and the
SDMX-JSON data writers use both.

The bounds are zoneless because their consumer, .Stat's `refineTimePeriod`,
adds the local UTC offset back to what it parses, which is right only for a
zoneless value; the NSI web service writes them zoneless too. The SDMX-JSON
schema asks for a zone, so this is a declared deviation
([#496](https://github.com/StatCat-Suite/sdmxlib/blob/main/history/gitlab/issues/496.md)).
"""

import calendar
import re
from collections.abc import Iterable
from datetime import date, timedelta

from sdmxlib.model.constraint import TimeBound, TimeRange

_ANNUAL = re.compile(r"(\d{4})(?:-A1)?")
_MONTHLY = re.compile(r"(\d{4})-M?(\d{2})")
_DAILY = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_QUARTERLY = re.compile(r"(\d{4})-Q([1-4])")
_SEMESTER = re.compile(r"(\d{4})-S([12])")
_TRIMESTER = re.compile(r"(\d{4})-T([1-3])")
_WEEKLY = re.compile(r"(\d{4})-W(\d{2})")
_DAY_OF_YEAR = re.compile(r"(\d{4})-D(\d{3})")


def _span(start: date, end: date) -> tuple[str, str]:
    """The first second of ``start`` and the last second of ``end``."""
    return f"{start.isoformat()}T00:00:00", f"{end.isoformat()}T23:59:59"


def _months(year: int, first: int, last: int) -> tuple[str, str]:
    """The span from the first day of month ``first`` to the last day of month ``last``."""
    return _span(date(year, first, 1), date(year, last, calendar.monthrange(year, last)[1]))


def _month_block(year: int, index: int, width: int) -> tuple[str, str]:
    """The ``index``-th block of ``width`` months in ``year``, counting from 1."""
    first = (index - 1) * width + 1
    return _months(year, first, first + width - 1)


def _match(period: str) -> tuple[str, str] | None:
    """The bounds of ``period``, raising `ValueError` for a date that does not exist."""
    if m := _DAILY.fullmatch(period):
        day = date(int(m[1]), int(m[2]), int(m[3]))
        return _span(day, day)
    if m := _MONTHLY.fullmatch(period):
        month = int(m[2])
        return _months(int(m[1]), month, month)
    if m := _QUARTERLY.fullmatch(period):
        return _month_block(int(m[1]), int(m[2]), 3)
    if m := _TRIMESTER.fullmatch(period):
        return _month_block(int(m[1]), int(m[2]), 4)
    if m := _SEMESTER.fullmatch(period):
        return _month_block(int(m[1]), int(m[2]), 6)
    if m := _WEEKLY.fullmatch(period):
        monday = date.fromisocalendar(int(m[1]), int(m[2]), 1)
        return _span(monday, monday + timedelta(days=6))
    if m := _DAY_OF_YEAR.fullmatch(period):
        year, ordinal = int(m[1]), int(m[2])
        if not 1 <= ordinal <= (366 if calendar.isleap(year) else 365):
            return None
        day = date(year, 1, 1) + timedelta(days=ordinal - 1)
        return _span(day, day)
    if m := _ANNUAL.fullmatch(period):
        return _months(int(m[1]), 1, 12)
    return None


def period_bounds(period: str) -> tuple[str, str] | None:
    """The first and last second of an SDMX time period, as zoneless ISO 8601 date-times.

    ``"2021"`` gives ``("2021-01-01T00:00:00", "2021-12-31T23:59:59")``. The
    shapes read:

    | Shape | Example | Extent |
    | --- | --- | --- |
    | `YYYY`, `YYYY-A1` | `2021`, `2021-A1` | the calendar year |
    | `YYYY-Sn` | `2021-S1` | half year: S1 is January to June |
    | `YYYY-Tn` | `2021-T2` | four months: T2 is May to August |
    | `YYYY-Qn` | `2021-Q2` | quarter: Q2 is April to June |
    | `YYYY-MM`, `YYYY-Mmm` | `2021-06`, `2021-M06` | the month |
    | `YYYY-Www` | `2021-W01` | ISO 8601 week, Monday to Sunday |
    | `YYYY-MM-DD`, `YYYY-Dddd` | `2021-06-30`, `2021-D181` | the day |

    A reporting period (`2021-Q2`, `2021-M06`, `2021-W01`) is read against a
    reporting year that starts on 1 January, the SDMX default. A period whose
    length comes from elsewhere is read by its shape alone: under a
    five-year frequency `2021` still gives the calendar year 2021.

    Args:
        period: A `TIME_PERIOD` value as written.

    Returns:
        The ``(start, end)`` pair, both inclusive, or None for any other
        shape: a date-time, a `<start>/<duration>` interval
        (``"2021-01-01/P5Y"``), a reporting year that starts on another day,
        or a date that does not exist (``"2021-02-30"``, ``"2021-W53"``).
    """
    try:
        return _match(period)
    except ValueError:
        return None


def period_range(periods: Iterable[str]) -> TimeRange | None:
    """The time range from the earliest period's start to the latest period's end, both inclusive.

    ``["2021", "2019-Q3"]`` gives the range from ``2019-07-01T00:00:00`` to
    ``2021-12-31T23:59:59``. Gaps between the periods are not recorded.

    Args:
        periods: `TIME_PERIOD` values as written, in any order.

    Returns:
        The covering `TimeRange`, or None when ``periods`` is empty or any
        period is one `period_bounds` cannot read. The caller then keeps the
        periods as a list of values, which loses nothing.
    """
    starts: list[str] = []
    ends: list[str] = []
    for period in periods:
        bounds = period_bounds(period)
        if bounds is None:
            return None
        starts.append(bounds[0])
        ends.append(bounds[1])
    if not starts:
        return None
    # Every bound has the one fixed-width shape, so text order is time order.
    return TimeRange(start=TimeBound(period=min(starts)), end=TimeBound(period=max(ends)))
