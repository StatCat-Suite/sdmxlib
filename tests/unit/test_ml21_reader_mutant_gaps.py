"""What mutation found the 2.1 reader's tests could not see (#458).

The triage of the rest — equivalent, and why — is in
`docs/design/mutation-evidence.md`.

Most of these share one cause: `tests/support/maximal.py` is the fixture
every reader is measured against, and wherever it holds a field's *default*,
a reader that ignored the document and used the default passed. Every
maintainable there is version ``1.0``, which is also what 2.1 says an absent
`@version` means — so reading `@version` and not reading it were the same.
"""

import functools

import pytest
from lxml import etree

import sdmxlib as sl
from sdmxlib.formats.gaps import without_dropped
from sdmxlib.model.errors import SdmxParseError
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import Ref
from sdmxlib.model.representation import Facet
from tests.support import maximal, shapes
from tests.support.narrow import present
from tests.support.roundtrip import as_structure

pytestmark = pytest.mark.unit

_MES = "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message"


@functools.cache
def _written() -> bytes:
    return sl.serialize(without_dropped(maximal.message(), "2.1"), version="2.1")


def _maintainables(root: etree._Element) -> list[etree._Element]:
    """Each artefact element: `<mes:Structures>` / a collection / the artefact."""
    structures = root.find(f"{{{_MES}}}Structures")
    assert structures is not None
    return [el for collection in structures for el in collection]


def _versions(msg: StructureMessage) -> dict[str, str]:
    return {f"{type(a).__name__} {a.id}": a.version for a in msg.all_artefacts()}


#: Fixed at ``1.0`` in every format, and refused at anything else.
#: An organisation unit scheme is not here: 2.1 lets it carry any version (#469).
_FIXED = {"AgencyScheme", "DataProviderScheme", "DataConsumerScheme", "MetadataProviderScheme"}


def _at_2_0(*, fixed_too: bool) -> bytes:
    text = _written().replace(b'version="1.0"', b'version="2.0"').replace(b"(1.0)", b"(2.0)")
    root = etree.fromstring(text.replace(b'maintainableParentVersion="1.0"', b'maintainableParentVersion="2.0"'))
    if not fixed_too:
        for el in _maintainables(root):
            if etree.QName(el).localname in _FIXED:
                el.set("version", "1.0")
    return etree.tostring(root)


def test_every_maintainable_reads_its_own_version() -> None:
    """At ``1.0`` a reader returning the default and one reading the attribute agree."""
    versions = _versions(as_structure(sl.parse(_at_2_0(fixed_too=False))))
    assert len(versions) >= 20, "the case lost its subject"  # a floor: maximal grows
    assert {v for name, v in versions.items() if name.split()[0] not in _FIXED} == {"2.0"}, versions


def test_an_organisation_scheme_at_another_version_is_refused() -> None:
    """Read, then refused — a reader taking the default would accept the document.

    Each kind on its own: the strict error stops at the first, and the other
    two schemes are read by their own lines.
    """
    lenient = as_structure(sl.parse(_at_2_0(fixed_too=True), strict=False))
    # Refused as it is built (#492), so named as the element is: 2.1 carries a
    # metadata provider scheme as a marked `DataProviderScheme`, and a refusal
    # names what the element says (`Refusal.urn`) — the split comes after.
    as_written = sorted("DataProviderScheme" if c == "MetadataProviderScheme" else c for c in _FIXED)
    assert sorted(r.artefact_class for r in lenient.refused) == as_written
    assert all("version is fixed to '1.0'" in r.reason for r in lenient.refused)


def test_every_maintainable_without_a_version_is_1_0() -> None:
    """2.1's `MaintainableType` defaults `@version` to ``1.0``."""
    root = etree.fromstring(_written())
    stripped = 0
    for el in _maintainables(root):
        if "version" in el.attrib:
            del el.attrib["version"]
            stripped += 1
    assert stripped >= 20, "the case lost its subject"
    versions = _versions(as_structure(sl.parse(etree.tostring(root))))
    assert set(versions.values()) == {"1.0"}, versions


# ── one edit to the maximal document, one case ────────────────────────────

_STR = "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure"
_NS = {"str": _STR}


def _doc() -> etree._Element:
    return etree.fromstring(_written())


def _read(root: etree._Element) -> StructureMessage:
    return as_structure(sl.parse(etree.tostring(root)))


def _one(root: etree._Element, path: str) -> etree._Element:
    found = root.find(f".//{path}", namespaces=_NS)
    assert found is not None, f"the maximal document has no {path}"
    return found


@pytest.mark.parametrize(
    "attribute", ["minLength", "maxLength", "startValue", "endValue", "minValue", "maxValue", "interval"]
)
def test_a_level_s_malformed_coding_format_names_the_attribute(attribute: str) -> None:
    root = _doc()
    _one(root, "str:Level/str:CodingFormat").set(attribute, "x")
    with pytest.raises(SdmxParseError, match=f"^@{attribute} must be an integer, got 'x'$"):
        _read(root)


def test_a_time_dimension_without_a_text_format_reads_as_the_default() -> None:
    """The schema requires the format; its absence reads as the default type (#507)."""
    root = _doc()
    time_dimension = _one(root, "str:TimeDimension")
    time_dimension.remove(_one(time_dimension, "str:LocalRepresentation"))
    dsd = _read(root).data_structures[0]
    assert present(dsd.time_dimension).representation == Facet(type="ObservationalTimePeriod")


def test_a_dsd_without_a_time_dimension_has_none() -> None:
    root = _doc()
    time_dimension = _one(root, "str:TimeDimension")
    parent = time_dimension.getparent()
    assert parent is not None
    parent.remove(time_dimension)
    assert _read(root).data_structures[0].time_dimension is None


def test_every_concept_role_is_read_not_only_the_first() -> None:
    root = _doc()
    role = _one(root, "str:ConceptRole")
    ref = _one(role, "Ref")
    second = etree.fromstring(etree.tostring(role))
    second_ref = second.find("Ref")
    assert second_ref is not None
    second_ref.set("id", f"{ref.get('id')}_2")
    role.addnext(second)
    component_id = present(role.getparent()).get("id")
    dsd = _read(root).data_structures[0]
    component = next(c for c in (*dsd.dimensions, *dsd.attributes, *dsd.measures) if c.id == component_id)
    assert component.concept_role[-2:] == (ref.get("id"), f"{ref.get('id')}_2")


def test_a_comment_in_a_metadata_target_is_skipped_not_the_end() -> None:
    root = _doc()
    for target in root.iterfind(".//str:MetadataTarget", namespaces=_NS):
        target.insert(0, etree.Comment(" a producer's note "))
    assert _read(root).metadata_structures == _read(_doc()).metadata_structures


def _node_code(root: etree._Element) -> etree._Element:
    return _one(root, "str:HierarchicalCode/str:Code/Ref")


def test_a_node_s_code_takes_the_reference_s_version_when_its_parent_s_is_absent() -> None:
    """`@version` is tolerated where producers put it; the XSD's own is `maintainableParentVersion` (#476)."""
    root = _doc()
    ref = _node_code(root)
    del ref.attrib["maintainableParentVersion"]
    ref.set("version", "3.0")
    node = _read(root).hierarchies[0].tree[0]
    assert present(node.code).urn.version == "3.0"


def test_a_node_s_code_with_no_version_at_all_is_1_0() -> None:
    root = _doc()
    ref = _node_code(root)
    del ref.attrib["maintainableParentVersion"]
    ref.attrib.pop("version", None)
    assert present(_read(root).hierarchies[0].tree[0].code).urn.version == "1.0"


def _refusal(root: etree._Element) -> str:
    (refusal,) = as_structure(sl.parse(etree.tostring(root), strict=False)).refused
    return f"{refusal.artefact_class}: {refusal.reason}"


def test_a_node_naming_no_code_is_refused_not_a_crash() -> None:
    """Every format requires the code; read as None, validation says so."""
    root = _doc()
    node = _one(root, "str:HierarchicalCode")
    node.remove(_one(node, "str:Code"))
    assert "a hierarchical code must reference the code it places" in _refusal(root)


def test_a_legacy_code_id_without_an_alias_names_no_code() -> None:
    """Earlier sdmxlib wrote `<CodeID>` alone: not valid, and read as naming nothing."""
    root = _doc()
    code = _one(root, "str:HierarchicalCode/str:Code")
    code.tag = f"{{{_STR}}}CodeID"
    ref = _one(code, "Ref")
    for name in ("agencyID", "maintainableParentID"):
        del ref.attrib[name]
    assert "a hierarchical code must reference the code it places" in _refusal(root)


def test_a_level_named_as_text_is_resolved_too() -> None:
    """2.1 wraps the level in a `<Ref>`; 3.0 writes it as text, and the reader takes either."""
    root = _doc()
    level = _one(root, "str:HierarchicalCode/str:Level")
    ref = _one(level, "Ref")
    level.remove(ref)
    level.text = ref.get("id")
    node = _read(root).hierarchies[0].tree[0]
    assert present(node.level).id == ref.get("id")


@pytest.mark.parametrize("spelling", ["Ref", "text"])
def test_a_node_naming_a_level_its_hierarchy_lacks_is_read_with_no_level_and_the_loss_recorded(spelling: str) -> None:
    """A reference to nothing: the node keeps its place, and the read says what it dropped (#501).

    #458 refused the hierarchy; before that, the reference vanished without a
    word. The reader takes the id wrapped in a `<Ref>` (2.1) or as text
    (3.x's spelling), so the loss is counted for both.
    """
    root = _doc()
    level = _one(root, "str:HierarchicalCode/str:Level")
    ref = _one(level, "Ref")
    if spelling == "text":
        level.remove(ref)
        level.text = "NOPE"
    else:
        ref.set("id", "NOPE")
    node_id = present(level.getparent()).get("id")
    back = as_structure(sl.parse(etree.tostring(root), strict=False))
    assert back.refused == ()
    assert [node.level for h in back.hierarchies for node in h.codes if node.id == node_id] == [None]
    (gap,) = back.lost
    assert (gap.construct, gap.count) == ("str:HierarchicalCode/str:Level", 1)


def test_a_concept_in_the_message_is_what_a_component_resolves_to() -> None:
    """Index keys every concept by URN; built wrong, every component's concept stayed unresolved."""
    dsd = _read(_doc()).data_structures[0]
    assert all(present(d.concept).resolved for d in dsd.dimensions)


def test_a_component_s_local_codelist_in_the_message_is_resolved() -> None:
    msg = _read(_doc())
    enumerated = [
        c.representation
        for c in (*msg.data_structures[0].attributes, *msg.data_structures[0].measures)
        if isinstance(c.representation, Ref)
    ]
    assert enumerated, "the case needs an enumerated attribute or measure"
    in_message = {str(cl.urn) for cl in msg.codelists}
    assert all(r.resolved for r in enumerated if str(r.urn) in in_message)
    assert any(str(r.urn) in in_message for r in enumerated), "none names a codelist in the message"


def test_a_refused_attachment_constraint_is_named_as_one() -> None:
    """`maximal` holds none (2.1-only); `shapes.message_21` does."""
    root = etree.fromstring(sl.serialize(shapes.message_21(), version="2.1"))
    del _one(root, "str:Constraints/str:AttachmentConstraint").attrib["agencyID"]
    lenient = as_structure(sl.parse(etree.tostring(root), strict=False))
    assert [r.artefact_class for r in lenient.refused] == ["AttachmentConstraint"]


def test_a_value_map_without_a_value_type_has_none() -> None:
    root = _doc()
    value_type = _one(root, "str:ToValueType")
    present(value_type.getparent()).remove(value_type)
    maps = [m for sm in _read(root).structure_maps for m in sm.maps if m.to_text_format is not None]
    assert maps, "the case needs a component map with a text format"
    assert all(m.to_value_type is None for m in maps)


def test_a_metadata_attribute_without_max_occurs_occurs_once() -> None:
    """`maxOccurs` defaults to 1 in the schema."""
    root = _doc()
    stated = _one(root, "str:MetadataAttribute[@maxOccurs]")
    del stated.attrib["maxOccurs"]
    msd = _read(root).metadata_structures[0]
    assert {a.max_occurs for a in msd.attributes} == {1}


def test_a_stub_without_components_has_none() -> None:
    """2.1's `StructureType` makes the components block optional, as a stub omits it."""
    root = _doc()
    for path in ("str:DataStructureComponents", "str:MetadataStructureComponents"):
        block = _one(root, path)
        present(block.getparent()).remove(block)
    msg = _read(root)
    dsd, msd = msg.data_structures[0], msg.metadata_structures[0]
    assert (dsd.time_dimension, dsd.measure_dimension, dsd.groups) == (None, None, None)
    assert (list(dsd.dimensions), list(dsd.attributes), list(dsd.measures)) == ([], [], [])
    assert list(msd.targets) == []


def test_a_metadata_attribute_s_codelist_in_the_message_is_resolved() -> None:
    msd = _read(_doc()).metadata_structures[0]
    enumerated = [a.representation for a in msd.attributes if isinstance(a.representation, Ref)]
    assert enumerated, "the case needs an enumerated metadata attribute"
    assert all(r.resolved for r in enumerated)


def _shapes_21() -> etree._Element:
    return etree.fromstring(sl.serialize(shapes.message_21(), version="2.1"))


def test_an_attachment_constraint_reads_its_own_version_and_description() -> None:
    """Outside `maximal` (2.1-only), so the two tests above never reach it."""
    (original,) = shapes.message_21().attachment_constraints
    assert original.description, "the case needs a description"
    assert original.description != original.name, "and one a reader of the name would not return"
    root = _shapes_21()
    element = _one(root, "str:Constraints/str:AttachmentConstraint")
    element.set("version", "2.0")
    (constraint,) = _read(root).attachment_constraints
    assert (constraint.version, constraint.description) == ("2.0", original.description)
    del element.attrib["version"]
    assert _read(root).attachment_constraints[0].version == "1.0"


def test_a_category_scheme_map_reads_its_description() -> None:
    """`maximal` holds no category scheme map; `shapes.message` does, and its own goes through 2.1."""
    (original,) = shapes.message().category_scheme_maps
    assert original.description, "the case needs a description"
    assert original.description != original.name, "and one a reader of the name would not return"
    written = sl.serialize(StructureMessage(category_scheme_maps=[original]), version="2.1")
    (back,) = as_structure(sl.parse(written)).category_scheme_maps
    assert back.description == original.description
