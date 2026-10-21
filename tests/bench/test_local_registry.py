"""sdmxlib.local.Registry benchmarks.

Measures the user-visible cost of loading and reading artefacts through
the DuckDB-backed LocalRegistry. Compared to ``InMemoryRegistry`` (covered in
``test_registry.py``), this exercises the SQL read path.
"""

import pytest

from sdmxlib.local import LocalRegistry
from sdmxlib.model.ref import artefact_urn
from tests.bench.fixtures import make_message

_SIZES = [
    pytest.param((10, 5, 100), id="small"),
    pytest.param((50, 8, 200), id="medium"),
]


@pytest.fixture(scope="session", params=_SIZES)
def populated_local_registry(request: pytest.FixtureRequest):
    """A fresh local LocalRegistry seeded from a synthetic StructureMessage."""
    n_dataflows, n_dims, codes_per_dim = request.param
    msg = make_message(n_dataflows=n_dataflows, n_dims=n_dims, codes_per_dim=codes_per_dim)
    reg = LocalRegistry(":memory:").open()
    reg.add_all(msg.all_artefacts())
    urns = {
        "codelist": str(artefact_urn(msg.codelists[0])),
        "dsd": str(artefact_urn(msg.data_structures[0])),
        "dataflow": str(artefact_urn(msg.dataflows[0])),
    }
    yield reg, urns
    reg.close()


def test_local_get_codelist_lazy(benchmark, populated_local_registry) -> None:
    """Default codelist lookup returns a LazyCodelist — spine only, no codes."""
    from sdmxlib.model.urn import SdmxUrn

    reg, urns = populated_local_registry
    urn = SdmxUrn.parse(urns["codelist"])
    benchmark(reg.get, urn)


def test_local_get_codelist_eager(benchmark, populated_local_registry) -> None:
    """eager=True fully materialises the Codelist with all codes."""
    from sdmxlib.model.urn import SdmxUrn

    reg, urns = populated_local_registry
    urn = SdmxUrn.parse(urns["codelist"])
    benchmark(reg.get, urn, eager=True)


def test_local_get_dsd(benchmark, populated_local_registry) -> None:
    """DSD read — joins components, concepts, codelist refs."""
    from sdmxlib.model.urn import SdmxUrn

    reg, urns = populated_local_registry
    urn = SdmxUrn.parse(urns["dsd"])
    benchmark(reg.get, urn)


def test_local_get_dataflow(benchmark, populated_local_registry) -> None:
    """Dataflow read — leaf artefact, single Ref to DSD."""
    from sdmxlib.model.urn import SdmxUrn

    reg, urns = populated_local_registry
    urn = SdmxUrn.parse(urns["dataflow"])
    benchmark(reg.get, urn)
