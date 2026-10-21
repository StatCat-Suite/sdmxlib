"""InMemoryRegistry build + lookup benchmarks.

Two hot paths:

- ``add_all(artefacts)`` from a freshly parsed message — measures the
  intern / index-build cost.
- ``get(urn)`` lookups in a steady-state registry — measures the index
  read cost (URN-keyed dict access plus any resolution shimming).
"""

import pytest

from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.in_memory_registry import InMemoryRegistry
from sdmxlib.model.ref import artefact_urn
from sdmxlib.model.urn import SdmxUrn
from tests.bench.fixtures import make_message

_SIZES = [
    pytest.param(100, id="100"),
    pytest.param(1000, id="1k"),
    pytest.param(10000, id="10k"),
]


@pytest.fixture(scope="session", params=_SIZES)
def sized_artefacts(request: pytest.FixtureRequest) -> list[MaintainableArtefact]:
    msg = make_message(n_dataflows=request.param, n_dims=5, codes_per_dim=20)
    return msg.all_artefacts()


@pytest.fixture(scope="session")
def populated_registry(sized_artefacts: list[MaintainableArtefact]) -> InMemoryRegistry:
    reg = InMemoryRegistry()
    reg.add_all(sized_artefacts)
    return reg


@pytest.fixture(scope="session")
def urns(sized_artefacts: list[MaintainableArtefact]) -> list[SdmxUrn[object]]:
    return [artefact_urn(a) for a in sized_artefacts]


def test_registry_add_all(benchmark, sized_artefacts: list[MaintainableArtefact]) -> None:
    """Bench: intern + index N artefacts into a fresh registry."""

    def build() -> None:
        InMemoryRegistry().add_all(sized_artefacts)

    benchmark(build)


def test_registry_lookup(benchmark, populated_registry: InMemoryRegistry, urns: list[SdmxUrn[object]]) -> None:
    """Bench: lookup every artefact by URN in a steady-state registry."""

    # Sanity: the URN list must actually resolve, else the bench measures
    # nothing but dict-miss cost.
    assert populated_registry.get(urns[0]) is not None

    def loop() -> None:
        for urn in urns:
            populated_registry.get(urn)

    benchmark(loop)
