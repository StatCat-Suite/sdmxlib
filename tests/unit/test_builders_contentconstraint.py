"""Tests for sdmxlib.builders.contentconstraint.build_dataflow_constraint."""

from datetime import UTC, datetime

import pytest

from sdmxlib.builders.contentconstraint import build_dataflow_constraint
from sdmxlib.local import LocalRegistry
from sdmxlib.model.binding import DataflowBinding
from sdmxlib.model.constraint import ConstraintType
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.ref import Ref
from tests.support.factories import build_observations, federated_catalog

pytestmark = pytest.mark.unit


def _build_binding(local: LocalRegistry) -> DataflowBinding:
    flow_urn = Dataflow.urn_for(agency="TEST", id="DF_TEST", version="1.0")
    binding = DataflowBinding(
        dataflow=Ref[Dataflow].unresolved(flow_urn),
        database_role="memory",
        table_location="data.observations",
        partition_layout=(),
        dimension_column_map={"FREQ": "FREQ", "REF_AREA": "REF_AREA"},
        measure_column_map={"OBS_VALUE": "OBS_VALUE"},
        attribute_column_map={},
        structural_version="TEST:DSD_TEST(1.0)",
        source_registry="local",
        materialized_at=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
        bound_at=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )
    local.add(binding)
    return binding


def test_builds_actual_constraint_attached_to_dataflow():
    with LocalRegistry(":memory:") as local:
        build_observations(local)
        _build_binding(local)
        cat = federated_catalog(local)
        flow_urn = Dataflow.urn_for(agency="TEST", id="DF_TEST", version="1.0")

        cc = build_dataflow_constraint(cat, flow_urn, name={"en": "Availability"})

        assert cc.constraint_type is ConstraintType.ACTUAL
        assert cc.id == "DF_TEST"
        assert cc.agency_id == "TEST"
        assert cc.version == "1.0"
        assert len(cc.attachment.dataflows) == 1
        assert str(cc.attachment.dataflows[0].urn) == str(flow_urn)


def test_one_inclusive_cuberegion_with_distinct_values_per_dimension():
    with LocalRegistry(":memory:") as local:
        build_observations(local)
        _build_binding(local)
        cat = federated_catalog(local)
        flow_urn = Dataflow.urn_for(agency="TEST", id="DF_TEST", version="1.0")

        cc = build_dataflow_constraint(cat, flow_urn, name={"en": "Availability"})

        assert len(cc.regions) == 1
        region = cc.regions[0]
        assert region.include is True
        # KeyValues are ordered by dimension id (alphabetical).
        assert [kv.dimension_id for kv in region.keys] == ["FREQ", "REF_AREA"]
        by_dim = {kv.dimension_id: kv.codes() for kv in region.keys}
        assert by_dim["FREQ"] == frozenset({"A", "Q", "M"})
        # NULL REF_AREA is excluded — availability lists real values only.
        assert by_dim["REF_AREA"] == frozenset({"CA", "US"})


def test_constraint_id_and_version_overridable():
    with LocalRegistry(":memory:") as local:
        build_observations(local)
        _build_binding(local)
        cat = federated_catalog(local)
        flow_urn = Dataflow.urn_for(agency="TEST", id="DF_TEST", version="1.0")

        cc = build_dataflow_constraint(
            cat, flow_urn, constraint_id="DF_TEST_AVAIL", version="2.0", name={"en": "Availability"}
        )

        assert cc.id == "DF_TEST_AVAIL"
        assert cc.version == "2.0"


def test_missing_binding_raises_keyerror():
    with LocalRegistry(":memory:") as local:
        build_observations(local)
        cat = federated_catalog(local)
        flow_urn = Dataflow.urn_for(agency="TEST", id="DF_TEST", version="1.0")

        with pytest.raises(KeyError):
            build_dataflow_constraint(cat, flow_urn, name={"en": "Availability"})


def test_explicit_conn_argument_is_used():
    with LocalRegistry(":memory:") as local:
        build_observations(local)
        _build_binding(local)
        cat = federated_catalog(local)
        flow_urn = Dataflow.urn_for(agency="TEST", id="DF_TEST", version="1.0")

        cc = build_dataflow_constraint(cat, flow_urn, conn=local.connection, name={"en": "Availability"})

        by_dim = {kv.dimension_id: kv.codes() for kv in cc.regions[0].keys}
        assert by_dim["FREQ"] == frozenset({"A", "Q", "M"})
