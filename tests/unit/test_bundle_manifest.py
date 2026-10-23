"""A bundle's manifest: a token per publication, a checksum per file (GH-66).

Two builds from the same inputs differ in every file's bytes, so identity is
the writer's `token`, minted at each publication, and the sha256 per file is
for integrity. These tests pin what a cache relies on: the token changes when
the bundle is published and never when it is read.
"""

import json
import uuid
from pathlib import Path

import jsonschema
import pytest

import sdmxlib as sl
from sdmxlib.bundle import (
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


def test_each_publication_mints_a_new_token_over_the_same_checksums(bundle: Path) -> None:
    first = write_manifest(bundle, bundle_id="b", producer=_PRODUCER)
    second = write_manifest(bundle, bundle_id="b", producer=_PRODUCER)
    assert first.token != second.token
    assert uuid.UUID(first.token).version == 7
    assert first.files == second.files, "nothing changed but the publication"


def test_reading_a_bundle_changes_neither_its_token_nor_its_files(bundle: Path) -> None:
    """What a cache keyed on the token relies on."""
    published = write_manifest(bundle, bundle_id="b", producer=_PRODUCER)
    with sl.LocalRegistry.bundle(bundle, require_manifest=True) as reg:
        assert reg.get_all(sl.Dataflow)
        assert reg.obs(_FLOW.urn).frame().height == 200_000
        manifest = reg.manifest
        assert manifest is not None
        assert manifest.token == published.token
    assert inventory(bundle) == published.files
    assert read_manifest(bundle).token == published.token


def test_the_inventory_is_every_file_but_the_manifest(bundle: Path) -> None:
    manifest = write_manifest(bundle, bundle_id="b", producer=_PRODUCER)
    on_disk = sorted(p.relative_to(bundle).as_posix() for p in bundle.rglob("*") if p.is_file())
    assert [f.path for f in manifest.files] == [p for p in on_disk if p != MANIFEST_NAME]
    assert any(f.path.startswith("parquet/") for f in manifest.files), "a table this size is not inlined"


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
    path.write_text(json.dumps(document | {"format_version": "1.7", "added_in_1_7": True}))
    assert read_manifest(bundle).bundle_id == "b"
    path.write_text(json.dumps(document | {"format_version": "2.0"}))
    with pytest.raises(ManifestError, match=r"manifest format 2\.0; this sdmxlib reads 1\.x"):
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


def test_an_unpublished_bundle_opens_unless_a_manifest_is_required(bundle: Path) -> None:
    with sl.LocalRegistry.bundle(bundle) as reg:
        assert reg.manifest is None
    with pytest.raises(ManifestError, match="not a published bundle"):
        sl.LocalRegistry.bundle(bundle, require_manifest=True)


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
    write_manifest(bundle, bundle_id="b", producer=_PRODUCER)
    with sl.LocalRegistry.bundle(bundle, require_manifest=True) as reg:
        assert reg.manifest is not None
