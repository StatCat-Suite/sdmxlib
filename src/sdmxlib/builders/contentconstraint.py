"""Synthesise a dataflow's availability ContentConstraint from the lake.

`build_dataflow_constraint` scans the observation table bound to a
dataflow and emits a `DataConstraint` (the SDMX-3 spelling of the 2.1
ContentConstraint) whose single inclusive `CubeRegion` lists each
dimension's distinct lake values, and gives the time dimension as the
`TimeRange` those values cover. This is the framework-free core of the
SDMX REST ``/availability`` response for ``context=dataflow``,
``componentID=all`` — no HTTP layer, no async.

The producer side (an edition builder) calls this at bundle-build time to
pre-bake availability caches; the consumer side (a downstream SDMX REST
service) calls the same function to serve ``/availability`` live. Both then serialise with
`sdmxlib.formats.sdmx_json_1_0.writer.write_contentconstraint`, so the two
processes emit byte-identical payloads.

Determinism: the assembled constraint sorts key values by dimension id and
the serialiser sorts each value list lexicographically, so repeated builds
over identical data produce identical bytes.

Example:
    import sdmxlib as sl
    from sdmxlib.builders.contentconstraint import build_dataflow_constraint
    from sdmxlib.formats.sdmx_json_1_0.writer import write_contentconstraint

    with sl.LocalRegistry.edition(bundle_uri) as reg:
        cc = build_dataflow_constraint(reg, dataflow_urn, name={"en": "Availability"})
        cache_bytes = write_contentconstraint(cc)
"""

from typing import Literal

import duckdb

from sdmxlib._duckdb import located
from sdmxlib.local.registry import EditionRegistry, LocalRegistry
from sdmxlib.model.binding import DataflowBinding
from sdmxlib.model.constraint import (
    ComponentValueSet,
    ConstraintAttachment,
    ConstraintType,
    CubeRegion,
    DataConstraint,
    KeyValue,
)
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.period import period_range
from sdmxlib.model.ref import Ref
from sdmxlib.model.rest_api import ApiVersion
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.rest import ComponentFilter, parse_components


def build_dataflow_constraint(
    registry: EditionRegistry,
    dataflow_urn: SdmxUrn[Dataflow],
    *,
    name: InternationalString | dict[str, str],
    conn: duckdb.DuckDBPyConnection | None = None,
    constraint_id: str | None = None,
    version: str = "1.0",
    time_dimension: Literal["range", "values"] = "range",
) -> DataConstraint:
    """Synthesise the dataflow's availability ContentConstraint from the lake.

    For each dimension in the dataflow's `DataflowBinding`, run a
    ``SELECT DISTINCT`` against the bound table and collect the distinct
    values into a `KeyValue`. The result is a single inclusive
    `CubeRegion` describing what is *actually* present
    (`ConstraintType.ACTUAL`).

    The DSD's time dimension is given as a `TimeRange` by default, from the
    earliest period's start to the latest period's end, both inclusive
    (`period_range`): the .Stat Suite Data Explorer reads a time
    dimension's availability only from a range. Gaps between periods are
    not recorded. The time dimension keeps its list of periods when
    ``time_dimension="values"``, when any period is one `period_bounds`
    cannot read (a date-time, a ``<start>/<duration>`` interval), when it
    holds no periods, and when the registry holds no DSD for the dataflow.

    The dimension set and physical column names come from the binding's
    ``dimension_column_map`` — the binding is the authoritative record of
    what columns the materialised table actually has, which is exactly the
    right source for an "actual availability" statement.

    Args:
        registry: An open registry: a published `EditionRegistry` or a `LocalRegistry`. Used to look up the
            binding and (when ``conn`` is omitted) the DuckDB connection.
        dataflow_urn: URN of the dataflow to describe.
        name: The constraint's name, in each language it has. Required: SDMX
            requires one, and which language is the caller's to say (#492).
        conn: DuckDB connection to scan. Defaults to the registry's own
            store connection (the registry's own connection); pass an
            explicit connection when the data store is not DuckDB-backed.
        constraint_id: Id for the emitted constraint. Defaults to the
            dataflow id. Override for byte-compatibility with an existing
            consumer's naming scheme.
        version: Version for the emitted constraint. Defaults to ``"1.0"``.
        time_dimension: ``"range"`` gives the time dimension as a
            `TimeRange`; ``"values"`` gives its distinct periods.

    Returns:
        A `DataConstraint` of type ``ACTUAL`` attached to the dataflow,
        carrying one inclusive `CubeRegion` of per-dimension distinct
        values, with the time dimension's as a range.

    Raises:
        KeyError: If no `DataflowBinding` exists for ``dataflow_urn``.
        ValueError: If ``time_dimension`` is neither ``"range"`` nor ``"values"``.
        TypeError: If ``conn`` is omitted and the registry cannot
            not expose a DuckDB ``connection``.
    """
    _check_time_dimension(time_dimension)
    binding = registry.binding_for(dataflow_urn)
    if binding is None:
        err = (
            f"No DataflowBinding found for {dataflow_urn}; cannot scan the lake "
            "for availability. Add a binding to the registry first."
        )
        raise KeyError(err)

    if conn is None:
        conn = registry.connection
        if conn is None:
            err = (
                "conn is required when the registry does not expose a "
                "DuckDB `connection` (got "
                f"{type(registry).__name__}). Pass conn=<duckdb connection> explicitly."
            )
            raise TypeError(err)

    time_id = _time_dimension_id(registry, dataflow_urn) if time_dimension == "range" else None
    table = located(binding.database_role, binding.table_location)
    key_values: list[KeyValue] = []
    for dim_id in sorted(binding.dimension_column_map):
        column = binding.dimension_column_map[dim_id]
        values = _distinct_values(conn, table, column)
        key_values.append(_key_value(dim_id, frozenset(values), time_id))

    region = CubeRegion(include=True, keys=tuple(key_values))
    attachment = ConstraintAttachment(dataflows=(Ref[Dataflow].unresolved(dataflow_urn),))
    return DataConstraint(
        id=constraint_id if constraint_id is not None else dataflow_urn.id,
        maintainer=dataflow_urn.agency,
        version=version,
        name=name,
        constraint_type=ConstraintType.ACTUAL,
        attachment=attachment,
        regions=(region,),
    )


def build_component_availability(
    registry: EditionRegistry,
    dataflow_urn: SdmxUrn[Dataflow],
    *,
    name: InternationalString | dict[str, str],
    key: str,
    component_id: str,
    api: ApiVersion,
    mode: Literal["exact", "available"] = "exact",
    conn: duckdb.DuckDBPyConnection | None = None,
    constraint_id: str | None = None,
    version: str = "1.0",
    time_dimension: Literal["range", "values"] = "range",
) -> DataConstraint:
    """Synthesise the per-component availability slice for ``(key, component_id, mode)``.

    This is the framework-free core of the SDMX REST request::

        GET /availableconstraint/<flow>/<key>/<component_id>?mode=<mode>

    For each enumerated component it runs ``SELECT DISTINCT`` over the bound
    lake table, filtered by the dotted ``key``, and collects the distinct
    values into a `KeyValue`. The result is a single inclusive
    `CubeRegion` of type `ConstraintType.ACTUAL`.

    One ``SELECT DISTINCT`` per component is deliberate, and measured. Batching
    them into a single scan of ``array_agg(DISTINCT c) FILTER (…)`` per column
    — the shape a consumer hand-wrote on the assumption it would be cheaper
    (#277) — is **1.2x to 2.5x slower** across every width, row count and
    cardinality tried, on parquet and in-memory alike. Parquet is columnar, so
    N queries projecting one column each read the same bytes as one query
    projecting N; there is no I/O to save, and a single-column parallel
    hash-distinct beats N distinct sets built inside one operator.
    `tests/bench/test_availability_scan_shape.py` keeps that answer attached to
    evidence, and fails if a DuckDB release ever reverses it.

    A sealed edition's stored availability (`EditionRegistry.availability`)
    removes work without changing the answer. A dimension whose selected
    values include every value it holds excludes no row, so its constraint is
    dropped before scanning; a dimension left with no constraint at all takes
    its values from the stored availability, with no scan. An attribute
    always scans, since stored availability lists dimensions only, and so
    does every component of a `LocalRegistry`, whose stored availability may
    belong to the edition it extends.

    The two modes differ only in which filter applies while enumerating a
    component ``C``:

    * ``mode="exact"`` — distinct ``C`` values present under the **full**
      key filter.
    * ``mode="available"`` — distinct ``C`` values under the key filter with
      ``C``'s own constraint **dropped**, i.e. the values that would still
      leave the cube non-empty if added to the key.

    The DSD's time dimension is given as a `TimeRange` by default, as in
    `build_dataflow_constraint`, and under the same rules: it keeps its list
    of periods when ``time_dimension="values"``, when any period is one
    `period_bounds` cannot read, when it holds no periods, and when the
    registry holds no DSD for the dataflow.

    Args:
        registry: An open registry: a published `EditionRegistry` or a `LocalRegistry`.
        dataflow_urn: URN of the dataflow to describe.
        name: The constraint's name, in each language it has. Required: SDMX
            requires one, and which language is the caller's to say (#492).
        key: SDMX dotted key over the DSD key dimensions
            (e.g. ``"A5..1._T.count.2021"``). ``"all"`` or ``""`` means the
            whole-wildcard key (no filter); then no DSD resolution is needed.
        api: SDMX-REST generation the caller is serving — ``"2.1"`` or
            ``"3.0"``. Required and never inferred: the dotted key has one
            fewer segment on 2.1, where the time dimension is filtered by
            ``startPeriod`` / ``endPeriod`` rather than by a slot (#337). A
            default would read a key of the other shape with every value one
            position out.
        component_id: The component to enumerate. ``"all"`` enumerates every
            bound dimension (one `KeyValue` each); otherwise just the named
            dimension (a `KeyValue`) or attribute (a `ComponentValueSet`).
        mode: ``"exact"`` or ``"available"`` (see above).
        conn: DuckDB connection to scan. Defaults to
            the registry's own connection.
        constraint_id: Id for the emitted constraint. Defaults to the
            dataflow id.
        version: Version for the emitted constraint. Defaults to ``"1.0"``.
        time_dimension: ``"range"`` gives the time dimension as a
            `TimeRange`; ``"values"`` gives its distinct periods.

    Returns:
        A `DataConstraint` of type ``ACTUAL`` attached to the dataflow,
        carrying one inclusive `CubeRegion` whose key values — or, for an
        attribute, component value set — are the availability slice. A
        component with no matching rows yields an empty value set.

    Raises:
        KeyError: If no `DataflowBinding` exists, or ``component_id`` (when
            not ``"all"``) is not a column on the bound table.
        ValueError: If ``mode`` or ``time_dimension`` is unrecognised, or the
            dotted ``key`` has a segment count that does not match the DSD's
            key dimensions.
        TypeError: If ``conn`` is omitted and the registry cannot
            not expose a DuckDB ``connection``.
    """
    if mode not in ("exact", "available"):
        err = f"Unrecognised mode {mode!r}; expected 'exact' or 'available'."
        raise ValueError(err)
    _check_time_dimension(time_dimension)

    binding = registry.binding_for(dataflow_urn)
    if binding is None:
        err = (
            f"No DataflowBinding found for {dataflow_urn}; cannot scan the lake "
            "for availability. Add a binding to the registry first."
        )
        raise KeyError(err)

    if conn is None:
        conn = registry.connection
        if conn is None:
            err = (
                "conn is required when the registry does not expose a "
                "DuckDB `connection` (got "
                f"{type(registry).__name__}). Pass conn=<duckdb connection> explicitly."
            )
            raise TypeError(err)

    # The dotted key is positional over the DSD's key slots, and how many
    # slots there are depends on the API version the *caller* is serving —
    # the time dimension takes one on 2.2.2 and not on 1.5 (#337). So the
    # version is a parameter here rather than a guess, and the DSD is only
    # needed when an actual key is supplied.
    slot_ids = [] if key in ("", "all") else _resolve_key_slot_ids(registry, dataflow_urn, api)
    component_filter = parse_components(key, {}, slot_ids)

    targets = sorted(binding.dimension_column_map) if component_id == "all" else [component_id]

    held = _held_values(registry, dataflow_urn)
    component_filter = _drop_covering(component_filter, held)

    time_id = _time_dimension_id(registry, dataflow_urn) if time_dimension == "range" else None
    table = located(binding.database_role, binding.table_location)
    key_values: list[KeyValue] = []
    components: list[ComponentValueSet] = []
    for target in targets:
        column = _component_column(binding, target)
        applied = _drop_constraint(component_filter, target) if mode == "available" else component_filter
        if applied.is_wildcard and target in held:
            values = held[target]
        else:
            where, params = applied.to_where_clauses(
                binding.dimension_column_map,
                binding.attribute_column_map,
            )
            values = frozenset(_distinct_values(conn, table, column, where=where, params=params))
        # A dimension is a region's key; an attribute is one of its component
        # value sets (2.1 `Attribute`, 3.x `Component`). Both were keys until
        # #433 gave components a field, so an attribute read as a dimension.
        if target in binding.dimension_column_map:
            key_values.append(_key_value(target, values, time_id))
        else:
            components.append(ComponentValueSet(component_id=target, values=values))

    region = CubeRegion(include=True, keys=tuple(key_values), components=tuple(components))
    attachment = ConstraintAttachment(dataflows=(Ref[Dataflow].unresolved(dataflow_urn),))
    return DataConstraint(
        id=constraint_id if constraint_id is not None else dataflow_urn.id,
        maintainer=dataflow_urn.agency,
        version=version,
        name=name,
        constraint_type=ConstraintType.ACTUAL,
        attachment=attachment,
        regions=(region,),
    )


def _resolve_key_slot_ids(registry: EditionRegistry, dataflow_urn: SdmxUrn[Dataflow], api: ApiVersion) -> list[str]:
    """Resolve the DSD's key-dimension ids (SDMX-key order) for the dataflow.

    Mirrors the dataflow → DSD hop `LocalRegistry.dsd` does for labelling: fetch the
    flow to learn its DSD URN, then fetch the DSD. The key order (regular
    dimensions then time) is what the dotted key is positional over.
    """
    flow = registry.get(dataflow_urn)
    structure = flow.structure if isinstance(flow, Dataflow) else None
    if not isinstance(structure, Ref):
        # Unresolvable DSD is a data/registry condition, not a caller type bug.
        err = f"Cannot resolve a DSD for {dataflow_urn}; a dotted key needs the DSD's key-dimension order."
        raise ValueError(err)  # noqa: TRY004
    dsd = registry.get(structure.urn)
    if not isinstance(dsd, DataStructure):
        err = f"Structure {structure.urn} for {dataflow_urn} did not resolve to a DataStructure."
        raise ValueError(err)  # noqa: TRY004
    return dsd.key_slot_ids(api)


def _check_time_dimension(time_dimension: str) -> None:
    """Refuse a ``time_dimension`` other than ``"range"`` or ``"values"``.

    Raises:
        ValueError: If ``time_dimension`` is unrecognised.
    """
    if time_dimension not in ("range", "values"):
        err = f"Unrecognised time_dimension {time_dimension!r}; expected 'range' or 'values'."
        raise ValueError(err)


def _time_dimension_id(registry: EditionRegistry, dataflow_urn: SdmxUrn[Dataflow]) -> str | None:
    """The id of the dataflow's DSD's time dimension; None without a DSD in the registry or a time dimension in it."""
    flow = registry.get(dataflow_urn)
    structure = flow.structure if isinstance(flow, Dataflow) else None
    if not isinstance(structure, Ref):
        return None
    dsd = registry.get(structure.urn)
    if not isinstance(dsd, DataStructure) or dsd.time_dimension is None:
        return None
    return dsd.time_dimension.id


def _key_value(dimension_id: str, values: frozenset[str], time_id: str | None) -> KeyValue:
    """One dimension's `KeyValue`: the `TimeRange` its periods cover for the time dimension, else its values.

    The time dimension keeps its values when `period_range` gives no range:
    it holds no periods, or a period whose bounds cannot be read.
    """
    if dimension_id == time_id:
        time_range = period_range(values)
        if time_range is not None:
            return KeyValue(dimension_id=dimension_id, time_range=time_range)
    return KeyValue(dimension_id=dimension_id, values=values)


def _component_column(binding: DataflowBinding, component_id: str) -> str:
    """Physical column for ``component_id`` — dimension first, then attribute."""
    column = binding.dimension_column_map.get(component_id) or binding.attribute_column_map.get(component_id)
    if column is None:
        err = (
            f"Component {component_id!r} is not a dimension or attribute column on the "
            f"table bound to this dataflow (dimensions: {sorted(binding.dimension_column_map)}, "
            f"attributes: {sorted(binding.attribute_column_map)})."
        )
        raise KeyError(err)
    return column


def _held_values(registry: EditionRegistry, dataflow_urn: SdmxUrn[Dataflow]) -> dict[str, frozenset[str]]:
    """The values each dimension holds, from the edition's stored availability; empty when it has none.

    Only a sealed edition's stored availability describes its data. A
    `LocalRegistry` started with ``build(previous=)`` holds the previous
    edition's availability until `seal` replaces it, while its
    lake may already hold other rows, so it reads as none here and every
    component scans.
    """
    if isinstance(registry, LocalRegistry):
        return {}
    stored = registry.availability(dataflow_urn)
    if stored is None:
        return {}
    return {kv.dimension_id: kv.codes() for kv in stored.region.keys}


def _drop_covering(component_filter: ComponentFilter, held: dict[str, frozenset[str]]) -> ComponentFilter:
    """Return a copy of ``component_filter`` without each dimension whose selection covers every value it holds.

    Such a selection excludes no row, so the scan's answer is the same
    without it. A dimension selected by a `SqlRelation`, or absent
    from ``held``, keeps its constraint.
    """
    dimensions = [
        None if isinstance(selected, list) and dim_id in held and held[dim_id] <= set(selected) else selected
        for dim_id, selected in zip(component_filter.dimension_ids, component_filter.dimensions, strict=True)
    ]
    return ComponentFilter(
        dimension_ids=component_filter.dimension_ids,
        dimensions=dimensions,
        attributes=component_filter.attributes,
    )


def _drop_constraint(component_filter: ComponentFilter, component_id: str) -> ComponentFilter:
    """Return a copy of ``component_filter`` with ``component_id``'s constraint removed.

    This is the ``mode="available"`` transform: the enumerated component's
    own value restriction is lifted so the scan reports every value that
    would keep the cube non-empty. A no-op when the component is unconstrained
    (wildcard segment, absent from the key, or an unfiltered attribute).
    """
    dimensions = list(component_filter.dimensions)
    if component_id in component_filter.dimension_ids:
        dimensions[component_filter.dimension_ids.index(component_id)] = None
    attributes = {k: v for k, v in component_filter.attributes.items() if k != component_id}
    return ComponentFilter(
        dimension_ids=component_filter.dimension_ids,
        dimensions=dimensions,
        attributes=attributes,
    )


def _distinct_values(
    conn: duckdb.DuckDBPyConnection,
    table: str,
    column: str,
    *,
    where: list[str] | None = None,
    params: list[object] | None = None,
) -> list[str]:
    """Return the distinct non-null values of ``column`` in ``table`` as strings.

    ``table`` is ``<database_role>.<schema>.<table>`` taken verbatim from
    the binding (an sdmxlib-controlled artefact); ``column`` comes from the
    binding's column map and is double-quoted as a DuckDB identifier. Optional
    ``where`` fragments (from `ComponentFilter.to_where_clauses`) are
    ANDed in, with their values bound via ``params``. Values are coerced to
    ``str`` since SDMX codes are textual even when the lake stores them as
    another type.
    """
    quoted = _quote_identifier(column)
    clauses = [*(where or []), f"{quoted} IS NOT NULL"]
    sql = f"SELECT DISTINCT {quoted} AS v FROM {table} WHERE {' AND '.join(clauses)} ORDER BY v"
    rows = conn.execute(sql, params or []).fetchall()
    return [str(row[0]) for row in rows]


def _quote_identifier(name: str) -> str:
    """Quote ``name`` as a DuckDB identifier (double quotes, doubled internals)."""
    escaped = name.replace('"', '""')
    return f'"{escaped}"'
