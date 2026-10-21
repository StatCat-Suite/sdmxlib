"""What #442 gave the store: item `uri`, level annotations, value-map validity, `Agency.parent`.

`test_store_round_trip.py` holds each of these over the maximal fixture, but
only through `get(eager=True)`. A store has more ways out than that — the lazy
proxies, and `collect()` on the path language — each building items from rows
in its own reader, and a column read by one of them is not read by the others
until someone makes it so (#322). These walk every way out.
"""

from __future__ import annotations

from datetime import date

import pytest

import sdmxlib as sl
from sdmxlib.model.annotations import Annotation
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy, Level
from sdmxlib.model.mapping import RepresentationMap, RepresentationMapping
from sdmxlib.model.organisation import Agency
from sdmxlib.model.ref import Ref
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.query.accessors import q
from sdmxlib.storage.lazy import LazyCodelist, LazyHierarchy
from sdmxlib.storage.resolve import level_urn

pytestmark = pytest.mark.unit

_CODE_URI = "https://example.test/code/A"
_NODE_URI = "https://example.test/node/TOP"
_LEVEL_URI = "https://example.test/level/L1"
_LEVEL = Level(
    id="L1",
    name={"en": "Level one"},
    annotations=[Annotation(id="lv", type="NOTE", title="level note")],
    uri=_LEVEL_URI,
)


def _codelist() -> Codelist:
    return Codelist(
        id="CL",
        maintainer=Agency(id="AG"),
        name={"en": "CL"},
        codes=[Code(id="A", name={"en": "a"}, uri=_CODE_URI), Code(id="B", name={"en": "b"})],
    )


def _hierarchy() -> Hierarchy:
    code = Ref[Code].unresolved(
        SdmxUrn.make(Code, package="codelist", artefact_class="Code", agency="AG", id="CL", version="1.0", item_id="A")
    )
    return Hierarchy(
        id="H",
        maintainer=Agency(id="AG"),
        name={"en": "H"},
        has_formal_levels=True,
        levels=[_LEVEL],
        tree=[HierarchicalCode(id="TOP", code=code, level=_LEVEL, uri=_NODE_URI)],
    )


def test_a_code_uri_survives_every_way_out_of_the_store() -> None:
    codelist = _codelist()
    with sl.LocalRegistry(":memory:") as reg:
        reg.add(codelist)
        eager = reg.get(codelist.urn, eager=True)
        lazy = reg.get(codelist.urn)
        collected = reg.query(q.code).filter(q.code.id == "A").collect()

        assert isinstance(eager, Codelist)
        assert eager.codes["A"].uri == _CODE_URI
        assert eager.codes["B"].uri is None
        assert isinstance(lazy, LazyCodelist)
        assert lazy.materialize().codes["A"].uri == _CODE_URI
        assert [c.uri for c in lazy.codes.filter(q.code.id == "A").collect()] == [_CODE_URI]
        assert [c.uri for c in collected] == [_CODE_URI]
        assert eager == codelist


def test_a_node_uri_and_a_level_survive_every_way_out_of_the_store() -> None:
    hierarchy = _hierarchy()
    with sl.LocalRegistry(":memory:") as reg:
        reg.add(hierarchy)
        eager = reg.get(hierarchy.urn, eager=True)
        lazy = reg.get(hierarchy.urn)
        (collected,) = reg.query(q.hcode).filter(q.hcode.id == "TOP").collect()

        assert isinstance(eager, Hierarchy)
        assert eager == hierarchy
        assert isinstance(lazy, LazyHierarchy)
        materialised = lazy.materialize()
        assert list(materialised.levels) == [_LEVEL]
        assert lazy.levels == (_LEVEL,)
        for node in (eager.tree[0], materialised.tree[0], collected):
            assert node.uri == _NODE_URI
            assert node.level == _LEVEL


def test_level_annotations_go_with_the_hierarchy() -> None:
    """Owned by the level's own item URN under the hierarchy, so the delete
    cascade — keyed on the owning maintainable — clears them with it."""
    hierarchy = _hierarchy()
    with sl.LocalRegistry(":memory:") as reg:

        def stored() -> int:
            owner = level_urn(str(hierarchy.urn), "L1")
            frame = reg.sql("SELECT count(*) AS n FROM sdmx.annotation WHERE owner_urn = $owner", owner=owner).collect()
            return int(frame["n"][0])

        reg.add(hierarchy)
        assert stored() == 1
        assert reg.drop(hierarchy.urn)
        assert stored() == 0


def test_value_map_validity_round_trips() -> None:
    rm = RepresentationMap(
        id="RM",
        maintainer=Agency(id="AG"),
        name={"en": "RM"},
        sources=(Ref[Codelist].unresolved(Codelist.urn_for(agency="AG", id="SRC", version="1.0")),),
        targets=(Ref[Codelist].unresolved(Codelist.urn_for(agency="AG", id="TGT", version="1.0")),),
        mappings=(
            RepresentationMapping(
                sources=("a",), targets=("x",), valid_from=date(2020, 1, 1), valid_to=date(2021, 12, 31)
            ),
            RepresentationMapping(sources=("b",), targets=("y",)),
        ),
    )
    with sl.LocalRegistry(":memory:") as reg:
        reg.add(rm)
        back = reg.get(rm.urn)
    assert isinstance(back, RepresentationMap)
    assert back.mappings == rm.mappings
