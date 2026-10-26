"""Tests for DataflowBinding — model + storage round-trip."""

from datetime import UTC, datetime

import attrs
import pytest

import sdmxlib as sl
from sdmxlib.local import LocalRegistry
from sdmxlib.model.binding import DataflowBinding
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.ref import Ref

pytestmark = pytest.mark.unit


def _binding(**overrides: object) -> DataflowBinding:
    defaults: dict[str, object] = {
        "dataflow": Ref[Dataflow].unresolved(Dataflow.urn_for(agency="STATCAN", id="CENSUS_PROFILE", version="2021")),
        "table_location": "lake.census_2021.observations",
        "dimension_column_map": {"REF_AREA": "REF_AREA", "TIME_PERIOD": "TIME_PERIOD"},
        "measure_column_map": {"OBS_COUNT": "OBS_COUNT"},
        "attribute_column_map": {"GEO_NAME": "GEO_NAME"},
        "structural_version": "STATCAN:CENSUS_DSD(2021)",
        "source_registry": "local",
        "materialized_at": datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
        "bound_at": datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    }
    defaults.update(overrides)
    return DataflowBinding(**defaults)  # type: ignore[arg-type]


# ── Model shape ─────────────────────────────────────────────────────────


def test_agency_id_derived_from_dataflow():
    b = _binding()
    assert b.agency_id == "STATCAN"


def test_id_derived_from_dataflow():
    b = _binding()
    assert b.id == "CENSUS_PROFILE"


def test_version_derived_from_dataflow():
    b = _binding()
    assert b.version == "2021"


def test_series_attributes_location_defaults_to_none():
    b = _binding()
    assert b.series_attributes_location is None


def test_classvar_sdmx_metadata():
    assert DataflowBinding.sdmx_package == "binding"
    assert DataflowBinding.sdmx_class == "DataflowBinding"


def test_urn_classmethod():
    urn = DataflowBinding.urn_for(agency="STATCAN", id="X", version="2021")
    assert str(urn) == "urn:sdmx:org.sdmx.infomodel.binding.DataflowBinding=STATCAN:X(2021)"


def test_a_binding_is_frozen():
    """Frozen like every artefact (#492): a changed binding is an ``attrs.evolve`` copy."""
    b = _binding()
    field = "table_location"  # by name: the checker already refuses the assignment statically
    with pytest.raises(attrs.exceptions.FrozenInstanceError):
        setattr(b, field, "lake.other.observations")
    assert attrs.evolve(b, table_location="lake.other.observations").table_location == "lake.other.observations"


# ── Storage round-trip ──────────────────────────────────────────────────


def test_round_trip_through_local_registry():
    binding = _binding()
    with LocalRegistry(":memory:") as local:
        local.add(binding)
        urn = DataflowBinding.urn_for(agency="STATCAN", id="CENSUS_PROFILE", version="2021")
        result = local.get(urn)
        assert result is not None
        assert result.table_location == binding.table_location
        assert result.dimension_column_map == binding.dimension_column_map
        assert result.measure_column_map == binding.measure_column_map
        assert result.attribute_column_map == binding.attribute_column_map
        assert result.structural_version == binding.structural_version
        assert result.source_registry == binding.source_registry
        assert result.materialized_at == binding.materialized_at
        assert result.bound_at == binding.bound_at
        assert result.series_attributes_location is None


def test_round_trip_with_series_attributes_location():
    binding = _binding(series_attributes_location="lake.census_2021.geography_attributes")
    with LocalRegistry(":memory:") as local:
        local.add(binding)
        urn = DataflowBinding.urn_for(agency="STATCAN", id="CENSUS_PROFILE", version="2021")
        result = local.get(urn)
        assert result is not None
        assert result.series_attributes_location == "lake.census_2021.geography_attributes"


def test_dataflow_ref_preserved_with_urn():
    binding = _binding()
    with LocalRegistry(":memory:") as local:
        local.add(binding)
        urn = DataflowBinding.urn_for(agency="STATCAN", id="CENSUS_PROFILE", version="2021")
        result = local.get(urn)
        assert result is not None
        # The dataflow ref carries the URN; resolution needs the dataflow to be in the
        # store too, which we don't put here.
        assert str(result.dataflow.urn) == str(binding.dataflow.urn)


def test_drop_binding():
    binding = _binding()
    with LocalRegistry(":memory:") as local:
        local.add(binding)
        urn = DataflowBinding.urn_for(agency="STATCAN", id="CENSUS_PROFILE", version="2021")
        assert urn in local
        local.drop(urn)
        assert urn not in local
        assert local.get(urn) is None


def test_replace_binding_overwrites_extension_table():
    """Re-adding a binding with the same URN replaces, not appends."""
    binding_v1 = _binding(table_location="v1.observations")
    binding_v2 = _binding(table_location="v2.observations")
    with LocalRegistry(":memory:") as local:
        local.add(binding_v1)
        local.add(binding_v2)
        urn = DataflowBinding.urn_for(agency="STATCAN", id="CENSUS_PROFILE", version="2021")
        result = local.get(urn)
        assert result is not None
        assert result.table_location == "v2.observations"
        assert result.database_role == "lake"


def test_get_all_dataflow_bindings():
    b1 = _binding()
    b2 = _binding(
        dataflow=Ref[Dataflow].unresolved(Dataflow.urn_for(agency="STATCAN", id="OTHER", version="2021")),
        table_location="other.observations",
    )
    with LocalRegistry(":memory:") as local:
        local.add(b1)
        local.add(b2)
        bindings = local.get_all(DataflowBinding)
        assert len(bindings) == 2
        urns = {b.dataflow.urn.id for b in bindings}
        assert urns == {"CENSUS_PROFILE", "OTHER"}


def test_binding_is_first_class_in_sl_namespace():
    """Top-level export."""
    assert sl.DataflowBinding is DataflowBinding


# ── TZ-aware timestamp contract (#92) ───────────────────────────────────


def test_naive_materialized_at_rejected():
    """Bindings must record a real instant, not a wall-clock — naive
    datetimes are ambiguous and silently mis-comparing them is a hazard.
    """
    with pytest.raises(ValueError, match="tz-aware"):
        _binding(materialized_at=datetime(2026, 4, 30, 12, 0))


def test_naive_bound_at_rejected():
    with pytest.raises(ValueError, match="tz-aware"):
        _binding(bound_at=datetime(2026, 4, 30, 12, 0))


def test_non_utc_tz_normalized_to_utc_on_round_trip():
    """An eastern offset round-trips as the same instant in UTC — storage
    is naive-UTC under the hood but the model contract is tz-aware."""
    from datetime import timedelta, timezone

    east2 = timezone(timedelta(hours=2))
    b = _binding(materialized_at=datetime(2026, 4, 30, 14, 0, tzinfo=east2))
    with LocalRegistry(":memory:") as local:
        local.add(b)
        loaded = local.get_all(DataflowBinding)[0]
    assert loaded.materialized_at.tzinfo == UTC
    assert loaded.materialized_at == datetime(2026, 4, 30, 12, 0, tzinfo=UTC)
