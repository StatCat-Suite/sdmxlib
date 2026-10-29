"""Read SDMX domain objects from DuckDB relational tables.

Reconstructs domain objects from the fully relational schema.
No JSON parsing.  Each artefact type has a dedicated reader
that issues SQL queries and constructs the attrs object.

Example:
    from sdmxlib.storage import schema, readers

    conn = duckdb.connect("store.duckdb")
    cl = readers.get_artefact(conn, codelist_urn)  # LazyCodelist
    cm = readers.code_map(conn, codelist_urn, lang="en")  # {code_id: label}


`Any` in this module is the untyped shell, deliberately (#288). Row tuples come
back from DuckDB with no static type, and their values flow straight into typed
domain constructors — ``Code(id=row[0], name=...)``. Converting the row types to
``tuple[object, ...]`` was measured: 36 errors, every one a value needing a
narrowing on the way into a constructor parameter whose type the *schema*
already guarantees. That is the trade CLAUDE.md describes as "typed core,
untyped shell": parse, validate, hand typed objects inward. The narrowings would
restate the schema in Python and go stale against it.

What is *not* excused by that: an `Any` that rides out through a public
signature. `_read_spine` returns a `SpineRow` TypedDict for exactly that reason
(#285), and `get_artefact` carries a scoped ``# noqa: ANN401`` explaining why
its dispatch has no static type.
"""

from collections.abc import Callable, Mapping
from datetime import date, datetime
from functools import partial
from typing import Any, Literal, cast

import duckdb
from typing_extensions import TypedDict

from sdmxlib._duckdb import first
from sdmxlib._time import from_storage
from sdmxlib.model._items import find_item, owner_classes, scheme_urn_of
from sdmxlib.model.annotations import Annotations
from sdmxlib.model.binding import DataflowBinding
from sdmxlib.model.category import Categorisation, Category, CategoryScheme
from sdmxlib.model.codelist import (
    CascadeSelection,
    Code,
    Codelist,
    code_of_kind,
    codelist_of_kind,
)
from sdmxlib.model.collections import ItemList, SdmxItem, SupportsId
from sdmxlib.model.concept import Concept, ConceptScheme, ISOConceptReference
from sdmxlib.model.constraint import (
    AvailabilityConstraint,
    ComponentValueSet,
    ConstraintAttachment,
    CubeRegion,
    DataConstraint,
    DataKey,
    DataKeySet,
    DataSetReference,
    KeyValue,
    QueryableDataSource,
    ReferencePeriod,
    RegionValue,
    ReleaseCalendar,
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
    MeasureDimension,
    MetadataAttributeUsage,
    TimeDimension,
)
from sdmxlib.model.errors import Refusal, SdmxParseError, ValidationError, ValidationIssue
from sdmxlib.model.hierarchy import HierarchicalCode, HierarchicalCodelist, Hierarchy, HierarchyAssociation, Level
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
    StructureSet,
)
from sdmxlib.model.metadata_provision import MetadataProvisionAgreement
from sdmxlib.model.metadataflow import Metadataflow
from sdmxlib.model.metadataset import (
    MetadataSet,
    ReportedAttribute,
    ReportedAttributeValue,
)
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
from sdmxlib.model.provision import ProvisionAgreement
from sdmxlib.model.ref import Ref
from sdmxlib.model.representation import Facet, enumeration_ref
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.model.validation import SdmxConformanceError
from sdmxlib.model.valuelist import ValueItem, ValueList
from sdmxlib.storage._checked import checked
from sdmxlib.storage._kinds import (
    CLASS_TO_NAME as _CLASS_TO_NAME,
)
from sdmxlib.storage._parsed import parsed
from sdmxlib.storage._projections import _thread_cursor
from sdmxlib.storage._projections import code_map as code_map  # noqa: PLC0414  # re-export
from sdmxlib.storage.lazy import LazyCodelist, LazyHierarchy
from sdmxlib.storage.resolve import annotation_rows, geo_kind, part_owner
from sdmxlib.storage.resolve import (
    build_enumeration_format as _build_enumeration_format,
)
from sdmxlib.storage.resolve import (
    build_representation as _build_representation,
)
from sdmxlib.storage.resolve import (
    codelist_extensions as _codelist_extensions,
)
from sdmxlib.storage.resolve import (
    istring_sql as _istring_sql,
)
from sdmxlib.storage.resolve import (
    json_to_istring as _json_to_istring,
)
from sdmxlib.storage.resolve import (
    links_of_artefact as _links_of_artefact,
)
from sdmxlib.storage.resolve import (
    map_to_istring as _map_to_istring,
)
from sdmxlib.storage.resolve import (
    read_levels as _read_levels,
)
from sdmxlib.storage.schema import (
    ENUMERATION_FORMAT_COLUMN_NAMES,
    FACET_COLUMN_NAMES,
    TO_TEXT_FORMAT_COLUMN_NAMES,
    sdmx_cursor,
)


def _required(value: datetime | None, column: str) -> datetime:
    """Narrow a NOT NULL timestamp for the checker.

    `from_storage` is `datetime | None` because most callers pass a nullable
    column. These two are declared NOT NULL, which no type can see from here.
    """
    if value is None:  # pragma: no cover - the schema forbids it
        msg = f"{column} is NOT NULL but came back empty"
        raise ValueError(msg)
    return value


# ── Shared helpers ──────────────────────────────────────────────────────


def _read_name_and_description(
    conn: duckdb.DuckDBPyConnection,
    owner_urn: str,
) -> tuple[InternationalString, InternationalString]:
    """A maintainable's name and description, in one statement (#426).

    Every reader needs both, and they used to be two queries. In DuckDB a
    point lookup costs ~0.4 ms however little it returns, so a statement saved
    on every `get()` is worth more than the tidier single-field helper.
    Folding these into the spine read as a join measured *slower* than two
    simple statements (2.3 ms against 1.1 ms) — DuckDB's per-statement cost
    grows with the plan, so simple statements beat one wide one.
    """
    rows = conn.execute(
        parsed(
            conn,
            "SELECT field, lang, text FROM localized_text WHERE owner_urn = ? AND field IN ('name', 'description')",
        ),
        [owner_urn],
    ).fetchall()
    texts: dict[str, dict[str, str]] = {"name": {}, "description": {}}
    for field, lang, text in rows:
        texts[field][lang] = text
    return (
        InternationalString.of(texts["name"]) if texts["name"] else InternationalString(),
        InternationalString.of(texts["description"]) if texts["description"] else InternationalString(),
    )


def _read_annotations(
    conn: duckdb.DuckDBPyConnection,
    owner_urn: str,
) -> Annotations:
    """Read annotations and their text for a single annotated entity.

    Prefer `_read_annotations_for_owners` when loading annotations
    for many siblings.
    """
    return annotation_rows(conn, "a.owner_urn = ?", [owner_urn]).get(owner_urn, Annotations.EMPTY)


def _read_annotations_subtree(
    conn: duckdb.DuckDBPyConnection,
    artefact_urn: str,
) -> dict[str, Annotations]:
    """Bulk-load annotations for an artefact and every item beneath it.

    By ``owner_artefact_urn``, the indexed column the writer sets on every
    annotation an artefact owns: its own, every code / concept / component /
    hierarchy node's, and its unidentifiable parts' (``<artefact urn>#...``,
    `resolve.part_owner`). It is the equality the delete cascade relies on
    (#145), and `resolve.annotations_of_artefact` reads by it too (#443).

    It replaced ``owner_urn = ? OR owner_urn LIKE '%=<suffix>.%' OR
    starts_with(owner_urn, '<urn>#')``, which selected the same rows but could
    use no index: on a 585,675-row annotation table, 21 ms to find a DSD's 2
    annotations, and most of `LocalRegistry.obs()` (#426).

    Returns ``{owner_urn: Annotations}`` for owners that have at least one
    annotation. Callers should default missing keys to ``Annotations()``.
    """
    return _read_annotations_for_owners_predicate(conn, "owner_artefact_urn = ?", [artefact_urn])


def _read_annotations_for_owners_predicate(
    conn: duckdb.DuckDBPyConnection,
    predicate: str,
    params: list[Any],
) -> dict[str, Annotations]:
    """Bulk-load annotations matching a WHERE predicate against ``annotation`` as ``a``.

    Lower-level than `_read_annotations_for_owners` (which expects a
    subquery yielding owner URNs); this one inlines a predicate on the
    annotation row itself, such as "every annotation this artefact owns".
    """
    return annotation_rows(conn, predicate, params)


def _read_annotations_for_owners(
    conn: duckdb.DuckDBPyConnection,
    owner_subquery: str,
    params: list[Any],
) -> dict[str, Annotations]:
    """Bulk-load annotations for every owner returned by ``owner_subquery``.

    Issues a single SQL query that joins ``annotation`` with
    ``annotation_text``. ``owner_subquery`` is a SELECT fragment that
    yields owner_urn strings — typically scoped by the parent artefact
    (e.g. ``"SELECT concept_urn FROM concept WHERE scheme_urn = ?"``).

    Returns ``{owner_urn: Annotations}``. Owners with no annotations are
    absent from the result; callers should default to ``Annotations()``.
    """
    return _read_annotations_for_owners_predicate(
        conn,
        f"a.owner_urn IN ({owner_subquery})",
        params,
    )


class SpineRow(TypedDict):
    """The `sdmx.artefact` columns every maintainable artefact shares.

    A `TypedDict` rather than ``dict[str, Any]`` because the shape is fixed and
    the read sites are not: ten per-type readers pull 31 fields out of this
    between them, and as an untyped map a mistyped key was a ``KeyError`` at
    runtime and a wrong value type was nothing at all (#285).
    """

    artefact_type: str
    agency_id: str
    resource_id: str
    version: str
    legacy_is_final: bool | None
    valid_from: datetime | None
    valid_to: datetime | None
    uri: str | None
    is_external_reference: bool
    service_url: str | None
    structure_url: str | None
    is_partial: bool
    is_partial_language: bool
    #: The artefact's own links, and how many its items hold (#422) — read
    #: with the row, so an artefact with none pays no statement for them.
    links: tuple[Link, ...]
    item_links: int


def _read_spine(conn: duckdb.DuckDBPyConnection, urn: str) -> SpineRow | None:
    """Read artefact metadata from the spine table, with its own links and a count of its items'.

    Each part filters on the URN itself rather than correlating with the
    artefact row: as correlated subqueries the same statement took 1.3 ms for
    one row against 0.4 ms, a third of every `get`. The URN is bound once, as
    `$urn`, because DuckDB probes each bound value for pandas with a failed
    import (`project_duckdb_pandas_probe`).
    """
    row = first(
        conn.execute(
            parsed(
                conn,
                """
        WITH own AS (
            SELECT list({'rel': rel, 'url': url, 'urn': urn, 'type': link_type, 'hreflang': hreflang}
                        ORDER BY position) AS links
            FROM link WHERE owner_urn = $urn
        ),
        items AS (
            SELECT count(*) AS n FROM link WHERE owner_artefact_urn = $urn AND owner_urn <> $urn
        )
        SELECT artefact_type, agency_id, resource_id, version, legacy_is_final,
               valid_from, valid_to, uri, is_external_ref, service_url, structure_url, is_partial,
               is_partial_language, own.links, items.n
        FROM artefact, own, items WHERE artefact.urn = $urn
        """,
            ),
            {"urn": urn},
        )
    )
    if row is None:
        return None
    return {
        "artefact_type": row[0],
        "agency_id": row[1],
        "resource_id": row[2],
        "version": row[3],
        "legacy_is_final": row[4],
        # Re-tagged UTC: the column is naive by convention, and handing a
        # naive value back would make an aware input come home unequal (#321).
        "valid_from": from_storage(row[5]),
        "valid_to": from_storage(row[6]),
        "uri": row[7],
        "is_external_reference": row[8],
        "service_url": row[9],
        "structure_url": row[10],
        "is_partial": row[11],
        "is_partial_language": row[12],
        "links": tuple(Link(**link) for link in row[13] or ()),
        "item_links": row[14],
    }


def _links_of(conn: duckdb.DuckDBPyConnection, urn: str, meta: SpineRow) -> dict[str, tuple[Link, ...]]:
    """Every link an artefact and its items hold, by owner URN (#422).

    The artefact's own came with its spine row, with a count of its items';
    only when that count is not zero is there a statement to pay — which, for
    a construct nearly every registry leaves out, keeps a `get()` at the cost
    #426 pinned.
    """
    if meta["item_links"]:
        return _links_of_artefact(conn, urn)
    return {urn: meta["links"]} if meta["links"] else {}


class _SpineFields(TypedDict):
    """The maintainable fields every artefact has, ready to unpack into one.

    A `TypedDict` rather than a plain dict so the checkers still verify each
    value against the constructor parameter it lands on — unpacking
    `dict[str, object]` would type-check nothing and trade one silent failure
    for another.
    """

    id: str
    maintainer: Agency
    version: str
    valid_from: datetime | None
    valid_to: datetime | None
    uri: str | None
    is_external_reference: bool
    service_url: str | None
    structure_url: str | None
    is_partial_language: bool


class SpineKwargs(_SpineFields, closed=True):
    """`_SpineFields`, closed (PEP 728): only a closed TypedDict is known to hold no other key."""


class SpineKwargs21(_SpineFields, closed=True):
    """The spine of an artefact SDMX 2.1 gives an `isFinal`: plus `legacy_is_final`.

    `is_final` itself is derived from the version (#420) and never passed. The
    classes 3.0 introduced or promoted to maintainables — `Hierarchy`, the
    maps, `HierarchyAssociation`, the metadata provision agreement, the
    metadata provider scheme — have no 2.1 `isFinal` to keep, nor do the
    organisation schemes, where 2.1 fixes it to `false`.
    """

    legacy_is_final: bool | None


def _spine_kwargs(meta: SpineRow) -> SpineKwargs:
    """Every spine field, so no reader can construct an artefact missing one.

    Nineteen readers each wrote these seven kwargs by hand, and seven of them
    forgot the same two: `is_final` and `uri` were stored correctly and read
    back as their defaults, on Categorisation, CategoryScheme, ConceptScheme,
    Dataflow, HierarchyAssociation, ProvisionAgreement and RepresentationMap
    (#320). Patching the seven would have left the twentieth reader free to be
    written the same way, so the list is written once here instead.

    A field added to `MaintainableArtefact` now belongs in exactly two places
    — the DDL and this dict — and reaches every reader from the second.
    """
    return {
        "id": meta["resource_id"],
        "maintainer": Agency(id=meta["agency_id"]),
        "version": meta["version"],
        "valid_from": meta["valid_from"],
        "valid_to": meta["valid_to"],
        "uri": meta["uri"],
        "is_external_reference": meta["is_external_reference"],
        "service_url": meta["service_url"],
        "structure_url": meta["structure_url"],
        "is_partial_language": meta["is_partial_language"],
    }


def _spine_kwargs_21(meta: SpineRow) -> SpineKwargs21:
    """`_spine_kwargs` plus `legacy_is_final`. See `SpineKwargs21`."""
    # Spelled out rather than spread over `_spine_kwargs`: unpacking an open
    # TypedDict into a wider one is unsound, and pyrefly rejects it. Seven
    # literal keys in one place is what this whole helper is for, so paying
    # it twice is cheap.
    return {
        "id": meta["resource_id"],
        "maintainer": Agency(id=meta["agency_id"]),
        "version": meta["version"],
        "legacy_is_final": meta["legacy_is_final"],
        "valid_from": meta["valid_from"],
        "valid_to": meta["valid_to"],
        "uri": meta["uri"],
        "is_external_reference": meta["is_external_reference"],
        "service_url": meta["service_url"],
        "structure_url": meta["structure_url"],
        "is_partial_language": meta["is_partial_language"],
    }


# ── Per-type readers ────────────────────────────────────────────────────


def _read_codelist_eager(conn: duckdb.DuckDBPyConnection, urn: str, meta: SpineRow) -> Codelist | None:
    """Read a full Codelist with all codes materialized."""
    name, desc = _read_name_and_description(conn, urn)
    annotations_by_owner = _read_annotations_subtree(conn, urn)
    anns = annotations_by_owner.get(urn, Annotations.EMPTY)
    links = _links_of(conn, urn, meta)

    rows = conn.execute(
        f"""
        SELECT code_urn, code_id, parent_id, {_istring_sql("name")}, {_istring_sql("description")}, uri,
               geo_value, geo_cell
        FROM code WHERE codelist_urn = ?
        ORDER BY position
        """,
        [urn],
    ).fetchall()

    codes = [
        code_of_kind(
            id=code_id,
            parent_id=parent_id,
            name=_json_to_istring(name_json),
            description=_json_to_istring(desc_json),
            annotations=annotations_by_owner.get(code_urn, Annotations.EMPTY),
            uri=code_uri,
            links=links.get(code_urn, ()),
            value=geo_value,
            geo_cell=geo_cell,
        )
        for code_urn, code_id, parent_id, name_json, desc_json, code_uri, geo_value, geo_cell in rows
    ]

    return codelist_of_kind(
        *geo_kind(conn, urn),
        **_spine_kwargs_21(meta),
        is_partial=meta["is_partial"],
        name=name,
        description=desc,
        annotations=anns,
        links=links.get(urn, ()),
        codes=ItemList(codes),
        extensions=_codelist_extensions(conn, urn),
    )


def _read_value_list(conn: duckdb.DuckDBPyConnection, urn: str, meta: SpineRow) -> ValueList:
    """Read a ValueList with its values, in order (#422)."""
    name, desc = _read_name_and_description(conn, urn)
    annotations_by_owner = _read_annotations_subtree(conn, urn)
    rows = conn.execute(
        f"""
        SELECT item_id, {_istring_sql("name")}, {_istring_sql("description")}
        FROM value_item WHERE value_list_urn = ?
        ORDER BY position
        """,
        [urn],
    ).fetchall()
    # The owner key `writers.put_value_list` gives each value's annotations:
    # the list's URN spelt as an item class, then the value.
    parsed = SdmxUrn.parse(urn)
    owner = SdmxUrn.make(
        ValueList,
        package=parsed.package,
        artefact_class="ValueItem",
        agency=parsed.agency,
        id=parsed.id,
        version=parsed.version,
    )
    prefix = f"{owner}."
    return ValueList(
        **_spine_kwargs(meta),
        name=name,
        description=desc,
        annotations=annotations_by_owner.get(urn, Annotations.EMPTY),
        links=meta["links"],
        items=ItemList(
            [
                ValueItem(
                    id=item_id,
                    name=_json_to_istring(name_json),
                    description=_json_to_istring(desc_json),
                    annotations=annotations_by_owner.get(prefix + item_id, Annotations.EMPTY),
                )
                for item_id, name_json, desc_json in rows
            ]
        ),
    )


def _component_urn(dsd_urn: str, comp_type: str, comp_id: str) -> str:
    """Synthetic URN for a DSD component (mirrors writers._make_item_urn)."""
    parsed = SdmxUrn.parse(dsd_urn)
    return str(
        SdmxUrn.make(
            DataStructure,
            package=parsed.package,
            artefact_class=comp_type,
            agency=parsed.agency,
            id=parsed.id,
            version=parsed.version,
            item_id=comp_id,
        )
    )


#: The five components a DSD holds, as `_deserialize_component` names them.
type _ComponentKind = Literal["Dimension", "TimeDimension", "MeasureDimension", "Attribute", "Measure"]


def _deserialize_component(
    row: tuple[Any, ...], annotations: Annotations, links: tuple[Link, ...]
) -> tuple[_ComponentKind, Any]:
    """Deserialize a dsd_component row into (comp_type, component_object)."""
    (
        comp_id,
        comp_type,
        _position,
        concept_urn_str,
        repr_kind,
        codelist_urn,
        text_type,
        min_length,
        max_length,
        min_value,
        max_value,
        decimals,
        pattern,
        is_sequence,
        start_value,
        end_value,
        is_multilingual,
        interval,
        time_interval,
        start_time,
        end_time,
        enum_text_type,
        enum_min_length,
        enum_max_length,
        enum_min_value,
        enum_max_value,
        enum_decimals,
        enum_pattern,
        enum_is_sequence,
        enum_start_value,
        enum_end_value,
        enum_is_multilingual,
        enum_interval,
        enum_time_interval,
        enum_start_time,
        enum_end_time,
        usage_status,
        attachment_level,
        attachment_group,
        related_dimensions,
        measure_usage,
        concept_roles,
        uri,
        optional_dimensions,
        related_measures,
        min_occurs,
        max_occurs,
        sentinel_values,
        attachment_groups,
    ) = row
    concept_role = tuple(concept_roles) if concept_roles else ()

    concept_ref = Ref[Concept].unresolved(Concept.parse_urn(concept_urn_str)) if concept_urn_str else None
    representation = _build_representation(
        repr_kind,
        codelist_urn,
        text_type=text_type,
        min_length=min_length,
        max_length=max_length,
        min_value=min_value,
        max_value=max_value,
        decimals=decimals,
        pattern=pattern,
        is_sequence=is_sequence,
        start_value=start_value,
        end_value=end_value,
        is_multilingual=is_multilingual,
        interval=interval,
        time_interval=time_interval,
        start_time=start_time,
        end_time=end_time,
        sentinel_values=sentinel_values,
    )
    enumeration_format = _build_enumeration_format(
        text_type=enum_text_type,
        min_length=enum_min_length,
        max_length=enum_max_length,
        min_value=enum_min_value,
        max_value=enum_max_value,
        decimals=enum_decimals,
        pattern=enum_pattern,
        is_sequence=enum_is_sequence,
        start_value=enum_start_value,
        end_value=enum_end_value,
        is_multilingual=enum_is_multilingual,
        interval=enum_interval,
        time_interval=enum_time_interval,
        start_time=enum_start_time,
        end_time=enum_end_time,
    )

    match comp_type:
        case "TimeDimension":
            return comp_type, TimeDimension(
                id=comp_id,
                concept=concept_ref,
                representation=representation if isinstance(representation, Facet) else None,
                annotations=annotations,
                uri=uri,
                links=links,
            )
        case "MeasureDimension":
            # Enumerated by a ConceptScheme, not a Codelist. `build_representation`
            # returns the Codelist-shaped union, so re-point the ref at the class
            # this component actually takes; a Facet passes through untouched.
            md_rep: Ref[ConceptScheme] | Facet | None = (
                Ref[ConceptScheme].unresolved(ConceptScheme.parse_urn(str(representation.urn)))
                if isinstance(representation, Ref)
                else representation
            )
            return comp_type, MeasureDimension(
                id=comp_id,
                concept=concept_ref,
                representation=md_rep,
                annotations=annotations,
                concept_role=concept_role,
                uri=uri,
            )
        case "Attribute":
            attachment = _stored_attachment(attachment_level, f"Attribute.{comp_id}")
            return comp_type, Attribute(
                id=comp_id,
                concept=concept_ref,
                representation=representation,
                enumeration_format=enumeration_format,
                usage=usage_status,
                attachment=attachment,
                related_dimensions=list(related_dimensions) if related_dimensions else None,
                optional_dimensions=optional_dimensions or (),
                related_measures=related_measures,
                related_group=attachment_group,
                attachment_groups=attachment_groups or (),
                min_occurs=min_occurs,
                max_occurs=max_occurs,
                annotations=annotations,
                concept_role=concept_role,
                uri=uri,
                links=links,
            )
        case "Measure":
            return comp_type, Measure(
                id=comp_id,
                concept=concept_ref,
                representation=representation,
                enumeration_format=enumeration_format,
                min_occurs=min_occurs,
                max_occurs=max_occurs,
                usage=measure_usage,
                annotations=annotations,
                concept_role=concept_role,
                uri=uri,
                links=links,
            )
        case _:
            return "Dimension", Dimension(
                id=comp_id,
                concept=concept_ref,
                representation=representation,
                enumeration_format=enumeration_format,
                annotations=annotations,
                concept_role=concept_role,
                uri=uri,
                links=links,
            )


def _read_groups(
    conn: duckdb.DuckDBPyConnection, urn: str, annotations_by_owner: dict[str, Annotations]
) -> list[Group]:
    """Read DSD groups and their dimension memberships."""
    group_rows = conn.execute(
        "SELECT group_id, uri, attachment_constraint_urn FROM dsd_group WHERE dsd_urn = ?",
        [urn],
    ).fetchall()
    groups: list[Group] = []
    for group_id, group_uri, constraint_urn in group_rows:
        dim_rows = conn.execute(
            "SELECT dimension_id FROM group_dimension WHERE dsd_urn = ? AND group_id = ?",
            [urn, group_id],
        ).fetchall()
        groups.append(
            Group(
                id=group_id,
                dimension_ids=[r[0] for r in dim_rows],
                attachment_constraint=(
                    Ref[object].unresolved(SdmxUrn.parse(constraint_urn)) if constraint_urn is not None else None
                ),
                annotations=annotations_by_owner.get(
                    _component_urn(urn, Group.sdmx_class, group_id), Annotations.EMPTY
                ),
                uri=group_uri,
            )
        )
    return groups


def _read_data_structure(conn: duckdb.DuckDBPyConnection, urn: str, meta: SpineRow) -> DataStructure | None:
    """Read a DataStructure from dsd_component rows."""
    name, desc = _read_name_and_description(conn, urn)
    annotations_by_owner = _read_annotations_subtree(conn, urn)
    anns = annotations_by_owner.get(urn, Annotations.EMPTY)
    links = _links_of(conn, urn, meta)

    comp_rows = conn.execute(
        parsed(
            conn,
            """
        SELECT component_id, component_type, position, concept_urn,
               repr_kind, codelist_urn, text_type, min_length, max_length,
               min_value, max_value, decimals, pattern, is_sequence,
               start_value, end_value, is_multilingual, interval, time_interval, start_time, end_time,
               enum_text_type, enum_min_length, enum_max_length,
               enum_min_value, enum_max_value, enum_decimals, enum_pattern,
               enum_is_sequence, enum_start_value, enum_end_value,
               enum_is_multilingual,
               enum_interval,
               enum_time_interval,
               enum_start_time,
               enum_end_time,
               usage_status, attachment_level, attachment_group,
               related_dimensions,
               measure_usage, concept_roles, uri,
               optional_dimensions, related_measures, min_occurs, max_occurs, sentinel_values,
               attachment_groups
        FROM dsd_component
        WHERE dsd_urn = ?
        ORDER BY position
        """,
        ),
        [urn],
    ).fetchall()

    dimensions: list[Dimension] = []
    time_dim: TimeDimension | None = None
    measure_dim: MeasureDimension | None = None
    attributes: list[Attribute] = []
    measures: list[Measure] = []

    for row in comp_rows:
        comp_id_raw, comp_type_raw = row[0], row[1]
        # "Dimension" is the default fallback in _deserialize_component, so any
        # unknown row falls back to that path — match it here for the URN too.
        urn_class = (
            comp_type_raw
            if comp_type_raw
            in {
                "TimeDimension",
                "MeasureDimension",
                "Attribute",
                "Measure",
            }
            else "Dimension"
        )
        comp_anns = annotations_by_owner.get(
            _component_urn(urn, urn_class, comp_id_raw),
            Annotations.EMPTY,
        )
        comp_type, comp = _deserialize_component(
            row, comp_anns, links.get(_component_urn(urn, urn_class, comp_id_raw), ())
        )
        match comp_type:
            case "Dimension":
                dimensions.append(comp)
            case "TimeDimension":
                time_dim = comp
            case "MeasureDimension":
                measure_dim = comp
            case "Attribute":
                attributes.append(comp)
            case "Measure":
                measures.append(comp)

    groups = _read_groups(conn, urn, annotations_by_owner)
    # One statement for both SDMX 3.0 additions (#422): a DSD without them,
    # which is every 2.1 one, should not pay a round trip each.
    # And what the DSD holds of itself (#422), in the same round trip.
    msd_urn, usage_rows, dim_list_uri, attr_list_uri, measure_list_uri, evolving = first(
        conn.execute(
            parsed(
                conn,
                """
        SELECT
            (SELECT target_urn FROM artefact_ref WHERE source_urn = $urn AND role = 'metadata'),
            (SELECT list(
                 (attribute_id, attachment_level, attachment_group, related_dimensions, optional_dimensions, uri)
                 ORDER BY position)
             FROM dsd_metadata_attribute WHERE dsd_urn = $urn),
            (SELECT dimension_descriptor_uri FROM data_structure WHERE urn = $urn),
            (SELECT attribute_descriptor_uri FROM data_structure WHERE urn = $urn),
            (SELECT measure_descriptor_uri FROM data_structure WHERE urn = $urn),
            (SELECT evolving_structure FROM data_structure WHERE urn = $urn)
        """,
            ),
            {"urn": urn},
        )
    ) or (None, None, None, None, None, None)
    usages = [
        MetadataAttributeUsage(
            id=attribute_id,
            attachment=_stored_attachment(level, f"MetadataAttributeUsage.{attribute_id}"),
            related_dimensions=list(related) if related else None,
            optional_dimensions=optional or (),
            related_group=group,
            uri=usage_uri,
            links=links.get(_component_urn(urn, "MetadataAttributeUsage", attribute_id), ()),
            annotations=annotations_by_owner.get(
                _component_urn(urn, "MetadataAttributeUsage", attribute_id), Annotations.EMPTY
            ),
        )
        # An unnamed struct comes back a tuple.
        for attribute_id, level, group, related, optional, usage_uri in usage_rows or []
    ]

    return DataStructure(
        **_spine_kwargs_21(meta),
        name=name,
        description=desc,
        annotations=anns,
        dimensions=dimensions,
        time_dimension=time_dim,
        measure_dimension=measure_dim,
        attributes=attributes,
        measures=measures,
        groups=groups or None,
        dimension_descriptor_uri=dim_list_uri,
        attribute_descriptor_uri=attr_list_uri,
        measure_descriptor_uri=measure_list_uri,
        dimension_descriptor_links=links.get(_component_urn(urn, "DimensionDescriptor", "DimensionDescriptor"), ()),
        attribute_descriptor_links=links.get(_component_urn(urn, "AttributeDescriptor", "AttributeDescriptor"), ()),
        measure_descriptor_links=links.get(_component_urn(urn, "MeasureDescriptor", "MeasureDescriptor"), ()),
        links=links.get(urn, ()),
        evolving_structure=bool(evolving),
        # Keyed by each list's own URN, as `writers.put_data_structure` keys them.
        dimension_descriptor_annotations=annotations_by_owner.get(
            _component_urn(urn, "DimensionDescriptor", "DimensionDescriptor"), Annotations.EMPTY
        ),
        attribute_descriptor_annotations=annotations_by_owner.get(
            _component_urn(urn, "AttributeDescriptor", "AttributeDescriptor"), Annotations.EMPTY
        ),
        measure_descriptor_annotations=annotations_by_owner.get(
            _component_urn(urn, "MeasureDescriptor", "MeasureDescriptor"), Annotations.EMPTY
        ),
        metadata=Ref[MetadataStructure].unresolved(MetadataStructure.parse_urn(msd_urn)) if msd_urn else None,
        metadata_attributes=usages,
    )


def _read_dataflow(conn: duckdb.DuckDBPyConnection, urn: str, meta: SpineRow) -> Dataflow | None:
    """Read a Dataflow from spine + dataflow extension."""
    name, desc = _read_name_and_description(conn, urn)
    anns = _read_annotations(conn, urn)
    links = _links_of(conn, urn, meta)

    row = first(conn.execute("SELECT dsd_urn, dimension_constraint FROM dataflow WHERE urn = ?", [urn]))
    structure_ref = None
    if row and row[0]:
        structure_ref = Ref[DataStructure].unresolved(DataStructure.parse_urn(row[0]))
    # NULL for a row written before the column existed, and for a flow with no
    # constraint — both mean "the whole DSD", which is the model's default.
    constraint: list[str] = (row[1] if row else None) or []

    return Dataflow(
        **_spine_kwargs_21(meta),
        name=name,
        description=desc,
        annotations=anns,
        links=links.get(urn, ()),
        structure=structure_ref,
        dimension_constraint=tuple(constraint),
    )


def _read_concept_scheme(conn: duckdb.DuckDBPyConnection, urn: str, meta: SpineRow) -> ConceptScheme | None:
    """Read a ConceptScheme from spine + concept rows."""
    name, desc = _read_name_and_description(conn, urn)
    annotations_by_owner = _read_annotations_subtree(conn, urn)
    anns = annotations_by_owner.get(urn, Annotations.EMPTY)
    links = _links_of(conn, urn, meta)

    rows = conn.execute(
        f"""
        SELECT concept_urn, concept_id, name, description, parent_id,
               repr_kind, codelist_urn, text_type,
               min_length, max_length,
               min_value, max_value, decimals, pattern,
               is_sequence, start_value, end_value, is_multilingual,
               interval, time_interval, start_time, end_time, uri, sentinel_values,
               {", ".join(ENUMERATION_FORMAT_COLUMN_NAMES)},
               iso_agency, iso_concept_scheme_id, iso_concept_id, min_occurs, max_occurs
        FROM concept WHERE scheme_urn = ?
        ORDER BY position
        """,
        [urn],
    ).fetchall()
    # The core representation's columns, then its enumeration format's, then
    # the ISO reference's three (#422).
    head = 24
    tail = head + len(ENUMERATION_FORMAT_COLUMN_NAMES)

    concepts: list[Concept] = []
    for row in rows:
        (
            c_urn,
            c_id,
            c_name,
            c_desc,
            c_parent_id,
            repr_kind,
            repr_cl_urn,
            text_type,
            min_length,
            max_length,
            min_value,
            max_value,
            decimals,
            pattern,
            is_sequence,
            start_value,
            end_value,
            is_multilingual,
            interval,
            time_interval,
            start_time,
            end_time,
            c_uri,
            sentinel_values,
        ) = row[:head]
        enumeration_format = _build_enumeration_format(**dict(zip(FACET_COLUMN_NAMES[2:], row[head:tail], strict=True)))
        iso_agency, iso_scheme_id, iso_concept_id, min_occurs, max_occurs = row[tail:]

        core_repr = _build_representation(
            repr_kind,
            repr_cl_urn,
            text_type=text_type,
            min_length=min_length,
            max_length=max_length,
            min_value=min_value,
            max_value=max_value,
            decimals=decimals,
            pattern=pattern,
            is_sequence=is_sequence,
            start_value=start_value,
            end_value=end_value,
            is_multilingual=is_multilingual,
            interval=interval,
            time_interval=time_interval,
            start_time=start_time,
            end_time=end_time,
            sentinel_values=sentinel_values,
        )

        c_anns = annotations_by_owner.get(c_urn, Annotations.EMPTY)
        concepts.append(
            Concept(
                id=c_id,
                name=_map_to_istring(c_name),
                description=_map_to_istring(c_desc),
                parent_id=c_parent_id,
                core_representation=core_repr,
                enumeration_format=enumeration_format,
                min_occurs=1 if min_occurs is None else min_occurs,
                max_occurs=max_occurs,
                iso_concept_reference=(
                    None
                    if iso_agency is None
                    else ISOConceptReference(
                        agency=iso_agency, concept_scheme_id=iso_scheme_id, concept_id=iso_concept_id
                    )
                ),
                annotations=c_anns,
                uri=c_uri,
                links=links.get(c_urn, ()),
            )
        )

    return ConceptScheme(
        **_spine_kwargs_21(meta),
        is_partial=meta["is_partial"],
        name=name,
        description=desc,
        annotations=anns,
        links=links.get(urn, ()),
        concepts=concepts,
    )


def _assemble[N: SupportsId](
    children_map: Mapping[str | None, list[str]], build: Callable[[str, ItemList[N]], N]
) -> ItemList[N]:
    """The roots of a tree whose nodes are built leaves first, each once, with its children (#492).

    A frozen node cannot be given its children after it is built, so each is
    built after them: an iterative post-order walk from the roots, which the
    `None` key of *children_map* lists. A node whose parent is not a node is
    left out, as the readers always left it out.

    Args:
        children_map: Each parent path's child paths, in order; `None` for the roots.
        build: Builds the node at a path, given its children.

    Returns:
        The roots, in order.
    """
    roots = children_map.get(None, [])
    built: dict[str, N] = {}
    stack: list[tuple[str, bool]] = [(path, False) for path in reversed(roots)]
    while stack:
        path, ready = stack.pop()
        kids = children_map.get(path, [])
        if ready:
            built[path] = build(path, ItemList([built[kid] for kid in kids]))
            continue
        stack.append((path, True))
        stack.extend((kid, False) for kid in reversed(kids))
    return ItemList([built[path] for path in roots])


def _read_hierarchy(conn: duckdb.DuckDBPyConnection, urn: str, meta: SpineRow) -> Hierarchy | None:
    """Read a Hierarchy from spine + hierarchy_node rows."""
    name, desc = _read_name_and_description(conn, urn)
    annotations_by_owner = _read_annotations_subtree(conn, urn)
    anns = annotations_by_owner.get(urn, Annotations.EMPTY)
    links = _links_of(conn, urn, meta)

    # SDMX 3.1: read Level definitions
    levels = list(_read_levels(conn, urn))
    levels_by_id: dict[str, Level] = {lv.id: lv for lv in levels}

    rows = conn.execute(
        """
        SELECT node_urn, node_id, path, parent_path, code_urn, level_id,
               valid_from, valid_to, uri, version
        FROM hierarchy_node WHERE hierarchy_urn = ?
        ORDER BY depth DESC, position
        """,
        [urn],
    ).fetchall()

    # Deepest first, as `LazyHierarchy.materialize` reads them: a frozen node
    # cannot be given its children later (#492), so each is built after them.
    # Keyed by the parent's *path* — a node id may repeat in another branch
    # (#439) — and siblings stay in position order.
    children_by_parent: dict[str | None, list[HierarchicalCode]] = {}

    for row in rows:
        node_urn, node_id, path, parent_path, code_urn_str, level_id, vf, vt, node_uri, n_version = row
        code_ref = None
        if code_urn_str:
            code_ref = Ref[Code].unresolved(Code.parse_urn(code_urn_str))

        level: Level | None = None
        if level_id:
            # An undeclared level is kept, as `LazyHierarchy.materialize` keeps
            # it, not dropped: the two paths disagreed, and dropping it hid
            # from the check below what it refuses (#455, #492).
            level = levels_by_id.get(level_id)
            if level is None:
                level = Level(id=level_id)

        node_anns = annotations_by_owner.get(node_urn, Annotations.EMPTY)
        node = HierarchicalCode(
            id=node_id,
            code=code_ref,
            children=children_by_parent.pop(path, []),
            annotations=node_anns,
            links=links.get(node_urn, ()),
            valid_from=from_storage(vf),
            valid_to=from_storage(vt),
            level=level,
            uri=node_uri,
            version=n_version,
        )
        children_by_parent.setdefault(parent_path, []).append(node)

    tree = ItemList(children_by_parent.pop(None, []))

    # ``has_formal_levels`` lives in its own table, and the eager path used to
    # skip it entirely — so `get_artefact(..., eager=True)` silently returned
    # False for a hierarchy stored as True, while the lazy path (which does
    # read it) returned the truth. Found in the #214 audit by activating a
    # KNOWN_GAPS entry that had been dead.
    formal_row = first(
        conn.execute(
            "SELECT has_formal_levels FROM hierarchy_meta WHERE urn = ?",
            [urn],
        )
    )

    return Hierarchy(
        **_spine_kwargs(meta),
        name=name,
        description=desc,
        annotations=anns,
        links=links.get(urn, ()),
        levels=levels,
        tree=tree,
        has_formal_levels=bool(formal_row[0]) if formal_row is not None else False,
    )


def _reference_period(row: object) -> ReferencePeriod | None:
    """The constraint's reported reference period, or `None`.

    All-or-nothing: the model requires both a start and an end, so a row with
    one column set and not the other cannot be reconstructed and reads as
    absent rather than being defaulted into a period nobody reported.
    """
    if not isinstance(row, tuple):
        return None
    start = from_storage(row[1])
    end = from_storage(row[2])
    if start is None or end is None:
        return None
    return ReferencePeriod(start_period=start, end_period=end)


def _release_calendar(row: object) -> ReleaseCalendar | None:
    """The constraint's reported release calendar, or `None`.

    Any one of the three columns being set means a calendar was reported —
    unlike a reference period, the parts are independently optional.
    """
    if not isinstance(row, tuple) or all(value is None for value in row[3:6]):
        return None
    return ReleaseCalendar(periodicity=row[3], offset=row[4], tolerance=row[5])


def _read_constraint_attachment(conn: duckdb.DuckDBPyConnection, urn: str) -> ConstraintAttachment:
    """A constraint's attachment, every alternative and in order (#422)."""
    row = first(
        conn.execute(
            "SELECT attachment_data_provider_urn, simple_data_sources FROM data_constraint WHERE urn = ?", [urn]
        )
    )
    provider_urn, sources = row if row is not None else (None, None)

    def attached(sql: str) -> tuple[Ref[object], ...]:
        rows = conn.execute(sql, [urn]).fetchall()
        return tuple(Ref[object].unresolved(SdmxUrn.parse(target)) for (target,) in rows)

    data_sets = conn.execute(
        """
        SELECT data_provider_urn, data_set_id FROM data_constraint_data_set
        WHERE constraint_urn = ? ORDER BY ordinal
        """,
        [urn],
    ).fetchall()
    queryable = conn.execute(
        """
        SELECT data_url, is_rest_datasource, is_web_service_datasource, wsdl_url, wadl_url
        FROM data_constraint_queryable_source WHERE constraint_urn = ? ORDER BY ordinal
        """,
        [urn],
    ).fetchall()
    return ConstraintAttachment(
        data_provider=Ref[DataProvider].unresolved(DataProvider.parse_urn(provider_urn)) if provider_urn else None,
        data_sets=tuple(
            DataSetReference(data_provider=Ref[DataProvider].unresolved(DataProvider.parse_urn(p)), id=i)
            for p, i in data_sets
        ),
        simple_data_sources=tuple(sources or ()),
        data_structures=attached("SELECT dsd_urn FROM data_constraint_dsd WHERE constraint_urn = ? ORDER BY ordinal"),
        dataflows=attached(
            "SELECT dataflow_urn FROM data_constraint_dataflow WHERE constraint_urn = ? ORDER BY ordinal"
        ),
        provision_agreements=attached(
            "SELECT provision_urn FROM data_constraint_provision WHERE constraint_urn = ? ORDER BY ordinal"
        ),
        queryable_data_sources=tuple(
            QueryableDataSource(
                data_url=data_url,
                is_rest_datasource=is_rest,
                is_web_service_datasource=is_ws,
                wsdl_url=wsdl,
                wadl_url=wadl,
            )
            for data_url, is_rest, is_ws, wsdl, wadl in queryable
        ),
    )


def _read_data_constraint(conn: duckdb.DuckDBPyConnection, urn: str, meta: SpineRow) -> DataConstraint | None:
    """Read a DataConstraint from relational tables."""
    name, desc = _read_name_and_description(conn, urn)
    # Its regions' and keys' too, keyed by `part_owner` (#422).
    by_owner = _read_annotations_subtree(conn, urn)
    links = _links_of(conn, urn, meta)
    anns = by_owner.get(urn, Annotations.EMPTY)

    cc_row = first(
        conn.execute(
            """
        SELECT constraint_type, ref_period_start, ref_period_end,
               release_periodicity, release_offset, release_tolerance
        FROM data_constraint WHERE urn = ?
        """,
            [urn],
        )
    )
    constraint_type = cc_row[0] if cc_row else "Allowed"
    reference_period = _reference_period(cc_row)
    release_calendar = _release_calendar(cc_row)

    attachment = _read_constraint_attachment(conn, urn)

    return DataConstraint(
        **_spine_kwargs_21(meta),
        name=name,
        description=desc,
        constraint_type=constraint_type,
        attachment=attachment,
        regions=_read_regions(conn, urn, by_owner),
        data_key_sets=_read_data_key_sets(conn, urn, by_owner),
        reference_period=reference_period,
        release_calendar=release_calendar,
        annotations=anns,
        links=links.get(urn, ()),
    )


def _read_value_sets(
    conn: duckdb.DuckDBPyConnection,
    urn: str,
    owner_column: str,
    owner_idx: int,
    selection_table: str,
    value_table: str,
) -> tuple[list[KeyValue], list[ComponentValueSet]]:
    """A cube region's or a data key's selections, with their values (#433, #422).

    The table and column names are literals at each caller, mirroring
    `writers._REGION_SETS` and `writers._KEY_SETS` — never input.
    """
    kv_rows = conn.execute(
        f"""
        SELECT component_id, value, cascade, lang, valid_from, valid_to FROM {value_table}
        WHERE constraint_urn = ? AND {owner_column} = ?
        """,
        [urn, owner_idx],
    ).fetchall()
    values: dict[str, set[RegionValue]] = {}
    for comp_id, val, cascade, lang, valid_from, valid_to in kv_rows:
        values.setdefault(comp_id, set()).add(
            RegionValue(
                value=val,
                cascade=CascadeSelection(cascade) if cascade is not None else None,
                lang=lang,
                valid_from=valid_from,
                valid_to=valid_to,
            )
        )
    # The selections themselves come from `{selection_table}` (#433): a
    # key with a time range, or with no values, has no value row.
    selection_rows = conn.execute(
        f"""
        SELECT component_id, is_key, start_period, start_inclusive, end_period, end_inclusive, is_included,
               remove_prefix, valid_from, valid_to, time_valid_from, time_valid_to
        FROM {selection_table}
        WHERE constraint_urn = ? AND {owner_column} = ?
        ORDER BY position
        """,
        [urn, owner_idx],
    ).fetchall()
    keys: list[KeyValue] = []
    components: list[ComponentValueSet] = []
    for (
        comp_id,
        is_key,
        start,
        start_inclusive,
        end,
        end_inclusive,
        included,
        remove_prefix,
        valid_from,
        valid_to,
        time_valid_from,
        time_valid_to,
    ) in selection_rows:
        time_range = _time_range(
            start=start,
            start_inclusive=start_inclusive,
            end=end,
            end_inclusive=end_inclusive,
            valid_from=time_valid_from,
            valid_to=time_valid_to,
        )
        held = frozenset(values.get(comp_id, ()))
        if is_key:
            keys.append(
                KeyValue(
                    dimension_id=comp_id,
                    values=held,
                    time_range=time_range,
                    include=included,
                    remove_prefix=remove_prefix,
                    valid_from=valid_from,
                    valid_to=valid_to,
                )
            )
        else:
            components.append(
                ComponentValueSet(
                    component_id=comp_id,
                    values=held,
                    time_range=time_range,
                    include=included,
                    remove_prefix=remove_prefix,
                )
            )
    return keys, components


def _read_regions(
    conn: duckdb.DuckDBPyConnection, urn: str, by_owner: dict[str, Annotations]
) -> tuple[CubeRegion, ...]:
    """A constraint's cube regions, with their selections (#433)."""
    region_rows = conn.execute(
        "SELECT region_idx, is_included FROM cube_region WHERE constraint_urn = ? ORDER BY region_idx",
        [urn],
    ).fetchall()
    regions: list[CubeRegion] = []
    for region_idx, is_included in region_rows:
        keys, components = _read_value_sets(conn, urn, "region_idx", region_idx, "region_selection", "region_key_value")
        regions.append(
            CubeRegion(
                include=is_included,
                keys=tuple(keys),
                components=tuple(components),
                annotations=by_owner.get(part_owner(urn, "regions", region_idx), Annotations.EMPTY),
            )
        )

    return tuple(regions)


def _read_data_key_sets(
    conn: duckdb.DuckDBPyConnection, urn: str, by_owner: dict[str, Annotations]
) -> tuple[DataKeySet, ...]:
    """A constraint's key sets, each with its keys (#437, #479)."""
    included = conn.execute(
        "SELECT is_included FROM data_key_set WHERE constraint_urn = ? ORDER BY set_idx", [urn]
    ).fetchall()
    in_set: list[list[DataKey]] = [[] for _ in included]
    for set_idx, key in _read_data_keys(conn, urn, by_owner):
        in_set[set_idx].append(key)
    return tuple(
        DataKeySet(is_included=bool(is_included), keys=tuple(keys))
        for (is_included,), keys in zip(included, in_set, strict=True)
    )


def _read_data_keys(
    conn: duckdb.DuckDBPyConnection, urn: str, by_owner: dict[str, Annotations]
) -> list[tuple[int, DataKey]]:
    """A constraint's explicit observation keys, each with the set it is in (#437)."""
    # One `data_key` row per key, so a key with no values still exists; its
    # values come back in dimension order.
    keys = conn.execute(
        "SELECT set_idx, valid_from, valid_to FROM data_key WHERE constraint_urn = ? ORDER BY key_idx", [urn]
    ).fetchall()
    pairs: list[list[tuple[str, str]]] = [[] for _ in keys]
    for key_idx, component_id, value in conn.execute(
        """
        SELECT key_idx, component_id, value FROM data_key_value
        WHERE constraint_urn = ?
        ORDER BY key_idx, position
        """,
        [urn],
    ).fetchall():
        idx: int = key_idx
        pairs[idx].append((component_id, value))
    in_set: dict[int, int] = {}  # each key's place in its set, which owns its annotations
    out: list[tuple[int, DataKey]] = []
    for i, (key, (set_idx, valid_from, valid_to)) in enumerate(zip(pairs, keys, strict=True)):
        place = in_set[set_idx] = in_set.get(set_idx, -1) + 1
        owner = part_owner(urn, f"data_key_sets[{set_idx}].keys", place)
        out.append(
            (
                set_idx,
                DataKey(
                    values=key,
                    annotations=by_owner.get(owner, Annotations.EMPTY),
                    valid_from=valid_from,
                    valid_to=valid_to,
                    components=tuple(
                        _read_value_sets(conn, urn, "key_idx", i, "data_key_component", "data_key_component_value")[1]
                    ),
                ),
            )
        )
    return out


def _time_range(
    *,
    start: str | None,
    start_inclusive: bool | None,
    end: str | None,
    end_inclusive: bool | None,
    valid_from: str | None,
    valid_to: str | None,
) -> TimeRange | None:
    if start is None and end is None and valid_from is None and valid_to is None:
        return None
    return TimeRange(
        start=TimeBound(period=start, inclusive=bool(start_inclusive)) if start is not None else None,
        end=TimeBound(period=end, inclusive=bool(end_inclusive)) if end is not None else None,
        valid_from=valid_from,
        valid_to=valid_to,
    )


def _read_category_scheme(conn: duckdb.DuckDBPyConnection, urn: str, meta: SpineRow) -> CategoryScheme | None:
    """Read a CategoryScheme from spine + category rows."""
    name, desc = _read_name_and_description(conn, urn)
    annotations_by_owner = _read_annotations_subtree(conn, urn)
    anns = annotations_by_owner.get(urn, Annotations.EMPTY)
    links = _links_of(conn, urn, meta)

    rows = conn.execute(
        """
        SELECT category_urn, category_id, path, parent_path, name, description, uri
        FROM category WHERE scheme_urn = ?
        ORDER BY position
        """,
        [urn],
    ).fetchall()

    # Keyed by path: a category id may repeat in another branch (#439), and
    # built after its children (#492).
    pending: dict[str, partial[Category]] = {}
    children_map: dict[str | None, list[str]] = {}

    for row in rows:
        cat_urn, cat_id, path, parent_path, c_name, c_desc, cat_uri = row
        cat_anns = annotations_by_owner.get(cat_urn, Annotations.EMPTY)
        pending[path] = partial(
            Category,
            id=cat_id,
            name=_map_to_istring(c_name),
            description=_map_to_istring(c_desc),
            annotations=cat_anns,
            links=links.get(cat_urn, ()),
            uri=cat_uri,
        )
        children_map.setdefault(parent_path, []).append(path)

    root_categories = _assemble(children_map, lambda path, kids: pending[path](categories=kids))

    return CategoryScheme(
        **_spine_kwargs_21(meta),
        is_partial=meta["is_partial"],
        name=name,
        description=desc,
        categories=root_categories,
        annotations=anns,
        links=links.get(urn, ()),
    )


def _read_categorisation(conn: duckdb.DuckDBPyConnection, urn: str, meta: SpineRow) -> Categorisation | None:
    """Read a Categorisation from spine + extension."""
    name, desc = _read_name_and_description(conn, urn)
    anns = _read_annotations(conn, urn)
    links = _links_of(conn, urn, meta)

    row = first(conn.execute("SELECT source_urn, target_urn FROM categorisation WHERE urn = ?", [urn]))
    source_ref = Ref[object].unresolved(SdmxUrn.parse(row[0])) if row and row[0] else None
    target_ref = Ref[object].unresolved(SdmxUrn.parse(row[1])) if row and row[1] else None

    return Categorisation(
        **_spine_kwargs_21(meta),
        name=name,
        description=desc,
        annotations=anns,
        links=links.get(urn, ()),
        source=source_ref,
        target=target_ref,
    )


def _container_refs(
    conn: duckdb.DuckDBPyConnection, urn: str, fields: tuple[str, ...]
) -> dict[str, tuple[Ref[object], ...]]:
    """A 2.1 grouping container's members, by the field they belong to (#395).

    The mirror of `writers.put_container`. Neither container has a table:
    each is a maintainable plus lists of refs, which `artefact` and
    `artefact_ref` already hold, and the `role` column is what keeps
    `StructureSet`'s two lists apart.

    Ordered by `ordinal` so a round trip returns members in the order they
    were written. 2.1 gives that order no meaning, but the model stores a
    tuple, and a tuple that reshuffles between reads is a diff nobody asked
    for.
    """
    rows = conn.execute(
        "SELECT target_urn, role FROM artefact_ref WHERE source_urn = ? ORDER BY role, ordinal",
        [urn],
    ).fetchall()
    by_role: dict[str, list[Ref[object]]] = {field: [] for field in fields}
    for target, role in rows:
        members = by_role.get(str(role))
        if members is not None:
            members.append(Ref[object].unresolved(SdmxUrn.parse(str(target))))
    return {field: tuple(members) for field, members in by_role.items()}


def _read_hierarchical_codelist(
    conn: duckdb.DuckDBPyConnection, urn: str, meta: SpineRow
) -> HierarchicalCodelist | None:
    """Read a HierarchicalCodelist from spine + `artefact_ref` — see `_container_refs`."""
    refs = _container_refs(conn, urn, ("hierarchies", "included_codelists"))
    name, desc = _read_name_and_description(conn, urn)
    return HierarchicalCodelist(
        **_spine_kwargs_21(meta),
        name=name,
        description=desc,
        annotations=_read_annotations(conn, urn),
        hierarchies=refs["hierarchies"],
        # Written as a container ref since #434; lost on a store round-trip before.
        included_codelists=refs["included_codelists"],
    )


def _read_structure_set(conn: duckdb.DuckDBPyConnection, urn: str, meta: SpineRow) -> StructureSet | None:
    """Read a StructureSet from spine + `artefact_ref` — see `_container_refs`.

    Constructed explicitly rather than through a shared generic: `type[T]`
    hides the constructor from the checkers, and two four-line calls they can
    verify beat one they cannot.
    """
    refs = _container_refs(conn, urn, ("maps", "related_structures"))
    name, desc = _read_name_and_description(conn, urn)
    return StructureSet(
        **_spine_kwargs_21(meta),
        name=name,
        description=desc,
        annotations=_read_annotations(conn, urn),
        maps=refs["maps"],
        related_structures=refs["related_structures"],
    )


def _read_hierarchy_association(
    conn: duckdb.DuckDBPyConnection, urn: str, meta: SpineRow
) -> HierarchyAssociation | None:
    """Read a HierarchyAssociation from spine + extension."""
    name, desc = _read_name_and_description(conn, urn)
    anns = _read_annotations(conn, urn)
    links = _links_of(conn, urn, meta)

    row = first(
        conn.execute(
            "SELECT linked_hierarchy_urn, linked_object_urn, context_object_urn "
            "FROM hierarchy_association WHERE urn = ?",
            [urn],
        )
    )
    if row is None:
        return None
    linked_h, linked_o, context_o = row
    linked_h_ref: Ref[Hierarchy] | None = Ref[Hierarchy].unresolved(Hierarchy.parse_urn(linked_h)) if linked_h else None
    linked_o_ref = Ref[object].unresolved(SdmxUrn.parse(linked_o)) if linked_o else None
    context_o_ref = Ref[object].unresolved(SdmxUrn.parse(context_o)) if context_o else None

    return HierarchyAssociation(
        **_spine_kwargs(meta),
        name=name,
        description=desc,
        annotations=anns,
        links=links.get(urn, ()),
        linked_hierarchy=linked_h_ref,
        linked_object=linked_o_ref,
        context_object=context_o_ref,
    )


def _read_provision_agreement(conn: duckdb.DuckDBPyConnection, urn: str, meta: SpineRow) -> ProvisionAgreement | None:
    """Read a ProvisionAgreement from spine + extension."""
    name, desc = _read_name_and_description(conn, urn)
    anns = _read_annotations(conn, urn)
    links = _links_of(conn, urn, meta)

    row = first(conn.execute("SELECT dataflow_urn, data_provider_urn FROM provision_agreement WHERE urn = ?", [urn]))
    df_ref = Ref[Dataflow].unresolved(Dataflow.parse_urn(row[0])) if row and row[0] else None
    dp_ref = Ref[object].unresolved(SdmxUrn.parse(row[1])) if row and row[1] else None

    return ProvisionAgreement(
        **_spine_kwargs_21(meta),
        name=name,
        description=desc,
        annotations=anns,
        links=links.get(urn, ()),
        dataflow=df_ref,
        data_provider=dp_ref,
    )


def _contacts_by_owner(conn: duckdb.DuckDBPyConnection, owner_query: str, urn: str) -> dict[str, list[Contact]]:
    """Contacts grouped by owner urn, for whichever organisation asked.

    One bulk load rather than one per organisation, and shared between
    agencies and data providers because `Organisation` is one IM concept with
    one contact shape — which is exactly what `DataProvider.contacts` being
    dropped on write had obscured (#309).

    `owner_query` selects the owner urns in scope. It is the only part that
    differs between callers and is a module-level constant at each one, never
    anything a caller supplies.
    """
    grouped: dict[str, list[Contact]] = {}
    rows = conn.execute(
        f"""
        SELECT owner_urn, contact_name, department, role, phones, faxes, x400s, uris, emails, contact_id
        FROM organisation_contact
        WHERE owner_urn IN ({owner_query})
        ORDER BY owner_urn, ordinal
        """,
        [urn],
    ).fetchall()
    for row in rows:
        owner, c_name, c_dept, c_role, c_phones, c_faxes, c_x400s, c_uris, c_emails, c_id = row
        grouped.setdefault(owner, []).append(
            Contact(
                name=_map_to_istring(c_name),
                department=_map_to_istring(c_dept),
                role=_map_to_istring(c_role),
                phones=tuple(c_phones or []),
                faxes=tuple(c_faxes or []),
                x400s=tuple(c_x400s or []),
                uris=tuple(c_uris or []),
                emails=tuple(c_emails or []),
                id=c_id,
            )
        )
    return grouped


def _read_agency_scheme(conn: duckdb.DuckDBPyConnection, urn: str, meta: SpineRow) -> AgencyScheme | None:
    """Read an AgencyScheme from spine + agency + organisation_contact."""
    name, desc = _read_name_and_description(conn, urn)
    annotations_by_owner = _read_annotations_subtree(conn, urn)
    anns = annotations_by_owner.get(urn, Annotations.EMPTY)
    links = _links_of(conn, urn, meta)

    rows = conn.execute(
        "SELECT agency_urn, agency_id, name, description, uri FROM agency WHERE scheme_urn = ?",
        [urn],
    ).fetchall()

    contacts_by_agency = _contacts_by_owner(conn, "SELECT agency_urn FROM agency WHERE scheme_urn = ?", urn)

    agencies: list[Agency] = []
    for row in rows:
        ag_urn, ag_id, ag_name, ag_desc, ag_uri = row
        contacts = contacts_by_agency.get(ag_urn, [])
        ag_anns = annotations_by_owner.get(ag_urn, Annotations.EMPTY)
        agencies.append(
            Agency(
                id=ag_id,
                name=_map_to_istring(ag_name),
                description=_map_to_istring(ag_desc),
                contacts=tuple(contacts),
                annotations=ag_anns,
                links=links.get(ag_urn, ()),
                uri=ag_uri,
            )
        )

    return AgencyScheme(
        **_spine_kwargs(meta),
        is_partial=meta["is_partial"],
        name=name,
        description=desc,
        agencies=agencies,
        annotations=anns,
        links=links.get(urn, ()),
    )


def _read_data_provider_scheme(conn: duckdb.DuckDBPyConnection, urn: str, meta: SpineRow) -> DataProviderScheme | None:
    """Read a DataProviderScheme from spine + data_provider + organisation_contact."""
    name, desc = _read_name_and_description(conn, urn)
    annotations_by_owner = _read_annotations_subtree(conn, urn)
    anns = annotations_by_owner.get(urn, Annotations.EMPTY)
    links = _links_of(conn, urn, meta)

    rows = conn.execute(
        "SELECT provider_urn, provider_id, name, description, uri FROM data_provider WHERE scheme_urn = ?",
        [urn],
    ).fetchall()
    contacts_by_provider = _contacts_by_owner(conn, "SELECT provider_urn FROM data_provider WHERE scheme_urn = ?", urn)

    providers: list[DataProvider] = []
    for row in rows:
        p_urn, p_id, p_name, p_desc, p_uri = row
        p_anns = annotations_by_owner.get(p_urn, Annotations.EMPTY)
        providers.append(
            DataProvider(
                id=p_id,
                name=_map_to_istring(p_name),
                description=_map_to_istring(p_desc),
                contacts=tuple(contacts_by_provider.get(p_urn, ())),
                annotations=p_anns,
                links=links.get(p_urn, ()),
                uri=p_uri,
            )
        )

    return DataProviderScheme(
        **_spine_kwargs(meta),
        is_partial=meta["is_partial"],
        name=name,
        description=desc,
        providers=providers,
        annotations=anns,
        links=links.get(urn, ()),
    )


def _read_metadata_provider_scheme(
    conn: duckdb.DuckDBPyConnection, urn: str, meta: SpineRow
) -> MetadataProviderScheme | None:
    """Read a MetadataProviderScheme from spine + metadata_provider + contacts.

    The metadata twin of `_read_data_provider_scheme` (#315).
    """
    name, desc = _read_name_and_description(conn, urn)
    annotations_by_owner = _read_annotations_subtree(conn, urn)
    anns = annotations_by_owner.get(urn, Annotations.EMPTY)
    links = _links_of(conn, urn, meta)

    rows = conn.execute(
        "SELECT provider_urn, provider_id, name, description, uri FROM metadata_provider WHERE scheme_urn = ?",
        [urn],
    ).fetchall()
    contacts_by_provider = _contacts_by_owner(
        conn, "SELECT provider_urn FROM metadata_provider WHERE scheme_urn = ?", urn
    )

    providers: list[MetadataProvider] = []
    for row in rows:
        p_urn, p_id, p_name, p_desc, p_uri = row
        p_anns = annotations_by_owner.get(p_urn, Annotations.EMPTY)
        providers.append(
            MetadataProvider(
                id=p_id,
                name=_map_to_istring(p_name),
                description=_map_to_istring(p_desc),
                contacts=tuple(contacts_by_provider.get(p_urn, ())),
                annotations=p_anns,
                links=links.get(p_urn, ()),
                uri=p_uri,
            )
        )

    return MetadataProviderScheme(
        **_spine_kwargs(meta),
        is_partial=meta["is_partial"],
        name=name,
        description=desc,
        providers=providers,
        annotations=anns,
        links=links.get(urn, ()),
    )


def _read_data_consumer_scheme(conn: duckdb.DuckDBPyConnection, urn: str, meta: SpineRow) -> DataConsumerScheme | None:
    """Read a DataConsumerScheme from spine + data_consumer + organisation_contact (#469)."""
    name, desc = _read_name_and_description(conn, urn)
    annotations_by_owner = _read_annotations_subtree(conn, urn)
    anns = annotations_by_owner.get(urn, Annotations.EMPTY)
    links = _links_of(conn, urn, meta)

    rows = conn.execute(
        "SELECT consumer_urn, consumer_id, name, description, uri FROM data_consumer WHERE scheme_urn = ?",
        [urn],
    ).fetchall()
    contacts = _contacts_by_owner(conn, "SELECT consumer_urn FROM data_consumer WHERE scheme_urn = ?", urn)
    consumers = [
        DataConsumer(
            id=c_id,
            name=_map_to_istring(c_name),
            description=_map_to_istring(c_desc),
            contacts=tuple(contacts.get(c_urn, ())),
            annotations=annotations_by_owner.get(c_urn, Annotations.EMPTY),
            links=links.get(c_urn, ()),
            uri=c_uri,
        )
        for c_urn, c_id, c_name, c_desc, c_uri in rows
    ]

    return DataConsumerScheme(
        **_spine_kwargs(meta),
        is_partial=meta["is_partial"],
        name=name,
        description=desc,
        consumers=consumers,
        annotations=anns,
        links=links.get(urn, ()),
    )


def _read_organisation_unit_scheme(
    conn: duckdb.DuckDBPyConnection, urn: str, meta: SpineRow
) -> OrganisationUnitScheme | None:
    """Read an OrganisationUnitScheme from spine + organisation_unit + organisation_contact (#469)."""
    name, desc = _read_name_and_description(conn, urn)
    annotations_by_owner = _read_annotations_subtree(conn, urn)
    anns = annotations_by_owner.get(urn, Annotations.EMPTY)
    links = _links_of(conn, urn, meta)

    rows = conn.execute(
        "SELECT unit_urn, unit_id, parent_id, name, description, uri FROM organisation_unit WHERE scheme_urn = ?",
        [urn],
    ).fetchall()
    contacts = _contacts_by_owner(conn, "SELECT unit_urn FROM organisation_unit WHERE scheme_urn = ?", urn)
    units = [
        OrganisationUnit(
            id=u_id,
            name=_map_to_istring(u_name),
            description=_map_to_istring(u_desc),
            contacts=tuple(contacts.get(u_urn, ())),
            annotations=annotations_by_owner.get(u_urn, Annotations.EMPTY),
            parent_id=u_parent,
            links=links.get(u_urn, ()),
            uri=u_uri,
        )
        for u_urn, u_id, u_parent, u_name, u_desc, u_uri in rows
    ]

    return OrganisationUnitScheme(
        **_spine_kwargs(meta),
        is_partial=meta["is_partial"],
        name=name,
        description=desc,
        organisation_units=units,
        annotations=anns,
        links=links.get(urn, ()),
    )


def _read_representation_map(conn: duckdb.DuckDBPyConnection, urn: str, meta: SpineRow) -> RepresentationMap | None:
    """Read a RepresentationMap from spine + its endpoint and mapping tables."""
    name, desc = _read_name_and_description(conn, urn)
    by_owner = _read_annotations_subtree(conn, urn)
    links = _links_of(conn, urn, meta)
    anns = by_owner.get(urn, Annotations.EMPTY)

    if first(conn.execute("SELECT 1 FROM representation_map WHERE urn = ?", [urn])) is None:
        return None

    def endpoints(side: str) -> tuple[MapEndpoint, ...]:
        rows: list[tuple[str | None, str | None]] = conn.execute(
            f"SELECT codelist_urn, data_type FROM representation_map_{side} "
            "WHERE representation_map_urn = ? ORDER BY ordinal",
            [urn],
        ).fetchall()
        return tuple(
            enumeration_ref(codelist_urn) if codelist_urn else MapDataType(data_type or "")
            for codelist_urn, data_type in rows
        )

    mapping_rows: list[
        tuple[list[str], list[bool], list[int | None], list[int | None], list[str], date | None, date | None]
    ] = conn.execute(
        """
        SELECT sources, source_is_regex, source_start_index, source_end_index, targets, valid_from, valid_to
        FROM representation_mapping WHERE representation_map_urn = ? ORDER BY ordinal
        """,
        [urn],
    ).fetchall()
    mappings = tuple(
        RepresentationMapping(
            sources=tuple(
                SourceValue(value=v, is_regex=rx, start_index=start, end_index=end)
                for v, rx, start, end in zip(values, regex, starts, ends, strict=True)
            ),
            targets=tuple(targets),
            valid_from=vf,
            valid_to=vt,
            annotations=by_owner.get(part_owner(urn, "mappings", i), Annotations.EMPTY),
        )
        for i, (values, regex, starts, ends, targets, vf, vt) in enumerate(mapping_rows)
    )

    return RepresentationMap(
        **_spine_kwargs(meta),
        name=name,
        description=desc,
        sources=endpoints("source"),
        targets=endpoints("target"),
        mappings=mappings,
        annotations=anns,
        links=links.get(urn, ()),
    )


def _read_map_ends(
    conn: duckdb.DuckDBPyConnection, urn: str, parent_table: str
) -> tuple[Ref[object] | None, Ref[object] | None] | None:
    """A scheme or structure map's source and target (#395); `None` when the row is absent."""
    row = first(conn.execute(f"SELECT source_urn, target_urn FROM {parent_table} WHERE urn = ?", [urn]))
    if row is None:
        return None
    src, tgt = row
    source = Ref[object].unresolved(SdmxUrn.parse(src)) if src else None
    target = Ref[object].unresolved(SdmxUrn.parse(tgt)) if tgt else None
    return source, target


def _read_map_parts(
    conn: duckdb.DuckDBPyConnection, urn: str, *, parent_table: str
) -> tuple[Ref[object] | None, Ref[object] | None, list[tuple[str, str, date | None, date | None]]] | None:
    """Endpoints and ordered item maps for a concept- or category-scheme map (#395).

    Shared because both store the same shape in `item_map`; the artefacts are
    built separately below, where each class's fields are known — the same
    reason `put_container`'s readers are separate, recorded on #395.

    Returns `None` when the parent row is absent, which is how the callers
    report "not stored".
    """
    ends = _read_map_ends(conn, urn, parent_table)
    if ends is None:
        return None
    pairs = conn.execute(
        "SELECT source_id, target_id, valid_from, valid_to FROM item_map WHERE map_urn = ? ORDER BY ordinal",
        [urn],
    ).fetchall()
    return *ends, [(str(a), str(b), vf, vt) for a, b, vf, vt in pairs]


def _read_item_scheme_map[T: (ConceptSchemeMap, CategorySchemeMap, OrganisationSchemeMap)](
    conn: duckdb.DuckDBPyConnection, urn: str, meta: SpineRow, cls: type[T], parent_table: str
) -> T | None:
    """Read a concept, category or organisation scheme map from spine + `parent_table` + item_map."""
    parts = _read_map_parts(conn, urn, parent_table=parent_table)
    if parts is None:
        return None
    source, target, pairs = parts
    name, desc = _read_name_and_description(conn, urn)
    by_owner = _read_annotations_subtree(conn, urn)
    links = _links_of(conn, urn, meta)
    return cls(
        **_spine_kwargs(meta),
        name=name,
        description=desc,
        source=source,
        target=target,
        maps=tuple(
            ItemMap(
                source_id=a,
                target_id=b,
                annotations=by_owner.get(part_owner(urn, "maps", i), Annotations.EMPTY),
                valid_from=vf,
                valid_to=vt,
            )
            for i, (a, b, vf, vt) in enumerate(pairs)
        ),
        annotations=by_owner.get(urn, Annotations.EMPTY),
        links=links.get(urn, ()),
    )


def _read_concept_scheme_map(conn: duckdb.DuckDBPyConnection, urn: str, meta: SpineRow) -> ConceptSchemeMap | None:
    return _read_item_scheme_map(conn, urn, meta, ConceptSchemeMap, "concept_scheme_map")


def _read_category_scheme_map(conn: duckdb.DuckDBPyConnection, urn: str, meta: SpineRow) -> CategorySchemeMap | None:
    return _read_item_scheme_map(conn, urn, meta, CategorySchemeMap, "category_scheme_map")


def _read_organisation_scheme_map(
    conn: duckdb.DuckDBPyConnection, urn: str, meta: SpineRow
) -> OrganisationSchemeMap | None:
    return _read_item_scheme_map(conn, urn, meta, OrganisationSchemeMap, "organisation_scheme_map")


class _StructureMapParts(TypedDict, closed=True):
    """A `StructureMap`'s SDMX 3.x lists beyond its component maps (#422)."""

    epoch_maps: tuple[EpochMap, ...]
    date_pattern_maps: tuple[DatePatternMap, ...]
    frequency_format_mappings: tuple[FrequencyFormatMapping, ...]
    fixed_value_maps: tuple[FixedValueMap, ...]


def _read_structure_map_parts(
    conn: duckdb.DuckDBPyConnection,
    urn: str,
    by_owner: dict[str, Annotations],
    links: dict[str, tuple[Link, ...]],
) -> _StructureMapParts:
    """A structure map's date and value maps, from their four tables (#422)."""

    def item_annotations(kind: str, item_id: str) -> Annotations:
        return by_owner.get(_component_urn(urn, kind, item_id), Annotations.EMPTY)

    def item_links(kind: str, item_id: str) -> tuple[Link, ...]:
        return links.get(_component_urn(urn, kind, item_id), ())

    tail = "frequency_dimension, mapped_frequencies, target_frequency_id, resolve_period"
    epoch_rows = conn.execute(
        f"SELECT map_id, uri, source_ids, target_ids, base_period, epoch_period, {tail} "
        "FROM epoch_map WHERE structure_map_urn = ? ORDER BY ordinal",
        [urn],
    ).fetchall()
    pattern_rows = conn.execute(
        f"SELECT map_id, uri, source_ids, target_ids, source_pattern, locale, {tail} "
        "FROM date_pattern_map WHERE structure_map_urn = ? ORDER BY ordinal",
        [urn],
    ).fetchall()
    format_rows = conn.execute(
        "SELECT mapping_id, uri, frequency_id, date_pattern FROM frequency_format_mapping "
        "WHERE structure_map_urn = ? ORDER BY ordinal",
        [urn],
    ).fetchall()
    fixed_rows = conn.execute(
        'SELECT source_id, target_id, "values" FROM fixed_value_map WHERE structure_map_urn = ? ORDER BY ordinal',
        [urn],
    ).fetchall()
    return {
        "epoch_maps": tuple(
            EpochMap(
                id=map_id,
                uri=uri,
                component_maps=tuple(zip(sources or [], targets or [], strict=True)),
                base_period=base,
                epoch_period=EpochPeriod(period),
                frequency_dimension=dimension,
                mapped_frequencies=tuple(mapped or []),
                target_frequency_id=to_freq,
                resolve_period=ResolvePeriod(resolve) if resolve else None,
                annotations=item_annotations("EpochMap", map_id),
                links=item_links("EpochMap", map_id),
            )
            for map_id, uri, sources, targets, base, period, dimension, mapped, to_freq, resolve in epoch_rows
        ),
        "date_pattern_maps": tuple(
            DatePatternMap(
                id=map_id,
                uri=uri,
                component_maps=tuple(zip(sources or [], targets or [], strict=True)),
                source_pattern=pattern,
                locale=locale,
                frequency_dimension=dimension,
                mapped_frequencies=tuple(mapped or []),
                target_frequency_id=to_freq,
                resolve_period=ResolvePeriod(resolve) if resolve else None,
                annotations=item_annotations("DatePatternMap", map_id),
                links=item_links("DatePatternMap", map_id),
            )
            for map_id, uri, sources, targets, pattern, locale, dimension, mapped, to_freq, resolve in pattern_rows
        ),
        "frequency_format_mappings": tuple(
            FrequencyFormatMapping(
                id=mapping_id,
                uri=uri,
                frequency_id=frequency,
                date_pattern=pattern,
                annotations=item_annotations("FrequencyFormatMapping", mapping_id),
                links=item_links("FrequencyFormatMapping", mapping_id),
            )
            for mapping_id, uri, frequency, pattern in format_rows
        ),
        "fixed_value_maps": tuple(
            FixedValueMap(
                source_id=source,
                target_id=target,
                values=tuple(values or []),
                annotations=by_owner.get(part_owner(urn, "fixed_value_maps", i), Annotations.EMPTY),
            )
            for i, (source, target, values) in enumerate(fixed_rows)
        ),
    }


def _read_structure_map(conn: duckdb.DuckDBPyConnection, urn: str, meta: SpineRow) -> StructureMap | None:
    """Read a StructureMap from spine + structure_map + component_map."""
    ends = _read_map_ends(conn, urn, "structure_map")
    if ends is None:
        return None
    source, target = ends
    component_maps = conn.execute(
        f"SELECT sources, targets, representation_map_urn, to_value_type, {', '.join(TO_TEXT_FORMAT_COLUMN_NAMES)} "
        "FROM component_map WHERE structure_map_urn = ? ORDER BY ordinal",
        [urn],
    ).fetchall()
    name, desc = _read_name_and_description(conn, urn)
    extension = first(conn.execute("SELECT is_extension FROM structure_map WHERE urn = ?", [urn]))
    by_owner = _read_annotations_subtree(conn, urn)
    links = _links_of(conn, urn, meta)
    return StructureMap(
        **_spine_kwargs(meta),
        name=name,
        description=desc,
        source=source,
        target=target,
        maps=tuple(
            ComponentMap(
                sources=tuple(sources),
                targets=tuple(targets),
                annotations=by_owner.get(part_owner(urn, "maps", i), Annotations.EMPTY),
                representation_map=Ref[object].unresolved(SdmxUrn.parse(rep)) if rep else None,
                to_text_format=_build_enumeration_format(**dict(zip(FACET_COLUMN_NAMES[2:], text_format, strict=True))),
                to_value_type=to_value_type,
            )
            for i, (sources, targets, rep, to_value_type, *text_format) in enumerate(component_maps)
        ),
        is_extension=bool(extension and extension[0]),
        **_read_structure_map_parts(conn, urn, by_owner, links),
        annotations=by_owner.get(urn, Annotations.EMPTY),
        links=links.get(urn, ()),
    )


# ── MetadataStructure / MetadataSet readers ────────────────────────────


def _read_metadata_structure(conn: duckdb.DuckDBPyConnection, urn: str, meta: SpineRow) -> MetadataStructure | None:
    """Read a MetadataStructure from spine + metadata_attribute rows."""
    name, desc = _read_name_and_description(conn, urn)
    annotations_by_owner = _read_annotations_subtree(conn, urn)
    anns = annotations_by_owner.get(urn, Annotations.EMPTY)
    links = _links_of(conn, urn, meta)

    rows = conn.execute(
        """
        SELECT attribute_urn, attribute_id, path, parent_path, position,
               concept_urn, repr_kind, codelist_urn, text_type,
               min_length, max_length, min_value, max_value,
               decimals, pattern, is_sequence, start_value, end_value,
               is_multilingual,
               interval,
               time_interval,
               start_time,
               end_time,
               enum_text_type, enum_min_length, enum_max_length,
               enum_min_value, enum_max_value, enum_decimals, enum_pattern,
               enum_is_sequence, enum_start_value, enum_end_value,
               enum_is_multilingual,
               enum_interval,
               enum_time_interval,
               enum_start_time,
               enum_end_time,
               min_occurs, max_occurs, is_presentational, uri, sentinel_values
        FROM metadata_attribute
        WHERE msd_urn = ?
        ORDER BY position
        """,
        [urn],
    ).fetchall()

    # Keyed by path: an attribute id may repeat in another branch (#439).
    # Built after its children, which a frozen attribute cannot be given later (#492).
    pending: dict[str, partial[MetadataAttribute]] = {}
    children_map: dict[str | None, list[str]] = {}

    for row in rows:
        (
            attr_urn,
            attr_id,
            path,
            parent_path,
            _position,
            concept_urn_str,
            repr_kind,
            codelist_urn,
            text_type,
            min_length,
            max_length,
            min_value,
            max_value,
            decimals,
            pattern,
            is_sequence,
            start_value,
            end_value,
            is_multilingual,
            interval,
            time_interval,
            start_time,
            end_time,
            enum_text_type,
            enum_min_length,
            enum_max_length,
            enum_min_value,
            enum_max_value,
            enum_decimals,
            enum_pattern,
            enum_is_sequence,
            enum_start_value,
            enum_end_value,
            enum_is_multilingual,
            enum_interval,
            enum_time_interval,
            enum_start_time,
            enum_end_time,
            min_occurs,
            max_occurs,
            is_presentational,
            attr_uri,
            sentinel_values,
        ) = row

        concept_ref = Ref[Concept].unresolved(Concept.parse_urn(concept_urn_str)) if concept_urn_str else None
        representation = _build_representation(
            repr_kind,
            codelist_urn,
            text_type=text_type,
            min_length=min_length,
            max_length=max_length,
            min_value=min_value,
            max_value=max_value,
            decimals=decimals,
            pattern=pattern,
            is_sequence=is_sequence,
            start_value=start_value,
            end_value=end_value,
            is_multilingual=is_multilingual,
            interval=interval,
            time_interval=time_interval,
            start_time=start_time,
            end_time=end_time,
            sentinel_values=sentinel_values,
        )
        attr_anns = annotations_by_owner.get(attr_urn, Annotations.EMPTY)

        pending[path] = partial(
            MetadataAttribute,
            id=attr_id,
            concept=concept_ref,
            representation=representation,
            enumeration_format=_build_enumeration_format(
                text_type=enum_text_type,
                min_length=enum_min_length,
                max_length=enum_max_length,
                min_value=enum_min_value,
                max_value=enum_max_value,
                decimals=enum_decimals,
                pattern=enum_pattern,
                is_sequence=enum_is_sequence,
                start_value=enum_start_value,
                end_value=enum_end_value,
                is_multilingual=enum_is_multilingual,
                interval=enum_interval,
                time_interval=enum_time_interval,
                start_time=enum_start_time,
                end_time=enum_end_time,
            ),
            min_occurs=min_occurs,
            max_occurs=max_occurs,
            is_presentational=is_presentational,
            annotations=attr_anns,
            uri=attr_uri,
            links=links.get(attr_urn, ()),
        )
        children_map.setdefault(parent_path, []).append(path)

    attributes = _assemble(children_map, lambda path, kids: pending[path](children=kids))

    list_uri = first(conn.execute("SELECT attribute_list_uri FROM metadata_structure WHERE urn = ?", [urn]))
    return MetadataStructure(
        **_spine_kwargs_21(meta),
        name=name,
        description=desc,
        annotations=anns,
        links=links.get(urn, ()),
        attributes=attributes,
        attribute_list_annotations=annotations_by_owner.get(
            _component_urn(urn, "MetadataAttributeDescriptor", "MetadataAttributeDescriptor"), Annotations.EMPTY
        ),
        attribute_list_uri=list_uri[0] if list_uri else None,
        attribute_list_links=links.get(
            _component_urn(urn, "MetadataAttributeDescriptor", "MetadataAttributeDescriptor"), ()
        ),
    )


def _build_reported_value(
    value_kind: str | None,
    scalar: str | None,
    i18n: dict[str, str] | None,
) -> ReportedAttributeValue | None:
    """Reconstruct one ReportedAttribute.value from its row fields."""
    if value_kind is None:
        return None
    if value_kind == "i18n":
        return _map_to_istring(i18n)
    return scalar


def _strip_sibling_index(segment: str) -> tuple[str, int]:
    """Split a path segment ``id`` or ``id[N]`` into (id, sibling_index)."""
    if segment.endswith("]") and "[" in segment:
        bare, _, rest = segment.rpartition("[")
        return bare, int(rest[:-1])
    return segment, 0


def _build_reported_tree(
    rows: list[tuple[str, int, str | None, str | None, dict[str, str] | None]],
) -> ItemList[ReportedAttribute]:
    """Reconstruct a ReportedAttribute tree from metadata_value rows, in document order.

    Rows are ``(attribute_path, sibling_index, value_kind, scalar, i18n)``,
    ordered by ``ordinal``: a pre-order walk of the tree as written, so a
    parent comes before its children and siblings come in their order
    (GH-197). Paths are dot-joined attribute ids; repeated siblings carry an
    ``[N]`` suffix on the segment, e.g. ``"QUALITY.NOTE[1]"``.
    """
    # Nodes by full path (with [N] suffixes) so the tree structure is
    # unambiguous even with sibling repetition; every prefix, so an ancestor
    # with no row of its own still materialises, at its first descendant.
    # Each node is built after its children, with its value (#492).
    values: dict[str, ReportedAttributeValue | None] = {}
    children_map: dict[str | None, list[str]] = {}
    placed: set[str] = set()
    for path, _idx, kind, scalar, i18n in rows:
        values[path] = _build_reported_value(kind, scalar, i18n)
        parts = path.split(".")
        for depth in range(1, len(parts) + 1):
            node = ".".join(parts[:depth])
            if node not in placed:
                placed.add(node)
                children_map.setdefault(".".join(parts[: depth - 1]) or None, []).append(node)

    def build(path: str, kids: ItemList[ReportedAttribute]) -> ReportedAttribute:
        leaf_id, _ = _strip_sibling_index(path.rsplit(".", 1)[-1])
        return ReportedAttribute(id=leaf_id, value=values.get(path), children=kids)

    return _assemble(children_map, build)


def _read_declared_targets(conn: duckdb.DuckDBPyConnection, source_urn: str) -> tuple[str, ...]:
    """Ordered declared-target URN strings for a Metadataflow / MPA.

    Raw strings (possibly wildcarded ``WildcardUrnType``) as stored — the
    caller decides whether to keep them as strings (Metadataflow) or wrap
    them in Refs (MetadataProvisionAgreement).
    """
    rows = conn.execute(
        "SELECT target_urn FROM metadata_declared_target WHERE source_urn = ? ORDER BY ordinal",
        [source_urn],
    ).fetchall()
    return tuple(r[0] for r in rows)


def _read_metadataflow(conn: duckdb.DuckDBPyConnection, urn: str, meta: SpineRow) -> Metadataflow | None:
    """Read a Metadataflow from spine + metadataflow extension."""
    name, desc = _read_name_and_description(conn, urn)
    anns = _read_annotations(conn, urn)
    links = _links_of(conn, urn, meta)

    row = first(conn.execute("SELECT msd_urn FROM metadataflow WHERE urn = ?", [urn]))
    structure_ref = None
    if row and row[0]:
        structure_ref = Ref[MetadataStructure].unresolved(MetadataStructure.parse_urn(row[0]))

    targets = _read_declared_targets(conn, urn)

    return Metadataflow(
        **_spine_kwargs_21(meta),
        name=name,
        description=desc,
        annotations=anns,
        links=links.get(urn, ()),
        structure=structure_ref,
        targets=targets,
    )


def _read_metadata_provision_agreement(
    conn: duckdb.DuckDBPyConnection, urn: str, meta: SpineRow
) -> MetadataProvisionAgreement | None:
    """Read a MetadataProvisionAgreement from spine + extension table."""
    name, desc = _read_name_and_description(conn, urn)
    anns = _read_annotations(conn, urn)
    links = _links_of(conn, urn, meta)

    row = first(
        conn.execute(
            "SELECT metadataflow_urn, metadata_provider_urn FROM metadata_provision_agreement WHERE urn = ?",
            [urn],
        )
    )
    flow_ref = Ref[Metadataflow].unresolved(Metadataflow.parse_urn(row[0])) if row and row[0] else None
    provider_ref = Ref[MetadataProvider].unresolved(MetadataProvider.parse_urn(row[1])) if row and row[1] else None
    # Strings, not Refs: a declared target may be a wildcard URN, which no
    # resolvable reference can express (#362) — same as `Metadataflow.targets`.
    targets = tuple(_read_declared_targets(conn, urn))

    return MetadataProvisionAgreement(
        **_spine_kwargs(meta),
        name=name,
        description=desc,
        annotations=anns,
        links=links.get(urn, ()),
        metadataflow=flow_ref,
        metadata_provider=provider_ref,
        targets=targets,
    )


def _read_metadata_set(conn: duckdb.DuckDBPyConnection, urn: str, meta: SpineRow) -> MetadataSet | None:
    """Read a MetadataSet from spine + metadata_set + targets + values."""
    name, desc = _read_name_and_description(conn, urn)
    anns = _read_annotations(conn, urn)
    links = _links_of(conn, urn, meta)

    set_row = first(conn.execute("SELECT structure_urn, structure_kind FROM metadata_set WHERE urn = ?", [urn]))
    if set_row is None:
        return None
    structure_urn, structure_kind = set_row
    parsed_structure_urn = SdmxUrn.parse(structure_urn)
    structure_ref: Ref[Metadataflow] | Ref[MetadataProvisionAgreement] = (
        Ref[MetadataProvisionAgreement].unresolved(cast("SdmxUrn[MetadataProvisionAgreement]", parsed_structure_urn))
        if structure_kind == "MetadataProvisionAgreement"
        else Ref[Metadataflow].unresolved(cast("SdmxUrn[Metadataflow]", parsed_structure_urn))
    )

    target_rows = conn.execute(
        """
        SELECT target_urn FROM metadata_target
        WHERE set_urn = ?
        ORDER BY ordinal
        """,
        [urn],
    ).fetchall()
    targets = [Ref[object].unresolved(SdmxUrn.parse(t[0])) for t in target_rows]

    value_rows = conn.execute(
        """
        SELECT attribute_path, sibling_index, value_kind, scalar, i18n
        FROM metadata_value
        WHERE set_urn = ?
        ORDER BY ordinal
        """,
        [urn],
    ).fetchall()
    attributes = _build_reported_tree(value_rows)

    return MetadataSet(
        **_spine_kwargs(meta),
        structure=structure_ref,
        name=name,
        description=desc,
        annotations=anns,
        links=links.get(urn, ()),
        targets=targets,
        attributes=attributes,
    )


# ── DataflowBinding reader ──────────────────────────────────────────────


def _read_dataflow_binding(conn: duckdb.DuckDBPyConnection, urn: str, _meta: SpineRow) -> DataflowBinding | None:
    """Read a DataflowBinding from its dataflow_binding row.

    Takes the spine row for the dispatch signature and reads nothing from it:
    a binding's identity is its dataflow's, carried on its own row.
    """
    row = first(
        conn.execute(
            """
        SELECT dataflow_urn, table_location, database_role,
               dimension_column_map, measure_column_map, attribute_column_map,
               structural_version, binding_source_registry,
               materialized_at, bound_at, series_attributes_location
        FROM dataflow_binding WHERE urn = ?
        """,
            [urn],
        )
    )
    if row is None:
        return None

    (
        dataflow_urn_str,
        table_location,
        database_role,
        dim_map,
        meas_map,
        attr_map,
        structural_version,
        binding_source_registry,
        materialized_at,
        bound_at,
        series_attributes_location,
    ) = row

    return DataflowBinding(
        dataflow=Ref[Dataflow].unresolved(Dataflow.parse_urn(dataflow_urn_str)),
        table_location=table_location,
        database_role=database_role,
        dimension_column_map=dict(dim_map or {}),
        measure_column_map=dict(meas_map or {}),
        attribute_column_map=dict(attr_map or {}),
        structural_version=structural_version,
        source_registry=binding_source_registry,
        # Schema stores TIMESTAMP (naive UTC); model contract is tz-aware.
        # Both are NOT NULL in the schema, so `from_storage` cannot return
        # None here; the narrowing is for the checker, which cannot read DDL.
        materialized_at=_required(from_storage(materialized_at), "materialized_at"),
        bound_at=_required(from_storage(bound_at), "bound_at"),
        series_attributes_location=series_attributes_location,
    )


# ── Public API ──────────────────────────────────────────────────────────

_READER_DISPATCH: dict[str, Callable[..., object]] = {
    "Codelist": _read_codelist_eager,
    "ValueList": _read_value_list,
    "DataStructure": _read_data_structure,
    "Dataflow": _read_dataflow,
    "ConceptScheme": _read_concept_scheme,
    "Hierarchy": _read_hierarchy,
    "HierarchyAssociation": _read_hierarchy_association,
    "DataConstraint": _read_data_constraint,
    "CategoryScheme": _read_category_scheme,
    "Categorisation": _read_categorisation,
    "ProvisionAgreement": _read_provision_agreement,
    "AgencyScheme": _read_agency_scheme,
    "DataProviderScheme": _read_data_provider_scheme,
    "MetadataProviderScheme": _read_metadata_provider_scheme,
    "DataConsumerScheme": _read_data_consumer_scheme,
    "OrganisationUnitScheme": _read_organisation_unit_scheme,
    "RepresentationMap": _read_representation_map,
    "ConceptSchemeMap": _read_concept_scheme_map,
    "CategorySchemeMap": _read_category_scheme_map,
    "OrganisationSchemeMap": _read_organisation_scheme_map,
    "StructureMap": _read_structure_map,
    "MetadataStructure": _read_metadata_structure,
    "Metadataflow": _read_metadataflow,
    "MetadataProvisionAgreement": _read_metadata_provision_agreement,
    "MetadataSet": _read_metadata_set,
    "DataflowBinding": _read_dataflow_binding,
    "HierarchicalCodelist": _read_hierarchical_codelist,
    "StructureSet": _read_structure_set,
}


def get_artefact(
    conn: duckdb.DuckDBPyConnection,
    urn: str,
    *,
    eager: bool = False,
    lazy_conn: duckdb.DuckDBPyConnection | None = None,
    refused: dict[str, Refusal] | None = None,
) -> Any | None:  # noqa: ANN401 — runtime dispatch on artefact_type; see docstring
    """Look up an artefact by URN string.

    For Codelists, returns a ``LazyCodelist`` by default. Pass
    ``eager=True`` to get a real ``Codelist`` with all codes loaded.

    Returns ``None`` if the URN is not in the store. An eager build that
    breaks a rule `Registry.add` enforces raises `SdmxConformanceError`
    carrying its `Refusal` (#492, `storage/_checked.py`) — or, when
    ``refused`` is given, is recorded there by URN and answered ``None``, which
    is how a bulk read leaves one artefact out and reads on. A lazy facade is
    checked when it materialises.

    The ``Any`` return is deliberate and is the **last** one in ``src/`` that
    is not either a documented dynamic proxy (`Ref.__getattr__`) or an
    ``Ref[object]``. It stays because the artefact class is a *runtime* value: this
    dispatches on the ``artefact_type`` column through ``_READERS``, so no
    single static type describes the result. Narrowing to ``object | None``
    would push an isinstance step onto all 66 call sites without making
    anything safer — the caller already knows which URN it asked for.

    The typed entry point is `Registry.get`, which recovers the class from a
    ``SdmxUrn[T]`` witness. Prefer it; this is the untyped shell it is built
    on, and the boundary CLAUDE.md's "typed core, untyped shell" describes.

    ``conn`` is used for the artefact-type lookup and forwarded to the
    eager reader (it may be a short-lived per-call cursor — that's safe
    because eager reads complete before this function returns).
    ``lazy_conn`` is the long-lived connection handed to lazy facades so
    they outlive ``conn``; defaults to ``conn`` when not supplied
    (caller asserts ``conn`` will outlive the returned facade — only
    appropriate when ``conn`` *is* the long-lived connection, never when
    it's a per-call cursor). See issue #75.
    """
    # The spine row, read once and handed on (#426). Every reader used to
    # re-read it after this function had already looked up the type from the
    # same row — a second point lookup, ~0.4 ms in DuckDB, on every `get()`.
    meta = _read_spine(conn, urn)
    if meta is None:
        return _get_item(conn, urn, eager=eager, lazy_conn=lazy_conn)

    artefact_type = meta["artefact_type"]

    facade_conn = lazy_conn if lazy_conn is not None else conn
    if artefact_type == "Codelist" and not eager:
        return make_lazy_codelist(facade_conn, urn, read=conn, meta=meta)
    if artefact_type == "Hierarchy" and not eager:
        return make_lazy_hierarchy(facade_conn, urn, read=conn, meta=meta)

    reader = _READER_DISPATCH.get(artefact_type)
    if reader is None:
        return None
    # Every eager build passes here once, before anything caches it (#492).
    try:
        return checked(artefact_type, urn, lambda: reader(conn, urn, meta))
    except SdmxConformanceError as exc:
        if refused is None:
            raise
        refused.update((refusal.urn, refusal) for refusal in exc.refused)
        return None


def _get_item(
    conn: duckdb.DuckDBPyConnection,
    urn: str,
    *,
    eager: bool,
    lazy_conn: duckdb.DuckDBPyConnection | None,
) -> SdmxItem | None:
    """An item URN, answered through the scheme that holds it (#405).

    The ``artefact`` table keys maintainables, so an item URN misses it — and
    `get_artefact` answered None for every `Code`, `Category` and `Dimension`
    URN, while the same store held the item and `Registry.get` was documented
    as looking an artefact up by URN.

    Two lookups rather than an items index: the scheme, then the item inside
    it. For a codelist or a hierarchy the scheme comes back as a lazy facade,
    so `find_item` reaches its subscript — one targeted query for one code,
    not 579,951 rows to answer for one of them. ``eager`` is forwarded rather
    than forced for that reason: an eager item lookup would materialise the
    whole scheme to return one item.

    A URN that does not parse, names no item, or names a scheme this store does
    not have all answer None, which is `get_artefact`'s existing "not here".

    Typed `SdmxItem` rather than `Any`: `get_artefact` returns `Any` because it
    dispatches on a runtime column and no static type describes the result, and
    that argument does not extend here — whatever this finds is an item, and
    `find_item` narrows it to one.
    """
    try:
        parsed = SdmxUrn.parse(urn)
    except SdmxParseError:
        return None
    if parsed.item_id is None:
        return None
    for owner_cls in owner_classes(parsed.artefact_class):
        scheme = get_artefact(conn, str(scheme_urn_of(parsed, owner_cls)), eager=eager, lazy_conn=lazy_conn)
        if scheme is None:
            continue
        found = find_item(scheme, parsed, owner_cls)
        if found is not None:
            return found
    return None


def urns_of_type(conn: duckdb.DuckDBPyConnection, artefact_type: type) -> list[str]:
    """The URN of every stored artefact of `artefact_type`, as `get_all` reads them one by one."""
    type_name = _CLASS_TO_NAME.get(artefact_type)
    if type_name is None:
        return []
    rows = conn.execute("SELECT urn FROM artefact WHERE artefact_type = ?", [type_name]).fetchall()
    return [urn for (urn,) in rows]


# ── LazyCodelist construction ───────────────────────────────────────────


def _facade_cursor(
    conn: duckdb.DuckDBPyConnection, read: duckdb.DuckDBPyConnection | None
) -> duckdb.DuckDBPyConnection:
    """The cursor a lazy facade's construction reads through.

    ``read`` when the caller already holds a per-call cursor — `get_artefact`
    always does — and otherwise a fresh one off ``conn``, so concurrent
    constructions never share a row buffer (#75). Opening one costs two
    statements' worth (the cursor and its ``USE``), which is why
    `get_artefact` passes its own instead (#426).
    """
    if read is not None:
        return read
    return sdmx_cursor(conn)


def make_lazy_codelist(
    conn: duckdb.DuckDBPyConnection,
    urn: str,
    *,
    read: duckdb.DuckDBPyConnection | None = None,
    meta: SpineRow | None = None,
) -> LazyCodelist | None:
    """Construct a LazyCodelist from spine + localized_text.

    ``conn`` must be the long-lived connection, not a per-call cursor —
    the returned LazyCodelist stores it for later method calls. Reads go
    through ``read`` if given, else a short-lived cursor (see
    `_facade_cursor`). ``meta`` is the spine row when the caller has already
    read it, as `get_artefact` has.
    """
    cur = _facade_cursor(conn, read)
    meta = meta if meta is not None else _read_spine(cur, urn)
    if meta is None:
        return None
    name, desc = _read_name_and_description(cur, urn)

    return LazyCodelist(
        **_spine_kwargs_21(meta),
        is_partial=meta["is_partial"],
        urn=urn,
        conn=conn,
        name=name,
        description=desc,
    )


def make_lazy_hierarchy(
    conn: duckdb.DuckDBPyConnection,
    urn: str,
    *,
    read: duckdb.DuckDBPyConnection | None = None,
    meta: SpineRow | None = None,
) -> LazyHierarchy | None:
    """Construct a LazyHierarchy from spine + localized_text + hierarchy_meta + levels.

    ``conn`` must be the long-lived connection — see
    `make_lazy_codelist` for the rationale (issue #75).

    No flattening, no node materialisation, no Ref resolution. Returns
    ``None`` when the spine row is absent. ``read`` and ``meta`` as for
    `make_lazy_codelist`.
    """
    cur = _facade_cursor(conn, read)
    meta = meta if meta is not None else _read_spine(cur, urn)
    if meta is None:
        return None
    name, desc = _read_name_and_description(cur, urn)

    formal_row = first(
        cur.execute(
            "SELECT has_formal_levels FROM hierarchy_meta WHERE urn = ?",
            [urn],
        )
    )
    has_formal_levels = bool(formal_row[0]) if formal_row is not None else False

    levels = _read_levels(cur, urn)

    return LazyHierarchy(
        **_spine_kwargs(meta),
        urn=urn,
        conn=conn,
        name=name,
        description=desc,
        has_formal_levels=has_formal_levels,
        levels=levels,
    )


# ── Direct SQL helpers ──────────────────────────────────────────────────


def code_map_for_dsd(
    conn: duckdb.DuckDBPyConnection,
    dsd_urn: str,
    *,
    lang: str,
) -> dict[str, dict[str, str]]:
    """Return ``{component_id: {code_id: label}}`` for all enumerated components.

    Single JOIN query — no N+1. Per-call cursor as in `code_map`, and the same
    id fallback: a code with no name in `lang` is labelled by its id rather
    than dropped, so this agrees with `Codelist.code_map` (#398).
    """
    cur = _thread_cursor(conn)
    rows = cur.execute(
        """
        SELECT dc.component_id, c.code_id, coalesce(c.name[?], c.code_id) AS label
        FROM sdmx.dsd_component dc
        JOIN sdmx.code c ON dc.codelist_urn = c.codelist_urn
        WHERE dc.dsd_urn = ?
          AND dc.codelist_urn IS NOT NULL
        ORDER BY dc.component_id, c.position
        """,
        [lang, dsd_urn],
    ).fetchall()

    result: dict[str, dict[str, str]] = {}
    for comp_id, code_id, label in rows:
        result.setdefault(comp_id, {})[code_id] = label
    return result


def _stored_attachment(level: object, path: str) -> AttachmentLevel:
    """A stored attachment level, refusing the NULL a store written before #508 may hold.

    `attachment` is required since #508, so the row would build an attribute
    the model refuses — `attachment_stated` raises the same error from here,
    and a store read reports it as refused (`storage._checked`) rather than
    building what no writer can write.

    Raises:
        ValidationError: If *level* is NULL.
    """
    if not level:
        raise ValidationError(
            [
                ValidationIssue(
                    path=f"{path}.attachment",
                    message="stored with no attachment, which every SDMX format requires (#508); re-add it with one",
                )
            ]
        )
    return AttachmentLevel(str(level))


def get_availability(conn: duckdb.DuckDBPyConnection, dataflow_urn: str) -> AvailabilityConstraint | None:
    """The stored answer to `dataflow_urn`'s unfiltered availability query, or None when none is stored."""
    head = first(
        conn.execute("SELECT obs_count, series_count FROM availability WHERE dataflow_urn = ?", [dataflow_urn])
    )
    if head is None:
        return None
    values: dict[str, list[str]] = {}
    for dimension_id, value in conn.execute(
        "SELECT dimension_id, value FROM availability_value WHERE dataflow_urn = ? ORDER BY dimension_id",
        [dataflow_urn],
    ).fetchall():
        values.setdefault(str(dimension_id), []).append(str(value))
    flow = Ref[Dataflow].unresolved(Dataflow.parse_urn(dataflow_urn))
    return AvailabilityConstraint(
        attachment=ConstraintAttachment(dataflows=(flow,)),
        region=CubeRegion(keys=tuple(KeyValue(dimension_id=d, values=frozenset(v)) for d, v in values.items())),
        obs_count=int(head[0]),
        series_count=None if head[1] is None else int(head[1]),
    )
