"""SDMX-ML 2.1 writer: domain model → lxml → bytes.

Serialises a StructureMessage (or any subset of its artefacts) to a
valid SDMX-ML 2.1 Structure message.

Example:
    from sdmxlib.formats.sdmx_ml21.writer import write

    xml_bytes = write(msg)
"""

from collections.abc import Sequence
from datetime import datetime

import attrs
from attrs import evolve, frozen
from lxml import etree
from typing_extensions import TypedDict

from sdmxlib.formats.gaps import without_dropped
from sdmxlib.formats.sdmx_ml._common import (
    SectionEntry,
    WriterHelpers,
    coding_format_attrs,
    hierarchy_containers,
    section_entry,
)
from sdmxlib.formats.sdmx_ml._header import write_header
from sdmxlib.model._container import inner_id
from sdmxlib.model.annotations import Annotation, Annotations
from sdmxlib.model.codelist import Codelist
from sdmxlib.model.collections import ArtefactList
from sdmxlib.model.constraint import (
    AttachmentConstraint,
    CubeRegion,
    DataConstraint,
)
from sdmxlib.model.datastructure import (
    REPORTING_YEAR_START_DAY,
    AttachmentLevel,
    Attribute,
    AttributeUsage,
    DataStructure,
    Dimension,
    Group,
    Measure,
    MeasureDimension,
    TimeDimension,
)
from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy, Level
from sdmxlib.model.mapping import (
    CategorySchemeMap,
    ComponentLocation,
    ComponentMap,
    ConceptSchemeMap,
    ItemMap,
    MapEndpoint,
    OrganisationSchemeMap,
    RepresentationMap,
    RepresentationMapping,
    SourceValue,
    StructureMap,
    StructureSet,
    component_location,
    is_built_value_map,
    mapped_structure,
)
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.metadata_provision import MetadataProvisionAgreement
from sdmxlib.model.metadataflow import Metadataflow
from sdmxlib.model.metadatastructure import (
    MetadataAttribute,
    MetadataStructure,
    MetadataTarget,
    ReportStructure,
    TargetObject,
)
from sdmxlib.model.organisation import (
    Agency,
    AgencyScheme,
    Contact,
    DataConsumer,
    DataConsumerScheme,
    DataProvider,
    DataProviderScheme,
    OrganisationUnit,
    OrganisationUnitScheme,
)
from sdmxlib.model.ref import Ref, artefact_urn
from sdmxlib.model.representation import Facet
from sdmxlib.model.validation import _PRIMARY_MEASURE_ID_21

from ._providers import data_provider_urn, with_providers_merged
from ._synthetic import (
    SYNTHETIC_REPORT_ID,
    SYNTHETIC_REPORT_LAYER_TYPE,
    SYNTHETIC_STRUCTURE_SET_ID,
    SYNTHETIC_STRUCTURE_SET_TYPE,
    SYNTHETIC_TARGET_ID,
    SYNTHETIC_TARGET_OBJECT,
)
from .namespaces import COM, MES, STR

# ── Namespace helpers ─────────────────────────────────────────────────────────

_NSMAP: dict[str, str] = {
    "mes": MES,
    "str": STR,
    "com": COM,
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
}

# ── Shared writer helpers ─────────────────────────────────────────────────────
# Pulled from sdmxlib.formats.sdmx_ml._common and used as `_helpers.x` so each
# call site says where the helper comes from. Only the namespace tag builders
# keep short names; they go away with the prefixed-path conversion (#209).
_helpers = WriterHelpers(com=COM, str_=STR, mes=MES, grammar="2.1")
_c = _helpers.c
_s = _helpers.s
_m = _helpers.m


# ── Codelists ─────────────────────────────────────────────────────────────────


def _write_concept_roles(parent: etree._Element, roles: tuple[str, ...]) -> None:
    """Emit one ``<str:ConceptRole>`` per role.

    References the SDMX cross-domain concept scheme (the canonical home for
    the standard roles). Per the v2.1 XSD the component sequence places
    ConceptRole after LocalRepresentation.
    """
    for role in roles:
        cr = etree.SubElement(parent, _s("ConceptRole"))
        ref_el = etree.SubElement(cr, "Ref")
        ref_el.set("agencyID", "SDMX")
        ref_el.set("maintainableParentID", "CROSS_DOMAIN_CONCEPTS")
        ref_el.set("maintainableParentVersion", "1.0")
        ref_el.set("id", role)
        ref_el.set("class", "Concept")
        ref_el.set("package", "conceptscheme")


def _write_contact(parent: etree._Element, contact: Contact) -> None:
    """Emit a v2.1 ``<str:Contact>``.

    Name is common-namespaced; Department / Role / the communication
    channels are structure-namespaced per ContactType.
    """
    el = etree.SubElement(parent, _s("Contact"))
    if contact.id is not None:
        el.set("id", contact.id)
    if contact.name:
        _helpers.write_istring(el, contact.name)
    for lang, text in contact.department._backing().items():
        dept_el = etree.SubElement(el, _s("Department"))
        dept_el.set("{http://www.w3.org/XML/1998/namespace}lang", lang)
        dept_el.text = text
    for lang, text in contact.role._backing().items():
        role_el = etree.SubElement(el, _s("Role"))
        role_el.set("{http://www.w3.org/XML/1998/namespace}lang", lang)
        role_el.text = text
    for phone in contact.phones:
        etree.SubElement(el, _s("Telephone")).text = phone
    for fax in contact.faxes:
        etree.SubElement(el, _s("Fax")).text = fax
    for x400 in contact.x400s:
        etree.SubElement(el, _s("X400")).text = x400
    for uri in contact.uris:
        etree.SubElement(el, _s("URI")).text = uri
    for email in contact.emails:
        etree.SubElement(el, _s("Email")).text = email


def _write_agency(parent: etree._Element, agency: Agency) -> None:
    el = etree.SubElement(parent, _s("Agency"))
    el.set("id", agency.id)
    _helpers.write_uri(el, agency)
    _helpers.write_annotations(el, agency.annotations)
    if agency.name:
        _helpers.write_istring(el, agency.name)
    if agency.description:
        _helpers.write_istring(el, agency.description, "Description")
    for contact in agency.contacts:
        _write_contact(el, contact)


def _write_agency_scheme(parent: etree._Element, scheme: AgencyScheme) -> None:
    el = etree.SubElement(parent, _s("AgencyScheme"))
    _helpers.write_maintainable_attrs(el, scheme)
    _helpers.write_annotations(el, scheme.annotations)
    if scheme.name:
        _helpers.write_istring(el, scheme.name)
    if scheme.description:
        _helpers.write_istring(el, scheme.description, "Description")
    for agency in scheme.agencies:
        _write_agency(el, agency)


def _write_data_provider(parent: etree._Element, provider: DataProvider) -> None:
    el = etree.SubElement(parent, _s("DataProvider"))
    el.set("id", provider.id)
    _helpers.write_uri(el, provider)
    _helpers.write_annotations(el, provider.annotations)
    if provider.name:
        _helpers.write_istring(el, provider.name)
    if provider.description:
        _helpers.write_istring(el, provider.description, "Description")
    for contact in provider.contacts:
        _write_contact(el, contact)


def _write_data_provider_scheme(parent: etree._Element, scheme: DataProviderScheme) -> None:
    el = etree.SubElement(parent, _s("DataProviderScheme"))
    _helpers.write_maintainable_attrs(el, scheme)
    _helpers.write_annotations(el, scheme.annotations)
    if scheme.name:
        _helpers.write_istring(el, scheme.name)
    if scheme.description:
        _helpers.write_istring(el, scheme.description, "Description")
    for provider in scheme.providers:
        _write_data_provider(el, provider)


def _write_organisation(parent: etree._Element, tag: str, item: DataConsumer | OrganisationUnit) -> None:
    """One organisation item: annotations, names, a unit's `<Parent>`, then contacts, in the schema's order (#469)."""
    el = etree.SubElement(parent, _s(tag))
    el.set("id", item.id)
    _helpers.write_uri(el, item)
    _helpers.write_annotations(el, item.annotations)
    _helpers.write_istring(el, item.name)
    if item.description:
        _helpers.write_istring(el, item.description, "Description")
    if isinstance(item, OrganisationUnit) and item.parent_id is not None:
        ref = etree.SubElement(etree.SubElement(el, _s("Parent")), "Ref")
        ref.set("id", item.parent_id)
    for contact in item.contacts:
        _write_contact(el, contact)


def _write_data_consumer_scheme(parent: etree._Element, scheme: DataConsumerScheme) -> None:
    el = etree.SubElement(parent, _s("DataConsumerScheme"))
    _helpers.write_maintainable_attrs(el, scheme)
    _helpers.write_annotations(el, scheme.annotations)
    _helpers.write_istring(el, scheme.name)
    if scheme.description:
        _helpers.write_istring(el, scheme.description, "Description")
    for consumer in scheme.consumers:
        _write_organisation(el, "DataConsumer", consumer)


def _write_organisation_unit_scheme(parent: etree._Element, scheme: OrganisationUnitScheme) -> None:
    el = etree.SubElement(parent, _s("OrganisationUnitScheme"))
    _helpers.write_maintainable_attrs(el, scheme)
    _helpers.write_annotations(el, scheme.annotations)
    _helpers.write_istring(el, scheme.name)
    if scheme.description:
        _helpers.write_istring(el, scheme.description, "Description")
    for unit in scheme.organisation_units:
        _write_organisation(el, "OrganisationUnit", unit)


def _write_metadata_provision_agreement(parent: etree._Element, mpa: MetadataProvisionAgreement) -> None:
    """A metadata provision agreement as 2.1 provisioned metadata: an agreement over a metadataflow (#421).

    Its provider is named as the data provider of the same id, which is where
    `with_providers_merged` writes it.
    """
    el = etree.SubElement(parent, _s("ProvisionAgreement"))
    _helpers.write_maintainable_attrs(el, mpa)
    _helpers.write_annotations(el, mpa.annotations)
    _helpers.write_istring(el, mpa.name)
    if mpa.description:
        _helpers.write_istring(el, mpa.description, "Description")
    if mpa.metadataflow is not None:
        _helpers.write_ref(etree.SubElement(el, _s("StructureUsage")), mpa.metadataflow)
    if mpa.metadata_provider is not None:
        provider = Ref[DataProvider].unresolved(data_provider_urn(mpa.metadata_provider.urn))
        _helpers.write_ref(etree.SubElement(el, _s("DataProvider")), provider)


def _write_dimension(parent: etree._Element, dim: Dimension, position: int) -> None:
    el = etree.SubElement(parent, _s("Dimension"))
    el.set("id", dim.id)
    _helpers.write_uri(el, dim)
    el.set("position", str(position))
    _helpers.write_annotations(el, dim.annotations)
    if isinstance(dim.concept, Ref):
        _helpers.write_concept_identity(el, dim.concept)
    if isinstance(dim.representation, (Ref, Facet)):
        _helpers.write_representation(el, dim.representation, enumeration_format=dim.enumeration_format)
    _write_concept_roles(el, dim.concept_role)


def _write_measure_dimension(parent: etree._Element, md: MeasureDimension, position: int) -> None:
    """Emit a SDMX 2.1 cross-sectional ``<str:MeasureDimension>``.

    The component is enumerated by a ConceptScheme. Per
    DimensionDescriptorType it follows the regular ``<Dimension>`` entries
    and precedes ``<TimeDimension>``.
    """
    el = etree.SubElement(parent, _s("MeasureDimension"))
    el.set("id", md.id)
    _helpers.write_uri(el, md)
    el.set("position", str(position))
    _helpers.write_annotations(el, md.annotations)
    if isinstance(md.concept, Ref):
        _helpers.write_concept_identity(el, md.concept)
    if isinstance(md.representation, (Ref, Facet)):
        _helpers.write_representation(el, md.representation)
    _write_concept_roles(el, md.concept_role)


def _write_time_dimension(parent: etree._Element, td: TimeDimension, position: int) -> None:
    """Emit ``<str:TimeDimension>``.

    ``position`` is 1-based over the whole dimension list. 2.1 states it
    explicitly because the time dimension is a *separate element* from the
    ordinary dimensions, so its place in the key cannot be read off document
    order the way theirs can. The writer computed positions for dimensions and
    the measure dimension and omitted this one, so a document stating
    ``position="6"`` came back without it (#237).
    """
    el = etree.SubElement(parent, _s("TimeDimension"))
    el.set("id", td.id)
    _helpers.write_uri(el, td)
    el.set("position", str(position))
    _helpers.write_annotations(el, td.annotations)
    if isinstance(td.concept, Ref):
        _helpers.write_concept_identity(el, td.concept)
    if td.representation is not None:
        local_rep = etree.SubElement(el, _s("LocalRepresentation"))
        _helpers.write_text_format(local_rep, td.representation)


def _write_attribute_relationship(parent: etree._Element, attr: Attribute) -> None:
    ar = etree.SubElement(parent, _s("AttributeRelationship"))
    match attr.attachment:
        case AttachmentLevel.OBSERVATION:
            pm = etree.SubElement(ar, _s("PrimaryMeasure"))
            ref = etree.SubElement(pm, "Ref")
            # 2.1 requires a measure ref here and pins its id, so the constant
            # is the version rule rather than anything read off the artefact —
            # which is why `Attribute` no longer carries a `related_measure`
            # for this to fall back from (#360). Imported rather than
            # re-spelled: `_write_measure` names the measure by the same
            # constant (#421), and two copies of the literal could drift into
            # disagreeing about what 2.1 permits.
            ref.set("id", _PRIMARY_MEASURE_ID_21)
        case AttachmentLevel.SERIES:
            for dim_id in attr.related_dimensions or []:
                dim_el = etree.SubElement(ar, _s("Dimension"))
                ref = etree.SubElement(dim_el, "Ref")
                ref.set("id", dim_id)
            for group_id in attr.attachment_groups:
                group_el = etree.SubElement(ar, _s("AttachmentGroup"))
                etree.SubElement(group_el, "Ref").set("id", group_id)
        case AttachmentLevel.GROUP:
            # Per the v2.1 XSD, <Group> is LocalGroupKeyDescriptorReferenceType —
            # a wrapped <Ref id="..."/> pointing at the GroupDescriptor.
            group_el = etree.SubElement(ar, _s("Group"))
            ref = etree.SubElement(group_el, "Ref")
            if attr.related_group is not None:
                ref.set("id", attr.related_group)
        case _:
            etree.SubElement(ar, _s("None"))


def _write_attribute(parent: etree._Element, attr: Attribute) -> None:
    # 2.1 reserves the id for its `ReportingYearStartDay`, so an attribute by
    # that id is one; `validate` refuses one 2.1 cannot write (#422).
    tag = "ReportingYearStartDay" if attr.id == REPORTING_YEAR_START_DAY else "Attribute"
    el = etree.SubElement(parent, _s(tag))
    el.set("id", attr.id)
    _helpers.write_uri(el, attr)
    # ``assignmentStatus`` is `use="required"` in the 2.1 XSD, so it is emitted
    # unconditionally. 2.1's AssignmentStatusType is {Mandatory, Conditional};
    # map from the 3.0-canonical {mandatory, optional} we hold in the model.
    # An attribute with no usage is Conditional — the weaker of the two, which
    # is what "unspecified" means for a consumer.
    el.set("assignmentStatus", "Mandatory" if attr.usage is AttributeUsage.MANDATORY else "Conditional")
    _helpers.write_annotations(el, attr.annotations)
    if isinstance(attr.concept, Ref):
        _helpers.write_concept_identity(el, attr.concept)
    if isinstance(attr.representation, (Ref, Facet)):
        _helpers.write_representation(el, attr.representation, enumeration_format=attr.enumeration_format)
    _write_concept_roles(el, attr.concept_role)
    _write_attribute_relationship(el, attr)


def _write_measure(parent: etree._Element, measure: Measure) -> None:
    el = etree.SubElement(parent, _s("PrimaryMeasure"))
    # 2.1 pins the id, so a 3.x measure named anything else is written under
    # it — a conversion `formats.gaps` declares (#421). `validate` has already
    # refused a DSD with more than one, so this cannot merge two measures.
    el.set("id", _PRIMARY_MEASURE_ID_21)
    _helpers.write_uri(el, measure)
    _helpers.write_annotations(el, measure.annotations)
    if isinstance(measure.concept, Ref):
        _helpers.write_concept_identity(el, measure.concept)
    if isinstance(measure.representation, (Ref, Facet)):
        _helpers.write_representation(el, measure.representation, enumeration_format=measure.enumeration_format)
    # NB: the v2.1 PrimaryMeasureType XSD does not permit ConceptRole (unlike
    # DimensionType / AttributeType), so measure concept roles are not emitted.


def _write_group(parent: etree._Element, group: Group) -> None:
    el = etree.SubElement(parent, _s("Group"))
    el.set("id", group.id)
    _helpers.write_uri(el, group)
    _helpers.write_annotations(el, group.annotations)
    for dim_id in group.dimension_ids:
        gd = etree.SubElement(el, _s("GroupDimension"))
        dr = etree.SubElement(gd, _s("DimensionReference"))
        ref = etree.SubElement(dr, "Ref")
        ref.set("id", dim_id)
    if group.attachment_constraint is not None:
        _helpers.write_ref(etree.SubElement(el, _s("AttachmentConstraint")), group.attachment_constraint)


def _write_dimension_list(components: etree._Element, dsd: DataStructure) -> None:
    """Emit ``<str:DimensionList>``: Dimension* → MeasureDimension? → TimeDimension?."""
    dim_list = etree.SubElement(components, _s("DimensionList"))
    dim_list.set("id", "DimensionDescriptor")
    if dsd.dimension_descriptor_uri is not None:
        dim_list.set("uri", dsd.dimension_descriptor_uri)
    _helpers.write_annotations(dim_list, dsd.dimension_descriptor_annotations)
    for i, dim in enumerate(dsd.dimensions, start=1):
        _write_dimension(dim_list, dim, i)
    if dsd.measure_dimension is not None:
        _write_measure_dimension(dim_list, dsd.measure_dimension, len(dsd.dimensions) + 1)
    if dsd.time_dimension is not None:
        time_position = len(dsd.dimensions) + (1 if dsd.measure_dimension is not None else 0) + 1
        _write_time_dimension(dim_list, dsd.time_dimension, time_position)


def _write_data_structure(parent: etree._Element, dsd: DataStructure) -> None:
    el = etree.SubElement(parent, _s("DataStructure"))
    _helpers.write_maintainable_attrs(el, dsd)
    _helpers.write_annotations(el, dsd.annotations)
    _helpers.write_istring(el, dsd.name)
    if dsd.description:
        _helpers.write_istring(el, dsd.description, "Description")

    components = etree.SubElement(el, _s("DataStructureComponents"))

    _write_dimension_list(components, dsd)

    if dsd.groups:
        for group in dsd.groups:
            _write_group(components, group)

    if dsd.attributes:
        attr_list = etree.SubElement(components, _s("AttributeList"))
        attr_list.set("id", "AttributeDescriptor")
        if dsd.attribute_descriptor_uri is not None:
            attr_list.set("uri", dsd.attribute_descriptor_uri)
        _helpers.write_annotations(attr_list, dsd.attribute_descriptor_annotations)
        for attr in dsd.attributes:
            _write_attribute(attr_list, attr)

    # `<MeasureList>` is mandatory in DataStructureComponentsType, and
    # MeasureListType requires a `<PrimaryMeasure>` inside it ("Only a primary
    # may be defined"). A measure-less DSD therefore has no valid SDMX-ML 2.1
    # encoding at all — emitting the document without it produced XML the
    # schema rejects, and which sdmxlib's own reader now refuses. Fail instead
    # of writing something unusable.
    if not dsd.measures:
        msg = (
            f"DataStructure {dsd.agency_id}:{dsd.id}({dsd.version}) has no measures, "
            "which SDMX-ML 2.1 cannot represent — <MeasureList> is required and must "
            "contain a <PrimaryMeasure>. Add a measure, or serialise to 3.0."
        )
        raise ValueError(msg)
    measure_list = etree.SubElement(components, _s("MeasureList"))
    measure_list.set("id", "MeasureDescriptor")
    if dsd.measure_descriptor_uri is not None:
        measure_list.set("uri", dsd.measure_descriptor_uri)
    _helpers.write_annotations(measure_list, dsd.measure_descriptor_annotations)
    for measure in dsd.measures:
        _write_measure(measure_list, measure)


# ── Dataflows ─────────────────────────────────────────────────────────────────


def _write_cube_region(parent: etree._Element, region: CubeRegion) -> None:
    el = etree.SubElement(parent, _s("CubeRegion"))
    if not region.include:
        el.set("include", "false")
    # `CubeRegionType` sequences `KeyValue*` then `Attribute*`, all in `com:`.
    for kv in region.keys:
        _helpers.write_selection(el, _c, "KeyValue", kv)
    for component in region.components:
        _helpers.write_selection(el, _c, "Attribute", component)


def _write_data_constraint(parent: etree._Element, cc: DataConstraint) -> None:
    el = etree.SubElement(parent, _s("ContentConstraint"))
    _helpers.write_maintainable_attrs(el, cc)
    el.set("type", cc.constraint_type.value)
    _helpers.write_annotations(el, cc.annotations)
    _helpers.write_istring(el, cc.name)
    if cc.description:
        _helpers.write_istring(el, cc.description, "Description")
    _helpers.write_constraint_attachment(el, cc.attachment)
    # DataKeySet precedes CubeRegion in ContentConstraintBaseType's sequence.
    _helpers.write_data_key_sets(el, cc.data_key_sets)
    for region in cc.regions:
        _write_cube_region(el, region)
    # 2.1 ContentConstraintType extension sequence: ReleaseCalendar BEFORE
    # ReferencePeriod (per SDMXStructureConstraint.xsd, in that order).
    if cc.release_calendar is not None:
        rc_el = etree.SubElement(el, _s("ReleaseCalendar"))
        etree.SubElement(rc_el, _s("Periodicity")).text = cc.release_calendar.periodicity
        etree.SubElement(rc_el, _s("Offset")).text = cc.release_calendar.offset
        etree.SubElement(rc_el, _s("Tolerance")).text = cc.release_calendar.tolerance
    if cc.reference_period is not None:
        rp_el = etree.SubElement(el, _s("ReferencePeriod"))
        rp_el.set("startTime", cc.reference_period.start_period.isoformat())
        rp_el.set("endTime", cc.reference_period.end_period.isoformat())


def _write_attachment_constraint(parent: etree._Element, ac: AttachmentConstraint) -> None:
    el = etree.SubElement(parent, _s("AttachmentConstraint"))
    _helpers.write_maintainable_attrs(el, ac)
    _helpers.write_annotations(el, ac.annotations)
    _helpers.write_istring(el, ac.name)
    if ac.description:
        _helpers.write_istring(el, ac.description, "Description")
    _helpers.write_constraint_attachment(el, ac.attachment)
    _helpers.write_data_key_sets(el, ac.data_key_sets)


# ── StructureSets ─────────────────────────────────────────────────────────────


def _write_codelist_map(parent: etree._Element, rm: RepresentationMap) -> None:
    """Emit a 2.1 ``<str:CodelistMap>`` from a top-level RepresentationMap.

    The 2.1 type is one codelist to one codelist, code to code. The broader
    3.x shapes — several or datatype endpoints, regex, index and N:M
    mappings — are refused before writing (`check_writable`); were one to
    reach here anyway, it is left out rather than restated as a code pair.
    """
    pairs = [pair for m in rm.mappings if (pair := _code_pair(m)) is not None]
    ends = (_only_codelist(rm.sources), _only_codelist(rm.targets))
    _helpers.write_scheme_map(parent, "CodelistMap", rm, "CodeMap", ends=ends, pairs=pairs)


def _only_codelist(endpoints: tuple[MapEndpoint, ...]) -> Ref[object] | None:
    """A map side's one codelist, or None when it is anything 2.1 cannot name."""
    match endpoints:
        case (Ref() as ref,):
            return ref
        case _:
            return None


def _code_pair(mapping: RepresentationMapping) -> ItemMap | None:
    """A mapping as 2.1's code-to-code pair, or None when it is not one."""
    match mapping.sources, mapping.targets:
        case (SourceValue(value=source) as sv,), (target,) if sv.is_literal:
            return ItemMap(source_id=source, target_id=target, annotations=mapping.annotations)
        case _:
            return None


def _write_concept_scheme_map(parent: etree._Element, csm: ConceptSchemeMap) -> None:
    _helpers.write_scheme_map(
        parent, "ConceptSchemeMap", csm, "ConceptMap", ends=(csm.source, csm.target), pairs=csm.maps
    )


def _write_organisation_scheme_map(parent: etree._Element, osm: OrganisationSchemeMap) -> None:
    _helpers.write_scheme_map(
        parent, "OrganisationSchemeMap", osm, "OrganisationMap", ends=(osm.source, osm.target), pairs=osm.maps
    )


def _write_category_scheme_map(parent: etree._Element, csm: CategorySchemeMap) -> None:
    _helpers.write_scheme_map(
        parent, "CategorySchemeMap", csm, "CategoryMap", ends=(csm.source, csm.target), pairs=csm.maps
    )


class _MappedStructures:
    """Resolves a StructureMap's endpoints against the message being written.

    2.1 needs each mapped component's class, which lives on the structure the
    map names rather than on the map. The message is the only place to find
    it — serialisation does not fetch — so this closes over the message's
    structures and dataflows once instead of rebuilding the URN index per map.
    """

    def __init__(self, msg: StructureMessage) -> None:
        self._data_structures = list(msg.data_structures)
        self._dataflows = list(msg.dataflows)

    def __call__(self, sm: StructureMap) -> tuple[DataStructure | None, DataStructure | None]:
        """The structures this map's source and target resolve to, if any."""
        return (
            mapped_structure(sm.source, self._data_structures, self._dataflows),
            mapped_structure(sm.target, self._data_structures, self._dataflows),
        )


def _write_local_component_ref(parent: etree._Element, component_id: str, where: ComponentLocation | None) -> None:
    """Emit a ``<Ref>`` under a ComponentMap Source or Target.

    ``LocalComponentListComponentRefType`` requires all four attributes: the
    component id, its object class, the package that class lives in, and the
    id of the ComponentList holding it. `where` supplies the three that a
    `ComponentMap` does not carry, read off the mapped structure.

    `where` is None only when validation was skipped — it is what
    `_check_structure_map_in_21` refuses on. The ref is still emitted, with
    the id alone, so the caller who passed ``validate=False`` gets the
    document they asked for rather than a silent omission.
    """
    ref_el = etree.SubElement(parent, "Ref")
    ref_el.set("id", component_id)
    if where is not None:
        ref_el.set("class", where.sdmx_class)
        ref_el.set("package", "datastructure")
        ref_el.set("containerID", where.descriptor_id)


@frozen
class _LocalMaps:
    """What a structure map's component maps can name from inside one ``<str:StructureSet>`` (#422).

    Attributes:
        codelist_maps: URN → id of each codelist map written into the same
            set, which 2.1 references by id alone.
        value_maps: URN → each value map a 2.1 reader built, written inline
            wherever a component map names it, never as an artefact.
    """

    codelist_maps: dict[str, str]
    value_maps: dict[str, RepresentationMap]


def _write_structure_map(
    parent: etree._Element, sm: StructureMap, structures: _MappedStructures, local: _LocalMaps
) -> None:
    """Emit a 2.1 ``<str:StructureMap>``.

    The element is ``StructureMap`` whatever the source artefact is —
    ``StructureSetType`` names exactly one such child and the Source/Target
    references say what is being mapped. This previously emitted
    ``<DataStructureMap>``, and the dataflow-source case ``<DataflowMap>``;
    neither element exists anywhere in SDMX-ML 2.1. Both were invented from
    the 3.0 vocabulary, and the 2.1 reader looked for the same invented names,
    so the pair round-tripped cleanly through sdmxlib and failed XSD
    validation for every other consumer (#226).

    The dataflow-source case used to emit no ComponentMaps at all, which the
    XSD requires at least one of. It was never reachable — 2.1 refused every
    structure map outright — and there is nothing special about it: a
    dataflow stands in for the structure it publishes, so its components are
    that structure's (#242).
    """
    el = etree.SubElement(parent, _s("StructureMap"))
    el.set("id", sm.id)
    if sm.is_extension:
        el.set("isExtension", "true")
    _helpers.write_annotations(el, sm.annotations)
    _helpers.write_uri(el, sm)
    _helpers.write_istring(el, sm.name)
    if sm.description:
        _helpers.write_istring(el, sm.description, "Description")
    if sm.source is not None:
        src_el = etree.SubElement(el, _s("Source"))
        _helpers.write_ref(src_el, sm.source)
    if sm.target is not None:
        tgt_el = etree.SubElement(el, _s("Target"))
        _helpers.write_ref(tgt_el, sm.target)
    source_dsd, target_dsd = structures(sm)
    for comp_map in sm.maps:
        m_el = etree.SubElement(el, _s("ComponentMap"))
        _helpers.write_annotations(m_el, comp_map.annotations)
        src_el = etree.SubElement(m_el, _s("Source"))
        # One of each: `check_writable` refuses an N:M map for 2.1 (#422).
        source_id, target_id = comp_map.sources[0], comp_map.targets[0]
        _write_local_component_ref(
            src_el,
            source_id,
            None if source_dsd is None else component_location(source_dsd, source_id),
        )
        tgt_el = etree.SubElement(m_el, _s("Target"))
        _write_local_component_ref(
            tgt_el,
            target_id,
            None if target_dsd is None else component_location(target_dsd, target_id),
        )
        _write_representation_mapping(m_el, comp_map, local)


def _write_representation_mapping(parent: etree._Element, comp_map: ComponentMap, local: _LocalMaps) -> None:
    """A component map's ``<RepresentationMapping>``, when 2.1 can say it (#422).

    A codelist map written into the same structure set is named locally; a
    value map the 2.1 reader built goes back inline as the ``<ValueMap>`` it
    was; and a target text format is itself. Anything else — an epoch or
    date-pattern map, a map in another set — has no 2.1 form and is dropped,
    declared in `formats/gaps.py`.
    """
    if comp_map.representation_map is not None:
        key = str(comp_map.representation_map.urn)
        if (map_id := local.codelist_maps.get(key)) is not None:
            rm_el = etree.SubElement(parent, _s("RepresentationMapping"))
            etree.SubElement(etree.SubElement(rm_el, _s("CodelistMap")), "Ref").set("id", map_id)
        elif (value_map := local.value_maps.get(key)) is not None:
            vm_el = etree.SubElement(etree.SubElement(parent, _s("RepresentationMapping")), _s("ValueMap"))
            for mapping in value_map.mappings:
                pair_el = etree.SubElement(vm_el, _s("ValueMapping"))
                pair_el.set("source", mapping.sources[0].value)
                pair_el.set("target", mapping.targets[0])
    elif comp_map.to_text_format is not None and comp_map.to_value_type is not None:
        rm_el = etree.SubElement(parent, _s("RepresentationMapping"))
        _helpers.write_text_format(rm_el, comp_map.to_text_format, "ToTextFormat")
        etree.SubElement(rm_el, _s("ToValueType")).text = comp_map.to_value_type.value


#: The map classes 2.1 writes inside a ``<str:StructureSet>``.
type _Map = RepresentationMap | StructureMap | ConceptSchemeMap | CategorySchemeMap | OrganisationSchemeMap


def _write_set_members(
    el: etree._Element,
    members: list[_Map],
    structures: _MappedStructures,
    value_maps: dict[str, RepresentationMap],
    set_id: str | None,
) -> None:
    """Write one set's maps, its codelist maps nameable by its structure maps.

    Each by its id inside the set (#421): ``CLM`` for ``CLM@SS`` in ``SS``,
    which a reader turns back into ``CLM@SS``. `set_id` is None for a set this
    library invents, inside which a map keeps its own id.
    """
    written = [m if set_id is None else attrs.evolve(m, id=inner_id(m.id, set_id) or m.id) for m in members]
    local = _LocalMaps(
        codelist_maps={
            str(artefact_urn(m)): w.id
            for m, w in zip(members, written, strict=True)
            if isinstance(m, RepresentationMap)
        },
        value_maps=value_maps,
    )
    for m in written:
        _write_map_into(el, m, structures, local)


def _write_map_into(el: etree._Element, m: object, structures: _MappedStructures, local: _LocalMaps) -> None:
    """Dispatch one map artefact into an open ``<str:StructureSet>``."""
    if isinstance(m, RepresentationMap):
        _write_codelist_map(el, m)
    elif isinstance(m, ConceptSchemeMap):
        _write_concept_scheme_map(el, m)
    elif isinstance(m, CategorySchemeMap):
        _write_category_scheme_map(el, m)
    elif isinstance(m, OrganisationSchemeMap):
        _write_organisation_scheme_map(el, m)
    elif isinstance(m, StructureMap):
        _write_structure_map(el, m, structures, local)


def _write_structure_sets(parent: etree._Element, msg: StructureMessage) -> None:
    """Emit ``<str:StructureSet>``s, declared ones first.

    A `StructureSet` in the message is written as itself — its own id, name,
    validity dates and grouping — because 2.1 makes the container a
    maintainable and discarding it changed the document (#239).

    Maps belonging to no declared set still need a home, since 2.1 has nowhere
    else to put them: those are grouped by agency and version into a synthetic
    wrapper, which is what every map used to get.
    """
    value_maps = {str(artefact_urn(m)): m for m in msg.representation_maps if is_built_value_map(m)}
    all_maps: list[_Map] = [
        *(m for m in msg.representation_maps if not is_built_value_map(m)),
        *msg.concept_scheme_maps,
        *msg.category_scheme_maps,
        *msg.organisation_scheme_maps,
        *msg.structure_maps,
    ]
    by_urn = {str(artefact_urn(m)): m for m in all_maps}
    claimed: set[str] = set(value_maps)
    structures = _MappedStructures(msg)

    for structure_set in msg.structure_sets:
        members: list[_Map] = []
        for ref in structure_set.maps:
            key = str(ref.urn)
            claimed.add(key)
            member = by_urn.get(key)
            if member is not None:
                members.append(member)
        _write_structure_set(parent, structure_set, members, structures, value_maps)

    orphans = [m for urn, m in by_urn.items() if urn not in claimed]
    for wrapper, maps in _synthetic_sets(orphans):
        _write_structure_set(parent, wrapper, maps, structures, value_maps)


def _write_structure_set(
    parent: etree._Element,
    structure_set: StructureSet,
    members: list[_Map],
    structures: _MappedStructures,
    value_maps: dict[str, RepresentationMap],
) -> None:
    """Emit one ``<str:StructureSet>`` holding `members`."""
    el = etree.SubElement(parent, _s("StructureSet"))
    _helpers.write_maintainable_attrs(el, structure_set)
    _helpers.write_annotations(el, structure_set.annotations)
    _helpers.write_istring(el, structure_set.name)
    if structure_set.description:
        _helpers.write_istring(el, structure_set.description, "Description")
    for ref in structure_set.related_structures:
        _helpers.write_ref(etree.SubElement(el, _s("RelatedStructure")), ref)
    synthetic = SYNTHETIC_STRUCTURE_SET_TYPE in structure_set.annotations.types
    _write_set_members(el, members, structures, value_maps, None if synthetic else structure_set.id)


def _synthetic_sets(orphans: list[_Map]) -> list[tuple[StructureSet, list[_Map]]]:
    """A marked wrapper for the maps no declared set claims, with what each holds.

    2.1 has nowhere else to put a map. One wrapper per agency, version,
    validity and external reference: what a 2.1 reader gives a map is its
    set's, so a map's own come back only from a set that carries them (#421).
    A second wrapper of one agency and version gets a numbered id.
    """
    groups: dict[tuple[object, ...], list[_Map]] = {}
    for m in orphans:
        groups.setdefault(_held_by_set(m), []).append(m)
    numbered: dict[tuple[str, str], int] = {}
    out: list[tuple[StructureSet, list[_Map]]] = []
    for maps in groups.values():
        first = maps[0]
        n = numbered[first.agency_id, first.version] = numbered.get((first.agency_id, first.version), 0) + 1
        wrapper = StructureSet(
            id=SYNTHETIC_STRUCTURE_SET_ID if n == 1 else f"{SYNTHETIC_STRUCTURE_SET_ID}_{n}",
            maintainer=first.maintainer,
            version=first.version,
            name={"en": "Synthetic structure-set wrapper"},
            annotations=Annotations([Annotation(type=SYNTHETIC_STRUCTURE_SET_TYPE)]),
            **_set_attrs(first),
        )
        out.append((wrapper, maps))
    return out


def _held_by_set(m: _Map) -> tuple[object, ...]:
    """What a map takes from the 2.1 set around it, as a grouping key."""
    return (m.agency_id, m.version, *_set_attrs(m).values())


def _set_attrs(m: _Map) -> _SetAttrs:
    """The validity and external reference a map's 2.1 set must carry for it."""
    return _SetAttrs(
        valid_from=m.valid_from,
        valid_to=m.valid_to,
        is_external_reference=m.is_external_reference,
        service_url=m.service_url,
        structure_url=m.structure_url,
    )


class _SetAttrs(TypedDict, closed=True):
    valid_from: datetime | None
    valid_to: datetime | None
    is_external_reference: bool
    service_url: str | None
    structure_url: str | None


# ── Hierarchies ───────────────────────────────────────────────────────────────


def _write_hierarchical_code(parent: etree._Element, hc: HierarchicalCode) -> None:
    """Emit a v2.1 ``<str:HierarchicalCode>`` per HierarchicalCodeType.

    The XSD restricts children to: Annotations (optional), then
    ``<str:Code>`` (full ref) OR
    ``<str:CodelistAliasRef>+<str:CodeID>`` (alias form, requires a
    matching ``<str:IncludedCodelist>`` on the containing HCL), then
    nested ``<str:HierarchicalCode>`` children. Name and Description are
    NOT allowed on a HierarchicalCode in 2.1 — they live on the codelist
    item the ref points at, not on the hierarchy node.

    We always emit the ``<str:Code>`` form because our model carries a
    fully-resolved code Ref. The alias form would require the model to
    also track an alias table on the HCL, which it doesn't.
    """
    el = etree.SubElement(parent, _s("HierarchicalCode"))
    el.set("id", hc.id)
    _helpers.write_uri(el, hc)
    if hc.version is not None:
        el.set("version", hc.version)
    if hc.valid_from is not None:
        el.set("validFrom", hc.valid_from.isoformat())
    if hc.valid_to is not None:
        el.set("validTo", hc.valid_to.isoformat())
    _helpers.write_annotations(el, hc.annotations)
    if hc.code is not None:
        code_el = etree.SubElement(el, _s("Code"))
        _helpers.write_ref(code_el, hc.code)
    for child in hc.children:
        _write_hierarchical_code(el, child)
    if hc.level is not None:
        # Last in `HierarchicalCodeType`'s sequence — after the nested codes,
        # not before them. 2.1 wraps the reference (`LocalLevelReferenceType`
        # is a `<Ref>` child) where 3.0 spells it as text, the same split as
        # `Parent`. Emitted by no 2.1 writer until #355, so a node's level
        # survived a 3.0 round-trip and vanished in 2.1.
        level_el = etree.SubElement(el, _s("Level"))
        etree.SubElement(level_el, "Ref").set("id", hc.level.id)


def _write_coding_format(cf_el: etree._Element, cf: Facet) -> None:
    """Set the attributes ``CodingTextFormatType`` permits on a Level format.

    It restricts SimpleComponentTextFormatType, but far less than this writer
    once assumed: textType, isSequence, interval, startValue, endValue,
    minLength, maxLength, minValue, maxValue and pattern are all allowed, and
    only ``decimals`` and the three time facets are prohibited. The writer
    emitted the first four and dropped the rest behind a comment claiming they
    "aren't permitted" — they are, and the 3.0 writer had been emitting them
    all along, so one hierarchy lost facets in 2.1 and kept them in 3.0 (#230).

    **#230 half-landed**, which #354's field census found: it restored
    minLength / maxLength / pattern and left ``minValue`` / ``maxValue`` out,
    so those two kept the exact loss #230 was about. They are `use="optional"`
    on `CodingTextFormatType` like the rest.

    The bounds go through `xsd_num`, not ``str``. `CodingTextFormatType` types
    them ``xs:positiveInteger``, so ``str(1.0)`` emits ``"1.0"`` — rejected by
    the XSD *and* by this library's own reader. That is #243's finding about
    `EnumerationFormat` recurring verbatim here, on a code path it did not
    reach.
    """
    for name, value in coding_format_attrs(cf).items():
        cf_el.set(name, value)


def _write_level_chain(parent: etree._Element, levels: Sequence[Level]) -> None:
    """Emit ``<str:Level>`` as a recursive chain per v2.1 ``LevelType``.

    The model carries levels as an ordered flat list (``Hierarchy.levels``);
    the XSD encodes the same chain via nested ``<Level><Level>...``
    elements (one root, each holding the next). Empty list emits nothing.
    """
    if not levels:
        return
    head, *tail = levels
    el = etree.SubElement(parent, _s("Level"))
    el.set("id", head.id)
    _helpers.write_uri(el, head)
    _helpers.write_annotations(el, head.annotations)
    _helpers.write_istring(el, head.name)
    if head.description:
        _helpers.write_istring(el, head.description, "Description")
    if head.coding_format is not None:
        cf_el = etree.SubElement(el, _s("CodingFormat"))
        _write_coding_format(cf_el, head.coding_format)
    _write_level_chain(el, tail)


def _write_hierarchical_codelists(parent: etree._Element, msg: StructureMessage) -> None:
    """Emit ``<str:HierarchicalCodelist>``s, declared containers first.

    A `HierarchicalCodelist` in the message is written as itself, carrying its
    own id and name and nesting the hierarchies it references. Hierarchies
    belonging to no declared container get one synthesised around them, which
    is what every hierarchy used to get — and which silently replaced the
    container's name with the inner hierarchy's (#239).
    """
    by_urn = {str(artefact_urn(h)): h for h in msg.hierarchies}
    claimed: set[str] = set()

    for hcl in msg.hierarchical_codelists:
        hcl_el = etree.SubElement(parent, _s("HierarchicalCodelist"))
        _helpers.write_maintainable_attrs(hcl_el, hcl)
        _helpers.write_annotations(hcl_el, hcl.annotations)
        _helpers.write_istring(hcl_el, hcl.name)
        if hcl.description:
            _helpers.write_istring(hcl_el, hcl.description, "Description")
        # `IncludedCodelist*` precedes `Hierarchy*` in `HierarchicalCodelistType`.
        # Read since #239 and never written until #434. No `@alias`: the nodes
        # are written in the full `<Code>` form, so nothing refers to one.
        for ref in hcl.included_codelists:
            _helpers.write_ref(etree.SubElement(hcl_el, _s("IncludedCodelist")), ref)
        for ref in hcl.hierarchies:
            key = str(ref.urn)
            claimed.add(key)
            member = by_urn.get(key)
            if member is not None:
                _write_hierarchy_body(hcl_el, member, hcl.id)

    for urn, h in by_urn.items():
        if urn not in claimed:
            _write_hierarchy(parent, h)


def _write_hierarchy(parent: etree._Element, h: Hierarchy) -> None:
    """Emit a synthesised ``<str:HierarchicalCodelist>`` around one Hierarchy.

    Used only for a hierarchy with no declared container. The wrapper reuses
    the hierarchy's own id and name, which is lossy in principle and
    unavoidable: 2.1 has nowhere to put a bare hierarchy.

    The HierarchyType sequence is: HierarchicalCode (1+), then Level
    (0..1). The ``@leveled`` attribute mirrors ``Hierarchy.has_formal_levels``.
    """
    hcl_el = etree.SubElement(parent, _s("HierarchicalCodelist"))
    _helpers.write_maintainable_attrs(hcl_el, h)
    _helpers.write_annotations(hcl_el, h.annotations)
    _helpers.write_istring(hcl_el, h.name)
    if h.description:
        _helpers.write_istring(hcl_el, h.description, "Description")
    _write_hierarchy_body(hcl_el, h, h.id)


def _write_hierarchy_body(hcl_el: etree._Element, h: Hierarchy, hcl_id: str) -> None:
    """Emit the nested ``<str:Hierarchy>`` inside the open container `hcl_id`.

    With the id it has inside that container: ``H`` for ``H@HCL_AGE`` in
    ``HCL_AGE``, which a reader turns back into ``H@HCL_AGE`` (#421).
    """
    hier_el = etree.SubElement(hcl_el, _s("Hierarchy"))
    hier_el.set("id", inner_id(h.id, hcl_id) or h.id)
    _helpers.write_uri(hier_el, h)
    # On the inner element, not only on the container. 2.1 has nowhere to put a
    # bare hierarchy, so one is wrapped in a synthesised
    # `<HierarchicalCodelist>` — and the annotations went on the wrapper, while
    # a reader rebuilds the `Hierarchy` from *this* element. They arrived
    # nowhere (#355). The container keeps its own copy: when the wrapper is a
    # real `HierarchicalCodelist` rather than a synthetic one, those are a
    # different artefact's annotations.
    _helpers.write_annotations(hier_el, h.annotations)
    if h.has_formal_levels:
        hier_el.set("leveled", "true")
    _helpers.write_istring(hier_el, h.name)
    if h.description:
        _helpers.write_istring(hier_el, h.description, "Description")
    for hc in h.tree:
        _write_hierarchical_code(hier_el, hc)
    _write_level_chain(hier_el, list(h.levels))


# ── Entry point ───────────────────────────────────────────────────────────────


# Table-driven dispatch for the top-level write(): per container, the list
# of (StructureMessage attribute, per-item writer) pairs. The four organisation
# schemes share the OrganisationSchemes container; the rest are
# one writer per container.
# Order matches the SDMX-ML 2.1 schema's StructuresType sequence
# (SDMXStructure.xsd), which strictly enforces section ordering.
# The writer slot is `Callable[[etree._Element, Any], None]` rather than bare
# `Any`: the table is heterogeneous in its *item* type — each writer takes a
# different artefact — but arity and the parent-element parameter are fixed,
# and a bare `Any` would also accept a non-callable or the wrong arity.
def _write_metadataflow(parent: etree._Element, mdf: Metadataflow) -> None:
    """Emit a 2.1 ``<str:Metadataflow>``.

    ``MetadataflowType`` is a StructureUsage: Annotations? → Name →
    Description? → Structure?, where Structure references the MSD. Simpler
    than the 3.0 form, which also requires one or more ``<Target>`` elements —
    2.1 has no Target here at all, so a metadataflow's targets have no 2.1
    representation and are dropped. `validation` records that rather than
    letting it pass unremarked (#225).
    """
    el = etree.SubElement(parent, _s("Metadataflow"))
    _helpers.write_maintainable_attrs(el, mdf)
    _helpers.write_annotations(el, mdf.annotations)
    _helpers.write_istring(el, mdf.name)
    if mdf.description:
        _helpers.write_istring(el, mdf.description, "Description")
    if mdf.structure is not None:
        struct_el = etree.SubElement(el, _s("Structure"))
        _helpers.write_ref(struct_el, mdf.structure)


def _write_metadata_attribute(parent: etree._Element, attr: MetadataAttribute) -> None:
    """Emit a ``<str:MetadataAttribute>``, recursing into children."""
    el = etree.SubElement(parent, _s("MetadataAttribute"))
    el.set("id", attr.id)
    _helpers.write_uri(el, attr)
    if attr.min_occurs != 1:
        el.set("minOccurs", str(attr.min_occurs))
    if attr.max_occurs != 1:
        el.set("maxOccurs", "unbounded" if attr.max_occurs is None else str(attr.max_occurs))
    # `isPresentational` is declared on 2.1's `MetadataAttributeType` beside
    # minOccurs/maxOccurs. The reader had always read it; the writer never
    # emitted one, so it round-tripped to the default (#355).
    if attr.is_presentational:
        el.set("isPresentational", "true")
    _helpers.write_annotations(el, attr.annotations)
    if isinstance(attr.concept, Ref):
        _helpers.write_concept_identity(el, attr.concept)
    _helpers.write_representation(el, attr.representation, enumeration_format=attr.enumeration_format)
    for child in attr.children:
        _write_metadata_attribute(el, child)


def _write_target_object(parent: etree._Element, obj: TargetObject) -> None:
    """One target object of a ``<str:MetadataTarget>``, as the element its kind names (#422)."""
    kind_el = etree.SubElement(parent, _s(obj.kind))
    kind_el.set("id", obj.id)
    if obj.uri is not None:
        kind_el.set("uri", obj.uri)
    if obj.object_type is not None:
        kind_el.set("objectType", obj.object_type)
    _helpers.write_annotations(kind_el, obj.annotations)
    rep_el = etree.SubElement(kind_el, _s("LocalRepresentation"))
    if obj.representation is not None:
        _helpers.write_text_format(rep_el, obj.representation)


def _write_metadata_structure(parent: etree._Element, msd: MetadataStructure) -> None:
    """Emit a 2.1 ``<str:MetadataStructure>``.

    ``MetadataStructureComponents`` is required and needs both a
    ``<MetadataTarget>`` and a ``<ReportStructure>`` — the reporting layer
    SDMX 3.0 deleted. sdmxlib carries it on `MetadataStructure` precisely so
    this writer can reproduce it rather than inventing one, which is what made
    2.1 MSDs unwritable before #245.

    An MSD with no reporting layer — one built for 3.0, or read from it — is
    written with one invented and marked (`_synthetic_report_layer`), which a
    2.1 reader drops again (#421). It was refused until then (#245).
    """
    el = etree.SubElement(parent, _s("MetadataStructure"))
    _helpers.write_maintainable_attrs(el, msd)
    _helpers.write_annotations(el, msd.annotations)
    _helpers.write_istring(el, msd.name)
    if msd.description:
        _helpers.write_istring(el, msd.description, "Description")

    components = etree.SubElement(el, _s("MetadataStructureComponents"))
    targets, reports = (
        (msd.targets, msd.report_structures) if msd.targets or msd.report_structures else _synthetic_report_layer(msd)
    )
    for target in targets:
        mt_el = etree.SubElement(components, _s("MetadataTarget"))
        mt_el.set("id", target.id)
        _helpers.write_uri(mt_el, target)
        _helpers.write_annotations(mt_el, target.annotations)
        for obj in target.target_objects:
            _write_target_object(mt_el, obj)

    by_id = {a.id: a for a in msd.attributes}
    for report in reports:
        rs_el = etree.SubElement(components, _s("ReportStructure"))
        rs_el.set("id", report.id)
        _helpers.write_uri(rs_el, report)
        _helpers.write_annotations(rs_el, report.annotations)
        for attribute_id in report.attribute_ids:
            attribute = by_id.get(attribute_id)
            if attribute is not None:
                _write_metadata_attribute(rs_el, attribute)
        for target_id in report.target_ids:
            mt_el = etree.SubElement(rs_el, _s("MetadataTarget"))
            etree.SubElement(mt_el, "Ref").set("id", target_id)


def _synthetic_report_layer(msd: MetadataStructure) -> tuple[tuple[MetadataTarget], tuple[ReportStructure]]:
    """The 2.1 reporting layer for an MSD that has none: any object, every attribute (#421).

    Marked, so the reader drops it and a 3.x MSD comes back as itself.
    """
    marked = Annotations([Annotation(type=SYNTHETIC_REPORT_LAYER_TYPE)])
    object_id, object_type = SYNTHETIC_TARGET_OBJECT
    target = MetadataTarget(
        id=SYNTHETIC_TARGET_ID,
        target_objects=(
            TargetObject(
                id=object_id,
                kind="IdentifiableObjectTarget",
                object_type=object_type,
                # `IdentifiableObjectTextFormatType` fixes the text type.
                representation=Facet(type="IdentifiableReference"),
            ),
        ),
        annotations=marked,
    )
    report = ReportStructure(
        id=SYNTHETIC_REPORT_ID,
        attribute_ids=tuple(attribute.id for attribute in msd.attributes),
        target_ids=(SYNTHETIC_TARGET_ID,),
        annotations=marked,
    )
    return (target,), (report,)


_SECTIONS: list[tuple[str, list[SectionEntry]]] = [
    (
        "OrganisationSchemes",
        [
            section_entry(lambda m: m.agency_schemes, _write_agency_scheme),
            section_entry(lambda m: m.data_provider_schemes, _write_data_provider_scheme),
            section_entry(lambda m: m.data_consumer_schemes, _write_data_consumer_scheme),
            section_entry(lambda m: m.organisation_unit_schemes, _write_organisation_unit_scheme),
        ],
    ),
    ("Dataflows", [section_entry(lambda m: m.dataflows, _helpers.write_dataflow)]),
    ("Metadataflows", [section_entry(lambda m: m.metadataflows, _write_metadataflow)]),
    ("CategorySchemes", [section_entry(lambda m: m.category_schemes, _helpers.write_category_scheme)]),
    ("Categorisations", [section_entry(lambda m: m.categorisations, _helpers.write_categorisation)]),
    ("Codelists", [section_entry(lambda m: m.codelists, _helpers.write_codelist)]),
    (
        "HierarchicalCodelists",
        [
            section_entry(
                lambda m: [m] if (m.hierarchies or m.hierarchical_codelists) else [], _write_hierarchical_codelists
            )
        ],
    ),
    ("Concepts", [section_entry(lambda m: m.concept_schemes, _helpers.write_concept_scheme)]),
    # After `Concepts` and before `DataStructures`, which is where 2.1's
    # `StructuresType` sequences it. It used to sit third, right after
    # `Metadataflows`, so any message carrying an MSD *and* a codelist, concept
    # scheme, category scheme or hierarchy emitted sections out of order and
    # was rejected by the XSD. Nothing caught it because no fixture combined
    # the two — found against a real IMF metadata structure, which arrives with
    # its concept scheme attached (#356).
    ("MetadataStructures", [section_entry(lambda m: m.metadata_structures, _write_metadata_structure)]),
    ("DataStructures", [section_entry(lambda m: m.data_structures, _write_data_structure)]),
    (
        "Constraints",
        [
            section_entry(lambda m: m.constraints, _write_data_constraint),
            section_entry(lambda m: m.attachment_constraints, _write_attachment_constraint),
        ],
    ),
    (
        "ProvisionAgreements",
        [
            section_entry(lambda m: m.provision_agreements, _helpers.write_provision_agreement),
            section_entry(lambda m: m.metadata_provision_agreements, _write_metadata_provision_agreement),
        ],
    ),
]


def write(msg: StructureMessage, *, pretty_print: bool = False) -> bytes:
    """Serialise a StructureMessage to SDMX-ML 2.1 XML bytes.

    Example:
        from sdmxlib.formats.sdmx_ml21.writer import write

        xml_bytes = write(msg)
        xml_bytes = write(msg, pretty_print=True)  # indented output

    A draft (`1.0.0-draft`) has no 2.1 form and is left out, with whatever in
    the message references it; `sdmxlib.dropped(msg, "2.1")` names each (#424).
    """
    return write_kept(without_dropped(msg, "2.1"), pretty_print=pretty_print)


def write_kept(msg: StructureMessage, *, pretty_print: bool = False) -> bytes:
    """`write` for a message `without_dropped` has already narrowed to SDMX-ML 2.1.

    `serialize` leaves out what 2.1 cannot hold before it validates, then
    calls this: through `write`, the same walk ran twice, and it was half the
    cost of a 2.1 DSD write (#464).
    """
    # 2.1 has no codelist extension: an extending codelist is written with the
    # codes it inherits (#421). `validate` refuses one whose extended codelists
    # cannot be found, before any of this runs.
    codelists = [_plain(codelist).apply_extensions(msg.codelists) for codelist in msg.codelists]
    msg = evolve(msg, codelists=ArtefactList(codelists))
    # 2.1 has no metadata provider: an agency's are written in its data
    # provider scheme, marked for the reader to take back out (#421).
    msg = with_providers_merged(msg)
    with hierarchy_containers(_hcl_of(msg)):
        return _write_document(msg, pretty_print=pretty_print)


def _plain(codelist: Codelist) -> Codelist:
    """A geographic codelist that extends others as the plain codelist 2.1 writes it as.

    2.1 has no geographic codelist and writes one as a plain `Codelist`
    anyway, which `formats.gaps` declares. Converting first is what lets it
    inherit codes that are not geographic, as 3.x allows: a
    `GeographicCodelist` itself holds only feature-set codes (#421).
    """
    if type(codelist) is Codelist or not codelist.extensions:
        return codelist
    return Codelist(**{field.alias: getattr(codelist, field.name) for field in attrs.fields(Codelist) if field.init})


def _hcl_of(msg: StructureMessage) -> dict[str, str]:
    """Hierarchy URN → the declared HierarchicalCodelist that claims it.

    What `_write_hierarchical_codelists` nests each hierarchy in, so a node
    reference names the container the document actually has (#439).
    """
    return {str(ref.urn): hcl.id for hcl in msg.hierarchical_codelists for ref in hcl.hierarchies}


def _write_document(msg: StructureMessage, *, pretty_print: bool) -> bytes:
    root = etree.Element(_m("Structure"), nsmap=_NSMAP)
    root.set(
        "{http://www.w3.org/2001/XMLSchema-instance}schemaLocation",
        f"{MES} SDMXMessage.xsd",
    )

    write_header(root, msg.header, com=COM, mes=MES)

    structures = etree.SubElement(root, _m("Structures"))

    for container_tag, entries in _SECTIONS:
        if not any(entry.get(msg) for entry in entries):
            continue
        container = etree.SubElement(structures, _s(container_tag))
        for entry in entries:
            for item in entry.get(msg):
                entry.write(container, item)
        # Per SDMX-ML 2.1 StructuresType sequence, StructureSets fits between
        # DataStructures and Constraints. Emit the synthetic wrapper here
        # rather than fold it into _SECTIONS because it draws from four
        # separate message attributes.
        if container_tag == "DataStructures" and any(
            (
                msg.representation_maps,
                msg.structure_maps,
                msg.concept_scheme_maps,
                msg.category_scheme_maps,
                msg.organisation_scheme_maps,
                msg.structure_sets,
            )
        ):
            ss_container = etree.SubElement(structures, _s("StructureSets"))
            _write_structure_sets(ss_container, msg)

    # If the message has no DataStructures but does have maps, we still need
    # to emit the StructureSets container at the right place. Cover that case
    # by tucking it in before Constraints.
    if structures.find(_s("StructureSets")) is None and any(
        (
            msg.representation_maps,
            msg.structure_maps,
            msg.concept_scheme_maps,
            msg.category_scheme_maps,
            msg.organisation_scheme_maps,
            msg.structure_sets,
        )
    ):
        # Insert before Constraints (if present) else append.
        ss_container = etree.Element(_s("StructureSets"))
        _write_structure_sets(ss_container, msg)
        anchor = structures.find(_s("Constraints"))
        if anchor is not None:
            anchor.addprevious(ss_container)
        else:
            structures.append(ss_container)

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=pretty_print)
