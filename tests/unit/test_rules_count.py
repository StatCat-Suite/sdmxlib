"""Each id is checked once per object built from it, and nowhere else (#492).

The id rules run as fields are set (`sdmxlib.model._rules`), so their cost is
per object built: +1.4% on reading the census bundle's 579,951-code geography
codelist (3.295 s to 3.341 s, medians of five interleaved A/B runs; peak RSS
unchanged within noise). That holds while each code is checked once, with one
regex match. A path that rebuilt items, or validated them
again on `add`, would multiply it without failing anything else, so the count
is asserted rather than the clock.
"""

import re

import pytest

import sdmxlib as sl
from sdmxlib.model import _rules
from tests.support.narrow import present

pytestmark = pytest.mark.unit

_N = 50


class _CountingPattern:
    """`ID_RE`, counting its matches: the one call every valid id costs."""

    def __init__(self, pattern: re.Pattern[str]) -> None:
        self.pattern = pattern
        self.calls = 0

    def match(self, value: str) -> re.Match[str] | None:
        self.calls += 1
        return self.pattern.match(value)


def test_an_id_is_checked_once_per_code_built_and_never_on_add_or_get(monkeypatch: pytest.MonkeyPatch) -> None:
    counting = _CountingPattern(_rules.ID_RE)
    monkeypatch.setattr(_rules, "ID_RE", counting)

    codes = [sl.Code(id=f"C{i}", name={"en": f"c{i}"}) for i in range(_N)]
    assert counting.calls == _N, "one check per code constructed"
    counting.calls = 0
    codelist = sl.Codelist(id="CL", maintainer="AG", name={"en": "cl"}, codes=codes)
    with sl.LocalRegistry(":memory:") as reg:
        reg.add(codelist)
        lazy = present(reg.get(codelist.urn))
        assert counting.calls == 0, "neither the codelist, add nor get rebuilds a code"
        assert len(lazy.codes.collect()) == _N
    assert counting.calls == _N, "one check per code read back"
