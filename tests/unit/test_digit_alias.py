"""An edition whose catalog alias starts with a digit attaches, checks and reads (GH-356).

An edition in an `EditionEngine` attaches its lake as
``<product_id>__<edition_id>__tables``. statcatlib names a product after its
StatCan table id, ``14100287``, and an unquoted DuckDB identifier cannot start
with a digit, so every statement naming the alias quotes it.
"""

from pathlib import Path

import duckdb
import polars as pl
import pytest

import sdmxlib as sl
from sdmxlib.edition import check_edition
from sdmxlib.local.data_store import DuckDBDataStore
from tests.support.bundles import FLOW, make_bundle, publish

pytestmark = pytest.mark.unit

_PRODUCT = "14100287"


@pytest.fixture(scope="module")
def edition(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A sealed edition of the product ``14100287``, holding 10 observations of `FLOW`."""
    return publish(make_bundle(tmp_path_factory.mktemp("products") / _PRODUCT, 10))


def test_the_alias_starts_with_the_product_id(edition: Path) -> None:
    with sl.LocalRegistry.edition(edition) as reg:
        assert reg.lake_alias.startswith(f"{_PRODUCT}__")
        with pytest.raises(duckdb.ParserException):
            reg.connection.execute(f"SELECT 1 FROM {reg.lake_alias}.d.obs")


def test_the_edition_checks_clean_at_publish(edition: Path) -> None:
    report = check_edition(edition, at="publish")
    assert report.findings == ()
    assert "lake" in report.checked
    assert "fingerprints" in report.checked
    assert "availability" in report.checked


def test_the_edition_returns_its_observations(edition: Path) -> None:
    with sl.LocalRegistry.edition(edition) as reg:
        query = reg.obs(FLOW.urn)
        assert query.count() == 10
        assert query.frame().height == 10
        assert reg.obs(FLOW.urn).filter(query.col("GEO") == "G1").count() == 2
        availability = reg.availability(FLOW.urn)
        assert availability is not None
        assert availability.obs_count == 10


def test_a_write_and_an_ingest_reach_a_catalog_whose_alias_starts_with_a_digit(tmp_path: Path) -> None:
    csv = tmp_path / "obs.csv"
    csv.write_text("GEO,OBS_VALUE\nA,1\nB,2\nC,3\n")
    with sl.LocalRegistry(":memory:") as local:
        local.connection.execute("ATTACH ':memory:' AS \"2021\"")
        store = DuckDBDataStore.from_registry(local)
        store.write_observations(schema="d", table="obs", source=pl.DataFrame({"GEO": ["A", "B"]}), lake_alias="2021")
        store.ingest_csv(schema="d", table="csv", path=csv, types={"GEO": "VARCHAR"}, lake_alias="2021")
        assert local.connection.execute('SELECT count(*) FROM "2021".d.obs').fetchall() == [(2,)]
        assert local.connection.execute('SELECT count(*) FROM "2021".d.csv').fetchall() == [(3,)]


def test_a_name_that_could_close_the_quotes_is_still_refused() -> None:
    with sl.LocalRegistry(":memory:") as local, pytest.raises(ValueError, match="Invalid SQL identifier"):
        DuckDBDataStore.from_registry(local, attach={'x" AS y; --': "':memory:'"})
