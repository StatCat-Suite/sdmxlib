"""Write SDMX domain objects to DuckDB relational tables.

Decomposes domain objects into the fully relational schema defined by
``storage.schema``.  No JSON blobs.  Every field queryable.

Example:
    from sdmxlib.storage import schema, writers

    conn = duckdb.connect("store.duckdb")
    cursor = schema.create_schema(conn)
    writers.put_codelist(cursor, codelist)
"""

import warnings
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Final, NamedTuple, Protocol

import duckdb
import pyarrow as pa

from sdmxlib._duckdb import first
from sdmxlib._time import to_storage
from sdmxlib.model._items import owner_classes, scheme_urn_of
from sdmxlib.model._tree import tree_rows
from sdmxlib.model.annotations import Annotations
from sdmxlib.model.binding import DataflowBinding
from sdmxlib.model.category import Categorisation, CategoryScheme
from sdmxlib.model.codelist import Codelist, GeoFeatureSetCode, GeographicCodelist, GeoGridCode, GeoGridCodelist
from sdmxlib.model.concept import ConceptScheme, ISOConceptReference
from sdmxlib.model.constraint import (
    AvailabilityConstraint,
    ComponentValueSet,
    DataConstraint,
    DataKeySet,
    KeyValue,
    TimeRange,
)
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import Component, DataStructure, Group
from sdmxlib.model.errors import SdmxParseError
from sdmxlib.model.hierarchy import HierarchicalCodelist, Hierarchy, HierarchyAssociation
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.link import Link
from sdmxlib.model.mapping import (
    CategorySchemeMap,
    ConceptSchemeMap,
    DatePatternMap,
    EpochMap,
    MapDataType,
    OrganisationSchemeMap,
    RepresentationMap,
    StructureMap,
    StructureSet,
)
from sdmxlib.model.metadata_provision import MetadataProvisionAgreement
from sdmxlib.model.metadataflow import Metadataflow
from sdmxlib.model.metadataset import MetadataSet, ReportedAttribute, ReportedAttributeValue
from sdmxlib.model.metadatastructure import MetadataStructure
from sdmxlib.model.organisation import (
    AgencyScheme,
    Contact,
    DataConsumerScheme,
    DataProviderScheme,
    MetadataProviderScheme,
    OrganisationUnitScheme,
)
from sdmxlib.model.provision import ProvisionAgreement
from sdmxlib.model.ref import Ref, SupportsUrn
from sdmxlib.model.registry import artefact_urn
from sdmxlib.model.representation import Facet
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.model.valuelist import ValueList
from sdmxlib.model.version import is_final
from sdmxlib.model.version import sort_key as version_sort_key
from sdmxlib.storage._kinds import CLASS_TO_NAME as _CLASS_TO_NAME
from sdmxlib.storage.resolve import level_urn, part_owner
from sdmxlib.storage.schema import ENUMERATION_FORMAT_COLUMN_NAMES, FACET_COLUMN_NAMES, TO_TEXT_FORMAT_COLUMN_NAMES

# ── Helpers ─────────────────────────────────────────────────────────────


def _istring_to_map(s: InternationalString | None) -> dict[str, str]:
    """Convert an InternationalString to a Python dict for MAP insertion."""
    if not s:
        return {}
    return s.as_dict()


def _to_arrow_array(values: list[object]) -> pa.Array:
    """Build a pyarrow Array from a column of Python values.

    Dict values become MAP(VARCHAR, VARCHAR); other types use pyarrow's
    default inference, which handles None, scalars, and list[str] fine.
    """
    sample = next((v for v in values if v is not None), None)
    if isinstance(sample, dict):
        return pa.array(
            [_map_pairs(v) for v in values],
            type=pa.map_(pa.string(), pa.string()),
        )
    if isinstance(sample, list) and sample and isinstance(sample[0], dict):
        # A list of structs — the `sentinel_values` column (#422). Typed
        # explicitly: inferred, its MAP fields became structs keyed by the
        # first row's languages, and an empty description read back as one
        # with a null `en`.
        return pa.array(
            [None if not isinstance(v, list) else [_sentinel_struct(s) for s in v] for v in values],
            type=_SENTINEL_TYPE,
        )
    return pa.array(values)


#: The `sentinel_values` column's arrow type, matching its DDL.
_SENTINEL_TYPE: Final = pa.list_(
    pa.struct(
        [
            ("value", pa.string()),
            ("name", pa.map_(pa.string(), pa.string())),
            ("description", pa.map_(pa.string(), pa.string())),
        ]
    )
)


def _sentinel_struct(row: object) -> dict[str, object]:
    if not isinstance(row, dict):
        msg = f"mixed column: expected a sentinel struct, got {type(row).__name__}"
        raise TypeError(msg)
    return {"value": row["value"], "name": _map_pairs(row["name"]), "description": _map_pairs(row["description"])}


def _map_pairs(value: object) -> list[tuple[object, object]] | None:
    """One MAP cell as key/value pairs, or ``None``.

    Narrowing per element rather than trusting the column's first non-null
    sample: the type was `list[Any]`, so a mixed column type-checked and then
    failed with an ``AttributeError`` from inside pyarrow, nowhere near the
    artefact that caused it (#288).
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        msg = f"mixed column: expected every value to be a dict, got {type(value).__name__}"
        raise TypeError(msg)
    return list(value.items())


def _execute_bulk_insert(
    conn: duckdb.DuckDBPyConnection,
    table: str,
    cols: Sequence[str],
    rows: Sequence[Sequence[object]],
) -> None:
    """Insert many rows via Arrow columnar ingestion.

    Avoids the multi-GB SQL parser allocation that VALUES-with-literals
    causes for large bulk inserts (issue #66).
    """
    if not rows:
        return
    columns = list(zip(*rows, strict=True))
    arrays = [_to_arrow_array(list(col)) for col in columns]
    arrow_table = pa.Table.from_arrays(arrays, names=list(cols))
    conn.register("__sdmx_bulk_insert", arrow_table)
    try:
        col_str = ", ".join(cols)
        conn.execute(f"INSERT INTO {table} ({col_str}) SELECT * FROM __sdmx_bulk_insert")
    finally:
        conn.unregister("__sdmx_bulk_insert")


# ── Spine + text + annotations helpers ──────────────────────────────────


def _put_spine(
    conn: duckdb.DuckDBPyConnection,
    urn: str,
    artefact_type: str,
    artefact: SupportsUrn,
    *,
    source_registry: str | None = None,
) -> None:
    """Insert or replace a row in the artefact spine table.

    ``source_registry`` is the provenance tag for federation: ``None`` for
    untagged (locally-authored without a label), a string id for cached
    remote artefacts (``"ESTAT"``, ``"ECB"``) or local-authored ones with
    a chosen label (``"local"``).

    Issues a `RuntimeWarning` when an existing row is overwritten
    with a different ``source_registry`` — one of #46's listed pitfalls
    ("identity collisions across registries"). The write proceeds
    last-write-wins; the warning surfaces the case to the caller.
    """
    if source_registry is not None:
        existing = first(
            conn.execute(
                "SELECT source_registry FROM artefact WHERE urn = ?",
                [urn],
            )
        )
        if existing is not None and existing[0] is not None and existing[0] != source_registry:
            warnings.warn(
                (
                    f"Spine row for {urn} is being overwritten: previously "
                    f"source_registry={existing[0]!r}, now {source_registry!r}. "
                    "Two sources publishing the same URN — last write wins."
                ),
                RuntimeWarning,
                stacklevel=3,
            )
    conn.execute("DELETE FROM artefact WHERE urn = ?", [urn])
    conn.execute(
        """
        INSERT INTO artefact
            (urn, artefact_type, agency_id, resource_id, version, version_sort,
             is_final, legacy_is_final, valid_from, valid_to, source_registry, fetched_at, uri,
             is_external_ref, service_url, structure_url, is_partial, is_partial_language)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            urn,
            artefact_type,
            artefact.agency_id,
            artefact.id,
            artefact.version,
            # Baked here rather than computed per query: ordering `version` as
            # text puts 1.9 after 1.10 (#311), and the input cannot change
            # while the artefact version does not.
            version_sort_key(artefact.version),
            # Baked for the same reason: derived from the version (#420).
            is_final(artefact.version),
            getattr(artefact, "legacy_is_final", None),
            # UTC-naive, per `sdmxlib._time`: a `TIMESTAMP` column has no zone,
            # so handing it an aware value converts to the session's local
            # time and returns a different instant per reader's machine (#321).
            to_storage(getattr(artefact, "valid_from", None)),
            to_storage(getattr(artefact, "valid_to", None)),
            source_registry,
            # The other half of §1.3's second rule: *which* source answered,
            # and *when*. The column sat beside `source_registry` in the DDL
            # and was written by nothing — always NULL, which is a schema
            # promising provenance it did not keep. Set exactly when
            # `source_registry` is, so the pair travels together: an artefact
            # added locally was not fetched from anywhere and says so with
            # two NULLs rather than a timestamp implying a source.
            to_storage(datetime.now(UTC)) if source_registry is not None else None,
            getattr(artefact, "uri", None),
            # A `SupportsUrn`, which declares none of these (#422).
            bool(getattr(artefact, "is_external_reference", False)),
            getattr(artefact, "service_url", None),
            getattr(artefact, "structure_url", None),
            bool(getattr(artefact, "is_partial", False)),
            bool(getattr(artefact, "is_partial_language", False)),
        ],
    )


def _put_artefact_localized_text(
    conn: duckdb.DuckDBPyConnection,
    owner_urn: str,
    artefact: SupportsUrn,
) -> None:
    """Insert localized_text rows for an artefact's name + description.

    Collects rows across both fields and all languages into a single bulk
    insert — bilingual bundles previously paid one ``execute`` per
    (field, lang) tuple (issue #139).

    **Arrow rather than ``executemany``, and the difference is not small.**
    DuckDB's ``executemany`` probes every parameter *value* for a pandas
    null with a lazy ``import pandas``. pandas is not a dependency here and
    is not installed, and Python does not cache a failed import — so each
    probe re-walks ``sys.path``. Measured on 20,000 four-column rows:
    ``executemany`` 4.90 s and 160,000 pandas lookups, Arrow 0.03 s and one
    (#317). This was the last ``executemany`` in the writer; the rest
    already went through `_execute_bulk_insert`.

    Plain INSERT (not ``INSERT OR REPLACE``): all public writers run
    `_delete_artefact_cascade` first, so the rows are guaranteed
    absent. Avoiding ``OR REPLACE`` sidesteps a DuckDB ART-index/heap
    desync bug that surfaced as #70 (``Failed to delete all rows from
    index``) on cross-process re-ingest.
    """
    rows: list[tuple[str, str, str, str]] = []
    for field in ("name", "description"):
        istring: InternationalString | None = getattr(artefact, field, None)
        if not istring:
            continue
        rows.extend((owner_urn, field, lang, text) for lang, text in istring._backing().items())
    if not rows:
        return
    _execute_bulk_insert(conn, "localized_text", ("owner_urn", "field", "lang", "text"), rows)


class _AnnotatedPart(Protocol):
    """A map, mapping, region or key: annotated, not identifiable (#422)."""

    @property
    def annotations(self) -> Annotations: ...


class _AnnotationBatch:
    """Accumulator for annotation + annotation_text rows, and links.

    Collects rows across an entire artefact write and flushes them with a
    single bulk-allocated id batch. Replaces the per-annotation
    ``INSERT … RETURNING id`` roundtrip pattern that made persisting a
    78k-code codelist take ~130 s (issue #66). An identifiable's links ride
    along (#422): they are keyed as its annotations are, and written at the
    same sites.
    """

    __slots__ = ("ann_rows", "artefact_urn", "link_rows", "text_rows", "url_rows")

    def __init__(self, artefact_urn: str) -> None:
        # The maintainable every row in this batch belongs to. A constructor
        # argument rather than a parameter on `add`, because one batch spans
        # exactly one artefact's write — so the ten `add` call sites, which
        # pass *item* URNs, cannot get it wrong (#145).
        self.artefact_urn = artefact_urn
        # Each row: [owner_urn, owner_artefact_urn, ordinal, annotation_id, title, annotation_type, value]
        self.ann_rows: list[list[object]] = []
        # Each entry: (local_idx_into_ann_rows, lang, text). The local
        # index is resolved to a real annotation.id at flush time.
        self.text_rows: list[tuple[int, str, str]] = []
        # Each entry: (local_idx_into_ann_rows, position, url, lang, link_urn, link_type) — #422.
        self.url_rows: list[tuple[int, int, str, str | None, str | None, str | None]] = []
        # Each row: [owner_urn, owner_artefact_urn, position, rel, url, urn, link_type, hreflang] — #422.
        self.link_rows: list[list[object]] = []

    def add(self, owner_urn: str, annotations: Annotations | None, links: tuple[Link, ...] = ()) -> None:
        self.link_rows.extend(
            [owner_urn, self.artefact_urn, position, link.rel, link.url, link.urn, link.type, link.hreflang]
            for position, link in enumerate(links)
        )
        if not annotations:
            return
        for ordinal, ann in enumerate(annotations):
            local_idx = len(self.ann_rows)
            self.ann_rows.append([owner_urn, self.artefact_urn, ordinal, ann.id, ann.title, ann.type, ann.value])
            self.url_rows.extend(
                (local_idx, position, u.url, u.lang, u.urn, u.type) for position, u in enumerate(ann.urls)
            )
            if ann.text:
                for lang, text in ann.text._backing().items():
                    self.text_rows.append((local_idx, lang, text))

    def flush(self, conn: duckdb.DuckDBPyConnection) -> None:
        if self.link_rows:
            _execute_bulk_insert(
                conn,
                "link",
                ["owner_urn", "owner_artefact_urn", "position", "rel", "url", "urn", "link_type", "hreflang"],
                self.link_rows,
            )
            self.link_rows.clear()
        if not self.ann_rows:
            return
        n = len(self.ann_rows)
        id_rows = conn.execute("SELECT nextval('annotation_id_seq') FROM range(?)", [n]).fetchall()
        ids = [r[0] for r in id_rows]

        full_ann_rows = [[ids[i], *row] for i, row in enumerate(self.ann_rows)]
        _execute_bulk_insert(
            conn,
            "annotation",
            [
                "id",
                "owner_urn",
                "owner_artefact_urn",
                "ordinal",
                "annotation_id",
                "title",
                "annotation_type",
                "value",
            ],
            full_ann_rows,
        )

        if self.url_rows:
            url_rows = [[ids[idx], *rest] for idx, *rest in self.url_rows]
            _execute_bulk_insert(
                conn, "annotation_url", ["annotation_id", "position", "url", "lang", "link_urn", "link_type"], url_rows
            )

        if self.text_rows:
            text_rows = [[ids[idx], lang, text] for idx, lang, text in self.text_rows]
            _execute_bulk_insert(
                conn,
                "annotation_text",
                ["annotation_id", "lang", "text"],
                text_rows,
            )

        self.ann_rows.clear()
        self.text_rows.clear()
        self.url_rows.clear()


def _put_part_annotations(
    conn: duckdb.DuckDBPyConnection, urn: str, field: str, parts: Sequence[_AnnotatedPart]
) -> None:
    """Write the annotations of an artefact's unidentifiable parts, keyed by `part_owner` (#422).

    `parts` in the order the reader rebuilds them, so a part's index names it.
    """
    batch = _AnnotationBatch(urn)
    for index, part in enumerate(parts):
        batch.add(part_owner(urn, field, index), part.annotations)
    batch.flush(conn)


def _put_artefact_text_and_annotations(
    conn: duckdb.DuckDBPyConnection,
    urn: str,
    artefact: SupportsUrn,
    batch: _AnnotationBatch | None = None,
) -> None:
    """Write localized_text and annotations for an artefact.

    If ``batch`` is provided, annotations are queued on it (caller flushes);
    otherwise they are flushed immediately.
    """
    _put_artefact_localized_text(conn, urn, artefact)
    anns = getattr(artefact, "annotations", None)
    links: tuple[Link, ...] = getattr(artefact, "links", ())
    if batch is not None:
        batch.add(urn, anns, links)
    else:
        batch = _AnnotationBatch(urn)
        batch.add(urn, anns, links)
        batch.flush(conn)


# ── Reference graph ─────────────────────────────────────────────────────


def _put_refs(
    conn: duckdb.DuckDBPyConnection,
    refs: list[tuple[str, str, str, int | None]],
) -> None:
    """Insert artefact_ref rows.

    Treats ``(source_urn, target_urn, role)`` as set semantics: a DSD
    that references the same codelist via multiple dimensions yields a
    single dependency edge here. The per-dimension ordinal (position) is
    recoverable from the matching component table
    (``dsd_component.position`` / ``msd_component.position``), so we
    don't lose information by collapsing duplicates. ``ON CONFLICT DO
    NOTHING`` keeps the first ordinal we saw — arbitrary, but ordinal on
    this table is only a hint for callers that want positional iteration
    through the ref graph without a join.
    """
    if not refs:
        return
    # Arrow columnar bulk insert. The 0.35.0 executemany rewrite assumed
    # DuckDB would amortise the ON CONFLICT PK lookup across the batch;
    # the v0.35.1 instrumentation showed it doesn't — per-row cost on a
    # 295k-edge hierarchy was 11.5 ms (issue #146 / #140). The Arrow
    # path inserts the whole batch in one SELECT and never pays the
    # per-row index lookup.
    #
    # ON CONFLICT is dropped: within one ``_put_refs`` call, source_urn
    # is constant (one artefact's outgoing edges) and
    # ``_delete_artefact_cascade`` has already cleared this artefact's
    # prior rows, so no PK collision is possible across calls. Intra-
    # call duplicates (a DSD referencing the same Codelist via two
    # dimensions) are dedup'd here in Python, keeping the first ordinal
    # seen — same set-semantics behaviour the ON CONFLICT clause used to
    # give. The ordinal on this table is only a positional hint; the
    # canonical per-dimension position lives in ``dsd_component``.
    seen: dict[tuple[str, str, str], int | None] = {}
    for src, tgt, role, ordinal in refs:
        seen.setdefault((src, tgt, role), ordinal)
    rows: list[list[object]] = [[src, tgt, role, ordinal] for (src, tgt, role), ordinal in seen.items()]
    _execute_bulk_insert(
        conn,
        "artefact_ref",
        ["source_urn", "target_urn", "role", "ordinal"],
        rows,
    )


# ── Hierarchy precomputation ────────────────────────────────────────────


def _compute_hierarchy(
    items: list[tuple[str, str | None]],
) -> dict[str, tuple[int, list[str]]]:
    """Compute depth and ancestor_ids from (id, parent_id) pairs.

    Returns {id: (depth, ancestor_ids)} where ancestor_ids is root-first.
    """
    result: dict[str, tuple[int, list[str]]] = {}

    parent_map = dict(items)

    def walk(item_id: str) -> tuple[int, list[str]]:
        if item_id in result:
            return result[item_id]
        parent_id = parent_map.get(item_id)
        if parent_id is None:
            result[item_id] = (0, [])
        else:
            parent_depth, parent_ancestors = walk(parent_id)
            result[item_id] = (parent_depth + 1, [*parent_ancestors, parent_id])
        return result[item_id]

    for item_id, _ in items:
        walk(item_id)
    return result


# ── Item URN helpers ────────────────────────────────────────────────────


def _item_urn_prefix(artefact: SupportsUrn, artefact_urn: str, item_class: str) -> str:
    """The prefix every item URN of one artefact shares, ending in ``.``.

    `_make_item_urn` parses the owner's URN and builds an `SdmxUrn` *per item*,
    and every caller loops over one artefact's items — so both are constant
    across the loop and only `item_id` varies. Writing the largest hierarchy in
    the 2021 Census Profile bundle went from **499,143 parses to 1** (#139).

    **That count is the whole claim; end-to-end wall clock does not move
    measurably.** Interleaved A/B/A runs of the same write came out 6.60 s /
    6.48 s / 5.32 s — the two *identical* arms differ by more than the arms
    being compared, because DuckDB insert time dominates and the machine
    drifts. What is left is a reduction in work that is objective and
    assertable, which is why the guard is
    `tests/unit/test_storage_item_urns.py` counting the parses rather than
    a benchmark asserting a duration.

    Concatenating is faithful to the grammar rather than a second copy of it:
    `SdmxUrn.__str__` builds an item URN as exactly ``base + "." + item_id``,
    and the base here is made without an `item_id` so that is what it is.
    """
    parsed = SdmxUrn.parse(artefact_urn)
    base = SdmxUrn.make(
        type(artefact),
        package=parsed.package,
        artefact_class=item_class,
        agency=parsed.agency,
        id=parsed.id,
        version=parsed.version,
    )
    return f"{base}."


def _make_item_urn(
    artefact: SupportsUrn,
    artefact_urn: str,
    item_class: str,
    item_id: str,
) -> str:
    """Build a synthetic item URN.

    E.g. urn:sdmx:org.sdmx.infomodel.codelist.Code=SDMX:CL_FREQ(1.0).A
    """
    # Replace the artefact class in the URN with the item class and append item_id
    # urn:sdmx:org.sdmx.infomodel.codelist.Codelist=SDMX:CL_FREQ(1.0)
    # → urn:sdmx:org.sdmx.infomodel.codelist.Code=SDMX:CL_FREQ(1.0).A
    parsed = SdmxUrn.parse(artefact_urn)
    item_urn = SdmxUrn.make(
        type(artefact),
        package=parsed.package,
        artefact_class=item_class,
        agency=parsed.agency,
        id=parsed.id,
        version=parsed.version,
        item_id=item_id,
    )
    return str(item_urn)


def _split_item_urn(urn: str) -> tuple[str, str] | None:
    """Split an item URN into ``(owning maintainable URN, item id)``.

    The inverse of `_make_item_urn`, and deliberately beside it: both encode
    the same grammar, and splitting it across modules is how a downstream
    consumer ends up doing it a third way in SQL (#372).

    **Which class owns an item is not a table here.** It was — ten rows keyed
    by item class, and the third copy of one fact: `validate_references`
    carried another shape of it and expanded exactly one class for four
    milestones (#404), and the readers indexed exactly one (#405). All three
    now read `model._items.owner_classes`, which inverts the
    `sdmx_package` / `sdmx_class` pair the item classes declare, so a new
    item-bearing scheme is a declaration on the item rather than a row in each
    place that needs to know.

    That widens what gets split, and the widening is safe by construction
    rather than by inspection. The one consumer joins these columns to
    ``dsd_component(dsd_urn, component_id)``, and an owner URN that is not a
    `DataStructure` cannot equal a ``dsd_urn`` — different class in the URN —
    so an owner this now places where the old map declined is inert for that
    join and informative for anything reading the column. The old docstring's
    worry, a wrong owner silently joining an association to the wrong
    component, is about a *wrong* mapping and not about a complete one.

    Args:
        urn: A URN string, item-level or not.

    Returns:
        The pair, or None when `urn` does not parse, names a maintainable
        rather than an item, or names an item class no scheme in the model
        holds.
    """
    try:
        parsed = SdmxUrn.parse(urn)
    except SdmxParseError:
        return None
    if parsed.item_id is None:
        return None
    owners = owner_classes(parsed.artefact_class)
    if not owners:
        return None
    # One owner for every item class the model has today. More than one would
    # need a lookup to tell them apart rather than a guess, and the first is
    # what a guess would be — so this stays correct while that holds and is
    # `tests/unit/test_item_resolution.py`'s subject if it stops.
    return str(scheme_urn_of(parsed, owners[0])), parsed.item_id


# ── Delete helpers ──────────────────────────────────────────────────────


#: Artefact type -> the DELETEs that clear *its* child and extension rows,
#: in the order they must run. Full statements rather than (table, column)
#: pairs because two of them are subqueries and one artefact needs three
#: statements ordered against each other; a pair table could not say that.
#:
#: **Ordering inside a tuple is load-bearing.** `organisation_contact` rows
#: are reached *through* their owner, so deleting agencies first leaves the
#: subquery empty and the contacts orphaned — they then accumulate one
#: duplicate set per re-add, silently (#309).
#:
#: A type absent here has no child tables; it still gets the universal
#: statements below. `test_the_cascade_covers_every_table_each_writer_writes`
#: derives the truth by writing each kind and watching which tables gain rows,
#: so this map cannot quietly fall behind the writers — which is exactly how
#: #395's three new map kinds shipped uncascaded and raised a
#: `ConstraintException` on re-ingest.
_CASCADE: Final[dict[str, tuple[str, ...]]] = {
    "Codelist": (
        "DELETE FROM code WHERE codelist_urn = ?",
        "DELETE FROM code_selection_member WHERE codelist_urn = ?",
        "DELETE FROM codelist_extension WHERE codelist_urn = ?",
        "DELETE FROM geo_codelist WHERE codelist_urn = ?",
    ),
    "ValueList": ("DELETE FROM value_item WHERE value_list_urn = ?",),
    "ConceptScheme": ("DELETE FROM concept WHERE scheme_urn = ?",),
    "CategoryScheme": ("DELETE FROM category WHERE scheme_urn = ?",),
    "Categorisation": ("DELETE FROM categorisation WHERE urn = ?",),
    "Hierarchy": (
        "DELETE FROM hierarchy_node WHERE hierarchy_urn = ?",
        "DELETE FROM hierarchy_level WHERE hierarchy_urn = ?",
        "DELETE FROM hierarchy_meta WHERE urn = ?",
    ),
    "HierarchyAssociation": ("DELETE FROM hierarchy_association WHERE urn = ?",),
    "AgencyScheme": (
        "DELETE FROM organisation_contact WHERE owner_urn IN (SELECT agency_urn FROM agency WHERE scheme_urn = ?)",
        "DELETE FROM agency WHERE scheme_urn = ?",
    ),
    "DataProviderScheme": (
        (
            "DELETE FROM organisation_contact "
            "WHERE owner_urn IN (SELECT provider_urn FROM data_provider WHERE scheme_urn = ?)"
        ),
        "DELETE FROM data_provider WHERE scheme_urn = ?",
    ),
    "MetadataProviderScheme": (
        (
            "DELETE FROM organisation_contact "
            "WHERE owner_urn IN (SELECT provider_urn FROM metadata_provider WHERE scheme_urn = ?)"
        ),
        "DELETE FROM metadata_provider WHERE scheme_urn = ?",
    ),
    "DataConsumerScheme": (
        (
            "DELETE FROM organisation_contact "
            "WHERE owner_urn IN (SELECT consumer_urn FROM data_consumer WHERE scheme_urn = ?)"
        ),
        "DELETE FROM data_consumer WHERE scheme_urn = ?",
    ),
    "OrganisationUnitScheme": (
        (
            "DELETE FROM organisation_contact "
            "WHERE owner_urn IN (SELECT unit_urn FROM organisation_unit WHERE scheme_urn = ?)"
        ),
        "DELETE FROM organisation_unit WHERE scheme_urn = ?",
    ),
    "DataStructure": (
        "DELETE FROM dsd_component WHERE dsd_urn = ?",
        "DELETE FROM dsd_metadata_attribute WHERE dsd_urn = ?",
        "DELETE FROM dsd_group WHERE dsd_urn = ?",
        "DELETE FROM group_dimension WHERE dsd_urn = ?",
        "DELETE FROM data_structure WHERE urn = ?",
    ),
    "Dataflow": ("DELETE FROM dataflow WHERE urn = ?",),
    "ProvisionAgreement": ("DELETE FROM provision_agreement WHERE urn = ?",),
    # Both metadata kinds below write declared targets too; without their
    # delete, a second add collided on the table's primary key (#428).
    "MetadataProvisionAgreement": (
        "DELETE FROM metadata_declared_target WHERE source_urn = ?",
        "DELETE FROM metadata_provision_agreement WHERE urn = ?",
    ),
    "DataConstraint": (
        "DELETE FROM region_key_value WHERE constraint_urn = ?",
        "DELETE FROM region_selection WHERE constraint_urn = ?",
        "DELETE FROM data_key_value WHERE constraint_urn = ?",
        "DELETE FROM data_key_component WHERE constraint_urn = ?",
        "DELETE FROM data_key_component_value WHERE constraint_urn = ?",
        "DELETE FROM data_key WHERE constraint_urn = ?",
        "DELETE FROM data_key_set WHERE constraint_urn = ?",
        "DELETE FROM cube_region WHERE constraint_urn = ?",
        "DELETE FROM data_constraint_dataflow WHERE constraint_urn = ?",
        "DELETE FROM data_constraint_dsd WHERE constraint_urn = ?",
        "DELETE FROM data_constraint_provision WHERE constraint_urn = ?",
        "DELETE FROM data_constraint_data_set WHERE constraint_urn = ?",
        "DELETE FROM data_constraint_queryable_source WHERE constraint_urn = ?",
        "DELETE FROM data_constraint WHERE urn = ?",
    ),
    "RepresentationMap": (
        "DELETE FROM representation_map_source WHERE representation_map_urn = ?",
        "DELETE FROM representation_map_target WHERE representation_map_urn = ?",
        "DELETE FROM representation_mapping WHERE representation_map_urn = ?",
        "DELETE FROM representation_map WHERE urn = ?",
    ),
    "ConceptSchemeMap": (
        "DELETE FROM item_map WHERE map_urn = ?",
        "DELETE FROM concept_scheme_map WHERE urn = ?",
    ),
    "CategorySchemeMap": (
        "DELETE FROM item_map WHERE map_urn = ?",
        "DELETE FROM category_scheme_map WHERE urn = ?",
    ),
    "OrganisationSchemeMap": (
        "DELETE FROM item_map WHERE map_urn = ?",
        "DELETE FROM organisation_scheme_map WHERE urn = ?",
    ),
    "StructureMap": (
        "DELETE FROM component_map WHERE structure_map_urn = ?",
        "DELETE FROM epoch_map WHERE structure_map_urn = ?",
        "DELETE FROM date_pattern_map WHERE structure_map_urn = ?",
        "DELETE FROM frequency_format_mapping WHERE structure_map_urn = ?",
        "DELETE FROM fixed_value_map WHERE structure_map_urn = ?",
        "DELETE FROM structure_map WHERE urn = ?",
    ),
    "MetadataStructure": (
        "DELETE FROM metadata_attribute WHERE msd_urn = ?",
        "DELETE FROM metadata_structure WHERE urn = ?",
    ),
    "Metadataflow": (
        "DELETE FROM metadata_declared_target WHERE source_urn = ?",
        "DELETE FROM metadataflow WHERE urn = ?",
    ),
    "MetadataSet": (
        "DELETE FROM metadata_value WHERE set_urn = ?",
        "DELETE FROM metadata_target WHERE set_urn = ?",
        "DELETE FROM metadata_declared_target WHERE source_urn = ?",
        "DELETE FROM metadata_set WHERE urn = ?",
    ),
    "DataflowBinding": ("DELETE FROM dataflow_binding WHERE urn = ?",),
}


def put_version_rewrite(conn: duckdb.DuckDBPyConnection, urn: str, original_version: str) -> None:
    """Record the version `urn` was published at, before `add_all` rewrote it (#420)."""
    conn.execute("INSERT OR REPLACE INTO version_rewrite VALUES (?, ?)", [urn, original_version])


def _delete_artefact_cascade(conn: duckdb.DuckDBPyConnection, urn: str, artefact_type: str) -> None:
    """Delete all rows associated with an artefact URN, for *its* type.

    **Returns immediately when the artefact is not stored**, which on a fresh
    materialisation is every artefact (#317).

    **Dispatched on the type since #144.** It used to run every statement in
    `_CASCADE` for every artefact — 40 `DELETE`s, of which roughly 34 were
    guaranteed no-ops, because writing a Dataflow cannot have left rows in
    `code`, `hierarchy_node` or thirty others. Measured on 627 MetadataSets
    from the 2021 Census Profile bundle: re-ingest ran 25,080 `DELETE`s and took
    35.6 s against 10.1 s for the same artefacts written fresh.

    Two thirds of that cost is not the `DELETE`s themselves. Each binds `urn`
    as a parameter, and DuckDB probes every parameter *value* for a pandas
    null with a lazy `import pandas` — absent here, uncached by Python, so
    each probe re-walks `sys.path`. Fewer statements is fewer probes.

    Dispatching on the *written* type is sound because a URN carries its
    artefact class, so a stored row and the artefact replacing it are
    necessarily the same kind — there is no URN two classes can both claim.

    **The precondition is an invariant, not an optimism.** Every writer runs
    this cascade *before* `_put_spine`, so a URN reaches the spine only after
    its old rows are gone, and child rows are written only after the spine row
    exists. An interrupted write therefore leaves either nothing or a spine
    row that a later write will find and cascade properly. There is no
    ordering that leaves child rows behind an absent spine row, and
    `test_the_cascade_precondition_holds_for_every_writer` asserts the
    ordering rather than trusting this paragraph.
    """
    if first(conn.execute("SELECT 1 FROM artefact WHERE urn = ? LIMIT 1", [urn])) is None:
        return
    p = [urn]
    for statement in _CASCADE.get(artefact_type, ()):
        conn.execute(statement, p)

    # Universal: every artefact has text, annotations and outgoing refs.
    #
    # All three are indexed equalities since #145. `localized_text` holds only
    # maintainables' labels — an item's live inline in a MAP column, which is
    # what `SideLabel` documents on the read side — so its owner *is* the
    # artefact URN and the `OR owner_urn LIKE` half it used to carry matched
    # nothing while costing a full scan. `annotation` genuinely holds item
    # URNs (585,663 of 585,675 rows in a real bundle), so it carries the
    # artefact it belongs to as its own column instead.
    conn.execute("DELETE FROM localized_text WHERE owner_urn = ?", p)
    conn.execute("DELETE FROM version_rewrite WHERE urn = ?", p)
    for side_table in ("annotation_text", "annotation_url"):
        # Identifier-only interpolation, from the literal tuple above.
        conn.execute(
            f"""
            DELETE FROM {side_table}
            WHERE annotation_id IN (
                SELECT id FROM annotation WHERE owner_artefact_urn = ?
            )
            """,
            p,
        )
    conn.execute("DELETE FROM annotation WHERE owner_artefact_urn = ?", p)
    conn.execute("DELETE FROM link WHERE owner_artefact_urn = ?", p)
    conn.execute("DELETE FROM artefact_ref WHERE source_urn = ?", p)


# ── Public writers ──────────────────────────────────────────────────────


def put_codelist(conn: duckdb.DuckDBPyConnection, cl: Codelist, *, source_registry: str | None = None) -> None:
    """Write a Codelist to artefact + localized_text + code tables."""
    urn = str(artefact_urn(cl))

    _delete_artefact_cascade(conn, urn, "Codelist")
    _put_spine(conn, urn, "Codelist", cl, source_registry=source_registry)
    ann_batch = _AnnotationBatch(urn)
    _put_artefact_text_and_annotations(conn, urn, cl, ann_batch)

    refs: list[tuple[str, str, str, int | None]] = []

    if cl.codes:
        # Compute hierarchy
        hierarchy = _compute_hierarchy([(c.id, c.parent_id) for c in cl.codes])

        all_rows: list[list[object]] = []
        code_prefix = _item_urn_prefix(cl, urn, "Code")
        for position, code in enumerate(cl.codes):
            depth, ancestors = hierarchy.get(code.id, (0, []))
            code_urn = code_prefix + code.id
            row: list[object] = [
                code_urn,
                urn,
                code.id,
                code.parent_id,
                position,
                _istring_to_map(code.name),
                _istring_to_map(code.description),
                depth,
                ancestors,
                code.uri,
                code.value if isinstance(code, GeoFeatureSetCode) else None,
                code.geo_cell if isinstance(code, GeoGridCode) else None,
            ]
            all_rows.append(row)
            ann_batch.add(code_urn, code.annotations, code.links)

        _execute_bulk_insert(
            conn,
            "code",
            [
                "code_urn",
                "codelist_urn",
                "code_id",
                "parent_id",
                "position",
                "name",
                "description",
                "depth",
                "ancestor_ids",
                "uri",
                "geo_value",
                "geo_cell",
            ],
            all_rows,
        )

    if isinstance(cl, GeographicCodelist | GeoGridCodelist):
        conn.execute(
            "INSERT INTO geo_codelist (codelist_urn, geo_type, grid_definition) VALUES (?, ?, ?)",
            [urn, type(cl).__name__, cl.grid_definition if isinstance(cl, GeoGridCodelist) else None],
        )
    _put_codelist_extensions(conn, urn, cl, refs)

    ann_batch.flush(conn)
    _put_refs(conn, refs)


def put_value_list(conn: duckdb.DuckDBPyConnection, vl: ValueList, *, source_registry: str | None = None) -> None:
    """Write a ValueList to artefact + localized_text + value_item (#422)."""
    urn = str(artefact_urn(vl))
    _delete_artefact_cascade(conn, urn, "ValueList")
    _put_spine(conn, urn, "ValueList", vl, source_registry=source_registry)
    ann_batch = _AnnotationBatch(urn)
    _put_artefact_text_and_annotations(conn, urn, vl, ann_batch)
    owner_prefix = _item_urn_prefix(vl, urn, "ValueItem")
    rows: list[list[object]] = []
    for position, item in enumerate(vl.items):
        rows.append([urn, position, item.id, _istring_to_map(item.name), _istring_to_map(item.description)])
        ann_batch.add(owner_prefix + item.id, item.annotations)
    if rows:
        _execute_bulk_insert(conn, "value_item", ["value_list_urn", "position", "item_id", "name", "description"], rows)
    ann_batch.flush(conn)


def _put_codelist_extensions(
    conn: duckdb.DuckDBPyConnection,
    urn: str,
    cl: Codelist,
    refs: list[tuple[str, str, str, int | None]],
) -> None:
    """Write a Codelist's SDMX 3.0 extensions and their code selections.

    An extension is also an outgoing edge, so the extended codelist goes into
    `artefact_ref` under the `extends` role as well — otherwise a composed
    codelist would be invisible to the inbound-reference index, and "what
    depends on this codelist" would answer without its extenders (#160).
    """
    if not cl.extensions:
        return

    extension_rows: list[list[object]] = []
    member_rows: list[list[object]] = []
    for position, extension in enumerate(cl.extensions):
        extends_urn = _ref_urn(extension.codelist, owner="CodelistExtension", field="codelist")
        if extends_urn is None:
            continue
        selection = extension.selection
        extension_rows.append(
            [urn, position, extends_urn, extension.prefix, selection.mode.value if selection else None]
        )
        refs.append((urn, extends_urn, "extends", position))
        if selection is None:
            continue
        member_rows.extend(
            [urn, position, member_idx, member.value, member.cascade.value if member.cascade else None]
            for member_idx, member in enumerate(selection.members)
        )

    _execute_bulk_insert(
        conn,
        "codelist_extension",
        ["codelist_urn", "position", "extends_urn", "prefix", "selection_mode"],
        extension_rows,
    )
    if member_rows:
        _execute_bulk_insert(
            conn,
            "code_selection_member",
            ["codelist_urn", "position", "member_idx", "value", "cascade"],
            member_rows,
        )


def _ref_urn(value: object, *, owner: str, field: str) -> str | None:
    """The urn a `Ref` carries, `None` for absent — and a refusal otherwise.

    The writers used to spell this `str(x.urn) if isinstance(x, Ref) else None`,
    which reads as "no reference" and means "no reference *or* something I did
    not recognise". `Dimension.concept` is declared `Ref[Concept] | None`, and
    attrs accepts a bare `Concept` at runtime, so passing the item itself wrote
    `concept_urn = NULL`: the artefact validated, round-tripped, and had simply
    lost the edge. Concept-driven discovery then answered `[]` against a
    catalogue that plainly used the concept (#279).

    Both checkers catch it in typed code. A notebook, a JSON-driven builder or
    any untyped construction path is not checked, and that is where it bites.

    Note which fields need this and which do not: a converter can wrap a bare
    **maintainable**, whose urn is self-contained, and `representation` does
    exactly that with a `Codelist`. It cannot wrap a bare **item** — a
    concept or a code — because the urn needs the parent scheme the field does
    not carry. So the item-valued fields are the ones that reached the writer
    holding something it could not turn into a urn.

    Raises:
        TypeError: If `value` is neither a `Ref` nor `None`. The message names
            how to build the reference, because the reason a caller passes the
            item is usually that they have it and the `Ref` looks like
            ceremony — and for an *item* it is not obvious, since the urn needs
            the parent scheme the component does not carry.
    """
    if value is None:
        return None
    if isinstance(value, Ref):
        return str(value.urn)
    msg = (
        f"{owner}.{field} must be a Ref or None, got {type(value).__name__}. "
        f"Writing it would store NULL and lose the reference silently. "
        f"Build one with Ref[T].unresolved(urn) — an item's urn needs its "
        f"parent scheme, e.g. SdmxUrn(package=..., artefact_class='Concept', "
        f"agency=..., id=<scheme id>, version=..., item_id=<item id>)."
    )
    raise TypeError(msg)


def _representation_columns(rep: object) -> list[object]:
    """A representation as its storage columns, in `FACET_COLUMN_NAMES` order.

    `schema._FACET_COLUMNS` made the *DDL* one definition, after the three
    tables carrying it had already drifted twice. This is the writer half of
    the same argument: the expansion was written out three times — for
    `dsd_component`, `concept` and `metadata_attribute` — and each copy was
    thirteen chances to reorder a value or omit one silently, since every
    column is nullable and a misplaced `None` reads exactly like an absent
    facet.

    `hierarchy_level` is the fourth caller and the reason this was extracted
    now (#160): a `Level.coding_format` is a plain `Facet`, so it takes the
    facet arm and leaves `codelist_urn` null.

    Args:
        rep: A `Ref[Codelist]`, a `Facet`, or `None`.

    Returns:
        Values positionally matching `FACET_COLUMN_NAMES`. All-`None`
        for `None`, which is how an absent representation stores.
    """
    if isinstance(rep, Ref):
        return ["enumeration", str(rep.urn), *_facet_values(None)]
    if isinstance(rep, Facet):
        return ["facet", None, *_facet_values(rep)]
    return [None] * len(FACET_COLUMN_NAMES)


def _facet_values(facet: Facet | None) -> list[object]:
    """A facet's columns, after `repr_kind` and `codelist_urn`.

    Also the `enum_*` group's values, which are the same eleven fields of a
    component's `enumeration_format` (#438).
    """
    if facet is None:
        return [None] * (len(FACET_COLUMN_NAMES) - 2)
    return [
        str(facet.type) if facet.type is not None else None,
        facet.min_length,
        facet.max_length,
        facet.min_value,
        facet.max_value,
        facet.decimals,
        facet.pattern,
        facet.is_sequence,
        facet.start_value,
        facet.end_value,
        facet.is_multilingual,
        facet.interval,
        facet.time_interval,
        facet.start_time,
        facet.end_time,
    ]


def _iso_values(iso: ISOConceptReference | None) -> list[str | None]:
    """A concept's ISO concept reference as its three columns (#422)."""
    if iso is None:
        return [None, None, None]
    return [iso.agency, iso.concept_scheme_id, iso.concept_id]


def _serialize_component(
    dsd_urn: str,
    position: int,
    comp_type: str,
    comp: Component,
    refs: list[tuple[str, str, str, int | None]],
) -> list[object]:
    """Serialize one DSD component to a row + collect refs."""
    concept_ref = getattr(comp, "concept", None)
    concept_urn_str = _ref_urn(concept_ref, owner=comp_type, field="concept")

    rep = getattr(comp, "representation", None)
    # `MeasureDimension` has no `enumeration_format` (see `_check_enumeration_format`).
    enumeration_format: Facet | None = getattr(comp, "enumeration_format", None)
    facet_columns = _representation_columns(rep)

    if isinstance(rep, Ref):
        refs.append((dsd_urn, str(rep.urn), "enumeration", position))
    elif not isinstance(rep, Facet) and rep is not None:
        # A backstop rather than the bug. `representation` has a converter
        # that wraps a bare `Codelist`, because a maintainable's urn is
        # self-contained — which is exactly why `concept` and
        # `HierarchicalCode.code` do *not*: an item's urn needs the parent
        # scheme the field does not carry, so their converters pass an item
        # through untouched and the writer met it. This arm catches whatever
        # is neither.
        _ref_urn(rep, owner=comp_type, field="representation")

    if concept_urn_str:
        refs.append((dsd_urn, concept_urn_str, "concept_identity", position))

    usage = getattr(comp, "usage", None)
    usage_status = usage.value if usage is not None else None
    attachment = getattr(comp, "attachment", None)
    attachment_str = attachment.value if attachment is not None else None

    # A measure's usage has its own column, and an attribute's `usage_status`
    # (#422): the column was declared and written NULL until a measure had one.
    measure_usage = None
    if comp_type == "Measure":
        measure_usage, usage_status = usage_status, None

    concept_roles = getattr(comp, "concept_role", ()) or ()
    related_dimensions = getattr(comp, "related_dimensions", None)
    related_group = getattr(comp, "related_group", None)

    return [
        dsd_urn,
        comp.id,
        comp_type,
        position,
        concept_urn_str,
        *facet_columns,
        *_facet_values(enumeration_format),
        usage_status,
        attachment_str,
        related_group,
        list(related_dimensions) if related_dimensions else None,
        measure_usage,
        list(concept_roles) if concept_roles else None,
        comp.uri,
        sorted(optional) if (optional := getattr(comp, "optional_dimensions", frozenset())) else None,
        list(related) if (related := getattr(comp, "related_measures", None)) is not None else None,
        getattr(comp, "min_occurs", 1),
        getattr(comp, "max_occurs", 1),
        _sentinel_rows(rep),
        list(groups) if (groups := getattr(comp, "attachment_groups", ())) else None,
    ]


def _sentinel_rows(rep: object) -> list[dict[str, object]] | None:
    """A text format's sentinel values as the ``sentinel_values`` column (#422); NULL for none."""
    if not isinstance(rep, Facet) or not rep.sentinel_values:
        return None
    return [
        {"value": s.value, "name": _istring_to_map(s.name), "description": _istring_to_map(s.description)}
        for s in rep.sentinel_values
    ]


_DSD_COMPONENT_COLS = [
    "dsd_urn",
    "component_id",
    "component_type",
    "position",
    "concept_urn",
    *FACET_COLUMN_NAMES,
    *ENUMERATION_FORMAT_COLUMN_NAMES,
    "usage_status",
    "attachment_level",
    "attachment_group",
    "related_dimensions",
    "measure_usage",
    "concept_roles",
    "uri",
    "optional_dimensions",
    "related_measures",
    "min_occurs",
    "max_occurs",
    "sentinel_values",
    "attachment_groups",
]


_DSD_METADATA_ATTRIBUTE_COLS = (
    "dsd_urn",
    "attribute_id",
    "position",
    "attachment_level",
    "attachment_group",
    "related_dimensions",
    "optional_dimensions",
    "uri",
)


def _add_descriptor_annotations(batch: _AnnotationBatch, dsd: DataStructure, urn: str) -> None:
    """Queue the annotations of a DSD's component lists and groups.

    The lists are not modelled, but are identifiable: each one's annotations
    are keyed by its own URN, whose id is its class (#422). A group's by its
    `GroupDimensionDescriptor` URN.
    """
    for descriptor, annotations, links in (
        ("DimensionDescriptor", dsd.dimension_descriptor_annotations, dsd.dimension_descriptor_links),
        ("AttributeDescriptor", dsd.attribute_descriptor_annotations, dsd.attribute_descriptor_links),
        ("MeasureDescriptor", dsd.measure_descriptor_annotations, dsd.measure_descriptor_links),
    ):
        batch.add(_make_item_urn(dsd, urn, descriptor, descriptor), annotations, links)
    for group in dsd.groups or ():
        batch.add(_make_item_urn(dsd, urn, Group.sdmx_class, group.id), group.annotations)


def put_data_structure(
    conn: duckdb.DuckDBPyConnection, dsd: DataStructure, *, source_registry: str | None = None
) -> None:
    """Write a DataStructure to artefact + dsd_component + groups."""
    urn = str(artefact_urn(dsd))

    _delete_artefact_cascade(conn, urn, "DataStructure")
    _put_spine(conn, urn, "DataStructure", dsd, source_registry=source_registry)
    ann_batch = _AnnotationBatch(urn)
    _put_artefact_text_and_annotations(conn, urn, dsd, ann_batch)
    conn.execute(
        """
        INSERT INTO data_structure
            (urn, dimension_descriptor_uri, attribute_descriptor_uri, measure_descriptor_uri, evolving_structure)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            urn,
            dsd.dimension_descriptor_uri,
            dsd.attribute_descriptor_uri,
            dsd.measure_descriptor_uri,
            dsd.evolving_structure,
        ],
    )

    components: list[tuple[str, Component]] = [("Dimension", dim) for dim in dsd.dimensions]
    if dsd.time_dimension is not None:
        components.append(("TimeDimension", dsd.time_dimension))
    if hasattr(dsd, "measure_dimension") and dsd.measure_dimension is not None:
        components.append(("MeasureDimension", dsd.measure_dimension))
    components.extend(("Attribute", attr) for attr in dsd.attributes)
    components.extend(("Measure", m) for m in dsd.measures)

    refs: list[tuple[str, str, str, int | None]] = []
    comp_rows: list[list[object]] = []
    for pos, (comp_type, comp) in enumerate(components):
        comp_rows.append(_serialize_component(urn, pos, comp_type, comp, refs))
        # Component owner URN follows the same item-URN convention as Codes
        # and Concepts (`<dsd_urn-with-component-class>.<component_id>`).
        comp_urn = _make_item_urn(dsd, urn, comp_type, comp.id)
        ann_batch.add(comp_urn, getattr(comp, "annotations", None), getattr(comp, "links", ()))

    if comp_rows:
        _execute_bulk_insert(conn, "dsd_component", _DSD_COMPONENT_COLS, comp_rows)
    usage_rows: list[Sequence[object]] = []
    for pos, usage in enumerate(dsd.metadata_attributes):
        usage_rows.append(
            [
                urn,
                usage.id,
                pos,
                usage.attachment.value if usage.attachment is not None else None,
                usage.related_group,
                list(usage.related_dimensions) if usage.related_dimensions else None,
                sorted(usage.optional_dimensions) or None,
                usage.uri,
            ]
        )
        # No URN of its own (the schema prohibits its id): its annotations
        # are keyed by one shaped like a component's, under its own class.
        ann_batch.add(_make_item_urn(dsd, urn, "MetadataAttributeUsage", usage.id), usage.annotations, usage.links)
    if usage_rows:
        _execute_bulk_insert(conn, "dsd_metadata_attribute", _DSD_METADATA_ATTRIBUTE_COLS, usage_rows)
    if dsd.metadata is not None:
        refs.append((urn, str(dsd.metadata.urn), "metadata", None))
    _add_descriptor_annotations(ann_batch, dsd, urn)
    ann_batch.flush(conn)

    if dsd.groups:
        # Bulk rather than a statement per group and per member. Beyond the
        # round trips, DuckDB probes every bound *value* for a pandas null via
        # a lazy `import pandas` — absent here and uncached by Python, so each
        # probe re-walks `sys.path`. This nested loop was 537 statements and
        # 3,092 such probes on ECB's 81 DSDs, about half of what remained
        # after #317's cascade skip.
        group_rows: list[Sequence[object]] = [
            [urn, group.id, group.uri, _ref_urn_or_none(group.attachment_constraint)] for group in dsd.groups
        ]
        member_rows: list[Sequence[object]] = [
            [urn, group.id, dim_id] for group in dsd.groups for dim_id in group.dimension_ids
        ]
        refs.extend(
            (urn, str(group.attachment_constraint.urn), "group_attachment_constraint", None)
            for group in dsd.groups
            if group.attachment_constraint is not None
        )
        _execute_bulk_insert(conn, "dsd_group", ("dsd_urn", "group_id", "uri", "attachment_constraint_urn"), group_rows)
        _execute_bulk_insert(conn, "group_dimension", ("dsd_urn", "group_id", "dimension_id"), member_rows)

    _put_refs(conn, refs)


def put_dataflow(conn: duckdb.DuckDBPyConnection, flow: Dataflow, *, source_registry: str | None = None) -> None:
    """Write a Dataflow to artefact + dataflow extension."""
    urn = str(artefact_urn(flow))

    _delete_artefact_cascade(conn, urn, "Dataflow")
    _put_spine(conn, urn, "Dataflow", flow, source_registry=source_registry)
    _put_artefact_text_and_annotations(conn, urn, flow)

    dsd_urn = str(flow.structure.urn) if flow.structure is not None else None
    conn.execute(
        "INSERT INTO dataflow (urn, dsd_urn, dimension_constraint) VALUES (?, ?, ?)",
        [urn, dsd_urn, list(flow.dimension_constraint)],
    )

    refs: list[tuple[str, str, str, int | None]] = []
    if dsd_urn is not None:
        refs.append((urn, dsd_urn, "structure", None))
    _put_refs(conn, refs)


def put_concept_scheme(
    conn: duckdb.DuckDBPyConnection, cs: ConceptScheme, *, source_registry: str | None = None
) -> None:
    """Write a ConceptScheme to artefact + concept table."""
    urn = str(artefact_urn(cs))

    _delete_artefact_cascade(conn, urn, "ConceptScheme")
    _put_spine(conn, urn, "ConceptScheme", cs, source_registry=source_registry)
    ann_batch = _AnnotationBatch(urn)
    _put_artefact_text_and_annotations(conn, urn, cs, ann_batch)

    refs: list[tuple[str, str, str, int | None]] = []

    if cs.concepts:
        # Concepts nest within a scheme via `<str:Parent>` (#307), so ancestry
        # is baked here exactly as it is for codes and categories — one walk at
        # write time, and `ancestors` / `descendants` become array containment
        # instead of a recursive CTE.
        hierarchy = _compute_hierarchy([(c.id, c.parent_id) for c in cs.concepts])
        all_rows: list[list[object]] = []
        concept_prefix = _item_urn_prefix(cs, urn, "Concept")
        for position, concept in enumerate(cs.concepts):
            concept_urn_str = concept_prefix + concept.id
            depth, ancestors = hierarchy.get(concept.id, (0, []))

            all_rows.append(
                [
                    concept_urn_str,
                    urn,
                    concept.id,
                    concept.parent_id,
                    position,
                    _istring_to_map(concept.name),
                    _istring_to_map(concept.description),
                    depth,
                    ancestors,
                    concept.uri,
                    *_representation_columns(concept.core_representation),
                    _sentinel_rows(concept.core_representation),
                    *_facet_values(concept.enumeration_format),
                    *_iso_values(concept.iso_concept_reference),
                    concept.min_occurs,
                    concept.max_occurs,
                ]
            )
            ann_batch.add(concept_urn_str, concept.annotations, concept.links)
            # The scheme references the codelist its concept is represented by,
            # as a DSD references its components' (GH-152): the fingerprint
            # closure and the version-rewrite check read `artefact_ref`.
            if isinstance(concept.core_representation, Ref):
                refs.append((urn, str(concept.core_representation.urn), "core_representation", position))

        _execute_bulk_insert(
            conn,
            "concept",
            [
                "concept_urn",
                "scheme_urn",
                "concept_id",
                "parent_id",
                "position",
                "name",
                "description",
                "depth",
                "ancestor_ids",
                "uri",
                *FACET_COLUMN_NAMES,
                "sentinel_values",
                *ENUMERATION_FORMAT_COLUMN_NAMES,
                "iso_agency",
                "iso_concept_scheme_id",
                "iso_concept_id",
                "min_occurs",
                "max_occurs",
            ],
            all_rows,
        )

    ann_batch.flush(conn)
    _put_refs(conn, refs)


def put_hierarchy(conn: duckdb.DuckDBPyConnection, h: Hierarchy, *, source_registry: str | None = None) -> None:
    """Write a Hierarchy to artefact + hierarchy_meta + hierarchy_node."""
    urn = str(artefact_urn(h))

    _delete_artefact_cascade(conn, urn, "Hierarchy")
    _put_spine(conn, urn, "Hierarchy", h, source_registry=source_registry)
    ann_batch = _AnnotationBatch(urn)
    _put_artefact_text_and_annotations(conn, urn, h, ann_batch)

    conn.execute(
        "INSERT INTO hierarchy_meta (urn, has_formal_levels) VALUES (?, ?)",
        [urn, h.has_formal_levels],
    )

    # SDMX 3.1: persist Level definitions
    if h.levels:
        level_rows: list[list[object]] = [
            [
                urn,
                lv.id,
                idx,
                _istring_to_map(lv.name),
                _istring_to_map(lv.description),
                *_representation_columns(lv.coding_format),
                lv.uri,
            ]
            for idx, lv in enumerate(h.levels)
        ]
        _execute_bulk_insert(
            conn,
            "hierarchy_level",
            ["hierarchy_urn", "level_id", "level_idx", "name", "description", *FACET_COLUMN_NAMES, "uri"],
            level_rows,
        )
        # A Level is identifiable, so its annotations hang off an item URN of
        # its own class like every other item's — `level_urn` is the one
        # spelling, shared with `resolve.read_levels` (#442).
        for lv in h.levels:
            ann_batch.add(level_urn(urn, lv.id), lv.annotations, lv.links)

    # One row per node, keyed by path: a node id may repeat in another branch
    # (#439), so the id names no single row and the URN carries the path.
    rows = list(tree_rows(h))
    if rows:
        refs: list[tuple[str, str, str, int | None]] = []
        all_rows: list[list[object]] = []

        node_prefix = _item_urn_prefix(h, urn, "HierarchicalCode")
        for position, row in enumerate(rows):
            node = row.node
            node_urn = node_prefix + row.path
            code_urn = _ref_urn(node.code, owner="HierarchicalCode", field="code")
            level_id = node.level.id if node.level is not None else None

            all_rows.append(
                [
                    node_urn,
                    urn,
                    node.id,
                    row.path,
                    row.parent_path,
                    position,
                    code_urn,
                    level_id,
                    to_storage(node.valid_from),
                    to_storage(node.valid_to),
                    row.depth,
                    row.ancestor_ids,
                    row.ancestor_paths,
                    node.uri,
                    node.version,
                ]
            )

            if code_urn:
                refs.append((urn, code_urn, "code", position))

            ann_batch.add(node_urn, node.annotations, node.links)

        _execute_bulk_insert(
            conn,
            "hierarchy_node",
            [
                "node_urn",
                "hierarchy_urn",
                "node_id",
                "path",
                "parent_path",
                "position",
                "code_urn",
                "level_id",
                "valid_from",
                "valid_to",
                "depth",
                "ancestor_ids",
                "ancestor_paths",
                "uri",
                "version",
            ],
            all_rows,
        )
        _put_refs(conn, refs)

    ann_batch.flush(conn)


class _ValueSetTables(NamedTuple):
    """Where one kind of owner keeps its value sets: a cube region's, or a data key's (#422)."""

    owner_column: str
    selection_table: str
    value_table: str


_REGION_SETS = _ValueSetTables("region_idx", "region_selection", "region_key_value")
_KEY_SETS = _ValueSetTables("key_idx", "data_key_component", "data_key_component_value")


def _put_value_sets(
    conn: duckdb.DuckDBPyConnection,
    urn: str,
    tables: _ValueSetTables,
    owner_idx: int,
    selections: Sequence[KeyValue | ComponentValueSet],
) -> None:
    """A region's or a data key's selections, and their values, in order.

    A data key's are all components; a region's are its keys, then its
    components, and `is_key` tells them apart on the way back (#433, #422).
    """
    value_rows = [
        [
            urn,
            owner_idx,
            _selection_id(selection),
            value.value,
            selection.include,
            value.cascade.value if value.cascade is not None else None,
            value.lang,
            value.valid_from,
            value.valid_to,
        ]
        for selection in selections
        for value in selection.values
    ]
    if value_rows:
        _execute_bulk_insert(
            conn,
            tables.value_table,
            [
                "constraint_urn",
                tables.owner_column,
                "component_id",
                "value",
                "is_included",
                "cascade",
                "lang",
                "valid_from",
                "valid_to",
            ],
            value_rows,
        )
    if selections:
        _execute_bulk_insert(
            conn,
            tables.selection_table,
            [
                "constraint_urn",
                tables.owner_column,
                "position",
                "component_id",
                "is_key",
                "start_period",
                "start_inclusive",
                "end_period",
                "end_inclusive",
                "is_included",
                "remove_prefix",
                "valid_from",
                "valid_to",
                "time_valid_from",
                "time_valid_to",
            ],
            [
                [
                    urn,
                    owner_idx,
                    position,
                    _selection_id(selection),
                    isinstance(selection, KeyValue),
                    *_bound_columns(selection.time_range),
                    selection.include,
                    selection.remove_prefix,
                    selection.valid_from if isinstance(selection, KeyValue) else None,
                    selection.valid_to if isinstance(selection, KeyValue) else None,
                    selection.time_range.valid_from if selection.time_range is not None else None,
                    selection.time_range.valid_to if selection.time_range is not None else None,
                ]
                for position, selection in enumerate(selections)
            ],
        )


#: The attachment kinds a constraint may hold several of (#422): table,
#: its URN column, the `artefact_ref` role, and the `ConstraintAttachment` field.
_ATTACHED_KINDS: Final = (
    ("data_constraint_dataflow", "dataflow_urn", "attachment_dataflow", "dataflows"),
    ("data_constraint_dsd", "dsd_urn", "attachment_dsd", "data_structures"),
    ("data_constraint_provision", "provision_urn", "attachment_provision", "provision_agreements"),
)


def put_data_constraint(
    conn: duckdb.DuckDBPyConnection, cc: DataConstraint, *, source_registry: str | None = None
) -> None:
    """Write a DataConstraint to relational tables."""
    urn = str(artefact_urn(cc))

    _delete_artefact_cascade(conn, urn, "DataConstraint")
    _put_spine(conn, urn, "DataConstraint", cc, source_registry=source_registry)
    _put_artefact_text_and_annotations(conn, urn, cc)

    attachment = cc.attachment
    provider_urn = str(attachment.data_provider.urn) if attachment.data_provider is not None else None

    # The provider as a column *and* a ref, as `put_provision_agreement` does:
    # the column is what a query joins on, the ref rows keep feeding the
    # generic inbound-reference index. See the schema comment on
    # `data_constraint`.
    conn.execute(
        """
        INSERT INTO data_constraint (
            urn, constraint_type,
            ref_period_start, ref_period_end,
            release_periodicity, release_offset, release_tolerance,
            attachment_data_provider_urn, simple_data_sources
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            urn,
            cc.constraint_type.value,
            to_storage(cc.reference_period.start_period) if cc.reference_period else None,
            to_storage(cc.reference_period.end_period) if cc.reference_period else None,
            cc.release_calendar.periodicity if cc.release_calendar else None,
            cc.release_calendar.offset if cc.release_calendar else None,
            cc.release_calendar.tolerance if cc.release_calendar else None,
            provider_urn,
            list(attachment.simple_data_sources),
        ],
    )

    refs: list[tuple[str, str, str, int | None]] = []
    if provider_urn is not None:
        refs.append((urn, provider_urn, "attachment_data_provider", None))
    for table, column, role, attached in _ATTACHED_KINDS:
        targets = [str(ref.urn) for ref in getattr(attachment, attached)]
        if targets:
            _execute_bulk_insert(
                conn, table, ["constraint_urn", "ordinal", column], [[urn, i, t] for i, t in enumerate(targets)]
            )
            refs.extend((urn, target, role, i) for i, target in enumerate(targets))
    if attachment.data_sets:
        _execute_bulk_insert(
            conn,
            "data_constraint_data_set",
            ["constraint_urn", "ordinal", "data_provider_urn", "data_set_id"],
            [[urn, i, str(ds.data_provider.urn), ds.id] for i, ds in enumerate(attachment.data_sets)],
        )
        refs.extend(
            (urn, str(ds.data_provider.urn), "attachment_data_set_provider", i)
            for i, ds in enumerate(attachment.data_sets)
        )
    if attachment.queryable_data_sources:
        _execute_bulk_insert(
            conn,
            "data_constraint_queryable_source",
            [
                "constraint_urn",
                "ordinal",
                "data_url",
                "is_rest_datasource",
                "is_web_service_datasource",
                "wsdl_url",
                "wadl_url",
            ],
            [
                [
                    urn,
                    i,
                    source.data_url,
                    source.is_rest_datasource,
                    source.is_web_service_datasource,
                    source.wsdl_url,
                    source.wadl_url,
                ]
                for i, source in enumerate(attachment.queryable_data_sources)
            ],
        )

    for region_idx, region in enumerate(cc.regions):
        conn.execute(
            "INSERT INTO cube_region (constraint_urn, region_idx, is_included) VALUES (?, ?, ?)",
            [urn, region_idx, region.include],
        )
        _put_value_sets(conn, urn, _REGION_SETS, region_idx, [*region.keys, *region.components])

    _put_data_key_sets(conn, urn, cc.data_key_sets)

    _put_part_annotations(conn, urn, "regions", cc.regions)
    _put_refs(conn, refs)


def _put_data_key_sets(conn: duckdb.DuckDBPyConnection, urn: str, key_sets: Sequence[DataKeySet]) -> None:
    """A constraint's key sets, each with its ``is_included``, and their keys (#437, #479)."""
    if not key_sets:
        return
    _execute_bulk_insert(
        conn,
        "data_key_set",
        ["constraint_urn", "set_idx", "is_included"],
        [[urn, s, key_set.is_included] for s, key_set in enumerate(key_sets)],
    )
    # Numbered across the constraint: the value and component tables key a key alone.
    keys = [(s, key) for s, key_set in enumerate(key_sets) for key in key_set.keys]
    _execute_bulk_insert(
        conn,
        "data_key",
        ["constraint_urn", "key_idx", "set_idx", "valid_from", "valid_to"],
        [[urn, i, s, key.valid_from, key.valid_to] for i, (s, key) in enumerate(keys)],
    )
    for key_idx, (_, key) in enumerate(keys):
        _put_value_sets(conn, urn, _KEY_SETS, key_idx, list(key.components))
    dkv_rows = [
        [urn, key_idx, position, component_id, value]
        for key_idx, (_, key) in enumerate(keys)
        for position, (component_id, value) in enumerate(key.values)
    ]
    if dkv_rows:
        _execute_bulk_insert(
            conn, "data_key_value", ["constraint_urn", "key_idx", "position", "component_id", "value"], dkv_rows
        )
    for s, key_set in enumerate(key_sets):
        _put_part_annotations(conn, urn, f"data_key_sets[{s}].keys", key_set.keys)


def _selection_id(selection: KeyValue | ComponentValueSet) -> str:
    return selection.dimension_id if isinstance(selection, KeyValue) else selection.component_id


def _bound_columns(time_range: TimeRange | None) -> list[object]:
    """`start_period, start_inclusive, end_period, end_inclusive` — NULL for an absent end."""
    start = time_range.start if time_range is not None else None
    end = time_range.end if time_range is not None else None
    return [
        start.period if start else None,
        start.inclusive if start else None,
        end.period if end else None,
        end.inclusive if end else None,
    ]


def put_category_scheme(
    conn: duckdb.DuckDBPyConnection, cs: CategoryScheme, *, source_registry: str | None = None
) -> None:
    """Write a CategoryScheme to artefact + category table."""
    urn = str(artefact_urn(cs))

    _delete_artefact_cascade(conn, urn, "CategoryScheme")
    _put_spine(conn, urn, "CategoryScheme", cs, source_registry=source_registry)
    ann_batch = _AnnotationBatch(urn)
    _put_artefact_text_and_annotations(conn, urn, cs, ann_batch)

    # Keyed by path, as `put_hierarchy` (#439).
    rows = list(tree_rows(cs))
    if rows:
        category_prefix = _item_urn_prefix(cs, urn, "Category")
        all_rows: list[list[object]] = []
        for position, row in enumerate(rows):
            cat = row.node
            cat_urn = category_prefix + row.path
            all_rows.append(
                [
                    cat_urn,
                    urn,
                    cat.id,
                    row.path,
                    row.parent_path,
                    position,
                    _istring_to_map(cat.name),
                    _istring_to_map(cat.description),
                    row.depth,
                    row.ancestor_ids,
                    row.ancestor_paths,
                    cat.uri,
                ]
            )
            ann_batch.add(cat_urn, cat.annotations, cat.links)

        _execute_bulk_insert(
            conn,
            "category",
            [
                "category_urn",
                "scheme_urn",
                "category_id",
                "path",
                "parent_path",
                "position",
                "name",
                "description",
                "depth",
                "ancestor_ids",
                "ancestor_paths",
                "uri",
            ],
            all_rows,
        )

    ann_batch.flush(conn)


def put_categorisation(
    conn: duckdb.DuckDBPyConnection, cat: Categorisation, *, source_registry: str | None = None
) -> None:
    """Write a Categorisation to artefact + categorisation extension."""
    urn = str(artefact_urn(cat))

    _delete_artefact_cascade(conn, urn, "Categorisation")
    _put_spine(conn, urn, "Categorisation", cat, source_registry=source_registry)
    _put_artefact_text_and_annotations(conn, urn, cat)

    source_urn = str(cat.source.urn) if cat.source is not None else None
    target_urn = str(cat.target.urn) if cat.target is not None else None
    conn.execute(
        "INSERT INTO categorisation (urn, source_urn, target_urn) VALUES (?, ?, ?)",
        [urn, source_urn, target_urn],
    )

    refs: list[tuple[str, str, str, int | None]] = []
    if source_urn is not None:
        refs.append((urn, source_urn, "source", None))
    if target_urn is not None:
        refs.append((urn, target_urn, "target", None))
    _put_refs(conn, refs)


#: Which `Ref`-tuple field of a 2.1 container goes under which `artefact_ref`
#: role. The role is what lets one table hold both of `StructureSet`'s lists
#: without them merging on read.
_CONTAINER_REF_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "HierarchicalCodelist": ("hierarchies", "included_codelists"),
    "StructureSet": ("maps", "related_structures"),
}


def put_container(
    conn: duckdb.DuckDBPyConnection,
    container: SupportsUrn,
    *,
    source_registry: str | None = None,
) -> None:
    """Write a 2.1 grouping container — `HierarchicalCodelist`, `StructureSet`.

    **No table of its own** (#395). SDMX-ML 2.1 nests hierarchies inside a
    `<str:HierarchicalCodelist>` and maps inside a `<str:StructureSet>`; 3.0
    removed both wrappers and promoted the members to top level, so sdmxlib
    models the container as a peer that *references* its members. That is a
    maintainable plus a list of refs, which `artefact` and `artefact_ref`
    already hold — the container needs a kind, not a schema.

    Storing them is what makes `parse(xml, registry=…)` work on an ordinary
    2.1 document again: 2.1 has no way to write a hierarchy *except* inside a
    `HierarchicalCodelist`, so refusing the container refused the file (#394),
    and dropping it lost the container's own name (#239).
    """
    urn = str(artefact_urn(container))
    type_name = type(container).__name__

    _delete_artefact_cascade(conn, urn, type_name)
    _put_spine(conn, urn, type_name, container, source_registry=source_registry)
    _put_artefact_text_and_annotations(conn, urn, container)

    refs: list[tuple[str, str, str, int | None]] = []
    for field in _CONTAINER_REF_FIELDS[type_name]:
        for ordinal, ref in enumerate(getattr(container, field, ()) or ()):
            refs.append((urn, str(ref.urn), field, ordinal))
    _put_refs(conn, refs)


def put_hierarchy_association(
    conn: duckdb.DuckDBPyConnection,
    ha: HierarchyAssociation,
    *,
    source_registry: str | None = None,
) -> None:
    """Write a HierarchyAssociation to artefact + hierarchy_association extension."""
    urn = str(artefact_urn(ha))

    _delete_artefact_cascade(conn, urn, "HierarchyAssociation")
    _put_spine(conn, urn, "HierarchyAssociation", ha, source_registry=source_registry)
    _put_artefact_text_and_annotations(conn, urn, ha)

    linked_h = str(ha.linked_hierarchy.urn) if ha.linked_hierarchy is not None else None
    linked_o = str(ha.linked_object.urn) if ha.linked_object is not None else None
    context_o = str(ha.context_object.urn) if ha.context_object is not None else None
    # The linked object is usually a Dimension, whose URN no table keys on.
    # Baking the two halves is what gives the association a join to
    # `dsd_component` — see `_split_item_urn` and schema 2.16.
    split = _split_item_urn(linked_o) if linked_o else None
    owner_urn, item_id = split if split is not None else (None, None)
    conn.execute(
        "INSERT INTO hierarchy_association "
        "(urn, linked_hierarchy_urn, linked_object_urn, context_object_urn, "
        "linked_object_owner_urn, linked_object_item_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [urn, linked_h, linked_o, context_o, owner_urn, item_id],
    )

    refs: list[tuple[str, str, str, int | None]] = []
    if linked_h is not None:
        refs.append((urn, linked_h, "linked_hierarchy", None))
    if linked_o is not None:
        refs.append((urn, linked_o, "linked_object", None))
    if context_o is not None:
        refs.append((urn, context_o, "context_object", None))
    _put_refs(conn, refs)


def put_provision_agreement(
    conn: duckdb.DuckDBPyConnection, pa: ProvisionAgreement, *, source_registry: str | None = None
) -> None:
    """Write a ProvisionAgreement to artefact + provision_agreement extension."""
    urn = str(artefact_urn(pa))

    _delete_artefact_cascade(conn, urn, "ProvisionAgreement")
    _put_spine(conn, urn, "ProvisionAgreement", pa, source_registry=source_registry)
    _put_artefact_text_and_annotations(conn, urn, pa)

    dataflow_urn = str(pa.dataflow.urn) if pa.dataflow is not None else None
    dp_urn = str(pa.data_provider.urn) if pa.data_provider is not None else None
    conn.execute(
        "INSERT INTO provision_agreement (urn, dataflow_urn, data_provider_urn) VALUES (?, ?, ?)",
        [urn, dataflow_urn, dp_urn],
    )

    refs: list[tuple[str, str, str, int | None]] = []
    if dataflow_urn is not None:
        refs.append((urn, dataflow_urn, "dataflow", None))
    if dp_urn is not None:
        refs.append((urn, dp_urn, "data_provider", None))
    _put_refs(conn, refs)


def _put_contacts(conn: duckdb.DuckDBPyConnection, owner_urn: str, contacts: Sequence[Contact]) -> None:
    """Write an organisation's contacts.

    Shared by `put_agency_scheme` and `put_data_provider_scheme` because
    `Agency` and `DataProvider` are both `Organisation` in the IM and carry the
    identical `contacts` field. It was inline in the agency writer and the
    provider writer simply had none, so `DataProvider.contacts` was dropped on
    every write (#309) — the same shape of defect as `Concept.parent` (#307):
    a model field with no storage behind it, silent on read and round-trip.
    """
    for ordinal, contact in enumerate(contacts):
        conn.execute(
            """
            INSERT INTO organisation_contact
                (owner_urn, ordinal, contact_name, department, role,
                 phones, faxes, x400s, uris, emails, contact_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                owner_urn,
                ordinal,
                _istring_to_map(contact.name),
                _istring_to_map(contact.department),
                _istring_to_map(contact.role),
                list(contact.phones),
                list(contact.faxes),
                list(contact.x400s),
                list(contact.uris),
                list(contact.emails),
                contact.id,
            ],
        )


def put_agency_scheme(
    conn: duckdb.DuckDBPyConnection, ascheme: AgencyScheme, *, source_registry: str | None = None
) -> None:
    """Write an AgencyScheme to artefact + agency + organisation_contact tables."""
    urn = str(artefact_urn(ascheme))

    _delete_artefact_cascade(conn, urn, "AgencyScheme")
    _put_spine(conn, urn, "AgencyScheme", ascheme, source_registry=source_registry)
    ann_batch = _AnnotationBatch(urn)
    _put_artefact_text_and_annotations(conn, urn, ascheme, ann_batch)

    if ascheme.agencies:
        agency_prefix = _item_urn_prefix(ascheme, urn, "Agency")
        all_rows: list[list[object]] = []
        for ag in ascheme.agencies:
            ag_urn = agency_prefix + ag.id
            all_rows.append(
                [
                    ag_urn,
                    urn,
                    ag.id,
                    _istring_to_map(ag.name),
                    _istring_to_map(ag.description),
                    ag.uri,
                ]
            )

            _put_contacts(conn, ag_urn, ag.contacts)

            ann_batch.add(ag_urn, ag.annotations, ag.links)

        _execute_bulk_insert(
            conn,
            "agency",
            ["agency_urn", "scheme_urn", "agency_id", "name", "description", "uri"],
            all_rows,
        )

    ann_batch.flush(conn)


def put_data_provider_scheme(
    conn: duckdb.DuckDBPyConnection, dps: DataProviderScheme, *, source_registry: str | None = None
) -> None:
    """Write a DataProviderScheme to artefact + data_provider + contacts."""
    urn = str(artefact_urn(dps))

    _delete_artefact_cascade(conn, urn, "DataProviderScheme")
    _put_spine(conn, urn, "DataProviderScheme", dps, source_registry=source_registry)
    ann_batch = _AnnotationBatch(urn)
    _put_artefact_text_and_annotations(conn, urn, dps, ann_batch)

    if dps.providers:
        provider_prefix = _item_urn_prefix(dps, urn, "DataProvider")
        all_rows: list[list[object]] = []
        for provider in dps.providers:
            p_urn = provider_prefix + provider.id
            all_rows.append(
                [
                    p_urn,
                    urn,
                    provider.id,
                    _istring_to_map(provider.name),
                    _istring_to_map(provider.description),
                    provider.uri,
                ]
            )
            _put_contacts(conn, p_urn, provider.contacts)
            ann_batch.add(p_urn, provider.annotations, provider.links)

        _execute_bulk_insert(
            conn,
            "data_provider",
            ["provider_urn", "scheme_urn", "provider_id", "name", "description", "uri"],
            all_rows,
        )

    ann_batch.flush(conn)


def put_metadata_provider_scheme(
    conn: duckdb.DuckDBPyConnection, mps: MetadataProviderScheme, *, source_registry: str | None = None
) -> None:
    """Write a MetadataProviderScheme to artefact + metadata_provider + contacts.

    The metadata twin of `put_data_provider_scheme`, item table and all — see
    the `metadata_provider` DDL for why it is a separate table (#315).
    """
    urn = str(artefact_urn(mps))

    _delete_artefact_cascade(conn, urn, "MetadataProviderScheme")
    _put_spine(conn, urn, "MetadataProviderScheme", mps, source_registry=source_registry)
    ann_batch = _AnnotationBatch(urn)
    _put_artefact_text_and_annotations(conn, urn, mps, ann_batch)

    if mps.providers:
        provider_prefix = _item_urn_prefix(mps, urn, "MetadataProvider")
        all_rows: list[list[object]] = []
        for provider in mps.providers:
            p_urn = provider_prefix + provider.id
            all_rows.append(
                [
                    p_urn,
                    urn,
                    provider.id,
                    _istring_to_map(provider.name),
                    _istring_to_map(provider.description),
                    provider.uri,
                ]
            )
            _put_contacts(conn, p_urn, provider.contacts)
            ann_batch.add(p_urn, provider.annotations, provider.links)

        _execute_bulk_insert(
            conn,
            "metadata_provider",
            ["provider_urn", "scheme_urn", "provider_id", "name", "description", "uri"],
            all_rows,
        )

    ann_batch.flush(conn)


def put_data_consumer_scheme(
    conn: duckdb.DuckDBPyConnection, dcs: DataConsumerScheme, *, source_registry: str | None = None
) -> None:
    """Write a DataConsumerScheme to artefact + data_consumer + contacts (#469)."""
    urn = str(artefact_urn(dcs))

    _delete_artefact_cascade(conn, urn, "DataConsumerScheme")
    _put_spine(conn, urn, "DataConsumerScheme", dcs, source_registry=source_registry)
    ann_batch = _AnnotationBatch(urn)
    _put_artefact_text_and_annotations(conn, urn, dcs, ann_batch)

    if dcs.consumers:
        prefix = _item_urn_prefix(dcs, urn, "DataConsumer")
        all_rows: list[list[object]] = []
        for consumer in dcs.consumers:
            c_urn = prefix + consumer.id
            all_rows.append(
                [
                    c_urn,
                    urn,
                    consumer.id,
                    _istring_to_map(consumer.name),
                    _istring_to_map(consumer.description),
                    consumer.uri,
                ]
            )
            _put_contacts(conn, c_urn, consumer.contacts)
            ann_batch.add(c_urn, consumer.annotations, consumer.links)

        _execute_bulk_insert(
            conn,
            "data_consumer",
            ["consumer_urn", "scheme_urn", "consumer_id", "name", "description", "uri"],
            all_rows,
        )

    ann_batch.flush(conn)


def put_organisation_unit_scheme(
    conn: duckdb.DuckDBPyConnection, ous: OrganisationUnitScheme, *, source_registry: str | None = None
) -> None:
    """Write an OrganisationUnitScheme to artefact + organisation_unit + contacts (#469)."""
    urn = str(artefact_urn(ous))

    _delete_artefact_cascade(conn, urn, "OrganisationUnitScheme")
    _put_spine(conn, urn, "OrganisationUnitScheme", ous, source_registry=source_registry)
    ann_batch = _AnnotationBatch(urn)
    _put_artefact_text_and_annotations(conn, urn, ous, ann_batch)

    if ous.organisation_units:
        prefix = _item_urn_prefix(ous, urn, "OrganisationUnit")
        all_rows: list[list[object]] = []
        for unit in ous.organisation_units:
            u_urn = prefix + unit.id
            all_rows.append(
                [
                    u_urn,
                    urn,
                    unit.id,
                    unit.parent_id,
                    _istring_to_map(unit.name),
                    _istring_to_map(unit.description),
                    unit.uri,
                ]
            )
            _put_contacts(conn, u_urn, unit.contacts)
            ann_batch.add(u_urn, unit.annotations, unit.links)

        _execute_bulk_insert(
            conn,
            "organisation_unit",
            ["unit_urn", "scheme_urn", "unit_id", "parent_id", "name", "description", "uri"],
            all_rows,
        )

    ann_batch.flush(conn)


def put_representation_map(
    conn: duckdb.DuckDBPyConnection, rm: RepresentationMap, *, source_registry: str | None = None
) -> None:
    """Write a RepresentationMap to its storage tables.

    Every endpoint — a codelist, a value list or a data type — and every
    mapping, in order, each source value's flags included (#422).
    """
    urn = str(artefact_urn(rm))

    _delete_artefact_cascade(conn, urn, "RepresentationMap")
    _put_spine(conn, urn, "RepresentationMap", rm, source_registry=source_registry)
    _put_artefact_text_and_annotations(conn, urn, rm)
    conn.execute("INSERT INTO representation_map (urn) VALUES (?)", [urn])

    refs: list[tuple[str, str, str, int | None]] = []
    for side, endpoints in (("source", rm.sources), ("target", rm.targets)):
        rows: list[list[object]] = []
        for i, endpoint in enumerate(endpoints):
            match endpoint:
                case MapDataType(data_type=data_type):
                    rows.append([urn, i, None, data_type])
                case Ref():
                    codelist_urn = str(endpoint.urn)
                    rows.append([urn, i, codelist_urn, None])
                    refs.append((urn, codelist_urn, f"{side}_codelist", i))
        if rows:
            _execute_bulk_insert(
                conn,
                f"representation_map_{side}",
                ["representation_map_urn", "ordinal", "codelist_urn", "data_type"],
                rows,
            )

    if rm.mappings:
        _execute_bulk_insert(
            conn,
            "representation_mapping",
            [
                "representation_map_urn",
                "ordinal",
                "sources",
                "source_is_regex",
                "source_start_index",
                "source_end_index",
                "targets",
                "valid_from",
                "valid_to",
            ],
            [
                [
                    urn,
                    i,
                    [sv.value for sv in m.sources],
                    [sv.is_regex for sv in m.sources],
                    [sv.start_index for sv in m.sources],
                    [sv.end_index for sv in m.sources],
                    list(m.targets),
                    m.valid_from,
                    m.valid_to,
                ]
                for i, m in enumerate(rm.mappings)
            ],
        )
    _put_part_annotations(conn, urn, "mappings", rm.mappings)

    _put_refs(conn, refs)


def _put_map_body(
    conn: duckdb.DuckDBPyConnection,
    artefact: ConceptSchemeMap | CategorySchemeMap | OrganisationSchemeMap | StructureMap,
    *,
    kind: str,
    parent_table: str,
    child_table: str,
    child_owner_column: str,
) -> None:
    """The endpoints and ordered pairs, for whichever of the three maps (#395).

    Only the part that differs between them. Cascade, spine and text stay in
    each writer rather than being folded in here: they are the same three
    lines, and `test_the_cascade_precondition_holds_for_every_writer` reads
    each dispatched writer's own source to check the cascade precedes the
    spine — a check delegation would hide.
    """
    urn = str(artefact_urn(artefact))
    source_urn = _ref_urn(artefact.source, owner=kind, field="source")
    target_urn = _ref_urn(artefact.target, owner=kind, field="target")
    conn.execute(
        f"INSERT INTO {parent_table} (urn, source_urn, target_urn) VALUES (?, ?, ?)",
        [urn, source_urn, target_urn],
    )

    refs: list[tuple[str, str, str, int | None]] = []
    if source_urn is not None:
        refs.append((urn, source_urn, "source", None))
    if target_urn is not None:
        refs.append((urn, target_urn, "target", None))

    columns: list[str]
    rows: list[list[object]]
    if isinstance(artefact, StructureMap):
        # N:M, what maps the values, and 2.1's text format mapping (#422).
        columns = [
            child_owner_column,
            "ordinal",
            "sources",
            "targets",
            "representation_map_urn",
            *TO_TEXT_FORMAT_COLUMN_NAMES,
            "to_value_type",
        ]
        rows = [
            [
                urn,
                i,
                list(m.sources),
                list(m.targets),
                _ref_urn_or_none(m.representation_map),
                *_facet_values(m.to_text_format),
                m.to_value_type.value if m.to_value_type is not None else None,
            ]
            for i, m in enumerate(artefact.maps)
        ]
        # What maps a component's values is a reference too (GH-152): a
        # representation map, or an epoch or date-pattern map of this one.
        refs += [
            (urn, mapped, "representation_map", i)
            for i, m in enumerate(artefact.maps)
            if (mapped := _ref_urn_or_none(m.representation_map)) is not None
        ]
    else:
        # An item map's validity (#422).
        columns = [child_owner_column, "ordinal", "source_id", "target_id", "valid_from", "valid_to"]
        rows = [[urn, i, m.source_id, m.target_id, m.valid_from, m.valid_to] for i, m in enumerate(artefact.maps)]
    if rows:
        _execute_bulk_insert(conn, child_table, columns, rows)
    _put_part_annotations(conn, urn, "maps", artefact.maps)

    _put_refs(conn, refs)


def put_concept_scheme_map(
    conn: duckdb.DuckDBPyConnection, csm: ConceptSchemeMap, *, source_registry: str | None = None
) -> None:
    """Write a ConceptSchemeMap to `concept_scheme_map` + `item_map`."""
    urn = str(artefact_urn(csm))
    _delete_artefact_cascade(conn, urn, "ConceptSchemeMap")
    _put_spine(conn, urn, "ConceptSchemeMap", csm, source_registry=source_registry)
    _put_artefact_text_and_annotations(conn, urn, csm)
    _put_map_body(
        conn,
        csm,
        kind="ConceptSchemeMap",
        parent_table="concept_scheme_map",
        child_table="item_map",
        child_owner_column="map_urn",
    )


def put_category_scheme_map(
    conn: duckdb.DuckDBPyConnection, csm: CategorySchemeMap, *, source_registry: str | None = None
) -> None:
    """Write a CategorySchemeMap to `category_scheme_map` + `item_map`."""
    urn = str(artefact_urn(csm))
    _delete_artefact_cascade(conn, urn, "CategorySchemeMap")
    _put_spine(conn, urn, "CategorySchemeMap", csm, source_registry=source_registry)
    _put_artefact_text_and_annotations(conn, urn, csm)
    _put_map_body(
        conn,
        csm,
        kind="CategorySchemeMap",
        parent_table="category_scheme_map",
        child_table="item_map",
        child_owner_column="map_urn",
    )


def put_organisation_scheme_map(
    conn: duckdb.DuckDBPyConnection, csm: OrganisationSchemeMap, *, source_registry: str | None = None
) -> None:
    """Write an OrganisationSchemeMap to `organisation_scheme_map` + `item_map`."""
    urn = str(artefact_urn(csm))
    _delete_artefact_cascade(conn, urn, "OrganisationSchemeMap")
    _put_spine(conn, urn, "OrganisationSchemeMap", csm, source_registry=source_registry)
    _put_artefact_text_and_annotations(conn, urn, csm)
    _put_map_body(
        conn,
        csm,
        kind="OrganisationSchemeMap",
        parent_table="organisation_scheme_map",
        child_table="item_map",
        child_owner_column="map_urn",
    )


def put_structure_map(conn: duckdb.DuckDBPyConnection, sm: StructureMap, *, source_registry: str | None = None) -> None:
    """Write a StructureMap to `structure_map` + `component_map`."""
    urn = str(artefact_urn(sm))
    _delete_artefact_cascade(conn, urn, "StructureMap")
    _put_spine(conn, urn, "StructureMap", sm, source_registry=source_registry)
    _put_artefact_text_and_annotations(conn, urn, sm)
    _put_map_body(
        conn,
        sm,
        kind="StructureMap",
        parent_table="structure_map",
        child_table="component_map",
        child_owner_column="structure_map_urn",
    )
    if sm.is_extension:
        # 2.1's alone (#422), so most maps skip the statement.
        conn.execute("UPDATE structure_map SET is_extension = TRUE WHERE urn = ?", [urn])
    _put_structure_map_parts(conn, urn, sm)


def _ref_urn_or_none(ref: object) -> str | None:
    return str(ref.urn) if isinstance(ref, Ref) else None


def _date_map_row(urn: str, ordinal: int, m: EpochMap | DatePatternMap) -> list[object]:
    """What `DateMapType` gives either kind, in the two tables' shared column order (#422)."""
    return [
        urn,
        ordinal,
        m.id,
        m.uri,
        [source for source, _ in m.component_maps],
        [target for _, target in m.component_maps],
    ]


_DATE_MAP_COLUMNS = ["structure_map_urn", "ordinal", "map_id", "uri", "source_ids", "target_ids"]
_DATE_MAP_TAIL = ["frequency_dimension", "mapped_frequencies", "target_frequency_id", "resolve_period"]


def _date_map_tail(m: EpochMap | DatePatternMap) -> list[object]:
    return [
        m.frequency_dimension,
        list(m.mapped_frequencies),
        m.target_frequency_id,
        m.resolve_period.value if m.resolve_period is not None else None,
    ]


def _put_structure_map_parts(conn: duckdb.DuckDBPyConnection, urn: str, sm: StructureMap) -> None:
    """A structure map's date and value maps, and their annotations (#422)."""
    batch = _AnnotationBatch(urn)
    if sm.epoch_maps:
        _execute_bulk_insert(
            conn,
            "epoch_map",
            [*_DATE_MAP_COLUMNS, "base_period", "epoch_period", *_DATE_MAP_TAIL],
            [
                [*_date_map_row(urn, i, m), m.base_period, m.epoch_period.value, *_date_map_tail(m)]
                for i, m in enumerate(sm.epoch_maps)
            ],
        )
    if sm.date_pattern_maps:
        _execute_bulk_insert(
            conn,
            "date_pattern_map",
            [*_DATE_MAP_COLUMNS, "source_pattern", "locale", *_DATE_MAP_TAIL],
            [
                [*_date_map_row(urn, i, m), m.source_pattern, m.locale, *_date_map_tail(m)]
                for i, m in enumerate(sm.date_pattern_maps)
            ],
        )
    if sm.frequency_format_mappings:
        _execute_bulk_insert(
            conn,
            "frequency_format_mapping",
            ["structure_map_urn", "ordinal", "mapping_id", "uri", "frequency_id", "date_pattern"],
            [[urn, i, m.id, m.uri, m.frequency_id, m.date_pattern] for i, m in enumerate(sm.frequency_format_mappings)],
        )
    if sm.fixed_value_maps:
        _execute_bulk_insert(
            conn,
            "fixed_value_map",
            ["structure_map_urn", "ordinal", "source_id", "target_id", "values"],
            [[urn, i, m.source_id, m.target_id, list(m.values)] for i, m in enumerate(sm.fixed_value_maps)],
        )
    # The identifiable kinds by their item URN; a fixed value map has none.
    for kind, items in (
        ("EpochMap", sm.epoch_maps),
        ("DatePatternMap", sm.date_pattern_maps),
        ("FrequencyFormatMapping", sm.frequency_format_mappings),
    ):
        for item in items:
            batch.add(_make_item_urn(sm, urn, kind, item.id), item.annotations, item.links)
    for i, fixed in enumerate(sm.fixed_value_maps):
        batch.add(part_owner(urn, "fixed_value_maps", i), fixed.annotations)
    batch.flush(conn)


# ── MetadataStructure / MetadataSet writers ─────────────────────────────


_METADATA_ATTRIBUTE_COLS = [
    "attribute_urn",
    "msd_urn",
    "attribute_id",
    "path",
    "parent_path",
    "position",
    "concept_urn",
    *FACET_COLUMN_NAMES,
    *ENUMERATION_FORMAT_COLUMN_NAMES,
    "min_occurs",
    "max_occurs",
    "is_presentational",
    "depth",
    "ancestor_ids",
    "ancestor_paths",
    "uri",
    "sentinel_values",
]


def put_metadata_structure(
    conn: duckdb.DuckDBPyConnection, msd: MetadataStructure, *, source_registry: str | None = None
) -> None:
    """Write a MetadataStructure to artefact + metadata_structure + metadata_attribute tables."""
    urn = str(artefact_urn(msd))

    _delete_artefact_cascade(conn, urn, "MetadataStructure")
    _put_spine(conn, urn, "MetadataStructure", msd, source_registry=source_registry)
    ann_batch = _AnnotationBatch(urn)
    _put_artefact_text_and_annotations(conn, urn, msd, ann_batch)

    conn.execute(
        "INSERT INTO metadata_structure (urn, attribute_list_uri) VALUES (?, ?)", [urn, msd.attribute_list_uri]
    )
    # Keyed as a DSD's component lists are.
    list_urn = _make_item_urn(msd, urn, "MetadataAttributeDescriptor", "MetadataAttributeDescriptor")
    ann_batch.add(list_urn, msd.attribute_list_annotations, msd.attribute_list_links)

    refs: list[tuple[str, str, str, int | None]] = []

    # Keyed by path, as `put_hierarchy` (#439).
    tree = list(tree_rows(msd))
    if tree:
        attribute_prefix = _item_urn_prefix(msd, urn, "MetadataAttribute")
        rows: list[list[object]] = []
        for position, row in enumerate(tree):
            attr = row.node
            attr_urn = attribute_prefix + row.path

            concept_urn = str(attr.concept.urn) if isinstance(attr.concept, Ref) else None
            if concept_urn:
                refs.append((urn, concept_urn, "concept_identity", position))

            rep = attr.representation
            # `is_multilingual` was once missing here *and* from the table, so
            # a `MetadataAttribute` carrying a multilingual facet round-tripped
            # as `None` while a `Concept` and a `Dimension` kept it. The column
            # group is derived (`schema._FACET_COLUMNS`) and so is this
            # expansion, so neither half can drift alone again.
            if isinstance(rep, Ref):
                refs.append((urn, str(rep.urn), "enumeration", position))

            rows.append(
                [
                    attr_urn,
                    urn,
                    attr.id,
                    row.path,
                    row.parent_path,
                    position,
                    concept_urn,
                    *_representation_columns(rep),
                    *_facet_values(attr.enumeration_format),
                    attr.min_occurs,
                    attr.max_occurs,
                    attr.is_presentational,
                    row.depth,
                    row.ancestor_ids,
                    row.ancestor_paths,
                    attr.uri,
                    _sentinel_rows(rep),
                ]
            )
            ann_batch.add(attr_urn, attr.annotations, attr.links)

        _execute_bulk_insert(conn, "metadata_attribute", _METADATA_ATTRIBUTE_COLS, rows)

    ann_batch.flush(conn)
    _put_refs(conn, refs)


def _serialize_reported_value(v: ReportedAttributeValue | None) -> tuple[str | None, str | None, dict[str, str]]:
    """Return (value_kind, scalar_text, i18n_map) for one ReportedAttribute.value.

    None → (None, None, {})
    InternationalString → ("i18n", None, {lang: text, ...})
    str/int/float/bool → ("scalar", str(v), {})
    """
    if v is None:
        return None, None, {}
    if isinstance(v, InternationalString):
        return "i18n", None, _istring_to_map(v)
    return "scalar", str(v), {}


def _collect_reported_values(
    attributes: Sequence[ReportedAttribute],
    prefix: str,
    set_urn: str,
    sibling_counters: dict[str, int],
    value_rows: list[list[object]],
) -> None:
    """Walk a ReportedAttribute tree, emitting metadata_value rows.

    Repetition (multiple sibling ReportedAttributes with the same id) is
    encoded by ``sibling_index`` — counted per (parent_path, attribute_id).
    Each row's ``ordinal`` is its place in a pre-order walk, so the rows in
    ``ordinal`` order are the tree in document order.
    """
    for attr in attributes:
        sibling_key = f"{prefix}|{attr.id}"
        idx = sibling_counters.get(sibling_key, 0)
        sibling_counters[sibling_key] = idx + 1
        # Use bracketed sibling index in the path to keep nested children
        # under the right repeated parent unambiguously.
        path = f"{prefix}.{attr.id}" if prefix else attr.id
        if idx > 0:
            path = f"{path}[{idx}]"

        kind, scalar, i18n = _serialize_reported_value(attr.value)
        value_rows.append([set_urn, path, idx, len(value_rows), kind, scalar, i18n])

        if attr.children:
            _collect_reported_values(attr.children, path, set_urn, sibling_counters, value_rows)


_METADATA_VALUE_COLS = [
    "set_urn",
    "attribute_path",
    "sibling_index",
    "ordinal",
    "value_kind",
    "scalar",
    "i18n",
]


def put_metadataflow(conn: duckdb.DuckDBPyConnection, mdf: Metadataflow, *, source_registry: str | None = None) -> None:
    """Write a Metadataflow to artefact + metadataflow extension."""
    urn = str(artefact_urn(mdf))

    _delete_artefact_cascade(conn, urn, "Metadataflow")
    _put_spine(conn, urn, "Metadataflow", mdf, source_registry=source_registry)
    _put_artefact_text_and_annotations(conn, urn, mdf)

    msd_urn = str(mdf.structure.urn) if mdf.structure is not None else None
    conn.execute(
        "INSERT INTO metadataflow (urn, msd_urn) VALUES (?, ?)",
        [urn, msd_urn],
    )

    # Declared targets are raw (possibly wildcarded) URN strings — persisted
    # verbatim in an ordered side table, not the resolved artefact_ref graph.
    target_rows = [[urn, ordinal, target] for ordinal, target in enumerate(mdf.targets)]
    if target_rows:
        _execute_bulk_insert(conn, "metadata_declared_target", ["source_urn", "ordinal", "target_urn"], target_rows)

    refs: list[tuple[str, str, str, int | None]] = []
    if msd_urn is not None:
        refs.append((urn, msd_urn, "structure", None))
    _put_refs(conn, refs)


def put_metadata_provision_agreement(
    conn: duckdb.DuckDBPyConnection, mpa: MetadataProvisionAgreement, *, source_registry: str | None = None
) -> None:
    """Write a MetadataProvisionAgreement to artefact + extension table."""
    urn = str(artefact_urn(mpa))

    _delete_artefact_cascade(conn, urn, "MetadataProvisionAgreement")
    _put_spine(conn, urn, "MetadataProvisionAgreement", mpa, source_registry=source_registry)
    _put_artefact_text_and_annotations(conn, urn, mpa)

    flow_urn = str(mpa.metadataflow.urn) if mpa.metadataflow is not None else None
    provider_urn = str(mpa.metadata_provider.urn) if mpa.metadata_provider is not None else None
    conn.execute(
        "INSERT INTO metadata_provision_agreement (urn, metadataflow_urn, metadata_provider_urn) VALUES (?, ?, ?)",
        [urn, flow_urn, provider_urn],
    )

    # Declared targets that restrict the flow for this provision. Stored in
    # the same ordered side table as Metadataflow targets, keyed by source.
    target_rows = [[urn, ordinal, target] for ordinal, target in enumerate(mpa.targets)]
    if target_rows:
        _execute_bulk_insert(conn, "metadata_declared_target", ["source_urn", "ordinal", "target_urn"], target_rows)

    refs: list[tuple[str, str, str, int | None]] = []
    if flow_urn is not None:
        refs.append((urn, flow_urn, "metadataflow", None))
    if provider_urn is not None:
        refs.append((urn, provider_urn, "metadata_provider", None))
    _put_refs(conn, refs)


def put_metadata_set(conn: duckdb.DuckDBPyConnection, ms: MetadataSet, *, source_registry: str | None = None) -> None:
    """Write a MetadataSet plus its targets and attribute tree."""
    urn = str(artefact_urn(ms))

    _delete_artefact_cascade(conn, urn, "MetadataSet")
    _put_spine(conn, urn, "MetadataSet", ms, source_registry=source_registry)
    _put_artefact_text_and_annotations(conn, urn, ms)

    structure_urn = str(ms.structure.urn)
    structure_kind = ms.structure.urn.artefact_class
    conn.execute(
        "INSERT INTO metadata_set (urn, structure_urn, structure_kind) VALUES (?, ?, ?)",
        [urn, structure_urn, structure_kind],
    )

    refs: list[tuple[str, str, str, int | None]] = [(urn, structure_urn, "structure", None)]

    target_rows: list[list[object]] = []
    for ordinal, target in enumerate(ms.targets):
        target_urn_str = str(target.urn)
        target_rows.append([urn, ordinal, target_urn_str])
        # One ref per (set, target) — ordinal disambiguates the artefact_ref PK
        # when a set lists the same target URN twice (rare but legal).
        refs.append((urn, target_urn_str, f"target[{ordinal}]", ordinal))

    if target_rows:
        _execute_bulk_insert(conn, "metadata_target", ["set_urn", "ordinal", "target_urn"], target_rows)

    value_rows: list[list[object]] = []
    _collect_reported_values(ms.attributes, "", urn, {}, value_rows)
    if value_rows:
        _execute_bulk_insert(conn, "metadata_value", _METADATA_VALUE_COLS, value_rows)

    _put_refs(conn, refs)


# ── DataflowBinding writer ──────────────────────────────────────────────


def put_dataflow_binding(
    conn: duckdb.DuckDBPyConnection, binding: DataflowBinding, *, source_registry: str | None = None
) -> None:
    """Write a DataflowBinding to artefact spine + dataflow_binding tables."""
    urn = str(artefact_urn(binding))

    _delete_artefact_cascade(conn, urn, "DataflowBinding")
    _put_spine(conn, urn, "DataflowBinding", binding, source_registry=source_registry)
    _put_artefact_text_and_annotations(conn, urn, binding)

    conn.execute(
        """
        INSERT INTO dataflow_binding (
            urn, dataflow_urn, table_location, database_role,
            dimension_column_map, measure_column_map, attribute_column_map,
            structural_version, binding_source_registry,
            materialized_at, bound_at, series_attributes_location
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            urn,
            str(binding.dataflow.urn),
            binding.table_location,
            binding.database_role,
            dict(binding.dimension_column_map),
            dict(binding.measure_column_map),
            dict(binding.attribute_column_map),
            binding.structural_version,
            binding.source_registry,
            # Schema stores TIMESTAMP (naive); model contract is tz-aware.
            # Normalize to UTC, strip tzinfo on write; reader re-tags as UTC.
            # The convention this column pioneered, now shared — see
            # `sdmxlib._time`, which is this pair extracted.
            to_storage(binding.materialized_at),
            to_storage(binding.bound_at),
            binding.series_attributes_location,
        ],
    )

    # Reference to the bound dataflow — federation-friendly outgoing edge.
    _put_refs(conn, [(urn, str(binding.dataflow.urn), "dataflow", None)])


# ── Dispatch ────────────────────────────────────────────────────────────

_WRITER_DISPATCH: dict[type, Callable[..., None]] = {
    Codelist: put_codelist,
    ValueList: put_value_list,
    DataStructure: put_data_structure,
    Dataflow: put_dataflow,
    ConceptScheme: put_concept_scheme,
    Hierarchy: put_hierarchy,
    HierarchyAssociation: put_hierarchy_association,
    DataConstraint: put_data_constraint,
    CategoryScheme: put_category_scheme,
    Categorisation: put_categorisation,
    ProvisionAgreement: put_provision_agreement,
    AgencyScheme: put_agency_scheme,
    DataProviderScheme: put_data_provider_scheme,
    MetadataProviderScheme: put_metadata_provider_scheme,
    DataConsumerScheme: put_data_consumer_scheme,
    OrganisationUnitScheme: put_organisation_unit_scheme,
    RepresentationMap: put_representation_map,
    ConceptSchemeMap: put_concept_scheme_map,
    CategorySchemeMap: put_category_scheme_map,
    OrganisationSchemeMap: put_organisation_scheme_map,
    StructureMap: put_structure_map,
    MetadataStructure: put_metadata_structure,
    Metadataflow: put_metadataflow,
    MetadataProvisionAgreement: put_metadata_provision_agreement,
    MetadataSet: put_metadata_set,
    DataflowBinding: put_dataflow_binding,
    HierarchicalCodelist: put_container,
    StructureSet: put_container,
}


#: Why each artefact type the store cannot take is not taken, and what to do.
#:
#: A bare "Unsupported artefact type: StructureMap" reads as *malformed*, and
#: it is not: the reader parsed the artefact correctly and the model holds it
#: fine. What is missing is a storage kind (#395), which is a different thing
#: and has a different fix.
#:
#: The refusal itself is deliberate. Filtering these out of
#: `add_all(msg.all_artefacts())` would be a silent drop on ingest, which is
#: what `formats/gaps.py` argues against for the wire formats — *a refusal is
#: visible, a drop is not* — and what completing `all_artefacts()` in #394 was
#: done to stop.
_NOT_PERSISTED: Final[dict[str, str]] = {
    "AttachmentConstraint": "an SDMX 2.1 artefact whose 3.0 form is a DataConstraint (#395)",
}


def _not_persisted_message(cls: type) -> str:
    """The refusal for an artefact this store has no kind for."""
    name = cls.__name__
    why = _NOT_PERSISTED.get(name)
    if why is None:
        return (
            f"{name} cannot be stored: this registry has no storage kind for it. If it should have one, that is #395."
        )
    return (
        f"{name} is not persisted by this registry — {why}. It was parsed and is "
        f"valid; only storage cannot hold it. Refused rather than skipped so the "
        f"artefact is not lost silently: read the message without `registry=` to "
        f"keep it in memory, or drop it from the message before adding."
    )


def put_artefact(conn: duckdb.DuckDBPyConnection, artefact: SupportsUrn, *, source_registry: str | None = None) -> None:
    """Write any supported artefact, dispatching to the appropriate writer."""
    if not isinstance(artefact, SupportsUrn):
        msg = (
            f"{type(artefact).__name__} does not satisfy SupportsUrn — "
            "it needs sdmx_package, sdmx_class, agency_id, id, version"
        )
        raise TypeError(msg)

    # Up the class's MRO, so a subclass is stored as the class it refines: a
    # geographic codelist is a `Codelist`, URN class and row alike (#422).
    writer = next((w for cls in type(artefact).__mro__ if (w := _WRITER_DISPATCH.get(cls)) is not None), None)
    if writer is not None:
        writer(conn, artefact, source_registry=source_registry)
        return

    type_name = _CLASS_TO_NAME.get(type(artefact))
    if type_name is None:
        msg = _not_persisted_message(type(artefact))
        raise TypeError(msg)
    # Registered but no dedicated writer — store spine + text only
    urn = str(artefact_urn(artefact))
    _delete_artefact_cascade(conn, urn, type_name)
    _put_spine(conn, urn, type_name, artefact, source_registry=source_registry)
    _put_artefact_text_and_annotations(conn, urn, artefact)


def drop_artefact(conn: duckdb.DuckDBPyConnection, urn: str) -> bool:
    """Delete an artefact and its child rows.

    Returns True if the artefact existed and was deleted.
    """
    # Unlike a writer, this has only a URN — the type comes off the spine.
    # One extra SELECT on a single-artefact operation, against the ~34
    # no-op DELETEs an undispatched cascade would run (#144).
    row = first(conn.execute("SELECT artefact_type FROM artefact WHERE urn = ?", [urn]))
    if row is None:
        return False
    _delete_artefact_cascade(conn, urn, str(row[0]))
    result = first(
        conn.execute(
            "DELETE FROM artefact WHERE urn = ? RETURNING urn",
            [urn],
        )
    )
    return result is not None


def clear_availability(conn: duckdb.DuckDBPyConnection) -> None:
    """Delete every stored availability.

    A publication started from another (`LocalRegistry.build(previous=)`)
    holds that one's rows until this runs.
    """
    conn.execute("DELETE FROM availability_value")
    conn.execute("DELETE FROM availability")


def put_availability(conn: duckdb.DuckDBPyConnection, dataflow_urn: str, availability: AvailabilityConstraint) -> None:
    """Store `availability`, the answer to `dataflow_urn`'s unfiltered availability query, in place of any earlier.

    The IM's `AvailabilityConstraint` is not an artefact (SDMX 3.1 §12.3.2),
    so it is keyed by the dataflow it describes: its observation count, and
    each dimension's values in its one cube region.
    """
    conn.execute("DELETE FROM availability_value WHERE dataflow_urn = ?", [dataflow_urn])
    conn.execute("DELETE FROM availability WHERE dataflow_urn = ?", [dataflow_urn])
    conn.execute(
        "INSERT INTO availability (dataflow_urn, obs_count, series_count) VALUES (?, ?, ?)",
        [dataflow_urn, availability.obs_count, availability.series_count],
    )
    rows = [
        (dataflow_urn, key.dimension_id, value)
        for key in availability.region.keys
        for value in sorted(v.value for v in key.values)
    ]
    _execute_bulk_insert(conn, "availability_value", ("dataflow_urn", "dimension_id", "value"), rows)
