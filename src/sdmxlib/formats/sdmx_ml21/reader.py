"""SDMX-ML 2.1 reader: lxml → domain model.

Collapses thirteen XML wrapper layers into clean domain objects.
All references within the message are auto-resolved.
"""

from collections.abc import Callable
from datetime import datetime

from attrs import frozen
from lxml import etree
from typing_extensions import TypedDict

from sdmxlib.formats._attachment import match_dimension_case
from sdmxlib.formats._coerce import to_int
from sdmxlib.formats._refuse import build_each_xml, refuse_xml
from sdmxlib.formats.sdmx_ml._closed_world import required
from sdmxlib.formats.sdmx_ml._common import (
    METADATA_CONSTRAINT,
    ReaderHelpers,
    ReferenceAttrs,
    ReferenceFields,
    component_id,
    flag,
    node_level,
    optional_flag,
)
from sdmxlib.formats.sdmx_ml._header import read_header
from sdmxlib.formats.sdmx_ml._losses import account
from sdmxlib.model._container import member_id
from sdmxlib.model._resolvers import document_binding
from sdmxlib.model.annotations import Annotation, Annotations
from sdmxlib.model.category import CategoryScheme
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.collections import ArtefactList, ItemList
from sdmxlib.model.concept import Concept, ConceptScheme
from sdmxlib.model.constraint import (
    AttachmentConstraint,
)
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import (
    REPORTING_YEAR_START_DAY,
    AttachmentLevel,
    Attribute,
    DataStructure,
    Dimension,
    Group,
    Measure,
    MeasureDimension,
    TimeDimension,
)
from sdmxlib.model.errors import Refusal
from sdmxlib.model.hierarchy import HierarchicalCode, HierarchicalCodelist, Hierarchy, Level
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.mapping import (
    BUILT_VALUE_MAP_TYPE,
    CategorySchemeMap,
    ComponentMap,
    ConceptSchemeMap,
    ItemMap,
    MapDataType,
    OrganisationSchemeMap,
    RepresentationMap,
    RepresentationMapping,
    StructureMap,
    StructureSet,
    is_built_value_map,
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
    MetadataProvider,
    OrganisationUnit,
    OrganisationUnitScheme,
)
from sdmxlib.model.ref import Ref, artefact_urn
from sdmxlib.model.registry import SupportsAddAll
from sdmxlib.model.representation import Facet
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.model.validation import SdmxConformanceError

from ._providers import metadata_provider_urn, split_provider_schemes
from ._synthetic import SYNTHETIC_REPORT_LAYER_TYPE, SYNTHETIC_STRUCTURE_SET_TYPE
from .namespaces import COM, MES, STR

# ── Shared reader helpers ─────────────────────────────────────────────────────
# Pulled from sdmxlib.formats.sdmx_ml._common and used as `_helpers.x` so each
# call site says where the helper comes from. Only the namespace tag builders
# are still bound to short names, because they appear inline in every lookup;
# they go away with the prefixed-path conversion (#209).
_helpers = ReaderHelpers(com=COM, str_=STR, has_is_final=True, has_links=False)
#: Prefix map for ``find``/``findall``. lxml resolves prefixes per call — there
#: is no way to bind it to a tree — so every lookup passes ``namespaces=_NS``.
_NS = {"com": COM, "str": STR, "mes": MES}


# ── OrganisationSchemes ───────────────────────────────────────────────────────


def _read_concept_role(component_el: etree._Element) -> tuple[str, ...]:
    """Read ``<str:ConceptRole>`` children → tuple of role ids.

    Ids come from the Ref ``id=`` attribute, or the item id of a URN-text
    payload.
    """
    roles: list[str] = []
    for role_el in component_el.findall("str:ConceptRole", namespaces=_NS):
        ref_el = role_el.find("Ref")
        if ref_el is not None:
            roles.append(required(ref_el, "id"))
            continue
        text = _helpers.urn_text(role_el)
        if text.startswith("urn:"):
            urn = SdmxUrn.parse(text)
            if urn.item_id is not None:
                roles.append(urn.item_id)
    return tuple(roles)


def _read_contact(c_el: etree._Element) -> Contact:
    """Read a v2.1 ``<str:Contact>``.

    Name is common-namespaced; Department / Role are structure-namespaced
    with ``xml:lang``.
    """
    return Contact(
        id=c_el.get("id"),
        name=_helpers.read_istring(c_el),
        department=_helpers.read_istring(c_el, "Department", prefix="str"),
        role=_helpers.read_istring(c_el, "Role", prefix="str"),
        phones=tuple(el.text for el in c_el.findall("str:Telephone", namespaces=_NS) if el.text),
        faxes=tuple(el.text for el in c_el.findall("str:Fax", namespaces=_NS) if el.text),
        x400s=tuple(el.text for el in c_el.findall("str:X400", namespaces=_NS) if el.text),
        uris=tuple(el.text for el in c_el.findall("str:URI", namespaces=_NS) if el.text),
        emails=tuple(el.text for el in c_el.findall("str:Email", namespaces=_NS) if el.text),
    )


class _OrganisationFields(TypedDict, closed=True):
    """What every 2.1 organisation item carries: an `Organisation` in the IM, whichever scheme holds it."""

    id: str
    uri: str | None
    name: InternationalString
    description: InternationalString
    contacts: tuple[Contact, ...]
    annotations: Annotations


class _SchemeFields(ReferenceFields, closed=True):
    """What every 2.1 organisation scheme carries besides its items."""

    id: str
    uri: str | None
    maintainer: str
    version: str
    name: InternationalString
    description: InternationalString
    annotations: Annotations
    valid_from: datetime | None
    valid_to: datetime | None
    is_partial: bool


def _organisation_fields(el: etree._Element) -> _OrganisationFields:
    """`_OrganisationFields` read off one item element."""
    return {
        "id": required(el, "id"),
        "uri": el.get("uri"),
        "name": _helpers.read_istring(el),
        "description": _helpers.read_istring(el, "Description"),
        "contacts": tuple(_read_contact(c) for c in el.findall("str:Contact", namespaces=_NS)),
        "annotations": _helpers.read_annotations(el),
    }


def _scheme_fields(el: etree._Element) -> _SchemeFields:
    """`_SchemeFields` read off one scheme element."""
    return {
        "id": required(el, "id"),
        "uri": el.get("uri"),
        "maintainer": required(el, "agencyID"),
        "version": el.get("version", "1.0"),
        "name": _helpers.read_istring(el),
        "description": _helpers.read_istring(el, "Description"),
        "annotations": _helpers.read_annotations(el),
        "valid_from": _helpers.read_datetime_attr(el, "validFrom"),
        "valid_to": _helpers.read_datetime_attr(el, "validTo"),
        **_helpers.read_reference_attrs(el),
        "is_partial": _helpers.read_is_partial(el),
    }


@frozen
class _OrganisationSchemes:
    agency: list[AgencyScheme]
    data_provider: list[DataProviderScheme]
    data_consumer: list[DataConsumerScheme]
    organisation_unit: list[OrganisationUnitScheme]


def _read_organisation_schemes(structures: etree._Element, refused: list[Refusal]) -> _OrganisationSchemes:
    container = structures.find("str:OrganisationSchemes", namespaces=_NS)
    if container is None:
        return _OrganisationSchemes([], [], [], [])

    def items(el: etree._Element, tag: str) -> list[etree._Element]:
        return el.findall(f"str:{tag}", namespaces=_NS)

    def agency_scheme(el: etree._Element) -> AgencyScheme:
        agencies = [Agency(**_organisation_fields(a)) for a in items(el, "Agency")]
        return AgencyScheme(**_scheme_fields(el), agencies=ItemList(agencies))

    def provider_scheme(el: etree._Element) -> DataProviderScheme:
        providers = [DataProvider(**_organisation_fields(p)) for p in items(el, "DataProvider")]
        return DataProviderScheme(**_scheme_fields(el), providers=ItemList(providers))

    def consumer_scheme(el: etree._Element) -> DataConsumerScheme:
        consumers = [DataConsumer(**_organisation_fields(c)) for c in items(el, "DataConsumer")]
        return DataConsumerScheme(**_scheme_fields(el), consumers=ItemList(consumers))

    def unit_scheme(el: etree._Element) -> OrganisationUnitScheme:
        units = [
            OrganisationUnit(**_organisation_fields(u), parent_id=_helpers.read_parent_id(u))
            for u in items(el, "OrganisationUnit")
        ]
        return OrganisationUnitScheme(**_scheme_fields(el), organisation_units=ItemList(units))

    return _OrganisationSchemes(
        agency=build_each_xml(items(container, "AgencyScheme"), agency_scheme, AgencyScheme, refused),
        data_provider=build_each_xml(
            items(container, "DataProviderScheme"), provider_scheme, DataProviderScheme, refused
        ),
        data_consumer=build_each_xml(
            items(container, "DataConsumerScheme"), consumer_scheme, DataConsumerScheme, refused
        ),
        organisation_unit=build_each_xml(
            items(container, "OrganisationUnitScheme"), unit_scheme, OrganisationUnitScheme, refused
        ),
    )


# ── Codelists ─────────────────────────────────────────────────────────────────


def _read_dimension(dim_el: etree._Element, index: dict[str, object]) -> Dimension:
    concept_ref: Ref[Concept] | None = None
    ci_el = dim_el.find("str:ConceptIdentity", namespaces=_NS)
    if ci_el is not None:
        concept_ref = _helpers.resolve_ref(ci_el, Concept)

    representation = None
    enumeration_format = None
    local_rep_el = dim_el.find("str:LocalRepresentation", namespaces=_NS)
    if local_rep_el is not None:
        representation = _helpers.read_representation(local_rep_el, index, enum_cls=Codelist)
        enumeration_format = _helpers.read_enumeration_format(local_rep_el)

    return Dimension(
        id=component_id(dim_el, concept_ref),
        uri=dim_el.get("uri"),
        concept=concept_ref,
        representation=representation,
        enumeration_format=enumeration_format,
        annotations=_helpers.read_annotations(dim_el),
        concept_role=_read_concept_role(dim_el),
    )


def _read_measure_dimension(md_el: etree._Element, index: dict[str, object]) -> MeasureDimension:
    concept_ref: Ref[Concept] | None = None
    ci_el = md_el.find("str:ConceptIdentity", namespaces=_NS)
    if ci_el is not None:
        concept_ref = _helpers.resolve_ref(ci_el, Concept)

    representation = None
    local_rep_el = md_el.find("str:LocalRepresentation", namespaces=_NS)
    if local_rep_el is not None:
        # Cross-sectional measure dimensions are enumerated by a ConceptScheme,
        # not a Codelist (XSD: ConceptSchemeReferenceType). Every other
        # component takes the Codelist default.
        representation = _helpers.read_representation(local_rep_el, index, enum_cls=ConceptScheme)

    return MeasureDimension(
        id=component_id(md_el, concept_ref),
        uri=md_el.get("uri"),
        concept=concept_ref,
        representation=representation,
        annotations=_helpers.read_annotations(md_el),
        concept_role=_read_concept_role(md_el),
    )


def _read_time_dimension(td_el: etree._Element) -> TimeDimension:
    concept_ref: Ref[Concept] | None = None
    ci_el = td_el.find("str:ConceptIdentity", namespaces=_NS)
    if ci_el is not None:
        concept_ref = _helpers.resolve_ref(ci_el, Concept)

    facet_rep: Facet | None = None
    local_rep_el = td_el.find("str:LocalRepresentation", namespaces=_NS)
    if local_rep_el is not None:
        tf_el = local_rep_el.find("str:TextFormat", namespaces=_NS)
        if tf_el is not None:
            facet_rep = _helpers.read_text_format(tf_el)

    return TimeDimension(
        id=td_el.get("id", "TIME_PERIOD"),
        uri=td_el.get("uri"),
        concept=concept_ref,
        representation=facet_rep,
        annotations=_helpers.read_annotations(td_el),
    )


def _read_attachment(
    ar_el: etree._Element,
) -> tuple[AttachmentLevel, list[str] | None, str | None]:
    """Parse AttributeRelationship → (level, related_dimensions, related_group).

    The ``<PrimaryMeasure>`` branch yields no measure id (#360). 2.1 does put a
    ``<Ref id=…/>`` there, but 2.1 also pins ``PrimaryMeasure/@id`` to
    ``OBS_VALUE`` and permits one measure, so the id is a constant restating
    what `AttachmentLevel.OBSERVATION` already says.
    """
    pm_el = ar_el.find("str:PrimaryMeasure", namespaces=_NS)
    if pm_el is not None:
        return AttachmentLevel.OBSERVATION, None, None

    dim_refs = ar_el.findall("str:Dimension", namespaces=_NS)
    if dim_refs:
        related = [required(ref_el, "id") for d in dim_refs if (ref_el := d.find("Ref")) is not None]
        return AttachmentLevel.SERIES, related or None, None

    group_el = ar_el.find("str:Group", namespaces=_NS)
    if group_el is not None:
        # 2.1: <Group><Ref id="GROUP_ID"/></Group>
        ref_el = group_el.find("Ref")
        group_id = required(ref_el, "id") if ref_el is not None else None
        return AttachmentLevel.GROUP, None, group_id

    return AttachmentLevel.DATASET, None, None


def _read_attribute(attr_el: etree._Element, index: dict[str, object]) -> Attribute:
    concept_ref: Ref[Concept] | None = None
    ci_el = attr_el.find("str:ConceptIdentity", namespaces=_NS)
    if ci_el is not None:
        concept_ref = _helpers.resolve_ref(ci_el, Concept)

    representation = None
    enumeration_format = None
    local_rep_el = attr_el.find("str:LocalRepresentation", namespaces=_NS)
    if local_rep_el is not None:
        representation = _helpers.read_representation(local_rep_el, index, enum_cls=Codelist)
        enumeration_format = _helpers.read_enumeration_format(local_rep_el)

    # Required by the schema; one a document omits varies with nothing, and
    # is read as reported once per data set, as the 3.x readers read FMR's (#421).
    attachment: AttachmentLevel = AttachmentLevel.DATASET
    related_dims: list[str] | None = None
    related_group: str | None = None
    attachment_groups: tuple[str, ...] = ()
    ar_el = attr_el.find("str:AttributeRelationship", namespaces=_NS)
    if ar_el is not None:
        attachment, related_dims, related_group = _read_attachment(ar_el)
        # Beside the related dimensions, the groups the value may also be
        # reported with (#422).
        attachment_groups = tuple(
            required(ref_el, "id")
            for g in ar_el.findall("str:AttachmentGroup", namespaces=_NS)
            if (ref_el := g.find("Ref")) is not None
        )

    # `ReportingYearStartDay`'s id is fixed (#422); any other attribute's is
    # its concept's when omitted (#477).
    if etree.QName(attr_el).localname == "ReportingYearStartDay":
        attribute_id = attr_el.get("id", REPORTING_YEAR_START_DAY)
    else:
        attribute_id = component_id(attr_el, concept_ref)
    return Attribute(
        id=attribute_id,
        uri=attr_el.get("uri"),
        usage=attr_el.get("assignmentStatus"),
        attachment=attachment,
        related_dimensions=related_dims,
        related_group=related_group,
        attachment_groups=attachment_groups,
        concept=concept_ref,
        representation=representation,
        enumeration_format=enumeration_format,
        annotations=_helpers.read_annotations(attr_el),
        concept_role=_read_concept_role(attr_el),
    )


def _read_measure(pm_el: etree._Element, index: dict[str, object]) -> Measure:
    concept_ref: Ref[Concept] | None = None
    ci_el = pm_el.find("str:ConceptIdentity", namespaces=_NS)
    if ci_el is not None:
        concept_ref = _helpers.resolve_ref(ci_el, Concept)

    representation = None
    enumeration_format = None
    local_rep_el = pm_el.find("str:LocalRepresentation", namespaces=_NS)
    if local_rep_el is not None:
        representation = _helpers.read_representation(local_rep_el, index, enum_cls=Codelist)
        enumeration_format = _helpers.read_enumeration_format(local_rep_el)

    return Measure(
        id=pm_el.get("id", "OBS_VALUE"),
        uri=pm_el.get("uri"),
        concept=concept_ref,
        representation=representation,
        enumeration_format=enumeration_format,
        annotations=_helpers.read_annotations(pm_el),
    )


def _read_groups(components_el: etree._Element) -> list[Group]:
    result: list[Group] = []
    for group_el in components_el.findall("str:Group", namespaces=_NS):
        # 2.1's alternative to the dimensions: an attachment constraint (#422).
        constraint_el = group_el.find("str:AttachmentConstraint", namespaces=_NS)
        dim_ids: list[str] = []
        for gd_el in group_el.findall("str:GroupDimension", namespaces=_NS):
            dr_el = gd_el.find("str:DimensionReference", namespaces=_NS)
            if dr_el is not None:
                ref_el = dr_el.find("Ref")
                if ref_el is not None:
                    dim_ids.append(required(ref_el, "id"))
        result.append(
            Group(
                id=required(group_el, "id"),
                uri=group_el.get("uri"),
                dimension_ids=dim_ids,
                attachment_constraint=(
                    _helpers.resolve_ref(constraint_el, object) if constraint_el is not None else None
                ),
                annotations=_helpers.read_annotations(group_el),
            )
        )
    return result


def _read_dsd_components(
    components_el: etree._Element, index: dict[str, object]
) -> tuple[list[Dimension], TimeDimension | None, MeasureDimension | None, list[Attribute], list[Measure], list[Group]]:
    """Parse DataStructureComponents into component lists."""
    dimensions: list[Dimension] = []
    time_dimension: TimeDimension | None = None
    measure_dimension: MeasureDimension | None = None
    attributes: list[Attribute] = []
    measures: list[Measure] = []

    dim_list_el = components_el.find("str:DimensionList", namespaces=_NS)
    if dim_list_el is not None:
        dimensions.extend(_read_dimension(d, index) for d in dim_list_el.findall("str:Dimension", namespaces=_NS))
        td_el = dim_list_el.find("str:TimeDimension", namespaces=_NS)
        if td_el is not None:
            time_dimension = _read_time_dimension(td_el)
        md_el = dim_list_el.find("str:MeasureDimension", namespaces=_NS)
        if md_el is not None:
            measure_dimension = _read_measure_dimension(md_el, index)

    attr_list_el = components_el.find("str:AttributeList", namespaces=_NS)
    if attr_list_el is not None:
        # `ReportingYearStartDay` is an attribute whose id 2.1 fixes; 3.x has
        # no element for it, and holds it as an attribute by that id (#422).
        attributes.extend(
            _read_attribute(a, index)
            for a in attr_list_el
            if isinstance(a.tag, str) and etree.QName(a).localname in {"Attribute", "ReportingYearStartDay"}
        )

    measure_list_el = components_el.find("str:MeasureList", namespaces=_NS)
    if measure_list_el is not None:
        pm_el = measure_list_el.find("str:PrimaryMeasure", namespaces=_NS)
        if pm_el is not None:
            measures.append(_read_measure(pm_el, index))

    groups = _read_groups(components_el)
    dimension_ids = [d.id for d in (*dimensions, time_dimension, measure_dimension) if d is not None]
    attributes = match_dimension_case(attributes, dimension_ids)
    return dimensions, time_dimension, measure_dimension, attributes, measures, groups


def _read_data_structures(
    structures: etree._Element,
    index: dict[str, object],
    agencies: dict[str, Agency],
    refused: list[Refusal],
) -> list[DataStructure]:
    container = structures.find("str:DataStructures", namespaces=_NS)
    if container is None:
        return []

    def one(dsd_el: etree._Element) -> DataStructure:
        dimensions: list[Dimension] = []
        time_dimension: TimeDimension | None = None
        measure_dimension: MeasureDimension | None = None
        attributes: list[Attribute] = []
        measures: list[Measure] = []
        groups: list[Group] = []

        components_el = dsd_el.find("str:DataStructureComponents", namespaces=_NS)
        if components_el is not None:
            dimensions, time_dimension, measure_dimension, attributes, measures, groups = _read_dsd_components(
                components_el, index
            )

        return DataStructure(
            id=required(dsd_el, "id"),
            uri=dsd_el.get("uri"),
            maintainer=_helpers.resolve_agency(required(dsd_el, "agencyID"), agencies),
            version=dsd_el.get("version", "1.0"),
            name=_helpers.read_istring(dsd_el),
            description=_helpers.read_istring(dsd_el, "Description"),
            legacy_is_final=_helpers.read_legacy_is_final(dsd_el),
            dimensions=ItemList(dimensions),
            time_dimension=time_dimension,
            measure_dimension=measure_dimension,
            attributes=ItemList(attributes),
            measures=ItemList(measures),
            groups=ItemList(groups) if groups else None,
            dimension_descriptor_annotations=_helpers.read_child_annotations(components_el, "DimensionList"),
            attribute_descriptor_annotations=_helpers.read_child_annotations(components_el, "AttributeList"),
            measure_descriptor_annotations=_helpers.read_child_annotations(components_el, "MeasureList"),
            dimension_descriptor_uri=_helpers.read_child_uri(components_el, "DimensionList"),
            attribute_descriptor_uri=_helpers.read_child_uri(components_el, "AttributeList"),
            measure_descriptor_uri=_helpers.read_child_uri(components_el, "MeasureList"),
            annotations=_helpers.read_annotations(dsd_el),
            valid_from=_helpers.read_datetime_attr(dsd_el, "validFrom"),
            valid_to=_helpers.read_datetime_attr(dsd_el, "validTo"),
            **_helpers.read_reference_attrs(dsd_el),
        )

    return build_each_xml(container.findall("str:DataStructure", namespaces=_NS), one, DataStructure, refused)


# ── Dataflows & Metadataflows ─────────────────────────────────────────────────


def _read_metadataflows(
    structures: etree._Element,
    agencies: dict[str, Agency],
    refused: list[Refusal],
) -> list[Metadataflow]:
    """Parse ``<str:Metadataflows>``.

    2.1's MetadataflowType is a plain StructureUsage — Annotations?, Name,
    Description?, Structure? — with no ``<Target>``, which 3.0 added and made
    mandatory. So a metadataflow read from 2.1 has empty ``targets``, and one
    written to 2.1 loses them; `validation` states that rather than leaving it
    to be discovered (#225).
    """
    container = structures.find("str:Metadataflows", namespaces=_NS)
    if container is None:
        return []

    def one(el: etree._Element) -> Metadataflow:
        struct_el = el.find("str:Structure", namespaces=_NS)
        return Metadataflow(
            id=required(el, "id"),
            uri=el.get("uri"),
            maintainer=_helpers.resolve_agency(required(el, "agencyID"), agencies),
            version=el.get("version", "1.0"),
            legacy_is_final=_helpers.read_legacy_is_final(el),
            name=_helpers.read_istring(el),
            description=_helpers.read_istring(el, "Description"),
            annotations=_helpers.read_annotations(el),
            structure=(_helpers.resolve_ref(struct_el, MetadataStructure) if struct_el is not None else None),
            valid_from=_helpers.read_datetime_attr(el, "validFrom"),
            valid_to=_helpers.read_datetime_attr(el, "validTo"),
            **_helpers.read_reference_attrs(el),
        )

    return build_each_xml(container.findall("str:Metadataflow", namespaces=_NS), one, Metadataflow, refused)


# ── CategorySchemes & Categorisations ─────────────────────────────────────────


# ── Constraints ───────────────────────────────────────────────────────────────


def _read_attachment_constraints(
    structures: etree._Element,
    agencies: dict[str, Agency],
    refused: list[Refusal],
) -> list[AttachmentConstraint]:
    """Parse 2.1-only ``<str:AttachmentConstraint>`` siblings inside ``<str:Constraints>``."""
    container = structures.find("str:Constraints", namespaces=_NS)
    if container is None:
        return []

    def one(ac_el: etree._Element) -> AttachmentConstraint:
        attach = _helpers.read_constraint_attachment(ac_el.find("str:ConstraintAttachment", namespaces=_NS))
        return AttachmentConstraint(
            id=required(ac_el, "id"),
            uri=ac_el.get("uri"),
            maintainer=_helpers.resolve_agency(required(ac_el, "agencyID"), agencies),
            version=ac_el.get("version", "1.0"),
            name=_helpers.read_istring(ac_el),
            description=_helpers.read_istring(ac_el, "Description"),
            legacy_is_final=_helpers.read_legacy_is_final(ac_el),
            attachment=attach,
            # The same `DataKeySetType` as a ContentConstraint's, so the same
            # reader: a copy of its own read no annotations, validity or
            # components, and no `isIncluded` (#479).
            data_key_sets=_helpers.read_data_key_sets(ac_el),
            annotations=_helpers.read_annotations(ac_el),
            valid_from=_helpers.read_datetime_attr(ac_el, "validFrom"),
            valid_to=_helpers.read_datetime_attr(ac_el, "validTo"),
            **_helpers.read_reference_attrs(ac_el),
        )

    ac_els: list[etree._Element] = []
    for ac_el in container.findall("str:AttachmentConstraint", namespaces=_NS):
        if _helpers.constrains_metadata(ac_el):
            refuse_xml(ac_el, AttachmentConstraint, METADATA_CONSTRAINT, refused)
        else:
            ac_els.append(ac_el)
    return build_each_xml(ac_els, one, AttachmentConstraint, refused)


def _read_source_target_refs(el: etree._Element) -> tuple[Ref[object] | None, Ref[object] | None]:
    """Read Source and Target ref children from a map element."""
    source_ref = None
    src_el = el.find("str:Source", namespaces=_NS)
    if src_el is not None:
        source_ref = _helpers.resolve_ref(src_el, object)
    target_ref = None
    tgt_el = el.find("str:Target", namespaces=_NS)
    if tgt_el is not None:
        target_ref = _helpers.resolve_ref(tgt_el, object)
    return source_ref, target_ref


def _read_id_pair_maps[T](parent: etree._Element, tag: str, cls: Callable[..., T]) -> list[T]:
    """Read item maps that have Source/Target children with Ref id attributes."""
    results: list[T] = []
    for m_el in parent.findall(f"str:{tag}", namespaces=_NS):
        m_src = m_el.find("str:Source", namespaces=_NS)
        m_tgt = m_el.find("str:Target", namespaces=_NS)
        if m_src is not None and m_tgt is not None:
            src_ref_el = m_src.find("Ref")
            tgt_ref_el = m_tgt.find("Ref")
            if src_ref_el is not None and tgt_ref_el is not None:
                results.append(
                    cls(
                        source_id=required(src_ref_el, "id"),
                        target_id=required(tgt_ref_el, "id"),
                        annotations=_helpers.read_annotations(m_el),
                    )
                )
    return results


@frozen
class _SetMembership:
    """What every map read from one ``<str:StructureSet>`` takes from it (#421).

    Attributes:
        agency: The set's agency, each map's.
        version: The set's version, each map's.
        own: A map's id from its id inside the set: `member_id`'s, or the bare
            id inside a wrapper this library invented.
        valid_from: The set's validity, each map's since 3.0 made it so.
        valid_to: As `valid_from`.
        reference_attrs: The set's external-reference attributes, likewise.
    """

    agency: Agency
    version: str
    own: Callable[[str], str]
    valid_from: datetime | None
    valid_to: datetime | None
    reference_attrs: ReferenceAttrs

    def maintainable(self) -> _MapMaintainable:
        """The keywords every map built from the set shares."""
        return _MapMaintainable(
            maintainer=self.agency,
            version=self.version,
            valid_from=self.valid_from,
            valid_to=self.valid_to,
            **self.reference_attrs,
        )


class _MapMaintainable(ReferenceFields, closed=True):
    """`_SetMembership.maintainable`: the maintainable keywords a map takes from its set."""

    maintainer: Agency
    version: str
    valid_from: datetime | None
    valid_to: datetime | None


def _read_component_map(
    cm_el: etree._Element,
    n: int,
    sm_el: etree._Element,
    held: _SetMembership,
    built: list[RepresentationMap],
) -> ComponentMap | None:
    """One 2.1 ``<str:ComponentMap>``, its ``<RepresentationMapping>`` included (#422).

    The mapping is one of three. A ``<CodelistMap>`` names a codelist map of
    the same structure set, which the reader already reads as a
    `RepresentationMap`, so it is that map's URN. A ``<ValueMap>``'s pairs
    have no 3.x form but a maintainable map, so one is built for them —
    data type to data type, `BUILT_VALUE_MAP_TYPE` on it, the structure map's
    name — appended to `built`, and referenced. ``<ToTextFormat>`` and
    ``<ToValueType>`` have no 3.x form at all and are held as themselves.

    None when the source or target names no component, as before.
    """
    pair = _read_id_pair_maps_of(cm_el)
    if pair is None:
        return None
    source_id, target_id = pair
    representation_map: Ref[object] | None = None
    to_text_format: Facet | None = None
    to_value_type: str | None = None
    rm_el = cm_el.find("str:RepresentationMapping", namespaces=_NS)
    if rm_el is not None:
        clm_ref = rm_el.find("str:CodelistMap/Ref", namespaces=_NS)
        vm_el = rm_el.find("str:ValueMap", namespaces=_NS)
        tf_el = rm_el.find("str:ToTextFormat", namespaces=_NS)
        if clm_ref is not None:
            representation_map = Ref[object].unresolved(
                RepresentationMap.urn_for(
                    agency=held.agency.id, id=held.own(required(clm_ref, "id")), version=held.version
                )
            )
        elif vm_el is not None:
            value_map = RepresentationMap(
                id=held.own(f"{required(sm_el, 'id')}_VALUE_MAP_{n}"),
                **held.maintainable(),
                name=_helpers.read_istring(sm_el),
                annotations=Annotations([Annotation(type=BUILT_VALUE_MAP_TYPE)]),
                sources=(MapDataType("String"),),
                targets=(MapDataType("String"),),
                mappings=tuple(
                    RepresentationMapping(sources=(required(vm, "source"),), targets=(required(vm, "target"),))
                    for vm in vm_el.findall("str:ValueMapping", namespaces=_NS)
                ),
            )
            built.append(value_map)
            representation_map = Ref[object].unresolved(artefact_urn(value_map))
        elif tf_el is not None:
            to_text_format = _helpers.read_text_format(tf_el)
            to_value_type = (rm_el.findtext("str:ToValueType", namespaces=_NS) or "").strip() or None
    return ComponentMap(
        sources=(source_id,),
        targets=(target_id,),
        annotations=_helpers.read_annotations(cm_el),
        representation_map=representation_map,
        to_text_format=to_text_format,
        to_value_type=to_value_type,
    )


def _read_id_pair_maps_of(m_el: etree._Element) -> tuple[str, str] | None:
    """A map's ``<Source><Ref/>`` and ``<Target><Ref/>`` ids, as `_read_id_pair_maps` reads them."""
    src_ref_el = m_el.find("str:Source/Ref", namespaces=_NS)
    tgt_ref_el = m_el.find("str:Target/Ref", namespaces=_NS)
    if src_ref_el is None or tgt_ref_el is None:
        return None
    return required(src_ref_el, "id"), required(tgt_ref_el, "id")


#: One StructureSet's yield: the maps it carries, promoted to top level, and the
#: set itself — None for a wrapper this library synthesised on a write.
type _StructureSetContents = tuple[
    list[RepresentationMap],
    list[StructureMap],
    list[ConceptSchemeMap],
    list[CategorySchemeMap],
    list[OrganisationSchemeMap],
    StructureSet | None,
]


def _read_structure_sets(
    structures: etree._Element,
    agencies: dict[str, Agency],
    refused: list[Refusal],
) -> tuple[
    list[RepresentationMap],
    list[StructureMap],
    list[ConceptSchemeMap],
    list[CategorySchemeMap],
    list[OrganisationSchemeMap],
    list[StructureSet],
]:
    """Unwrap 2.1 ``<str:StructureSets>`` into the top-level map lists.

    Each map inside a ``<str:StructureSet>`` becomes its own top-level
    maintainable, inheriting the container's agency/version, *and* the
    container itself is kept as a `StructureSet` referencing them. It is a
    maintainable in 2.1 — its own id, name, validity dates — and discarding it
    meant a document's set identity and grouping were lost on re-emission
    (#239). The 2.1
    ``<str:CodelistMap>``/``<str:CodeMap>`` shape rehydrates into the
    3.0-native ``RepresentationMap``/``RepresentationMapping``; ``StructureMap`` covers
    both the DSD- and dataflow-source cases (dispatch on the
    source ref class).

    The unit of refusal is the StructureSet, not each map in it (#435): the
    maps are maintainables only because this model promotes them, and the
    document's author published them as one artefact.
    """
    container = structures.find("str:StructureSets", namespaces=_NS)
    if container is None:
        return [], [], [], [], [], []

    def one(ss_el: etree._Element) -> _StructureSetContents:
        rep_maps: list[RepresentationMap] = []
        struct_maps: list[StructureMap] = []
        concept_scheme_maps: list[ConceptSchemeMap] = []
        category_scheme_maps: list[CategorySchemeMap] = []
        organisation_scheme_maps: list[OrganisationSchemeMap] = []
        # A wrapper this library invented on a previous write is not an
        # artefact — reifying it would turn a downgrade into an assertion
        # that its maps belong together, and block the trip back to 3.0 over
        # a grouping nobody stated. Its maps stay, with their own ids; the
        # container goes.
        annotations = _helpers.read_annotations(ss_el)
        synthetic = SYNTHETIC_STRUCTURE_SET_TYPE in annotations.types
        ss_id = required(ss_el, "id")
        # What a map takes from its set, which 3.0 made each map's own (#421):
        # an id unique within the agency, and the maintainable's attributes.
        held = _SetMembership(
            agency=_helpers.resolve_agency(required(ss_el, "agencyID"), agencies),
            version=ss_el.get("version", "1.0"),
            own=(lambda inner: inner) if synthetic else (lambda inner: member_id(inner, ss_id)),
            valid_from=_helpers.read_datetime_attr(ss_el, "validFrom"),
            valid_to=_helpers.read_datetime_attr(ss_el, "validTo"),
            reference_attrs=_helpers.read_reference_attrs(ss_el),
        )
        ss_agency, ss_version = held.agency, held.version

        for cm_el in ss_el.findall("str:CodelistMap", namespaces=_NS):
            source_ref, target_ref = _read_source_target_refs(cm_el)
            mappings = [
                RepresentationMapping(sources=(vm.source_id,), targets=(vm.target_id,), annotations=vm.annotations)
                for vm in _read_id_pair_maps(cm_el, "CodeMap", ItemMap)
            ]
            rep_maps.append(
                RepresentationMap(
                    id=held.own(required(cm_el, "id")),
                    uri=cm_el.get("uri"),
                    **held.maintainable(),
                    name=_helpers.read_istring(cm_el),
                    description=_helpers.read_istring(cm_el, "Description"),
                    annotations=_helpers.read_annotations(cm_el),
                    sources=() if source_ref is None else (source_ref,),
                    targets=() if target_ref is None else (target_ref,),
                    mappings=tuple(mappings),
                )
            )

        for csm_el in ss_el.findall("str:ConceptSchemeMap", namespaces=_NS):
            source_ref, target_ref = _read_source_target_refs(csm_el)
            item_maps = [
                ItemMap(source_id=p.source_id, target_id=p.target_id, annotations=p.annotations)
                for p in _read_id_pair_maps(csm_el, "ConceptMap", ItemMap)
            ]
            concept_scheme_maps.append(
                ConceptSchemeMap(
                    id=held.own(required(csm_el, "id")),
                    uri=csm_el.get("uri"),
                    **held.maintainable(),
                    name=_helpers.read_istring(csm_el),
                    description=_helpers.read_istring(csm_el, "Description"),
                    annotations=_helpers.read_annotations(csm_el),
                    source=source_ref,
                    target=target_ref,
                    maps=tuple(item_maps),
                )
            )

        for catm_el in ss_el.findall("str:CategorySchemeMap", namespaces=_NS):
            source_ref, target_ref = _read_source_target_refs(catm_el)
            item_maps = [
                ItemMap(source_id=p.source_id, target_id=p.target_id, annotations=p.annotations)
                for p in _read_id_pair_maps(catm_el, "CategoryMap", ItemMap)
            ]
            category_scheme_maps.append(
                CategorySchemeMap(
                    id=held.own(required(catm_el, "id")),
                    uri=catm_el.get("uri"),
                    **held.maintainable(),
                    name=_helpers.read_istring(catm_el),
                    description=_helpers.read_istring(catm_el, "Description"),
                    annotations=_helpers.read_annotations(catm_el),
                    source=source_ref,
                    target=target_ref,
                    maps=tuple(item_maps),
                )
            )

        for osm_el in ss_el.findall("str:OrganisationSchemeMap", namespaces=_NS):
            source_ref, target_ref = _read_source_target_refs(osm_el)
            item_maps = [
                ItemMap(source_id=p.source_id, target_id=p.target_id, annotations=p.annotations)
                for p in _read_id_pair_maps(osm_el, "OrganisationMap", ItemMap)
            ]
            organisation_scheme_maps.append(
                OrganisationSchemeMap(
                    id=held.own(required(osm_el, "id")),
                    uri=osm_el.get("uri"),
                    **held.maintainable(),
                    name=_helpers.read_istring(osm_el),
                    description=_helpers.read_istring(osm_el, "Description"),
                    annotations=_helpers.read_annotations(osm_el),
                    source=source_ref,
                    target=target_ref,
                    maps=tuple(item_maps),
                )
            )

        for dsm_el in ss_el.findall("str:StructureMap", namespaces=_NS):
            source_ref, target_ref = _read_source_target_refs(dsm_el)
            comp_maps = [
                comp_map
                for n, cm_el in enumerate(dsm_el.findall("str:ComponentMap", namespaces=_NS), start=1)
                if (comp_map := _read_component_map(cm_el, n, dsm_el, held, rep_maps)) is not None
            ]
            struct_maps.append(
                StructureMap(
                    id=held.own(required(dsm_el, "id")),
                    uri=dsm_el.get("uri"),
                    **held.maintainable(),
                    name=_helpers.read_istring(dsm_el),
                    description=_helpers.read_istring(dsm_el, "Description"),
                    annotations=_helpers.read_annotations(dsm_el),
                    source=source_ref,
                    target=target_ref,
                    maps=tuple(comp_maps),
                    is_extension=flag(dsm_el, "isExtension"),
                )
            )

        if synthetic:
            return rep_maps, struct_maps, concept_scheme_maps, category_scheme_maps, organisation_scheme_maps, None

        # Whatever this container contributed is what it groups. Refs rather
        # than values, so each map stays single-sourced at top level. A value
        # map built from a component map's <ValueMap> is not among them: the
        # set holds the structure map, and the pairs are its (#422).
        grouped = [
            *(m for m in rep_maps if not is_built_value_map(m)),
            *struct_maps,
            *concept_scheme_maps,
            *category_scheme_maps,
            *organisation_scheme_maps,
        ]
        structure_set = StructureSet(
            id=ss_id,
            uri=ss_el.get("uri"),
            maintainer=ss_agency,
            version=ss_version,
            name=_helpers.read_istring(ss_el),
            description=_helpers.read_istring(ss_el, "Description"),
            legacy_is_final=_helpers.read_legacy_is_final(ss_el),
            annotations=annotations,
            maps=tuple(Ref[object].unresolved(artefact_urn(m)) for m in grouped),
            related_structures=tuple(
                ref
                for rs_el in ss_el.findall("str:RelatedStructure", namespaces=_NS)
                if (ref := _helpers.resolve_ref(rs_el, object)) is not None
            ),
            valid_from=held.valid_from,
            valid_to=held.valid_to,
            **held.reference_attrs,
        )
        return rep_maps, struct_maps, concept_scheme_maps, category_scheme_maps, organisation_scheme_maps, structure_set

    built = build_each_xml(container.findall("str:StructureSet", namespaces=_NS), one, StructureSet, refused)
    return (
        [m for b in built for m in b[0]],
        [m for b in built for m in b[1]],
        [m for b in built for m in b[2]],
        [m for b in built for m in b[3]],
        [m for b in built for m in b[4]],
        [b[5] for b in built if b[5] is not None],
    )


# ── Hierarchies ───────────────────────────────────────────────────────────────


def _aliased_codelist(hc_el: etree._Element, aliases: dict[str, SdmxUrn[object]]) -> SdmxUrn[object] | None:
    """The included codelist a node's `<CodelistAliasRef>` names, if it names one."""
    alias_el = hc_el.find("str:CodelistAliasRef", namespaces=_NS)
    if alias_el is None or not alias_el.text:
        return None
    return aliases.get(alias_el.text.strip())


def _read_hierarchical_code(
    hc_el: etree._Element, levels_by_id: dict[str, Level], aliases: dict[str, SdmxUrn[object]]
) -> HierarchicalCode:
    """Recursively parse a HierarchicalCode element.

    The v2.1 XSD's HierarchicalCodeType offers two ways to point at the
    referenced code: ``<Code>`` (full Ref) or
    ``<CodelistAliasRef>+<CodeID>`` (alias form). Both wrap a Ref to a
    Code item; we accept either shape and unify into a single
    ``Ref[Code]``. Earlier sdmxlib output used ``<CodeID>`` alone — that's
    not XSD-valid (the alias-form requires the partner element) but the
    reader keeps tolerating it so legacy fixtures still parse.
    """
    code_ref: SdmxUrn[Code] | None = None
    # lxml elements with no children are falsy, so chain the `find`s
    # explicitly rather than via `or`.
    ref_carrier = hc_el.find("str:Code", namespaces=_NS)
    if ref_carrier is None:
        ref_carrier = hc_el.find("str:CodeID", namespaces=_NS)
    if ref_carrier is not None:
        ref_el = ref_carrier.find("Ref")
        if ref_el is not None:
            agency = ref_el.get("agencyID", "")
            parent_id = ref_el.get("maintainableParentID", "")
            item_id = required(ref_el, "id")
            # `HierarchicalCodeRefType` defaults the parent's version to 1.0 (#476).
            version = ref_el.get("maintainableParentVersion", ref_el.get("version", "1.0"))
            if agency and parent_id and item_id:
                code_ref = SdmxUrn.make(
                    Code,
                    package="codelist",
                    artefact_class="Code",
                    agency=agency,
                    id=parent_id,
                    version=version,
                    item_id=item_id,
                )
            elif item_id and (codelist := _aliased_codelist(hc_el, aliases)) is not None:
                # The alias form (#434): `<CodeID><Ref id="_T"/>` names only the
                # code, and `<CodelistAliasRef>` names the `<IncludedCodelist
                # alias=…>` it comes from. OECD writes every 2.1 HCL this way,
                # and all 8,297 of its codes were read as having none.
                code_ref = SdmxUrn.make(
                    Code,
                    package="codelist",
                    artefact_class="Code",
                    agency=codelist.agency,
                    id=codelist.id,
                    version=codelist.version,
                    item_id=item_id,
                )
        elif (text := _helpers.urn_text(ref_carrier)).startswith("urn:"):
            code_ref = Code.parse_urn(text)

    children = [
        _read_hierarchical_code(child_el, levels_by_id, aliases)
        for child_el in hc_el.findall("str:HierarchicalCode", namespaces=_NS)
    ]
    # 2.1 wraps the level reference in a `<Ref>` where 3.0 spells it as text,
    # the same split as `Parent`. Resolved against the hierarchy's own levels
    # so the node carries the `Level` object rather than a dangling id — which
    # is what the 3.0 reader has always done (#355).
    level = None
    level_el = hc_el.find("str:Level", namespaces=_NS)
    if level_el is not None:
        ref_el = level_el.find("Ref")
        level_id = required(ref_el, "id") if ref_el is not None else (level_el.text or "").strip()
        if level_id:
            level = node_level(levels_by_id, level_id)

    return HierarchicalCode(
        id=required(hc_el, "id"),
        uri=hc_el.get("uri"),
        code=code_ref,
        children=children,
        level=level,
        # The writer had emitted these all along; nothing read them back, so
        # they round-tripped to nothing (#355).
        annotations=_helpers.read_annotations(hc_el),
        valid_from=_helpers.read_datetime_attr(hc_el, "validFrom"),
        valid_to=_helpers.read_datetime_attr(hc_el, "validTo"),
        version=hc_el.get("version"),
    )


def _read_level_chain(level_el: etree._Element) -> list[Level]:
    """Flatten a recursive v2.1 ``<str:Level>`` chain into an ordered list.

    Per the XSD, each ``<Level>`` may contain at most one nested ``<Level>``
    describing the next level down. We walk that chain and return the
    levels root-first to match the order on ``Hierarchy.levels``.
    """
    out: list[Level] = []
    current: etree._Element | None = level_el
    while current is not None:
        cf_el = current.find("str:CodingFormat", namespaces=_NS)
        coding_format: Facet | None = None
        if cf_el is not None:
            start_str = cf_el.get("startValue")
            end_str = cf_el.get("endValue")
            min_len_str = cf_el.get("minLength")
            max_len_str = cf_el.get("maxLength")
            # `minValue` / `maxValue` are `use="optional"` on
            # `CodingTextFormatType` in every version. #354 taught the 2.1
            # writer to emit them and #364 the 3.x writers; this reader was
            # still not asking, so they round-tripped to nothing (#355).
            min_val_str = cf_el.get("minValue")
            max_val_str = cf_el.get("maxValue")
            interval_str = cf_el.get("interval")
            coding_format = Facet(
                type=cf_el.get("textType"),
                is_sequence=optional_flag(cf_el, "isSequence"),
                min_length=to_int(min_len_str, what="@minLength") if min_len_str is not None else None,
                max_length=to_int(max_len_str, what="@maxLength") if max_len_str is not None else None,
                start_value=to_int(start_str, what="@startValue") if start_str is not None else None,
                end_value=to_int(end_str, what="@endValue") if end_str is not None else None,
                min_value=to_int(min_val_str, what="@minValue") if min_val_str is not None else None,
                max_value=to_int(max_val_str, what="@maxValue") if max_val_str is not None else None,
                pattern=cf_el.get("pattern"),
                interval=to_int(interval_str, what="@interval") if interval_str is not None else None,
            )
        out.append(
            Level(
                id=required(current, "id"),
                uri=current.get("uri"),
                name=_helpers.read_istring(current),
                description=_helpers.read_istring(current, "Description"),
                coding_format=coding_format,
                annotations=_helpers.read_annotations(current),
            )
        )
        current = current.find("str:Level", namespaces=_NS)
    return out


def _read_hierarchies(
    structures: etree._Element,
    agencies: dict[str, Agency],
    refused: list[Refusal],
) -> tuple[list[Hierarchy], list[HierarchicalCodelist]]:
    """Unwrap ``<str:HierarchicalCodelists>``, keeping the containers that say something.

    Each ``<str:Hierarchy>`` becomes a top-level `Hierarchy` as in SDMX 3.0,
    with the id `member_id` gives it (#421), and with the container's agency,
    version, validity and external-reference attributes, which 3.0 made its
    own. The ``<str:HierarchicalCodelist>`` around it is kept as a peer
    referencing them. Discarding it lost the container's own name — a
    "Geographic hierarchy" containing "Canada → provinces → cities" came back
    with only the latter (#239).

    Unless it is only a wrapper (`_wraps_only`): the container every 2.1
    writer puts around a standalone hierarchy, ECB's 36 and BIS's 48 among
    them. It carries nothing the hierarchy does not, and holding it would turn
    one hierarchy written to 2.1 into two artefacts read back.

    The unit of refusal is the HierarchicalCodelist, hierarchies and all
    (#435) — it is the maintainable the document published.
    """
    container = structures.find("str:HierarchicalCodelists", namespaces=_NS)
    if container is None:
        return [], []

    def one(hcl_el: etree._Element) -> tuple[list[Hierarchy], HierarchicalCodelist | None]:
        result: list[Hierarchy] = []
        hcl_id = required(hcl_el, "id")
        hcl_agency = required(hcl_el, "agencyID")
        hcl_version = hcl_el.get("version", "1.0")
        valid_from = _helpers.read_datetime_attr(hcl_el, "validFrom")
        valid_to = _helpers.read_datetime_attr(hcl_el, "validTo")
        reference_attrs = _helpers.read_reference_attrs(hcl_el)
        # `alias` → the codelist it stands for, for nodes in the alias form.
        included: list[tuple[str | None, Ref[object]]] = [
            (ic_el.get("alias"), ref)
            for ic_el in hcl_el.findall("str:IncludedCodelist", namespaces=_NS)
            if (ref := _helpers.resolve_ref(ic_el, object)) is not None
        ]
        aliases = {alias: ref.urn for alias, ref in included if alias}
        for h_el in hcl_el.findall("str:Hierarchy", namespaces=_NS):
            # Levels first: a node's `<Level>` names one of them, so the tree
            # cannot be resolved before they exist.
            level_root = h_el.find("str:Level", namespaces=_NS)
            levels: list[Level] = _read_level_chain(level_root) if level_root is not None else []
            levels_by_id = {level.id: level for level in levels}
            tree = [
                _read_hierarchical_code(hc_el, levels_by_id, aliases)
                for hc_el in h_el.findall("str:HierarchicalCode", namespaces=_NS)
            ]
            result.append(
                Hierarchy(
                    id=member_id(required(h_el, "id"), hcl_id),
                    uri=h_el.get("uri"),
                    maintainer=_helpers.resolve_agency(hcl_agency, agencies),
                    version=hcl_version,
                    valid_from=valid_from,
                    valid_to=valid_to,
                    **reference_attrs,
                    name=_helpers.read_istring(h_el),
                    description=_helpers.read_istring(h_el, "Description"),
                    annotations=_helpers.read_annotations(h_el),
                    has_formal_levels=flag(h_el, "leveled"),
                    levels=levels,
                    tree=tree,
                )
            )
        hierarchical_codelist = HierarchicalCodelist(
            id=hcl_id,
            uri=hcl_el.get("uri"),
            maintainer=_helpers.resolve_agency(hcl_agency, agencies),
            version=hcl_version,
            name=_helpers.read_istring(hcl_el),
            description=_helpers.read_istring(hcl_el, "Description"),
            annotations=_helpers.read_annotations(hcl_el),
            hierarchies=tuple(Ref[object].unresolved(artefact_urn(h)) for h in result),
            legacy_is_final=_helpers.read_legacy_is_final(hcl_el),
            included_codelists=tuple(ref for _alias, ref in included),
            valid_from=valid_from,
            valid_to=valid_to,
            **reference_attrs,
        )
        return result, None if _wraps_only(hierarchical_codelist, result) else hierarchical_codelist

    built = build_each_xml(
        container.findall("str:HierarchicalCodelist", namespaces=_NS), one, HierarchicalCodelist, refused
    )
    return [h for hierarchies, _ in built for h in hierarchies], [hcl for _, hcl in built if hcl is not None]


def _wraps_only(hcl: HierarchicalCodelist, hierarchies: list[Hierarchy]) -> bool:
    """Whether `hcl` is only the container 2.1 requires around one hierarchy (#421).

    One hierarchy, with the codelist's id, uri, name, description and
    annotations, and nothing the hierarchy lacks: no included codelists, no
    ``isFinal`` disagreeing with the version. Its agency, version, validity and
    external-reference attributes are the hierarchy's already.
    """
    if len(hierarchies) != 1 or hcl.included_codelists or hcl.legacy_is_final is not None:
        return False
    (hierarchy,) = hierarchies
    return (hierarchy.id, hierarchy.uri, hierarchy.name, hierarchy.description, hierarchy.annotations) == (
        hcl.id,
        hcl.uri,
        hcl.name,
        hcl.description,
        hcl.annotations,
    )


def _merge_report_attributes(
    attrs: dict[str, tuple[str, MetadataAttribute]], report_id: str, own: list[MetadataAttribute]
) -> None:
    """Add one report structure's root attributes to the MSD's single list.

    2.1 gives each `ReportStructure` its own attribute tree; the model keeps the
    3.0 shape — one list, which each report references by id (#245). A root id
    two reports declare is one attribute when the declarations are identical,
    and is kept once. Declared *differently*, the same id names two attributes
    the single list cannot hold apart, so the MSD is refused (#439) rather than
    keeping whichever came last — what concatenating them did, and what the
    2.1 writer's id lookup then wrote back for both reports.

    Raises:
        SdmxConformanceError: When two reports declare one root id differently.
    """
    for attr in own:
        seen = attrs.get(attr.id)
        if seen is None:
            attrs[attr.id] = (report_id, attr)
        elif seen[1] != attr:
            element = f"MetadataAttribute {attr.id}"
            detail = (
                f"report structures {seen[0]!r} and {report_id!r} each declare a root attribute {attr.id!r}, "
                "differently. Valid SDMX-ML 2.1, but sdmxlib holds an MSD's attributes in SDMX 3.0's single list, "
                "where one id is one attribute"
            )
            raise SdmxConformanceError(element, detail)


def _read_metadata_attribute(attr_el: etree._Element, index: dict[str, object]) -> MetadataAttribute:
    """Parse one ``<str:MetadataAttribute>``, recursing into its children.

    2.1 and 3.0 give the attribute itself the same shape — id, ConceptIdentity,
    LocalRepresentation, min/maxOccurs, nested attributes — so only the
    surrounding container differs between the versions.
    """
    ci_el = attr_el.find("str:ConceptIdentity", namespaces=_NS)
    concept_ref = _helpers.resolve_ref(ci_el, Concept) if ci_el is not None else None

    rep_el = attr_el.find("str:LocalRepresentation", namespaces=_NS)
    representation = _helpers.read_representation(rep_el, index, enum_cls=Codelist) if rep_el is not None else None
    # 2.1's `MetadataAttributeRepresentationType` is TextFormat | (Enumeration,
    # EnumerationFormat) — the same shape as a DSD component's. The writer had
    # always passed the format through; nothing read it back (#355).
    enumeration_format = _helpers.read_enumeration_format(rep_el) if rep_el is not None else None

    max_occurs_attr = attr_el.get("maxOccurs")
    if max_occurs_attr is None:
        max_occurs: int | None = 1
    elif max_occurs_attr == "unbounded":
        max_occurs = None
    else:
        max_occurs = _helpers.read_int(attr_el, "maxOccurs", default=1)

    return MetadataAttribute(
        id=component_id(attr_el, concept_ref),
        uri=attr_el.get("uri"),
        concept=concept_ref,
        representation=representation,
        enumeration_format=enumeration_format,
        min_occurs=_helpers.read_int(attr_el, "minOccurs", default=1),
        max_occurs=max_occurs,
        is_presentational=flag(attr_el, "isPresentational"),
        children=ItemList(
            [_read_metadata_attribute(c, index) for c in attr_el.findall("str:MetadataAttribute", namespaces=_NS)]
        ),
        annotations=_helpers.read_annotations(attr_el),
    )


_TARGET_KINDS = (
    "KeyDescriptorValuesTarget",
    "DataSetTarget",
    "ConstraintContentTarget",
    "ReportPeriodTarget",
    "IdentifiableObjectTarget",
)


def _read_metadata_target(mt_el: etree._Element) -> MetadataTarget:
    """Parse one ``<str:MetadataTarget>`` and every target object in it, in order (#422).

    2.1 models a target object's kind as a *choice of element*, not an
    attribute, so the kind is the element's name.
    """
    target_objects: list[TargetObject] = []
    for child in mt_el:
        if not isinstance(child.tag, str):
            continue  # a comment or processing instruction
        kind = etree.QName(child).localname
        if kind not in _TARGET_KINDS:
            continue
        rep_el = child.find("str:LocalRepresentation", namespaces=_NS)
        tf_el = rep_el.find("str:TextFormat", namespaces=_NS) if rep_el is not None else None
        target_objects.append(
            TargetObject(
                id=required(child, "id"),
                kind=kind,
                object_type=child.get("objectType"),
                representation=_helpers.read_text_format(tf_el) if tf_el is not None else None,
                annotations=_helpers.read_annotations(child),
                uri=child.get("uri"),
            )
        )
    return MetadataTarget(
        id=required(mt_el, "id"),
        uri=mt_el.get("uri"),
        target_objects=target_objects,
        annotations=_helpers.read_annotations(mt_el),
    )


def _read_metadata_structures(
    structures: etree._Element,
    index: dict[str, object],
    agencies: dict[str, Agency],
    refused: list[Refusal],
) -> list[MetadataStructure]:
    """Parse ``<str:MetadataStructures>``, flattening the 2.1 reporting layer.

    2.1 wraps its attributes in ``MetadataStructureComponents`` → one or more
    ``ReportStructure``, each naming the ``MetadataTarget``s it reports
    against. SDMX 3.0 removed that layer, and this model follows 3.0: the
    attributes of every report structure are concatenated into
    `MetadataStructure.attributes`, in document order.

    The targets and the report-structure grouping are kept too, on
    `MetadataStructure.targets` and `report_structures` (#245), so a 2.1 MSD
    writes back as itself — unless the layer is one sdmxlib invented for an
    MSD that had none, which is dropped (#421).
    """
    container = structures.find("str:MetadataStructures", namespaces=_NS)
    if container is None:
        return []

    def one(msd_el: etree._Element) -> MetadataStructure:
        attrs: dict[str, tuple[str, MetadataAttribute]] = {}
        targets: list[MetadataTarget] = []
        reports: list[ReportStructure] = []
        components_el = msd_el.find("str:MetadataStructureComponents", namespaces=_NS)
        if components_el is not None:
            targets = [_read_metadata_target(t) for t in components_el.findall("str:MetadataTarget", namespaces=_NS)]
            for rs_el in components_el.findall("str:ReportStructure", namespaces=_NS):
                own = [
                    _read_metadata_attribute(a, index) for a in rs_el.findall("str:MetadataAttribute", namespaces=_NS)
                ]
                _merge_report_attributes(attrs, required(rs_el, "id"), own)
                reports.append(
                    ReportStructure(
                        id=required(rs_el, "id"),
                        uri=rs_el.get("uri"),
                        attribute_ids=tuple(a.id for a in own),
                        target_ids=tuple(
                            required(ref, "id")
                            for mt_el in rs_el.findall("str:MetadataTarget", namespaces=_NS)
                            if (ref := mt_el.find("Ref")) is not None
                        ),
                        annotations=_helpers.read_annotations(rs_el),
                    )
                )
        return MetadataStructure(
            id=required(msd_el, "id"),
            uri=msd_el.get("uri"),
            maintainer=_helpers.resolve_agency(required(msd_el, "agencyID"), agencies),
            version=msd_el.get("version", "1.0"),
            legacy_is_final=_helpers.read_legacy_is_final(msd_el),
            name=_helpers.read_istring(msd_el),
            description=_helpers.read_istring(msd_el, "Description"),
            annotations=_helpers.read_annotations(msd_el),
            attributes=ItemList([attr for _, attr in attrs.values()]),
            # A layer this library invented for an MSD that had none is not
            # the document's (#421): dropped, so a 3.x MSD comes back as itself.
            targets=tuple(t for t in targets if SYNTHETIC_REPORT_LAYER_TYPE not in t.annotations.types),
            report_structures=tuple(r for r in reports if SYNTHETIC_REPORT_LAYER_TYPE not in r.annotations.types),
            valid_from=_helpers.read_datetime_attr(msd_el, "validFrom"),
            valid_to=_helpers.read_datetime_attr(msd_el, "validTo"),
            **_helpers.read_reference_attrs(msd_el),
        )

    return build_each_xml(container.findall("str:MetadataStructure", namespaces=_NS), one, MetadataStructure, refused)


# ── Metadata provision agreements ─────────────────────────────────────────────


def _read_metadata_provision_agreements(
    structures: etree._Element,
    agencies: dict[str, Agency],
    refused: list[Refusal],
) -> list[MetadataProvisionAgreement]:
    """Parse each ``<str:ProvisionAgreement>`` over a metadataflow as the metadata provision agreement it is (#421).

    2.1 provisioned metadata with a plain agreement naming a data provider;
    SDMX 3.0 gave metadata an agreement and a provider of its own. The
    provider is read as the metadata provider of the same agency and id,
    which is where sdmxlib's own 2.1 output put it (`_providers`).
    """
    container = structures.find("str:ProvisionAgreements", namespaces=_NS)
    if container is None:
        return []

    def one(pa_el: etree._Element) -> MetadataProvisionAgreement:
        usage = pa_el.find("str:StructureUsage", namespaces=_NS)
        provider_el = pa_el.find("str:DataProvider", namespaces=_NS)
        provider = _helpers.resolve_ref(provider_el, object) if provider_el is not None else None
        provider_urn = metadata_provider_urn(provider.urn) if provider is not None else None
        return MetadataProvisionAgreement(
            id=required(pa_el, "id"),
            uri=pa_el.get("uri"),
            maintainer=_helpers.resolve_agency(required(pa_el, "agencyID"), agencies),
            version=pa_el.get("version", "1.0"),
            name=_helpers.read_istring(pa_el),
            description=_helpers.read_istring(pa_el, "Description"),
            annotations=_helpers.read_annotations(pa_el),
            metadataflow=_helpers.resolve_ref(usage, Metadataflow) if usage is not None else None,
            metadata_provider=(None if provider_urn is None else Ref.unresolved(provider_urn)),
            valid_from=_helpers.read_datetime_attr(pa_el, "validFrom"),
            valid_to=_helpers.read_datetime_attr(pa_el, "validTo"),
            **_helpers.read_reference_attrs(pa_el),
        )

    elements = [
        el for el in container.findall("str:ProvisionAgreement", namespaces=_NS) if _helpers.provisions_metadata(el)
    ]
    return build_each_xml(elements, one, MetadataProvisionAgreement, refused)


# ── Entry point ───────────────────────────────────────────────────────────────


def read(data: bytes, *, registry: SupportsAddAll | None = None) -> StructureMessage:
    """Parse SDMX-ML 2.1 bytes into a StructureMessage.

    References within the message are auto-resolved.

    If *registry* is provided all parsed artefacts are added to it via
    ``registry.add_all()``, their Refs bound to it for cross-message resolution.
    An artefact that contradicts the schema is refused — recorded in
    `StructureMessage.refused`, never partly built — and the rest are read;
    whether that fails the call is `sdmxlib.formats.parse`'s decision (#435).

    Every reference is bound to the document's index (#492), so it finds what
    the document defines wherever it defines it; refusing an artefact removes
    it from the index (`formats._refuse.refuse_invalid`). Read into a
    registry, they are bound to the registry instead (`document_binding`).
    """
    index: dict[str, object] = {}
    with document_binding(index):
        msg = _read(data, index)
    if registry is not None:
        registry.add_all(msg.all_artefacts())
    return msg


def _read(data: bytes, index: dict[str, object]) -> StructureMessage:
    root = etree.fromstring(data)
    header, header_lost = read_header(root, com=COM)
    structures = root.find("mes:Structures", namespaces=_NS)
    if structures is None:
        return StructureMessage(header=header, lost=() if header_lost is None else (header_lost,))

    # Artefacts refused for contradicting the schema, each read on past
    # rather than failing the document (#435).
    refused: list[Refusal] = []

    # ── Phase 1: parse leaf artefacts (no refs to other artefacts) ──────────
    organisation_schemes = _read_organisation_schemes(structures, refused)
    agency_schemes, data_provider_schemes = organisation_schemes.agency, organisation_schemes.data_provider
    # 2.1 has no metadata provider; sdmxlib writes a 3.x one into its
    # agency's data provider scheme, marked, and takes it back out (#421).
    data_provider_schemes, metadata_provider_schemes = split_provider_schemes(data_provider_schemes)

    # Build agency lookup for opportunistic resolution in subsequent phases
    agencies: dict[str, Agency] = {a.id: a for s in agency_schemes for a in s.agencies}

    codelists = _helpers.read_codelists(structures, agencies, refused)
    concept_schemes = _helpers.read_concept_schemes(structures, agencies, refused)
    category_schemes = _helpers.read_category_schemes(structures, agencies, refused)

    # ── Phase 2: build resolution index ─────────────────────────────────────

    _helpers.index_all(index, codelists, Codelist)

    for cs in concept_schemes:
        _helpers.index_artefact(index, cs, ConceptScheme)
        for concept in cs.concepts:
            _helpers.index_item(index, concept, cs, ConceptScheme.sdmx_package, "Concept")

    _helpers.index_all(index, category_schemes, CategoryScheme)

    # ── Phase 3: parse artefacts that reference codelists / concepts ─────────
    data_structures = _read_data_structures(structures, index, agencies, refused)
    _helpers.index_all(index, data_structures, DataStructure)

    dataflows = _helpers.read_dataflows(structures, agencies, refused)
    metadataflows = _read_metadataflows(structures, agencies, refused)
    metadata_structures = _read_metadata_structures(structures, index, agencies, refused)
    _helpers.index_all(index, dataflows, Dataflow)
    _helpers.index_all(index, metadataflows, Metadataflow)
    for scheme in metadata_provider_schemes:
        for provider in scheme.providers:
            _helpers.index_item(index, provider, scheme, MetadataProvider.sdmx_package, MetadataProvider.sdmx_class)

    # ── Phase 4: parse artefacts that reference DSDs / dataflows ────────────
    categorisations = _helpers.read_categorisations(structures, agencies, refused)
    provision_agreements = _helpers.read_provision_agreements(structures, agencies, refused)
    metadata_provision_agreements = _read_metadata_provision_agreements(structures, agencies, refused)
    constraints = _helpers.read_constraints(structures, agencies, refused)
    attachment_constraints = _read_attachment_constraints(structures, agencies, refused)
    rep_maps, struct_maps, concept_scheme_maps, category_scheme_maps, organisation_scheme_maps, structure_sets = (
        _read_structure_sets(structures, agencies, refused)
    )
    hierarchies, hierarchical_codelists = _read_hierarchies(structures, agencies, refused)

    msg = StructureMessage(
        header=header,
        lost=() if header_lost is None else (header_lost,),
        codelists=ArtefactList(codelists),
        concept_schemes=ArtefactList(concept_schemes),
        data_structures=ArtefactList(data_structures),
        dataflows=ArtefactList(dataflows),
        metadataflows=ArtefactList(metadataflows),
        metadata_structures=ArtefactList(metadata_structures),
        category_schemes=ArtefactList(category_schemes),
        categorisations=ArtefactList(categorisations),
        provision_agreements=ArtefactList(provision_agreements),
        agency_schemes=ArtefactList(agency_schemes),
        data_provider_schemes=ArtefactList(data_provider_schemes),
        metadata_provider_schemes=ArtefactList(metadata_provider_schemes),
        data_consumer_schemes=ArtefactList(organisation_schemes.data_consumer),
        organisation_unit_schemes=ArtefactList(organisation_schemes.organisation_unit),
        metadata_provision_agreements=ArtefactList(metadata_provision_agreements),
        constraints=ArtefactList(constraints),
        attachment_constraints=ArtefactList(attachment_constraints),
        representation_maps=ArtefactList(rep_maps),
        structure_maps=ArtefactList(struct_maps),
        concept_scheme_maps=ArtefactList(concept_scheme_maps),
        category_scheme_maps=ArtefactList(category_scheme_maps),
        organisation_scheme_maps=ArtefactList(organisation_scheme_maps),
        hierarchies=ArtefactList(hierarchies),
        structure_sets=ArtefactList(structure_sets),
        hierarchical_codelists=ArtefactList(hierarchical_codelists),
        refused=tuple(refused),
    )
    # Before interning: what the read could not hold (#422).
    return account(structures, msg)
