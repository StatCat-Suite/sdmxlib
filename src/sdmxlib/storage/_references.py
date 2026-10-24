"""The ``references`` check in SQL: every stored reference names something in the store (GH-129).

The references are the rows of ``sdmx.artefact_ref`` and the value of every
reference column the declared edges name (`query._edges.EDGES`), each held by
the artefact that owns its row, and each as the URN was written. The edge
columns cover what ``artefact_ref`` leaves out: a concept's core
representation has no row there. A pair ``artefact_ref`` holds counts once.

A target resolves when it is an artefact's URN or an item's. Items are stored by key,
in one table per kind, so their URNs are built here from the owning scheme's
URN, the item class and the item's id, or its path for a nested item, as SDMX
builds them.

A target that matches nothing exactly is tried under the spellings the
loading check (`sdmxlib.model.validation.reference_issues`) also accepts: a
URN without a version names the latest one the store holds, and the
component classes SDMX renamed (`CLASS_ALIAS_GROUPS`) match under either name.
Every reference resolves exactly in a typical bundle, so the second query
rarely runs.
"""

import duckdb
from attrs import frozen

from sdmxlib.model._items import CLASS_ALIAS_GROUPS
from sdmxlib.model.category import Category
from sdmxlib.model.codelist import Code
from sdmxlib.model.collections import SdmxItem
from sdmxlib.model.concept import Concept
from sdmxlib.model.datastructure import Attribute, Dimension, Group, Measure, MeasureDimension, TimeDimension
from sdmxlib.model.hierarchy import HierarchicalCode, Level
from sdmxlib.model.mapping import DatePatternMap, EpochMap, FrequencyFormatMapping
from sdmxlib.model.metadatastructure import MetadataAttribute
from sdmxlib.model.organisation import Agency, DataConsumer, DataProvider, MetadataProvider, OrganisationUnit
from sdmxlib.query._edges import EDGES


@frozen
class StoredItems:
    """Where the store keeps one kind of item.

    Attributes:
        item: The item class, which gives the URN its package and class.
        table: The table, in the ``sdmx`` schema.
        scheme: The column holding the owning scheme's URN.
        key: The column holding what follows the scheme in the item's URN: its
            id, or its dotted path for an item nested in a tree.
        where: A condition on the table's rows, when it holds several kinds.
    """

    item: type[SdmxItem]
    table: str
    scheme: str
    key: str
    where: str | None = None


#: Every kind of item a reference can name. `tests/unit/test_references_sql.py` asserts
#: the item classes here are those the loading check registers, less the two
#: the store does not keep.
STORED_ITEMS: tuple[StoredItems, ...] = (
    StoredItems(Agency, "agency", "scheme_urn", "agency_id"),
    StoredItems(DataConsumer, "data_consumer", "scheme_urn", "consumer_id"),
    StoredItems(DataProvider, "data_provider", "scheme_urn", "provider_id"),
    StoredItems(MetadataProvider, "metadata_provider", "scheme_urn", "provider_id"),
    StoredItems(OrganisationUnit, "organisation_unit", "scheme_urn", "unit_id"),
    StoredItems(Category, "category", "scheme_urn", "path"),
    StoredItems(Code, "code", "codelist_urn", "code_id"),
    StoredItems(Concept, "concept", "scheme_urn", "concept_id"),
    StoredItems(Dimension, "dsd_component", "dsd_urn", "component_id", "component_type = 'Dimension'"),
    StoredItems(TimeDimension, "dsd_component", "dsd_urn", "component_id", "component_type = 'TimeDimension'"),
    StoredItems(MeasureDimension, "dsd_component", "dsd_urn", "component_id", "component_type = 'MeasureDimension'"),
    StoredItems(Attribute, "dsd_component", "dsd_urn", "component_id", "component_type = 'Attribute'"),
    StoredItems(Measure, "dsd_component", "dsd_urn", "component_id", "component_type = 'Measure'"),
    StoredItems(Group, "dsd_group", "dsd_urn", "group_id"),
    StoredItems(HierarchicalCode, "hierarchy_node", "hierarchy_urn", "path"),
    StoredItems(Level, "hierarchy_level", "hierarchy_urn", "level_id"),
    StoredItems(MetadataAttribute, "metadata_attribute", "msd_urn", "path"),
    StoredItems(DatePatternMap, "date_pattern_map", "structure_map_urn", "map_id"),
    StoredItems(EpochMap, "epoch_map", "structure_map_urn", "map_id"),
    StoredItems(FrequencyFormatMapping, "frequency_format_mapping", "structure_map_urn", "mapping_id"),
)

#: A URN's parts, as `SdmxUrn.parse` reads them; an absent version or item is ''.
_URN_PARTS = r"^urn:sdmx:org\.sdmx\.infomodel\.([^.]+)\.([^=]+)=([^:]+):([^.(]+)(?:\(([^)]+)\))?(?:\.(.+))?$"
_PART_NAMES = "['package', 'class', 'agency', 'id', 'version', 'item']"


@frozen
class Unresolved:
    """A stored reference whose target is not in the store.

    Attributes:
        source: The URN of the artefact holding the reference.
        role: What the reference is to its source, as ``artefact_ref`` records it.
        target: The URN it names, as written.
    """

    source: str
    role: str
    target: str


#: Edge columns ending ``_urn`` that hold no reference, with the reason.
NOT_REFERENCES: dict[tuple[str, str], str] = {
    ("organisation_contact", "owner_urn"): "the organisation the contact belongs to, its row's owner",
    ("annotation", "owner_urn"): "what the annotation is on, its row's owner",
}

#: Reference columns that are not declared edges, as ``(table, column)`` →
#: the owner column. The edge table leaves out the first two for traversal
#: (`tests/unit/test_query_edges.py`'s ``NOT_EDGES`` gives why) and covers
#: none of the last three's tables; each is still a reference the model holds.
UNDECLARED_REFERENCES: dict[tuple[str, str], str] = {
    ("component_map", "representation_map_urn"): "structure_map_urn",
    ("dsd_group", "attachment_constraint_urn"): "dsd_urn",
    ("codelist_extension", "extends_urn"): "codelist_urn",
    ("data_constraint_data_set", "data_provider_urn"): "constraint_urn",
    ("dataflow_binding", "dataflow_urn"): "urn",
}

#: Tables whose rows no artefact holds.
NO_OWNER: dict[str, str] = {
    "hierarchy_applicability": "a view over hierarchy associations, whose own references are checked",
}


def _owner(table: str) -> str | None:
    """The column naming the artefact a row of `table` belongs to.

    ``urn`` on an artefact's own table; otherwise the first declared edge that
    must be present and points at an artefact or at an artefact's own table.
    """
    owning = [
        edge.column
        for edge in EDGES
        if edge.source == table and isinstance(edge.column, str) and not edge.optional and edge.target_column == "urn"
    ]
    if "urn" in owning:
        return "urn"
    return owning[0] if owning else None


def reference_columns() -> list[tuple[str, str, str]]:
    """Every edge column holding a reference, as ``(table, owner column, column)``."""
    found: dict[tuple[str, str], str] = {}
    for edge in EDGES:
        if edge.source == "artefact" or edge.source in NO_OWNER or not isinstance(edge.column, str):
            continue
        if not edge.column.endswith("_urn") or (edge.source, edge.column) in NOT_REFERENCES:
            continue
        owner = _owner(edge.source)
        if owner is None or owner == edge.column:
            continue
        found[edge.source, edge.column] = owner
    found |= UNDECLARED_REFERENCES
    return [(table, owner, column) for (table, column), owner in found.items()]


def _references() -> str:
    """A query of every stored reference as ``(source_urn, role, target_urn)``."""
    columns = " UNION ALL ".join(
        f"SELECT {owner}, '{column.removesuffix('_urn')}', {column} FROM sdmx.{table} WHERE {column} IS NOT NULL"
        for table, owner, column in reference_columns()
    )
    return f"""
        SELECT source_urn, role, target_urn FROM sdmx.artefact_ref
        UNION ALL
        SELECT DISTINCT e.*
        FROM ({columns}) AS e (source_urn, role, target_urn)
        WHERE NOT EXISTS (
            SELECT 1 FROM sdmx.artefact_ref AS r
            WHERE r.source_urn = e.source_urn AND r.target_urn = e.target_urn
        )
    """


def _known() -> tuple[str, list[object]]:
    """A query of every URN a reference may name, and its parameters."""
    parts = ["SELECT urn FROM sdmx.artefact"]
    params: list[object] = []
    for stored in STORED_ITEMS:
        where = f" WHERE {stored.where}" if stored.where else ""
        parts.append(
            f"SELECT 'urn:sdmx:org.sdmx.infomodel.' || ? || '.' || ? || '=' "
            f"|| split_part({stored.scheme}, '=', 2) || '.' || {stored.key} "
            f"FROM sdmx.{stored.table}{where}"
        )
        params += [stored.item.sdmx_package, stored.item.sdmx_class]
    return " UNION ALL ".join(parts), params


def unresolved_references(conn: duckdb.DuckDBPyConnection) -> list[Unresolved]:
    """Every stored reference whose target names nothing in the store, under any spelling.

    Args:
        conn: A connection to the store.

    Returns:
        The references, ordered by source, role and target.
    """
    known, params = _known()
    missing = conn.execute(
        f"""
        SELECT DISTINCT source_urn, role, target_urn
        FROM ({_references()}) AS r
        WHERE NOT EXISTS (SELECT 1 FROM ({known}) AS k (urn) WHERE k.urn = r.target_urn)
        ORDER BY source_urn, role, target_urn
        """,
        params,
    ).fetchall()
    if not missing:
        return []
    aliases = [(spelling, group[0]) for group in CLASS_ALIAS_GROUPS for spelling in group]
    alias_rows = ", ".join("(?, ?)" for _ in aliases)
    respelled = {
        target
        for (target,) in conn.execute(
            f"""
            WITH
                alias (spelling, canonical) AS (VALUES {alias_rows}),
                wanted AS (
                    SELECT DISTINCT target_urn, regexp_extract(target_urn, ?, {_PART_NAMES}) AS p
                    FROM (SELECT unnest(?::VARCHAR[]) AS target_urn)
                ),
                present AS (
                    SELECT regexp_extract(urn, ?, {_PART_NAMES}) AS p FROM ({known}) AS k (urn)
                )
            SELECT w.target_urn
            FROM wanted AS w
            JOIN present AS k
              ON k.p.package = w.p.package
             AND k.p.agency = w.p.agency
             AND k.p.id = w.p.id
             AND k.p.item = w.p.item
             AND coalesce((SELECT canonical FROM alias WHERE spelling = k.p.class), k.p.class)
               = coalesce((SELECT canonical FROM alias WHERE spelling = w.p.class), w.p.class)
             AND (k.p.version = w.p.version OR w.p.version IN ('', 'latest'))
            """,
            [
                *(value for pair in aliases for value in pair),
                _URN_PARTS,
                sorted({target for _, _, target in missing}),
                _URN_PARTS,
                *params,
            ],
        ).fetchall()
    }
    return [Unresolved(source, role, target) for source, role, target in missing if target not in respelled]
