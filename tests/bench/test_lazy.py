"""LazyCodelist benchmarks.

Measures the cost of constructing a LazyCodelist facade and pulling
common views out of it (``code_map(lang="en")`` is the canonical use).
"""

from collections.abc import Generator

import pytest

from sdmxlib.local import LocalRegistry
from sdmxlib.model.ref import artefact_urn
from tests.bench.fixtures import make_codelist

_SIZES = [
    pytest.param(100, id="100"),
    pytest.param(1000, id="1k"),
    pytest.param(10000, id="10k"),
]


@pytest.fixture(scope="session", params=_SIZES)
def populated_registry(request: pytest.FixtureRequest) -> Generator[tuple[LocalRegistry, str]]:
    """A fresh LocalRegistry containing one Codelist of N codes."""
    cl = make_codelist(f"CL_BENCH_{request.param}", request.param)
    reg = LocalRegistry(":memory:").open()
    reg.add(cl)
    yield reg, str(artefact_urn(cl))
    reg.close()


def test_lazy_codelist_construct(benchmark, populated_registry: tuple[LocalRegistry, str]) -> None:
    """Bench: get a LazyCodelist from the registry (spine row + facade)."""
    reg, urn = populated_registry
    benchmark(reg.codelist, urn)


def test_lazy_codelist_code_map(benchmark, populated_registry: tuple[LocalRegistry, str]) -> None:
    """Bench: LazyCodelist.code_map(lang="en") — SQL-backed materialise."""
    reg, urn = populated_registry
    lazy = reg.codelist(urn)
    assert lazy is not None

    def run() -> dict[str, str]:
        return lazy.code_map(lang="en")

    benchmark(run)
