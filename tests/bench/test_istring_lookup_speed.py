"""What `InternationalString.get` costs beside the text lookup it replaced (GH-309).

Reports both, over the hub's mix of a million strings, and asserts nothing:
`tests/unit/test_istring_lookup_calls.py` guards the cost by counting the
Python calls one `get` makes. A timed ratio was the guard until GH-432, and
failed `just ci` at its 1.5x budget without a change to the code, since both
sides call `normalize_tag` and move with the interpreter.

Measured on CPython 3.14.10 (GH-432): `get` took 396 ns a call with
`normalize_tag` checking every tag, and 250 ns once a tag it has accepted
skips the check.

    just bench -k istring
"""

from collections.abc import Callable

import pytest
from pytest_benchmark.fixture import BenchmarkFixture

from sdmxlib.model.istring import InternationalString, normalize_tag

pytestmark = pytest.mark.bench

_STRINGS = 1_000_000

#: The hub's mix, in each block of 20 strings: 12 hold `en` and `fr`, 5 only
#: `en`, and 3 are empty.
_BLOCK, _BOTH, _EN_ONLY = 20, 12, 17


@pytest.fixture(scope="module")
def strings() -> list[InternationalString]:
    rows = [
        {"en": f"L{i}", "fr": f"F{i}"} if i % _BLOCK < _BOTH else ({"en": f"L{i}"} if i % _BLOCK < _EN_ONLY else {})
        for i in range(_STRINGS)
    ]
    return [InternationalString._from_normalized(row) for row in rows]


def _each(lookup: Callable[[InternationalString], object], strings: list[InternationalString]) -> None:
    for s in strings:
        lookup(s)


def test_text_lookup(benchmark: BenchmarkFixture, strings: list[InternationalString]) -> None:
    """The lookup before GH-309: normalise the tag, return the text."""
    benchmark(_each, lambda s: s._backing().get(normalize_tag("fr")), strings)


def test_get(benchmark: BenchmarkFixture, strings: list[InternationalString]) -> None:
    benchmark(_each, lambda s: s.get("fr"), strings)
