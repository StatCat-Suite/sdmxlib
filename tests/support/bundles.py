"""Small real bundles, for the bundle contract's tests (GH-66, GH-67).

Real in every part that matters: a publication started by
`LocalRegistry.build`, its `meta.duckdb`, a DuckLake catalog, and data files in
the bundle's shared ``data/`` when the table is large enough that DuckLake does
not inline it into the catalog.
"""

from pathlib import Path

import sdmxlib as sl
from sdmxlib.bundle import Producer, write_manifest
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
    """The first publication of a bundle at `root`, unpublished: `rows` observations bound to `FLOW`, and `extra`.

    Returns the publication directory, ``<root>/<token>/``; its data files are
    in ``<root>/data/``. `concept_ref` points at a concept scheme the store
    does not hold, so the bundle's structural closure is not complete; a test
    checking references adds the scheme through `extra`.
    """
    root.mkdir(parents=True)
    with sl.LocalRegistry.build(root) as reg:
        reg._data_plane()  # ATTACHes the lake
        reg.connection.execute("CREATE SCHEMA lake.d")
        reg.connection.execute(
            "CREATE TABLE lake.d.obs AS SELECT 'G' || (range % 7) AS GEO, range::DOUBLE AS OBS_VALUE FROM range(?)",
            [rows],
        )
        reg.add_all([*extra, DSD, FLOW])
        reg.add(DataflowBinding.from_dsd(DSD, dataflow=Ref.to(FLOW), schema="d", table="obs", database_role="lake"))
        return reg.publication


def publish(root: Path) -> Path:
    """The publication `root` published with `write_manifest`, so `LocalRegistry.bundle` opens it (GH-122)."""
    write_manifest(root, bundle_id=root.parent.name, producer=Producer(name="tests", version="0"))
    return root
