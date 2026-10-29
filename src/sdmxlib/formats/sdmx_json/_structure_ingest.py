"""SDMX-JSON 2.0 structure DTO → attrs domain model (the read side).

Part of the msgspec migration (milestone: SDMX-JSON dict+orjson → msgspec).
The reader decodes/`convert`s each `/data` slice into the typed Struct DTOs from
`_structure_dto`, then these `from_*` functions ingest them into the attrs
model — the inverse of the `to_*` write converters. Intra-message ref
resolution uses the shared `resolve_urn` (`_resolve`) against the reader's
`index`, which is threaded in; agency lookup stays in the reader (it owns the
`agencies` map) and is passed in.

Filled in family by family alongside the write side; this covers the item
schemes so far.
"""

from datetime import date, datetime, time, timedelta

from typing_extensions import TypedDict

from sdmxlib.formats._attachment import match_dimension_case
from sdmxlib.formats._coerce import to_datetime, to_enum
from sdmxlib.formats._positions import check_positions
from sdmxlib.formats.sdmx_json._dto import Annotation, ingest_annotations, ingest_istr
from sdmxlib.formats.sdmx_json._resolve import resolve_any_urn, resolve_urn
from sdmxlib.formats.sdmx_json._structure_dto import (
    AgencySchemeJson,
    AttributeJson,
    AttributeRelationship,
    CategorisationJson,
    CategoryJson,
    CategorySchemeJson,
    CategorySchemeMapJson,
    CodeJson,
    CodelistExtensionJson,
    CodelistJson,
    CodeSelectionJson,
    ComponentValueSetJson,
    ConceptJson,
    ConceptSchemeJson,
    ConceptSchemeMapJson,
    ConstraintAttachmentJson,
    ContactJson,
    CubeRegionJson,
    DataConstraintJson,
    DataConsumerSchemeJson,
    DataflowJson,
    DataProviderSchemeJson,
    DataStructureJson,
    DateMapJson,
    DimensionJson,
    Format,
    GeoFeatureSetCodeJson,
    GeographicCodelistJson,
    GeoGridCodeJson,
    GeoGridCodelistJson,
    GroupJson,
    HierarchicalCodeJson,
    HierarchyAssociationJson,
    HierarchyJson,
    ItemMapJson,
    LevelJson,
    MaintainableJson,
    MapEndpointJson,
    MeasureJson,
    MemberValueJson,
    MetadataAttributeJson,
    MetadataAttributeUsageJson,
    MetadataflowJson,
    MetadataProviderSchemeJson,
    MetadataProvisionAgreementJson,
    MetadataStructureJson,
    OrganisationJson,
    OrganisationSchemeMapJson,
    OrganisationUnitJson,
    OrganisationUnitSchemeJson,
    ProvisionAgreementJson,
    Representation,
    RepresentationMapJson,
    StructureMapJson,
    TimeDimensionJson,
    TimePeriodRangeJson,
    TimeRangeJson,
    ValueJson,
    ValueListJson,
    ValueMappingJson,
    WireRegionValue,
    read_links,
    read_uri,
)
from sdmxlib.model.annotations import Annotations
from sdmxlib.model.category import Categorisation, Category, CategoryScheme
from sdmxlib.model.codelist import (
    CascadeSelection,
    Code,
    Codelist,
    CodelistExtension,
    CodeSelection,
    GeoFeatureSetCode,
    GeographicCodelist,
    GeoGridCode,
    GeoGridCodelist,
    InclusionMode,
    MemberValue,
)
from sdmxlib.model.collections import ItemList
from sdmxlib.model.concept import Concept, ConceptScheme, ISOConceptReference
from sdmxlib.model.constraint import (
    ComponentValueSet,
    ConstraintAttachment,
    CubeRegion,
    DataConstraint,
    DataKey,
    DataKeySet,
    KeyValue,
    QueryableDataSource,
    RegionValue,
    TimeBound,
    TimeRange,
)
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import (
    AttachmentLevel,
    Attribute,
    DataStructure,
    Dimension,
    Group,
    Measure,
    MetadataAttributeUsage,
    TimeDimension,
)
from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy, HierarchyAssociation, Level
from sdmxlib.model.link import Link
from sdmxlib.model.mapping import (
    CategorySchemeMap,
    ComponentMap,
    ConceptSchemeMap,
    DatePatternMap,
    EpochMap,
    EpochPeriod,
    FixedValueMap,
    FrequencyFormatMapping,
    ItemMap,
    MapDataType,
    MapEndpoint,
    OrganisationSchemeMap,
    RepresentationMap,
    RepresentationMapping,
    ResolvePeriod,
    SourceValue,
    StructureMap,
)
from sdmxlib.model.metadata_provision import MetadataProvisionAgreement
from sdmxlib.model.metadataflow import Metadataflow
from sdmxlib.model.metadatastructure import MetadataAttribute, MetadataStructure, codelist_representation
from sdmxlib.model.organisation import (
    Agency,
    AgencyScheme,
    Contact,
    DataConsumer,
    DataConsumerScheme,
    DataProvider,
    DataProviderScheme,
    MetadataProvider,
    MetadataProviderScheme,
    OrganisationUnit,
    OrganisationUnitScheme,
)
from sdmxlib.model.provision import ProvisionAgreement
from sdmxlib.model.ref import Ref
from sdmxlib.model.representation import Facet, SentinelValue
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.model.validation import SdmxConformanceError
from sdmxlib.model.valuelist import ValueItem, ValueList


def parse_dt(s: str | None) -> datetime | None:
    """ISO-8601 string (as emitted by the write side) → datetime, or None."""
    return to_datetime(s, what="a validity timestamp") if s else None


#: What ingest holds for a construct a document omits and the model cannot
#: (#474). `_losses.ASSUMPTIONS` declares each, so the read records it.
ASSUMED_VERSION = "1.0"
ASSUMED_EPOCH_PERIOD = "day"


def version_of(dto: MaintainableJson) -> str:
    """`dto`'s version; un-versioned, which SDMX-JSON 2.0 allows and the model cannot hold, as 1.0 (#474)."""
    return ASSUMED_VERSION if dto.version is None else dto.version


# ── Codelist ─────────────────────────────────────────────────────────────────


def from_code(c: CodeJson) -> Code:
    return Code(
        id=c.id,
        name=ingest_istr(c.name, c.names),
        description=ingest_istr(c.description, c.descriptions),
        parent_id=c.parent,
        annotations=ingest_annotations(c.annotations),
        uri=read_uri(c.links),
        links=read_links(c.links),
    )


def from_geo_feature_set_code(c: GeoFeatureSetCodeJson) -> GeoFeatureSetCode:
    return GeoFeatureSetCode(
        id=c.id,
        name=ingest_istr(c.name, c.names),
        description=ingest_istr(c.description, c.descriptions),
        parent_id=c.parent,
        annotations=ingest_annotations(c.annotations),
        uri=read_uri(c.links),
        links=read_links(c.links),
        value=c.value,
    )


def from_geo_grid_code(c: GeoGridCodeJson) -> GeoGridCode:
    return GeoGridCode(
        id=c.id,
        name=ingest_istr(c.name, c.names),
        description=ingest_istr(c.description, c.descriptions),
        parent_id=c.parent,
        annotations=ingest_annotations(c.annotations),
        uri=read_uri(c.links),
        links=read_links(c.links),
        geo_cell=c.geo_cell,
    )


def from_member_value(mv: str | MemberValueJson) -> MemberValue:
    """MemberValueType element → MemberValue.

    Bare string → id with no cascade; object → id + the `CascadeSelection`
    decoded from ``cascadeValues`` (``True``/``False``/``"excluderoot"``).
    """
    if isinstance(mv, str):
        return MemberValue(value=mv)
    cascade: CascadeSelection | None = None
    cv = mv.cascade_values
    if cv is True:
        cascade = CascadeSelection.INCLUDE_DESCENDANTS
    elif cv is False:
        cascade = CascadeSelection.SELF_ONLY
    elif cv == "excluderoot":
        cascade = CascadeSelection.EXCLUDE_ROOT
    return MemberValue(value=mv.value, cascade=cascade)


def from_code_selection(sel: CodeSelectionJson, mode: InclusionMode) -> CodeSelection:
    # Never None once decoded: `CodeSelectionJson.__post_init__` requires it.
    return CodeSelection(mode=mode, members=tuple(from_member_value(m) for m in sel.member_values or ()))


def from_codelist_extension(ext: CodelistExtensionJson) -> CodelistExtension | None:
    """Ingest a `CodelistExtensionJson`, or None if its ``codelist`` isn't a URN.

    Extensions are leaves (the extended codelist isn't resolved against the
    message), so URN resolution uses an empty index — a URN string yields an
    unresolved Ref, a non-URN drops the extension (matching the old reader).
    """
    ref = resolve_urn(ext.codelist, Codelist)
    if ref is None:
        return None
    # xs:choice: at most one selection key is present.
    selection: CodeSelection | None = None
    if ext.inclusive_code_selection is not None:
        selection = from_code_selection(ext.inclusive_code_selection, InclusionMode.INCLUDE)
    elif ext.exclusive_code_selection is not None:
        selection = from_code_selection(ext.exclusive_code_selection, InclusionMode.EXCLUDE)
    return CodelistExtension(codelist=ref, selection=selection, prefix=ext.prefix)


def from_codelist(clj: CodelistJson, maintainer: Agency) -> Codelist:
    """Ingest a `CodelistJson`. *maintainer* (agency lookup) is passed in."""
    extensions = tuple(e for ext in clj.codelist_extensions or [] if (e := from_codelist_extension(ext)) is not None)
    return Codelist(
        id=clj.id,
        maintainer=maintainer,
        version=version_of(clj),
        name=ingest_istr(clj.name, clj.names),
        description=ingest_istr(clj.description, clj.descriptions),
        codes=ItemList([from_code(c) for c in clj.codes or []]),
        extensions=extensions,
        annotations=ingest_annotations(clj.annotations),
        is_external_reference=bool(clj.is_external_reference),
        is_partial_language=bool(clj.is_partial_language),
        is_partial=bool(clj.is_partial),
        uri=read_uri(clj.links),
        links=read_links(clj.links),
        valid_from=parse_dt(clj.valid_from),
        valid_to=parse_dt(clj.valid_to),
    )


def from_geographic_codelist(clj: GeographicCodelistJson, maintainer: Agency) -> GeographicCodelist:
    """Ingest a `GeographicCodelistJson`, as `from_codelist` does a plain one."""
    extensions = tuple(e for ext in clj.codelist_extensions or [] if (e := from_codelist_extension(ext)) is not None)
    return GeographicCodelist(
        id=clj.id,
        maintainer=maintainer,
        version=version_of(clj),
        name=ingest_istr(clj.name, clj.names),
        description=ingest_istr(clj.description, clj.descriptions),
        codes=ItemList([from_geo_feature_set_code(c) for c in clj.geo_feature_set_codes or []]),
        extensions=extensions,
        annotations=ingest_annotations(clj.annotations),
        is_external_reference=bool(clj.is_external_reference),
        is_partial_language=bool(clj.is_partial_language),
        is_partial=bool(clj.is_partial),
        uri=read_uri(clj.links),
        links=read_links(clj.links),
        valid_from=parse_dt(clj.valid_from),
        valid_to=parse_dt(clj.valid_to),
    )


def from_geo_grid_codelist(clj: GeoGridCodelistJson, maintainer: Agency) -> GeoGridCodelist:
    """Ingest a `GeoGridCodelistJson`, as `from_codelist` does a plain one."""
    extensions = tuple(e for ext in clj.codelist_extensions or [] if (e := from_codelist_extension(ext)) is not None)
    return GeoGridCodelist(
        id=clj.id,
        maintainer=maintainer,
        version=version_of(clj),
        name=ingest_istr(clj.name, clj.names),
        description=ingest_istr(clj.description, clj.descriptions),
        codes=ItemList([from_geo_grid_code(c) for c in clj.geo_grid_codes or []]),
        extensions=extensions,
        annotations=ingest_annotations(clj.annotations),
        is_external_reference=bool(clj.is_external_reference),
        is_partial_language=bool(clj.is_partial_language),
        is_partial=bool(clj.is_partial),
        uri=read_uri(clj.links),
        links=read_links(clj.links),
        valid_from=parse_dt(clj.valid_from),
        valid_to=parse_dt(clj.valid_to),
        grid_definition=clj.grid_definition or "",
    )


# ── Representation (shared: concept core_representation; DSD local_representation) ─


def from_format(f: Format) -> Facet:
    return Facet(
        type=f.data_type,
        max_length=f.max_length,
        min_length=f.min_length,
        max_value=f.max_value,
        min_value=f.min_value,
        decimals=f.decimals,
        pattern=f.pattern,
        start_value=f.start_value,
        end_value=f.end_value,
        is_sequence=f.is_sequence,
        is_multilingual=bool(f.is_multilingual),
        interval=f.interval,
        time_interval=f.time_interval,
        start_time=f.start_time,
        end_time=f.end_time,
        sentinel_values=tuple(
            SentinelValue(
                value=str(s.value) if s.value is not None else "",
                name=ingest_istr(s.name, s.names),
                description=ingest_istr(s.description, s.descriptions),
            )
            for s in f.sentinel_values or []
        ),
    )


def from_enumeration_format(rep: Representation | None) -> Facet | None:
    """The facet constraining an enumerated representation's codes, if any."""
    if rep is None or rep.enumeration_format is None:
        return None
    return from_format(rep.enumeration_format)


def resolve_enumeration(urn_val: str) -> Ref[Codelist] | Ref[ValueList] | None:
    """An ``AnyCodelistReferenceType`` URN, resolved as the class it names (#422)."""
    if urn_val.split("=", 1)[0].endswith(".ValueList"):
        return resolve_urn(urn_val, ValueList)
    return resolve_urn(urn_val, Codelist)


def from_representation(rep: Representation | None) -> Ref[Codelist] | Ref[ValueList] | Facet | None:
    if rep is None:
        return None
    if rep.enumeration is not None:
        return resolve_enumeration(rep.enumeration)
    if rep.format is not None:
        return from_format(rep.format)
    return None


# ── ConceptScheme ────────────────────────────────────────────────────────────


def from_value_list(vlj: ValueListJson, maintainer: Agency) -> ValueList:
    return ValueList(
        id=vlj.id,
        maintainer=maintainer,
        version=version_of(vlj),
        name=ingest_istr(vlj.name, vlj.names),
        description=ingest_istr(vlj.description, vlj.descriptions),
        items=ItemList(
            [
                ValueItem(
                    id=item.id,
                    name=ingest_istr(item.name, item.names),
                    description=ingest_istr(item.description, item.descriptions),
                    annotations=ingest_annotations(item.annotations),
                )
                for item in vlj.value_items or []
            ]
        ),
        annotations=ingest_annotations(vlj.annotations),
        is_external_reference=bool(vlj.is_external_reference),
        is_partial_language=bool(vlj.is_partial_language),
        uri=read_uri(vlj.links),
        links=read_links(vlj.links),
        valid_from=parse_dt(vlj.valid_from),
        valid_to=parse_dt(vlj.valid_to),
    )


def from_concept(c: ConceptJson) -> Concept:
    min_occurs, max_occurs = _occurs(c.core_representation)
    return Concept(
        id=c.id,
        name=ingest_istr(c.name, c.names),
        description=ingest_istr(c.description, c.descriptions),
        parent_id=c.parent,
        core_representation=from_representation(c.core_representation),
        enumeration_format=from_enumeration_format(c.core_representation),
        min_occurs=min_occurs,
        max_occurs=max_occurs,
        iso_concept_reference=(
            None
            if (iso := c.iso_concept_reference) is None
            else ISOConceptReference(
                agency=iso.concept_agency, concept_scheme_id=iso.concept_scheme_id, concept_id=iso.concept_id
            )
        ),
        annotations=ingest_annotations(c.annotations),
        uri=read_uri(c.links),
        links=read_links(c.links),
    )


def from_concept_scheme(csj: ConceptSchemeJson, maintainer: Agency) -> ConceptScheme:
    return ConceptScheme(
        id=csj.id,
        maintainer=maintainer,
        version=version_of(csj),
        name=ingest_istr(csj.name, csj.names),
        description=ingest_istr(csj.description, csj.descriptions),
        concepts=ItemList([from_concept(c) for c in csj.concepts or []]),
        annotations=ingest_annotations(csj.annotations),
        is_external_reference=bool(csj.is_external_reference),
        is_partial_language=bool(csj.is_partial_language),
        is_partial=bool(csj.is_partial),
        uri=read_uri(csj.links),
        links=read_links(csj.links),
        valid_from=parse_dt(csj.valid_from),
        valid_to=parse_dt(csj.valid_to),
    )


# ── CategoryScheme ───────────────────────────────────────────────────────────


def from_category(cat: CategoryJson) -> Category:
    return Category(
        id=cat.id,
        name=ingest_istr(cat.name, cat.names),
        description=ingest_istr(cat.description, cat.descriptions),
        categories=ItemList([from_category(c) for c in cat.categories or []]),
        annotations=ingest_annotations(cat.annotations),
        uri=read_uri(cat.links),
        links=read_links(cat.links),
    )


def from_category_scheme(csj: CategorySchemeJson, maintainer: Agency) -> CategoryScheme:
    return CategoryScheme(
        id=csj.id,
        maintainer=maintainer,
        version=version_of(csj),
        name=ingest_istr(csj.name, csj.names),
        description=ingest_istr(csj.description, csj.descriptions),
        categories=ItemList([from_category(c) for c in csj.categories or []]),
        annotations=ingest_annotations(csj.annotations),
        is_external_reference=bool(csj.is_external_reference),
        is_partial_language=bool(csj.is_partial_language),
        is_partial=bool(csj.is_partial),
        uri=read_uri(csj.links),
        links=read_links(csj.links),
        valid_from=parse_dt(csj.valid_from),
        valid_to=parse_dt(csj.valid_to),
    )


# ── DataStructure components + Dataflow ──────────────────────────────────────


def _role_ids(role_urns: list[str] | None) -> tuple[str, ...]:
    """ConceptRoles URN strings → tuple of role item ids (inverse of the write).

    Raises:
        SdmxConformanceError: If a role is not a concept's URN. It was skipped,
            and the role silently lost (#458).
    """
    if not role_urns:
        return ()
    out: list[str] = []
    for raw in role_urns:
        try:
            urn = SdmxUrn.parse(raw)
        except ValueError, TypeError:
            urn = None
        if urn is None or urn.item_id is None:
            element, detail = "conceptRoles", f"{raw!r} is not a concept's URN"
            raise SdmxConformanceError(element, detail)
        out.append(urn.item_id)
    return tuple(out)


def from_attachment(
    ar: AttributeRelationship | None,
) -> tuple[AttachmentLevel, list[str] | None, frozenset[str], str | None]:
    """AttributeRelationship DTO → (level, related_dimensions, optional_dimensions, related_group).

    The observation branch yields no measure id (#360). SDMX-JSON follows the
    3.0 information model, where an attribute relationship has no measure
    reference to read — this returned a hard-coded ``"OBS_VALUE"``.
    """
    if ar is None:
        # Required by the schema; one a document omits varies with nothing,
        # read as dataset-level as the SDMX-ML readers read it (#421).
        return AttachmentLevel.DATASET, None, frozenset(), None
    if ar.observation is not None or ar.primary_measure is not None:
        return AttachmentLevel.OBSERVATION, None, frozenset(), None
    if ar.dataflow is not None or ar.none is not None:
        return AttachmentLevel.DATASET, None, frozenset(), None
    if ar.dimensions:
        # `areDimensionsOptional` is parallel to `dimensions` (#422).
        flags = ar.are_dimensions_optional or []
        optional = frozenset(d for d, flag in zip(ar.dimensions, flags, strict=False) if flag)
        return AttachmentLevel.SERIES, list(ar.dimensions), optional, None
    if ar.group is not None:
        return AttachmentLevel.GROUP, None, frozenset(), ar.group
    return AttachmentLevel.DATASET, None, frozenset(), None


def _occurs(rep: Representation | None) -> tuple[int, int | None]:
    """A representation's `minOccurs` / `maxOccurs` (#422); None is unbounded."""
    if rep is None:
        return 1, 1
    min_occurs = 1 if rep.min_occurs is None else rep.min_occurs
    match rep.max_occurs:
        case None:
            return min_occurs, 1
        case "unbounded":
            return min_occurs, None
        case int() as count:
            return min_occurs, count
        case other:
            detail = f"{other!r} is neither a count nor 'unbounded'"
            element = "maxOccurs"
            raise SdmxConformanceError(element, detail)


def component_id_of(stated: str | None, concept: Ref[Concept] | None, where: str) -> str:
    """A component's id, or its concept's when it states none: the schema's rule (#477).

    Raises:
        SdmxConformanceError: If the component states neither an id nor a concept to take one from.
    """
    if stated is not None:
        return stated
    if concept is not None and concept.urn.item_id:
        return concept.urn.item_id
    detail = "a component without an id takes its concept's, and this one has no conceptIdentity"
    raise SdmxConformanceError(where, detail)


def from_dimension(d: DimensionJson) -> Dimension:
    concept = resolve_urn(d.concept_identity, Concept)
    return Dimension(
        id=component_id_of(d.id, concept, "dimension"),
        concept=concept,
        representation=from_representation(d.local_representation),
        enumeration_format=from_enumeration_format(d.local_representation),
        annotations=ingest_annotations(d.annotations),
        uri=read_uri(d.links),
        links=read_links(d.links),
        concept_role=_role_ids(d.concept_roles),
    )


def from_time_dimension(td: TimeDimensionJson) -> TimeDimension:
    """A time dimension; refused without the concept the schema requires (#474).

    A missing format is not refused: its type has a default the model holds
    (#507), and `_losses.ASSUMPTIONS` records that the read supplied it.

    Raises:
        SdmxConformanceError: If the concept is missing.
    """
    if td.concept_identity is None:
        element, detail = "timeDimension", "the schema requires a conceptIdentity"
        raise SdmxConformanceError(element, detail)
    rep = from_representation(td.local_representation) if td.local_representation is not None else None
    return TimeDimension(
        id=td.id or "TIME_PERIOD",
        concept=resolve_urn(td.concept_identity, Concept),
        representation=rep if isinstance(rep, Facet) else None,
        annotations=ingest_annotations(td.annotations),
        uri=read_uri(td.links),
        links=read_links(td.links),
    )


def from_attribute(a: AttributeJson) -> Attribute:
    level, dims, optional, group = from_attachment(a.attribute_relationship)
    min_occurs, max_occurs = _occurs(a.local_representation)
    concept = resolve_urn(a.concept_identity, Concept)
    return Attribute(
        id=component_id_of(a.id, concept, "attribute"),
        usage=a.usage or a.assignment_status,
        attachment=level,
        related_dimensions=dims,
        optional_dimensions=optional,
        related_measures=a.measure_relationship,
        related_group=group,
        concept=concept,
        representation=from_representation(a.local_representation),
        enumeration_format=from_enumeration_format(a.local_representation),
        min_occurs=min_occurs,
        max_occurs=max_occurs,
        annotations=ingest_annotations(a.annotations),
        uri=read_uri(a.links),
        links=read_links(a.links),
        concept_role=_role_ids(a.concept_roles),
    )


def from_metadata_attribute_usage(u: MetadataAttributeUsageJson) -> MetadataAttributeUsage:
    level, dims, optional, group = from_attachment(u.attribute_relationship)
    return MetadataAttributeUsage(
        id=u.metadata_attribute_reference,
        attachment=level,
        related_dimensions=dims,
        optional_dimensions=optional,
        related_group=group,
        annotations=ingest_annotations(u.annotations),
        uri=read_uri(u.links),
        links=read_links(u.links),
    )


def from_measure(m: MeasureJson) -> Measure:
    min_occurs, max_occurs = _occurs(m.local_representation)
    concept = resolve_urn(m.concept_identity, Concept)
    return Measure(
        # Not 2.1's fixed OBS_VALUE: a measure is a component like any other (#477).
        id=component_id_of(m.id, concept, "measure"),
        concept=concept,
        representation=from_representation(m.local_representation),
        enumeration_format=from_enumeration_format(m.local_representation),
        min_occurs=min_occurs,
        max_occurs=max_occurs,
        usage=m.usage,
        annotations=ingest_annotations(m.annotations),
        uri=read_uri(m.links),
        links=read_links(m.links),
        concept_role=_role_ids(m.concept_roles),
    )


def from_group(g: GroupJson) -> Group:
    # `uri` via links, as `from_category` already did — the writer now fills
    # them and this is the other half (#355).
    return Group(
        id=g.id,
        dimension_ids=list(g.group_dimensions),
        annotations=ingest_annotations(g.annotations),
        uri=read_uri(g.links),
    )


def from_data_structure(dsd: DataStructureJson, maintainer: Agency) -> DataStructure:
    dims: list[Dimension] = []
    time_dim: TimeDimension | None = None
    attrs: list[Attribute] = []
    measures: list[Measure] = []
    groups: list[Group] = []
    usages: list[MetadataAttributeUsage] = []
    # Each list's annotations, as decoded: the lists themselves are not modelled.
    dimension_list_annotations: list[Annotation] | None = None
    attribute_list_annotations: list[Annotation] | None = None
    measure_list_annotations: list[Annotation] | None = None
    list_uris: dict[str, str | None] = {"dimension": None, "attribute": None, "measure": None}
    list_links: dict[str, tuple[Link, ...]] = {"dimension": (), "attribute": (), "measure": ()}
    comps = dsd.data_structure_components
    if comps is not None:
        if comps.dimension_list is not None:
            dimension_list_annotations = comps.dimension_list.annotations
            list_uris["dimension"] = read_uri(comps.dimension_list.links)
            list_links["dimension"] = read_links(comps.dimension_list.links)
            stated = comps.dimension_list.dimensions
            dims = [from_dimension(d) for d in stated]
            # Named by the id each dimension *has*: one that states none takes
            # its concept's (#477), and was named '' in the refusal.
            check_positions([(dim.id, d.position) for dim, d in zip(dims, stated, strict=True)], element="position")
            if comps.dimension_list.time_dimension is not None:
                time_dim = from_time_dimension(comps.dimension_list.time_dimension)
        if comps.attribute_list is not None:
            attribute_list_annotations = comps.attribute_list.annotations
            list_uris["attribute"] = read_uri(comps.attribute_list.links)
            list_links["attribute"] = read_links(comps.attribute_list.links)
            attrs = [from_attribute(a) for a in comps.attribute_list.attributes]
            usages = [from_metadata_attribute_usage(u) for u in comps.attribute_list.metadata_attribute_usages or []]
        if comps.measure_list is not None:
            ml = comps.measure_list
            measure_list_annotations = ml.annotations
            list_uris["measure"] = read_uri(ml.links)
            list_links["measure"] = read_links(ml.links)
            raw = ml.measures or ([ml.primary_measure] if ml.primary_measure is not None else [])
            measures = [from_measure(m) for m in raw]
        if comps.groups:
            groups = [from_group(g) for g in comps.groups]
    dimension_ids = [d.id for d in dims] + ([time_dim.id] if time_dim is not None else [])
    attrs = match_dimension_case(attrs, dimension_ids)
    usages = match_dimension_case(usages, dimension_ids)
    return DataStructure(
        id=dsd.id,
        maintainer=maintainer,
        version=version_of(dsd),
        name=ingest_istr(dsd.name, dsd.names),
        description=ingest_istr(dsd.description, dsd.descriptions),
        dimensions=ItemList(dims),
        time_dimension=time_dim,
        attributes=ItemList(attrs),
        measures=ItemList(measures),
        groups=ItemList(groups) if groups else None,
        dimension_descriptor_annotations=ingest_annotations(dimension_list_annotations),
        attribute_descriptor_annotations=ingest_annotations(attribute_list_annotations),
        measure_descriptor_annotations=ingest_annotations(measure_list_annotations),
        dimension_descriptor_uri=list_uris["dimension"],
        dimension_descriptor_links=list_links["dimension"],
        attribute_descriptor_uri=list_uris["attribute"],
        attribute_descriptor_links=list_links["attribute"],
        measure_descriptor_uri=list_uris["measure"],
        measure_descriptor_links=list_links["measure"],
        evolving_structure=bool(dsd.evolving_structure),
        metadata=resolve_urn(dsd.metadata, MetadataStructure),
        metadata_attributes=ItemList(usages),
        annotations=ingest_annotations(dsd.annotations),
        is_external_reference=bool(dsd.is_external_reference),
        is_partial_language=bool(dsd.is_partial_language),
        uri=read_uri(dsd.links),
        links=read_links(dsd.links),
        valid_from=parse_dt(dsd.valid_from),
        valid_to=parse_dt(dsd.valid_to),
    )


def from_dataflow(df: DataflowJson, maintainer: Agency) -> Dataflow:
    return Dataflow(
        id=df.id,
        maintainer=maintainer,
        version=version_of(df),
        name=ingest_istr(df.name, df.names),
        description=ingest_istr(df.description, df.descriptions),
        annotations=ingest_annotations(df.annotations),
        is_external_reference=bool(df.is_external_reference),
        is_partial_language=bool(df.is_partial_language),
        uri=read_uri(df.links),
        links=read_links(df.links),
        structure=resolve_urn(df.structure, DataStructure),
        valid_from=parse_dt(df.valid_from),
        valid_to=parse_dt(df.valid_to),
    )


# ── Organisation schemes, Categorisation, ProvisionAgreement ─────────────────


def from_contact(c: ContactJson) -> Contact:
    return Contact(
        id=c.id,
        name=ingest_istr(c.name, c.names),
        department=ingest_istr(c.department, c.departments),
        role=ingest_istr(c.role, c.roles),
        phones=tuple(c.telephones or []),
        faxes=tuple(c.faxes or []),
        x400s=tuple(c.x400s or []),
        uris=tuple(c.uris or []),
        emails=tuple(c.emails or []),
    )


def from_agency(o: OrganisationJson) -> Agency:
    return Agency(
        id=o.id,
        name=ingest_istr(o.name, o.names),
        description=ingest_istr(o.description, o.descriptions),
        contacts=tuple(from_contact(c) for c in o.contacts or []),
        annotations=ingest_annotations(o.annotations),
        uri=read_uri(o.links),
        links=read_links(o.links),
    )


def from_data_provider(o: OrganisationJson) -> DataProvider:
    return DataProvider(
        id=o.id,
        name=ingest_istr(o.name, o.names),
        description=ingest_istr(o.description, o.descriptions),
        contacts=tuple(from_contact(c) for c in o.contacts or []),
        annotations=ingest_annotations(o.annotations),
        uri=read_uri(o.links),
        links=read_links(o.links),
    )


def from_metadata_provider(o: OrganisationJson) -> MetadataProvider:
    return MetadataProvider(
        id=o.id,
        name=ingest_istr(o.name, o.names),
        description=ingest_istr(o.description, o.descriptions),
        contacts=tuple(from_contact(c) for c in o.contacts or []),
        annotations=ingest_annotations(o.annotations),
        uri=read_uri(o.links),
        links=read_links(o.links),
    )


def from_agency_scheme(s: AgencySchemeJson) -> AgencyScheme:
    # Org schemes DEFINE the agencies, so maintainer is the raw agencyID string
    # (not resolved against the agencies map, unlike other maintainables).
    return AgencyScheme(
        id=s.id,
        maintainer=s.agency_id,
        version=version_of(s),
        name=ingest_istr(s.name, s.names),
        description=ingest_istr(s.description, s.descriptions),
        agencies=ItemList([from_agency(o) for o in s.agencies or []]),
        annotations=ingest_annotations(s.annotations),
        is_external_reference=bool(s.is_external_reference),
        is_partial_language=bool(s.is_partial_language),
        is_partial=bool(s.is_partial),
        uri=read_uri(s.links),
        links=read_links(s.links),
        valid_from=parse_dt(s.valid_from),
        valid_to=parse_dt(s.valid_to),
    )


def from_data_provider_scheme(s: DataProviderSchemeJson) -> DataProviderScheme:
    return DataProviderScheme(
        id=s.id,
        maintainer=s.agency_id,
        version=version_of(s),
        name=ingest_istr(s.name, s.names),
        description=ingest_istr(s.description, s.descriptions),
        providers=ItemList([from_data_provider(o) for o in s.data_providers or []]),
        annotations=ingest_annotations(s.annotations),
        is_external_reference=bool(s.is_external_reference),
        is_partial_language=bool(s.is_partial_language),
        is_partial=bool(s.is_partial),
        uri=read_uri(s.links),
        links=read_links(s.links),
        valid_from=parse_dt(s.valid_from),
        valid_to=parse_dt(s.valid_to),
    )


def from_metadata_provider_scheme(s: MetadataProviderSchemeJson) -> MetadataProviderScheme:
    return MetadataProviderScheme(
        id=s.id,
        maintainer=s.agency_id,
        version=version_of(s),
        name=ingest_istr(s.name, s.names),
        description=ingest_istr(s.description, s.descriptions),
        providers=ItemList([from_metadata_provider(o) for o in s.metadata_providers or []]),
        annotations=ingest_annotations(s.annotations),
        is_external_reference=bool(s.is_external_reference),
        is_partial_language=bool(s.is_partial_language),
        is_partial=bool(s.is_partial),
        uri=read_uri(s.links),
        links=read_links(s.links),
        valid_from=parse_dt(s.valid_from),
        valid_to=parse_dt(s.valid_to),
    )


def from_data_consumer(o: OrganisationJson) -> DataConsumer:
    return DataConsumer(
        id=o.id,
        name=ingest_istr(o.name, o.names),
        description=ingest_istr(o.description, o.descriptions),
        contacts=tuple(from_contact(c) for c in o.contacts or []),
        annotations=ingest_annotations(o.annotations),
        uri=read_uri(o.links),
        links=read_links(o.links),
    )


def from_organisation_unit(o: OrganisationUnitJson) -> OrganisationUnit:
    return OrganisationUnit(
        id=o.id,
        name=ingest_istr(o.name, o.names),
        description=ingest_istr(o.description, o.descriptions),
        contacts=tuple(from_contact(c) for c in o.contacts or []),
        annotations=ingest_annotations(o.annotations),
        parent_id=o.parent,
        uri=read_uri(o.links),
        links=read_links(o.links),
    )


def from_data_consumer_scheme(s: DataConsumerSchemeJson) -> DataConsumerScheme:
    return DataConsumerScheme(
        id=s.id,
        maintainer=s.agency_id,
        version=version_of(s),
        name=ingest_istr(s.name, s.names),
        description=ingest_istr(s.description, s.descriptions),
        consumers=ItemList([from_data_consumer(o) for o in s.data_consumers or []]),
        annotations=ingest_annotations(s.annotations),
        is_external_reference=bool(s.is_external_reference),
        is_partial_language=bool(s.is_partial_language),
        is_partial=bool(s.is_partial),
        uri=read_uri(s.links),
        links=read_links(s.links),
        valid_from=parse_dt(s.valid_from),
        valid_to=parse_dt(s.valid_to),
    )


def from_organisation_unit_scheme(s: OrganisationUnitSchemeJson) -> OrganisationUnitScheme:
    """An organisation unit scheme, refused at a version SDMX-JSON does not allow it (#469).

    Raises:
        SdmxConformanceError: If its version is not ``1.0``. SDMX-JSON derives
            every organisation scheme from `MaintainableWithoutVersionType`,
            whose version is the const ``1.0``. Only SDMX-ML 2.1 carries
            another, and the model holds that one, so a reader has to refuse
            here what the schema refuses.
    """
    version = version_of(s)
    if version != ASSUMED_VERSION:
        element = "organisationUnitScheme"
        detail = f"an organisation unit scheme's version is fixed to {ASSUMED_VERSION!r} in SDMX-JSON, got {version!r}"
        raise SdmxConformanceError(element, detail)
    return OrganisationUnitScheme(
        id=s.id,
        maintainer=s.agency_id,
        version=version,
        name=ingest_istr(s.name, s.names),
        description=ingest_istr(s.description, s.descriptions),
        organisation_units=ItemList([from_organisation_unit(o) for o in s.organisation_units or []]),
        annotations=ingest_annotations(s.annotations),
        is_external_reference=bool(s.is_external_reference),
        is_partial_language=bool(s.is_partial_language),
        is_partial=bool(s.is_partial),
        uri=read_uri(s.links),
        links=read_links(s.links),
        valid_from=parse_dt(s.valid_from),
        valid_to=parse_dt(s.valid_to),
    )


def from_categorisation(cat: CategorisationJson, maintainer: Agency) -> Categorisation:
    return Categorisation(
        id=cat.id,
        maintainer=maintainer,
        version=version_of(cat),
        name=ingest_istr(cat.name, cat.names),
        description=ingest_istr(cat.description, cat.descriptions),
        annotations=ingest_annotations(cat.annotations),
        is_external_reference=bool(cat.is_external_reference),
        is_partial_language=bool(cat.is_partial_language),
        uri=read_uri(cat.links),
        links=read_links(cat.links),
        source=resolve_any_urn(cat.source),
        target=resolve_any_urn(cat.target),
        valid_from=parse_dt(cat.valid_from),
        valid_to=parse_dt(cat.valid_to),
    )


def from_provision_agreement(pa: ProvisionAgreementJson, maintainer: Agency) -> ProvisionAgreement:
    return ProvisionAgreement(
        id=pa.id,
        maintainer=maintainer,
        version=version_of(pa),
        name=ingest_istr(pa.name, pa.names),
        description=ingest_istr(pa.description, pa.descriptions),
        annotations=ingest_annotations(pa.annotations),
        is_external_reference=bool(pa.is_external_reference),
        is_partial_language=bool(pa.is_partial_language),
        uri=read_uri(pa.links),
        links=read_links(pa.links),
        dataflow=resolve_urn(pa.dataflow, Dataflow),
        data_provider=resolve_any_urn(pa.data_provider),
        valid_from=parse_dt(pa.valid_from),
        valid_to=parse_dt(pa.valid_to),
    )


# ── DataConstraint ───────────────────────────────────────────────────────────


def from_constraint_attachment(ca: ConstraintAttachmentJson | None) -> ConstraintAttachment:
    """Every alternative and artefact of a `constraintAttachment` (#422).

    Until #422 the first of each array was kept and the data sources dropped.
    """
    if ca is None:
        return ConstraintAttachment()

    def refs(urns: list[str] | None) -> tuple[Ref[object], ...]:
        found = (resolve_any_urn(urn) for urn in urns or ())
        return tuple(ref for ref in found if ref is not None)

    return ConstraintAttachment(
        data_provider=resolve_any_urn(ca.data_provider) if ca.data_provider is not None else None,
        simple_data_sources=tuple(ca.simple_data_sources or ()),
        data_structures=refs(ca.data_structures),
        dataflows=refs(ca.dataflows),
        provision_agreements=refs(ca.provision_agreements),
        queryable_data_sources=tuple(
            QueryableDataSource(
                data_url=source.data_url,
                is_rest_datasource=source.is_rest_datasource,
                is_web_service_datasource=source.is_web_service_datasource,
                wsdl_url=source.wsdl_url,
                wadl_url=source.wadl_url,
            )
            for source in ca.queryable_data_sources or ()
        ),
    )


def _from_bound(bound: TimePeriodRangeJson | None) -> TimeBound | None:
    return None if bound is None else TimeBound(period=bound.period, inclusive=bound.is_inclusive)


def from_time_range(time_range: TimeRangeJson | None) -> TimeRange | None:
    """`afterPeriod` is a start, `beforePeriod` an end, as in SDMX-ML (#433)."""
    if time_range is None:
        return None
    return TimeRange(
        start=_from_bound(time_range.start_period or time_range.after_period),
        end=_from_bound(time_range.end_period or time_range.before_period),
        valid_from=time_range.valid_from,
        valid_to=time_range.valid_to,
    )


def from_wire_value(value: WireRegionValue) -> RegionValue:
    """A region value, bare or in its object form (#422)."""
    if isinstance(value, str):
        return RegionValue(value)
    return RegionValue(
        value=value.value,
        cascade=_cascade(value),
        lang=value.lang,
        valid_from=value.valid_from,
        valid_to=value.valid_to,
    )


def _cascade(value: ValueJson) -> CascadeSelection | None:
    """`CascadeSelectionType`: a boolean, or the string "excluderoot"."""
    match value.cascade_values:
        case None:
            return None
        case bool() as flag:
            return CascadeSelection.INCLUDE_DESCENDANTS if flag else CascadeSelection.SELF_ONLY
        case text:
            return to_enum(CascadeSelection, text, what="cascadeValues")


def from_component_value_set(c: ComponentValueSetJson) -> ComponentValueSet:
    """A component's value set — a cube region's or a data key's (#422)."""
    return ComponentValueSet(
        component_id=c.id,
        values=frozenset(from_wire_value(v) for v in c.values),
        time_range=from_time_range(c.time_range),
        include=c.include is not False,
        remove_prefix=bool(c.remove_prefix),
    )


def from_cube_region(r: CubeRegionJson) -> CubeRegion:
    """One cube region; a value set's ``include: false`` excludes its values (#422)."""
    keys = [
        KeyValue(
            dimension_id=kv.id,
            values=frozenset(from_wire_value(v) for v in kv.values),
            time_range=from_time_range(kv.time_range),
            include=kv.include is not False,
            remove_prefix=bool(kv.remove_prefix),
            valid_from=kv.valid_from,
            valid_to=kv.valid_to,
        )
        for kv in r.key_values
    ]
    components = [from_component_value_set(c) for c in r.components]
    return CubeRegion(
        include=r.include if r.include is not None else True,
        keys=tuple(keys),
        components=tuple(components),
        annotations=ingest_annotations(r.annotations),
    )


def from_data_constraint(cc: DataConstraintJson, maintainer: Agency) -> DataConstraint:
    return DataConstraint(
        id=cc.id,
        maintainer=maintainer,
        version=version_of(cc),
        name=ingest_istr(cc.name, cc.names),
        description=ingest_istr(cc.description, cc.descriptions),
        attachment=from_constraint_attachment(cc.constraint_attachment),
        regions=tuple(from_cube_region(r) for r in cc.cube_regions or []),
        data_key_sets=tuple(
            DataKeySet(
                is_included=key_set.is_included,
                keys=tuple(
                    DataKey(
                        values=tuple((kv.id, kv.value) for kv in key.key_values),
                        annotations=ingest_annotations(key.annotations),
                        valid_from=key.valid_from,
                        valid_to=key.valid_to,
                        components=tuple(from_component_value_set(c) for c in key.components or []),
                    )
                    for key in key_set.keys
                ),
            )
            for key_set in cc.data_key_sets or []
        ),
        annotations=ingest_annotations(cc.annotations),
        is_external_reference=bool(cc.is_external_reference),
        is_partial_language=bool(cc.is_partial_language),
        uri=read_uri(cc.links),
        links=read_links(cc.links),
        valid_from=parse_dt(cc.valid_from),
        valid_to=parse_dt(cc.valid_to),
    )


# ── Hierarchy + HierarchyAssociation ─────────────────────────────────────────


def _hcode_ref(urn_val: str | None) -> Ref[Code] | None:
    """HierarchicalCode ``code`` URN → unresolved Ref[Code] (or None).

    The write side never resolves these against the message index — a
    hierarchical code just carries the target code's URN — so ingest mirrors
    that with an unresolved Ref. An absent code is left to `validate`, which
    names it; one that is present but not a code's URN is refused here.

    Raises:
        SdmxConformanceError: If the code is not a code's URN. It was skipped
            with a warning, and the node read as placing nothing (#458).
    """
    if urn_val is None:
        return None
    try:
        return Ref[Code].unresolved(Code.parse_urn(urn_val))
    except ValueError, TypeError:
        element, detail = "hierarchicalCode", f"code {urn_val!r} is not a code's URN"
        raise SdmxConformanceError(element, detail) from None


def from_hierarchical_code(hc: HierarchicalCodeJson, levels_by_id: dict[str, Level]) -> HierarchicalCode:
    level = levels_by_id.get(hc.level) if hc.level is not None else None
    return HierarchicalCode(
        id=hc.id,
        code=_hcode_ref(hc.code),
        children=[from_hierarchical_code(c, levels_by_id) for c in hc.hierarchical_codes or []],
        annotations=ingest_annotations(hc.annotations),
        uri=read_uri(hc.links),
        links=read_links(hc.links),
        level=level,
        valid_from=parse_dt(hc.valid_from),
        valid_to=parse_dt(hc.valid_to),
    )


def from_level(lv: LevelJson) -> Level:
    return Level(
        id=lv.id,
        name=ingest_istr(lv.name, lv.names),
        description=ingest_istr(lv.description, lv.descriptions),
        coding_format=from_format(lv.coding_format) if lv.coding_format is not None else None,
        annotations=ingest_annotations(lv.annotations),
        uri=read_uri(lv.links),
        links=read_links(lv.links),
    )


def _flatten_levels(head: LevelJson | None) -> list[Level]:
    """Recursive SDMX-JSON head Level node → the model's flat level list.

    Inverse of the write side's ``_levels_head``: walk the ``level`` chain
    head→tail (the head is the first tier).
    """
    levels: list[Level] = []
    node = head
    while node is not None:
        levels.append(from_level(node))
        node = node.level
    return levels


def from_hierarchy(h: HierarchyJson, maintainer: Agency) -> Hierarchy:
    levels = _flatten_levels(h.level)
    levels_by_id = {lv.id: lv for lv in levels}
    tree = [from_hierarchical_code(hc, levels_by_id) for hc in h.hierarchical_codes or []]
    return Hierarchy(
        id=h.id,
        maintainer=maintainer,
        version=version_of(h),
        name=ingest_istr(h.name, h.names),
        description=ingest_istr(h.description, h.descriptions),
        has_formal_levels=bool(h.has_formal_levels),
        levels=levels,
        tree=tree,
        annotations=ingest_annotations(h.annotations),
        is_external_reference=bool(h.is_external_reference),
        is_partial_language=bool(h.is_partial_language),
        uri=read_uri(h.links),
        links=read_links(h.links),
        valid_from=parse_dt(h.valid_from),
        valid_to=parse_dt(h.valid_to),
    )


def from_hierarchy_association(ha: HierarchyAssociationJson, maintainer: Agency) -> HierarchyAssociation:
    return HierarchyAssociation(
        id=ha.id,
        maintainer=maintainer,
        version=version_of(ha),
        name=ingest_istr(ha.name, ha.names),
        description=ingest_istr(ha.description, ha.descriptions),
        annotations=ingest_annotations(ha.annotations),
        is_external_reference=bool(ha.is_external_reference),
        is_partial_language=bool(ha.is_partial_language),
        uri=read_uri(ha.links),
        links=read_links(ha.links),
        valid_from=parse_dt(ha.valid_from),
        valid_to=parse_dt(ha.valid_to),
        linked_hierarchy=resolve_urn(ha.linked_hierarchy, Hierarchy),
        linked_object=resolve_any_urn(ha.linked_object),
        context_object=resolve_any_urn(ha.context_object),
    )


# ── MetadataStructure (MSD) + Metadataflow ───────────────────────────────────


def _max_occurs(raw: int | str | None) -> int | None:
    """OccurenceType DTO value → model max_occurs.

    Absent (None) defaults to 1; the literal "unbounded" maps to the model's
    None sentinel; a positive int passes through. Inverse of the write side's
    ``to_metadata_attribute`` max_occurs handling.
    """
    if raw is None:
        return 1
    if raw == "unbounded":
        return None
    if isinstance(raw, int):
        return raw
    # A count or "unbounded", as `_occurs` reads a representation's: a string
    # numeral is not one, and was read as a count (#458).
    element, detail = "maxOccurs", f"{raw!r} is neither a count nor 'unbounded'"
    raise SdmxConformanceError(element, detail)


def from_metadata_attribute(attr: MetadataAttributeJson, index: dict[str, object]) -> MetadataAttribute:
    concept = resolve_urn(attr.concept_identity, Concept)
    return MetadataAttribute(
        id=component_id_of(attr.id, concept, "metadataAttribute"),
        concept=concept,
        representation=codelist_representation(from_representation(attr.local_representation)),
        enumeration_format=from_enumeration_format(attr.local_representation),
        min_occurs=attr.min_occurs if attr.min_occurs is not None else 1,
        max_occurs=_max_occurs(attr.max_occurs),
        is_presentational=bool(attr.is_presentational),
        children=ItemList([from_metadata_attribute(c, index) for c in attr.metadata_attributes or []]),
        annotations=ingest_annotations(attr.annotations),
        uri=read_uri(attr.links),
        links=read_links(attr.links),
    )


def from_metadata_structure(
    msd: MetadataStructureJson, maintainer: Agency, index: dict[str, object]
) -> MetadataStructure:
    comps = msd.metadata_structure_components
    attribute_list = comps.metadata_attribute_list if comps is not None else None
    attrs: list[MetadataAttributeJson] = attribute_list.metadata_attributes if attribute_list is not None else []
    return MetadataStructure(
        id=msd.id,
        maintainer=maintainer,
        version=version_of(msd),
        name=ingest_istr(msd.name, msd.names),
        description=ingest_istr(msd.description, msd.descriptions),
        attributes=ItemList([from_metadata_attribute(a, index) for a in attrs]),
        attribute_list_annotations=ingest_annotations(
            attribute_list.annotations if attribute_list is not None else None
        ),
        attribute_list_uri=read_uri(attribute_list.links) if attribute_list is not None else None,
        attribute_list_links=read_links(attribute_list.links) if attribute_list is not None else (),
        annotations=ingest_annotations(msd.annotations),
        is_external_reference=bool(msd.is_external_reference),
        is_partial_language=bool(msd.is_partial_language),
        uri=read_uri(msd.links),
        links=read_links(msd.links),
        valid_from=parse_dt(msd.valid_from),
        valid_to=parse_dt(msd.valid_to),
    )


def from_metadataflow(mdf: MetadataflowJson, maintainer: Agency) -> Metadataflow:
    return Metadataflow(
        id=mdf.id,
        maintainer=maintainer,
        version=version_of(mdf),
        name=ingest_istr(mdf.name, mdf.names),
        description=ingest_istr(mdf.description, mdf.descriptions),
        annotations=ingest_annotations(mdf.annotations),
        is_external_reference=bool(mdf.is_external_reference),
        is_partial_language=bool(mdf.is_partial_language),
        uri=read_uri(mdf.links),
        links=read_links(mdf.links),
        valid_from=parse_dt(mdf.valid_from),
        valid_to=parse_dt(mdf.valid_to),
        structure=resolve_urn(mdf.structure, MetadataStructure),
        targets=tuple(mdf.targets or ()),
    )


def from_metadata_provision_agreement(
    mpa: MetadataProvisionAgreementJson, maintainer: Agency
) -> MetadataProvisionAgreement:
    return MetadataProvisionAgreement(
        id=mpa.id,
        maintainer=maintainer,
        version=version_of(mpa),
        name=ingest_istr(mpa.name, mpa.names),
        description=ingest_istr(mpa.description, mpa.descriptions),
        annotations=ingest_annotations(mpa.annotations),
        is_external_reference=bool(mpa.is_external_reference),
        is_partial_language=bool(mpa.is_partial_language),
        uri=read_uri(mpa.links),
        links=read_links(mpa.links),
        valid_from=parse_dt(mpa.valid_from),
        valid_to=parse_dt(mpa.valid_to),
        metadataflow=resolve_urn(mpa.metadataflow, Metadataflow),
        metadata_provider=resolve_urn(mpa.metadata_provider, MetadataProvider),
        targets=tuple(mpa.targets or ()),
    )


# ── Structure maps (representation / concept-scheme / category-scheme / structure) ─


def parse_date(s: str | None, *, element: str) -> date | None:
    """A map's validity ``date-time`` → the date it starts, at midnight UTC (#496).

    The inverse of the writer's `map_validity`. The model holds SDMX-ML 3.0's
    ``xs:date``, so any other instant, a bare date, or a timestamp with no zone
    names something it cannot hold, and is refused rather than truncated.
    """
    if not s:
        return None
    instant = to_datetime(s, what=f"{element} as a date-time")
    if instant.utcoffset() != timedelta(0) or instant.time() != time():
        raise SdmxConformanceError(element, f"{s!r} is not midnight UTC, and the model holds a date")
    return instant.date()


def from_map_endpoints(eps: list[MapEndpointJson] | None) -> tuple[MapEndpoint, ...]:
    """A RepresentationMap's source or target array → its endpoints, in order (#422).

    Each entry is a codelist (or value list) URN or a data type.
    """
    endpoints: list[MapEndpoint] = []
    for ep in eps or []:
        if ep.codelist is not None and (ref := resolve_enumeration(ep.codelist)) is not None:
            endpoints.append(ref)
        elif ep.data_type is not None:
            endpoints.append(MapDataType(data_type=ep.data_type))
    return tuple(endpoints)


def from_item_map(m: ItemMapJson) -> ItemMap:
    """One concept- or category-scheme map entry; the inverse of the writer's `_item_map`."""
    return ItemMap(
        source_id=m.source_value.value,
        target_id=m.target_value,
        annotations=ingest_annotations(m.annotations),
        valid_from=parse_date(m.valid_from, element="validFrom"),
        valid_to=parse_date(m.valid_to, element="validTo"),
    )


def from_value_mapping(m: ValueMappingJson) -> RepresentationMapping:
    """ValueMappingJson → a `RepresentationMapping`; the inverse of the writer's ``to_value_mapping``.

    Every source value keeps its own ``isRegEx`` / ``startIndex`` / ``endIndex``.
    """
    return RepresentationMapping(
        sources=tuple(
            SourceValue(value=sv.value, is_regex=bool(sv.is_reg_ex), start_index=sv.start_index, end_index=sv.end_index)
            for sv in m.source_values
        ),
        targets=tuple(m.target_values),
        valid_from=parse_date(m.valid_from, element="validFrom"),
        valid_to=parse_date(m.valid_to, element="validTo"),
        annotations=ingest_annotations(m.annotations),
    )


def from_representation_map(rm: RepresentationMapJson, maintainer: Agency) -> RepresentationMap:
    return RepresentationMap(
        id=rm.id,
        maintainer=maintainer,
        version=version_of(rm),
        name=ingest_istr(rm.name, rm.names),
        description=ingest_istr(rm.description, rm.descriptions),
        sources=from_map_endpoints(rm.source),
        targets=from_map_endpoints(rm.target),
        mappings=tuple(from_value_mapping(m) for m in rm.representation_mappings or []),
        annotations=ingest_annotations(rm.annotations),
        is_external_reference=bool(rm.is_external_reference),
        is_partial_language=bool(rm.is_partial_language),
        uri=read_uri(rm.links),
        links=read_links(rm.links),
        valid_from=parse_dt(rm.valid_from),
        valid_to=parse_dt(rm.valid_to),
    )


def from_concept_scheme_map(csm: ConceptSchemeMapJson, maintainer: Agency) -> ConceptSchemeMap:
    maps = [from_item_map(m) for m in csm.item_maps or []]
    return ConceptSchemeMap(
        id=csm.id,
        maintainer=maintainer,
        version=version_of(csm),
        name=ingest_istr(csm.name, csm.names),
        description=ingest_istr(csm.description, csm.descriptions),
        source=resolve_any_urn(csm.source),
        target=resolve_any_urn(csm.target),
        maps=tuple(maps),
        annotations=ingest_annotations(csm.annotations),
        is_external_reference=bool(csm.is_external_reference),
        is_partial_language=bool(csm.is_partial_language),
        uri=read_uri(csm.links),
        links=read_links(csm.links),
        valid_from=parse_dt(csm.valid_from),
        valid_to=parse_dt(csm.valid_to),
    )


def from_category_scheme_map(catm: CategorySchemeMapJson, maintainer: Agency) -> CategorySchemeMap:
    maps = [from_item_map(m) for m in catm.item_maps or []]
    return CategorySchemeMap(
        id=catm.id,
        maintainer=maintainer,
        version=version_of(catm),
        name=ingest_istr(catm.name, catm.names),
        description=ingest_istr(catm.description, catm.descriptions),
        source=resolve_any_urn(catm.source),
        target=resolve_any_urn(catm.target),
        maps=tuple(maps),
        annotations=ingest_annotations(catm.annotations),
        is_external_reference=bool(catm.is_external_reference),
        is_partial_language=bool(catm.is_partial_language),
        uri=read_uri(catm.links),
        links=read_links(catm.links),
        valid_from=parse_dt(catm.valid_from),
        valid_to=parse_dt(catm.valid_to),
    )


def from_organisation_scheme_map(catm: OrganisationSchemeMapJson, maintainer: Agency) -> OrganisationSchemeMap:
    maps = [from_item_map(m) for m in catm.item_maps or []]
    return OrganisationSchemeMap(
        id=catm.id,
        maintainer=maintainer,
        version=version_of(catm),
        name=ingest_istr(catm.name, catm.names),
        description=ingest_istr(catm.description, catm.descriptions),
        source=resolve_any_urn(catm.source),
        target=resolve_any_urn(catm.target),
        maps=tuple(maps),
        annotations=ingest_annotations(catm.annotations),
        is_external_reference=bool(catm.is_external_reference),
        is_partial_language=bool(catm.is_partial_language),
        uri=read_uri(catm.links),
        links=read_links(catm.links),
        valid_from=parse_dt(catm.valid_from),
        valid_to=parse_dt(catm.valid_to),
    )


class _DateMapKwargs(TypedDict, closed=True):
    """What `EpochMap` and `DatePatternMap` share — `DateMapType`'s content (#422)."""

    id: str
    component_maps: tuple[tuple[str, str], ...]
    resolve_period: ResolvePeriod | None
    frequency_dimension: str | None
    mapped_frequencies: tuple[str, ...]
    target_frequency_id: str | None
    annotations: Annotations
    uri: str | None
    links: tuple[Link, ...]


def _date_map_kwargs(m: DateMapJson) -> _DateMapKwargs:
    return {
        "id": m.id,
        "component_maps": tuple((c.source, c.target) for c in m.mapped_components),
        "resolve_period": to_enum(ResolvePeriod, m.resolve_period, what="resolvePeriod") if m.resolve_period else None,
        "frequency_dimension": m.frequency_dimension,
        "mapped_frequencies": tuple(m.mapped_frequencies or []),
        "target_frequency_id": m.target_frequency_id,
        "annotations": ingest_annotations(m.annotations),
        "uri": read_uri(m.links),
        "links": read_links(m.links),
    }


def from_structure_map(sm: StructureMapJson, maintainer: Agency) -> StructureMap:
    maps = [
        ComponentMap(
            sources=tuple(m.source),
            targets=tuple(m.target),
            annotations=ingest_annotations(m.annotations),
            representation_map=resolve_any_urn(m.representation_map),
        )
        for m in sm.component_maps or []
    ]
    return StructureMap(
        id=sm.id,
        maintainer=maintainer,
        version=version_of(sm),
        name=ingest_istr(sm.name, sm.names),
        description=ingest_istr(sm.description, sm.descriptions),
        source=resolve_any_urn(sm.source),
        target=resolve_any_urn(sm.target),
        maps=tuple(maps),
        epoch_maps=tuple(
            EpochMap(
                **_date_map_kwargs(m),
                base_period=m.base_period,
                epoch_period=to_enum(EpochPeriod, m.epoch_period or ASSUMED_EPOCH_PERIOD, what="epochPeriod"),
            )
            for m in sm.epoch_maps or []
        ),
        date_pattern_maps=tuple(
            DatePatternMap(**_date_map_kwargs(m), source_pattern=m.source_pattern, locale=m.locale)
            for m in sm.date_pattern_maps or []
        ),
        frequency_format_mappings=tuple(
            FrequencyFormatMapping(
                id=m.id,
                frequency_id=m.frequency_id,
                date_pattern=m.date_pattern,
                annotations=ingest_annotations(m.annotations),
                uri=read_uri(m.links),
                links=read_links(m.links),
            )
            for m in sm.frequency_format_mappings or []
        ),
        fixed_value_maps=tuple(
            FixedValueMap(
                source_id=m.source,
                target_id=m.target,
                values=tuple(m.values or []),
                annotations=ingest_annotations(m.annotations),
            )
            for m in sm.fixed_value_maps or []
        ),
        annotations=ingest_annotations(sm.annotations),
        is_external_reference=bool(sm.is_external_reference),
        is_partial_language=bool(sm.is_partial_language),
        uri=read_uri(sm.links),
        links=read_links(sm.links),
        valid_from=parse_dt(sm.valid_from),
        valid_to=parse_dt(sm.valid_to),
    )
