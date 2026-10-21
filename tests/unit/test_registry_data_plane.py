"""A registry answers for observations as well as artefacts (§6 step 6).

`FederatedCatalog` composed three roles — structural reader, bindings reader,
data store — and every one of its methods was *look up the binding for this
dataflow, then delegate*. Two of the three were usually the same object. These
tests pin the same four questions answered by the registry itself, which is
what the spec means by a registry: structures, plus registrations, plus the
data they point at.

The one that matters most is `test_a_relation_valued_filter_crosses_the_planes`:
both planes share one DuckDB connection, which is what lets a subquery over
`sdmx.code` narrow an observation query. Two connections would make that
unresolvable, and §5.9 says the refusal must be explicit rather than silent.
"""

from __future__ import annotations

from datetime import UTC, datetime

import msgspec
import pyarrow as pa
import pytest

import sdmxlib as sl
from sdmxlib.formats.sdmx_csv import write_sdmx_csv
from sdmxlib.formats.sdmx_json.data_v2 import write_sdmx_json_data
from sdmxlib.local.registry import LocalRegistry
from sdmxlib.model.binding import DataflowBinding
from sdmxlib.model.concept import Concept
from sdmxlib.model.ref import Ref

pytestmark = pytest.mark.unit


def _concept(id_: str) -> Ref[Concept]:
    return Ref[Concept].unresolved(Concept.urn_for(agency="AG", scheme="CS", id=id_, version="1.0"))


@pytest.fixture
def reg():
    """A registry holding one dataflow's structure, binding and rows.

    The binding **renames** `REF_AREA` to `geo`, so anything that came back
    named `geo` would be leaking the producer's layout rather than answering in
    the language every filter is written in.
    """
    codelist = sl.Codelist(
        id="CL_GEO",
        maintainer="AG",
        version="1.0",
        name={"en": "geo"},
        codes=[sl.Code(id="ON", name={"en": "Ontario"}), sl.Code(id="QC", name={"en": "Quebec"})],
    )
    dsd = sl.DataStructure(
        id="D",
        maintainer="AG",
        version="1.0",
        name={"en": "d"},
        dimensions=[sl.Dimension(id="REF_AREA", concept=_concept("REF_AREA"), representation=Ref.to(codelist))],
        measures=[sl.Measure(id="OBS_VALUE", concept=_concept("OBS_VALUE"))],
    )
    flow = sl.Dataflow(id="DF", maintainer="AG", version="1.0", name={"en": "flow"}, structure=Ref.to(dsd))
    with LocalRegistry(":memory:") as registry:
        registry.add_all([codelist, dsd, flow])
        cursor = registry.connection.cursor()
        cursor.execute("CREATE SCHEMA IF NOT EXISTS data")
        cursor.execute("CREATE TABLE data.obs (geo TEXT, v DOUBLE)")
        cursor.execute("INSERT INTO data.obs VALUES ('ON', 1.0), ('QC', 2.0)")
        registry.add(
            DataflowBinding(
                dataflow=Ref.to(flow),
                database_role="memory",
                table_location="data.obs",
                dimension_column_map={"REF_AREA": "geo"},
                measure_column_map={"OBS_VALUE": "v"},
                attribute_column_map={},
                structural_version="AG:D(1.0)",
                source_registry="local",
                materialized_at=datetime(2026, 1, 1, tzinfo=UTC),
                bound_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
        registry.flow = flow  # type: ignore[attr-defined]
        yield registry


# ── the four the catalog used to answer ───────────────────────────────────


def test_binding_for_finds_the_registration(reg) -> None:
    """A binding *is* a registration — the record of where a provider's data
    sits — so it is read from the registry like any other artefact."""
    assert reg.binding_for(reg.flow).table_location == "data.obs"


def test_dsd_comes_back_with_its_representations_resolved(reg) -> None:
    """What labelling needs: a codelist ref that is actually followed.

    Asserted as an *outcome*, not a mechanism. An earlier version of this test
    claimed the by-URN round trip was what caused it — inherited from the
    catalog's docstring — and a mutation showed the claim is false locally:
    `get` already resolves a stored artefact's refs, so reading the DSD off
    `flow.structure()` resolves it too. The by-URN form matters when a ref
    arrives *unresolved*, which is a remote condition this fixture cannot
    reach.
    """
    dsd = reg.dsd(reg.flow)
    assert dsd.id == "D"
    assert isinstance(dsd.dimensions["REF_AREA"].representation, Ref)
    assert dsd.dimensions["REF_AREA"].representation.resolved, "or labelling silently does nothing"


def test_observations_runs_the_query_language(reg) -> None:
    obs = reg.obs(reg.flow)
    assert obs.filter(obs.col("REF_AREA") == "ON").frame().to_dicts() == [{"REF_AREA": "ON", "OBS_VALUE": 1.0}]


def test_browse_lists_what_has_rows_here(reg) -> None:
    """Keyed on bindings, so it answers "which dataflows have observations"
    rather than "which dataflows exist" — the second is `query(q.dataflow)`."""
    entries = reg.browse()
    assert [e.binding.table_location for e in entries] == ["data.obs"]
    assert entries[0].name.get("en") == "flow"


# ── identity in, this registry's structure out ────────────────────────────


@pytest.mark.parametrize("form", ["artefact", "ref", "urn"])
def test_a_dataflow_may_be_named_three_ways(reg, form: str) -> None:
    """All three are *identity*; none is read for its structure, so an artefact
    resolved elsewhere cannot make this registry read rows with foreign
    structure."""
    flow = {"artefact": reg.flow, "ref": Ref.to(reg.flow), "urn": reg.flow.urn}[form]
    assert reg.obs(flow).count() == 2


def test_a_dataflow_with_no_rows_here_is_refused_by_name(reg) -> None:
    """`None` would read as "no observations", which is indistinguishable from
    an empty table. The refusal names the flow, because adding a binding — or
    opening the bundle that has one — is the fix."""
    other = sl.Dataflow.urn_for(agency="AG", id="NOPE", version="1.0")
    with pytest.raises(KeyError, match="no DataflowBinding"):
        reg.obs(other)


# ── one connection, both planes ───────────────────────────────────────────


def test_a_relation_valued_filter_crosses_the_planes(reg) -> None:
    """The reason a registry owns its data store rather than being handed one.

    `IN (<subquery>)` puts a structural query inside an observation query, and
    DuckDB can only resolve that when both sides are on **one connection**.
    A registry that composed a separate store would make this fail — and §5.9
    requires that be a stated refusal, never a silent wrong answer.
    """
    obs = reg.obs(reg.flow)
    ontario = reg.query(sl.q.code).filter(sl.q.code.name["en"].str.contains("Ontario")).select(sl.q.code.id)
    assert obs.filter(obs.col("REF_AREA").is_in(ontario)).frame()["REF_AREA"].to_list() == ["ON"]


def test_labels_come_from_the_registry_s_own_codelists(reg) -> None:
    """The catalog needed a structural reader passed in to do this. The
    registry already holds the codelist."""
    frame = reg.obs(reg.flow).frame(labels="en")
    assert frame["REF_AREA_label_en"].to_list() == ["Ontario", "Quebec"]


# ── the bundle constructor ────────────────────────────────────────────────


def test_a_bundle_opens_artefacts_and_observations_together(tmp_path) -> None:
    """`LocalRegistry.bundle(path)` replaces `FederatedCatalog(uri)`.

    A *constructor, not a type*: a bundle answers exactly what any other local
    registry answers, so it differs in wiring rather than in method set. The
    layout is the one a bundle builder writes and a downstream REST service
    serves — `meta.duckdb` beside `lake.ducklake` and `parquet/`.
    """
    import polars as pl

    from sdmxlib.local.data_store import DuckDBDataStore

    dsd = sl.DataStructure(
        id="D",
        maintainer="AG",
        version="1.0",
        name={"en": "d"},
        dimensions=[sl.Dimension(id="REF_AREA", concept=_concept("REF_AREA"))],
        measures=[sl.Measure(id="OBS_VALUE", concept=_concept("OBS_VALUE"))],
    )
    flow = sl.Dataflow(id="DF", maintainer="AG", version="1.0", name={"en": "f"}, structure=Ref.to(dsd))

    with LocalRegistry(tmp_path / "meta.duckdb") as writer:
        writer.add_all([dsd, flow])
        store = DuckDBDataStore.from_registry(
            writer,
            attach={
                "lake": (
                    f"'ducklake:{tmp_path / 'lake.ducklake'}'",
                    f"(DATA_PATH '{tmp_path / 'parquet'}/')",
                )
            },
            extensions=["ducklake"],
        )
        store.write_observations(
            schema="data",
            table="obs",
            source=pl.DataFrame({"REF_AREA": ["ON", "QC"], "OBS_VALUE": [1.0, 2.0]}),
        )
        writer.add(DataflowBinding.from_dsd(dsd, dataflow=Ref.to(flow), schema="data", table="obs"))

    with LocalRegistry.bundle(tmp_path) as reg:
        assert [e.binding.table_location for e in reg.browse()] == ["data.obs"]
        obs = reg.obs(flow.urn)
        assert obs.filter(obs.col("REF_AREA") == "QC").frame().to_dicts() == [{"REF_AREA": "QC", "OBS_VALUE": 2.0}]
        assert reg.query(sl.q.dsd).ids() == ["D"], "and the structural side, from the same object"


def test_a_plain_registry_attaches_no_lake(tmp_path) -> None:
    """The ATTACH is the bundle constructor's, not every registry's — opening
    one to read a codelist must not pay for a DuckLake extension."""
    with LocalRegistry(tmp_path / "plain.duckdb") as reg:
        assert reg._attach is None
    with LocalRegistry.bundle(tmp_path) as bundled:
        assert "lake" in (bundled._attach or {})


# ── streaming out, through a renamed binding ──────────────────────────────


def test_the_csv_writer_names_columns_the_way_the_batches_do() -> None:
    """A regression, not a tidy-up.

    The writers used to take the *binding* and read their column order off its
    physical names. That agreed with the batches only while the two happened to
    match — and every writer fixture in the suite used an identity binding, so
    nothing noticed when step 3 made an observation query project by component
    id. Against this fixture's binding the old signature raised
    ``KeyError: ... missing column(s) ['geo', 'v']``, naming columns the batch
    was correct not to have.

    The structure is now the only source of both, so they cannot disagree.
    """
    codelist = sl.Codelist(
        id="CL_GEO",
        maintainer="AG",
        version="1.0",
        name={"en": "geo"},
        codes=[sl.Code(id="ON", name={"en": "Ontario"}), sl.Code(id="QC", name={"en": "Quebec"})],
    )
    dsd = sl.DataStructure(
        id="D",
        maintainer="AG",
        version="1.0",
        name={"en": "d"},
        dimensions=[sl.Dimension(id="REF_AREA", concept=_concept("REF_AREA"), representation=Ref.to(codelist))],
        measures=[sl.Measure(id="OBS_VALUE", concept=_concept("OBS_VALUE"))],
    )
    flow = sl.Dataflow(id="DF", maintainer="AG", version="1.0", name={"en": "flow"}, structure=Ref.to(dsd))
    batch = pa.record_batch({"REF_AREA": ["ON"], "OBS_VALUE": [1.0]})

    text = b"".join(write_sdmx_csv([batch], dsd, structure_id="AG:DF(1.0)")).decode()
    assert text.splitlines()[0].split(",")[3:] == ["REF_AREA", "OBS_VALUE"]

    doc = msgspec.json.decode(
        b"".join(
            write_sdmx_json_data(
                [batch],
                dsd,
                Ref.to(flow),
                message_id="m",
                prepared=datetime(2026, 6, 16, tzinfo=UTC),
                sender_id="S",
                lang="en",
            )
        ),
        type=dict,
    )
    assert doc["data"]["structures"][0]["dimensions"]["observation"][0]["id"] == "REF_AREA"


def test_streaming_a_renamed_binding_reaches_the_writer_intact(reg) -> None:
    """The two halves joined up: the query projects by component id, and the
    writer expects component ids. Neither half alone would have caught the
    break — the query's own tests assert its column names, and the writer's
    fixtures never renamed anything."""
    batches = list(reg.obs(reg.flow).stream())
    assert [b.schema.names for b in batches] == [["REF_AREA", "OBS_VALUE"]], "not the binding's ['geo', 'v']"

    dsd = reg.dsd(reg.flow)
    rows = b"".join(write_sdmx_csv(batches, dsd, structure_id="AG:DF(1.0)")).decode().splitlines()
    assert rows[0].split(",")[3:] == ["REF_AREA", "OBS_VALUE"]
    assert [r.split(",")[3:] for r in rows[1:]] == [["ON", "1.0"], ["QC", "2.0"]]
