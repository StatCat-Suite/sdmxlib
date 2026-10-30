"""`InternationalString.get` costs at most 1.5 times a plain text lookup (GH-309).

A lookup builds the `LocalisedString` it returns, so it does more than the
`dict.get` it replaced: a struct allocation and a check of the tag against the
seen set. The suite design ("Text model: add LocalisedString per the SDMX
Information Model", StatCat Suite hub `docs/design/localised-string.md`)
budgets that at 1.5 times the old cost, and measured 1.44 times on CPython
3.14.8 (`bench_final_split.py` in the hub's evidence directory).

Both sides are timed in one process over the same million strings, so the
ratio does not depend on the machine. A ratio over budget is measured once
more before the test fails, since a single run can catch other load.

Marked `bundle` as well as `bench` so `just ci` runs it with the real-bundle
assertions. It needs no bundle.
"""

import statistics
import time
from collections.abc import Callable

import pytest

from sdmxlib.model.istring import InternationalString, LocalisedString, normalize_tag

pytestmark = [pytest.mark.bench, pytest.mark.bundle]

_STRINGS = 1_000_000
_RUNS = 5
_BUDGET = 1.5


#: The hub's mix, in each block of 20 strings: 12 hold `en` and `fr`, 5 only
#: `en`, and 3 are empty.
_BLOCK, _BOTH, _EN_ONLY = 20, 12, 17


def _strings() -> list[InternationalString]:
    rows = [
        {"en": f"L{i}", "fr": f"F{i}"} if i % _BLOCK < _BOTH else ({"en": f"L{i}"} if i % _BLOCK < _EN_ONLY else {})
        for i in range(_STRINGS)
    ]
    return [InternationalString._from_normalized(row) for row in rows]


def _base(s: InternationalString) -> str | None:
    """The lookup before GH-309: normalise the tag, return the text."""
    return s._backing().get(normalize_tag("fr"))


def _candidate(s: InternationalString) -> LocalisedString | None:
    return InternationalString.get(s, "fr")


def _median(lookup: Callable[[InternationalString], object], strings: list[InternationalString]) -> float:
    times: list[float] = []
    for _ in range(_RUNS):
        start = time.perf_counter()
        for s in strings:
            lookup(s)
        times.append(time.perf_counter() - start)
    return statistics.median(times)


def _ratio(strings: list[InternationalString]) -> float:
    _candidate(strings[0])  # the seen set holds `fr` before timing starts
    return _median(_candidate, strings) / _median(_base, strings)


def test_get_costs_at_most_one_and_a_half_text_lookups() -> None:
    strings = _strings()
    ratio = _ratio(strings)
    if ratio > _BUDGET:
        ratio = _ratio(strings)
    # Above 1: the candidate does the base's work and more, so a ratio at or
    # below 1 means the two sides no longer measure what they name.
    assert 1.0 < ratio <= _BUDGET, f"InternationalString.get costs {ratio:.2f}x a text lookup; the budget is {_BUDGET}x"
