"""What a binding's data holds, and the coded values it must not hold (GH-194).

One scan per component column at publish answers two questions:

- **Availability.** The values each dimension holds: the SDMX 3.1
  `AvailabilityConstraint` for the unfiltered query, one cube region, which
  `seal` stores so a server answers ``/availability/<flow>/all/all``
  without scanning the observations (2.6 s on the census edition, 0.25 s read
  back and serialised).
- **Conformance.** Every value in a column a codelist or value list enumerates
  is one of its codes. A value that is not, such as an empty string standing
  for "no value", is a `NonCode`: `seal` refuses the edition
  and `check_edition` reports it.
"""

from attrs import frozen

from sdmxlib.builders.contentconstraint import _distinct_values
from sdmxlib.local.registry import EditionRegistry
from sdmxlib.model.binding import DataflowBinding
from sdmxlib.model.constraint import AvailabilityConstraint, ConstraintAttachment, CubeRegion, KeyValue
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import Attribute, Dimension, Measure
from sdmxlib.model.ref import Ref
from sdmxlib.storage._columns import table_columns, value_counts


@frozen
class NonCode:
    """A value a coded column holds that is not a code of its enumeration.

    Attributes:
        dataflow: The bound dataflow's URN.
        component_id: The component the column holds.
        value: The value, as text.
        rows: How many rows hold it.
        enumeration: The URN of the codelist or value list it is not in.
    """

    dataflow: str
    component_id: str
    value: str
    rows: int
    enumeration: str

    def __str__(self) -> str:
        """``<component> holds <value> in <n> rows, not a code of <enumeration>``."""
        return f"{self.component_id} holds {self.value!r} in {self.rows:,} rows, not a code of {self.enumeration}"


def survey(
    registry: EditionRegistry, binding: DataflowBinding, *, obs_count: int
) -> tuple[AvailabilityConstraint, list[NonCode]]:
    """`binding`'s availability, and every value its coded columns hold that is not a code.

    Args:
        registry: An open registry over the edition: the one being
            published, or a published one being checked.
        binding: The binding to scan.
        obs_count: Its observation count, which the caller has counted.

    Returns:
        The availability for the unfiltered query, and the non-code values,
        sorted by component and value.
    """
    located = registry.locate(binding)
    conn = registry.connection
    dataflow_urn = str(binding.dataflow.urn)
    tables = _tables(located)
    columns = {table: table_columns(conn, table) for table in tables}

    def table_of(column: str) -> str | None:
        return next((table for table in tables if column in columns[table]), None)

    dsd = registry.dsd(binding.dataflow)
    enumerated: dict[str, Dimension | Attribute | Measure] = {}
    if dsd is not None:
        for component in (*dsd.dimensions, *dsd.attributes, *dsd.measures):
            if component.is_enumerated:
                enumerated[component.id] = component

    keys: list[KeyValue] = []
    non_codes: list[NonCode] = []
    mapped = {**located.dimension_column_map, **located.attribute_column_map, **located.measure_column_map}
    for component_id, column in sorted(mapped.items()):
        table = table_of(column)
        if table is None:
            continue
        held = _distinct_values(conn, table, column)
        if component_id in located.dimension_column_map:
            keys.append(KeyValue(dimension_id=component_id, values=frozenset(held)))
        component = enumerated.get(component_id)
        codes = component.code_ids() if component is not None else None
        if component is None or codes is None:
            continue
        outside = sorted(set(held) - set(codes))
        if outside:
            enumeration = str(component.representation.urn) if isinstance(component.representation, Ref) else ""
            non_codes += [
                NonCode(dataflow_urn, component_id, value, rows, enumeration)
                for value, rows in value_counts(conn, table, column, outside)
            ]
    flow = Ref[Dataflow].unresolved(binding.dataflow.urn)
    availability = AvailabilityConstraint(
        attachment=ConstraintAttachment(dataflows=(flow,)),
        region=CubeRegion(keys=tuple(keys)),
        obs_count=obs_count,
    )
    return availability, non_codes


def _tables(binding: DataflowBinding) -> list[str]:
    """The tables `binding` reads: its own, then its series-attribute sidecar."""
    main = f"{binding.database_role}.{binding.table_location}"
    sidecar = binding.series_attributes_location
    return [main] if sidecar is None else [main, f"{binding.database_role}.{sidecar}"]
