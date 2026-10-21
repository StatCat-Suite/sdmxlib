"""Tests for ItemList[T] and ArtefactList[T]."""

from dataclasses import dataclass

import pytest

from sdmxlib.model.collections import ArtefactList, ItemList, Maintainable
from sdmxlib.model.expr import item
from sdmxlib.model.ref import SdmxRef
from sdmxlib.model.urn import SdmxUrn

pytestmark = pytest.mark.unit


# ── Fixtures ──────────────────────────────────────────────────────────────


# `ItemList[T: SupportsId]` binds on a *mutable* `id: str`, so a frozen
# dataclass does not satisfy it — unlike `Maintainable` / `SupportsUrn`, which
# use the read-only property form for exactly that reason. No model class is
# affected (none of the `@frozen` ones is an ItemList element), so these
# doubles are unfrozen rather than the protocol widened for a case that does
# not exist yet (#347).
@dataclass
class Item:
    id: str
    value: int = 0


@dataclass
class Artefact:
    id: str
    agency_id: str
    version: str = "1.0"
    size: int = 0


def _items() -> list[Item]:
    return [Item("A", 3), Item("B", 1), Item("C", 2)]


def _artefacts() -> list[Artefact]:
    return [
        Artefact("CL_FREQ", "SDMX", "1.0", 5),
        Artefact("CL_AREA", "SDMX", "2.0", 10),
        Artefact("CL_FREQ", "ESTAT", "1.0", 3),
    ]


# ── Maintainable protocol ─────────────────────────────────────────────────


def test_maintainable_protocol():
    a = Artefact("X", "A")
    assert isinstance(a, Maintainable)


# ── ItemList ──────────────────────────────────────────────────────────────


def test_itemlist_len():
    assert len(ItemList(_items())) == 3


def test_itemlist_empty():
    assert not ItemList()
    assert ItemList(_items())


def test_itemlist_getitem_int():
    lst = ItemList(_items())
    assert lst[0].id == "A"
    assert lst[-1].id == "C"


def test_itemlist_getitem_str():
    lst = ItemList(_items())
    assert lst["B"].id == "B"


def test_itemlist_getitem_str_missing():
    lst = ItemList(_items())
    with pytest.raises(KeyError):
        _ = lst["Z"]


def test_itemlist_get():
    lst = ItemList(_items())
    assert lst.get("A") is not None
    assert lst.get("Z") is None


def test_itemlist_contains_str():
    lst = ItemList(_items())
    assert "A" in lst
    assert "Z" not in lst


def test_itemlist_contains_item():
    lst = ItemList(_items())
    item = lst["A"]
    assert item in lst


def test_itemlist_ids():
    lst = ItemList(_items())
    assert lst.ids() == ["A", "B", "C"]


def test_itemlist_filter():
    lst = ItemList(_items())
    result = lst.filter(lambda i: i.value > 1)
    assert result.ids() == ["A", "C"]


def test_itemlist_first_where():
    lst = ItemList(_items())
    found = lst.first_where(lambda i: i.value == 1)
    assert found is not None
    assert found.id == "B"


def test_itemlist_first_where_none():
    lst = ItemList(_items())
    assert lst.first_where(lambda i: i.value == 99) is None


def test_itemlist_partition():
    lst = ItemList(_items())
    yes, no = lst.partition(lambda i: i.value > 1)
    assert yes.ids() == ["A", "C"]
    assert no.ids() == ["B"]


def test_itemlist_sort_by_asc():
    lst = ItemList(_items())
    sorted_lst = lst.sort_by(lambda i: i.value)
    assert sorted_lst.ids() == ["B", "C", "A"]


def test_itemlist_sort_by_desc():
    lst = ItemList(_items())
    sorted_lst = lst.sort_by(lambda i: i.value, descending=True)
    assert sorted_lst.ids() == ["A", "C", "B"]


def test_itemlist_to_dict_default():
    lst = ItemList(_items())
    d = lst.to_dict()
    assert set(d.keys()) == {"A", "B", "C"}
    assert d["A"].value == 3


def test_itemlist_to_dict_custom_key():
    lst = ItemList(_items())
    d = lst.to_dict(key=lambda i: i.value)
    assert d[3].id == "A"


def test_itemlist_equality():
    a = ItemList(_items())
    b = ItemList(_items())
    assert a == b


def test_itemlist_is_unhashable():
    """Its contents are mutable, so it is not a key (#290).

    This asserted `hash(a) == hash(b)` and passed — but only because the `Item`
    double at the top of this module *was* a `@dataclass(frozen=True)`. Every
    real domain item is an attrs `@define`, so the test exercised hashability
    with input no caller has. The behaviour it confirmed did not exist for any
    type the class is used with. The double is unfrozen now (#347), which makes
    it resemble the real thing in that respect too.
    """
    with pytest.raises(TypeError, match="unhashable type: 'ItemList'"):
        hash(ItemList(_items()))


def test_itemlist_repr():
    lst = ItemList(_items())
    r = repr(lst)
    assert "ItemList" in r


def test_itemlist_iter():
    lst = ItemList(_items())
    ids = [i.id for i in lst]
    assert ids == ["A", "B", "C"]


# ── ArtefactList ──────────────────────────────────────────────────────────


def test_artefactlist_len():
    assert len(ArtefactList(_artefacts())) == 3


def test_artefactlist_getitem_int():
    lst = ArtefactList(_artefacts())
    assert lst[0].id == "CL_FREQ"


def test_artefactlist_getitem_bare_id_unambiguous():
    lst = ArtefactList([Artefact("CL_AREA", "SDMX")])
    assert lst["CL_AREA"].id == "CL_AREA"


def test_artefactlist_getitem_bare_id_ambiguous():
    lst = ArtefactList(_artefacts())
    with pytest.raises(KeyError, match="Ambiguous"):
        _ = lst["CL_FREQ"]


def test_artefactlist_get_bare_id_ambiguous_returns_none():
    lst = ArtefactList(_artefacts())
    assert lst.get("CL_FREQ") is None


def test_artefactlist_getitem_agency_qualified():
    lst = ArtefactList(_artefacts())
    result = lst["SDMX:CL_FREQ"]
    assert result.agency_id == "SDMX"
    assert result.id == "CL_FREQ"


def test_artefactlist_getitem_fully_qualified():
    lst = ArtefactList(_artefacts())
    result = lst["SDMX:CL_FREQ(1.0)"]
    assert result.version == "1.0"
    assert result.agency_id == "SDMX"


def test_artefactlist_getitem_missing():
    lst = ArtefactList(_artefacts())
    with pytest.raises(KeyError):
        _ = lst["SDMX:NONEXISTENT"]


def test_artefactlist_getitem_urn_exact():
    lst = ArtefactList(_artefacts())
    urn = SdmxUrn.make(
        Artefact,
        package="codelist",
        artefact_class="Codelist",
        agency="SDMX",
        id="CL_FREQ",
        version="1.0",
    )
    result = lst[urn]
    assert result.agency_id == "SDMX"
    assert result.id == "CL_FREQ"


def test_artefactlist_getitem_urn_latest_matches_any():
    lst = ArtefactList([Artefact("CL_AREA", "SDMX", "2.0")])
    urn = SdmxUrn.make(
        Artefact,
        package="codelist",
        artefact_class="Codelist",
        agency="SDMX",
        id="CL_AREA",
        version="latest",
    )
    result = lst[urn]
    assert result.version == "2.0"


def test_artefactlist_getitem_sdmxref():
    lst = ArtefactList(_artefacts())
    ref = SdmxRef(agency="ESTAT", id="CL_FREQ")
    result = lst[ref]
    assert result.agency_id == "ESTAT"


def test_artefactlist_getitem_sdmxref_ambiguous():
    lst = ArtefactList(_artefacts())
    ref = SdmxRef(id="CL_FREQ")  # matches SDMX and ESTAT
    with pytest.raises(KeyError, match="Ambiguous"):
        _ = lst[ref]


def test_artefactlist_ids():
    lst = ArtefactList([Artefact("CL_AREA", "SDMX")])
    assert lst.ids() == ["CL_AREA"]


def test_artefactlist_ids_qualified():
    lst = ArtefactList([Artefact("CL_AREA", "SDMX")])
    assert lst.ids(qualified=True) == ["SDMX:CL_AREA(1.0)"]


def test_artefactlist_filter():
    lst = ArtefactList(_artefacts())
    result = lst.filter(lambda a: a.agency_id == "SDMX")
    assert len(result) == 2


def test_artefactlist_partition():
    lst = ArtefactList(_artefacts())
    sdmx, other = lst.partition(lambda a: a.agency_id == "SDMX")
    assert len(sdmx) == 2
    assert len(other) == 1


def test_artefactlist_sort_by():
    lst = ArtefactList(_artefacts())
    sorted_lst = lst.sort_by(lambda a: a.size)
    assert sorted_lst[0].size == 3


def test_artefactlist_first_where():
    lst = ArtefactList(_artefacts())
    found = lst.first_where(lambda a: a.size == 10)
    assert found is not None
    assert found.id == "CL_AREA"


def test_artefactlist_to_dict():
    lst = ArtefactList([Artefact("CL_AREA", "SDMX")])
    d = lst.to_dict()
    assert "CL_AREA" in d


def test_artefactlist_equality():
    a = ArtefactList(_artefacts())
    b = ArtefactList(_artefacts())
    assert a == b


def test_artefactlist_repr():
    lst = ArtefactList([Artefact("CL_AREA", "SDMX")])
    r = repr(lst)
    assert "ArtefactList" in r
    assert "SDMX:CL_AREA(1.0)" in r


# ── item() expression API ─────────────────────────────────────────────────


def test_item_expr_equality():
    lst = ItemList(_items())
    result = lst.filter(item("id") == "A")
    assert result.ids() == ["A"]


def test_item_expr_inequality():
    lst = ItemList(_items())
    result = lst.filter(item("id") != "B")
    assert result.ids() == ["A", "C"]


def test_item_expr_truthy():
    lst = ItemList([Item(id="X", value=1), Item(id="Y", value=0), Item(id="Z", value=2)])
    result = lst.filter(item("value"))
    assert result.ids() == ["X", "Z"]


def test_item_expr_invert():
    lst = ItemList([Item(id="X", value=1), Item(id="Y", value=0), Item(id="Z", value=2)])
    result = lst.filter(~item("value"))
    assert result.ids() == ["Y"]


def test_item_expr_is_in():
    lst = ItemList(_items())
    result = lst.filter(item("id").is_in(["A", "C"]))
    assert result.ids() == ["A", "C"]


def test_item_expr_is_null():
    @dataclass
    class MaybeItem:
        id: str
        parent: str | None = None

    lst = ItemList([MaybeItem("X"), MaybeItem("Y", parent="X"), MaybeItem("Z")])
    result = lst.filter(item("parent").is_null())
    assert result.ids() == ["X", "Z"]
