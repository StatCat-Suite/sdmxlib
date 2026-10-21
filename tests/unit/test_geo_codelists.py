"""SDMX 3.x's geographic codelists are modelled, not refused (#422).

A `GeographicCodelist` and a `GeoGridCodelist` are codelists — their URN class
is `Codelist` — whose codes are a `GeoFeatureSetCode` and a `GeoGridCode`. The
censuses prove every format and the store carry each field (the maximal fixture
holds one of each); these pin the wire spellings, the class each read gives
back, what 2.1 and SDMX-JSON cannot hold, and the refusals.
"""

import functools
from collections.abc import Callable

import attrs
import pytest
from lxml import etree

import sdmxlib as sl
from sdmxlib.formats.gaps import unrepresentable
from sdmxlib.formats.sdmx_json import reader as json_reader
from sdmxlib.formats.sdmx_json import writer as json_writer
from sdmxlib.formats.sdmx_ml21 import reader as ml21_reader
from sdmxlib.formats.sdmx_ml21 import writer as ml21_writer
from sdmxlib.formats.sdmx_ml30 import reader as ml30_reader
from sdmxlib.formats.sdmx_ml30 import writer as ml30_writer
from sdmxlib.formats.sdmx_ml31 import reader as ml31_reader
from sdmxlib.formats.sdmx_ml31 import writer as ml31_writer
from sdmxlib.model._items import owner_classes
from sdmxlib.model.codelist import (
    Code,
    Codelist,
    GeoFeatureSetCode,
    GeographicCodelist,
    GeoGridCode,
    GeoGridCodelist,
    codelist_of_kind,
)
from sdmxlib.model.collections import ItemList
from sdmxlib.model.message import StructureMessage
from tests.support.json_schema import assert_valid_sdmx_json, compile_json_schema
from tests.support.narrow import present
from tests.support.xsd import SchemaVersion, assert_valid_sdmx_ml, compile_structure_schema

pytestmark = pytest.mark.unit

_V3: dict[SchemaVersion, tuple[Callable[[StructureMessage], bytes], Callable[[bytes], StructureMessage]]] = {
    "3.0": (ml30_writer.write, ml30_reader.read),
    "3.1": (ml31_writer.write, ml31_reader.read),
}


@functools.cache
def _xsd(version: SchemaVersion) -> etree.XMLSchema:
    return compile_structure_schema(version)


def _geographic() -> GeographicCodelist:
    return GeographicCodelist(
        id="CL_REGION",
        maintainer="ESTAT",
        version="1.0.0",
        name={"en": "Regions"},
        codes=[
            GeoFeatureSetCode(id="BE", name={"en": "Belgium"}, value="POLYGON((0 0, 1 0, 1 1, 0 0))"),
            GeoFeatureSetCode(id="BE1", name={"en": "Brussels"}, parent_id="BE", value="POINT(0.5 0.5)"),
        ],
    )


def _grid() -> GeoGridCodelist:
    return GeoGridCodelist(
        id="CL_GRID",
        maintainer="ESTAT",
        version="1.0.0",
        name={"en": "1 km grid"},
        grid_definition="1kmN[0-9]{4}E[0-9]{4}",
        codes=[GeoGridCode(id="C1", name={"en": "Cell"}, geo_cell="1kmN2599E4498")],
    )


def _plain_holding_a_grid_code() -> Codelist:
    """A plain codelist may hold a geographic code: it is in `Code`'s substitution group."""
    return Codelist(
        id="CL_MIXED",
        maintainer="ESTAT",
        version="1.0.0",
        name={"en": "Mixed"},
        codes=[Code(id="A", name={"en": "A"}), GeoGridCode(id="G", name={"en": "G"}, geo_cell="1kmN1E1")],
    )


def _message() -> StructureMessage:
    return StructureMessage(codelists=[_plain_holding_a_grid_code(), _geographic(), _grid()])


# ── the model ─────────────────────────────────────────────────────────────


def test_a_geographic_codelist_is_a_codelist_down_to_its_urn() -> None:
    geographic = _geographic()
    assert isinstance(geographic, Codelist)
    assert str(geographic.urn) == "urn:sdmx:org.sdmx.infomodel.codelist.Codelist=ESTAT:CL_REGION(1.0.0)"
    assert str(geographic.ref_to("BE1").urn) == "urn:sdmx:org.sdmx.infomodel.codelist.Code=ESTAT:CL_REGION(1.0.0).BE1"


def test_an_item_urn_is_looked_up_through_codelist_alone() -> None:
    """The subclasses share `Codelist`'s URN, so trying them too would repeat the lookup."""
    assert owner_classes("Code") == (Codelist,)


@pytest.mark.parametrize(
    ("make", "wrong"),
    [
        (
            lambda codes: GeographicCodelist(name={"en": "CL"}, id="CL", maintainer="A", codes=codes),
            Code(name={"en": "X"}, id="X"),
        ),
        (
            lambda codes: GeoGridCodelist(
                name={"en": "CL"}, id="CL", maintainer="A", grid_definition=".*", codes=codes
            ),
            Code(name={"en": "X"}, id="X"),
        ),
        (
            lambda codes: GeoGridCodelist(
                name={"en": "CL"}, id="CL", maintainer="A", grid_definition=".*", codes=codes
            ),
            GeoFeatureSetCode(name={"en": "X"}, id="X", value="v"),
        ),
    ],
    ids=["geographic-plain", "grid-plain", "grid-feature-set"],
)
def test_a_geographic_codelist_refuses_a_code_of_another_class(
    make: Callable[[list[Code]], Codelist], wrong: Code
) -> None:
    with pytest.raises(TypeError, match="codes must all be"):
        make([wrong])


def test_the_code_class_is_checked_on_evolve_too() -> None:
    """`codes` stays `ItemList[Code]` on the subclass, so the class is a runtime check — on every copy (#492)."""
    grid = _grid()
    with pytest.raises(TypeError, match="codes must all be GeoGridCode; not 'X'"):
        attrs.evolve(grid, codes=ItemList([Code(name={"en": "X"}, id="X")]))


def test_an_unknown_geo_type_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown geographic codelist type 'Geo'"):
        codelist_of_kind(
            "Geo",
            None,
            id="CL",
            maintainer=sl.Agency(id="A"),
            version="1.0",
            name=sl.InternationalString(),
            description=sl.InternationalString(),
            legacy_is_final=None,
            annotations=sl.Annotations(),
            valid_from=None,
            valid_to=None,
            codes=ItemList([]),
            extensions=(),
            is_external_reference=False,
            service_url=None,
            structure_url=None,
            is_partial_language=False,
            is_partial=False,
            uri=None,
            links=(),
        )


# ── SDMX-ML 3.x ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("version", sorted(_V3))
def test_sdmx_ml_3x_round_trips_each_kind(version: SchemaVersion) -> None:
    write, read = _V3[version]
    written = write(_message())
    assert_valid_sdmx_ml(written, _xsd(version))
    back = read(written)
    assert {cl.id: type(cl) for cl in back.codelists} == {
        "CL_MIXED": Codelist,
        "CL_REGION": GeographicCodelist,
        "CL_GRID": GeoGridCodelist,
    }
    for cl in _message().codelists:
        assert back.codelists[cl.id] == cl
    assert back.lost == ()
    assert back.refused == ()


@pytest.mark.parametrize("version", sorted(_V3))
def test_sdmx_ml_3x_spells_each_kind_as_the_xsd_does(version: SchemaVersion) -> None:
    write, _ = _V3[version]
    root = etree.fromstring(write(_message()))
    ns = {"s": root.nsmap.get("str") or next(v for k, v in root.nsmap.items() if k and k.startswith("str"))}
    geographic = present(root.find(".//s:GeographicCodelists/s:GeographicCodelist", ns))
    assert geographic.get("geoType") == "GeographicCodelist"
    assert [(c.get("id"), c.get("value")) for c in geographic.findall("s:GeoFeatureSetCode", ns)] == [
        ("BE", "POLYGON((0 0, 1 0, 1 1, 0 0))"),
        ("BE1", "POINT(0.5 0.5)"),
    ]
    grid = present(root.find(".//s:GeoGridCodelists/s:GeoGridCodelist", ns))
    assert grid.get("geoType") == "GeoGridCodelist"
    assert grid.findtext("s:GridDefinition", namespaces=ns) == "1kmN[0-9]{4}E[0-9]{4}"
    assert grid.findtext("s:GeoGridCode/s:GeoCell", namespaces=ns) == "1kmN2599E4498"
    # A plain codelist keeps its own container, its geographic code beside a plain one.
    mixed = present(root.find(".//s:Codelists/s:Codelist", ns))
    assert [etree.QName(c).localname for c in mixed][-2:] == ["Code", "GeoGridCode"]


# ── SDMX-JSON 2.0 ──────────────────────────────────────────────────────────


def test_sdmx_json_round_trips_each_geographic_kind() -> None:
    message = StructureMessage(codelists=[_geographic(), _grid()])
    written = json_writer.write(message)
    assert_valid_sdmx_json(written, compile_json_schema("structure", "2.0"))
    assert_valid_sdmx_json(written, compile_json_schema("structure", "2.1"))
    back = json_reader.read(written)
    assert [type(cl) for cl in back.codelists] == [GeographicCodelist, GeoGridCodelist]
    assert list(back.codelists) == list(message.codelists)
    assert back.refused == ()


def test_sdmx_json_has_no_geographic_code_in_a_plain_codelist() -> None:
    """SDMX-JSON has no substitution groups: only a geographic codelist's own key holds one."""
    mixed = _plain_holding_a_grid_code()
    assert [gap.field for gap in unrepresentable(mixed, "json")] == ["codes[].geo_cell"]
    back = json_reader.read(json_writer.write(StructureMessage(codelists=[mixed])))
    assert type(back.codelists["CL_MIXED"].codes["G"]) is Code
    # The geographic kinds carry their own codes, so they lose nothing.
    assert unrepresentable(_geographic(), "json") == ()
    assert unrepresentable(_grid(), "json") == ()


# ── SDMX-ML 2.1 ────────────────────────────────────────────────────────────


def test_sdmx_ml_2_1_writes_each_as_the_codelist_it_is() -> None:
    back = ml21_reader.read(ml21_writer.write(_message()))
    assert {cl.id: type(cl) for cl in back.codelists} == {
        "CL_MIXED": Codelist,
        "CL_REGION": Codelist,
        "CL_GRID": Codelist,
    }
    assert all(type(code) is Code for cl in back.codelists for code in cl.codes)
    assert [cl.codes.ids() for cl in back.codelists] == [cl.codes.ids() for cl in _message().codelists]


def test_sdmx_ml_2_1_declares_what_it_loses() -> None:
    assert sorted(gap.field for gap in unrepresentable(_geographic(), "2.1")) == ["codes[].value", "geo_type"]
    assert sorted(gap.field for gap in unrepresentable(_grid(), "2.1")) == [
        "codes[].geo_cell",
        "geo_type",
        "grid_definition",
    ]
    assert [gap.field for gap in unrepresentable(_plain_holding_a_grid_code(), "2.1")] == ["codes[].geo_cell"]
    assert unrepresentable(_geographic(), "3.0") == ()


# ── the store ──────────────────────────────────────────────────────────────


def test_the_store_gives_back_each_class() -> None:
    with sl.LocalRegistry(":memory:") as registry:
        for codelist in _message().codelists:
            registry.add(codelist)
        for codelist in _message().codelists:
            eager = registry.get(codelist.urn, eager=True)
            assert type(eager) is type(codelist)
            assert eager == codelist
            lazy = registry.get(codelist.urn)
            assert isinstance(lazy, sl.LazyCodelist)
            assert lazy.materialize() == codelist
        mixed = registry.get(_plain_holding_a_grid_code().urn)
        assert isinstance(mixed, sl.LazyCodelist)
        assert mixed.codes_for(["G"]) == {"G": GeoGridCode(id="G", name={"en": "G"}, geo_cell="1kmN1E1")}


def test_a_codelist_re_added_as_plain_loses_its_geographic_row() -> None:
    """The cascade clears `geo_codelist`, so a replacement is not read back geographic."""
    grid = _grid()
    plain = Codelist(id=grid.id, maintainer="ESTAT", version=grid.version, name=grid.name)
    with sl.LocalRegistry(":memory:") as registry:
        registry.add(grid)
        registry.add(plain)
        assert type(registry.get(grid.urn, eager=True)) is Codelist
