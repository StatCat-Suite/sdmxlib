"""The SDMX-ML v3 structure reader, shared by 3.0 and 3.1 (#346).

`sdmx_ml31/reader.py` was a copy of `sdmx_ml30/reader.py`: measured at AST
level, **30 of 30 functions byte-identical** with no divergence at all. The two
generations read the same grammar; only the namespace URIs differ, and those
already lived in each package's `namespaces.py`.

So there is nothing to parameterise here beyond the three URIs
`ReaderHelpers` already takes. That is what makes the reader the half worth
merging first — the writer's three genuine divergences (`Categorisation`
without `@version`, `DataConstraint` without `@role`, `Dataflow` with
`<DimensionConstraint>`) still need a "what this generation omits" mechanism,
and this needs none.

A class rather than a closure factory, for the reason #209 records: the factory
form scored 88 on C901 with 210 statements and erased every signature behind
`Callable[...]`. `ReaderHelpers` in `_common.py` is the same decision.

The cost this removes is the one #345 was: **every fix to a shared path had to
be applied twice, by someone who knew to look.** When nobody knew, two branches
went missing from one reader and the round-trip guard passed because the
fixture had been trimmed to match.

`tests/unit/test_ml3_fork_drift.py` still guards the writer pair, which is
still forked.
"""

from collections.abc import Callable
from datetime import datetime

from attrs import frozen
from lxml import etree
from typing_extensions import TypedDict

from sdmxlib.formats._attachment import match_dimension_case
from sdmxlib.formats._coerce import to_date, to_int
from sdmxlib.formats._positions import check_positions
from sdmxlib.formats._refuse import build_each_xml
from sdmxlib.formats.sdmx_ml._closed_world import required
from sdmxlib.formats.sdmx_ml._common import ReaderHelpers, ReferenceFields, component_id, flag, node_level
from sdmxlib.formats.sdmx_ml._header import read_header
from sdmxlib.formats.sdmx_ml._losses import account
from sdmxlib.model._resolvers import document_binding
from sdmxlib.model.annotations import Annotations
from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.category import CategoryScheme
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.collections import ArtefactList, ItemList
from sdmxlib.model.concept import Concept, ConceptScheme
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
from sdmxlib.model.errors import Refusal
from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy, HierarchyAssociation, Level
from sdmxlib.model.istring import InternationalString
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
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.metadata_provision import MetadataProvisionAgreement
from sdmxlib.model.metadataflow import Metadataflow
from sdmxlib.model.metadatastructure import MetadataAttribute, MetadataStructure
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
from sdmxlib.model.ref import Ref
from sdmxlib.model.registry import SupportsAddAll
from sdmxlib.model.representation import Facet
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.model.valuelist import ValueList

# ── Shared reader helpers ─────────────────────────────────────────────────────
# Pulled from sdmxlib.formats.sdmx_ml._common and used as `_helpers.x` so each
# call site says where the helper comes from. Only the namespace tag builders
# are still bound to short names, because they appear inline in every lookup;
# they go away with the prefixed-path conversion (#209).
#: Prefix map for ``find``/``findall``. lxml resolves prefixes per call — there
#: is no way to bind it to a tree — so every lookup passes ``namespaces=_NS``.


# ── OrganisationSchemes ───────────────────────────────────────────────────────


class _OrganisationFields(TypedDict, closed=True):
    """What every 3.x organisation item carries: an `Organisation` in the IM, whichever scheme holds it."""

    id: str
    uri: str | None
    links: tuple[Link, ...]
    name: InternationalString
    description: InternationalString
    contacts: tuple[Contact, ...]
    annotations: Annotations


class _SchemeFields(ReferenceFields, closed=True):
    """What every 3.x organisation scheme carries besides its items."""

    id: str
    uri: str | None
    links: tuple[Link, ...]
    maintainer: str
    name: InternationalString
    description: InternationalString
    annotations: Annotations
    valid_from: datetime | None
    valid_to: datetime | None
    is_partial: bool


@frozen
class _OrganisationSchemes:
    agency: list[AgencyScheme]
    data_provider: list[DataProviderScheme]
    metadata_provider: list[MetadataProviderScheme]
    data_consumer: list[DataConsumerScheme]
    organisation_unit: list[OrganisationUnitScheme]


class _DateMapFields(TypedDict, closed=True):
    """The keywords `EpochMap` and `DatePatternMap` share — `DateMapType`'s content."""

    id: str
    uri: str | None
    links: tuple[Link, ...]
    annotations: Annotations
    component_maps: tuple[tuple[str, str], ...]
    frequency_dimension: str | None
    mapped_frequencies: tuple[str, ...]
    target_frequency_id: str | None
    resolve_period: ResolvePeriod | None


class _StructureMapParts(TypedDict, closed=True):
    """A `StructureMap`'s SDMX 3.x lists beyond its component maps (#422)."""

    epoch_maps: tuple[EpochMap, ...]
    date_pattern_maps: tuple[DatePatternMap, ...]
    frequency_format_mappings: tuple[FrequencyFormatMapping, ...]
    fixed_value_maps: tuple[FixedValueMap, ...]


class V3Reader:
    """One SDMX-ML v3 structure reader, bound to a generation's namespaces.

    Args:
        com: the `com:` namespace URI for this generation.
        str_: the `str:` namespace URI.
        mes: the `mes:` namespace URI.
    """

    def __init__(self, *, com: str, str_: str, mes: str) -> None:
        # 3.0 removed `isFinal` from every maintainable, in both generations.
        self._helpers = ReaderHelpers(com=com, str_=str_, has_is_final=False, has_links=True)
        #: Prefix map for every ``find``/``findall``. lxml resolves prefixes
        #: per call — there is no way to bind one to a tree — so every lookup
        #: passes it.
        self._ns = {"com": com, "str": str_, "mes": mes}

    def _read_contact(self, c_el: etree._Element) -> Contact:
        # ContactType i18n fields: <com:Name> in the common namespace, but
        # <str:Department> and <str:Role> are in the structure namespace per
        # the XSD.
        return Contact(
            id=c_el.get("id"),
            name=self._helpers.read_istring(c_el),
            department=self._helpers.read_istring(c_el, "Department", prefix="str"),
            role=self._helpers.read_istring(c_el, "Role", prefix="str"),
            phones=tuple(el.text for el in c_el.findall("str:Telephone", namespaces=self._ns) if el.text),
            faxes=tuple(el.text for el in c_el.findall("str:Fax", namespaces=self._ns) if el.text),
            x400s=tuple(el.text for el in c_el.findall("str:X400", namespaces=self._ns) if el.text),
            uris=tuple(el.text for el in c_el.findall("str:URI", namespaces=self._ns) if el.text),
            emails=tuple(el.text for el in c_el.findall("str:Email", namespaces=self._ns) if el.text),
        )

    def _organisation_fields(self, el: etree._Element) -> _OrganisationFields:
        """`_OrganisationFields` read off one item element."""
        return {
            "id": required(el, "id"),
            "uri": el.get("uri"),
            "links": self._helpers.read_links(el),
            "name": self._helpers.read_istring(el),
            "description": self._helpers.read_istring(el, "Description"),
            "contacts": tuple(self._read_contact(c) for c in el.findall("str:Contact", namespaces=self._ns)),
            "annotations": self._helpers.read_annotations(el),
        }

    def _scheme_fields(self, el: etree._Element) -> _SchemeFields:
        """`_SchemeFields` read off one scheme element.

        No `version`: 3.x prohibits the attribute on an organisation scheme,
        and the model's default is the fixed 1.0 (#458).
        """
        return {
            "id": required(el, "id"),
            "uri": el.get("uri"),
            "links": self._helpers.read_links(el),
            "maintainer": required(el, "agencyID"),
            "name": self._helpers.read_istring(el),
            "description": self._helpers.read_istring(el, "Description"),
            "annotations": self._helpers.read_annotations(el),
            "valid_from": self._helpers.read_datetime_attr(el, "validFrom"),
            "valid_to": self._helpers.read_datetime_attr(el, "validTo"),
            **self._helpers.read_reference_attrs(el),
            "is_partial": self._helpers.read_is_partial(el),
        }

    def _read_organisation_schemes(self, structures: etree._Element, refused: list[Refusal]) -> _OrganisationSchemes:
        # SDMX-ML 3.0 splits 2.1's `OrganisationSchemes` into a container per
        # scheme class. This used to read the unified container as well, which
        # the closed-world check refuses before this runs (#435). Under
        # `strict=False` its schemes then came back both refused and read (#458).

        def items(el: etree._Element, tag: str) -> list[etree._Element]:
            return el.findall(f"str:{tag}", namespaces=self._ns)

        def agency_scheme(el: etree._Element) -> AgencyScheme:
            agencies = [Agency(**self._organisation_fields(a)) for a in items(el, "Agency")]
            return AgencyScheme(**self._scheme_fields(el), agencies=ItemList(agencies))

        def provider_scheme(el: etree._Element) -> DataProviderScheme:
            providers = [DataProvider(**self._organisation_fields(p)) for p in items(el, "DataProvider")]
            return DataProviderScheme(**self._scheme_fields(el), providers=ItemList(providers))

        def metadata_provider_scheme(el: etree._Element) -> MetadataProviderScheme:
            providers = [MetadataProvider(**self._organisation_fields(p)) for p in items(el, "MetadataProvider")]
            return MetadataProviderScheme(**self._scheme_fields(el), providers=ItemList(providers))

        def consumer_scheme(el: etree._Element) -> DataConsumerScheme:
            consumers = [DataConsumer(**self._organisation_fields(c)) for c in items(el, "DataConsumer")]
            return DataConsumerScheme(**self._scheme_fields(el), consumers=ItemList(consumers))

        def unit_scheme(el: etree._Element) -> OrganisationUnitScheme:
            units = [
                OrganisationUnit(**self._organisation_fields(u), parent_id=self._helpers.read_parent_id(u))
                for u in items(el, "OrganisationUnit")
            ]
            return OrganisationUnitScheme(**self._scheme_fields(el), organisation_units=ItemList(units))

        def each[T](tag: str, build: Callable[[etree._Element], T], cls: type[MaintainableArtefact]) -> list[T]:
            containers = structures.findall(f"str:{tag}s", namespaces=self._ns)
            return build_each_xml([el for c in containers for el in items(c, tag)], build, cls, refused)

        return _OrganisationSchemes(
            agency=each("AgencyScheme", agency_scheme, AgencyScheme),
            data_provider=each("DataProviderScheme", provider_scheme, DataProviderScheme),
            metadata_provider=each("MetadataProviderScheme", metadata_provider_scheme, MetadataProviderScheme),
            data_consumer=each("DataConsumerScheme", consumer_scheme, DataConsumerScheme),
            organisation_unit=each("OrganisationUnitScheme", unit_scheme, OrganisationUnitScheme),
        )

    # ── Codelists ─────────────────────────────────────────────────────────────────

    def _read_concept_role(self, component_el: etree._Element) -> tuple[str, ...]:
        """Read all <str:ConceptRole> children → tuple of role ids.

        Each is a concept's URN, as text; the role is its item id. Components
        may declare several. 2.1's ``<Ref>`` form is refused by the
        closed-world check before this runs (#435, #458).
        """
        roles: list[str] = []
        for role_el in component_el.findall("str:ConceptRole", namespaces=self._ns):
            text = (role_el.text or "").strip()
            if text.startswith("urn:"):
                urn = SdmxUrn.parse(text)
                if urn.item_id is not None:
                    roles.append(urn.item_id)
        return tuple(roles)

    def _read_dimension(self, dim_el: etree._Element, index: dict[str, object]) -> Dimension:
        concept_ref: Ref[Concept] | None = None
        ci_el = dim_el.find("str:ConceptIdentity", namespaces=self._ns)
        if ci_el is not None:
            concept_ref = self._helpers.resolve_ref(ci_el, Concept)

        representation = None
        enumeration_format = None
        local_rep_el = dim_el.find("str:LocalRepresentation", namespaces=self._ns)
        if local_rep_el is not None:
            representation = self._helpers.read_enumerated_representation(local_rep_el, index)
            enumeration_format = self._helpers.read_enumeration_format(local_rep_el)

        return Dimension(
            id=component_id(dim_el, concept_ref),
            uri=dim_el.get("uri"),
            links=self._helpers.read_links(dim_el),
            concept=concept_ref,
            representation=representation,
            enumeration_format=enumeration_format,
            annotations=self._helpers.read_annotations(dim_el),
            concept_role=self._read_concept_role(dim_el),
        )

    def _read_time_dimension(self, td_el: etree._Element) -> TimeDimension:
        concept_ref: Ref[Concept] | None = None
        ci_el = td_el.find("str:ConceptIdentity", namespaces=self._ns)
        if ci_el is not None:
            concept_ref = self._helpers.resolve_ref(ci_el, Concept)

        facet_rep: Facet | None = None
        local_rep_el = td_el.find("str:LocalRepresentation", namespaces=self._ns)
        if local_rep_el is not None:
            tf_el = local_rep_el.find("str:TextFormat", namespaces=self._ns)
            if tf_el is not None:
                facet_rep = self._helpers.read_text_format(tf_el)

        return TimeDimension(
            id=td_el.get("id", "TIME_PERIOD"),
            uri=td_el.get("uri"),
            links=self._helpers.read_links(td_el),
            concept=concept_ref,
            representation=facet_rep,
            annotations=self._helpers.read_annotations(td_el),
        )

    def _read_attachment(
        self,
        ar_el: etree._Element,
    ) -> tuple[AttachmentLevel, list[str] | None, frozenset[str], str | None]:
        """Parse AttributeRelationship → (level, related_dimensions, optional_dimensions, related_group).

        SDMX 3.0 shape (per v3.0.0 XSD):
          - <Observation/> → observation-level. A draft's <ObservationValue/>
            is refused by the closed-world check (#435, #458).
          - <Dimension>id</Dimension>+ → series-level (dim id is text content,
            not a nested <Ref/> like in 2.1).
          - <Group>id</Group> → group-level (group id as text content, IDType).
          - <Dataflow/> → dataflow-level (3.0 equivalent of 2.1's <None/>).
        """
        if ar_el.find("str:Observation", namespaces=self._ns) is not None:
            return AttachmentLevel.OBSERVATION, None, frozenset(), None

        dim_els = ar_el.findall("str:Dimension", namespaces=self._ns)
        if dim_els:
            related: list[str] = []
            # 3.0 `OptionalLocalDimensionReferenceType` (#422).
            optional = frozenset(d.text.strip() for d in dim_els if d.text and d.text.strip() and flag(d, "optional"))
            # Text content. 2.1's nested ``<Ref>`` is refused by the
            # closed-world check before this runs (#435, #458).
            related.extend(d.text.strip() for d in dim_els if d.text and d.text.strip())
            return AttachmentLevel.SERIES, related or None, optional, None

        group_el = ar_el.find("str:Group", namespaces=self._ns)
        if group_el is not None:
            group_id = (group_el.text or "").strip() or None
            return AttachmentLevel.GROUP, None, frozenset(), group_id

        return AttachmentLevel.DATASET, None, frozenset(), None

    def _read_occurs(self, local_rep_el: etree._Element | None) -> tuple[int, int | None]:
        """A DSD component's `minOccurs` / `maxOccurs` (#422); None is unbounded."""
        return self._helpers.read_occurs(local_rep_el)

    def _read_metadata_attribute_usage(self, usage_el: etree._Element) -> MetadataAttributeUsage:
        """One `<str:MetadataAttributeUsage>` (#422): the MSD attribute it uses, and its attachment."""
        ref_el = usage_el.find("str:MetadataAttributeReference", namespaces=self._ns)
        # Every schema requires the relationship; FMR omits it (IMF: 16
        # attributes, 3,047 usages), meaning one that varies with nothing —
        # reported once per data set (#421).
        attachment: AttachmentLevel = AttachmentLevel.DATASET
        related_dims: list[str] | None = None
        optional_dims: frozenset[str] = frozenset()
        related_group: str | None = None
        ar_el = usage_el.find("str:AttributeRelationship", namespaces=self._ns)
        if ar_el is not None:
            attachment, related_dims, optional_dims, related_group = self._read_attachment(ar_el)
        return MetadataAttributeUsage(
            id=(ref_el.text or "").strip() if ref_el is not None else "",
            attachment=attachment,
            related_dimensions=related_dims,
            optional_dimensions=optional_dims,
            related_group=related_group,
            annotations=self._helpers.read_annotations(usage_el),
            uri=usage_el.get("uri"),
            links=self._helpers.read_links(usage_el),
        )

    def _read_attribute(self, attr_el: etree._Element, index: dict[str, object]) -> Attribute:
        concept_ref: Ref[Concept] | None = None
        ci_el = attr_el.find("str:ConceptIdentity", namespaces=self._ns)
        if ci_el is not None:
            concept_ref = self._helpers.resolve_ref(ci_el, Concept)

        representation = None
        enumeration_format = None
        local_rep_el = attr_el.find("str:LocalRepresentation", namespaces=self._ns)
        if local_rep_el is not None:
            representation = self._helpers.read_enumerated_representation(local_rep_el, index)
            enumeration_format = self._helpers.read_enumeration_format(local_rep_el)

        # Every schema requires the relationship; FMR omits it (IMF: 16
        # attributes, 3,047 usages), meaning one that varies with nothing —
        # reported once per data set (#421).
        attachment: AttachmentLevel = AttachmentLevel.DATASET
        related_dims: list[str] | None = None
        optional_dims: frozenset[str] = frozenset()
        related_group: str | None = None
        ar_el = attr_el.find("str:AttributeRelationship", namespaces=self._ns)
        if ar_el is not None:
            attachment, related_dims, optional_dims, related_group = self._read_attachment(ar_el)
        mr_el = attr_el.find("str:MeasureRelationship", namespaces=self._ns)
        related_measures = (
            None
            if mr_el is None
            else tuple(m.text.strip() for m in mr_el.findall("str:Measure", namespaces=self._ns) if m.text)
        )
        min_occurs, max_occurs = self._read_occurs(local_rep_el)

        # SDMX 3.0: attribute named ``usage`` (2.1 used ``assignmentStatus``)
        return Attribute(
            id=component_id(attr_el, concept_ref),
            uri=attr_el.get("uri"),
            links=self._helpers.read_links(attr_el),
            usage=attr_el.get("usage"),
            attachment=attachment,
            related_dimensions=related_dims,
            optional_dimensions=optional_dims,
            related_measures=related_measures,
            related_group=related_group,
            concept=concept_ref,
            representation=representation,
            enumeration_format=enumeration_format,
            min_occurs=min_occurs,
            max_occurs=max_occurs,
            annotations=self._helpers.read_annotations(attr_el),
            concept_role=self._read_concept_role(attr_el),
        )

    def _read_measure(self, measure_el: etree._Element, index: dict[str, object]) -> Measure:
        """Read a single <str:Measure> element (SDMX 3.0 replaces PrimaryMeasure)."""
        concept_ref: Ref[Concept] | None = None
        ci_el = measure_el.find("str:ConceptIdentity", namespaces=self._ns)
        if ci_el is not None:
            concept_ref = self._helpers.resolve_ref(ci_el, Concept)

        representation = None
        enumeration_format = None
        local_rep_el = measure_el.find("str:LocalRepresentation", namespaces=self._ns)
        if local_rep_el is not None:
            representation = self._helpers.read_enumerated_representation(local_rep_el, index)
            enumeration_format = self._helpers.read_enumeration_format(local_rep_el)

        min_occurs, max_occurs = self._read_occurs(local_rep_el)
        return Measure(
            # Not 2.1's fixed OBS_VALUE: a 3.x measure is a component like any other.
            id=component_id(measure_el, concept_ref),
            uri=measure_el.get("uri"),
            links=self._helpers.read_links(measure_el),
            usage=measure_el.get("usage"),
            concept=concept_ref,
            representation=representation,
            enumeration_format=enumeration_format,
            min_occurs=min_occurs,
            max_occurs=max_occurs,
            annotations=self._helpers.read_annotations(measure_el),
            concept_role=self._read_concept_role(measure_el),
        )

    def _read_groups(self, components_el: etree._Element) -> list[Group]:
        """Read Group elements: ``<GroupDimension><DimensionReference>DIM_ID</...></...>``.

        2.1's nested ``<Ref>`` and a draft's ``@dimensionRef`` used to be read
        too. The closed-world check refuses both before this runs (#435), so
        those branches were dead (#458).
        """
        result: list[Group] = []
        for group_el in components_el.findall("str:Group", namespaces=self._ns):
            dim_ids: list[str] = []
            for gd_el in group_el.findall("str:GroupDimension", namespaces=self._ns):
                dr_el = gd_el.find("str:DimensionReference", namespaces=self._ns)
                if dr_el is not None and dr_el.text and dr_el.text.strip():
                    dim_ids.append(dr_el.text.strip())
            result.append(
                Group(
                    id=required(group_el, "id"),
                    uri=group_el.get("uri"),
                    dimension_ids=dim_ids,
                    annotations=self._helpers.read_annotations(group_el),
                )
            )
        return result

    def _read_dsd_components(
        self, components_el: etree._Element, index: dict[str, object]
    ) -> tuple[
        list[Dimension], TimeDimension | None, list[Attribute], list[Measure], list[Group], list[MetadataAttributeUsage]
    ]:
        dimensions: list[Dimension] = []
        time_dimension: TimeDimension | None = None
        attributes: list[Attribute] = []
        measures: list[Measure] = []

        dim_list_el = components_el.find("str:DimensionList", namespaces=self._ns)
        if dim_list_el is not None:
            check_positions(
                [
                    (d.get("id", ""), d.get("position"))
                    for d in dim_list_el.findall("str:Dimension", namespaces=self._ns)
                ],
                element="str:Dimension",
            )
            dimensions.extend(
                self._read_dimension(d, index) for d in dim_list_el.findall("str:Dimension", namespaces=self._ns)
            )
            td_el = dim_list_el.find("str:TimeDimension", namespaces=self._ns)
            if td_el is not None:
                time_dimension = self._read_time_dimension(td_el)

        attr_list_el = components_el.find("str:AttributeList", namespaces=self._ns)
        usages: list[MetadataAttributeUsage] = []
        if attr_list_el is not None:
            attributes.extend(
                self._read_attribute(a, index) for a in attr_list_el.findall("str:Attribute", namespaces=self._ns)
            )
            usages.extend(
                self._read_metadata_attribute_usage(u)
                for u in attr_list_el.findall("str:MetadataAttributeUsage", namespaces=self._ns)
            )

        # SDMX 3.0: MeasureList has <str:Measure> children (multiple allowed)
        measure_list_el = components_el.find("str:MeasureList", namespaces=self._ns)
        if measure_list_el is not None:
            measures.extend(
                self._read_measure(m, index) for m in measure_list_el.findall("str:Measure", namespaces=self._ns)
            )

        groups = self._read_groups(components_el)
        dimension_ids = [d.id for d in dimensions] + ([time_dimension.id] if time_dimension is not None else [])
        attributes = match_dimension_case(attributes, dimension_ids)
        usages = match_dimension_case(usages, dimension_ids)
        return dimensions, time_dimension, attributes, measures, groups, usages

    def _read_data_structures(
        self,
        structures: etree._Element,
        index: dict[str, object],
        agencies: dict[str, Agency],
        refused: list[Refusal],
    ) -> list[DataStructure]:
        container = structures.find("str:DataStructures", namespaces=self._ns)
        if container is None:
            return []

        def one(dsd_el: etree._Element) -> DataStructure:
            dimensions: list[Dimension] = []
            time_dimension: TimeDimension | None = None
            attributes: list[Attribute] = []
            measures: list[Measure] = []
            groups: list[Group] = []
            usages: list[MetadataAttributeUsage] = []

            components_el = dsd_el.find("str:DataStructureComponents", namespaces=self._ns)
            if components_el is not None:
                dimensions, time_dimension, attributes, measures, groups, usages = self._read_dsd_components(
                    components_el, index
                )

            return DataStructure(
                id=required(dsd_el, "id"),
                uri=dsd_el.get("uri"),
                links=self._helpers.read_links(dsd_el),
                maintainer=self._helpers.resolve_agency(required(dsd_el, "agencyID"), agencies),
                version=dsd_el.get("version", "1.0"),
                name=self._helpers.read_istring(dsd_el),
                description=self._helpers.read_istring(dsd_el, "Description"),
                dimensions=ItemList(dimensions),
                time_dimension=time_dimension,
                attributes=ItemList(attributes),
                measures=ItemList(measures),
                groups=ItemList(groups) if groups else None,
                dimension_descriptor_annotations=self._helpers.read_child_annotations(components_el, "DimensionList"),
                attribute_descriptor_annotations=self._helpers.read_child_annotations(components_el, "AttributeList"),
                measure_descriptor_annotations=self._helpers.read_child_annotations(components_el, "MeasureList"),
                dimension_descriptor_uri=self._helpers.read_child_uri(components_el, "DimensionList"),
                dimension_descriptor_links=self._helpers.read_child_links(components_el, "DimensionList"),
                attribute_descriptor_uri=self._helpers.read_child_uri(components_el, "AttributeList"),
                attribute_descriptor_links=self._helpers.read_child_links(components_el, "AttributeList"),
                measure_descriptor_uri=self._helpers.read_child_uri(components_el, "MeasureList"),
                measure_descriptor_links=self._helpers.read_child_links(components_el, "MeasureList"),
                evolving_structure=flag(dsd_el, "evolvingStructure"),
                metadata=self._helpers.resolve_ref_text(
                    dsd_el.find("str:Metadata", namespaces=self._ns), MetadataStructure
                ),
                metadata_attributes=ItemList(usages),
                annotations=self._helpers.read_annotations(dsd_el),
                valid_from=self._helpers.read_datetime_attr(dsd_el, "validFrom"),
                valid_to=self._helpers.read_datetime_attr(dsd_el, "validTo"),
                **self._helpers.read_reference_attrs(dsd_el),
            )

        return build_each_xml(container.findall("str:DataStructure", namespaces=self._ns), one, DataStructure, refused)

    # ── Dataflows ─────────────────────────────────────────────────────────────────

    def _read_item_maps_30(self, parent: etree._Element) -> list[ItemMap]:
        """Parse 3.0 ``<str:ItemMap>`` children (SingleValueMappingType).

        Each ItemMap holds ``<str:SourceValue>`` text and ``<str:TargetValue>``
        text. Maps without both sides are skipped.
        """
        result: list[ItemMap] = []
        for im_el in parent.findall("str:ItemMap", namespaces=self._ns):
            sv = im_el.find("str:SourceValue", namespaces=self._ns)
            tv = im_el.find("str:TargetValue", namespaces=self._ns)
            if sv is None or tv is None or sv.text is None or tv.text is None:
                continue
            valid_from, valid_to = im_el.get("validFrom"), im_el.get("validTo")
            result.append(
                ItemMap(
                    source_id=sv.text.strip(),
                    target_id=tv.text.strip(),
                    annotations=self._helpers.read_annotations(im_el),
                    valid_from=to_date(valid_from, what="@validFrom") if valid_from else None,
                    valid_to=to_date(valid_to, what="@validTo") if valid_to else None,
                )
            )
        return result

    def _read_mappings_30(self, parent: etree._Element) -> list[RepresentationMapping]:
        """Parse 3.x ``<str:RepresentationMapping>`` children (ValueMappingType).

        Each holds one or more ``SourceValue``s and zero or more
        ``TargetValue``s (#422). A source value's optional ``isRegEx`` /
        ``startIndex`` / ``endIndex`` say how it matches, so they are read per
        value. The mapping's ``validFrom`` / ``validTo`` are ``xs:date``.
        """
        result: list[RepresentationMapping] = []
        for rm_el in parent.findall("str:RepresentationMapping", namespaces=self._ns):
            valid_from_str = rm_el.get("validFrom")
            valid_to_str = rm_el.get("validTo")
            result.append(
                RepresentationMapping(
                    sources=tuple(
                        self._source_value(sv) for sv in rm_el.findall("str:SourceValue", namespaces=self._ns)
                    ),
                    targets=tuple(self._texts(rm_el, "TargetValue")),
                    valid_from=to_date(valid_from_str, what="@validFrom") if valid_from_str else None,
                    valid_to=to_date(valid_to_str, what="@validTo") if valid_to_str else None,
                    annotations=self._helpers.read_annotations(rm_el),
                )
            )
        return result

    @staticmethod
    def _source_value(el: etree._Element) -> SourceValue:
        """One ``<str:SourceValue>`` (``MappedValueType``)."""
        start = el.get("startIndex")
        end = el.get("endIndex")
        return SourceValue(
            value=(el.text or "").strip(),
            is_regex=flag(el, "isRegEx"),
            start_index=None if start is None else to_int(start, what="<SourceValue> @startIndex"),
            end_index=None if end is None else to_int(end, what="<SourceValue> @endIndex"),
        )

    def _read_component_maps_30(self, parent: etree._Element) -> list[ComponentMap]:
        """Parse 3.x ``<str:ComponentMap>`` children inside a StructureMap — N sources to M targets."""
        return [
            ComponentMap(
                sources=tuple(self._texts(cm_el, "Source")),
                targets=tuple(self._texts(cm_el, "Target")),
                annotations=self._helpers.read_annotations(cm_el),
                representation_map=self._helpers.resolve_ref_text(
                    cm_el.find("str:RepresentationMap", namespaces=self._ns), object
                ),
            )
            for cm_el in parent.findall("str:ComponentMap", namespaces=self._ns)
        ]

    def _texts(self, el: etree._Element, tag: str) -> list[str]:
        """Every ``str:{tag}`` child's stripped text, in order."""
        return [(child.text or "").strip() for child in el.findall(f"str:{tag}", namespaces=self._ns)]

    def _date_map_fields(self, el: etree._Element) -> _DateMapFields:
        """What `DateMapType` gives an epoch and a date-pattern map alike (#422)."""
        resolve = el.get("resolvePeriod")
        return {
            "id": required(el, "id"),
            "uri": el.get("uri"),
            "links": self._helpers.read_links(el),
            "annotations": self._helpers.read_annotations(el),
            "component_maps": tuple(zip(self._texts(el, "Source"), self._texts(el, "Target"), strict=False)),
            "frequency_dimension": el.findtext("str:FrequencyDimension", namespaces=self._ns),
            "mapped_frequencies": tuple(self._texts(el, "MappedFrequencies")),
            "target_frequency_id": el.findtext("str:TargetFrequencyID", namespaces=self._ns),
            "resolve_period": ResolvePeriod(resolve) if resolve else None,
        }

    def _read_structure_map_parts(self, el: etree._Element) -> _StructureMapParts:
        """A structure map's date maps, frequency formats and fixed values (#422)."""
        return {
            "epoch_maps": tuple(
                EpochMap(
                    **self._date_map_fields(em_el),
                    base_period=required(em_el, "basePeriod"),
                    epoch_period=EpochPeriod(em_el.get("epochPeriod", "day")),
                )
                for em_el in el.findall("str:EpochMap", namespaces=self._ns)
            ),
            "date_pattern_maps": tuple(
                DatePatternMap(
                    **self._date_map_fields(pm_el),
                    source_pattern=required(pm_el, "sourcePattern"),
                    locale=required(pm_el, "locale"),
                )
                for pm_el in el.findall("str:DatePatternMap", namespaces=self._ns)
            ),
            "frequency_format_mappings": tuple(
                FrequencyFormatMapping(
                    id=required(fm_el, "id"),
                    uri=fm_el.get("uri"),
                    links=self._helpers.read_links(fm_el),
                    annotations=self._helpers.read_annotations(fm_el),
                    frequency_id=fm_el.findtext("str:FrequencyId", default="", namespaces=self._ns),
                    date_pattern=fm_el.findtext("str:DatePattern", default="", namespaces=self._ns),
                )
                for fm_el in el.findall("str:FrequencyFormatMapping", namespaces=self._ns)
            ),
            "fixed_value_maps": tuple(
                FixedValueMap(
                    source_id=fv_el.findtext("str:Source", namespaces=self._ns),
                    target_id=fv_el.findtext("str:Target", namespaces=self._ns),
                    values=tuple(self._texts(fv_el, "Value")),
                    annotations=self._helpers.read_annotations(fv_el),
                )
                for fv_el in el.findall("str:FixedValueMap", namespaces=self._ns)
            ),
        }

    def _read_endpoints(self, el: etree._Element, side: str) -> list[MapEndpoint]:
        """One side of a `RepresentationMap` — codelists and data types, in document order.

        `side` is `"Source"` or `"Target"`; the two are read identically and
        differ only in the element name, which is what made writing one and
        forgetting the other easy. Each side is an unbounded choice of the two
        kinds (#422), so they are read together to keep their order.
        """
        endpoints: list[MapEndpoint] = []
        for child in el.iterchildren(tag=etree.Element):
            name = etree.QName(child).localname
            if name == f"{side}Codelist":
                # `AnyCodelistReferenceType`: a value list may stand where a codelist does.
                kind = ValueList if self._helpers.names_value_list(child) else Codelist
                ref = self._helpers.resolve_ref_text(child, kind)
                if ref is not None:
                    endpoints.append(ref)
            elif name == f"{side}DataType":
                endpoints.append(MapDataType((child.text or "").strip()))
        return endpoints

    def _read_representation_maps(
        self,
        structures: etree._Element,
        agencies: dict[str, Agency],
        refused: list[Refusal],
    ) -> list[RepresentationMap]:
        container = structures.find("str:RepresentationMaps", namespaces=self._ns)
        if container is None:
            return []

        def one(el: etree._Element) -> RepresentationMap:
            return RepresentationMap(
                id=required(el, "id"),
                uri=el.get("uri"),
                links=self._helpers.read_links(el),
                maintainer=self._helpers.resolve_agency(required(el, "agencyID"), agencies),
                version=el.get("version", "1.0"),
                name=self._helpers.read_istring(el),
                description=self._helpers.read_istring(el, "Description"),
                sources=tuple(self._read_endpoints(el, "Source")),
                targets=tuple(self._read_endpoints(el, "Target")),
                mappings=tuple(self._read_mappings_30(el)),
                annotations=self._helpers.read_annotations(el),
                valid_from=self._helpers.read_datetime_attr(el, "validFrom"),
                valid_to=self._helpers.read_datetime_attr(el, "validTo"),
                **self._helpers.read_reference_attrs(el),
            )

        return build_each_xml(
            container.findall("str:RepresentationMap", namespaces=self._ns),
            one,
            RepresentationMap,
            refused,
        )

    def _read_item_scheme_maps[T: (ConceptSchemeMap, CategorySchemeMap, OrganisationSchemeMap)](
        self,
        structures: etree._Element,
        container_tag: str,
        item_tag: str,
        cls: type[T],
        agencies: dict[str, Agency],
        refused: list[Refusal],
    ) -> list[T]:
        container = structures.find(f"str:{container_tag}", namespaces=self._ns)
        if container is None:
            return []

        def one(el: etree._Element) -> T:
            source_ref = self._helpers.resolve_ref_text(el.find("str:Source", namespaces=self._ns), object)
            target_ref = self._helpers.resolve_ref_text(el.find("str:Target", namespaces=self._ns), object)
            return cls(
                id=required(el, "id"),
                uri=el.get("uri"),
                links=self._helpers.read_links(el),
                maintainer=self._helpers.resolve_agency(required(el, "agencyID"), agencies),
                version=el.get("version", "1.0"),
                name=self._helpers.read_istring(el),
                description=self._helpers.read_istring(el, "Description"),
                source=source_ref,
                target=target_ref,
                maps=tuple(self._read_item_maps_30(el)),
                annotations=self._helpers.read_annotations(el),
                valid_from=self._helpers.read_datetime_attr(el, "validFrom"),
                valid_to=self._helpers.read_datetime_attr(el, "validTo"),
                **self._helpers.read_reference_attrs(el),
            )

        return build_each_xml(container.findall(f"str:{item_tag}", namespaces=self._ns), one, cls, refused)

    def _read_structure_maps(
        self,
        structures: etree._Element,
        agencies: dict[str, Agency],
        refused: list[Refusal],
    ) -> list[StructureMap]:
        container = structures.find("str:StructureMaps", namespaces=self._ns)
        if container is None:
            return []

        def one(el: etree._Element) -> StructureMap:
            source_ref = self._helpers.resolve_ref_text(el.find("str:Source", namespaces=self._ns), object)
            target_ref = self._helpers.resolve_ref_text(el.find("str:Target", namespaces=self._ns), object)
            return StructureMap(
                id=required(el, "id"),
                uri=el.get("uri"),
                links=self._helpers.read_links(el),
                maintainer=self._helpers.resolve_agency(required(el, "agencyID"), agencies),
                version=el.get("version", "1.0"),
                name=self._helpers.read_istring(el),
                description=self._helpers.read_istring(el, "Description"),
                source=source_ref,
                target=target_ref,
                maps=tuple(self._read_component_maps_30(el)),
                **self._read_structure_map_parts(el),
                annotations=self._helpers.read_annotations(el),
                valid_from=self._helpers.read_datetime_attr(el, "validFrom"),
                valid_to=self._helpers.read_datetime_attr(el, "validTo"),
                **self._helpers.read_reference_attrs(el),
            )

        return build_each_xml(container.findall("str:StructureMap", namespaces=self._ns), one, StructureMap, refused)

    def _read_hierarchical_code(
        self,
        hc_el: etree._Element,
        index: dict[str, object],
        levels_by_id: dict[str, Level] | None = None,
    ) -> HierarchicalCode:
        """Recursively read a HierarchicalCode element."""
        code_ref: Ref[Code] | None = None
        code_el = hc_el.find("str:Code", namespaces=self._ns)
        if code_el is not None:
            resolved = self._helpers.resolve_ref(code_el, Code)
            if isinstance(resolved, Ref):
                code_ref = resolved

        children = [
            self._read_hierarchical_code(child_el, index, levels_by_id)
            for child_el in hc_el.findall("str:HierarchicalCode", namespaces=self._ns)
        ]

        # SDMX 3.1: optional <Level> is text containing the level id (an
        # ``IDType`` per the XSD), not a wrapped Ref. The pre-XSD-validation
        # reader expected a wrapped Ref; we now accept both shapes for legacy
        # tolerance.
        level: Level | None = None
        level_el = hc_el.find("str:Level", namespaces=self._ns)
        if level_el is not None and (level_id := (level_el.text or "").strip()):
            # Text. A nested ``<Ref>`` is refused by the closed-world check
            # before this runs (#435, #458).
            level = node_level(levels_by_id or {}, level_id)

        return HierarchicalCode(
            id=required(hc_el, "id"),
            uri=hc_el.get("uri"),
            links=self._helpers.read_links(hc_el),
            code=code_ref,
            children=children,
            annotations=self._helpers.read_annotations(hc_el),
            valid_from=self._helpers.read_datetime_attr(hc_el, "validFrom"),
            valid_to=self._helpers.read_datetime_attr(hc_el, "validTo"),
            level=level,
        )

    def _read_hierarchies(
        self,
        structures: etree._Element,
        index: dict[str, object],
        agencies: dict[str, Agency],
        refused: list[Refusal],
    ) -> list[Hierarchy]:
        container = structures.find("str:Hierarchies", namespaces=self._ns)
        if container is None:
            return []

        def one(h_el: etree._Element) -> Hierarchy:
            # The levels root-first, down the chain each `Level` holds (#422).
            levels: list[Level] = []
            levels_by_id: dict[str, Level] = {}
            for lv_el in self._level_chain(h_el):
                cf_el = lv_el.find("str:CodingFormat", namespaces=self._ns)
                lv = Level(
                    id=required(lv_el, "id"),
                    uri=lv_el.get("uri"),
                    links=self._helpers.read_links(lv_el),
                    name=self._helpers.read_istring(lv_el),
                    description=self._helpers.read_istring(lv_el, "Description"),
                    coding_format=self._helpers.read_text_format(cf_el) if cf_el is not None else None,
                    annotations=self._helpers.read_annotations(lv_el),
                )
                levels.append(lv)
                levels_by_id[lv.id] = lv

            tree = [
                self._read_hierarchical_code(hc_el, index, levels_by_id or None)
                for hc_el in h_el.findall("str:HierarchicalCode", namespaces=self._ns)
            ]
            return Hierarchy(
                id=required(h_el, "id"),
                uri=h_el.get("uri"),
                links=self._helpers.read_links(h_el),
                maintainer=self._helpers.resolve_agency(required(h_el, "agencyID"), agencies),
                version=h_el.get("version", "1.0"),
                name=self._helpers.read_istring(h_el),
                description=self._helpers.read_istring(h_el, "Description"),
                has_formal_levels=flag(h_el, "hasFormalLevels"),
                levels=levels,
                tree=tree,
                annotations=self._helpers.read_annotations(h_el),
                valid_from=self._helpers.read_datetime_attr(h_el, "validFrom"),
                valid_to=self._helpers.read_datetime_attr(h_el, "validTo"),
                **self._helpers.read_reference_attrs(h_el),
            )

        return build_each_xml(container.findall("str:Hierarchy", namespaces=self._ns), one, Hierarchy, refused)

    def _level_chain(self, h_el: etree._Element) -> list[etree._Element]:
        """A hierarchy's `Level` elements, each the next one down from the last."""
        chain: list[etree._Element] = []
        holder: etree._Element | None = h_el
        while holder is not None and (lv_el := holder.find("str:Level", namespaces=self._ns)) is not None:
            chain.append(lv_el)
            holder = lv_el
        return chain

    def _read_hierarchy_associations(
        self,
        structures: etree._Element,
        agencies: dict[str, Agency],
        refused: list[Refusal],
    ) -> list[HierarchyAssociation]:
        """Parse the v3.0 / 3.1 ``<str:HierarchyAssociations>`` container.

        Per ``HierarchyAssociationType``: required ``<LinkedHierarchy>``,
        required ``<LinkedObject>``, optional ``<ContextObject>`` — all URN-text
        refs.
        """
        container = structures.find("str:HierarchyAssociations", namespaces=self._ns)
        if container is None:
            return []

        def one(ha_el: etree._Element) -> HierarchyAssociation:
            linked_h_el = ha_el.find("str:LinkedHierarchy", namespaces=self._ns)
            linked_h = self._helpers.resolve_ref_text(linked_h_el, Hierarchy) if linked_h_el is not None else None
            linked_obj_el = ha_el.find("str:LinkedObject", namespaces=self._ns)
            linked_obj = self._helpers.resolve_ref_text(linked_obj_el, object) if linked_obj_el is not None else None
            context_el = ha_el.find("str:ContextObject", namespaces=self._ns)
            context_obj = self._helpers.resolve_ref_text(context_el, object) if context_el is not None else None
            return HierarchyAssociation(
                id=required(ha_el, "id"),
                uri=ha_el.get("uri"),
                links=self._helpers.read_links(ha_el),
                maintainer=self._helpers.resolve_agency(required(ha_el, "agencyID"), agencies),
                version=ha_el.get("version", "1.0"),
                name=self._helpers.read_istring(ha_el),
                description=self._helpers.read_istring(ha_el, "Description"),
                annotations=self._helpers.read_annotations(ha_el),
                valid_from=self._helpers.read_datetime_attr(ha_el, "validFrom"),
                valid_to=self._helpers.read_datetime_attr(ha_el, "validTo"),
                **self._helpers.read_reference_attrs(ha_el),
                linked_hierarchy=linked_h,
                linked_object=linked_obj,
                context_object=context_obj,
            )

        return build_each_xml(
            container.findall("str:HierarchyAssociation", namespaces=self._ns),
            one,
            HierarchyAssociation,
            refused,
        )

    # ── MetadataStructures (MSD) ──────────────────────────────────────────────────

    def _read_metadata_attribute(self, attr_el: etree._Element, index: dict[str, object]) -> MetadataAttribute:
        concept_ref: Ref[Concept] | None = None
        ci_el = attr_el.find("str:ConceptIdentity", namespaces=self._ns)
        if ci_el is not None:
            concept_ref = self._helpers.resolve_ref(ci_el, Concept)

        representation = None
        enumeration_format = None
        rep_el = attr_el.find("str:LocalRepresentation", namespaces=self._ns)
        if rep_el is not None:
            representation = self._helpers.read_enumerated_representation(rep_el, index)
            enumeration_format = self._helpers.read_enumeration_format(rep_el)

        max_occurs_attr = attr_el.get("maxOccurs")
        if max_occurs_attr is None:
            max_occurs: int | None = 1
        elif max_occurs_attr == "unbounded":
            max_occurs = None
        else:
            max_occurs = self._helpers.read_int(attr_el, "maxOccurs", default=1)

        children = [
            self._read_metadata_attribute(c, index)
            for c in attr_el.findall("str:MetadataAttribute", namespaces=self._ns)
        ]

        return MetadataAttribute(
            id=component_id(attr_el, concept_ref),
            uri=attr_el.get("uri"),
            links=self._helpers.read_links(attr_el),
            concept=concept_ref,
            representation=representation,
            enumeration_format=enumeration_format,
            # XSD default for both attributes is 1.
            min_occurs=self._helpers.read_int(attr_el, "minOccurs", default=1),
            max_occurs=max_occurs,
            is_presentational=flag(attr_el, "isPresentational"),
            children=ItemList(children),
            annotations=self._helpers.read_annotations(attr_el),
        )

    def _read_metadata_structures(
        self,
        structures: etree._Element,
        index: dict[str, object],
        agencies: dict[str, Agency],
        refused: list[Refusal],
    ) -> list[MetadataStructure]:
        container = structures.find("str:MetadataStructures", namespaces=self._ns)
        if container is None:
            return []

        def one(msd_el: etree._Element) -> MetadataStructure:
            components_el = msd_el.find("str:MetadataStructureComponents", namespaces=self._ns)
            attrs: list[MetadataAttribute] = []
            if components_el is not None:
                attr_list_el = components_el.find("str:MetadataAttributeList", namespaces=self._ns)
                if attr_list_el is not None:
                    attrs = [
                        self._read_metadata_attribute(a, index)
                        for a in attr_list_el.findall("str:MetadataAttribute", namespaces=self._ns)
                    ]
            return MetadataStructure(
                id=required(msd_el, "id"),
                uri=msd_el.get("uri"),
                links=self._helpers.read_links(msd_el),
                maintainer=self._helpers.resolve_agency(required(msd_el, "agencyID"), agencies),
                version=msd_el.get("version", "1.0"),
                name=self._helpers.read_istring(msd_el),
                description=self._helpers.read_istring(msd_el, "Description"),
                attributes=ItemList(attrs),
                attribute_list_annotations=self._helpers.read_child_annotations(components_el, "MetadataAttributeList"),
                attribute_list_uri=self._helpers.read_child_uri(components_el, "MetadataAttributeList"),
                attribute_list_links=self._helpers.read_child_links(components_el, "MetadataAttributeList"),
                annotations=self._helpers.read_annotations(msd_el),
                valid_from=self._helpers.read_datetime_attr(msd_el, "validFrom"),
                valid_to=self._helpers.read_datetime_attr(msd_el, "validTo"),
                **self._helpers.read_reference_attrs(msd_el),
            )

        return build_each_xml(
            container.findall("str:MetadataStructure", namespaces=self._ns),
            one,
            MetadataStructure,
            refused,
        )

    # ── Metadataflows ─────────────────────────────────────────────────────────────

    def _read_metadata_provision_agreements(
        self,
        structures: etree._Element,
        agencies: dict[str, Agency],
        refused: list[Refusal],
    ) -> list[MetadataProvisionAgreement]:
        container = structures.find("str:MetadataProvisionAgreements", namespaces=self._ns)
        if container is None:
            return []

        def one(el: etree._Element) -> MetadataProvisionAgreement:
            mdf_el = el.find("str:Metadataflow", namespaces=self._ns)
            provider_el = el.find("str:MetadataProvider", namespaces=self._ns)
            return MetadataProvisionAgreement(
                id=required(el, "id"),
                uri=el.get("uri"),
                links=self._helpers.read_links(el),
                maintainer=self._helpers.resolve_agency(required(el, "agencyID"), agencies),
                version=el.get("version", "1.0"),
                name=self._helpers.read_istring(el),
                description=self._helpers.read_istring(el, "Description"),
                annotations=self._helpers.read_annotations(el),
                metadataflow=(None if mdf_el is None else self._helpers.resolve_ref(mdf_el, Metadataflow)),
                metadata_provider=(
                    None if provider_el is None else self._helpers.resolve_ref(provider_el, MetadataProvider)
                ),
                # Wildcard URNs, so text rather than resolvable refs.
                targets=tuple(
                    x.text.strip() for x in el.findall("str:Target", namespaces=self._ns) if x.text and x.text.strip()
                ),
                valid_from=self._helpers.read_datetime_attr(el, "validFrom"),
                valid_to=self._helpers.read_datetime_attr(el, "validTo"),
                **self._helpers.read_reference_attrs(el),
            )

        return build_each_xml(
            container.findall("str:MetadataProvisionAgreement", namespaces=self._ns),
            one,
            MetadataProvisionAgreement,
            refused,
        )

    def _read_metadataflows(
        self,
        structures: etree._Element,
        agencies: dict[str, Agency],
        refused: list[Refusal],
    ) -> list[Metadataflow]:
        container = structures.find("str:Metadataflows", namespaces=self._ns)
        if container is None:
            return []

        def one(mdf_el: etree._Element) -> Metadataflow:
            # 3.0: Structure is a URN-text element (not a wrapped Ref).
            structure_ref: Ref[MetadataStructure] | None = None
            struct_el = mdf_el.find("str:Structure", namespaces=self._ns)
            if struct_el is not None:
                structure_ref = self._helpers.resolve_ref(struct_el, MetadataStructure)
            # 3.0: Target elements (URN-text, may be wildcarded) — at least one
            # required by the XSD.
            targets = tuple(
                t.text.strip() for t in mdf_el.findall("str:Target", namespaces=self._ns) if t.text and t.text.strip()
            )
            return Metadataflow(
                id=required(mdf_el, "id"),
                uri=mdf_el.get("uri"),
                links=self._helpers.read_links(mdf_el),
                maintainer=self._helpers.resolve_agency(required(mdf_el, "agencyID"), agencies),
                version=mdf_el.get("version", "1.0"),
                name=self._helpers.read_istring(mdf_el),
                description=self._helpers.read_istring(mdf_el, "Description"),
                annotations=self._helpers.read_annotations(mdf_el),
                structure=structure_ref,
                targets=targets,
                valid_from=self._helpers.read_datetime_attr(mdf_el, "validFrom"),
                valid_to=self._helpers.read_datetime_attr(mdf_el, "validTo"),
                **self._helpers.read_reference_attrs(mdf_el),
            )

        return build_each_xml(container.findall("str:Metadataflow", namespaces=self._ns), one, Metadataflow, refused)

    def read(self, data: bytes, *, registry: SupportsAddAll | None = None) -> StructureMessage:
        """Parse SDMX-ML 3.0 bytes into a StructureMessage.

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
            msg = self._read(data, index)
        if registry is not None:
            registry.add_all(msg.all_artefacts())
        return msg

    def _read(self, data: bytes, index: dict[str, object]) -> StructureMessage:
        root = etree.fromstring(data)
        header, header_lost = read_header(root, com=self._ns["com"])
        structures = root.find("mes:Structures", namespaces=self._ns)
        if structures is None:
            return StructureMessage(header=header, lost=() if header_lost is None else (header_lost,))

        # Artefacts refused for contradicting the schema, each read on past
        # rather than failing the document (#435).
        refused: list[Refusal] = []

        # Phase 1: parse leaf artefacts (no refs to other artefacts)
        organisation_schemes = self._read_organisation_schemes(structures, refused)
        agency_schemes = organisation_schemes.agency
        data_provider_schemes = organisation_schemes.data_provider
        metadata_provider_schemes = organisation_schemes.metadata_provider

        # Build agency lookup for opportunistic resolution in subsequent phases
        agencies: dict[str, Agency] = {a.id: a for s in agency_schemes for a in s.agencies}

        codelists = self._helpers.read_codelists(structures, agencies, refused)
        value_lists = self._helpers.read_value_lists(structures, agencies, refused)
        concept_schemes = self._helpers.read_concept_schemes(structures, agencies, refused)
        category_schemes = self._helpers.read_category_schemes(structures, agencies, refused)

        # Phase 2: build resolution index

        self._helpers.index_all(index, codelists, Codelist)
        self._helpers.index_all(index, value_lists, ValueList)

        for cs in concept_schemes:
            self._helpers.index_artefact(index, cs, ConceptScheme)
            for concept in cs.concepts:
                self._helpers.index_item(index, concept, cs, ConceptScheme.sdmx_package, "Concept")

        self._helpers.index_all(index, category_schemes, CategoryScheme)

        # Phase 3: parse artefacts that reference codelists / concepts
        data_structures = self._read_data_structures(structures, index, agencies, refused)
        self._helpers.index_all(index, data_structures, DataStructure)

        dataflows = self._helpers.read_dataflows(structures, agencies, refused)
        self._helpers.index_all(index, dataflows, Dataflow)

        # Phase 4: parse artefacts that reference DSDs / dataflows
        categorisations = self._helpers.read_categorisations(structures, agencies, refused)
        provision_agreements = self._helpers.read_provision_agreements(structures, agencies, refused)
        constraints = self._helpers.read_constraints(structures, agencies, refused)
        availability_constraints = self._helpers.read_availability_constraints(structures, refused)
        representation_maps = self._read_representation_maps(structures, agencies, refused)
        structure_maps = self._read_structure_maps(structures, agencies, refused)
        concept_scheme_maps = self._read_item_scheme_maps(
            structures,
            "ConceptSchemeMaps",
            "ConceptSchemeMap",
            ConceptSchemeMap,
            agencies,
            refused,
        )
        category_scheme_maps = self._read_item_scheme_maps(
            structures,
            "CategorySchemeMaps",
            "CategorySchemeMap",
            CategorySchemeMap,
            agencies,
            refused,
        )
        organisation_scheme_maps = self._read_item_scheme_maps(
            structures,
            "OrganisationSchemeMaps",
            "OrganisationSchemeMap",
            OrganisationSchemeMap,
            agencies,
            refused,
        )
        hierarchies = self._read_hierarchies(structures, index, agencies, refused)
        # HierarchyAssociations reference Hierarchies and identifiable artefacts
        # already indexed in phases 2/3; index Hierarchies first so the
        # association reader can resolve LinkedHierarchy refs.
        self._helpers.index_all(index, hierarchies, Hierarchy)
        hierarchy_associations = self._read_hierarchy_associations(structures, agencies, refused)
        metadata_structures = self._read_metadata_structures(structures, index, agencies, refused)
        self._helpers.index_all(index, metadata_structures, MetadataStructure)
        metadataflows = self._read_metadataflows(structures, agencies, refused)
        metadata_provision_agreements = self._read_metadata_provision_agreements(structures, agencies, refused)

        msg = StructureMessage(
            header=header,
            lost=() if header_lost is None else (header_lost,),
            codelists=ArtefactList(codelists),
            value_lists=ArtefactList(value_lists),
            concept_schemes=ArtefactList(concept_schemes),
            data_structures=ArtefactList(data_structures),
            dataflows=ArtefactList(dataflows),
            category_schemes=ArtefactList(category_schemes),
            categorisations=ArtefactList(categorisations),
            provision_agreements=ArtefactList(provision_agreements),
            agency_schemes=ArtefactList(agency_schemes),
            data_provider_schemes=ArtefactList(data_provider_schemes),
            metadata_provider_schemes=ArtefactList(metadata_provider_schemes),
            data_consumer_schemes=ArtefactList(organisation_schemes.data_consumer),
            organisation_unit_schemes=ArtefactList(organisation_schemes.organisation_unit),
            constraints=ArtefactList(constraints),
            availability_constraints=tuple(availability_constraints),
            representation_maps=ArtefactList(representation_maps),
            structure_maps=ArtefactList(structure_maps),
            concept_scheme_maps=ArtefactList(concept_scheme_maps),
            category_scheme_maps=ArtefactList(category_scheme_maps),
            organisation_scheme_maps=ArtefactList(organisation_scheme_maps),
            hierarchies=ArtefactList(hierarchies),
            hierarchy_associations=ArtefactList(hierarchy_associations),
            metadata_structures=ArtefactList(metadata_structures),
            metadataflows=ArtefactList(metadataflows),
            metadata_provision_agreements=ArtefactList(metadata_provision_agreements),
            refused=tuple(refused),
        )
        # Before interning: what the read could not hold (#422).
        return account(structures, msg)
