"""SDMX-JSON 2.0 reader: JSON → domain model.

Parses SDMX-JSON 2.0 Structure messages into the sdmxlib domain model.
All references within the message are auto-resolved where possible.
"""

import functools
import typing
from collections.abc import Callable, Iterable

import attrs
import msgspec

from sdmxlib.formats._refuse import Identity, build_each
from sdmxlib.formats._tags import message_language
from sdmxlib.model._resolvers import document_binding
from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.category import Categorisation, CategoryScheme
from sdmxlib.model.codelist import Codelist, GeographicCodelist, GeoGridCodelist
from sdmxlib.model.collections import ArtefactList, SupportsId
from sdmxlib.model.concept import ConceptScheme
from sdmxlib.model.constraint import DataConstraint
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import (
    DataStructure,
)
from sdmxlib.model.errors import ReadAssumption, ReadGap, Refusal
from sdmxlib.model.hierarchy import Hierarchy, HierarchyAssociation
from sdmxlib.model.mapping import (
    CategorySchemeMap,
    ConceptSchemeMap,
    OrganisationSchemeMap,
    RepresentationMap,
    StructureMap,
)
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.metadata_provision import MetadataProvisionAgreement
from sdmxlib.model.metadataflow import Metadataflow
from sdmxlib.model.metadatastructure import MetadataStructure
from sdmxlib.model.organisation import (
    Agency,
    AgencyScheme,
    DataConsumerScheme,
    DataProviderScheme,
    MetadataProviderScheme,
    OrganisationUnitScheme,
)
from sdmxlib.model.provision import ProvisionAgreement
from sdmxlib.model.ref import SupportsUrn, artefact_urn
from sdmxlib.model.registry import SupportsAddAll
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.model.valuelist import ValueList

from ._header import MetaJson, from_meta
from ._losses import assumed, raw_sections, undeclared, unread_sections
from ._structure_dto import (
    MaintainableJson,
    StructureDataJson,
    StructureMessageJson,
)
from ._structure_ingest import (
    from_agency_scheme,
    from_categorisation,
    from_category_scheme,
    from_category_scheme_map,
    from_codelist,
    from_concept_scheme,
    from_concept_scheme_map,
    from_data_constraint,
    from_data_consumer_scheme,
    from_data_provider_scheme,
    from_data_structure,
    from_dataflow,
    from_geo_grid_codelist,
    from_geographic_codelist,
    from_hierarchy,
    from_hierarchy_association,
    from_metadata_provider_scheme,
    from_metadata_provision_agreement,
    from_metadata_structure,
    from_metadataflow,
    from_organisation_scheme_map,
    from_organisation_unit_scheme,
    from_provision_agreement,
    from_representation_map,
    from_structure_map,
    from_value_list,
    version_of,
)

# ── Index helpers ─────────────────────────────────────────────────────────────


def _index_each(index: dict[str, object], artefacts: Iterable[SupportsUrn], artefact_type: type[SupportsUrn]) -> None:
    """`_index_artefact` for each of `artefacts`."""
    for obj in artefacts:
        _index_artefact(index, obj, artefact_type)


def _index_artefact(index: dict[str, object], obj: SupportsUrn, artefact_type: type[SupportsUrn]) -> None:
    """Add a maintainable artefact to the resolution index."""
    package = artefact_type.sdmx_package
    cls = artefact_type.sdmx_class
    urn = SdmxUrn.make(
        artefact_type,
        package=package,
        artefact_class=cls,
        agency=obj.agency_id,
        id=obj.id,
        version=obj.version,
    )
    index[str(urn)] = obj
    urn_latest = SdmxUrn.make(artefact_type, package=package, artefact_class=cls, agency=obj.agency_id, id=obj.id)
    index[str(urn_latest)] = obj


def _index_item(index: dict[str, object], item: SupportsId, parent: SupportsUrn, package: str, cls: str) -> None:
    """Add an item (e.g. Concept) to the index under its item URN."""
    urn = SdmxUrn.make(
        object,
        package=package,
        artefact_class=cls,
        agency=parent.agency_id,
        id=parent.id,
        version=parent.version,
        item_id=item.id,
    )
    index[str(urn)] = item
    urn_latest = SdmxUrn.make(
        object,
        package=package,
        artefact_class=cls,
        agency=parent.agency_id,
        id=parent.id,
        item_id=item.id,
    )
    index[str(urn_latest)] = item


# ── Agency resolution ─────────────────────────────────────────────────────────


def _resolve_agency(agency_id: str, agencies: dict[str, Agency]) -> Agency:
    """Return the rich Agency if parsed from an AgencyScheme, else a minimal one."""
    return agencies.get(agency_id, Agency(id=agency_id))


# ── Per-artefact refusal ──────────────────────────────────────────────────────


def _dto_identity(dto: MaintainableJson) -> Identity:
    """A maintainable DTO's agency, id and version, to name it if refused."""
    return dto.agency_id, dto.id, version_of(dto)


def _each[D: MaintainableJson, T](
    dtos: list[D] | None, build: Callable[[D], T], cls: type[MaintainableArtefact], refused: list[Refusal]
) -> list[T]:
    """Build one artefact per DTO, refusing each that contradicts the schema (#435).

    Takes the section as the message holds it, absent included: a caller's
    ``section or []`` made the empty literal part of `D`, which then could not
    type the ``build`` lambda's parameter.
    """
    return build_each(() if dtos is None else dtos, build, cls, _dto_identity, refused)


# ── OrganisationSchemes ───────────────────────────────────────────────────────


def _read_organisation_schemes(
    data: StructureDataJson,
    refused: list[Refusal],
) -> tuple[list[AgencyScheme], list[DataProviderScheme], list[MetadataProviderScheme]]:
    agency_dtos = data.agency_schemes or []
    provider_dtos = data.data_provider_schemes or []
    metadata_provider_dtos = data.metadata_provider_schemes or []
    return (
        _each(agency_dtos, from_agency_scheme, AgencyScheme, refused),
        _each(provider_dtos, from_data_provider_scheme, DataProviderScheme, refused),
        _each(metadata_provider_dtos, from_metadata_provider_scheme, MetadataProviderScheme, refused),
    )


def _read_consumer_and_unit_schemes(
    data: StructureDataJson, refused: list[Refusal]
) -> tuple[list[DataConsumerScheme], list[OrganisationUnitScheme]]:
    """The two organisation schemes with no role elsewhere in the message (#469)."""
    return (
        _each(data.data_consumer_schemes or [], from_data_consumer_scheme, DataConsumerScheme, refused),
        _each(data.organisation_unit_schemes or [], from_organisation_unit_scheme, OrganisationUnitScheme, refused),
    )


# ── Codelists ─────────────────────────────────────────────────────────────────


def _read_codelists(data: StructureDataJson, agencies: dict[str, Agency], refused: list[Refusal]) -> list[Codelist]:
    """Every codelist, the two geographic kinds' sections included — they are codelists."""
    return [
        *_each(
            data.codelists,
            lambda clj: from_codelist(clj, _resolve_agency(clj.agency_id, agencies)),
            Codelist,
            refused,
        ),
        *_each(
            data.geographic_codelists,
            lambda clj: from_geographic_codelist(clj, _resolve_agency(clj.agency_id, agencies)),
            GeographicCodelist,
            refused,
        ),
        *_each(
            data.geo_grid_codelists,
            lambda clj: from_geo_grid_codelist(clj, _resolve_agency(clj.agency_id, agencies)),
            GeoGridCodelist,
            refused,
        ),
    ]


def _read_value_lists(data: StructureDataJson, agencies: dict[str, Agency], refused: list[Refusal]) -> list[ValueList]:
    return _each(
        data.value_lists,
        lambda vlj: from_value_list(vlj, _resolve_agency(vlj.agency_id, agencies)),
        ValueList,
        refused,
    )


# ── ConceptSchemes ────────────────────────────────────────────────────────────


def _read_concept_schemes(
    data: StructureDataJson,
    agencies: dict[str, Agency],
    refused: list[Refusal],
) -> list[ConceptScheme]:
    dtos = data.concept_schemes or []
    return _each(
        dtos,
        lambda csj: from_concept_scheme(csj, _resolve_agency(csj.agency_id, agencies)),
        ConceptScheme,
        refused,
    )


# ── CategorySchemes ───────────────────────────────────────────────────────────


def _read_category_schemes(
    data: StructureDataJson, agencies: dict[str, Agency], refused: list[Refusal]
) -> list[CategoryScheme]:
    dtos = data.category_schemes or []
    return _each(
        dtos, lambda csj: from_category_scheme(csj, _resolve_agency(csj.agency_id, agencies)), CategoryScheme, refused
    )


# ── DataStructures ────────────────────────────────────────────────────────────


def _read_data_structures(
    data: StructureDataJson,
    agencies: dict[str, Agency],
    refused: list[Refusal],
) -> list[DataStructure]:
    dtos = data.data_structures or []
    return _each(
        dtos,
        lambda dsd: from_data_structure(dsd, _resolve_agency(dsd.agency_id, agencies)),
        DataStructure,
        refused,
    )


# ── Dataflows ─────────────────────────────────────────────────────────────────


def _read_dataflows(
    data: StructureDataJson,
    agencies: dict[str, Agency],
    refused: list[Refusal],
) -> list[Dataflow]:
    dtos = data.dataflows or []
    return _each(dtos, lambda df: from_dataflow(df, _resolve_agency(df.agency_id, agencies)), Dataflow, refused)


# ── Categorisations ───────────────────────────────────────────────────────────


def _read_categorisations(
    data: StructureDataJson,
    agencies: dict[str, Agency],
    refused: list[Refusal],
) -> list[Categorisation]:
    dtos = data.categorisations or []
    return _each(
        dtos, lambda c: from_categorisation(c, _resolve_agency(c.agency_id, agencies)), Categorisation, refused
    )


# ── ProvisionAgreements ───────────────────────────────────────────────────────


def _read_provision_agreements(
    data: StructureDataJson,
    agencies: dict[str, Agency],
    refused: list[Refusal],
) -> list[ProvisionAgreement]:
    dtos = data.provision_agreements or []
    return _each(
        dtos,
        lambda pa: from_provision_agreement(pa, _resolve_agency(pa.agency_id, agencies)),
        ProvisionAgreement,
        refused,
    )


def _read_constraints(
    data: StructureDataJson,
    agencies: dict[str, Agency],
    refused: list[Refusal],
) -> list[DataConstraint]:
    dtos = data.data_constraints or []
    return _each(
        dtos,
        lambda cc: from_data_constraint(cc, _resolve_agency(cc.agency_id, agencies)),
        DataConstraint,
        refused,
    )


def _read_structure_sets(
    data: StructureDataJson,
    agencies: dict[str, Agency],
    refused: list[Refusal],
) -> tuple[
    list[RepresentationMap],
    list[StructureMap],
    list[ConceptSchemeMap],
    list[CategorySchemeMap],
    list[OrganisationSchemeMap],
]:
    """Parse the SDMX-JSON 2.1 top-level map arrays into their model types.

    Each map kind lives at its own ``/data`` key:

    * ``representationMaps``
    * ``structureMaps`` (covers DSD-source and Dataflow-source maps)
    * ``conceptSchemeMaps``
    * ``categorySchemeMaps``
    * ``organisationSchemeMaps``

    Each map is a top-level maintainable carrying its own ``agencyID`` +
    ``version``.
    """
    rep_dtos = data.representation_maps or []
    csm_dtos = data.concept_scheme_maps or []
    catm_dtos = data.category_scheme_maps or []
    osm_dtos = data.organisation_scheme_maps or []
    sm_dtos = data.structure_maps or []
    return (
        _each(
            rep_dtos,
            lambda rm: from_representation_map(rm, _resolve_agency(rm.agency_id, agencies)),
            RepresentationMap,
            refused,
        ),
        _each(
            sm_dtos,
            lambda sm: from_structure_map(sm, _resolve_agency(sm.agency_id, agencies)),
            StructureMap,
            refused,
        ),
        _each(
            csm_dtos,
            lambda csm: from_concept_scheme_map(csm, _resolve_agency(csm.agency_id, agencies)),
            ConceptSchemeMap,
            refused,
        ),
        _each(
            catm_dtos,
            lambda catm: from_category_scheme_map(catm, _resolve_agency(catm.agency_id, agencies)),
            CategorySchemeMap,
            refused,
        ),
        _each(
            osm_dtos,
            lambda osm: from_organisation_scheme_map(osm, _resolve_agency(osm.agency_id, agencies)),
            OrganisationSchemeMap,
            refused,
        ),
    )


# ── Hierarchies ──────────────────────────────────────────────────────────────


def _read_hierarchies(
    data: StructureDataJson,
    agencies: dict[str, Agency],
    refused: list[Refusal],
) -> list[Hierarchy]:
    dtos = data.hierarchies or []
    return _each(dtos, lambda h: from_hierarchy(h, _resolve_agency(h.agency_id, agencies)), Hierarchy, refused)


def _read_hierarchy_associations(
    data: StructureDataJson,
    agencies: dict[str, Agency],
    refused: list[Refusal],
) -> list[HierarchyAssociation]:
    dtos = data.hierarchy_associations or []
    return _each(
        dtos,
        lambda ha: from_hierarchy_association(ha, _resolve_agency(ha.agency_id, agencies)),
        HierarchyAssociation,
        refused,
    )


# ── MetadataStructures (MSD) ──────────────────────────────────────────────────


def _read_metadata_structures(
    data: StructureDataJson,
    index: dict[str, object],
    agencies: dict[str, Agency],
    refused: list[Refusal],
) -> list[MetadataStructure]:
    dtos = data.metadata_structures or []
    return _each(
        dtos,
        lambda msd: from_metadata_structure(msd, _resolve_agency(msd.agency_id, agencies), index),
        MetadataStructure,
        refused,
    )


# ── Metadataflows ─────────────────────────────────────────────────────────────


def _read_metadataflows(
    data: StructureDataJson,
    agencies: dict[str, Agency],
    refused: list[Refusal],
) -> list[Metadataflow]:
    dtos = data.metadataflows or []
    return _each(
        dtos, lambda mdf: from_metadataflow(mdf, _resolve_agency(mdf.agency_id, agencies)), Metadataflow, refused
    )


def _read_metadata_provision_agreements(
    data: StructureDataJson,
    agencies: dict[str, Agency],
    refused: list[Refusal],
) -> list[MetadataProvisionAgreement]:
    dtos = data.metadata_provision_agreements or []
    return _each(
        dtos,
        lambda mpa: from_metadata_provision_agreement(mpa, _resolve_agency(mpa.agency_id, agencies)),
        MetadataProvisionAgreement,
        refused,
    )


# ── Entry point ───────────────────────────────────────────────────────────────


def read(data: bytes, *, registry: SupportsAddAll | None = None) -> StructureMessage:
    """Parse SDMX-JSON 2.0 bytes into a StructureMessage.

    References within the message are auto-resolved where URNs match
    artefacts present in the same message.

    If *registry* is provided all parsed artefacts are added to it via
    ``registry.add_all()``, their Refs bound to it for cross-message resolution.
    An artefact that contradicts the schema is refused — recorded in
    `StructureMessage.refused`, never partly built — and the rest are read;
    whether that fails the call is `sdmxlib.formats.parse`'s decision (#435).

    Args:
        data: Raw JSON bytes of an SDMX-JSON 2.0 Structure message.
        registry: Optional Registry to intern artefacts into after parsing.

    Returns:
        A fully constructed StructureMessage with resolved cross-references.

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
    # Artefacts refused for contradicting the schema, each read on past
    # rather than failing the document (#435).
    refused: list[Refusal] = []
    payload, meta = _decode(data, refused)
    with message_language(None if meta is None else meta.content_languages):
        return _build(data, payload, meta, index, refused)


def _build(
    data: bytes, payload: StructureDataJson, meta: MetaJson | None, index: dict[str, object], refused: list[Refusal]
) -> StructureMessage:
    header, header_lost = from_meta(meta)

    # ── Phase 1: parse leaf artefacts (no refs to other artefacts) ──────────
    agency_schemes, data_provider_schemes, metadata_provider_schemes = _read_organisation_schemes(payload, refused)
    data_consumer_schemes, organisation_unit_schemes = _read_consumer_and_unit_schemes(payload, refused)

    # Build agency lookup for opportunistic resolution in subsequent phases
    agencies: dict[str, Agency] = {a.id: a for s in agency_schemes for a in s.agencies}

    codelists = _read_codelists(payload, agencies, refused)
    value_lists = _read_value_lists(payload, agencies, refused)
    category_schemes = _read_category_schemes(payload, agencies, refused)

    # ── Phase 2a: build the codelist index so concept core_representation
    # references can resolve when read in Phase 2b ───────────────────────────

    _index_each(index, codelists, Codelist)
    _index_each(index, value_lists, ValueList)

    # ── Phase 2b: concept schemes (concepts may reference codelists via
    # coreRepresentation enumeration) ───────────────────────────────────────
    concept_schemes = _read_concept_schemes(payload, agencies, refused)

    for cs in concept_schemes:
        _index_artefact(index, cs, ConceptScheme)
        for concept in cs.concepts:
            _index_item(index, concept, cs, ConceptScheme.sdmx_package, "Concept")

    for cat_s in category_schemes:
        _index_artefact(index, cat_s, CategoryScheme)

    # ── Phase 3: parse artefacts that reference codelists / concepts ─────────
    data_structures = _read_data_structures(payload, agencies, refused)
    for dsd in data_structures:
        _index_artefact(index, dsd, DataStructure)

    dataflows = _read_dataflows(payload, agencies, refused)
    for df in dataflows:
        _index_artefact(index, df, Dataflow)

    # ── Phase 4: parse artefacts that reference DSDs / dataflows ────────────
    categorisations = _read_categorisations(payload, agencies, refused)
    provision_agreements = _read_provision_agreements(payload, agencies, refused)
    constraints = _read_constraints(payload, agencies, refused)
    rep_maps, struct_maps, concept_scheme_maps, category_scheme_maps, organisation_scheme_maps = _read_structure_sets(
        payload, agencies, refused
    )
    hierarchies = _read_hierarchies(payload, agencies, refused)
    # Index hierarchies before reading HierarchyAssociations so the latter's
    # linkedHierarchy URN refs resolve against in-message artefacts.
    for h in hierarchies:
        _index_artefact(index, h, Hierarchy)
    hierarchy_associations = _read_hierarchy_associations(payload, agencies, refused)
    metadata_structures = _read_metadata_structures(payload, index, agencies, refused)
    # Index MSDs so Metadataflow refs can resolve in the same message.
    for msd in metadata_structures:
        _index_artefact(index, msd, MetadataStructure)
    metadataflows = _read_metadataflows(payload, agencies, refused)
    metadata_provision_agreements = _read_metadata_provision_agreements(payload, agencies, refused)

    msg = StructureMessage(
        header=header,
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
        data_consumer_schemes=ArtefactList(data_consumer_schemes),
        organisation_unit_schemes=ArtefactList(organisation_unit_schemes),
        constraints=ArtefactList(constraints),
        representation_maps=ArtefactList(rep_maps),
        structure_maps=ArtefactList(struct_maps),
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
    return _account(data, payload, msg, header_lost)


#: Each `StructureDataJson` section and the class it builds. Exact —
#: `test_read_losses` compares it with the struct's fields — so a section the
#: reader learns to read is also one whose losses are counted.
SECTIONS: dict[str, type[MaintainableArtefact]] = {
    "agency_schemes": AgencyScheme,
    "data_provider_schemes": DataProviderScheme,
    "metadata_provider_schemes": MetadataProviderScheme,
    "data_consumer_schemes": DataConsumerScheme,
    "organisation_unit_schemes": OrganisationUnitScheme,
    "codelists": Codelist,
    "geographic_codelists": GeographicCodelist,
    "geo_grid_codelists": GeoGridCodelist,
    "value_lists": ValueList,
    "concept_schemes": ConceptScheme,
    "category_schemes": CategoryScheme,
    "data_structures": DataStructure,
    "dataflows": Dataflow,
    "categorisations": Categorisation,
    "provision_agreements": ProvisionAgreement,
    "data_constraints": DataConstraint,
    "hierarchies": Hierarchy,
    "hierarchy_associations": HierarchyAssociation,
    "metadata_structures": MetadataStructure,
    "metadataflows": Metadataflow,
    "metadata_provision_agreements": MetadataProvisionAgreement,
    "representation_maps": RepresentationMap,
    "structure_maps": StructureMap,
    "concept_scheme_maps": ConceptSchemeMap,
    "category_scheme_maps": CategorySchemeMap,
    "organisation_scheme_maps": OrganisationSchemeMap,
}


class _MetaOnly(msgspec.Struct):
    meta: MetaJson | None = None


def _decode(data: bytes, refused: list[Refusal]) -> tuple[StructureDataJson, MetaJson | None]:
    """`data`'s sections, typed, and its ``meta``; an entry its struct cannot decode is refused alone (#474).

    One typed pass over the whole document is the fast path. msgspec stops it
    at the first entry missing something its struct requires, which used to
    fail the document, so only then is each entry decoded on its own: the ones
    that fail are refused, named by what they say of themselves, and the rest
    are decoded again together, as the fast path would have.
    """
    try:
        doc = msgspec.json.decode(data, type=StructureMessageJson)
    except msgspec.ValidationError:
        pass
    else:
        meta = msgspec.json.decode(doc.meta, type=MetaJson | None) if doc.meta else None
        return doc.data or StructureDataJson(), meta
    kept: dict[str, list[msgspec.Raw]] = {}
    for wire_name, raw in raw_sections(data).items():
        section = _SECTION_NAMES.get(wire_name)
        if section is not None:
            kept[wire_name] = [
                one
                for one in msgspec.json.decode(raw, type=list[msgspec.Raw])
                if _decodes(one, _ENTRY_TYPES[section], SECTIONS[section], refused)
            ]
    meta = msgspec.json.decode(data, type=_MetaOnly).meta
    return msgspec.json.decode(msgspec.json.encode(kept), type=StructureDataJson), meta


def _decodes(one: msgspec.Raw, entry: type, cls: type[MaintainableArtefact], refused: list[Refusal]) -> bool:
    """Whether `entry` decodes `one`; if not, a refusal names it by what it says of its identity.

    Read untyped: the identity may be what is wrong with it.
    """
    try:
        msgspec.json.decode(one, type=entry)
    except msgspec.ValidationError as exc:
        said = msgspec.json.decode(one, type=object)
        fields: dict[object, object] = said if isinstance(said, dict) else {}

        def text(key: str, absent: str) -> str:
            value = fields.get(key)
            return value if isinstance(value, str) else absent

        urn = SdmxUrn[object](
            package=cls.sdmx_package,
            artefact_class=cls.sdmx_class,
            agency=text("agencyID", ""),
            id=text("id", ""),
            version=text("version", "1.0"),
        )
        refused.append(Refusal(artefact_class=cls.sdmx_class, urn=str(urn), reason=str(exc)))
        return False
    return True


def _account(
    data: bytes, payload: StructureDataJson, msg: StructureMessage, header_lost: ReadGap | None
) -> StructureMessage:
    """`msg` with refusals for the sections no struct reads, what it assumed, and its losses to work out when asked.

    The refusals and assumptions are found now: they decide whether a strict
    or lossless `parse` raises, and finding them reads only the section keys
    and the DTOs already decoded. The losses are left to
    `StructureMessage.lost`, because finding them walks every key of every
    artefact, and that walk was 40% of the read (#461). Until then the message
    holds `data`, and nothing else of the read.
    """
    built = frozenset(str(artefact_urn(artefact)) for artefact in msg.all_artefacts())
    unread = unread_sections(raw_sections(data))
    supplied: list[ReadAssumption] = []
    for section, cls in SECTIONS.items():
        dtos: list[MaintainableJson] | None = getattr(payload, section)
        for dto in dtos or ():
            urn = _urn_of(cls, dto)
            if urn in built:
                supplied.extend(assumed(urn, dto))
    return attrs.evolve(
        msg,
        refused=(*msg.refused, *unread),
        assumed=(*msg.assumed, *supplied),
        lost=functools.partial(_losses, data, built, header_lost),
    )


def _losses(data: bytes, built: frozenset[str], header_lost: ReadGap | None) -> tuple[ReadGap, ...]:
    """What `data`'s structs skipped, for each artefact in `built` (#422), after the header's, if it was lost.

    Each entry is decoded twice: typed, as the read did, and untyped, for the
    keys the structs had no field for. One at a time, because an entry the
    read refused does not decode typed, and is no artefact to account for.
    """
    lost: list[ReadGap] = [] if header_lost is None else [header_lost]
    for wire_name, raw in raw_sections(data).items():
        section = _SECTION_NAMES.get(wire_name)
        if section is None:
            continue
        for one in msgspec.json.decode(raw, type=list[msgspec.Raw]):
            try:
                dto: MaintainableJson = msgspec.json.decode(one, type=_ENTRY_TYPES[section])
            except msgspec.ValidationError:
                continue  # refused when read
            urn = _urn_of(SECTIONS[section], dto)
            if urn in built:
                lost.extend(undeclared(urn, msgspec.json.decode(one, type=object), dto))
    return tuple(lost)


#: Each `SECTIONS` key's spelling in the document.
_WIRE_NAMES: dict[str, str] = {field.name: field.encode_name for field in msgspec.structs.fields(StructureDataJson)}


#: Each `SECTIONS` key by its spelling in the document.
_SECTION_NAMES: dict[str, str] = {wire: section for section, wire in _WIRE_NAMES.items()}


def _entry_type(field: msgspec.structs.FieldInfo) -> type:
    """``X`` from a section field declared ``list[X] | None``."""
    (listed,) = [arg for arg in typing.get_args(field.type) if typing.get_origin(arg) is list]
    entry: type = typing.get_args(listed)[0]
    return entry


#: Each `SECTIONS` key's entry struct, for decoding one entry at a time.
_ENTRY_TYPES: dict[str, type] = {field.name: _entry_type(field) for field in msgspec.structs.fields(StructureDataJson)}


def _urn_of(cls: type[MaintainableArtefact], dto: MaintainableJson) -> str:
    """The URN of what `cls` builds from `dto`, from the DTO's own identity."""
    urn = SdmxUrn[object](
        package=cls.sdmx_package,
        artefact_class=cls.sdmx_class,
        agency=dto.agency_id,
        id=dto.id,
        version=version_of(dto),
    )
    return str(urn)
