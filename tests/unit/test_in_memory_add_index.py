"""`_by_type` is keyed by URN, like `_store` (#138).

#138 asked for a benchmark of `applicable_hierarchies` at 10^2 / 10^3 / 10^4
artefacts, to decide whether an inbound-Ref index was worth building. The
benchmark found the cost somewhere else: `add` held a list per Python type and
guarded it with

    if artefact not in self._by_type[t]:

which is a linear scan using attrs' generated `__eq__` — a *deep* field
comparison, on a list that grows with every add. Adding 10,002 artefacts cost
0.79 s and rising superlinearly (5.6 -> 18.2 -> 79.3 us per artefact at n =
100 / 1,000 / 5,000). Keyed by URN it is 3.0 us, flat.

The same line was also wrong. `_store` replaces by URN; that list deduped by
*equality*, so re-adding a modified artefact appended a second entry:

    reg.add(v1); reg.add(v2)          # same URN, different name
    len(reg)                          # 1
    reg.get(urn).name                 # "second"
    reg.get_all(Codelist)             # ['first', 'second']   <- the ghost

Two tests, because the two would not fail together: a dict keyed by anything
unique fixes the complexity, and only a dict keyed by *the URN* fixes the
staleness.

The inbound index #138 proposes is deliberately not here. The scan it would
replace measures 419 us at 10,000 associations with two matches, 8 us at 100,
and the largest bundle we have holds 11 — no caller is slow, which is the
precondition the issue set for itself and `docs/design/caching.md` requires.
The DuckDB half is already in `schema.py` as `idx_ha_linked_item`.
"""

from __future__ import annotations

import pytest

import sdmxlib as sl
from sdmxlib.model.in_memory_registry import InMemoryRegistry

pytestmark = pytest.mark.unit

#: Enough that a quadratic guard is unmistakable and few enough to stay a unit
#: test. The old code made ADDS * (ADDS - 1) / 2 = 19,900 comparisons here.
ADDS = 200


def _codelist(cl_id: str, name: str) -> sl.Codelist:
    return sl.Codelist(id=cl_id, maintainer="AG", version="1.0", name={"en": name})


def test_re_adding_replaces_rather_than_accumulating() -> None:
    """`get` and `get_all` must agree about what is stored.

    They did not: `get` answered with the new artefact while `get_all` still
    enumerated the old one, so any caller that iterates — drift checks,
    discovery, every `applicable_*` method — saw a version nothing could
    fetch.
    """
    reg = InMemoryRegistry()
    reg.add(_codelist("CL", "first"))
    reg.add(_codelist("CL", "second"))

    urn = sl.Codelist.urn_for(agency="AG", id="CL", version="1.0")
    stored = reg.get(urn)
    assert stored is not None
    names = [cl.name["en"] for cl in reg.get_all(sl.Codelist)]
    assert names == ["second"], f"get() says {stored.name['en']!r} and get_all() says {names}"
    assert len(reg) == 1


def test_a_replacement_keeps_its_original_position() -> None:
    """Replacing must not reorder the enumeration.

    A dict keyed by URN gives this for free — the key keeps its insertion slot
    — and it matters because `get_all` order is what `applicable_hierarchies`
    sorts and what callers page through. A `remove`-then-`append` fix would
    have passed the test above and silently moved the artefact to the end.
    """
    reg = InMemoryRegistry()
    for cl_id in ("A", "B", "C"):
        reg.add(_codelist(cl_id, cl_id))
    reg.add(_codelist("A", "A-updated"))

    assert [cl.id for cl in reg.get_all(sl.Codelist)] == ["A", "B", "C"]
    assert [cl.name["en"] for cl in reg.get_all(sl.Codelist)] == ["A-updated", "B", "C"]


def test_add_does_not_compare_the_artefact_against_everything_stored(monkeypatch: pytest.MonkeyPatch) -> None:
    """The complexity, as a count of the expensive operation.

    Counted rather than timed, per CLAUDE.md: `__eq__` on an attrs class walks
    every field, so its *call count* is what separates O(n) from O(n^2) and it
    reads the same on a loaded machine. Bounded at `ADDS` rather than zero
    because nothing here promises the store never compares — only that it does
    not compare against each artefact already held.
    """
    calls = {"n": 0}
    real_eq = sl.Codelist.__eq__

    def counting_eq(self: sl.Codelist, other: object) -> bool:
        calls["n"] += 1
        return real_eq(self, other)

    monkeypatch.setattr(sl.Codelist, "__eq__", counting_eq)

    reg = InMemoryRegistry()
    for i in range(ADDS):
        reg.add(_codelist(f"CL{i:04d}", f"c{i}"))

    assert len(reg) == ADDS
    assert calls["n"] <= ADDS, (
        f"{calls['n']} Codelist.__eq__ calls to add {ADDS} codelists — "
        f"{calls['n'] / ADDS:.1f} per add, so `add` is comparing against what "
        f"is already stored. That is the quadratic #138's benchmark found."
    )
