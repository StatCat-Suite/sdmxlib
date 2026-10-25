"""A bundle whose data files are read from object storage (GH-180).

`file://` stands in for a store: DuckDB reads it as it reads ``s3://`` or
``az://``, through the same `remote=` path, so these run without one. The
same sequence against Azurite, an S3 server and a GCS emulator is
``tests/stores/test_remote_bundle.py``.
"""

from collections.abc import Callable
from pathlib import Path

import polars as pl
import pytest

import sdmxlib as sl
from sdmxlib.bundle import check_bundle, check_on_open, copy_bundle, read_manifest
from sdmxlib.local.object_store import data_uri, extension_for, sizes
from sdmxlib.testing import Dim, Shape, synthetic_bundle

pytestmark = pytest.mark.unit

_FLOW = sl.Dataflow.urn_for(agency="SYNTHETIC", id="DF_SYNTHETIC", version="1.0")
_SHAPE = Shape(dimensions=(Dim(codes=20, depth=2, fanout=(2, 4)), Dim(codes=5)), observations=300, periods=4)


@pytest.mark.parametrize(
    ("remote", "expected"),
    [
        (
            "abfss://c@acct.dfs.core.windows.net/bundles/census/019a/",
            "abfss://c@acct.dfs.core.windows.net/bundles/census/data/",
        ),
        ("az://bundles/census/019a", "az://bundles/census/data/"),
        ("s3://bucket/a/b/019a/", "s3://bucket/a/b/data/"),
        ("gs://bucket/census/019a/", "gs://bucket/census/data/"),
        ("https://host:8443/x/019a/", "https://host:8443/x/data/"),
        ("file:///srv/bundles/census/019a/", "file:///srv/bundles/census/data/"),
    ],
)
def test_data_path_resolves_against_the_remote_publication(remote: str, expected: str) -> None:
    assert data_uri(remote, "../data/") == expected


def test_a_data_path_inside_the_publication_resolves_below_it() -> None:
    assert data_uri("s3://b/census/019a/", "./data") == "s3://b/census/019a/data/"


def test_a_data_path_climbing_above_the_store_root_is_refused() -> None:
    with pytest.raises(ValueError, match="climbs above the root"):
        data_uri("az://bundles/", "../../data/")


@pytest.mark.parametrize("data_path", ["/data/", "s3://elsewhere/data/"])
def test_an_absolute_data_path_is_refused(data_path: str) -> None:
    with pytest.raises(ValueError, match="not relative"):
        data_uri("s3://b/census/019a/", data_path)


@pytest.mark.parametrize(
    ("remote", "message"),
    [("/srv/bundles/census/019a/", "is not a URI"), ("ftp://host/census/019a/", "DuckDB does not read")],
)
def test_a_remote_duckdb_cannot_read_is_refused_naming_the_schemes(remote: str, message: str) -> None:
    with pytest.raises(ValueError, match=message) as raised:
        data_uri(remote, "../data/")
    assert "s3://" in str(raised.value)
    assert "abfss://" in str(raised.value)


@pytest.mark.parametrize(
    ("uri", "extension"),
    [
        ("s3://b/k", "httpfs"),
        ("r2://b/k", "httpfs"),
        ("gs://b/k", "httpfs"),
        ("https://h/k", "httpfs"),
        ("az://c/k", "azure"),
        ("ABFSS://c@a.dfs.core.windows.net/k", "azure"),
        ("file:///srv/k", None),
    ],
)
def test_each_scheme_names_the_extension_that_reads_it(uri: str, extension: str | None) -> None:
    assert extension_for(uri) == extension


def test_a_secret_renders_as_duckdb_create_secret() -> None:
    secret = sl.StoreSecret(
        "s3",
        provider="config",
        options={"key_id": "k", "secret": "it's", "endpoint": "minio:9000", "use_ssl": False, "port": 9000},
        scope="s3://bucket/",
    )
    assert secret.statement() == (
        "CREATE OR REPLACE SECRET sdmxlib_s3 (TYPE s3, PROVIDER 'config', KEY_ID 'k', SECRET 'it''s', "
        "ENDPOINT 'minio:9000', USE_SSL false, PORT 9000, SCOPE 's3://bucket/')"
    )


def test_a_named_secret_keeps_its_name() -> None:
    statement = sl.StoreSecret("azure", name="second_account").statement()
    assert statement.startswith("CREATE OR REPLACE SECRET second_account ")


@pytest.mark.parametrize(
    ("make", "message"),
    [
        (lambda: sl.StoreSecret("ftp"), "type 'ftp' is not one of"),
        (lambda: sl.StoreSecret("s3", name="a; DROP"), "is not a DuckDB secret name"),
        (lambda: sl.StoreSecret("s3", options={"key id": "x"}), "is not a DuckDB secret option name"),
    ],
)
def test_a_secret_duckdb_would_misread_is_refused(make: Callable[[], sl.StoreSecret], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        make()


def test_the_engine_creates_its_secrets() -> None:
    secret = sl.StoreSecret("s3", options={"key_id": "k", "secret": "s", "endpoint": "localhost:1"}, name="probe")
    engine = sl.BundleEngine(secrets=[secret])
    try:
        names = engine.connection.execute("SELECT name, type FROM duckdb_secrets()").fetchall()
    finally:
        engine.close()
    assert ("probe", "s3") in names


@pytest.fixture(scope="module")
def published(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A synthetic publication, built and published locally."""
    bundle = tmp_path_factory.mktemp("store") / "census"
    return bundle / synthetic_bundle(bundle, _SHAPE, seed=5).token


@pytest.fixture
def pod(published: Path, tmp_path: Path) -> Path:
    """What a server holds: the publication's tier 1 and 2 files, and no data files."""
    held = tmp_path / "pod" / "census" / published.name
    copy_bundle(published, held, tiers=(1, 2))
    assert not (held.parent / "data").exists()
    return held


def _remote(published: Path) -> str:
    return f"file://{published}/"


def _rows(path: Path, *, remote: str | None = None) -> pl.DataFrame:
    with sl.LocalRegistry.bundle(path, remote=remote) as reg:
        return reg.obs(_FLOW).frame().sort("DIM_1", "DIM_2", "TIME_PERIOD")


def test_a_server_holding_tiers_1_and_2_reads_every_row_from_the_store(published: Path, pod: Path) -> None:
    assert _rows(pod, remote=_remote(published)).equals(_rows(published))


def test_the_registry_reports_where_it_reads_the_data_files(published: Path, pod: Path) -> None:
    with sl.LocalRegistry.bundle(pod, remote=_remote(published)) as reg:
        assert reg.data_path == f"file://{published.parent}/data/"
    with sl.LocalRegistry.bundle(published) as reg:
        assert reg.data_path == f"{(published.parent / 'data').resolve()}/"


def test_the_receive_check_passes_on_a_server_reading_the_store(published: Path, pod: Path) -> None:
    report = check_bundle(pod, at="receive", remote=_remote(published))
    assert report.ok, "\n".join(map(str, report.findings))
    assert "remote" in report.checked
    assert "lake" in report.checked


def test_check_on_open_stamps_a_server_reading_the_store(published: Path, pod: Path, tmp_path: Path) -> None:
    stamps = tmp_path / "stamps"
    first = check_on_open(pod, stamps=stamps, remote=_remote(published))
    second = check_on_open(pod, stamps=stamps, remote=_remote(published))
    assert first.ok, "\n".join(map(str, first.findings))
    assert (first.at, second.at) == ("receive", "open")


def test_a_data_file_missing_from_the_store_is_named_by_both_checks(published: Path, tmp_path: Path) -> None:
    store = tmp_path / "store" / "census"
    copy_bundle(published, store / published.name)
    pod = tmp_path / "pod" / "census" / published.name
    copy_bundle(published, pod, tiers=(1, 2))
    manifest = read_manifest(published)
    lost = next(f for f in manifest.files if f.root == "data")
    (store / "data" / lost.path).unlink()
    report = check_bundle(pod, at="receive", remote=f"file://{store / published.name}/")
    subjects = {(f.check, f.subject, f.detail) for f in report.findings}
    assert ("remote", f"../data/{lost.path}", "listed in the manifest but missing from the store") in subjects
    assert ("lake", f"../data/{lost.path}", "referenced by the DuckLake catalog but missing") in subjects


def test_the_local_check_still_names_the_data_files_a_server_without_remote_lacks(pod: Path) -> None:
    report = check_bundle(pod, at="receive")
    assert any(f.check == "files" and f.detail == "listed in the manifest but missing" for f in report.findings)


def test_remote_is_refused_at_publish(published: Path) -> None:
    with pytest.raises(ValueError, match="at='publish' hashes them locally"):
        check_bundle(published, at="publish", remote=_remote(published))


def test_sizes_reads_a_missing_file_as_none(published: Path) -> None:
    engine = sl.BundleEngine()
    try:
        data = published.parent / "data"
        held = next(p for p in data.rglob("*.parquet"))
        found = sizes(engine.connection, [f"file://{held}", f"file://{data}/absent.parquet"])
    finally:
        engine.close()
    assert found == {f"file://{held}": held.stat().st_size, f"file://{data}/absent.parquet": None}
