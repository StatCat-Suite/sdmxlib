"""`LocalRegistry` has two constructors, and they must initialise the same fields.

`__init__` and `from_connection` both build a `LocalRegistry`, but only one runs
`__init__`: `from_connection` goes through `cls.__new__(cls)` and assigns every
field itself. So a field added to `__init__` alone is simply *absent* on a
registry built the other way, and the first read of it is an `AttributeError`
in front of a user.

This has now happened twice. The second time it took out the entire data plane:
`_data_store` and `_attach` were added for observations and never mirrored, so
`reg.obs(flow)` raised

    AttributeError: 'LocalRegistry' object has no attribute '_data_store'

on every registry built by `from_connection` — which is the constructor the
docstring recommends for exactly the case observations need, sharing a
connection with an already-attached DuckLake catalog.

There was already a comment warning about this, written after the first
occurrence. A comment is not a guard. This is.
"""

from datetime import UTC, datetime

import duckdb
import pytest

import sdmxlib as sl
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure, Dimension, Measure
from sdmxlib.model.organisation import Agency
from sdmxlib.model.ref import Ref
from tests.support.factories import concept_ref

pytestmark = pytest.mark.unit


@pytest.fixture
def from_init():
    registry = sl.LocalRegistry(":memory:")
    registry.open()
    try:
        yield registry
    finally:
        registry.close()


@pytest.fixture
def from_conn():
    with duckdb.connect(":memory:") as conn:
        # The observations table a binding points at. Created here because
        # `from_connection` exists for callers who bring their own attached
        # data — reaching the data plane is the whole point of the fixture.
        conn.execute("CREATE SCHEMA IF NOT EXISTS data")
        conn.execute("CREATE TABLE IF NOT EXISTS data.obs (GEO VARCHAR, OBS_VALUE DOUBLE)")
        yield sl.LocalRegistry.from_connection(conn)


def test_both_constructors_set_the_same_fields(from_init, from_conn):
    """The structural guard.

    Compared as a symmetric difference so it catches both directions: a field
    `from_connection` forgets, and one it sets that `__init__` does not (which
    would mean the two objects disagree about their own shape).
    """
    divergence = set(vars(from_init)) ^ set(vars(from_conn))
    assert not divergence, (
        f"the two constructors disagree on {sorted(divergence)}. Every field must be set in "
        "both — `from_connection` goes through `cls.__new__` and does not run `__init__`."
    )


def test_a_connection_registry_can_reach_the_data_plane(from_conn):
    """The specific failure, reached.

    A *bound* dataflow is required for this to test anything: `obs()`
    calls `binding_for` first, and with no binding it raises `KeyError` before
    `_data_plane` is ever entered. The first version of this test asserted that
    `KeyError` and passed against the broken code — covering the guard clause
    instead of the bug.
    """
    from sdmxlib.model.binding import DataflowBinding

    dsd = DataStructure(
        id="DSD",
        maintainer=Agency(id="SDMX"),
        version="1.0",
        name={"en": "DSD"},
        dimensions=[Dimension(id="GEO", concept=concept_ref("GEO"))],
        measures=[Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )
    flow = Dataflow(
        id="DF",
        maintainer=Agency(id="SDMX"),
        version="1.0",
        name={"en": "DF"},
        structure=Ref[DataStructure].to(dsd),
    )
    from_conn.add(dsd)
    from_conn.add(flow)
    from_conn.add(
        DataflowBinding(
            dataflow=Ref[Dataflow].to(flow),
            table_location="data.obs",
            # No catalog prefix: the table lives in this very connection, which
            # is the shape `from_connection` exists to serve.
            database_role="memory",
            dimension_column_map={"GEO": "GEO"},
            measure_column_map={"OBS_VALUE": "OBS_VALUE"},
            structural_version="SDMX:DSD(1.0)",
            source_registry="test",
            materialized_at=datetime(2026, 1, 1, tzinfo=UTC),
            bound_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )

    # Reaches `_data_plane()`, which is where the AttributeError was, and gets
    # a usable query out the other side.
    query = from_conn.obs(Ref[Dataflow].to(flow))
    assert query.count() == 0
    assert "GEO" in query.components


def test_a_connection_registry_has_no_attach_spec(from_conn):
    """`from_connection` attaches nothing, and that is the intended contract.

    A caller sharing a connection has already attached whatever it wants
    visible; replaying a spec here would either duplicate that or fight it.
    Pinned because `None` is a deliberate value, not an oversight — which is
    precisely what the last `None`-looking field turned out to be.
    """
    assert from_conn._attach is None
    assert from_conn._data_store is None
