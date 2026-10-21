"""Shared unit-test fixtures.

Each artefact is exposed two ways:

* `make_<name>` — factory fixture that returns a callable accepting
  keyword overrides. Use when a test needs to build several variants
  or override defaults.
* `<name>` — plain fixture returning the default instance. Use when the
  test only needs one.

The single shared `cursor` fixture creates an in-memory DuckDB connection
with the sdmx schema applied.

Defaults are deliberately minimal — they don't try to model anything
realistic. Tests that care about the structural shape should override
explicitly via the factory.
"""

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import duckdb
import pytest
from lxml import etree

from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.concept import Concept, ConceptScheme
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure, Dimension, Measure
from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy
from sdmxlib.model.ref import Ref
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.storage.schema import create_schema

type _DuckDBConfig = dict[str, str | bool | int | float | list[str]]


@pytest.fixture(autouse=True)
def _one_duckdb_thread(request: pytest.FixtureRequest) -> Iterator[None]:
    """Every DuckDB connection the unit tier opens runs on one thread, unless the caller says otherwise.

    DuckDB gives each connection a pool of one thread per core. Under
    `just test`'s ten workers that was a hundred threads on ten cores, and the
    parallel phase spent most of its CPU creating and scheduling them:
    measured, ~105 s and ~590 CPU-s at the default, 97-99 s at 2 threads,
    71-87 s and 330-390 CPU-s at 1, with every test passing at each.

    What one thread cannot do is vary the order of an unordered result, which
    is how a missing ``ORDER BY`` would show. Unit-test tables are far below the
    ~120,000-row group DuckDB splits a scan at, so they ran on one thread
    regardless. The bundle tier, on real data, keeps the default; this fixture
    is the unit tier's alone.

    The `serial` tests keep the default too. They race threads over one
    registry to reproduce #75, and with one DuckDB thread the race they exist
    to catch stopped showing: their sabotage check failed. They run alone, so
    the default costs nothing there.
    """
    if request.node.get_closest_marker("serial") is not None:
        yield
        return
    connect = duckdb.connect

    def one_thread(
        database: str | Path = ":memory:", *, read_only: bool = False, config: _DuckDBConfig | None = None
    ) -> duckdb.DuckDBPyConnection:
        return connect(database, read_only=read_only, config={"threads": 1, **(config or {})})

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(duckdb, "connect", one_thread)
        yield


@pytest.fixture
def cursor() -> duckdb.DuckDBPyConnection:
    """Fresh in-memory DuckDB connection with the sdmx schema created."""
    conn = duckdb.connect(":memory:")
    return create_schema(conn)


# ── SDMX-ML XSD schemas (session-scoped — compilation is ~100 ms each) ──


@pytest.fixture(scope="session")
def sdmx_ml21_schema() -> etree.XMLSchema:
    """Compiled SDMX-ML 2.1 XSD set; see tests/schemas/README.md."""
    from tests.support.xsd import compile_structure_schema

    return compile_structure_schema("2.1")


@pytest.fixture(scope="session")
def sdmx_ml30_schema() -> etree.XMLSchema:
    """Compiled SDMX-ML 3.0 XSD set; see tests/schemas/README.md."""
    from tests.support.xsd import compile_structure_schema

    return compile_structure_schema("3.0")


@pytest.fixture(scope="session")
def sdmx_ml31_schema() -> etree.XMLSchema:
    """Compiled SDMX-ML 3.1 XSD set; see tests/schemas/README.md.

    Available for forward-compat checks of writer output that targets 3.0
    against the newer schema. Our writers don't yet emit 3.1-specific
    shapes.
    """
    from tests.support.xsd import compile_structure_schema

    return compile_structure_schema("3.1")


@pytest.fixture
def make_codelist() -> Callable[..., Codelist]:
    """Factory: build a Codelist with defaults overridable by kwargs."""

    def _make(**kwargs: Any) -> Codelist:
        defaults: dict[str, Any] = {
            "id": "CL_FREQ",
            "maintainer": "SDMX",
            "version": "1.0",
            "name": {"en": "Frequency"},
            "codes": [
                Code(id="A", name={"en": "Annual"}),
                Code(id="Q", name={"en": "Quarterly"}),
            ],
        }
        defaults.update(kwargs)
        return Codelist(**defaults)

    return _make


@pytest.fixture
def codelist(make_codelist: Callable[..., Codelist]) -> Codelist:
    """Default Codelist (SDMX:CL_FREQ(1.0))."""
    return make_codelist()


@pytest.fixture
def make_concept_scheme() -> Callable[..., ConceptScheme]:
    """Factory: build a ConceptScheme with defaults overridable by kwargs."""

    def _make(**kwargs: Any) -> ConceptScheme:
        defaults: dict[str, Any] = {
            "id": "CS_COMMON",
            "maintainer": "SDMX",
            "version": "1.0",
            "name": {"en": "Common Concepts"},
            "concepts": [
                Concept(id="FREQ", name={"en": "Frequency"}, description={"en": "Observation frequency"}),
                Concept(id="REF_AREA", name={"en": "Reference Area"}),
            ],
        }
        defaults.update(kwargs)
        return ConceptScheme(**defaults)

    return _make


@pytest.fixture
def concept_scheme(make_concept_scheme: Callable[..., ConceptScheme]) -> ConceptScheme:
    """Default ConceptScheme (SDMX:CS_COMMON(1.0))."""
    return make_concept_scheme()


@pytest.fixture
def make_dsd() -> Callable[..., DataStructure]:
    """Factory: build a DataStructure with defaults overridable by kwargs.

    The default DSD has one enumerated dimension (FREQ → CL_FREQ) and one
    measure (OBS_VALUE). Pass ``dimensions=`` / ``measures=`` / etc. to
    fully replace the relevant list.
    """

    def _make(**kwargs: Any) -> DataStructure:
        cl_urn = SdmxUrn.make(
            Codelist,
            package="codelist",
            artefact_class="Codelist",
            agency="SDMX",
            id="CL_FREQ",
            version="1.0",
        )
        concept_urn = Concept.urn_for(agency="SDMX", scheme="CS_COMMON", id="FREQ")
        defaults: dict[str, Any] = {
            "id": "DSD_TEST",
            "maintainer": "SDMX",
            "version": "1.0",
            "name": {"en": "Test DSD"},
            "dimensions": [
                Dimension(
                    id="FREQ",
                    concept=Ref[Concept].unresolved(concept_urn),
                    representation=Ref[Codelist].unresolved(cl_urn),
                ),
            ],
            "measures": [
                Measure(
                    id="OBS_VALUE",
                    concept=Ref[Concept].unresolved(Concept.urn_for(agency="SDMX", scheme="CS_COMMON", id="OBS_VALUE")),
                )
            ],
        }
        defaults.update(kwargs)
        return DataStructure(**defaults)

    return _make


@pytest.fixture
def dsd(make_dsd: Callable[..., DataStructure]) -> DataStructure:
    """Default DataStructure (SDMX:DSD_TEST(1.0))."""
    return make_dsd()


@pytest.fixture
def make_dataflow() -> Callable[..., Dataflow]:
    """Factory: build a Dataflow with defaults overridable by kwargs."""

    def _make(**kwargs: Any) -> Dataflow:
        dsd_urn = SdmxUrn.make(
            DataStructure,
            package="datastructure",
            artefact_class="DataStructure",
            agency="SDMX",
            id="DSD_TEST",
            version="1.0",
        )
        defaults: dict[str, Any] = {
            "id": "DF_TEST",
            "maintainer": "SDMX",
            "version": "1.0",
            "name": {"en": "Test Dataflow"},
            "description": {"en": "A test dataflow"},
            "structure": dsd_urn,
        }
        defaults.update(kwargs)
        return Dataflow(**defaults)

    return _make


@pytest.fixture
def dataflow(make_dataflow: Callable[..., Dataflow]) -> Dataflow:
    """Default Dataflow (SDMX:DF_TEST(1.0))."""
    return make_dataflow()


def _geo_code(code_id: str) -> Ref[Code]:
    """A hierarchical code places a code (#492: the store refuses one that does not)."""
    return Ref[Code].unresolved(Code.parse_urn(f"urn:sdmx:org.sdmx.infomodel.codelist.Code=SDMX:CL_GEO(1.0).{code_id}"))


@pytest.fixture
def make_hierarchy() -> Callable[..., Hierarchy]:
    """Factory: build a Hierarchy with defaults overridable by kwargs."""

    def _make(**kwargs: Any) -> Hierarchy:
        defaults: dict[str, Any] = {
            "id": "H_GEO",
            "maintainer": "SDMX",
            "version": "1.0",
            "name": {"en": "Geography"},
            "tree": [
                HierarchicalCode(
                    id="CANADA",
                    code=_geo_code("CANADA"),
                    children=[
                        HierarchicalCode(id="ON", code=_geo_code("ON")),
                        HierarchicalCode(id="QC", code=_geo_code("QC")),
                    ],
                ),
            ],
        }
        defaults.update(kwargs)
        return Hierarchy(**defaults)

    return _make


@pytest.fixture
def hierarchy(make_hierarchy: Callable[..., Hierarchy]) -> Hierarchy:
    """Default Hierarchy (SDMX:H_GEO(1.0))."""
    return make_hierarchy()
