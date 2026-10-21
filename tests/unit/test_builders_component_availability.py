"""Tests for builders.contentconstraint.build_component_availability."""

from datetime import UTC, datetime

import attrs
import pytest

from sdmxlib.builders.contentconstraint import (
    build_component_availability,
    build_dataflow_constraint,
)
from sdmxlib.local import LocalRegistry
from sdmxlib.model.binding import DataflowBinding
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.constraint import ConstraintType
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure, Dimension, Measure
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.ref import Ref
from tests.support.factories import build_observations, concept_ref

pytestmark = pytest.mark.unit

_FLOW_URN = Dataflow.urn_for(agency="TEST", id="DF_TEST", version="1.0")


def _build_structural(local: LocalRegistry) -> None:
    cl = Codelist(
        id="CL_FREQ",
        maintainer="TEST",
        version="1.0",
        name=InternationalString(en="Frequency"),
        codes=[Code(name={"en": "A"}, id="A"), Code(name={"en": "Q"}, id="Q"), Code(name={"en": "M"}, id="M")],
    )
    cl_urn = Codelist.urn_for(agency="TEST", id="CL_FREQ", version="1.0")
    dsd = DataStructure(
        id="DSD_TEST",
        maintainer="TEST",
        version="1.0",
        name=InternationalString(en="Test DSD"),
        # Key order is [FREQ, REF_AREA] — the dotted key is positional over this.
        dimensions=[
            Dimension(id="FREQ", concept=concept_ref("FREQ"), representation=Ref[Codelist].unresolved(cl_urn)),
            Dimension(id="REF_AREA", concept=concept_ref("REF_AREA")),
        ],
        measures=[Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )
    dsd_urn = DataStructure.urn_for(agency="TEST", id="DSD_TEST", version="1.0")
    flow = Dataflow(
        id="DF_TEST",
        maintainer="TEST",
        version="1.0",
        name=InternationalString(en="Test Dataflow"),
        structure=Ref[DataStructure].unresolved(dsd_urn),
    )
    local.add(cl)
    local.add(dsd)
    local.add(flow)


def _build_binding(local: LocalRegistry) -> DataflowBinding:
    binding = DataflowBinding(
        dataflow=Ref[Dataflow].unresolved(_FLOW_URN),
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


def _make_catalog(local: LocalRegistry) -> LocalRegistry:
    _build_structural(local)
    build_observations(local)
    _build_binding(local)
    return local


def _values_by_dim(cc) -> dict[str, frozenset[str]]:
    return {kv.dimension_id: kv.codes() for kv in cc.regions[0].keys}


def test_key_all_component_all_matches_build_dataflow_constraint():
    with LocalRegistry(":memory:") as local:
        cat = _make_catalog(local)
        component = build_component_availability(
            cat, _FLOW_URN, key="all", component_id="all", mode="exact", api="2.1", name={"en": "Availability"}
        )
        flow_wide = build_dataflow_constraint(cat, _FLOW_URN, name={"en": "Availability"})
        assert _values_by_dim(component) == _values_by_dim(flow_wide)
        assert component.constraint_type is ConstraintType.ACTUAL


def test_exact_specific_component_applies_full_key():
    with LocalRegistry(":memory:") as local:
        cat = _make_catalog(local)
        # key FREQ=A, REF_AREA=CA; enumerate REF_AREA under the full filter.
        cc = build_component_availability(
            cat, _FLOW_URN, key="A.CA", component_id="REF_AREA", mode="exact", api="2.1", name={"en": "Availability"}
        )
        assert [kv.dimension_id for kv in cc.regions[0].keys] == ["REF_AREA"]
        assert _values_by_dim(cc)["REF_AREA"] == frozenset({"CA"})


def test_available_drops_enumerated_component_constraint():
    with LocalRegistry(":memory:") as local:
        cat = _make_catalog(local)
        # Same key, but mode=available drops REF_AREA's own constraint, so the
        # filter is just FREQ=A — whose rows carry REF_AREA in {CA, US}.
        cc = build_component_availability(
            cat,
            _FLOW_URN,
            key="A.CA",
            component_id="REF_AREA",
            mode="available",
            api="2.1",
            name={"en": "Availability"},
        )
        assert _values_by_dim(cc)["REF_AREA"] == frozenset({"CA", "US"})


def test_available_keeps_other_components_constraints():
    with LocalRegistry(":memory:") as local:
        cat = _make_catalog(local)
        # Enumerate FREQ with REF_AREA=US fixed; available drops FREQ's own
        # constraint (there is none here) but keeps REF_AREA=US.
        cc = build_component_availability(
            cat, _FLOW_URN, key=".US", component_id="FREQ", mode="available", api="2.1", name={"en": "Availability"}
        )
        # REF_AREA=US rows have FREQ in {A, M}.
        assert _values_by_dim(cc)["FREQ"] == frozenset({"A", "M"})


def test_component_all_with_partial_key():
    with LocalRegistry(":memory:") as local:
        cat = _make_catalog(local)
        # key FREQ=A, REF_AREA wildcard; enumerate every dimension (exact).
        cc = build_component_availability(
            cat, _FLOW_URN, key="A.", component_id="all", mode="exact", api="2.1", name={"en": "Availability"}
        )
        # Targets are sorted alphabetically.
        assert [kv.dimension_id for kv in cc.regions[0].keys] == ["FREQ", "REF_AREA"]
        by_dim = _values_by_dim(cc)
        assert by_dim["FREQ"] == frozenset({"A"})
        assert by_dim["REF_AREA"] == frozenset({"CA", "US"})


def test_empty_result_yields_empty_value_set():
    with LocalRegistry(":memory:") as local:
        cat = _make_catalog(local)
        # FREQ=Z matches no rows.
        cc = build_component_availability(
            cat, _FLOW_URN, key="Z.CA", component_id="REF_AREA", mode="exact", api="2.1", name={"en": "Availability"}
        )
        assert _values_by_dim(cc)["REF_AREA"] == frozenset()


def test_deterministic_across_calls():
    with LocalRegistry(":memory:") as local:
        cat = _make_catalog(local)
        first = build_component_availability(
            cat, _FLOW_URN, key="A.", component_id="all", api="2.1", name={"en": "Availability"}
        )
        second = build_component_availability(
            cat, _FLOW_URN, key="A.", component_id="all", api="2.1", name={"en": "Availability"}
        )
        assert _values_by_dim(first) == _values_by_dim(second)


def test_unknown_mode_raises():
    with LocalRegistry(":memory:") as local:
        cat = _make_catalog(local)
        with pytest.raises(ValueError, match="mode"):
            build_component_availability(
                cat,
                _FLOW_URN,
                key="all",
                component_id="REF_AREA",
                mode="fuzzy",  # type: ignore[arg-type]
                api="2.1",
                name={"en": "Availability"},
            )


def test_unknown_component_raises_keyerror():
    with LocalRegistry(":memory:") as local:
        cat = _make_catalog(local)
        with pytest.raises(KeyError):
            build_component_availability(
                cat, _FLOW_URN, key="all", component_id="NOPE", api="2.1", name={"en": "Availability"}
            )


def test_key_segment_mismatch_raises():
    with LocalRegistry(":memory:") as local:
        cat = _make_catalog(local)
        # DSD has 2 key dimensions; a 3-segment key is invalid.
        with pytest.raises(ValueError, match="segment"):
            build_component_availability(
                cat, _FLOW_URN, key="A.CA.X", component_id="REF_AREA", api="2.1", name={"en": "Availability"}
            )


def test_missing_binding_raises_keyerror():
    with LocalRegistry(":memory:") as local:
        _build_structural(local)
        cat = local
        with pytest.raises(KeyError):
            build_component_availability(
                cat, _FLOW_URN, key="all", component_id="all", api="2.1", name={"en": "Availability"}
            )


def test_an_attribute_target_is_a_component_value_set_not_a_key():
    """An attribute is not a dimension, so it does not belong in `keys` (#433).

    The bound table has no attribute column of its own here, so the attribute
    is bound to the `REF_AREA` column: what is under test is where the values
    land, not what they are.
    """
    with LocalRegistry(":memory:") as local:
        _build_structural(local)
        build_observations(local)
        binding = _build_binding(local)
        local.add(attrs.evolve(binding, attribute_column_map={"UNIT_MULT": "REF_AREA"}))
        cc = build_component_availability(
            local, _FLOW_URN, key="all", component_id="UNIT_MULT", mode="exact", api="2.1", name={"en": "Availability"}
        )
    (region,) = cc.regions
    assert region.keys == ()
    assert [(c.component_id, c.codes()) for c in region.components] == [("UNIT_MULT", frozenset({"CA", "US"}))]
