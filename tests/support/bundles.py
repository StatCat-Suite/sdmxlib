"""Small real bundles, for the bundle contract's tests (GH-66, GH-67).

Real in every part that matters: a `meta.duckdb` written by `LocalRegistry`, a
DuckLake catalog, and parquet files when the table is large enough that
DuckLake does not inline it into the catalog.
"""

from pathlib import Path

import duckdb

import sdmxlib as sl
from sdmxlib.model.binding import DataflowBinding
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure, Dimension, Measure
from sdmxlib.model.organisation import Agency
from sdmxlib.model.ref import Ref, SupportsUrn
from tests.support.factories import concept_ref

DSD = DataStructure(
    id="DSD",
    maintainer=Agency(id="SDMX"),
    version="1.0",
    name={"en": "DSD"},
    dimensions=[Dimension(id="GEO", concept=concept_ref("GEO"))],
    measures=[Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
)
FLOW = Dataflow(id="F", maintainer=Agency(id="SDMX"), version="1.0", name={"en": "F"}, structure=Ref.to(DSD))


def make_bundle(root: Path, rows: int, *, extra: tuple[SupportsUrn, ...] = ()) -> Path:
    """A bundle at `root` of `rows` observations bound to `FLOW`, with `extra` artefacts in its store.

    `concept_ref` points at a concept scheme the store does not hold, so the
    bundle's structural closure is not complete; a test checking references
    adds the scheme through `extra`.
    """
    root.mkdir()
    conn = duckdb.connect(":memory:")
    conn.execute("INSTALL ducklake")
    conn.execute("LOAD ducklake")
    conn.execute(f"ATTACH 'ducklake:{root}/lake.ducklake' AS lake (DATA_PATH '{root}/parquet/')")
    conn.execute("CREATE SCHEMA lake.d")
    conn.execute(
        f"CREATE TABLE lake.d.obs AS SELECT 'G' || (range % 7) AS GEO, range::DOUBLE AS OBS_VALUE FROM range({rows})"
    )
    conn.close()
    with sl.LocalRegistry(root / "meta.duckdb") as reg:
        reg.add_all([*extra, DSD, FLOW])
        reg.add(DataflowBinding.from_dsd(DSD, dataflow=Ref.to(FLOW), schema="d", table="obs", database_role="lake"))
    return root
