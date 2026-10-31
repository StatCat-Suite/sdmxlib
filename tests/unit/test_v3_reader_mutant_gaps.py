"""What mutation found the SDMX-ML 3.x reader's tests could not see (#458).

The triage of the rest — equivalent, and why — is in
`docs/design/evidence/mutation-runs.md`.

The reader took 2.1's shapes inside a 3.x document: a ``<Ref>`` child where
3.x has text, a draft's ``@dimensionRef``, the unified ``OrganisationSchemes``
container. The closed-world check (#435) refuses each of them first, so the
branches reading them never ran, except one: a refused container was still
read, and under ``strict=False`` its schemes came back refused *and* present.
The branches are gone; the refusals are pinned here.
"""

from __future__ import annotations

import functools
import re
from typing import TYPE_CHECKING

import attrs
import pytest
from lxml import etree

import sdmxlib as sl
from sdmxlib.formats.gaps import without_dropped
from sdmxlib.formats.sdmx_ml30.reader import read as read_30
from sdmxlib.model.errors import SdmxParseError
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import Ref
from sdmxlib.model.representation import Facet
from sdmxlib.model.validation import SdmxConformanceError
from tests.support import maximal, shapes
from tests.support.narrow import present
from tests.support.roundtrip import as_structure

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

pytestmark = pytest.mark.unit

_MES = "http://www.sdmx.org/resources/sdmxml/schemas/v3_0/message"
_STR = "http://www.sdmx.org/resources/sdmxml/schemas/v3_0/structure"
_NS = {"str": _STR}


@functools.cache
def _written() -> str:
    """`maximal` as SDMX-ML 3.0, less the measure dimension 3.0 refuses."""
    msg = maximal.message()
    dsds = [attrs.evolve(d, measure_dimension=None) for d in msg.data_structures]
    msg = attrs.evolve(msg, data_structures=sl.ArtefactList(dsds))
    return sl.serialize(without_dropped(msg, "3.0"), version="3.0").decode()


def _parse(text: str, *, strict: bool = True) -> StructureMessage:
    return as_structure(sl.parse(text.encode(), strict=strict))


def _swap(pattern: str, make: Callable[[re.Match[str]], str]) -> str:
    text = _written()
    found = re.search(pattern, text)
    assert found is not None, f"the case lost its subject: {pattern}"
    return text.replace(found.group(0), make(found), 1)


# ── 2.1's shapes are refused ──────────────────────────────────────────────


def _wrapped(found: re.Match[str]) -> str:
    return f'{found.group(1)}<Ref id="{found.group(2)}"/>{found.group(3)}'


_LEGACY = {
    "an attribute's dimension": (
        r"(<str:AttributeRelationship>\s*<str:Dimension[^>]*>)([^<]+)(</str:Dimension>)",
        _wrapped,
        "<Ref>: not allowed in <str:Dimension>",
    ),
    "an attribute's group": (
        r"(<str:AttributeRelationship>\s*<str:Group>)([^<]+)(</str:Group>)",
        _wrapped,
        "<Ref>: not allowed in <str:Group>",
    ),
    "a group's dimension": (
        r"(<str:DimensionReference>)([^<]+)(</str:DimensionReference>)",
        _wrapped,
        "<Ref>: not allowed in <str:DimensionReference>",
    ),
    "a concept role": (
        r"(<str:ConceptRole>)([^<]+)(</str:ConceptRole>)",
        lambda found: f'{found.group(1)}<Ref id="ROLE"/>{found.group(3)}',
        "<Ref>: not allowed in <str:ConceptRole>",
    ),
    "a node's level": (
        r"(<str:HierarchicalCode\b(?:(?!</str:HierarchicalCode>).)*?<str:Level>)([^<]+)(</str:Level>)",
        _wrapped,
        "<Ref>: not allowed in <str:Level>",
    ),
    "a draft's dimensionRef": (
        r"<str:GroupDimension>\s*<str:DimensionReference>([^<]+)</str:DimensionReference>\s*</str:GroupDimension>",
        lambda found: f'<str:GroupDimension dimensionRef="{found.group(1)}"/>',
        "@dimensionRef is not allowed",
    ),
}


@pytest.mark.parametrize("case", sorted(_LEGACY))
def test_a_2_1_shape_in_a_3_x_document_is_refused(case: str) -> None:
    pattern, make, refusal = _LEGACY[case]
    with pytest.raises(SdmxConformanceError, match=re.escape(refusal)):
        _parse(_swap(pattern, make))


def test_a_refused_unified_scheme_container_is_not_also_read() -> None:
    """2.1's `OrganisationSchemes`: refused, and its schemes not read anyway."""
    text = _written().replace("str:AgencySchemes>", "str:OrganisationSchemes>")
    assert "<str:OrganisationSchemes>" in text, "the case lost its subject"
    msg = _parse(text, strict=False)
    assert [r.artefact_class for r in msg.refused] == ["str:OrganisationSchemes"]
    assert list(msg.agency_schemes) == []


# ── versions ──────────────────────────────────────────────────────────────


def _maintainables(root: etree._Element) -> list[etree._Element]:
    structures = present(root.find(f"{{{_MES}}}Structures"))
    return [el for collection in structures for el in collection]


#: Fixed at ``1.0``, and refused at anything else: 3.x prohibits the version on
#: every organisation scheme, the unit scheme included (#469).
_FIXED = {
    "AgencyScheme",
    "DataProviderScheme",
    "DataConsumerScheme",
    "MetadataProviderScheme",
    "OrganisationUnitScheme",
}


def test_every_maintainable_reads_its_own_version() -> None:
    """`maximal` versions everything ``1.0``, which is also what the reader assumes."""
    root = etree.fromstring(_written().encode())
    moved = 0
    for el in _maintainables(root):
        if etree.QName(el).localname not in _FIXED and el.get("version") == "1.0":
            el.set("version", "2.0")
            moved += 1
    assert moved >= 20, "the case lost its subject"  # a floor: maximal grows
    msg = _parse(etree.tostring(root).decode(), strict=False)
    versions = {f"{type(a).__name__} {a.id}": a.version for a in msg.all_artefacts()}
    assert {v for name, v in versions.items() if name.split()[0] not in _FIXED} == {"2.0"}, versions


def test_every_maintainable_without_a_version_is_assumed_1_0() -> None:
    """3.x calls it un-versioned; the model cannot hold that, so ``1.0`` (#474)."""
    root = etree.fromstring(_written().encode())
    for el in _maintainables(root):
        el.attrib.pop("version", None)
    msg = _parse(etree.tostring(root).decode())
    assert len(list(msg.all_artefacts())) >= 20, "the case lost its subject"
    assert {a.version for a in msg.all_artefacts()} == {"1.0"}


# ── what a refusal says ───────────────────────────────────────────────────


def test_a_dimension_out_of_position_is_refused_naming_it() -> None:
    """A position must grow with document order; the refusal says which two disagree."""
    text = _swap(
        r"(<str:DimensionList[^>]*>)", lambda found: f'{found.group(1)}<str:Dimension id="FIRST" position="5"/>'
    )
    with pytest.raises(SdmxConformanceError) as caught:
        _parse(text)
    assert str(caught.value).endswith(
        "<str:Dimension>: dimension 'DIM' comes after 'FIRST' in the dimension list but says position='1', "
        "not more than its 5; a position must be consistent with document order"
    )


@pytest.mark.parametrize(
    ("element", "attribute"),
    [
        ("ItemMap", "validFrom"),
        ("ItemMap", "validTo"),
        ("RepresentationMapping", "validFrom"),
        ("RepresentationMapping", "validTo"),
    ],
)
def test_a_malformed_mapping_date_names_its_attribute(element: str, attribute: str) -> None:
    text = _swap(rf'(<str:{element}\b[^>]*{attribute}=")[^"]*(")', lambda found: f"{found.group(1)}x{found.group(2)}")
    with pytest.raises(SdmxParseError, match=f"^@{attribute} must be an ISO 8601 date, got 'x'$"):
        _parse(text)


@pytest.mark.parametrize("attribute", ["startIndex", "endIndex"])
def test_a_malformed_source_value_index_names_its_attribute(attribute: str) -> None:
    text = _swap(r"<str:SourceValue>", lambda _: f'<str:SourceValue {attribute}="x">')
    with pytest.raises(SdmxParseError, match=f"^<SourceValue> @{attribute} must be an integer, got 'x'$"):
        _parse(text)


# ── what a lenient read records ───────────────────────────────────────────


def test_a_refused_category_scheme_map_is_recorded() -> None:
    """`maximal` holds no category scheme map; `shapes` does."""
    root = etree.fromstring(sl.serialize(shapes.message(), version="3.0"))
    (csm,) = root.iter(f"{{{_STR}}}CategorySchemeMap")
    del csm.attrib["agencyID"]
    msg = as_structure(sl.parse(etree.tostring(root), strict=False))
    assert [r.artefact_class for r in msg.refused] == ["CategorySchemeMap"]


def test_a_registry_passed_to_the_read_receives_every_artefact() -> None:
    received: list[object] = []

    class _Registry:
        def add_all(self, artefacts: Iterable[object]) -> None:
            received.extend(artefacts)

    msg = read_30(_written().encode(), registry=_Registry())
    assert received == list(msg.all_artefacts())
    assert received, "the case lost its subject"


# ── defaults and within-message resolution ────────────────────────────────


def test_a_metadata_attribute_without_max_occurs_is_bounded_at_one() -> None:
    msg = _parse(_written())
    (child,) = msg.metadata_structures[0].attributes["MA"].children
    assert child.max_occurs == 1


def test_a_usage_without_a_relationship_is_reported_per_data_set() -> None:
    """Every schema requires the relationship; FMR omits it (#421)."""
    text = _swap(
        r"(<str:MetadataAttributeUsage[^>]*>(?:(?!</str:MetadataAttributeUsage>).)*?)"
        r"<str:AttributeRelationship>(?:(?!</str:AttributeRelationship>).)*</str:AttributeRelationship>",
        lambda found: found.group(1),
    )
    usage = _parse(text).data_structures[0].metadata_attributes[0]
    assert (usage.attachment, usage.related_dimensions, usage.related_group) == (sl.AttachmentLevel.DATASET, None, None)


def test_a_component_s_codelist_in_the_message_is_resolved() -> None:
    msg = _parse(_written())
    in_message = {str(cl.urn) for cl in msg.codelists}
    dsd = msg.data_structures[0]
    msd = msg.metadata_structures[0]
    for component in (*dsd.dimensions, *dsd.measures, *msd.attributes):
        rep = component.representation
        if isinstance(rep, Ref) and str(rep.urn) in in_message:
            assert rep.resolved, component.id
    named = [
        c.id
        for c in (*dsd.dimensions, *dsd.measures, *msd.attributes)
        if isinstance(c.representation, Ref) and str(c.representation.urn) in in_message
    ]
    assert {"DIM", "MA"} <= set(named) or len(named) >= 3, named


def test_an_empty_source_value_is_the_empty_string() -> None:
    text = _swap(r"<str:SourceValue>[^<]*</str:SourceValue>", lambda _: "<str:SourceValue></str:SourceValue>")
    sources = [
        s.value
        for m in _parse(text).representation_maps
        for mapping in m.mappings
        for s in mapping.sources
        if hasattr(s, "value")
    ]
    assert "" in sources


# ── refusals the reader makes itself ──────────────────────────────────────


def test_a_node_naming_a_level_its_hierarchy_lacks_is_read_with_no_level_and_the_loss_recorded() -> None:
    """A reference to nothing: the node keeps its place, and the read says what it dropped (#501).

    #458 refused the hierarchy, which cost IMF all 99 of theirs for a bare
    number on each node. Before that, the reference vanished without a word.
    """
    text = _written()
    assert text.count("<str:Level>L2</str:Level>") == 1, "the case lost its subject"
    back = _parse(text.replace("<str:Level>L2</str:Level>", "<str:Level>NOPE</str:Level>"), strict=False)
    assert back.refused == ()
    leaf = next(node for h in back.hierarchies for node in h.codes if node.id == "LEAF")
    assert leaf.level is None
    (gap,) = back.lost
    assert (gap.construct, gap.count) == ("str:HierarchicalCode/str:Level", 1)


# ── what an absent block means ────────────────────────────────────────────


def test_a_time_dimension_without_a_representation_reads_as_the_default() -> None:
    """The schema requires the format; its absence reads as the default type (#507)."""
    text = _swap(
        r"(<str:TimeDimension\b(?:(?!</str:TimeDimension>).)*?)<str:LocalRepresentation>"
        r"(?:(?!</str:LocalRepresentation>).)*</str:LocalRepresentation>",
        lambda found: found.group(1),
    )
    time_dimension = present(_parse(text).data_structures[0].time_dimension)
    assert time_dimension.representation == Facet(type="ObservationalTimePeriod")


@pytest.mark.parametrize(
    ("element", "components"),
    [
        ("DataStructure", "DataStructureComponents"),
        ("MetadataStructure", "MetadataStructureComponents"),
    ],
)
def test_a_stub_without_its_components_has_none(element: str, components: str) -> None:
    """`minOccurs="0"`: an external reference need not carry its components."""
    text = _swap(rf"<str:{components}>(?:(?!</str:{components}>).)*</str:{components}>", lambda _: "")
    msg = _parse(text)
    if element == "DataStructure":
        (dsd,) = msg.data_structures
        assert (list(dsd.dimensions), dsd.time_dimension, list(dsd.attributes), list(dsd.measures)) == (
            [],
            None,
            [],
            [],
        )
        assert (dsd.groups, list(dsd.metadata_attributes)) == (None, [])
    else:
        assert list(msg.metadata_structures["MSD"].attributes) == []
