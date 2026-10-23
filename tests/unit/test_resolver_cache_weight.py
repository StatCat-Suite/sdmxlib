"""The resolver cache is bounded in items, and the bound corresponds to memory (#386).

`DEFAULT_RESOLVE_CACHE_SIZE = 256` bounded the cache by **entry count** while
its entries differed in size by four orders of magnitude. On the census bundle the
five largest artefacts total ~4.4 GB and occupy 5 of 256 slots, so a service
could report `size: 5, max_size: 256`, look healthy, and be sitting on 4.4 GB:
the failure the bound existed to prevent, reachable with the counter reading 2%
full. `docs/design/caching.md` requires a runtime cache to be bounded; that met
the letter of it and nothing else.

These tests are about the *bound*, so they assert on the weight the cache
reports rather than on wall memory. The items→bytes correspondence that makes
the weight meaningful is measured, and lives in `DEFAULT_RESOLVE_CACHE_ITEMS`'s
comment where the number it justifies is.
"""

from __future__ import annotations

import inspect

import attrs
import pytest

import sdmxlib as sl
from sdmxlib.local import LocalRegistry
from sdmxlib.local.registry import (
    _ITEM_ATTRS,
    DEFAULT_RESOLVE_CACHE_ITEMS,
    ITEM_FLOOR,
    _FIFOCache,
    weigh,
)
from tests.support.factories import code_ref

pytestmark = pytest.mark.unit


def _codelist(id_: str, n: int) -> sl.Codelist:
    """A codelist of `n` codes; the tests vary `n`, so it is the argument."""
    return sl.Codelist(
        id=id_,
        maintainer="AG",
        version="1.0",
        name={"en": id_},
        codes=[sl.Code(id=f"C{i}", name={"en": f"c{i}"}) for i in range(n)],
    )


# ── what an entry weighs ─────────────────────────────────────────────────


def test_an_artefact_weighs_its_items() -> None:
    """The claim the whole bound rests on: weight tracks size, not entry count."""
    assert weigh(_codelist("CL", 5_000)) == 5_000
    assert weigh(_codelist("CL", 100)) == 100


def test_a_small_artefact_weighs_the_floor_rather_than_nothing() -> None:
    """A pure weigher would call these free and lose the one thing an entry
    count was good at — bounding a flood of small artefacts.

    The floor is measured rather than picked: a zero-item artefact retains
    0.00–0.08 MB on the census bundle, which is about what 64 items weigh.
    """
    assert weigh(_codelist("CL", 0)) == ITEM_FLOOR
    assert weigh(_codelist("CL", 1)) == ITEM_FLOOR
    assert weigh(sl.Dataflow(id="DF", maintainer="AG", version="1.0", name={"en": "d"})) == ITEM_FLOOR


def test_a_hierarchy_weighs_every_node_and_not_its_roots() -> None:
    """`tree` holds the roots and `codes` the flat index over all of them, and
    the two are the same objects (#383).

    Counting `tree` would weigh a 499,143-node hierarchy as 1; counting both
    would double it. This is the one place the probe order in `_ITEM_ATTRS`
    carries a real decision rather than a preference.
    """
    # Above `ITEM_FLOOR`, or the floor would answer and the test would pass
    # for a weigher that counted nothing at all.
    leaves = [sl.HierarchicalCode(id=f"N{i}", code=code_ref("X")) for i in range(199)]
    root = sl.HierarchicalCode(id="ROOT", children=leaves, code=code_ref("ROOT"))
    hierarchy = sl.Hierarchy(id="H", maintainer="AG", version="1.0", name={"en": "h"}, tree=[root])
    assert len(hierarchy.tree) == 1, "one root — what counting `tree` would weigh this as"
    assert len(hierarchy.codes) == 200, "the flat index over every node"
    assert weigh(hierarchy) == 200, "one root and 199 leaves, counted once each"


def test_a_lazy_artefact_weighs_the_floor_without_touching_the_database() -> None:
    """A `LazyCodelist` holds a URN and a connection, not 579,951 codes, so the
    floor is the honest weight — and asking its `codes` for a length would run
    SQL. A weigher that queries the database on every insert is not a weigher.
    """
    with LocalRegistry(":memory:") as local:
        local.add(_codelist("CL_BIG", 500))
        lazy = local.get(sl.Codelist.urn_for(agency="AG", id="CL_BIG", version="1.0"))
        assert type(lazy).__name__ == "LazyCodelist", "the premise of this test"
        assert weigh(lazy) == ITEM_FLOOR


# ── the bound holds ──────────────────────────────────────────────────────


def test_caching_large_artefacts_does_not_exceed_the_bound() -> None:
    """The acceptance criterion, on the weight the cache reports.

    Under the old bound this passed trivially and meant nothing: ten entries is
    ten entries whether they hold ten codes or ten million.
    """
    cache = _FIFOCache(10_000)
    for i in range(10):
        cache[f"urn:{i}"] = _codelist(f"CL{i}", 4_000)
        assert cache.weight <= cache.maxitems, f"over the bound after {i + 1} inserts: {cache.weight}"
    assert len(cache) == 2, "4,000 items each, so a 10,000-item budget holds two"


def test_the_old_bound_would_have_let_this_through() -> None:
    """Why the previous test is not vacuous.

    The same ten artefacts under an entry-count bound of 256 stay resident and
    the counter reads `10 / 256` — 4% full, holding 40,000 items. That is the
    #386 shape in miniature, and stating it here is what keeps the assertion
    above from reading as a tautology.
    """
    entries = [_codelist(f"CL{i}", 4_000) for i in range(10)]
    assert sum(weigh(a) for a in entries) == 40_000
    assert len(entries) < 256, "an entry-count bound would have admitted every one"


def test_a_flood_of_small_artefacts_is_still_bounded() -> None:
    """What the floor is for. Without it these weigh zero and never evict."""
    cache = _FIFOCache(10 * ITEM_FLOOR)
    for i in range(200):
        cache[f"urn:{i}"] = _codelist(f"CL{i}", 0)
    assert cache.weight <= cache.maxitems
    assert len(cache) == 10


def test_replacing_an_entry_does_not_inflate_the_weight() -> None:
    """Re-resolving the same URN must not add its weight twice — that would
    evict a cache which was never over its bound."""
    cache = _FIFOCache(10_000)
    for _ in range(20):
        cache["urn:same"] = _codelist("CL", 1_000)
    assert len(cache) == 1
    assert cache.weight == 1_000


def test_clearing_resets_the_weight() -> None:
    cache = _FIFOCache(10_000)
    cache["urn:a"] = _codelist("CL", 900)
    assert cache.weight == 900
    cache.clear()
    assert cache.weight == 0
    assert len(cache) == 0


# ── the over-weight entry, which a count-based cache never had to answer ──


def test_an_entry_heavier_than_the_bound_is_refused() -> None:
    """Refused rather than admitted-and-evicted, which is Caffeine's and
    Guava's behaviour.

    Admitting it first would evict everything else on the way to evicting
    itself, so one over-budget read empties the cache. Refusing keeps the
    resident weight under the bound at every instant.
    """
    cache = _FIFOCache(1_000)
    cache["urn:small"] = _codelist("CL_SMALL", 500)
    cache["urn:huge"] = _codelist("CL_HUGE", 5_000)

    assert "urn:huge" not in cache, "too heavy for this bound to hold at all"
    assert "urn:small" in cache, "and it did not displace the working set on its way out"
    assert cache.weight == 500
    assert cache.refused == 1


def test_refusals_are_reported() -> None:
    """A consumer asking "why does this never cache" needs somewhere to look —
    a poor hit rate alongside a rising `refused` is a working set that does not
    fit, which is a different fix from a cold cache."""
    with LocalRegistry(":memory:", resolve_cache_items=ITEM_FLOOR) as local:
        assert local.resolve_cache_info()["refused"] == 0
        assert local.resolve_cache_info()["max_items"] == ITEM_FLOOR


def test_an_entry_exactly_at_the_bound_is_admitted() -> None:
    """The boundary is `>`, not `>=`: an artefact that fits exactly is the
    largest thing the cache is for, not the smallest thing it refuses."""
    cache = _FIFOCache(1_000)
    cache["urn:exact"] = _codelist("CL", 1_000)
    assert "urn:exact" in cache
    assert cache.refused == 0
    assert cache.weight == 1_000


# ── nothing goes quietly unweighed ───────────────────────────────────────


#: Model classes with an `ItemList` field that `weigh` deliberately does not
#: reach, and why each is safe to leave at the floor.
#:
#: Every one is a *container* rather than a cached artefact, or holds items
#: measured in tens. The resolver cache holds maintainables resolved from a
#: `Ref`, and the ones that get large are the schemes above.
_WEIGHS_THE_FLOOR: dict[str, str] = {
    "HierarchicalCode": "one node — weighed through Hierarchy.codes, which flattens them",
    "MetadataAttribute": "one slot — weighed through MetadataStructure.attributes",
    "ReportedAttribute": "one reported value — weighed through MetadataSet.attributes",
}


def _item_names(cls: type) -> set[str]:
    """Names by which this class's items are reachable — fields and properties.

    Fields *and* properties, because `Hierarchy` stores `_codes` and exposes
    `codes`, and `weigh` reads the public name. `hasattr` would be the shorter
    check and is too loose: it answers True for inherited attributes holding no
    items at all, which quietly marks a real gap as covered.
    """
    fields = {f.name for f in attrs.fields(cls)}
    properties = {name for name, value in vars(cls).items() if isinstance(value, property)}
    return fields | properties


def test_every_itemlist_bearing_class_is_weighed_or_listed() -> None:
    """A new scheme type must not go quietly unweighed.

    `weigh` probes attribute *names*, so a class whose items hang off a new one
    would weigh the floor — under-counting the bound rather than failing, which
    is the failure mode this whole issue is about. This fails instead, and the
    fix is one line in `_ITEM_ATTRS` or one in the map above with a reason.
    """
    unreached: dict[str, list[str]] = {}
    for name in dir(sl):
        cls = getattr(sl, name)
        if not (inspect.isclass(cls) and attrs.has(cls)):
            continue
        holds_items = [f.name for f in attrs.fields(cls) if f.type is not None and "ItemList" in str(f.type)]
        if not holds_items:
            continue
        # `_ITEM_ATTRS` itself, not a hand-copy of it: a name added there must
        # close a gap here without anyone remembering to update two lists.
        if _item_names(cls) & set(_ITEM_ATTRS):
            continue
        if name in _WEIGHS_THE_FLOOR:
            continue
        unreached[name] = holds_items
    assert not unreached, (
        f"these classes hold ItemList fields that `weigh` does not reach, so they would "
        f"weigh ITEM_FLOOR regardless of size: {unreached}. Add the attribute to "
        f"_ITEM_ATTRS, or the class to _WEIGHS_THE_FLOOR with the reason it is small."
    )


def test_the_floor_list_has_no_stale_entries() -> None:
    """A ratchet, like `tools/any_baseline.txt`: an entry that no longer names
    a class with `ItemList` fields is a note nobody will delete otherwise."""
    stale = []
    for name in _WEIGHS_THE_FLOOR:
        cls = getattr(sl, name, None)
        if not (inspect.isclass(cls) and attrs.has(cls)):
            stale.append(f"{name}: no longer a model class")
        elif not any("ItemList" in str(f.type) for f in attrs.fields(cls) if f.type is not None):
            stale.append(f"{name}: no longer holds an ItemList")
        elif _item_names(cls) & set(_ITEM_ATTRS):
            stale.append(f"{name}: _ITEM_ATTRS now reaches it, so it does not weigh the floor")
    assert not stale, f"_WEIGHS_THE_FLOOR has entries that no longer describe anything: {stale}"


def test_the_default_is_stated_in_items() -> None:
    """The constant that replaced `DEFAULT_RESOLVE_CACHE_SIZE = 256`.

    Asserted so the docstring's memory arithmetic — 1,000,000 items is 0.7 GB
    of codes to 2.0 GB of nodes — cannot drift from the number it describes
    without this failing.
    """
    assert DEFAULT_RESOLVE_CACHE_ITEMS == 1_000_000
    assert DEFAULT_RESOLVE_CACHE_ITEMS > ITEM_FLOOR * 1_000, "a floor-weight flood must not fill it trivially"


# ── how a large artefact actually reaches the cache ──────────────────────


def test_a_version_latest_ref_puts_a_whole_scheme_in_the_cache() -> None:
    """The route a large entry takes, which is narrower than #386 assumed.

    Measured on the census bundle while fixing this: ordinary reads leave the
    cache holding **floor-weight entries only** — a `Metadataflow`, a
    `MetadataStructure`, a `LazyHierarchy`, 64 apiece. #378 rerouted item
    resolution to a targeted by-URN read, so resolving a `Ref[Code]` no longer
    drags in the whole codelist.

    `_items_from_loaded_scheme` is what remains of the old path, kept for two
    cases its docstring names: a `Concept`, and a `version="latest"` item URN
    whose stored `code_urn` carries a concrete version and so cannot be matched
    directly. That second case still loads the entire parent scheme eagerly and
    promotes it — so on a bundle whose refs say `latest`, one slot holds
    579,951 codes and ~400 MB.

    The bound is worth having either way: an entry count could not describe
    that, and the eventual size of an entry is not something the cache gets to
    assume. But the 4.4 GB figure in the issue needs this qualification, and it
    is why this test exists rather than an assertion that the default read path
    fills the cache — which, since #378, it does not.
    """
    codes = [sl.Code(id=f"C{i}", name={"en": f"c{i}"}) for i in range(5_000)]
    codelist = sl.Codelist(id="CL_BIG", maintainer="AG", version="1.0", name={"en": "big"}, codes=codes)
    latest = sl.Code.parse_urn("urn:sdmx:org.sdmx.infomodel.codelist.Code=AG:CL_BIG(latest).C0")
    node = sl.HierarchicalCode(id="N0", code=sl.Ref[sl.Code].unresolved(latest))
    hierarchy = sl.Hierarchy(id="H", maintainer="AG", version="1.0", name={"en": "h"}, tree=[node])

    with LocalRegistry(":memory:") as local:
        local.add_all([codelist, hierarchy])
        local.get(sl.Hierarchy.urn_for(agency="AG", id="H", version="1.0"), eager=True)
        info = local.resolve_cache_info()

    assert info["entries"] == 1, "one artefact resident"
    assert info["weight"] == 5_000, (
        "and it weighs its whole codelist, which is the point — under the old "
        "bound this same state read as 1 of 256, or 0.4% full"
    )
    assert info["weight"] <= info["max_items"]


def test_that_scheme_is_refused_when_it_cannot_fit() -> None:
    """The same read against a bound smaller than the scheme.

    End-to-end rather than on `_FIFOCache` directly, because the refusal has to
    survive the promote step: entries reach the cache from `_CallScopedMemo`
    after the call succeeds, and a refusal there must not fail the read.
    """
    codes = [sl.Code(id=f"C{i}", name={"en": f"c{i}"}) for i in range(5_000)]
    codelist = sl.Codelist(id="CL_BIG", maintainer="AG", version="1.0", name={"en": "big"}, codes=codes)
    latest = sl.Code.parse_urn("urn:sdmx:org.sdmx.infomodel.codelist.Code=AG:CL_BIG(latest).C0")
    node = sl.HierarchicalCode(id="N0", code=sl.Ref[sl.Code].unresolved(latest))
    hierarchy = sl.Hierarchy(id="H", maintainer="AG", version="1.0", name={"en": "h"}, tree=[node])

    with LocalRegistry(":memory:", resolve_cache_items=1_000) as local:
        local.add_all([codelist, hierarchy])
        read = local.get(sl.Hierarchy.urn_for(agency="AG", id="H", version="1.0"), eager=True)
        info = local.resolve_cache_info()

    assert read is not None, "the read still succeeds — refusing to cache is not refusing to serve"
    assert info["entries"] == 0
    assert info["weight"] == 0
    assert info["refused"] == 1, "and it says so, so a consumer can tell this from a cold cache"
