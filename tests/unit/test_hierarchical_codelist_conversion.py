"""2.1's HierarchicalCodelist and 3.x's Hierarchy, converted both ways (#421).

SDMX 3.0 made `Hierarchy` maintainable and removed the HierarchicalCodelist
around it. Read from 2.1, each hierarchy takes the id FMR gives it,
``<inner>@<codelist>``, so OECD's 210 hierarchies called ``H`` stop sharing one
URN. A codelist that only wraps one hierarchy is read as that hierarchy. 3.x
leaves the codelist out and writes its hierarchies; 2.1 leaves a
HierarchyAssociation out.
"""

from datetime import UTC, datetime

import attrs
import pytest

from sdmxlib.formats import parse, serialize
from sdmxlib.formats.gaps import dropped, unrepresentable
from sdmxlib.model._container import inner_id, member_id
from sdmxlib.model.codelist import Code
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.hierarchy import (
    HierarchicalCode,
    HierarchicalCodelist,
    Hierarchy,
    HierarchyAssociation,
)
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import Ref, artefact_urn
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.model.validation import ValidationError
from tests.support.roundtrip import as_structure, structure_schema
from tests.support.xsd import assert_valid_sdmx_ml

pytestmark = pytest.mark.unit


# ── The id rule ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("inner", "hcl", "want"),
    [
        pytest.param("H", "HCL_AGE", "H@HCL_AGE", id="composed"),
        pytest.param("HCL_X", "HCL_X", "HCL_X", id="a-wrapper-keeps-its-id"),
        pytest.param("EU@HCL_C", "EU@HCL_C", "EU@HCL_C", id="fmr-served-wrapper"),
        pytest.param("H", "EU@HCL_C", "H@EU@HCL_C", id="inside-an-fmr-id"),
    ],
)
def test_member_id_and_its_inverse(inner: str, hcl: str, want: str) -> None:
    assert member_id(inner, hcl) == want
    assert inner_id(want, hcl) == inner


@pytest.mark.parametrize(
    ("hierarchy_id", "hcl"),
    [
        pytest.param("H", "HCL", id="no-suffix"),
        pytest.param("@HCL", "HCL", id="empty-inner"),
        pytest.param("HCL@HCL", "HCL", id="inner-is-the-codelist"),
        pytest.param("H@OTHER", "HCL", id="another-codelist"),
    ],
)
def test_an_id_member_id_never_gives_has_no_inner_id(hierarchy_id: str, hcl: str) -> None:
    assert inner_id(hierarchy_id, hcl) is None


# ── Reading 2.1 ───────────────────────────────────────────────────────────────


_HEAD = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<mes:Structure xmlns:mes="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message"'
    b' xmlns:str="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure"'
    b' xmlns:com="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common">'
    b"<mes:Header><mes:ID>T</mes:ID><mes:Test>false</mes:Test>"
    b'<mes:Prepared>2024-01-01T00:00:00</mes:Prepared><mes:Sender id="T"/></mes:Header>'
    b"<mes:Structures><str:HierarchicalCodelists>"
)
_TAIL = b"</str:HierarchicalCodelists></mes:Structures></mes:Structure>"


def _hcl(hcl_id: str, *hierarchies: tuple[str, str], name: str | None = None, attrs_: str = "") -> bytes:
    """An HCL in agency AG, holding (id, name) hierarchies of one node each."""
    body = b"".join(
        b'<str:Hierarchy id="%s"><com:Name xml:lang="en">%s</com:Name>'
        b'<str:HierarchicalCode id="N"><str:Code><Ref agencyID="AG" maintainableParentID="CL" '
        b'maintainableParentVersion="1.0" id="C" package="codelist" class="Code"/></str:Code>'
        b"</str:HierarchicalCode></str:Hierarchy>" % (h_id.encode(), h_name.encode())
        for h_id, h_name in hierarchies
    )
    head = b'<str:HierarchicalCodelist id="%s" agencyID="AG" version="1.0"%s>' % (hcl_id.encode(), attrs_.encode())
    name_el = b'<com:Name xml:lang="en">%s</com:Name>' % (name or hcl_id).encode()
    return head + name_el + body + b"</str:HierarchicalCodelist>"


def _read(*hcls: bytes) -> StructureMessage:
    return as_structure(parse(_HEAD + b"".join(hcls) + _TAIL))


def test_hierarchies_of_one_inner_id_in_two_codelists_stay_two() -> None:
    """OECD's shape: every codelist names its hierarchy ``H``."""
    msg = _read(_hcl("HCL_AGE", ("H", "Age")), _hcl("HCL_SEX", ("H", "Sex")))
    assert [(h.id, h.name["en"].text) for h in msg.hierarchies] == [("H@HCL_AGE", "Age"), ("H@HCL_SEX", "Sex")]
    assert [[str(ref.urn) for ref in hcl.hierarchies] for hcl in msg.hierarchical_codelists] == [
        [str(artefact_urn(h))] for h in msg.hierarchies
    ]


def test_a_hierarchy_takes_its_codelists_maintainable_attributes() -> None:
    msg = _read(
        _hcl(
            "HCL",
            ("H", "h"),
            attrs_=' validFrom="2020-01-01T00:00:00" isExternalReference="true" serviceURL="https://example.org/r"',
        )
    )
    (hierarchy,) = msg.hierarchies
    assert hierarchy.valid_from == datetime(2020, 1, 1, tzinfo=UTC)
    assert (hierarchy.is_external_reference, hierarchy.service_url) == (True, "https://example.org/r")


def test_a_codelist_that_only_wraps_its_hierarchy_is_read_as_the_hierarchy() -> None:
    """ECB's, BIS's and every 2.1 writer's: one hierarchy, the same id and name."""
    msg = _read(_hcl("EU@HCL_C", ("EU@HCL_C", "EU@HCL_C")))
    assert [h.id for h in msg.hierarchies] == ["EU@HCL_C"]
    assert not msg.hierarchical_codelists
    assert (msg.refused, msg.lost) == ((), ())


@pytest.mark.parametrize(
    "hcl",
    [
        pytest.param(_hcl("HCL", ("HCL", "HCL"), name="Another name"), id="its-own-name"),
        pytest.param(_hcl("HCL", ("HCL", "HCL"), ("B", "B")), id="two-hierarchies"),
        pytest.param(_hcl("HCL", ("H", "HCL")), id="another-id"),
        pytest.param(_hcl("HCL", ("HCL", "HCL"), attrs_=' isFinal="true"'), id="its-own-finality"),
    ],
)
def test_a_codelist_saying_more_than_its_hierarchy_is_kept(hcl: bytes) -> None:
    assert [c.id for c in _read(hcl).hierarchical_codelists] == ["HCL"]


# ── Writing 2.1 ───────────────────────────────────────────────────────────────


def _node() -> HierarchicalCode:
    code = SdmxUrn.make(
        Code, package="codelist", artefact_class="Code", agency="AG", id="CL", version="1.0", item_id="C"
    )
    return HierarchicalCode(id="N", code=code)


def test_2_1_writes_each_hierarchy_by_its_id_in_the_codelist_and_reads_it_back() -> None:
    msg = _read(_hcl("HCL_AGE", ("H", "Age")), _hcl("HCL_SEX", ("H", "Sex")))
    written = serialize(msg, version="2.1")
    assert_valid_sdmx_ml(written, structure_schema("2.1"))
    assert written.count(b'<str:Hierarchy id="H">') == 2
    back = as_structure(parse(written))
    assert (back.hierarchies, back.hierarchical_codelists) == (msg.hierarchies, msg.hierarchical_codelists)


def _standalone() -> Hierarchy:
    return Hierarchy(
        id="H_GEO",
        maintainer="AG",
        name={"en": "Geography"},
        valid_from=datetime(2020, 1, 1, tzinfo=UTC),
        is_external_reference=True,
        service_url="https://example.org/r",
        tree=[_node()],
    )


def test_a_standalone_hierarchy_comes_back_from_2_1_as_itself() -> None:
    """Its wrapper is read away again, with the validity it carried (#421)."""
    written = serialize(StructureMessage(hierarchies=[_standalone()]), version="2.1")
    back = as_structure(parse(written))
    assert (list(back.hierarchies), list(back.hierarchical_codelists)) == ([_standalone()], [])
    assert unrepresentable(_standalone(), "2.1") == ()


def _claiming(hierarchy: Hierarchy) -> StructureMessage:
    hcl = HierarchicalCodelist(
        id="HCL", maintainer="AG", name={"en": "hcl"}, hierarchies=(Ref[object].unresolved(hierarchy.urn),)
    )
    return StructureMessage(hierarchies=[hierarchy], hierarchical_codelists=[hcl])


_AGREES = Hierarchy(id="H@HCL", maintainer="AG", name={"en": "h"}, tree=[_node()])


@pytest.mark.parametrize(
    ("changed", "named"),
    [
        pytest.param(attrs.evolve(_AGREES, id="H"), "id", id="id"),
        pytest.param(attrs.evolve(_AGREES, version="2.0"), "version", id="version"),
        pytest.param(attrs.evolve(_AGREES, valid_to=datetime(2030, 1, 1, tzinfo=UTC)), "valid_to", id="validity"),
        pytest.param(
            attrs.evolve(_AGREES, structure_url="https://example.org/s"), "structure_url", id="external-reference"
        ),
    ],
)
def test_2_1_refuses_a_hierarchy_its_codelist_would_give_back_changed(changed: Hierarchy, named: str) -> None:
    assert serialize(_claiming(_AGREES), version="2.1")
    with pytest.raises(ValidationError, match=f"container's {named}, not its own"):
        serialize(_claiming(changed), version="2.1")


# ── Each format's half: what it leaves out ───────────────────────────────────


@pytest.mark.parametrize("version", ["3.0", "3.1", "json"])
def test_3x_writes_the_hierarchies_and_leaves_the_codelist_out(version: str) -> None:
    msg = _read(_hcl("HCL_AGE", ("H", "Age")), _hcl("HCL_SEX", ("H", "Sex")))
    (gap,) = unrepresentable(msg.hierarchical_codelists[0], version)
    assert gap.field == "sdmx_class"
    assert [drop.urn for drop in dropped(msg, version)] == [
        str(artefact_urn(hcl)) for hcl in msg.hierarchical_codelists
    ]
    written = serialize(msg, version=version)
    if version != "json":
        # Two hierarchies called H were one URN, which 3.0's UniqueHierarchy
        # key refused for 167 of OECD's.
        assert_valid_sdmx_ml(written, structure_schema(version))
    back = as_structure(parse(written))
    assert (back.hierarchies, list(back.hierarchical_codelists)) == (msg.hierarchies, [])


def test_2_1_leaves_a_hierarchy_association_out() -> None:
    hierarchy = _standalone()
    association = HierarchyAssociation(
        id="HA",
        maintainer="AG",
        name={"en": "ha"},
        linked_hierarchy=Ref.to(hierarchy),
        linked_object=Ref[object].unresolved(Dataflow.urn_for(agency="AG", id="DF", version="1.0")),
    )
    msg = StructureMessage(hierarchies=[hierarchy], hierarchy_associations=[association])
    assert [gap.field for gap in unrepresentable(association, "2.1")] == ["sdmx_class"]
    assert [drop.urn for drop in dropped(msg, "2.1")] == [str(artefact_urn(association))]
    back = as_structure(parse(serialize(msg, version="2.1")))
    assert (list(back.hierarchies), list(back.hierarchy_associations)) == ([hierarchy], [])
    assert dropped(msg, "3.0") == ()
