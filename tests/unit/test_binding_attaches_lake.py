"""A binding names a catalog that is attached: `binding_for` and `locate` attach the lake they name.

The lake attaches lazily, so opening a bundle to read a codelist costs nothing.
SQL built from a binding (``<database_role>.<table_location>``) reads that
catalog, so it has to be attached by the time the binding is handed out. Before
this, only `obs()` attached it: on a freshly opened bundle the availability
builders, which run SQL from `binding_for`, failed with "Catalog Error: Table
... does not exist", which is how a server's first `/availableconstraint`
request would have failed.
"""

from pathlib import Path

import duckdb
import pytest

import sdmxlib as sl
from sdmxlib.builders.contentconstraint import build_component_availability, build_dataflow_constraint
from tests.support.bundles import FLOW, make_bundle, publish
from tests.support.narrow import present

pytestmark = pytest.mark.unit


def _attached(conn: duckdb.DuckDBPyConnection, catalog: str) -> bool:
    return conn.execute("SELECT count(*) FROM duckdb_databases() WHERE database_name = ?", [catalog]).fetchone() == (1,)


@pytest.fixture
def published(tmp_path: Path) -> Path:
    return publish(make_bundle(tmp_path / "b", 10))


def test_opening_a_bundle_and_reading_structure_attaches_no_lake(published: Path) -> None:
    with sl.LocalRegistry.edition(published) as reg:
        assert reg.get(FLOW.urn) is not None
        assert not _attached(reg.connection, reg.lake_alias)


def test_binding_for_attaches_the_lake_it_names(published: Path) -> None:
    with sl.LocalRegistry.edition(published) as reg:
        binding = present(reg.binding_for(FLOW.urn))
        assert binding.database_role == reg.lake_alias
        assert _attached(reg.connection, reg.lake_alias)
        count = reg.connection.execute(f"SELECT count(*) FROM {binding.database_role}.{binding.table_location}")
        assert count.fetchone() == (10,)


def test_availability_runs_on_a_freshly_opened_bundle(published: Path) -> None:
    """Neither builder reads observations through `obs()` first, as a server's first request does not."""
    with sl.LocalRegistry.edition(published) as reg:
        flow = build_dataflow_constraint(reg, FLOW.urn, name={"en": "a"})
        assert {k.dimension_id: len(k.values) for r in flow.regions for k in r.keys} == {"GEO": 7}
    with sl.LocalRegistry.edition(published) as reg:
        component = build_component_availability(
            reg, FLOW.urn, name={"en": "a"}, key="all", component_id="all", api="2.1"
        )
        assert {k.dimension_id: len(k.values) for r in component.regions for k in r.keys} == {"GEO": 7}


def test_a_bundle_in_a_shared_engine_attaches_its_own_lake(published: Path) -> None:
    engine = sl.EditionEngine()
    try:
        with sl.LocalRegistry.edition(published, engine=engine) as reg:
            present(reg.binding_for(FLOW.urn))
            assert _attached(engine.connection, reg.lake_alias)
            assert reg.lake_alias.endswith("__tables")
    finally:
        engine.close()


def test_a_writable_publication_attaches_its_lake_for_a_binding(tmp_path: Path) -> None:
    """`LocalRegistry.build` attaches its lake as ``lake``, the role itself: the same lazy path, unmapped."""
    publication = make_bundle(tmp_path / "b", 10)
    with sl.LocalRegistry.build(publication.parent, resume=publication.name) as reg:
        assert not _attached(reg.connection, "lake")
        binding = present(reg.binding_for(FLOW.urn))
        assert binding.database_role == "lake"
        assert _attached(reg.connection, "lake")
