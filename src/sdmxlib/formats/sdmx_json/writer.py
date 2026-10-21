"""SDMX-JSON 2.0 writer: domain model → JSON bytes.

Serialises a StructureMessage to a valid SDMX-JSON 2.0 Structure message.

Example:
    from sdmxlib.formats.sdmx_json.writer import write

    json_bytes = write(msg)
    json_bytes = write(msg, pretty_print=True)
"""

from collections.abc import Callable, Sequence
from typing import Any, NamedTuple

import attrs
import msgspec

from sdmxlib.formats.gaps import with_derived_targets, without_dropped
from sdmxlib.formats.sdmx_json._periods import zoneless_periods
from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.codelist import Codelist, GeographicCodelist, GeoGridCodelist, wildcard_misfits
from sdmxlib.model.collections import ArtefactList
from sdmxlib.model.constraint import DataConstraint
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import artefact_urn
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.model.validation import ValidationError, check_writable

from ._dto import ENCODER, Link, WireStruct
from ._header import to_meta
from ._structure_dto import (
    MaintainableJson,
    to_agency_scheme,
    to_categorisation,
    to_category_scheme,
    to_category_scheme_map,
    to_codelist,
    to_concept_scheme,
    to_concept_scheme_map,
    to_data_constraint,
    to_data_consumer_scheme,
    to_data_provider_scheme,
    to_data_structure,
    to_dataflow,
    to_geo_grid_codelist,
    to_geographic_codelist,
    to_hierarchy,
    to_hierarchy_association,
    to_metadata_provider_scheme,
    to_metadata_provision_agreement,
    to_metadata_structure,
    to_metadataflow,
    to_organisation_scheme_map,
    to_organisation_unit_scheme,
    to_provision_agreement,
    to_representation_map,
    to_structure_map,
    to_value_list,
)

# ── StructureSets ─────────────────────────────────────────────────────────────


def _write_structure_set_maps(msg: StructureMessage) -> dict[str, list[Any]]:
    """Per-kind top-level map arrays for the SDMX-JSON 2.1 ``/data`` envelope.

    SDMX-JSON 2.1 dropped the 2.0-style ``structureSets`` wrapper (#129): each
    map kind lives at its own ``/data`` key. Returns only the keys present in
    *msg*; the caller folds them into the top-level ``data`` dict (as Struct
    DTOs, encoded in the mixed tree).
    """
    out: dict[str, list[Any]] = {}
    if msg.representation_maps:
        out["representationMaps"] = [to_representation_map(rm) for rm in msg.representation_maps]
    if msg.concept_scheme_maps:
        out["conceptSchemeMaps"] = [to_concept_scheme_map(csm) for csm in msg.concept_scheme_maps]
    if msg.category_scheme_maps:
        out["categorySchemeMaps"] = [to_category_scheme_map(catm) for catm in msg.category_scheme_maps]
    if msg.organisation_scheme_maps:
        out["organisationSchemeMaps"] = [to_organisation_scheme_map(osm) for osm in msg.organisation_scheme_maps]
    if msg.structure_maps:
        out["structureMaps"] = [to_structure_map(sm) for sm in msg.structure_maps]
    return out


# ── Entry point ───────────────────────────────────────────────────────────────


class _Section(NamedTuple):
    """One ``/data`` section of a Structure message.

    Storage erases the artefact type to `Any`; entries are built through
    `_section`, which binds the accessor and converter to one type variable so
    the erasure happens *after* the pairing has been checked. See #210.
    """

    key: str
    # `Any`, not a bound: each entry in the section table has a *different*
    # `T`, and storing them in one list erases it. Python has no existential
    # types, so `Sequence[Any]` is the honest encoding of "some artefact type,
    # fixed per entry, unknown at the table". `section_entry[T]` keeps the two
    # halves consistent at construction, which is where it matters (#288).
    get: Callable[[StructureMessage], Sequence[Any]]
    convert: Callable[[Any], WireStruct]


def _section[T](
    key: str,
    get: Callable[[StructureMessage], Sequence[T]],
    convert: Callable[[T], WireStruct],
) -> _Section:
    """Pair a message accessor with the DTO converter for what it yields.

    ``T`` is inferred from *both* arguments, so a mismatched pair is a type
    error rather than a runtime failure inside ``write()``.

    Args:
        key: The ``/data`` key this section is emitted under.
        get: Reads one artefact collection off the message.
        convert: Renders one of those artefacts as its wire DTO.

    Returns:
        The type-erased section entry.
    """
    return _Section(key, get, convert)


# Table-driven dispatch for the top-level write().
def _data_constraints_for_json(msg: StructureMessage) -> list[DataConstraint]:
    """Every constraint SDMX-JSON writes, including converted 2.1 ones.

    The JSON schema follows the 3.0 model and has no AttachmentConstraint, so
    an attachment constraint is written as its equivalent `DataConstraint`
    rather than refused (#244).
    """
    return [*msg.constraints, *(ac.to_data_constraint() for ac in msg.attachment_constraints)]


def _plain_codelists(msg: StructureMessage) -> list[Codelist]:
    """The message's codelists that are neither geographic kind."""
    return [cl for cl in msg.codelists if not isinstance(cl, GeographicCodelist | GeoGridCodelist)]


def _codelists_of[C: Codelist](msg: StructureMessage, kind: type[C]) -> list[C]:
    """The message's codelists of one geographic kind, each under its own key."""
    return [cl for cl in msg.codelists if isinstance(cl, kind)]


_SECTIONS: list[_Section] = [
    _section("agencySchemes", lambda m: m.agency_schemes, to_agency_scheme),
    _section("dataProviderSchemes", lambda m: m.data_provider_schemes, to_data_provider_scheme),
    _section("metadataProviderSchemes", lambda m: m.metadata_provider_schemes, to_metadata_provider_scheme),
    _section("dataConsumerSchemes", lambda m: m.data_consumer_schemes, to_data_consumer_scheme),
    _section("organisationUnitSchemes", lambda m: m.organisation_unit_schemes, to_organisation_unit_scheme),
    _section("codelists", _plain_codelists, to_codelist),
    _section("geographicCodelists", lambda m: _codelists_of(m, GeographicCodelist), to_geographic_codelist),
    _section("geoGridCodelists", lambda m: _codelists_of(m, GeoGridCodelist), to_geo_grid_codelist),
    _section("valueLists", lambda m: m.value_lists, to_value_list),
    _section("conceptSchemes", lambda m: m.concept_schemes, to_concept_scheme),
    _section("categorySchemes", lambda m: m.category_schemes, to_category_scheme),
    _section("dataStructures", lambda m: m.data_structures, to_data_structure),
    _section("dataflows", lambda m: m.dataflows, to_dataflow),
    _section("categorisations", lambda m: m.categorisations, to_categorisation),
    _section("provisionAgreements", lambda m: m.provision_agreements, to_provision_agreement),
    _section("dataConstraints", _data_constraints_for_json, to_data_constraint),
    _section("hierarchies", lambda m: m.hierarchies, to_hierarchy),
    _section("hierarchyAssociations", lambda m: m.hierarchy_associations, to_hierarchy_association),
    _section("metadataStructures", lambda m: m.metadata_structures, to_metadata_structure),
    _section("metadataflows", lambda m: m.metadataflows, to_metadataflow),
    _section(
        "metadataProvisionAgreements",
        lambda m: m.metadata_provision_agreements,
        to_metadata_provision_agreement,
    ),
]


def _metadata_link_dtos(set_urns: list[str], base_url: str) -> list[Link]:
    """Native SDMX 3.0 ``rel="metadata"`` back-links to MetadataSet reports.

    Mirrors FMR's export-time output: one link per report, ``href`` pointing
    at the metadataset retrieval endpoint and ``urn`` the report URN.
    """
    base = base_url.rstrip("/")
    links: list[Link] = []
    for set_urn in set_urns:
        u = SdmxUrn.parse(set_urn)
        href = f"{base}/metadata/metadataset/{u.agency}/{u.id}/{u.version}"
        links.append(Link(rel="metadata", href=href, urn=set_urn))
    return links


#: The SDMX-JSON 2.0.0 structure schema, where SDMX publishes it and where FMR,
#: the reference implementation, points. Optional in `meta`, but pysdmx will
#: not read a structure message without it (#465). The schema's own `$id` is a
#: GitHub URL that stopped resolving when the repository's `master` dropped its
#: versioned folders; this copy declares the same `$id`.
STRUCTURE_SCHEMA_URL = "https://json.sdmx.org/2.0.0/sdmx-json-structure-schema.json"


def write(
    msg: StructureMessage,
    *,
    pretty_print: bool = False,
    metadata_links: dict[str, list[str]] | None = None,
    base_url: str | None = None,
) -> bytes:
    """Serialise a StructureMessage to SDMX-JSON 2.0 bytes.

    Args:
        msg: The StructureMessage to serialise.
        pretty_print: If True, indent the output with 2-space indentation.
        metadata_links: Optional ``artefact_urn -> [metadataset_urn, ...]``
            map (from ``builders.metadata_annotation.build_metadata_target_links``).
            For each maintainable in the message whose URN is a key, emit a
            native SDMX 3.0 ``rel="metadata"`` link per report — the FMR
            discovery mechanism (#191). Placed on the targeted artefact
            (dataflow, DSD, …), matching FMR.
        base_url: REST service base (e.g. ``https://host/FusionRegistry/sdmx/v2``)
            used to build absolute ``href``s. Required when ``metadata_links``
            is given.

    Returns:
        UTF-8 encoded JSON bytes of a valid SDMX-JSON 2.0 Structure message.

    Raises:
        ValueError: If ``metadata_links`` is given without ``base_url``.
        ValidationError: If an artefact's version is one the structure
            schema's `VersionType` (SDMX-ML 3.0's grammar) does not accept,
            or a constraint's period is a date-time with no time zone.
    """
    return write_kept(
        without_dropped(msg, "json"),
        pretty_print=pretty_print,
        metadata_links=metadata_links,
        base_url=base_url,
    )


def _refuse_unwritable(msg: StructureMessage) -> None:
    """Refuse what SDMX-JSON cannot write, before anything is encoded, as the SDMX-ML writers do.

    Raises:
        ValidationError: If a constraint's period is a date-time with no zone.
    """
    # The structure schema's `VersionType` is SDMX-ML 3.0's, so a version 3.0
    # refuses would be written here schema-invalid (#417).
    for artefact in msg.all_artefacts():
        if isinstance(artefact, MaintainableArtefact):
            check_writable(artefact.version, "json", artefact=f"{type(artefact).__name__}:{artefact.id}")
    # Nor can it write a date-time period with no zone, which SDMX-ML can (#496).
    constraints = [*msg.constraints, *msg.attachment_constraints]
    if issues := [issue for cc in constraints for issue in zoneless_periods(cc)]:
        raise ValidationError(issues)


def write_kept(
    msg: StructureMessage,
    *,
    pretty_print: bool = False,
    metadata_links: dict[str, list[str]] | None = None,
    base_url: str | None = None,
) -> bytes:
    """`write` for a message `without_dropped` has already narrowed to SDMX-JSON.

    `serialize` leaves out what the format cannot hold before it validates,
    then calls this: through `write`, the same walk ran twice, and it was half
    the cost of an SDMX-ML 2.1 DSD write (#464). Arguments as `write`.

    Raises:
        ValueError: If ``metadata_links`` is given without ``base_url``.
    """
    link_base: str | None = None
    if metadata_links:
        if base_url is None:
            msg_err = "base_url is required when metadata_links is provided"
            raise ValueError(msg_err)
        link_base = base_url
    # A metadataflow read from 2.1 is given the targets SDMX-JSON requires.
    # What it cannot hold at all, a HierarchicalCodelist say, `write` has
    # already left out, its hierarchies written without it (#421).
    msg = with_derived_targets(msg)
    # SDMX-JSON's member value is a plain id, so a `%` wildcard is written as
    # the codes it matches (#451); `validate` refuses one that cannot be.
    codelists = [
        cl if wildcard_misfits(cl, msg.codelists) else cl.expand_wildcards(msg.codelists) for cl in msg.codelists
    ]
    msg = attrs.evolve(msg, codelists=ArtefactList(codelists))

    _refuse_unwritable(msg)

    data: dict[str, object] = {}

    for sec in _SECTIONS:
        items = sec.get(msg)
        if not items:
            continue
        dtos: list[Any] = []
        for item in items:
            dto = sec.convert(item)
            if metadata_links and link_base is not None and isinstance(dto, MaintainableJson):
                set_urns = metadata_links.get(str(artefact_urn(item)))
                if set_urns:
                    # Extend rather than assign: the converter may already have
                    # put the artefact's `@uri` here under `rel="describedby"`,
                    # and replacing the list would drop it (#238). A report a
                    # held link already points at is not linked twice (#422).
                    held = dto.links or []
                    linked = {link.urn for link in held if link.rel == "metadata"}
                    fresh = [u for u in set_urns if u not in linked]
                    dto.links = [*held, *_metadata_link_dtos(fresh, link_base)] or None
            dtos.append(dto)
        data[sec.key] = dtos

    # SDMX-JSON 2.1: each map kind lives at its own top-level key under
    # /data (#129) rather than the legacy ``structureSets`` wrapper.
    data.update(_write_structure_set_maps(msg))

    # `data` is a mixed tree: migrated sections are Struct DTOs, not-yet-migrated
    # ones are dicts — msgspec encodes both. `meta` uses the shared foundation
    # Structs.
    doc: dict[str, object] = {
        "meta": to_meta(msg.header, schema=STRUCTURE_SCHEMA_URL),
        "data": data,
    }

    encoded = ENCODER.encode(doc)
    return msgspec.json.format(encoded, indent=2) if pretty_print else encoded
