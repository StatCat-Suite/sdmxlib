"""Phase F — drift validation + browse-across-federation."""

from datetime import UTC, datetime

import pytest

import sdmxlib as sl
from sdmxlib.local import LocalRegistry
from sdmxlib.model.binding import DataflowBinding, DataflowEntry, DriftIssue
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure, Dimension, Measure
from sdmxlib.model.in_memory_registry import InMemoryRegistry
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.ref import Ref
from tests.support.factories import concept_ref

pytestmark = pytest.mark.unit


# ── Fixtures ────────────────────────────────────────────────────────────


def _dsd(*, agency: str = "TEST", id: str = "DSD_TEST", version: str = "1.0") -> DataStructure:
    return DataStructure(
        id=id,
        maintainer=agency,
        version=version,
        name=InternationalString(en="Test DSD"),
        dimensions=[Dimension(id="REF_AREA", concept=concept_ref("REF_AREA"))],
        measures=[Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )


def _flow(*, agency: str = "TEST", id: str = "DF_TEST", version: str = "1.0", dsd_version: str = "1.0") -> Dataflow:
    dsd_urn = DataStructure.urn_for(agency=agency, id="DSD_TEST", version=dsd_version)
    return Dataflow(
        id=id,
        maintainer=agency,
        version=version,
        name=InternationalString(en="Test Dataflow"),
        structure=Ref[DataStructure].unresolved(dsd_urn),
    )


def _binding(*, structural_version: str = "TEST:DSD_TEST(1.0)") -> DataflowBinding:
    flow_urn = Dataflow.urn_for(agency="TEST", id="DF_TEST", version="1.0")
    return DataflowBinding(
        dataflow=Ref[Dataflow].unresolved(flow_urn),
        table_location="data.observations",
        partition_layout=(),
        dimension_column_map={"REF_AREA": "REF_AREA"},
        measure_column_map={"OBS_VALUE": "OBS_VALUE"},
        attribute_column_map={},
        structural_version=structural_version,
        source_registry="local",
        materialized_at=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
        bound_at=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )


# ── DriftIssue model ────────────────────────────────────────────────────


def test_drift_issue_is_frozen():
    issue = DriftIssue(kind="x", binding_urn="urn:x", description="why")
    with pytest.raises(AttributeError):
        issue.kind = "y"  # type: ignore[misc]


def test_drift_issue_carries_kind_urn_description():
    issue = DriftIssue(kind="structural_version_drift", binding_urn="urn:b", description="d")
    assert issue.kind == "structural_version_drift"
    assert issue.binding_urn == "urn:b"
    assert issue.description == "d"


# ── check_binding_drift / validate_binding — happy path ─────────────────


def test_check_binding_drift_returns_empty_when_clean():
    reg = InMemoryRegistry()
    reg.add(_dsd(version="1.0"))
    reg.add(_flow(dsd_version="1.0"))
    binding = _binding(structural_version="TEST:DSD_TEST(1.0)")
    assert reg.check_binding_drift(binding) == []


def test_validate_binding_no_raise_when_clean():
    reg = InMemoryRegistry()
    reg.add(_dsd(version="1.0"))
    reg.add(_flow(dsd_version="1.0"))
    binding = _binding(structural_version="TEST:DSD_TEST(1.0)")
    reg.validate_binding(binding)  # no raise


# ── check_binding_drift — drift detection (inspection API) ──────────────


def test_check_binding_drift_reports_dataflow_missing():
    reg = InMemoryRegistry()
    binding = _binding()  # no dataflow in registry
    issues = reg.check_binding_drift(binding)
    assert len(issues) == 1
    assert issues[0].kind == "dataflow_missing"


def test_check_binding_drift_reports_structural_version_drift():
    """A dataflow whose DSD has been re-versioned upstream surfaces a drift issue."""
    reg = InMemoryRegistry()
    reg.add(_dsd(version="2.0"))
    reg.add(_flow(dsd_version="2.0"))
    binding = _binding(structural_version="TEST:DSD_TEST(1.0)")
    issues = reg.check_binding_drift(binding)
    assert len(issues) == 1
    assert issues[0].kind == "structural_version_drift"
    assert "1.0" in issues[0].description
    assert "2.0" in issues[0].description


def test_check_binding_drift_reports_dsd_unresolvable_when_structure_is_none():
    reg = InMemoryRegistry()
    flow_urn = Dataflow.urn_for(agency="TEST", id="DF_TEST", version="1.0")
    flow_no_dsd = Dataflow(
        id="DF_TEST",
        maintainer="TEST",
        version="1.0",
        name=InternationalString(en="Test"),
        structure=None,
    )
    reg.add(flow_no_dsd)
    binding = DataflowBinding(
        dataflow=Ref[Dataflow].unresolved(flow_urn),
        table_location="data.observations",
        partition_layout=(),
        dimension_column_map={},
        measure_column_map={},
        attribute_column_map={},
        structural_version="TEST:DSD_TEST(1.0)",
        source_registry="local",
        materialized_at=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
        bound_at=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )
    issues = reg.check_binding_drift(binding)
    assert len(issues) == 1
    assert issues[0].kind == "dsd_unresolvable"


# ── validate_binding — raising API ──────────────────────────────────────


def test_validate_binding_raises_on_drift():
    from sdmxlib.model.binding import DriftError

    reg = InMemoryRegistry()
    reg.add(_dsd(version="2.0"))
    reg.add(_flow(dsd_version="2.0"))
    binding = _binding(structural_version="TEST:DSD_TEST(1.0)")
    with pytest.raises(DriftError) as exc:
        reg.validate_binding(binding)
    assert len(exc.value.issues) == 1
    assert exc.value.issues[0].kind == "structural_version_drift"


# ── FederatedCatalog.browse ─────────────────────────────────────────────


def _populate(local: LocalRegistry) -> None:
    """Add a DSD + dataflow + binding to the local store."""
    local.add(_dsd(version="1.0"))
    local.add(_flow(dsd_version="1.0"))
    local.add(_binding())


def test_browse_lists_locally_bound_dataflows():
    with LocalRegistry(":memory:", source_registry="local") as local:
        _populate(local)
        cat = local
        entries = cat.browse()
        assert len(entries) == 1
        entry = entries[0]
        assert isinstance(entry, DataflowEntry)
        assert entry.dataflow_urn.agency == "TEST"
        assert entry.dataflow_urn.id == "DF_TEST"


def test_browse_carries_provenance_annotation():
    with LocalRegistry(":memory:", source_registry="local") as local:
        _populate(local)
        cat = local
        entries = cat.browse()
        assert entries[0].source_registry == "local"


def test_browse_carries_dataflow_name_when_resolvable():
    with LocalRegistry(":memory:", source_registry="local") as local:
        _populate(local)
        cat = local
        entries = cat.browse()
        assert entries[0].name is not None
        assert entries[0].name["en"] == "Test Dataflow"


def test_browse_returns_empty_when_no_bindings():
    with LocalRegistry(":memory:", source_registry="local") as local:
        cat = local
        assert cat.browse() == []


def test_browse_includes_binding_for_each_entry():
    with LocalRegistry(":memory:", source_registry="local") as local:
        _populate(local)
        cat = local
        entries = cat.browse()
        assert isinstance(entries[0].binding, DataflowBinding)
        assert entries[0].binding.table_location == "data.observations"


# `test_browse_raises_when_bindings_registry_lacks_get_all` lived here. It
# passed a *different* object for the `bindings` role than for `structural` —
# one without `get_all` — and asserted `browse()` refused it. Deleting
# `FederatedCatalog` deletes the failure: a registry browses its own bindings,
# and `get_all` is on the class. The test could only be restored by
# reintroducing the composition it was testing.


# ── Public exports ──────────────────────────────────────────────────────


def test_top_level_exports():
    assert sl.DriftIssue is DriftIssue
    assert sl.DataflowEntry is DataflowEntry


def test_check_binding_drift_works_on_a_local_registry_too() -> None:
    """The same call against the store, not just `InMemoryRegistry`.

    `LocalRegistry.check_binding_drift` is its own method — it reaches
    `_validate_binding` by a different route — and until now only a
    documentation driver exercised it, which is not where library behaviour
    should be pinned. Kept when `docs/cookbook/federation.md` stopped needing
    a hand-written driver (its blocks run from the page).
    """
    dsd = _dsd(version="1.0")
    flow = _flow(dsd_version="1.0")
    with sl.LocalRegistry(":memory:") as reg:
        reg.add_all([dsd, flow])
        assert reg.check_binding_drift(_binding(structural_version="TEST:DSD_TEST(1.0)")) == []

        drifted = reg.check_binding_drift(_binding(structural_version="TEST:DSD_TEST(9.9)"))
        assert drifted, "a binding written against another DSD version has drifted"
