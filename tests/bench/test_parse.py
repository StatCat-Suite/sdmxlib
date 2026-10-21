"""Parse-throughput benchmarks.

For each wire format and each size, serialise the synthetic message
once (session-scoped), then benchmark ``parse(bytes)``.
"""

import pytest

from sdmxlib.formats import parse, serialize
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


@pytest.fixture(scope="session")
def payloads(sized_message) -> dict[str, bytes]:
    return {v: serialize(sized_message, version=v) for v in _VERSIONS}


@pytest.mark.parametrize("version", _VERSIONS)
def test_parse(benchmark, payloads: dict[str, bytes], version: str) -> None:
    data = payloads[version]
    benchmark(parse, data)
