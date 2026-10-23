"""A bundle's manifest: a token per publication, a checksum per file (GH-66, GH-123).

Two builds from the same inputs differ in every file's bytes, so identity is
the writer's `token`, minted when a publication's build starts, and the sha256
per file is for integrity. These tests pin what a cache relies on: the token
changes with each publication and never when one is read.
"""

import json
import uuid
from pathlib import Path

import jsonschema
import pytest

import sdmxlib as sl
from sdmxlib.bundle import (
    DATA_PATH,
    MANIFEST_NAME,
    ManifestError,
    Producer,
    inventory,
    manifest_schema,
    read_manifest,
    write_manifest,
)
from sdmxlib.storage._manifest import generate_schema
from tests.support.bundles import FLOW as _FLOW
from tests.support.bundles import make_bundle as _bundle

pytestmark = pytest.mark.unit

_PRODUCER = Producer(name="test-builder", version="1.0")


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    return _bundle(tmp_path / "bundle", rows=200_000)


def test_the_token_build_minted_names_the_publication(bundle: Path) -> None:
    manifest = write_manifest(bundle, bundle_id="b", producer=_PRODUCER)
    assert uuid.UUID(manifest.token).version == 7
    assert bundle.name == manifest.token
    assert manifest.previous_token is None
    assert manifest.data_path == DATA_PATH


def test_a_publication_is_published_once(bundle: Path) -> None:
    write_manifest(bundle, bundle_id="b", producer=_PRODUCER)
    before = (bundle / MANIFEST_NAME).read_bytes()
    with pytest.raises(ManifestError, match=r"already published.*LocalRegistry\.build\(.*previous="):
        write_manifest(bundle, bundle_id="b", producer=_PRODUCER)
    assert (bundle / MANIFEST_NAME).read_bytes() == before


def test_a_directory_build_did_not_start_has_no_token_to_publish(tmp_path: Path) -> None:
    with sl.LocalRegistry(tmp_path / "meta.duckdb") as reg:
        reg.add(_FLOW)
    with pytest.raises(ManifestError, match=r"not started by LocalRegistry\.build"):
        write_manifest(tmp_path, bundle_id="b", producer=_PRODUCER)


def test_reading_a_bundle_changes_neither_its_token_nor_its_files(bundle: Path) -> None:
    """What a cache keyed on the token relies on."""
    published = write_manifest(bundle, bundle_id="b", producer=_PRODUCER)
    with sl.LocalRegistry.bundle(bundle) as reg:
        assert reg.get_all(sl.Dataflow)
        assert reg.obs(_FLOW.urn).frame().height == 200_000
        manifest = reg.manifest
        assert manifest is not None
        assert manifest.token == published.token
    assert inventory(bundle) == tuple(f for f in published.files if f.root == "publication")
    assert read_manifest(bundle).token == published.token


def test_the_files_are_the_publications_and_the_data_files_its_lake_references(bundle: Path) -> None:
    manifest = write_manifest(bundle, bundle_id="b", producer=_PRODUCER)
    publication = sorted(p.relative_to(bundle).as_posix() for p in bundle.rglob("*") if p.is_file())
    data = bundle.parent / "data"
    data_files = sorted(p.relative_to(data).as_posix() for p in data.rglob("*") if p.is_file())
    assert [f.path for f in manifest.files if f.root == "publication"] == [p for p in publication if p != MANIFEST_NAME]
    assert [f.path for f in manifest.files if f.root == "data"] == data_files
    assert len(data_files) == 1, "a table this size is not inlined, and one insert writes one file"


@pytest.mark.parametrize("rows", [1, 200_000], ids=["inlined", "parquet"])
def test_observation_counts_come_from_the_lake(tmp_path: Path, rows: int) -> None:
    """Counted through the bound table, so an inlined table, which has no
    parquet file, is counted as well as one that has."""
    manifest = write_manifest(_bundle(tmp_path / "b", rows), bundle_id="b", producer=_PRODUCER)
    assert [(b.dataflow, b.observations) for b in manifest.bindings] == [(str(_FLOW.urn), rows)]


def test_a_newer_minor_format_reads_and_a_new_major_is_refused(bundle: Path) -> None:
    write_manifest(bundle, bundle_id="b", producer=_PRODUCER)
    path = bundle / MANIFEST_NAME
    document = json.loads(path.read_text())
    path.write_text(json.dumps(document | {"format_version": "2.7", "added_in_2_7": True}))
    assert read_manifest(bundle).bundle_id == "b"
    path.write_text(json.dumps(document | {"format_version": "3.0"}))
    with pytest.raises(ManifestError, match=r"manifest format 3\.0; this sdmxlib reads 2\.x"):
        read_manifest(bundle)
    path.write_text(json.dumps(document | {"format_version": "1.1"}))
    with pytest.raises(ManifestError, match=r"manifest format 1\.1; this sdmxlib reads 2\.x"):
        read_manifest(bundle)
    path.write_text("{not json")
    with pytest.raises(ManifestError, match="not a bundle manifest"):
        read_manifest(bundle)


def test_a_manifest_on_another_schema_is_refused_before_the_store_is_opened(bundle: Path) -> None:
    """The store is replaced with bytes DuckDB cannot open, so reaching it at
    all would raise DuckDB's error: the refusal must come from the manifest."""
    write_manifest(bundle, bundle_id="b", producer=_PRODUCER)
    path = bundle / MANIFEST_NAME
    path.write_text(json.dumps(json.loads(path.read_text()) | {"schema_version": "2.19"}))
    (bundle / "meta.duckdb").write_bytes(b"not a duckdb file")
    with pytest.raises(sl.StoreSchemaError, match=r"store schema 2\.19 \(its manifest says so\)"):
        sl.LocalRegistry.bundle(bundle)


def test_an_unpublished_bundle_is_refused_by_bundle_and_resumes_to_build(bundle: Path) -> None:
    """`bundle()` opens published publications only; one still being built
    reopens with `build(resume=)`, writable, and has no manifest yet (GH-122)."""
    with pytest.raises(ManifestError, match="not a published bundle"):
        sl.LocalRegistry.bundle(bundle)
    with sl.LocalRegistry.build(bundle.parent, resume=bundle.name) as reg:
        assert reg.manifest is None
        assert reg.publication == bundle
        assert reg.obs(_FLOW.urn).count() == 200_000


def test_the_shipped_schema_is_the_models_and_a_written_manifest_meets_it(bundle: Path) -> None:
    assert manifest_schema() == generate_schema(), "regenerate src/sdmxlib/bundle/manifest.schema.json"
    write_manifest(bundle, bundle_id="b", producer=_PRODUCER, product={"edition": 2021}, extensions={"x": {"v": 1}})
    jsonschema.validate(json.loads((bundle / MANIFEST_NAME).read_text()), json.loads(manifest_schema()))


def test_a_builders_own_manifest_is_refused_by_name(bundle: Path) -> None:
    """sdmxlib owns `bundle.manifest.json`. A builder's manifest from before
    this format, with no `format_version`, is refused with what to do, rather
    than as a bare missing-field error or by being read around."""
    (bundle / MANIFEST_NAME).write_text(json.dumps({"build_id": "x", "content_digest": "y", "files": {}}))
    with pytest.raises(ManifestError, match=r"not an sdmxlib bundle manifest .*write_manifest"):
        sl.LocalRegistry.bundle(bundle)
    (bundle / MANIFEST_NAME).unlink()
    write_manifest(bundle, bundle_id="b", producer=_PRODUCER)
    with sl.LocalRegistry.bundle(bundle) as reg:
        assert reg.manifest is not None
