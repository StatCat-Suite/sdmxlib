"""The constraint builders give the DSD's time dimension as a `TimeRange` (GH-392)."""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal, cast

import pytest

from sdmxlib.builders.contentconstraint import build_component_availability, build_dataflow_constraint
from sdmxlib.formats.sdmx_json_1_0.writer import write_contentconstraint
from sdmxlib.local import LocalRegistry
from sdmxlib.model.binding import DataflowBinding
from sdmxlib.model.constraint import DataConstraint, KeyValue, TimeBound, TimeRange
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure, Dimension, Measure, TimeDimension
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.ref import Ref
from tests.support.factories import concept_ref

pytestmark = pytest.mark.unit

_FLOW_URN = Dataflow.urn_for(agency="TEST", id="DF_TIME", version="1.0")
_DSD_URN = DataStructure.urn_for(agency="TEST", id="DSD_TIME", version="1.0")
_NAME = {"en": "Availability"}

#: ``(FREQ, TIME_PERIOD, OBS_VALUE)``. 2019 and 2020 are A rows only, so a key
#: on Q narrows the range.
_ROWS = [
    ("A", "2019", 1.0),
    ("A", "2020", 2.0),
    ("Q", "2021-Q1", 3.0),
    ("Q", "2021-Q2", 4.0),
]


def _registry(local: LocalRegistry, rows: Sequence[tuple[str, str, float]] = _ROWS, *, with_dsd: bool = True) -> None:
    """A dataflow over ``FREQ`` and the time dimension ``TIME_PERIOD``, bound to ``data.observations``."""
    if with_dsd:
        local.add(
            DataStructure(
                id="DSD_TIME",
                maintainer="TEST",
                version="1.0",
                name=InternationalString(en="Time DSD"),
                dimensions=[Dimension(id="FREQ", concept=concept_ref("FREQ"))],
                time_dimension=TimeDimension(id="TIME_PERIOD", concept=concept_ref("TIME_PERIOD")),
                measures=[Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
            )
        )
        local.add(
            Dataflow(
                id="DF_TIME",
                maintainer="TEST",
                version="1.0",
                name=InternationalString(en="Time flow"),
                structure=Ref[DataStructure].unresolved(_DSD_URN),
            )
        )
    cur = local.connection.cursor()
    cur.execute("CREATE SCHEMA IF NOT EXISTS data")
    cur.execute("CREATE TABLE data.observations (FREQ TEXT, TIME_PERIOD TEXT, OBS_VALUE DOUBLE)")
    cur.executemany("INSERT INTO data.observations VALUES (?, ?, ?)", [list(r) for r in rows])
    local.add(
        DataflowBinding(
            dataflow=Ref[Dataflow].unresolved(_FLOW_URN),
            database_role="memory",
            table_location="data.observations",
            dimension_column_map={"FREQ": "FREQ", "TIME_PERIOD": "TIME_PERIOD"},
            measure_column_map={"OBS_VALUE": "OBS_VALUE"},
            attribute_column_map={},
            structural_version="TEST:DSD_TIME(1.0)",
            source_registry="local",
            materialized_at=datetime(2026, 11, 18, tzinfo=UTC),
            bound_at=datetime(2026, 11, 18, tzinfo=UTC),
        )
    )


def _keys(cc: DataConstraint) -> dict[str, KeyValue]:
    return {kv.dimension_id: kv for kv in cc.regions[0].keys}


def _range(start: str, end: str) -> TimeRange:
    return TimeRange(start=TimeBound(period=start), end=TimeBound(period=end))


def test_dataflow_constraint_gives_the_time_dimension_as_a_range() -> None:
    with LocalRegistry(":memory:") as local:
        _registry(local)
        keys = _keys(build_dataflow_constraint(local, _FLOW_URN, name=_NAME))

    assert keys["TIME_PERIOD"].time_range == _range("2019-01-01T00:00:00", "2021-06-30T23:59:59")
    assert keys["TIME_PERIOD"].codes() == frozenset()
    assert keys["FREQ"].time_range is None
    assert keys["FREQ"].codes() == {"A", "Q"}


def test_dataflow_constraint_keeps_the_periods_when_asked() -> None:
    with LocalRegistry(":memory:") as local:
        _registry(local)
        keys = _keys(build_dataflow_constraint(local, _FLOW_URN, name=_NAME, time_dimension="values"))

    assert keys["TIME_PERIOD"].time_range is None
    assert keys["TIME_PERIOD"].codes() == {"2019", "2020", "2021-Q1", "2021-Q2"}


def test_one_unread_period_keeps_the_whole_list() -> None:
    rows = [*_ROWS, ("A", "2021-01-01/P5Y", 5.0)]
    with LocalRegistry(":memory:") as local:
        _registry(local, rows)
        keys = _keys(build_dataflow_constraint(local, _FLOW_URN, name=_NAME))

    assert keys["TIME_PERIOD"].time_range is None
    assert keys["TIME_PERIOD"].codes() == {"2019", "2020", "2021-Q1", "2021-Q2", "2021-01-01/P5Y"}


def test_without_a_dsd_no_dimension_is_a_range() -> None:
    with LocalRegistry(":memory:") as local:
        _registry(local, with_dsd=False)
        keys = _keys(build_dataflow_constraint(local, _FLOW_URN, name=_NAME))

    assert keys["TIME_PERIOD"].time_range is None
    assert keys["TIME_PERIOD"].codes() == {"2019", "2020", "2021-Q1", "2021-Q2"}


def test_component_availability_ranges_over_the_rows_the_key_selects() -> None:
    with LocalRegistry(":memory:") as local:
        _registry(local)
        everything = _keys(
            build_component_availability(local, _FLOW_URN, name=_NAME, key="all", component_id="all", api="2.1")
        )
        quarterly = _keys(
            build_component_availability(local, _FLOW_URN, name=_NAME, key="Q", component_id="TIME_PERIOD", api="2.1")
        )

    assert everything["TIME_PERIOD"].time_range == _range("2019-01-01T00:00:00", "2021-06-30T23:59:59")
    assert quarterly["TIME_PERIOD"].time_range == _range("2021-01-01T00:00:00", "2021-06-30T23:59:59")


def test_component_availability_keeps_the_periods_when_asked() -> None:
    with LocalRegistry(":memory:") as local:
        _registry(local)
        keys = _keys(
            build_component_availability(
                local, _FLOW_URN, name=_NAME, key="Q", component_id="TIME_PERIOD", api="2.1", time_dimension="values"
            )
        )

    assert keys["TIME_PERIOD"].codes() == {"2021-Q1", "2021-Q2"}


def test_no_rows_leave_an_empty_value_list() -> None:
    with LocalRegistry(":memory:") as local:
        _registry(local)
        keys = _keys(
            build_component_availability(local, _FLOW_URN, name=_NAME, key="M", component_id="TIME_PERIOD", api="2.1")
        )

    assert keys["TIME_PERIOD"].time_range is None
    assert keys["TIME_PERIOD"].codes() == frozenset()


#: A shape the signature does not allow, typed as one it does so the call
#: reaches the runtime check.
_UNKNOWN = cast("Literal['range', 'values']", "list")


def test_an_unknown_time_dimension_shape_is_refused() -> None:
    with LocalRegistry(":memory:") as local:
        _registry(local)
        with pytest.raises(ValueError, match="time_dimension"):
            build_dataflow_constraint(local, _FLOW_URN, name=_NAME, time_dimension=_UNKNOWN)
        with pytest.raises(ValueError, match="time_dimension"):
            build_component_availability(
                local,
                _FLOW_URN,
                name=_NAME,
                key="all",
                component_id="all",
                api="2.1",
                time_dimension=_UNKNOWN,
            )


def test_a_single_annual_period_writes_a_sdmx_json_time_range() -> None:
    """The production case: one annual period, read by the .Stat Data Explorer as ``timeRange``."""
    with LocalRegistry(":memory:") as local:
        _registry(local, [("A", "2021", 1.0)])
        payload = write_contentconstraint(build_dataflow_constraint(local, _FLOW_URN, name=_NAME))

    assert (
        b'{"id":"TIME_PERIOD","timeRange":{"startPeriod":{"period":"2021-01-01T00:00:00"},'
        b'"endPeriod":{"period":"2021-12-31T23:59:59"}}}'
    ) in payload
