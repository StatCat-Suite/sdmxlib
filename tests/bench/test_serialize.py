"""Serialise-throughput benchmarks. Symmetric to test_parse."""

import pytest

from sdmxlib.formats import serialize
from tests.bench.fixtures import make_message

_SIZES = [
    pytest.param((10, 5, 20), id="small"),
    pytest.param((100, 8, 100), id="medium"),
    pytest.param((500, 10, 200), id="large"),
]
_VERSIONS = ["2.1", "3.0", "3.1", "json"]


@pytest.fixture(scope="session", params=_SIZES)
def sized_message(request: pytest.FixtureRequest):
    n_dataflows, n_dims, codes_per_dim = request.param
    return make_message(n_dataflows=n_dataflows, n_dims=n_dims, codes_per_dim=codes_per_dim)


@pytest.mark.parametrize("version", _VERSIONS)
def test_serialize(benchmark, sized_message, version: str) -> None:
    benchmark(serialize, sized_message, version=version)
