"""What mutation found the 2.1 writer's tests could not see (#458).

The triage of the rest — equivalent, and why — is in
`docs/design/mutation-evidence.md`.

Most of these share one cause: a round-trip test reads back what the reader
keeps, and the 2.1 reader keeps less than the writer writes. A component's
``position``, a concept role's scheme, the synthetic wrappers' own fields, the
message header and the section order all reach the document and never the
model, so a writer getting them wrong passed every round trip. What only the
document holds is asserted on the document.
"""

import functools

import attrs
import pytest
from lxml import etree

import sdmxlib as sl
from sdmxlib.formats.gaps import without_dropped
from sdmxlib.formats.sdmx_ml21._synthetic import SYNTHETIC_REPORT_LAYER_TYPE, SYNTHETIC_STRUCTURE_SET_TYPE
from sdmxlib.formats.sdmx_ml21.writer import write, write_kept
from sdmxlib.model.codelist import Code, Codelist, CodelistExtension, GeoFeatureSetCode, GeographicCodelist
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import Ref
from tests.support import maximal, shapes
from tests.support.narrow import present
from tests.support.roundtrip import as_structure, structure_schema
from tests.support.xsd import assert_valid_sdmx_ml

pytestmark = pytest.mark.unit

_MES = "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message"
_STR = "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure"
_COM = "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common"


@functools.cache
def _maximal() -> StructureMessage:
    return maximal.message()


def _root(msg: StructureMessage) -> etree._Element:
    return etree.fromstring(write(msg))


def _sections(msg: StructureMessage) -> list[str]:
    structures = present(_root(msg).find(f"{{{_MES}}}Structures"))
    return [etree.QName(el).localname for el in structures]


# ── the document around the structures ───────────────────────────────────


def test_the_document_declares_itself_utf_8() -> None:
    """lxml leaves the declaration out of a UTF-8 document unless asked."""
    assert write(shapes.message_21()).startswith(b"<?xml version='1.0' encoding='UTF-8'?>\n<mes:Structure ")


def test_a_written_document_is_one_line_unless_pretty_printed() -> None:
    """The declaration ends a line of its own either way, so ``\\n in`` cannot tell."""
    plain, pretty = write(shapes.message_21()), write(shapes.message_21(), pretty_print=True)
    assert plain.count(b"\n") == 1
    # `serialize` calls this one, always saying which; its own default agrees.
    assert write_kept(without_dropped(shapes.message_21(), "2.1")) == plain
    assert pretty.count(b"\n") > 20, "a floor: the fixture grows"
    assert etree.tostring(etree.fromstring(plain)) == etree.tostring(etree.fromstring(pretty, _no_blanks()))


def _no_blanks() -> etree.XMLParser:
    return etree.XMLParser(remove_blank_text=True)


def test_the_header_is_the_one_sdmxlib_writes() -> None:
    header = present(_root(shapes.message_21()).find(f"{{{_MES}}}Header"))
    assert [(etree.QName(el).localname, el.text, dict(el.attrib)) for el in header] == [
        ("ID", "SDMX_MSG", {}),
        ("Test", "false", {}),
        ("Prepared", "2000-01-01T00:00:00", {}),
        ("Sender", None, {"id": "sdmxlib"}),
    ]


def test_structure_sets_follow_data_structures_with_no_constraint_after() -> None:
    """2.1's `StructuresType` sequence, with provision agreements after the sets."""
    msg = attrs.evolve(_maximal(), constraints=sl.ArtefactList([]), attachment_constraints=sl.ArtefactList([]))
    assert _sections(msg)[-3:] == ["DataStructures", "StructureSets", "ProvisionAgreements"]


def test_structure_sets_precede_constraints_with_no_data_structure() -> None:
    """With no `DataStructures` to follow, the sets go in ahead of `Constraints`."""
    msg = attrs.evolve(_maximal(), data_structures=sl.ArtefactList([]))
    assert _sections(msg)[-3:] == ["StructureSets", "Constraints", "ProvisionAgreements"]


# ── a data structure ──────────────────────────────────────────────────────


def test_the_dimension_list_is_numbered_in_order() -> None:
    """Dimensions, then the measure dimension, then time — one position each."""
    dim_list = present(_root(_maximal()).find(f".//{{{_STR}}}DimensionList"))
    positions = [(etree.QName(el).localname, el.get("position")) for el in dim_list if el.get("position")]
    assert positions == [("Dimension", "1"), ("MeasureDimension", "2"), ("TimeDimension", "3")]


def test_a_concept_role_names_the_cross_domain_scheme() -> None:
    """The reader keeps a role's id, so only the document shows where it points."""
    refs = _root(_maximal()).findall(f".//{{{_STR}}}ConceptRole/Ref")
    assert len(refs) == 3, "the case lost its subject"
    assert {tuple(sorted((k, v) for k, v in ref.attrib.items() if k != "id")) for ref in refs} == {
        (
            ("agencyID", "SDMX"),
            ("class", "Concept"),
            ("maintainableParentID", "CROSS_DOMAIN_CONCEPTS"),
            ("maintainableParentVersion", "1.0"),
            ("package", "conceptscheme"),
        )
    }


def test_a_measureless_data_structure_is_refused_saying_why() -> None:
    (dsd,) = shapes.message_21().data_structures
    msg = StructureMessage(data_structures=[attrs.evolve(dsd, measures=[])])
    with pytest.raises(ValueError, match="no measures") as caught:
        write(msg)
    assert str(caught.value) == (
        f"DataStructure AG:{dsd.id}(1.0) has no measures, which SDMX-ML 2.1 cannot represent — "
        "<MeasureList> is required and must contain a <PrimaryMeasure>. Add a measure, or serialise to 3.0."
    )


# ── constraints ───────────────────────────────────────────────────────────


def test_an_excluding_cube_region_is_written_as_xs_boolean_false() -> None:
    """``FALSE`` is not in xs:boolean's lexical space; ``false`` is."""
    full = shapes.message_21()
    constraint, *others = full.constraints
    (region,) = constraint.regions
    excluding = attrs.evolve(constraint, regions=[attrs.evolve(region, include=False)])
    written = write(attrs.evolve(full, constraints=sl.ArtefactList([excluding, *others])))
    assert_valid_sdmx_ml(written, structure_schema("2.1"))
    (el,) = etree.fromstring(written).iter(f"{{{_STR}}}CubeRegion")
    assert el.get("include") == "false"


# ── codelists ─────────────────────────────────────────────────────────────


def test_a_geographic_codelist_inherits_plain_codes_in_2_1() -> None:
    """A `GeographicCodelist` holds only feature-set codes, so 2.1 converts it first."""
    plain = Codelist(id="CL", maintainer="AG", name={"en": "cl"}, codes=[Code(id="A", name={"en": "a"})])
    geo = GeographicCodelist(
        id="CL_GEO",
        maintainer="AG",
        name={"en": "geo"},
        codes=[GeoFeatureSetCode(id="EU", name={"en": "eu"}, value="POLYGON((0 0, 1 0, 1 1, 0 0))")],
        extensions=[CodelistExtension(codelist=Ref.to(plain))],
    )
    back = as_structure(sl.parse(write(StructureMessage(codelists=[plain, geo]))))
    assert back.codelists["CL_GEO"].codes.ids() == ["EU", "A"]


# ── hierarchies ───────────────────────────────────────────────────────────


def test_a_hierarchy_no_container_claims_comes_back_as_itself() -> None:
    """Written in an HCL of its own, carrying its description and annotations."""
    msg = attrs.evolve(_maximal(), hierarchical_codelists=sl.ArtefactList([]))
    (hierarchy,) = msg.hierarchies
    assert hierarchy.description, "the case lost its subject"
    assert hierarchy.annotations, "the case lost its subject"
    written = write(msg)
    (hcl,) = etree.fromstring(written).iter(f"{{{_STR}}}HierarchicalCodelist")
    assert hcl.get("id") == hierarchy.id
    # The reader rebuilds the hierarchy from the inner element, so what the
    # wrapper carries shows only here: the hierarchy's own, as a copy.
    inner = present(hcl.find(f"{{{_STR}}}Hierarchy"))
    for tag in (f"{{{_COM}}}Annotations", f"{{{_COM}}}Name", f"{{{_COM}}}Description"):
        copied = [etree.tostring(el) for el in hcl.iterchildren(tag)]
        assert copied, tag
        assert copied == [etree.tostring(el) for el in inner.iterchildren(tag)]
    (back,) = as_structure(sl.parse(written)).hierarchies
    # The annotations less what 2.1 cannot carry of one — declared in `gaps.py`.
    assert (back.description, back.annotations.types) == (hierarchy.description, hierarchy.annotations.types)


# ── the synthetic report layer ────────────────────────────────────────────


def test_an_msd_without_a_report_layer_is_written_with_a_marked_one() -> None:
    """2.1 requires the layer; the one invented is marked, so the reader drops it."""
    full = _maximal()
    msd = attrs.evolve(full.metadata_structures[0], targets=(), report_structures=())
    written = write(StructureMessage(concept_schemes=full.concept_schemes, metadata_structures=[msd]))
    assert_valid_sdmx_ml(written, structure_schema("2.1"))
    components = present(etree.fromstring(written).find(f".//{{{_STR}}}MetadataStructureComponents"))
    target, report = components
    for layer in (target, report):
        (annotations,) = layer.iterchildren(f"{{{_COM}}}Annotations")
        assert [el.text for el in annotations.iter(f"{{{_COM}}}AnnotationType")] == [SYNTHETIC_REPORT_LAYER_TYPE]
    (obj,) = target.iter(f"{{{_STR}}}IdentifiableObjectTarget")
    assert obj.get("objectType")
    # The type 2.1 fixes for the kind, left out as a default is (#503).
    assert [el.get("textType") for el in obj.iter(f"{{{_STR}}}TextFormat")] == [None]
    (back,) = as_structure(sl.parse(written)).metadata_structures
    assert (back.targets, back.report_structures) == ((), ())
    assert back.attributes.ids() == msd.attributes.ids()


# ── the synthetic structure-set wrapper ───────────────────────────────────


def test_a_map_outside_any_set_takes_its_version_from_the_wrapper() -> None:
    """2.1 maps are versioned by their set; a map at 2.0 needs a wrapper at 2.0."""
    full = shapes.message_21()
    maps = [attrs.evolve(m, version="2.0") for m in full.category_scheme_maps]
    assert maps, "the case lost its subject"
    msg = attrs.evolve(full, category_scheme_maps=sl.ArtefactList(maps))
    written = write(msg)
    wrappers = list(etree.fromstring(written).iter(f"{{{_STR}}}StructureSet"))
    assert [(el.get("id"), el.get("version")) for el in wrappers] == [("SS_SYNTHETIC", "2.0")]
    (name,) = wrappers[0].iterchildren(f"{{{_COM}}}Name")
    lang = "{http://www.w3.org/XML/1998/namespace}lang"
    assert (name.get(lang), name.text) == ("en", "Synthetic structure-set wrapper")
    back = as_structure(sl.parse(written))
    assert {m.version for m in back.category_scheme_maps} == {"2.0"}


def test_an_annotated_marker_is_what_the_reader_drops() -> None:
    """Sanity for the case above: the wrapper carries the marker it is dropped by."""
    wrapper = next(etree.fromstring(write(shapes.message_21())).iter(f"{{{_STR}}}StructureSet"))
    (annotations,) = wrapper.iterchildren(f"{{{_COM}}}Annotations")
    assert [el.text for el in annotations.iter(f"{{{_COM}}}AnnotationType")] == [SYNTHETIC_STRUCTURE_SET_TYPE]


# ── a metadata structure ──────────────────────────────────────────────────


def test_a_metadata_attributes_occurrence_bounds_of_two_are_written() -> None:
    """``1`` is the XSD default and left out; anything else is stated."""
    full = _maximal()
    msd = full.metadata_structures[0]
    first, *rest = msd.attributes
    bounded = attrs.evolve(msd, attributes=[attrs.evolve(first, min_occurs=2, max_occurs=2), *rest])
    written = write(StructureMessage(concept_schemes=full.concept_schemes, metadata_structures=[bounded]))
    (back,) = as_structure(sl.parse(written)).metadata_structures
    assert (back.attributes[first.id].min_occurs, back.attributes[first.id].max_occurs) == (2, 2)
