"""A binding whose catalog is not attached says so (design §5.17).

§5.17 read this as a hole in §2's absence rule: `obs()`,
`write_observations()` and `browse()` "need a lake, which only
`LocalRegistry.edition()` attaches — so on `LocalRegistry("metadata.duckdb")` they
must *raise*, which §2 says only a type can prevent."

**Measured, the premise does not hold.** `write_observations` refuses on a
registry without a lake, naming `LocalRegistry.build` (GH-198). `browse()`
answers on a plain registry, with an empty list. And `obs()` answers perfectly well
on a plain registry whose binding points at a local table, which
`test_registry_data_plane.py` has relied on all along. None of the three needs a
lake.

What needs the lake is a **binding**, when its `database_role` names one. That
is data, and data cannot be checked by a type — so §2's rule was never in
tension and `EditionRegistry` does not need to exist for this reason.

What *was* wrong is the refusal. A bundle's binding read through a plain
registry reached DuckDB as ``Binder Error: Catalog "lake" does not exist!``,
naming neither the binding nor the fix — the same shape §5.8 and §5.9 removed
elsewhere.
"""

from __future__ import annotations

from datetime import UTC, datetime

import polars as pl
import pytest

import sdmxlib as sl
from sdmxlib.model.binding import DataflowBinding
from sdmxlib.model.ref import Ref
from tests.support.bundles import publish
from tests.support.factories import concept_ref

pytestmark = pytest.mark.unit

_DSD = sl.DataStructure(
    id="D",
    maintainer="AG",
    version="1.0",
    name={"en": "d"},
    dimensions=[sl.Dimension(id="REF_AREA", concept=concept_ref("REF_AREA"))],
    measures=[sl.Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
)
_FLOW = sl.Dataflow(id="DF", maintainer="AG", version="1.0", name={"en": "f"}, structure=Ref.to(_DSD))


def _binding(role: str) -> DataflowBinding:
    return DataflowBinding(
        dataflow=Ref.to(_FLOW),
        database_role=role,
        table_location="data.obs",
        dimension_column_map={"REF_AREA": "REF_AREA"},
        measure_column_map={"OBS_VALUE": "OBS_VALUE"},
        attribute_column_map={},
        structural_version="AG:D(1.0)",
        source_registry="local",
        materialized_at=datetime(2026, 1, 1, tzinfo=UTC),
        bound_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


@pytest.fixture
def reg():
    """A plain registry — no lake attached — holding one dataflow and a table."""
    with sl.LocalRegistry(":memory:") as registry:
        registry.add_all([_DSD, _FLOW])
        cursor = registry.connection.cursor()
        cursor.execute("CREATE SCHEMA IF NOT EXISTS data")
        cursor.execute("CREATE TABLE data.obs (REF_AREA TEXT, OBS_VALUE DOUBLE)")
        cursor.execute("INSERT INTO data.obs VALUES ('ON', 1.0)")
        yield registry


# ── the premise §5.17 rested on ───────────────────────────────────────────


def test_observations_answers_on_a_plain_registry(reg) -> None:
    """The claim that decides §5.17. If this raised, the method really would
    need a lake and §2's rule really would be in tension."""
    reg.add(_binding("memory"))
    assert reg.obs(_FLOW).count() == 1


def test_browse_answers_on_a_plain_registry(reg) -> None:
    assert reg.browse() == []


def test_write_observations_without_a_lake_says_where_one_comes_from(reg) -> None:
    """A builder writes through the registry (GH-198); one with no lake refuses, naming `build`."""
    with pytest.raises(KeyError) as caught:
        reg.write_observations(schema="data", table="obs", source=pl.DataFrame({"REF_AREA": ["CA"]}))
    message = str(caught.value)
    assert "no lake" in message
    assert "LocalRegistry.build(product)" in message


# ── the refusal that was actually missing ─────────────────────────────────


def test_an_unattached_catalog_is_named(reg) -> None:
    """Was ``Binder Error: Catalog "lake" does not exist!`` — true, and useless
    to a caller who does not know that `lake` is what `edition()` attaches."""
    reg.add(_binding("lake"))
    with pytest.raises(KeyError) as caught:
        reg.obs(_FLOW)

    message = str(caught.value)
    assert "AG:DF(1.0)" in message, "which dataflow"
    assert "lake.data.obs" in message, "and where it thinks its rows are"
    assert "edition(" in message, "and the constructor that attaches it"


def test_the_refusal_lists_what_is_attached(reg) -> None:
    """ "No catalog named 'lake'" invites "well, what is there?". Answering it
    costs one column and turns a guess into a look."""
    reg.add(_binding("lake"))
    with pytest.raises(KeyError, match="memory"):
        reg.obs(_FLOW)


def test_the_refusal_comes_before_any_query_runs(reg) -> None:
    """`obs()` is lazy, but this refusal is not deferred to the
    terminal: the binding is wrong the moment it is read, and a caller who
    builds a filter first would otherwise see the error attributed to the
    filter."""
    reg.add(_binding("lake"))
    with pytest.raises(KeyError):
        reg.obs(_FLOW)


def test_a_bundle_binding_works_in_a_bundle(tmp_path) -> None:
    """The other side: the same binding is fine where the catalog exists, so
    the check is about attachment rather than about the name `lake`."""
    import polars as pl

    with sl.LocalRegistry.build(tmp_path) as writer:
        writer.add_all([_DSD, _FLOW])
        writer._data_plane().write_observations(
            schema="data",
            table="obs",
            source=pl.DataFrame({"REF_AREA": ["ON"], "OBS_VALUE": [1.0]}),
        )
        writer.add(DataflowBinding.from_dsd(_DSD, dataflow=Ref.to(_FLOW), schema="data", table="obs"))
        publication = writer.edition_dir

    with sl.LocalRegistry.edition(publish(publication)) as bundled:
        assert bundled.obs(_FLOW).count() == 1
