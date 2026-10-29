"""What mutation found the tests of the shared SDMX-ML helpers could not see (#458).

`formats/sdmx_ml/_common.py` holds what the 2.1 and 3.x readers and writers
share. The triage of the rest — equivalent, and why — is in
`docs/design/evidence/mutation-runs.md`.

Two causes account for most of these. A refusal was tested by its type and
not its message, so a message naming the wrong element, or nothing, passed.
And the fixtures state every default, so a reader ignoring an absent
attribute's default could not be told from one reading it.
"""

import functools
from collections.abc import Callable

import attrs
import pytest
from lxml import etree

import sdmxlib as sl
from sdmxlib.formats.gaps import without_dropped
from sdmxlib.formats.sdmx_ml._allowed_children import NAMESPACES
from sdmxlib.formats.sdmx_ml21 import writer as ml21_writer
from sdmxlib.formats.sdmx_ml30 import writer as ml30_writer
from sdmxlib.formats.sdmx_ml31 import writer as ml31_writer
from sdmxlib.model.errors import SdmxParseError
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.validation import ValidationError
from tests.support import maximal, shapes
from tests.support.narrow import present
from tests.support.roundtrip import as_structure
from tests.support.unchecked import unchecked

pytestmark = pytest.mark.unit

_VERSIONS = ("2.1", "3.0", "3.1")


@functools.cache
def _written(version: str) -> bytes:
    """`maximal` in `version`, less the measure dimension 3.x refuses."""
    msg = maximal.message()
    if version != "2.1":
        dsds = [attrs.evolve(d, measure_dimension=None) for d in msg.data_structures]
        msg = attrs.evolve(msg, data_structures=sl.ArtefactList(dsds))
    return sl.serialize(without_dropped(msg, version), version=version)


def _doc(version: str) -> etree._Element:
    return etree.fromstring(_written(version))


def _read(root: etree._Element) -> StructureMessage:
    return as_structure(sl.parse(etree.tostring(root)))


def _refusal(root: etree._Element) -> str:
    (refusal,) = as_structure(sl.parse(etree.tostring(root), strict=False)).refused
    return f"{refusal.urn}: {refusal.reason}"


def _one(root: etree._Element, path: str) -> etree._Element:
    found = root.find(f".//{path}", namespaces=NAMESPACES[_version_of(root)])
    assert found is not None, f"the maximal document has no {path}"
    return found


def _version_of(root: etree._Element) -> str:
    namespace = etree.QName(root).namespace or ""
    return next(v for v, ns in NAMESPACES.items() if ns["mes"] == namespace)


# ── a refusal names what it refuses ───────────────────────────────────────


@pytest.mark.parametrize("version", _VERSIONS)
def test_a_wrong_class_reference_names_the_element_holding_it(version: str) -> None:
    root = _doc(version)
    flow_structure = _one(root, "str:Dataflow/str:Structure")
    if version == "2.1":
        present(flow_structure.find("Ref")).set("class", "Codelist")
    else:
        flow_structure.text = present(flow_structure.text).replace("DataStructure=", "Codelist=")
    assert "<Structure>: schema requires a DataStructure reference here, but the URN names a Codelist" in _refusal(root)


@pytest.mark.parametrize("version", _VERSIONS)
def test_a_wrong_class_enumeration_names_the_element(version: str) -> None:
    """A concept's core representation, read without the index: the other path to the same check."""
    root = _doc(version)
    enumeration = _one(root, "str:Concept/str:CoreRepresentation/str:Enumeration")
    if version == "2.1":
        present(enumeration.find("Ref")).set("class", "ConceptScheme")
        present(enumeration.find("Ref")).set("package", "conceptscheme")
    else:
        enumeration.text = present(enumeration.text).replace("codelist.Codelist=", "conceptscheme.ConceptScheme=")
    assert "<Enumeration>: schema requires a Codelist reference here, but the URN names a ConceptScheme" in (
        _refusal(root)
    )


def test_a_2_1_core_representation_without_package_or_class_is_a_codelist() -> None:
    """Both are optional with a fixed value (#240), on this path as on the indexed one."""
    root = _doc("2.1")
    ref = present(_one(root, "str:Concept/str:CoreRepresentation/str:Enumeration").find("Ref"))
    expected = (ref.get("agencyID"), ref.get("id"), ref.get("version"))
    del ref.attrib["class"], ref.attrib["package"]
    urn = next(
        c.core_representation.urn
        for cs in _read(root).concept_schemes
        for c in cs.concepts
        if isinstance(c.core_representation, sl.Ref)
    )
    assert (urn.artefact_class, urn.package, urn.agency, urn.id, urn.version) == ("Codelist", "codelist", *expected)


def test_a_component_with_neither_id_nor_concept_is_refused_by_name() -> None:
    root = _doc("2.1")
    dimension = _one(root, "str:DimensionList/str:Dimension")
    del dimension.attrib["id"]
    dimension.remove(present(dimension.find("str:ConceptIdentity", namespaces=NAMESPACES["2.1"])))
    assert (
        "<Dimension>: a component without @id takes its concept's id, and this one has no ConceptIdentity"
        in _refusal(root)
    )


@pytest.mark.parametrize(("version", "attribute"), [("3.0", "role"), ("2.1", "type")])
def test_a_constraint_role_outside_the_enumeration_names_its_attribute(version: str, attribute: str) -> None:
    root = _doc(version)
    constraint = next(root.iter("{*}DataConstraint", "{*}ContentConstraint"))
    constraint.set(attribute, "Possible")
    assert (
        f"<{etree.QName(constraint).localname}>: {attribute}='Possible' on constraint {constraint.get('id')!r} "
        "is not a value the SDMX schema permits; expected Allowed or Actual."
    ) in _refusal(root)


@pytest.mark.parametrize(
    ("version", "path", "attribute", "message"),
    [
        ("3.0", "str:CubeRegion/str:KeyValue/str:Value", "cascadeValues", "<Value> @cascadeValues must be one of"),
        (
            "3.0",
            "str:CodelistExtension//str:MemberValue",
            "cascadeValues",
            "<MemberValue> @cascadeValues must be one of",
        ),
        ("2.1", "str:ReferencePeriod", "startTime", "<ReferencePeriod> @startTime must be an ISO 8601 date-time"),
        ("2.1", "str:ReferencePeriod", "endTime", "<ReferencePeriod> @endTime must be an ISO 8601 date-time"),
        ("3.0", "str:Codelist", "validFrom", "@validFrom must be an ISO 8601 date-time"),
        ("3.0", "str:TextFormat", "maxLength", "a TextFormat bound must be an integer"),
        ("3.0", "str:TextFormat", "minValue", "a TextFormat bound must be a number"),
        ("3.0", "str:CoreRepresentation", "minOccurs", "<CoreRepresentation> @minOccurs must be an integer"),
    ],
)
def test_a_malformed_value_names_where_it_was(version: str, path: str, attribute: str, message: str) -> None:
    root = _doc(version)
    _one(root, path).set(attribute, "x")
    with pytest.raises(SdmxParseError, match=r"^" + message.replace("@", "@").replace("(", r"\(")):
        _read(root)


def test_a_malformed_availability_count_names_the_attribute() -> None:
    root = etree.fromstring(ml31_writer.write(without_dropped(shapes.message_31(), "3.1")))
    next(root.iter("{*}AvailabilityConstraint")).set("seriesCount", "x")
    with pytest.raises(SdmxParseError, match=r"^@seriesCount must be an integer, got 'x'$"):
        _read(root)


# ── 3.1's availability constraint ─────────────────────────────────────────


def _availability_doc() -> etree._Element:
    return etree.fromstring(ml31_writer.write(without_dropped(shapes.message_31(), "3.1")))


def test_an_availability_constraint_round_trips_whole() -> None:
    """Its annotations among the rest: `maximal` holds none, and no census reads one."""
    msg = without_dropped(shapes.message_31(), "3.1")
    assert msg.availability_constraints[0].annotations, "the case lost its subject"
    back = as_structure(sl.parse(ml31_writer.write(msg)))
    assert back.availability_constraints == msg.availability_constraints


def test_an_availability_constraint_without_a_region_is_refused_by_position() -> None:
    root = _availability_doc()
    constraint = next(root.iter("{*}AvailabilityConstraint"))
    constraint.remove(next(constraint.iter("{*}CubeRegion")))
    assert _refusal(root) == (
        "AvailabilityConstraint[1]: <AvailabilityConstraint>: AvailabilityConstraintType requires a <CubeRegion>"
    )


# ── what an absent attribute means ───────────────────────────────────────


@pytest.mark.parametrize("version", _VERSIONS)
def test_a_cube_region_without_include_includes(version: str) -> None:
    root = _doc(version)
    for region in root.iter("{*}CubeRegion"):
        region.set("include", "false")
    excluded = _read(root)
    for region in root.iter("{*}CubeRegion"):
        del region.attrib["include"]
    regions = [r for c in _read(root).constraints for r in c.regions]
    assert regions, "the case lost its subject"
    assert not any(r.include for c in excluded.constraints for r in c.regions)
    assert all(r.include for r in regions)


@pytest.mark.parametrize("version", _VERSIONS)
def test_a_time_bound_without_is_inclusive_is_inclusive(version: str) -> None:
    root = _doc(version)
    tags = ("StartPeriod", "EndPeriod", "AfterPeriod", "BeforePeriod")
    bounds = [bound for tag in tags for bound in root.iter(f"{{*}}{tag}")]
    for bound in bounds:
        bound.set("isInclusive", "false")
    for bound in bounds:
        del bound.attrib["isInclusive"]
    ranges = [
        k.time_range for c in _read(root).constraints for r in c.regions for k in r.keys if k.time_range is not None
    ]
    assert ranges, "the case lost its subject"
    assert all(b.inclusive for t in ranges for b in (t.start, t.end) if b is not None)


def test_a_3_x_value_list_without_a_version_is_1_0() -> None:
    root = _doc("3.0")
    value_list = _one(root, "str:ValueList")
    value_list.set("version", "2.0")
    assert _read(root).value_lists[0].version == "2.0"
    del value_list.attrib["version"]
    assert _read(root).value_lists[0].version == "1.0"


def test_a_codelist_extension_without_a_selection_has_none() -> None:
    root = _doc("3.0")
    extension = _one(root, "str:CodelistExtension")
    for selection in extension.findall("str:*", namespaces=NAMESPACES["3.0"]):
        if etree.QName(selection).localname != "Codelist":
            extension.remove(selection)
    extensions = [e for cl in _read(root).codelists for e in cl.extensions]
    assert extensions[0].selection is None


@pytest.mark.parametrize("version", ["2.1", "3.0"])
def test_a_release_calendar_s_empty_elements_read_empty(version: str) -> None:
    root = _doc(version)
    for el in _one(root, "str:ReleaseCalendar"):
        el.text = None
    (calendar,) = (c.release_calendar for c in _read(root).constraints if c.release_calendar is not None)
    assert (calendar.periodicity, calendar.offset, calendar.tolerance) == ("", "", "")


@pytest.mark.parametrize("version", _VERSIONS)
def test_an_artefact_s_maintainer_is_the_agency_its_scheme_describes(version: str) -> None:
    root = _doc(version)
    (scheme,) = _read(root).agency_schemes
    agency = scheme.agencies[0]
    assert agency.name, "the case lost its subject: a bare Agency(id) differs from it only by its name"
    _one(root, "str:Dataflow").set("agencyID", agency.id)
    assert _read(root).dataflows[0].maintainer == agency


# ── the writers ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("version", ["2.1", "3.0"])
def test_a_queryable_data_source_s_false_flags_are_written_false(version: str) -> None:
    source_msg = shapes.message_21() if version == "2.1" else shapes.message()
    (queryable,) = (c for c in source_msg.constraints if c.id == "DC_QUERYABLE")
    (source,) = queryable.attachment.queryable_data_sources
    off = attrs.evolve(source, is_rest_datasource=False, is_web_service_datasource=False)
    constraint = attrs.evolve(queryable, attachment=attrs.evolve(queryable.attachment, queryable_data_sources=(off,)))
    msg = StructureMessage(dataflows=source_msg.dataflows, constraints=[constraint])
    root = etree.fromstring(sl.serialize(msg, version=version))
    el = next(root.iter("{*}QueryableDataSource"))
    assert (el.get("isRESTDatasource"), el.get("isWebServiceDatasource")) == ("false", "false")


def test_a_final_organisation_scheme_writes_no_is_final_in_2_1() -> None:
    """2.1 fixes it `false` there; the writer, called directly, does not check the version first."""
    # Unchecked: refused as it is built (#492); the writer, called directly, is the subject.
    with unchecked():
        scheme = attrs.evolve(maximal.message().agency_schemes[0], version="1.0.0")
    assert scheme.is_final, "the case lost its subject"
    root = etree.fromstring(ml21_writer.write(StructureMessage(agency_schemes=[scheme])))
    assert "isFinal" not in next(root.iter("{*}AgencyScheme")).attrib


@pytest.mark.parametrize(
    ("write", "version"), [(ml21_writer.write, "1.x"), (ml30_writer.write, "1.03")], ids=["2.1", "3.0"]
)
def test_an_unwritable_version_names_its_artefact(write: Callable[[StructureMessage], bytes], version: str) -> None:
    """2.1 rewrites a 3.0 version it can, so only one in neither grammar reaches its guard.

    Such a version is refused as it is set (#492), so it is built unchecked:
    the guard is for a caller who got one past that.
    """
    with unchecked():
        flow = attrs.evolve(maximal.message().dataflows[0], version=version)
    with pytest.raises(ValidationError, match=rf"Dataflow:{flow.id}\.version"):
        write(StructureMessage(dataflows=[flow]))


@pytest.mark.parametrize(("value", "expected"), [(1.5, "1.5"), (1e16, "10000000000000000"), (2.0, "2")])
def test_a_facet_bound_is_written_as_a_decimal_literal(value: float, expected: str) -> None:
    msg = maximal.message()
    (cs,) = (cs for cs in msg.concept_schemes if any(isinstance(c.core_representation, sl.Facet) for c in cs.concepts))
    concepts = [
        attrs.evolve(c, core_representation=sl.Facet(type="Decimal", max_value=value))
        if isinstance(c.core_representation, sl.Facet)
        else c
        for c in cs.concepts
    ]
    root = etree.fromstring(ml30_writer.write(StructureMessage(concept_schemes=[attrs.evolve(cs, concepts=concepts)])))
    assert {el.get("maxValue") for el in root.iter("{*}TextFormat")} == {expected}


def _rewritten(msg: StructureMessage) -> etree._Element:
    """`msg` through the 3.0 writer alone: these are what `serialize` would refuse or drop first."""
    return etree.fromstring(ml30_writer.write(msg))


def _maximal_30() -> StructureMessage:
    return as_structure(sl.parse(_written("3.0")))


def test_an_enumeration_format_drops_what_its_type_prohibits() -> None:
    """`CodedTextFormatType` prohibits `decimals` and has no `isMultiLingual`; `TextFormat` keeps both."""
    msg = _maximal_30()
    dsd = msg.data_structures[0]
    dimension = dsd.dimensions[0]
    widened = attrs.evolve(present(dimension.enumeration_format), decimals=2, is_multilingual=True)
    # Unchecked: refused as it is built (#492); the writer, called directly, is the subject.
    with unchecked():
        dsd = attrs.evolve(dsd, dimensions=[attrs.evolve(dimension, enumeration_format=widened), *dsd.dimensions[1:]])
    root = _rewritten(attrs.evolve(msg, data_structures=sl.ArtefactList([dsd])))
    (fmt,) = (el for d in root.iter("{*}Dimension") for el in d.iter("{*}EnumerationFormat"))
    assert {"decimals", "isMultiLingual"}.isdisjoint(fmt.attrib)
    assert fmt.get("maxLength") is not None, "the case lost its subject: the rest is still written"


def test_a_coding_format_without_a_type_writes_none() -> None:
    msg = _maximal_30()
    (hierarchy,) = (h for h in msg.hierarchies if h.levels)
    levels = [
        attrs.evolve(level, coding_format=attrs.evolve(present(level.coding_format), type=None))
        for level in hierarchy.levels
    ]
    root = _rewritten(attrs.evolve(msg, hierarchies=sl.ArtefactList([attrs.evolve(hierarchy, levels=levels)])))
    formats = list(root.iter("{*}CodingFormat"))
    assert formats, "the case lost its subject"
    assert all("textType" not in el.attrib for el in formats)


def test_a_concept_with_a_text_format_writes_no_enumeration_format() -> None:
    """`ConceptRepresentation` puts one beside an `Enumeration` only (#422)."""
    msg = _maximal_30()
    (scheme,) = (cs for cs in msg.concept_schemes if any(c.enumeration_format for c in cs.concepts))
    fmt = next(c.enumeration_format for c in scheme.concepts if c.enumeration_format)
    concepts = [
        attrs.evolve(c, enumeration_format=fmt) if isinstance(c.core_representation, sl.Facet) else c
        for c in scheme.concepts
    ]
    # Unchecked: refused as it is built (#492); the writer, called directly, is the subject.
    with unchecked():
        scheme = attrs.evolve(scheme, concepts=concepts)
    root = _rewritten(attrs.evolve(msg, concept_schemes=sl.ArtefactList([scheme])))
    text_formatted = [c for c in root.iter("{*}Concept") if next(c.iter("{*}TextFormat"), None) is not None]
    assert text_formatted, "the case lost its subject"
    assert all(next(c.iter("{*}EnumerationFormat"), None) is None for c in text_formatted)


def test_a_value_in_several_languages_is_written_in_order_untagged_first() -> None:
    """By value, then language — an untagged value sorts as language ``""``."""
    msg = _maximal_30()
    constraint = msg.constraints[0]
    region = constraint.regions[0]
    key = region.keys[0]
    values = frozenset(sl.RegionValue(value="A", lang=lang) for lang in ("fr", None, "en"))
    region = attrs.evolve(region, keys=(attrs.evolve(key, values=values), *region.keys[1:]))
    # Unchecked: refused as it is built (#492); the writer, called directly, is the subject.
    with unchecked():
        constraint = attrs.evolve(constraint, regions=(region, *constraint.regions[1:]))
    root = _rewritten(attrs.evolve(msg, constraints=sl.ArtefactList([constraint])))
    region_el = next(root.iter("{*}CubeRegion"))
    key_el = next(el for el in region_el.iter("{*}KeyValue") if el.get("id") == key.dimension_id)
    assert [v.get("{http://www.w3.org/XML/1998/namespace}lang") for v in key_el.iter("{*}Value")] == [None, "en", "fr"]


@pytest.mark.parametrize("version", ["3.0", "3.1"])
def test_a_3_x_component_s_codelist_in_the_message_is_resolved(version: str) -> None:
    msg = _read(_doc(version))
    dsd = msg.data_structures[0]
    enumerated = [
        c.representation
        for c in (*dsd.dimensions, *dsd.attributes, *dsd.measures)
        if isinstance(c.representation, sl.Ref)
    ]
    in_message = {str(cl.urn) for cl in msg.codelists}
    named = [r for r in enumerated if str(r.urn) in in_message]
    assert named, "the case lost its subject: no component names a codelist in the message"
    assert all(r.resolved for r in named)


def test_a_2_1_data_set_naming_no_provider_is_refused() -> None:
    """`SetReferenceType` requires the provider; read as None, the reference meant nothing."""
    root = etree.fromstring(sl.serialize(shapes.message_21(), version="2.1"))
    data_set = next(root.iter("{*}DataSet"))
    data_set.remove(next(data_set.iter("{*}DataProvider")))
    assert "<DataSet>: SetReferenceType requires a <com:DataProvider>; this names none" in _refusal(root)


@pytest.mark.parametrize("version", _VERSIONS)
def test_a_dataflow_without_a_structure_has_none(version: str) -> None:
    """``minOccurs="0"`` in every version: a stub flow names no structure yet."""
    root = _doc(version)
    flow = _one(root, "str:Dataflow")
    flow.remove(present(flow.find("str:Structure", namespaces=NAMESPACES[version])))
    assert _read(root).dataflows[0].structure is None


def test_an_empty_simple_data_source_and_key_value_read_empty() -> None:
    root = etree.fromstring(sl.serialize(shapes.message_21(), version="2.1"))
    next(root.iter("{*}SimpleDataSource")).text = None
    next(next(root.iter("{*}DataKeySet")).iter("{*}Value")).text = None
    msg = _read(root)
    assert "" in {s for c in msg.constraints for s in c.attachment.simple_data_sources}
    # The fixture's one key set is an attachment constraint's.
    assert "" in {v for c in msg.attachment_constraints for ks in c.data_key_sets for k in ks.keys for _, v in k.values}


def test_a_2_1_urn_without_a_version_resolves_to_nothing() -> None:
    """An omitted version never means "whatever is latest" (#476): a reference so written stays unresolved."""
    root = _doc("2.1")
    structure = _one(root, "str:Dataflow/str:Structure")
    ref = present(structure.find("Ref"))
    structure.remove(ref)
    urn = f"urn:sdmx:org.sdmx.infomodel.datastructure.DataStructure={ref.get('agencyID')}:{ref.get('id')}"
    etree.SubElement(structure, "URN").text = urn
    versioned = _read(_doc("2.1")).dataflows[0].structure
    assert present(versioned).resolved, "the case lost its subject: the versioned form resolves"
    flow_structure = present(_read(root).dataflows[0].structure)
    assert (str(flow_structure.urn), flow_structure.resolved) == (urn, False)
