"""`SdmxUrn.owner()`: an item or component URN to its maintainable's URN (GH-404)."""

import pytest

import sdmxlib as sl
from sdmxlib.model import _items
from sdmxlib.model._items import _maintainable_classes, _owner_map

pytestmark = pytest.mark.unit

_PREFIX = "urn:sdmx:org.sdmx.infomodel"


@pytest.mark.parametrize(
    ("item", "owner"),
    [
        ("conceptscheme.Concept=A:CS(1.0).FREQ", "conceptscheme.ConceptScheme=A:CS(1.0)"),
        ("codelist.Code=A:CL_FREQ(1.0.0).M", "codelist.Codelist=A:CL_FREQ(1.0.0)"),
        ("datastructure.Dimension=A:DSD(2.0.0-draft).FREQ", "datastructure.DataStructure=A:DSD(2.0.0-draft)"),
        # 2.1's spelling of the measure, which the model declares as `Measure`.
        ("datastructure.PrimaryMeasure=A:DSD(1.0).OBS_VALUE", "datastructure.DataStructure=A:DSD(1.0)"),
        # A nested item: the id is a path, and all of it goes.
        ("categoryscheme.Category=A:CAT(1.0).ECO.GDP", "categoryscheme.CategoryScheme=A:CAT(1.0)"),
        ("base.Agency=SDMX:AGENCIES(1.0).ESTAT", "base.AgencyScheme=SDMX:AGENCIES(1.0)"),
    ],
)
def test_an_item_urn_names_its_scheme(item: str, owner: str):
    assert str(sl.SdmxUrn.parse(f"{_PREFIX}.{item}").owner()) == f"{_PREFIX}.{owner}"


def test_a_maintainable_urn_is_its_own_owner():
    urn = sl.Codelist.urn_for(agency="A", id="CL_FREQ", version="1.0")
    assert urn.owner() == urn


def test_an_item_class_no_scheme_holds_is_refused():
    with pytest.raises(ValueError, match=r"'Widget' is held by no scheme class"):
        sl.SdmxUrn.parse(f"{_PREFIX}.codelist.Widget=A:CL(1.0).X").owner()


def test_an_item_class_two_schemes_hold_is_refused(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(_items, "owner_classes", lambda _: (sl.Codelist, sl.ValueList))
    with pytest.raises(ValueError, match=r"held by Codelist, ValueList, so it has no one owner"):
        sl.SdmxUrn.parse(f"{_PREFIX}.codelist.Code=A:CL(1.0).X").owner()


def test_every_item_class_has_exactly_one_owner():
    """Pins what `owner()` relies on: a second owner for any class makes its URNs refused."""
    owners = _owner_map(_maintainable_classes())
    assert len(owners) == 24
    assert {spelling: len(held) for spelling, held in owners.items() if len(held) != 1} == {}
