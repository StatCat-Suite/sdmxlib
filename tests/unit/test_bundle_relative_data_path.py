"""`bundle()` must open a bundle wherever it now is (#387).

A DuckLake catalog records the data path it was *written* with. A bundle is a
directory that gets built in one place and read in another — copied, unpacked,
mounted — so that recorded path is provenance, not truth.

A bundle builder writes a **relative** one
(`data/<bundle>/parquet/`). Opening the bundle passes an absolute data path,
DuckLake compares the two, and refuses the ATTACH:

    Error: DATA_PATH parameter "/abs/.../parquet/" does not match existing
    data path in the catalog "data/<bundle>/parquet/".

That made the entire data plane unreachable for the bundle the library's own
first-party producer emits — found by trying to profile it. The caller states
where the publication is now, and the layout fixes ``data/`` beside it, so
`OVERRIDE_DATA_PATH true` is the correct reading rather than a workaround.

The fixture below writes its catalog from inside the parent directory, which is
what makes DuckLake record a relative path — building it any other way produces
an absolute one and tests nothing.
"""

import os
import pathlib
import shutil
from datetime import UTC, datetime

import duckdb
import pytest

import sdmxlib as sl
from sdmxlib.model.binding import DataflowBinding
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure, Dimension, Measure
from sdmxlib.model.organisation import Agency
from sdmxlib.model.ref import Ref
from tests.support.factories import concept_ref

pytestmark = pytest.mark.unit


@pytest.fixture
def relative_bundle(tmp_path: pathlib.Path) -> pathlib.Path:
    """A publication whose DuckLake catalog records a relative data path."""
    with sl.LocalRegistry.build(tmp_path / "abundle") as builder:
        publication = builder.publication
    previous = pathlib.Path.cwd()
    os.chdir(tmp_path)
    try:
        conn = duckdb.connect(":memory:")
        conn.execute("INSTALL ducklake")
        conn.execute("LOAD ducklake")
        conn.execute(f"ATTACH 'ducklake:abundle/{publication.name}/lake.ducklake' AS lake (DATA_PATH 'abundle/data/')")
        conn.execute("CREATE SCHEMA lake.d")
        conn.execute("CREATE TABLE lake.d.obs AS SELECT 'CA' AS GEO, 1.0 AS OBS_VALUE")
        conn.close()
    finally:
        os.chdir(previous)
    return publication


def test_the_fixture_really_records_a_relative_path(relative_bundle):
    """Without this the rest of the file passes against the unfixed code.

    An absolute recorded path matches what `bundle()` passes, so the mismatch
    never arises and the ATTACH succeeds either way.
    """
    catalog = duckdb.connect(str(relative_bundle / "lake.ducklake"), read_only=True)
    stored = catalog.execute("SELECT value FROM ducklake_metadata WHERE key = 'data_path'").fetchone()
    catalog.close()
    assert stored is not None
    assert not stored[0].startswith("/"), f"fixture recorded an absolute path {stored[0]!r}"


def _bind(registry: sl.LocalRegistry) -> Ref[Dataflow]:
    """Register a dataflow bound to the lake table, and return its ref.

    Going through `obs()` rather than `reg.sql()` is deliberate: the
    lake is ATTACHed **lazily**, when something first asks for observations, so
    raw SQL never triggers it and could not see the regression at all.
    """
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
    registry.add(dsd)
    registry.add(flow)
    registry.add(
        DataflowBinding(
            dataflow=Ref[Dataflow].to(flow),
            table_location="d.obs",
            database_role="lake",
            dimension_column_map={"GEO": "GEO"},
            measure_column_map={"OBS_VALUE": "OBS_VALUE"},
            structural_version="SDMX:DSD(1.0)",
            source_registry="test",
            materialized_at=datetime(2026, 1, 1, tzinfo=UTC),
            bound_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    return Ref[Dataflow].to(flow)


def test_a_bundle_with_a_relative_data_path_opens(relative_bundle):
    """The regression: the lazy ATTACH raised a DuckDB `Error`."""
    registry = sl.LocalRegistry.build(relative_bundle.parent, resume=relative_bundle.name)
    try:
        flow = _bind(registry)
        assert registry.obs(flow).count() == 1
    finally:
        registry.close()


def test_the_bundle_reads_from_the_directory_it_was_given(relative_bundle, tmp_path):
    """Moving the bundle must not send it looking at the old location.

    The point of overriding: the caller says where the bundle is, and that wins
    over what the catalog remembers. A copy under a different name proves the
    recorded path is not being consulted — the parquet is only findable
    relative to the *new* directory.
    """
    moved = tmp_path / "moved-elsewhere"
    shutil.copytree(relative_bundle.parent, moved)

    registry = sl.LocalRegistry.build(moved, resume=relative_bundle.name)
    try:
        flow = _bind(registry)
        assert registry.obs(flow).count() == 1
    finally:
        registry.close()
