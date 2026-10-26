"""DataflowBinding — first-class artefact linking a Dataflow to a data table.

A binding records the mapping between a Dataflow's structural definition
(DSD dimensions, measures, attributes) and a concrete data location, plus
provenance for staleness detection. Federated catalogs use bindings to
answer "where do the observations for this dataflow live?" without the
caller needing to know about the underlying storage.

The binding artefact is local-store-owned: a Dataflow may live in a
remote registry (FMR, ESTAT, ECB), but the binding to a Parquet/DuckLake
table lives in the local registry that knows about the data.

The bundle architecture splits the physical address of a table into
``database_role`` (ATTACH alias of the catalog database) and
``table_location`` (``schema.table`` inside that database). The resolver
re-joins them at query time as ``<database_role>.<table_location>``;
this keeps bindings portable across bundles that may attach the lake
under different aliases.

Example:
    now = datetime.now(UTC)
    binding = DataflowBinding(
        dataflow=Ref[Dataflow].unresolved(dataflow_urn),
        database_role="lake",
        table_location="census_2021.observations",
        dimension_column_map={"REF_AREA": "REF_AREA", "TIME_PERIOD": "TIME_PERIOD"},
        measure_column_map={"OBS_COUNT": "OBS_COUNT"},
        attribute_column_map={"GEO_NAME": "GEO_NAME"},
        structural_version="STATCAN:CENSUS_DSD(2021)",
        source_registry="local",
        materialized_at=now,
        bound_at=now,
    )
    local.add(binding)
"""

from datetime import UTC, datetime
from typing import ClassVar, Self

from attrs import field, frozen

from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.ref import Ref, refuse_urn_text, to_ref
from sdmxlib.model.registry import SupportsGet
from sdmxlib.model.urn import ParsesUrn, SdmxUrn


def _require_aware(value: datetime) -> datetime:
    """Reject naive datetimes — bindings must record a real instant.

    Storage normalises to UTC on write, so callers may pass any tz-aware
    datetime; only ``tzinfo=None`` is rejected (per CLAUDE.md no-default-
    tz: the call site decides UTC, not us).
    """
    if value.tzinfo is None:
        msg = "DataflowBinding timestamps must be tz-aware — pass datetime(..., tzinfo=UTC) or datetime.now(UTC)"
        raise ValueError(msg)
    return value


@frozen(kw_only=True, unsafe_hash=False)
class DataflowBinding(ParsesUrn):
    """Binding between an SDMX dataflow and a concrete data table.

    Attributes:
        dataflow: Ref to the bound dataflow (may be remote).
        database_role: ATTACH alias of the catalog database that owns the
            table. Defaults to ``"lake"`` to match the conventional
            bundle layout (``meta.duckdb`` + ``lake.ducklake`` attached
            as ``lake``). Combined with `table_location` at query
            time as ``<database_role>.<table_location>``.
        table_location: ``schema.table`` inside the database identified
            by `database_role`. Does NOT include the database
            alias; the resolver prepends it. For raw Parquet stores
            without an ATTACH this can be a path or URI.
        dimension_column_map: SDMX dimension id → table column name.
            Identity mapping is the common case.
        measure_column_map: SDMX measure id → table column name.
        attribute_column_map: SDMX attribute id → table column name.
        structural_version: ``agency:id(version)`` tag of the DSD/codelists
            the data was written against. Comparing against the live
            registry detects "structural side has moved on, data is stale"
            without re-reading the table.
        source_registry: Provenance label for the structural side
            (e.g. ``"local"``, ``"ESTAT"``, ``"ECB"``).
        materialized_at: When the data was last refreshed. Must be
            tz-aware; storage normalises to UTC.
        bound_at: When this binding record was created. Must be
            tz-aware; storage normalises to UTC.
        series_attributes_location: Optional sidecar table for series-level
            attributes that are constant across (REF_AREA, TIME_PERIOD)
            slices and would otherwise bloat the fact table 5x if
            denormalised in. Same shape as ``table_location``.
    """

    sdmx_package: ClassVar[str] = "binding"
    sdmx_class: ClassVar[str] = "DataflowBinding"

    dataflow: Ref[Dataflow] = field(converter=to_ref, validator=refuse_urn_text)
    table_location: str
    database_role: str = "lake"
    dimension_column_map: dict[str, str] = field(factory=dict)
    measure_column_map: dict[str, str] = field(factory=dict)
    attribute_column_map: dict[str, str] = field(factory=dict)
    structural_version: str
    source_registry: str
    materialized_at: datetime = field(converter=_require_aware)
    bound_at: datetime = field(converter=_require_aware)
    series_attributes_location: str | None = None

    # ── SupportsUrn conformance — derived from the bound dataflow's identity ──

    @property
    def agency_id(self) -> str:
        """Agency of the bound dataflow."""
        return self.dataflow.urn.agency

    @property
    def id(self) -> str:
        """Id of the bound dataflow (the binding shares the dataflow's identity)."""
        return self.dataflow.urn.id

    @property
    def version(self) -> str:
        """Version of the bound dataflow."""
        return self.dataflow.urn.version

    # ── Typed URN helper ─────────────────────────────────────────────────

    def component_of(self, column: str) -> str | None:
        """The SDMX component id a physical column carries, or `None`.

        The inverse of the three column maps, and the only route from a
        compiled predicate — which carries a physical name and nothing else —
        back to the component a DSD talks about.

        First mapping wins if two ids share a column, which is malformed rather
        than meaningful: `check_binding_drift` is where that should surface,
        not here.

        Args:
            column: A physical column name, as it appears on the table.

        Returns:
            The component id, or `None` when this binding maps no such column.
        """
        for mapping in (self.dimension_column_map, self.measure_column_map, self.attribute_column_map):
            for component_id, mapped in mapping.items():
                if mapped == column:
                    return component_id
        return None

    @classmethod
    def urn_for(
        cls,
        *,
        agency: str,
        id: str,
        version: str = "1.0",
    ) -> SdmxUrn[Self]:
        """Construct a typed ``SdmxUrn[DataflowBinding]``.

        The binding's URN mirrors the bound dataflow's identity — one
        binding per dataflow per version.
        """
        return SdmxUrn.make(
            cls,
            package=cls.sdmx_package,
            artefact_class=cls.sdmx_class,
            agency=agency,
            id=id,
            version=version,
        )

    # ── Convenience constructor ──────────────────────────────────────────

    @classmethod
    def from_dsd(
        cls,
        dsd: DataStructure,
        *,
        dataflow: Ref[Dataflow],
        schema: str,
        table: str,
        source_registry: str = "local",
        materialized_at: datetime | None = None,
        bound_at: datetime | None = None,
        database_role: str = "lake",
        series_attributes_location: str | None = None,
    ) -> DataflowBinding:
        """Build a DataflowBinding from a DSD with identity column mapping.

        Derives ``dimension_column_map`` / ``measure_column_map`` /
        ``attribute_column_map`` from the DSD's components (SDMX id →
        SDMX id), pins ``structural_version`` as
        ``f"{dsd.agency_id}:{dsd.id}({dsd.version})"``, and addresses the
        target table via ``database_role`` + ``schema.table`` (no
        database alias prefix is written into ``table_location``).

        ``materialized_at`` and ``bound_at`` default to ``datetime.now(UTC)``.

        Args:
            dsd: The `DataStructure` whose components define the
                column maps.
            dataflow: Ref to the bound dataflow.
            schema: Schema portion of ``table_location``.
            table: Table portion of ``table_location``.
            source_registry: Provenance label for the structural side.
            materialized_at: When the data was last refreshed. Defaults
                to ``datetime.now(UTC)``.
            bound_at: When this binding record was created. Defaults to
                ``datetime.now(UTC)``.
            database_role: ATTACH alias of the database that owns the
                table. Defaults to ``"lake"``.
            series_attributes_location: Optional sidecar table for
                series-level attributes (same shape as
                ``schema.table`` — no database alias prefix).

        Returns:
            A `DataflowBinding` ready to be added to a registry.
        """
        now = datetime.now(UTC)
        # Every key slot, in key order: a 2.1 MeasureDimension is a dimension
        # of the key and a column of the table, and `dimensions` leaves it out.
        dim_map = {slot: slot for slot in dsd.key_slot_ids("3.0")}
        measure_map = {m.id: m.id for m in dsd.measures}
        attr_map = {a.id: a.id for a in dsd.attributes}
        structural_version = f"{dsd.agency_id}:{dsd.id}({dsd.version})"
        return cls(
            dataflow=dataflow,
            database_role=database_role,
            table_location=f"{schema}.{table}",
            dimension_column_map=dim_map,
            measure_column_map=measure_map,
            attribute_column_map=attr_map,
            structural_version=structural_version,
            source_registry=source_registry,
            materialized_at=materialized_at if materialized_at is not None else now,
            bound_at=bound_at if bound_at is not None else now,
            series_attributes_location=series_attributes_location,
        )


# ── Drift detection ─────────────────────────────────────────────────────


class DriftError(Exception):
    """Raised by ``Registry.validate_binding()`` when any drift is detected.

    ``issues`` carries every drift found — not just the first — so a single
    catch surfaces the full set. For inspection without raising (the
    expected pattern when you want to display drift in a UI or log it),
    use ``Registry.check_binding_drift()`` instead.
    """

    issues: list[DriftIssue]

    def __init__(self, issues: list[DriftIssue]) -> None:
        self.issues = issues
        super().__init__(self._format(issues))

    @staticmethod
    def _format(issues: list[DriftIssue]) -> str:
        n = len(issues)
        head_count = 5
        head = "\n".join(f"  {i.kind}: {i.description}" for i in issues[:head_count])
        suffix = f"\n  ... and {n - head_count} more" if n > head_count else ""
        plural = "" if n == 1 else "s"
        return f"{n} drift issue{plural}:\n{head}{suffix}"


@frozen
class DriftIssue:
    """A single drift between a binding's pinned structural state and the live registry.

    Attributes:
        kind: Stable identifier for the kind of drift. v1 vocabulary:
            ``"dataflow_missing"`` (the dataflow has disappeared from
            the registry), ``"dsd_unresolvable"`` (the dataflow exists
            but its DSD reference can't be resolved), and
            ``"structural_version_drift"`` (the binding's pinned DSD
            version differs from the current one).
        binding_urn: URN of the binding the issue relates to.
        description: Human-readable explanation including before/after
            values where relevant.
    """

    kind: str
    binding_urn: str
    description: str


def _validate_binding(binding: DataflowBinding, registry: SupportsGet) -> list[DriftIssue]:
    """Internal helper: check a binding against a registry for structural drift.

    Called by the public `validate_binding` method on registries
    (e.g. `InMemoryRegistry.validate_binding`). ``registry`` needs only
    ``get(urn) -> artefact | None``, so it is typed to `SupportsGet` rather
    than the wider `RegistryReader`.
    """
    binding_urn = str(
        DataflowBinding.urn_for(
            agency=binding.agency_id,
            id=binding.id,
            version=binding.version,
        ),
    )
    issues: list[DriftIssue] = []

    flow = registry.get(binding.dataflow.urn)
    if flow is None:
        issues.append(
            DriftIssue(
                kind="dataflow_missing",
                binding_urn=binding_urn,
                description=(
                    f"Dataflow {binding.dataflow.urn} not found in the registry. "
                    "The bound dataflow may have been retired or routed to a different source."
                ),
            ),
        )
        return issues

    if not isinstance(flow, Dataflow) or flow.structure is None:
        issues.append(
            DriftIssue(
                kind="dsd_unresolvable",
                binding_urn=binding_urn,
                description=(
                    f"Dataflow {binding.dataflow.urn} has no resolvable DSD reference; cannot validate structural pin."
                ),
            ),
        )
        return issues

    structure_ref = flow.structure
    if not isinstance(structure_ref, Ref):
        issues.append(
            DriftIssue(
                kind="dsd_unresolvable",
                binding_urn=binding_urn,
                description=(
                    f"Dataflow {binding.dataflow.urn} structure is not a Ref; cannot determine the current DSD URN."
                ),
            ),
        )
        return issues

    current_dsd_urn = structure_ref.urn
    current_version = f"{current_dsd_urn.agency}:{current_dsd_urn.id}({current_dsd_urn.version})"

    if current_version != binding.structural_version:
        issues.append(
            DriftIssue(
                kind="structural_version_drift",
                binding_urn=binding_urn,
                description=(
                    f"Binding pinned to {binding.structural_version}, "
                    f"current DSD URN is {current_version}. "
                    "Re-bind against the current DSD or accept stale labels."
                ),
            ),
        )

    return issues


@frozen
class DataflowEntry:
    """One row of a `browse` result.

    Attributes:
        dataflow_urn: URN of the bound dataflow.
        binding: The `DataflowBinding` that links the dataflow to
            its observation table. Always present for v1 (browse only
            enumerates locally-bound dataflows).
        name: The dataflow's `InternationalString` name when the
            structural side resolves the dataflow; ``None`` otherwise.
        source_registry: Provenance label from the binding —
            ``"local"`` for locally-authored, ``"ESTAT"`` /
            ``"ECB"`` / ... for remote-cached.
    """

    dataflow_urn: SdmxUrn[Dataflow]
    binding: DataflowBinding
    name: InternationalString | None
    source_registry: str | None
