"""End-to-end tests for the registry's query lifecycle.

These tested `FederatedCatalog`, which composed a structural reader, a bindings
reader and a data store — and every fixture here passed the *same* registry for
all three, which is the clearest evidence there was nothing to compose. The
class is gone; the lifecycle it ran is not, so the tests point at the registry
that now runs it.
"""

from datetime import UTC, datetime

import polars as pl
import pytest

import sdmxlib as sl
from sdmxlib.data_store import DataStore
from sdmxlib.local import LocalRegistry
from sdmxlib.local.data_store import DuckDBDataStore
from sdmxlib.model.binding import DataflowBinding
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure, Dimension, Measure
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.ref import Ref
from tests.support.factories import OBSERVATION_ROWS, build_observations, concept_ref, federated_catalog

pytestmark = pytest.mark.unit


# ── Fixture builders ────────────────────────────────────────────────────


def _build_structural(local: LocalRegistry) -> tuple[Codelist, DataStructure, Dataflow]:
    """Populate ``local`` with a small DSD + codelist + dataflow."""
    cl = Codelist(
        id="CL_FREQ",
        maintainer="TEST",
        version="1.0",
        name=InternationalString(en="Frequency"),
        codes=[
            Code(id="A", name=InternationalString(en="Annual")),
            Code(id="Q", name=InternationalString(en="Quarterly")),
            Code(id="M", name=InternationalString(en="Monthly")),
        ],
    )
    cl_urn = Codelist.urn_for(agency="TEST", id="CL_FREQ", version="1.0")
    dsd = DataStructure(
        id="DSD_TEST",
        maintainer="TEST",
        version="1.0",
        name=InternationalString(en="Test DSD"),
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
    return cl, dsd, flow


def _build_binding(local: LocalRegistry) -> DataflowBinding:
    """Create + register a binding for the test dataflow."""
    flow_urn = Dataflow.urn_for(agency="TEST", id="DF_TEST", version="1.0")
    binding = DataflowBinding(
        dataflow=Ref[Dataflow].unresolved(flow_urn),
        database_role="memory",
        table_location="data.observations",
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


# ── Construction + protocol conformance ─────────────────────────────────


def test_data_store_protocol_conformance():
    with LocalRegistry(":memory:") as local:
        store = DuckDBDataStore.from_registry(local)
        assert isinstance(store, DataStore)


def test_registry_repr_names_its_source():
    """Was `test_federated_catalog_repr`, asserting the composed class named
    its three parts. There are no parts now, so a registry names what it is."""
    with LocalRegistry(":memory:") as local:
        assert "LocalRegistry" in repr(local)


# ── Binding lookup ──────────────────────────────────────────────────────


def test_binding_for_returns_registered_binding():
    with LocalRegistry(":memory:", source_registry="local") as local:
        _build_structural(local)
        build_observations(local, rows=OBSERVATION_ROWS[:4])
        binding = _build_binding(local)
        cat = federated_catalog(local)
        flow_urn = Dataflow.urn_for(agency="TEST", id="DF_TEST", version="1.0")
        result = cat.binding_for(flow_urn)
        assert result is not None
        assert result.table_location == binding.table_location


def test_query_raises_when_no_binding():
    with LocalRegistry(":memory:", source_registry="local") as local:
        _build_structural(local)
        build_observations(local, rows=OBSERVATION_ROWS[:4])
        # No binding added.
        cat = federated_catalog(local)
        with pytest.raises(KeyError, match="no DataflowBinding"):
            cat.obs(Dataflow.urn_for(agency="TEST", id="DF_TEST", version="1.0"))


# ── Data scan ───────────────────────────────────────────────────────────


def test_query_returns_all_observations_unfiltered():
    with LocalRegistry(":memory:", source_registry="local") as local:
        _build_structural(local)
        build_observations(local, rows=OBSERVATION_ROWS[:4])
        _build_binding(local)
        cat = federated_catalog(local)
        flow_urn = Dataflow.urn_for(agency="TEST", id="DF_TEST", version="1.0")
        df = cat.obs(flow_urn).frame()
        assert len(df) == 4
        assert set(df.columns) == {"FREQ", "REF_AREA", "OBS_VALUE"}


def test_query_filters_by_where_and_params():
    with LocalRegistry(":memory:", source_registry="local") as local:
        _build_structural(local)
        build_observations(local, rows=OBSERVATION_ROWS[:4])
        _build_binding(local)
        cat = federated_catalog(local)
        flow_urn = Dataflow.urn_for(agency="TEST", id="DF_TEST", version="1.0")
        obs = cat.obs(flow_urn)
        df = obs.filter(obs.col("REF_AREA") == "CA").frame()
        assert len(df) == 2
        assert df["REF_AREA"].unique().to_list() == ["CA"]


def test_query_columns_projection():
    with LocalRegistry(":memory:", source_registry="local") as local:
        _build_structural(local)
        build_observations(local, rows=OBSERVATION_ROWS[:4])
        _build_binding(local)
        cat = federated_catalog(local)
        flow_urn = Dataflow.urn_for(agency="TEST", id="DF_TEST", version="1.0")
        obs = cat.obs(flow_urn)
        df = obs.select(FREQ=obs.col("FREQ"), OBS_VALUE=obs.col("OBS_VALUE")).collect()
        assert set(df.columns) == {"FREQ", "OBS_VALUE"}


def test_query_order_by_and_limit():
    with LocalRegistry(":memory:", source_registry="local") as local:
        _build_structural(local)
        build_observations(local, rows=OBSERVATION_ROWS[:4])
        _build_binding(local)
        cat = federated_catalog(local)
        flow_urn = Dataflow.urn_for(agency="TEST", id="DF_TEST", version="1.0")
        obs = cat.obs(flow_urn)
        df = obs.order_by(obs.col("OBS_VALUE").desc()).limit(2).frame()
        assert len(df) == 2
        assert df["OBS_VALUE"].to_list() == [200.0, 100.0]


# ── Result annotation (labelling) ───────────────────────────────────────


def test_query_with_labels_attaches_codelist_names():
    with LocalRegistry(":memory:", source_registry="local") as local:
        _build_structural(local)
        build_observations(local, rows=OBSERVATION_ROWS[:4])
        _build_binding(local)
        cat = federated_catalog(local)
        flow_urn = Dataflow.urn_for(agency="TEST", id="DF_TEST", version="1.0")
        df = cat.obs(flow_urn).frame(labels="en")
        # FREQ column was enumerated; slpl.label adds FREQ_label.
        assert "FREQ_label_en" in df.columns
        # Non-enumerated REF_AREA gets no _label column.
        assert "REF_AREA_label_en" not in df.columns
        # Spot-check the labels — every "A" row should be labelled "Annual".
        labels = df.filter(pl.col("FREQ") == "A")["FREQ_label_en"].to_list()
        assert labels  # at least one
        assert all(label == "Annual" for label in labels)


def test_a_frame_without_labels_asks_for_no_language():
    """`test_with_labels_requires_lang` used to sit here, and has nothing left
    to assert: `with_labels=True, lang=None` was a two-argument state that
    could disagree with itself, and `labels="en"` cannot — the language *is*
    the request. Omitting it asks for nothing, which is this test."""
    with LocalRegistry(":memory:", source_registry="local") as local:
        _build_structural(local)
        build_observations(local, rows=OBSERVATION_ROWS[:4])
        _build_binding(local)
        cat = federated_catalog(local)
        flow_urn = Dataflow.urn_for(agency="TEST", id="DF_TEST", version="1.0")
        df = cat.obs(flow_urn).frame()
        # No labelling → no _label columns.
        assert all(not c.endswith("_label") for c in df.columns)


# ── series_attributes_location JOIN ─────────────────────────────────────


def _build_observations_with_sidecar(local: LocalRegistry) -> None:
    """Fact + sidecar tables: GEO_NAME and ALT_GEO_CODE live on sidecar."""
    cur = local.connection.cursor()
    cur.execute("CREATE SCHEMA IF NOT EXISTS data")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS data.obs_main (
            FREQ TEXT,
            REF_AREA TEXT,
            OBS_VALUE DOUBLE
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS data.obs_geo_attrs (
            REF_AREA TEXT,
            GEO_NAME TEXT,
            ALT_GEO_CODE TEXT
        )
    """)
    cur.execute("DELETE FROM data.obs_main")
    cur.execute("DELETE FROM data.obs_geo_attrs")
    cur.execute("INSERT INTO data.obs_main VALUES ('A', 'CA', 100.0)")
    cur.execute("INSERT INTO data.obs_main VALUES ('A', 'US', 200.0)")
    cur.execute("INSERT INTO data.obs_geo_attrs VALUES ('CA', 'Canada', 'CAN')")
    cur.execute("INSERT INTO data.obs_geo_attrs VALUES ('US', 'United States', 'USA')")


def _build_binding_with_sidecar(local: LocalRegistry) -> DataflowBinding:
    flow_urn = Dataflow.urn_for(agency="TEST", id="DF_TEST", version="1.0")
    binding = DataflowBinding(
        dataflow=Ref[Dataflow].unresolved(flow_urn),
        database_role="memory",
        table_location="data.obs_main",
        dimension_column_map={"FREQ": "FREQ", "REF_AREA": "REF_AREA"},
        measure_column_map={"OBS_VALUE": "OBS_VALUE"},
        attribute_column_map={"GEO_NAME": "GEO_NAME", "ALT_GEO_CODE": "ALT_GEO_CODE"},
        structural_version="TEST:DSD_TEST(1.0)",
        source_registry="local",
        materialized_at=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
        bound_at=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
        series_attributes_location="data.obs_geo_attrs",
    )
    local.add(binding)
    return binding


def test_query_joins_series_attributes_location():
    with LocalRegistry(":memory:", source_registry="local") as local:
        _build_structural(local)
        _build_observations_with_sidecar(local)
        _build_binding_with_sidecar(local)
        cat = federated_catalog(local)
        flow_urn = Dataflow.urn_for(agency="TEST", id="DF_TEST", version="1.0")
        df = cat.obs(flow_urn).frame()
        assert len(df) == 2
        assert {"FREQ", "REF_AREA", "OBS_VALUE", "GEO_NAME", "ALT_GEO_CODE"} <= set(df.columns)
        canada = df.filter(pl.col("REF_AREA") == "CA")
        assert canada["GEO_NAME"][0] == "Canada"
        assert canada["ALT_GEO_CODE"][0] == "CAN"


def test_query_with_columns_selects_attribute_from_sidecar():
    with LocalRegistry(":memory:", source_registry="local") as local:
        _build_structural(local)
        _build_observations_with_sidecar(local)
        _build_binding_with_sidecar(local)
        cat = federated_catalog(local)
        flow_urn = Dataflow.urn_for(agency="TEST", id="DF_TEST", version="1.0")
        obs = cat.obs(flow_urn)
        df = obs.select(REF_AREA=obs.col("REF_AREA"), GEO_NAME=obs.col("GEO_NAME")).collect()
        assert set(df.columns) == {"REF_AREA", "GEO_NAME"}


def test_query_no_sidecar_unchanged_behaviour():
    """A binding with series_attributes_location=None goes through the simple path."""
    with LocalRegistry(":memory:", source_registry="local") as local:
        _build_structural(local)
        build_observations(local, rows=OBSERVATION_ROWS[:4])
        _build_binding(local)  # no sidecar
        cat = federated_catalog(local)
        flow_urn = Dataflow.urn_for(agency="TEST", id="DF_TEST", version="1.0")
        df = cat.obs(flow_urn).frame()
        assert set(df.columns) == {"FREQ", "REF_AREA", "OBS_VALUE"}


# ── ComponentFilter integration ─────────────────────────────────────────


def test_query_with_component_filter_translates_to_where_in():
    with LocalRegistry(":memory:", source_registry="local") as local:
        _build_structural(local)
        build_observations(local, rows=OBSERVATION_ROWS[:4])
        _build_binding(local)
        cat = federated_catalog(local)
        flow_urn = Dataflow.urn_for(agency="TEST", id="DF_TEST", version="1.0")
        from sdmxlib.rest import parse_components

        cf = parse_components("A.CA+US", {}, ["FREQ", "REF_AREA"])
        df = cat.obs(flow_urn).filter_components(cf).frame()
        assert len(df) == 2
        assert set(df["REF_AREA"].to_list()) == {"CA", "US"}
        assert set(df["FREQ"].to_list()) == {"A"}


def test_query_with_component_filter_wildcard_returns_everything():
    with LocalRegistry(":memory:", source_registry="local") as local:
        _build_structural(local)
        build_observations(local, rows=OBSERVATION_ROWS[:4])
        _build_binding(local)
        cat = federated_catalog(local)
        flow_urn = Dataflow.urn_for(agency="TEST", id="DF_TEST", version="1.0")
        from sdmxlib.rest import parse_components

        cf = parse_components("all", {}, ["FREQ", "REF_AREA"])
        df = cat.obs(flow_urn).filter_components(cf).frame()
        assert len(df) == 4


def test_query_with_lazy_codelist_relation_filter():
    """A query from LazyCodelist.narrower_transitive(...) becomes IN (subquery)."""
    with LocalRegistry(":memory:", source_registry="local") as local:
        _build_structural(local)
        build_observations(local, rows=OBSERVATION_ROWS[:4])
        _build_binding(local)
        # Add a geo hierarchy: NA → CA, US.
        geo_cl = Codelist(
            id="CL_GEO",
            maintainer="TEST",
            name=InternationalString(en="Geography"),
            codes=[
                Code(id="NA", name=InternationalString(en="North America")),
                Code(id="CA", name=InternationalString(en="Canada"), parent_id="NA"),
                Code(id="US", name=InternationalString(en="United States"), parent_id="NA"),
                Code(id="MX", name=InternationalString(en="Mexico"), parent_id="NA"),
            ],
        )
        local.add(geo_cl)
        cat = federated_catalog(local)
        flow_urn = Dataflow.urn_for(agency="TEST", id="DF_TEST", version="1.0")
        from sdmxlib.rest import parse_components

        geo_urn = str(Codelist.urn_for(agency="TEST", id="CL_GEO", version="1.0"))
        lazy_cl = local.codelist(geo_urn)
        assert lazy_cl is not None
        # narrower_transitive("NA") = {CA, US, MX} — observations only have CA/US.
        rel = lazy_cl.narrower_transitive("NA").select(sl.q.code.id)
        cf = parse_components("all", {"REF_AREA": rel}, ["FREQ", "REF_AREA"])
        df = cat.obs(flow_urn).filter_components(cf).frame()
        assert len(df) == 4
        assert set(df["REF_AREA"].to_list()) == {"CA", "US"}


def test_query_lazy_relation_avoids_parameter_explosion():
    """A relation filter sends a single subquery binding, not N code parameters."""
    with LocalRegistry(":memory:", source_registry="local") as local:
        _build_structural(local)
        build_observations(local, rows=OBSERVATION_ROWS[:4])
        _build_binding(local)
        geo_cl = Codelist(
            id="CL_GEO",
            maintainer="TEST",
            name=InternationalString(en="Geography"),
            codes=[
                Code(id="NA", name=InternationalString(en="North America")),
                Code(id="CA", name=InternationalString(en="Canada"), parent_id="NA"),
                Code(id="US", name=InternationalString(en="United States"), parent_id="NA"),
            ],
        )
        local.add(geo_cl)
        from sdmxlib.rest import ComponentFilter

        geo_urn = str(Codelist.urn_for(agency="TEST", id="CL_GEO", version="1.0"))
        lazy_cl = local.codelist(geo_urn)
        assert lazy_cl is not None
        rel = lazy_cl.narrower_transitive("NA").select(sl.q.code.id)
        cf = ComponentFilter(
            dimension_ids=["FREQ", "REF_AREA"],
            dimensions=[None, rel],
        )
        binding = _build_binding(local)
        fragments, params = cf.to_where_clauses(binding.dimension_column_map)
        # One fragment, and its bind list reflects the *subquery's* params,
        # not the materialised code list (which would be 2+ values).
        assert len(fragments) == 1
        assert "IN (SELECT" in fragments[0]
        # CodeQuery.to_sql() binds (codelist_urn, ancestor_id) — small fixed set.
        # Exact: the comment above already says this is a *fixed* set, and a
        # bound that admits five hides a rewrite binding four (#403).
        assert len(params) == 2, f"CodeQuery.to_sql() binds codelist_urn and ancestor_id; got {params}"


def test_query_with_attribute_filter_via_sidecar():
    """c[GEO_NAME]=Canada should filter via the JOINed sidecar table."""
    with LocalRegistry(":memory:", source_registry="local") as local:
        _build_structural(local)
        _build_observations_with_sidecar(local)
        _build_binding_with_sidecar(local)
        cat = federated_catalog(local)
        flow_urn = Dataflow.urn_for(agency="TEST", id="DF_TEST", version="1.0")
        from sdmxlib.rest import parse_components

        cf = parse_components("all", {"GEO_NAME": ["Canada"]}, ["FREQ", "REF_AREA"])
        df = cat.obs(flow_urn).filter_components(cf).frame()
        assert len(df) == 1
        assert df["REF_AREA"][0] == "CA"


# ── Streaming ───────────────────────────────────────────────────────────


def test_query_stream_yields_record_batches():
    import pyarrow as pa

    with LocalRegistry(":memory:", source_registry="local") as local:
        _build_structural(local)
        build_observations(local, rows=OBSERVATION_ROWS[:4])
        _build_binding(local)
        cat = federated_catalog(local)
        flow_urn = Dataflow.urn_for(agency="TEST", id="DF_TEST", version="1.0")
        batches = list(cat.obs(flow_urn).stream(chunk_rows=2))
        assert all(isinstance(b, pa.RecordBatch) for b in batches)
        total_rows = sum(b.num_rows for b in batches)
        assert total_rows == 4


def test_query_stream_with_component_filter():
    with LocalRegistry(":memory:", source_registry="local") as local:
        _build_structural(local)
        build_observations(local, rows=OBSERVATION_ROWS[:4])
        _build_binding(local)
        cat = federated_catalog(local)
        flow_urn = Dataflow.urn_for(agency="TEST", id="DF_TEST", version="1.0")
        from sdmxlib.rest import parse_components

        cf = parse_components("A.CA", {}, ["FREQ", "REF_AREA"])
        batches = list(cat.obs(flow_urn).filter_components(cf).stream())
        total = sum(b.num_rows for b in batches)
        assert total == 1


def test_query_stream_chunks_are_bounded():
    """A stream of N rows with chunk_rows=k emits ceil(N/k) batches."""
    with LocalRegistry(":memory:", source_registry="local") as local:
        _build_structural(local)
        build_observations(local, rows=OBSERVATION_ROWS[:4])
        _build_binding(local)
        cat = federated_catalog(local)
        flow_urn = Dataflow.urn_for(agency="TEST", id="DF_TEST", version="1.0")
        batches = list(cat.obs(flow_urn).stream(chunk_rows=1))
        # 4 rows, chunk_rows=1 → at least 4 batches (DuckDB may merge small ones)
        assert sum(b.num_rows for b in batches) == 4


def test_query_stream_raises_when_no_binding():
    with LocalRegistry(":memory:", source_registry="local") as local:
        _build_structural(local)
        build_observations(local, rows=OBSERVATION_ROWS[:4])
        cat = federated_catalog(local)
        flow_urn = Dataflow.urn_for(agency="TEST", id="DF_TEST", version="1.0")
        with pytest.raises(KeyError, match="no DataflowBinding"):
            list(cat.obs(flow_urn).stream())


# ── DuckDBDataStore basics ──────────────────────────────────────────────


def test_duckdb_data_store_uses_shared_connection():
    with LocalRegistry(":memory:") as local:
        store = DuckDBDataStore.from_registry(local)
        assert store.connection is local.connection
        assert store.registry is local


def test_duckdb_data_store_attach_validates_alias():
    with LocalRegistry(":memory:") as local, pytest.raises(ValueError, match="Invalid SQL identifier"):
        DuckDBDataStore.from_registry(local, attach={"bad alias": "ducklake:'/tmp/cat.db'"})


def test_azure_credentials_force_curl_transport():
    """abfss:// needs libcurl-backed transport so OS env CA bundles work.

    The Azure SDK's default transport on Linux ignores ``SSL_CERT_FILE``
    / ``CURL_CA_BUNDLE``; the named ``curl`` adapter delegates to
    libcurl which does honour them. The option is SET whenever
    ``azure_credentials`` is passed, before any abfss-touching read.

    Critically, the transport must be set with ``SET GLOBAL`` so it
    survives the per-thread cursors the data plane uses (see #81).
    Asserting through a fresh cursor catches the regression that a bare
    session-scoped ``SET`` would silently produce.
    """
    with LocalRegistry(":memory:") as local:
        store = DuckDBDataStore.from_registry(
            local,
            azure_credentials={"chain": "cli"},
            extensions=["azure"],
        )
        with store:
            row = store.connection.execute(
                "SELECT value FROM duckdb_settings() WHERE name = 'azure_transport_option_type'"
            ).fetchone()
            assert row is not None
            assert row[0] == "curl"

            # Regression for #81: a freshly-opened cursor must see the
            # curl transport too. With a session-scoped SET this returns
            # ``default``; with SET GLOBAL it returns ``curl``.
            cursor_row = (
                store.connection.cursor().execute("SELECT current_setting('azure_transport_option_type')").fetchone()
            )
            assert cursor_row is not None
            assert cursor_row[0] == "curl"


def test_no_azure_credentials_leaves_transport_default():
    """Don't touch ``azure_transport_option_type`` when no abfss is in play."""
    with LocalRegistry(":memory:") as local:
        store = DuckDBDataStore.from_registry(local)
        with store:
            row = store.connection.execute(
                "SELECT value FROM duckdb_settings() WHERE name = 'azure_transport_option_type'"
            ).fetchone()
            # Setting may not exist at all without the azure extension loaded.
            assert row is None or row[0] == "default"


def test_ducklake_attach_triggers_azure_setup():
    """``ducklake:`` ATTACH specs hide their data_path; must still trigger azure.

    Regression for #81: a DuckLake catalog ATTACHed via ``ducklake:`` can
    reference abfss-backed parquet through the lake metadata file, which
    the ATTACH SQL text doesn't expose. ``_needs_azure()`` must catch the
    ``ducklake:`` prefix and force the curl transport pre-emptively;
    otherwise the first data query against the lake hits the SSL CA cert
    failure that 0.28.2 was supposed to prevent.

    Path-style construction is used so the heuristic can be inspected
    without triggering ``open()`` (which would attempt a real ATTACH
    against a non-existent lake file).
    """
    store = DuckDBDataStore(
        ":memory:",
        attach={"lake": ("'ducklake:/nonexistent.ducklake'", "")},
    )
    assert store._needs_azure() is True


def test_local_only_attach_does_not_trigger_azure_setup():
    """Plain DuckDB ATTACH without abfss / ducklake leaves transport untouched."""
    store = DuckDBDataStore(
        ":memory:",
        attach={"side": ("'/tmp/some_other.duckdb'", "")},
    )
    assert store._needs_azure() is False


def test_abfss_attach_triggers_azure_setup():
    """abfss:// in ATTACH spec text triggers the azure heuristic."""
    store = DuckDBDataStore(
        ":memory:",
        attach={
            "lake": (
                "'abfss://container@account.dfs.core.windows.net/path/lake.db'",
                "",
            ),
        },
    )
    assert store._needs_azure() is True


# ── Public exports ──────────────────────────────────────────────────────


def test_top_level_exports():
    assert sl.DataStore is DataStore
    assert sl.DuckDBDataStore is DuckDBDataStore
