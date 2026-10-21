"""DuckDB storage layer benchmarks.

Measures the cost of writing artefacts into a fresh in-memory DuckDB
schema. Compared sizes surface the per-row overhead of the writer.
"""

import duckdb
import pytest

from sdmxlib.storage.schema import create_schema
from sdmxlib.storage.writers import put_codelist, put_data_structure
from tests.bench.fixtures import make_codelist, make_dsd

_CL_SIZES = [
    pytest.param(100, id="100"),
    pytest.param(1000, id="1k"),
    pytest.param(10000, id="10k"),
]
_DSD_SIZES = [
    pytest.param((3, 50), id="3x50"),
    pytest.param((10, 200), id="10x200"),
]


@pytest.fixture
def fresh_conn():
    conn = duckdb.connect(":memory:")
    try:
        # create_schema returns a cursor with search_path = 'sdmx' set;
        # the writers expect that cursor, not the bare connection.
        yield create_schema(conn)
    finally:
        conn.close()


@pytest.mark.parametrize("n_codes", _CL_SIZES)
def test_put_codelist(benchmark, fresh_conn, n_codes: int) -> None:
    """Bench: write a fresh Codelist of N codes into DuckDB."""
    cl = make_codelist(f"CL_BENCH_{n_codes}", n_codes)

    def run() -> None:
        # New connection each iteration would amortise the schema cost;
        # we want steady-state writer cost, so reuse the connection and
        # rely on put_codelist's idempotent upsert.
        put_codelist(fresh_conn, cl)

    benchmark(run)


@pytest.mark.parametrize("dims_codes", _DSD_SIZES)
def test_put_data_structure(benchmark, fresh_conn, dims_codes: tuple[int, int]) -> None:
    """Bench: write a DSD plus its backing codelists + concept scheme."""
    n_dims, codes_per_dim = dims_codes
    dsd, codelists, cs = make_dsd("DSD_BENCH", n_dims=n_dims, codes_per_dim=codes_per_dim)
    from sdmxlib.storage.writers import put_concept_scheme

    def run() -> None:
        put_concept_scheme(fresh_conn, cs)
        for cl in codelists:
            put_codelist(fresh_conn, cl)
        put_data_structure(fresh_conn, dsd)

    benchmark(run)
