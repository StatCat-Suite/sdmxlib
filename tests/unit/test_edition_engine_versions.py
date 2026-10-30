"""Manifest 2.0's engine versions, artefact inventory, tiers and md5 (GH-124).

A reader refuses, from the manifest alone, a publication whose DuckDB storage
version is newer than its engine reads or whose DuckLake format is not its
own, so a mismatched bundle is refused with both versions named and no file
opened. The tests that refuse replace `metadata.duckdb` with bytes DuckDB cannot
open, so reaching the file at all would raise DuckDB's error instead.
"""

import hashlib
import json
from pathlib import Path

import duckdb
import pytest

import sdmxlib as sl
from sdmxlib.edition import (
    DUCKLAKE_SPEC_VERSION,
    MANIFEST_NAME,
    ManifestError,
    Producer,
    StoreFormatError,
    check_edition,
    copy_edition,
    read_manifest,
    seal,
)
from sdmxlib.storage._manifest import engine_storage_version, storage_version_of
from tests.support.bundles import CONCEPTS, DSD, FLOW, make_bundle, publish

pytestmark = pytest.mark.unit

_PRODUCER = Producer(name="tests", version="0")


def _edit(publication: Path, **fields: object) -> None:
    path = publication / MANIFEST_NAME
    path.write_text(json.dumps(json.loads(path.read_text()) | fields))


def _unopenable(publication: Path) -> None:
    (publication / "metadata.duckdb").write_bytes(b"not a duckdb file")


# ── engine versions ───────────────────────────────────────────────────────


def test_the_manifest_records_the_engine_that_wrote_the_files(tmp_path: Path) -> None:
    manifest = read_manifest(publish(make_bundle(tmp_path / "b", 10)))
    assert manifest.duckdb_version == duckdb.__version__
    assert manifest.duckdb_storage_version == engine_storage_version()
    assert manifest.ducklake_spec_version == DUCKLAKE_SPEC_VERSION
    (installed,) = duckdb.connect().execute(
        "SELECT extension_version FROM duckdb_extensions() WHERE extension_name = 'ducklake'"
    ).fetchone() or (None,)
    assert manifest.ducklake_version == installed


def test_a_fresh_catalog_records_the_ducklake_spec_this_build_reads(tmp_path: Path) -> None:
    """Fails when a DuckLake upgrade changes the format, so the bump is deliberate."""
    conn = duckdb.connect()
    conn.execute(f"ATTACH 'ducklake:{tmp_path / 'tables.ducklake'}' AS lake (DATA_PATH '{tmp_path}/')")
    (spec,) = conn.execute(
        "SELECT value FROM __ducklake_metadata_lake.ducklake_metadata WHERE key = 'version'"
    ).fetchone() or (None,)
    assert spec == DUCKLAKE_SPEC_VERSION


def test_a_publication_without_a_lake_records_no_ducklake_versions(tmp_path: Path) -> None:
    with sl.LocalRegistry.build(tmp_path / "b") as builder:
        builder.add(DSD)
        publication = builder.edition_dir
    manifest = seal(publication, product_id="b", producer=_PRODUCER)
    assert (manifest.ducklake_version, manifest.ducklake_spec_version) == (None, None)
    assert not (publication / "tables.ducklake").exists()
    with sl.LocalRegistry.edition(publication) as reg:
        assert reg.get_all(sl.DataStructure)


@pytest.mark.parametrize(
    ("storage", "number"), [("v1.0.0", 64), ("v1.2.0", 65), ("v1.4.0", 67), ("v1.5.0", 68), ("v2.0.0", 69)]
)
def test_the_storage_version_is_read_from_the_file_header(tmp_path: Path, storage: str, number: int) -> None:
    path = tmp_path / "x.duckdb"
    _write_at(path, storage)
    assert storage_version_of(path) == number


def test_a_file_in_duckdb_2_0s_own_format_reads_as_69_and_not_999(tmp_path: Path) -> None:
    """DuckDB 2.0 writes 999 in the main header and the version in the database header (GH-174)."""
    path = tmp_path / "x.duckdb"
    duckdb.connect(str(path)).close()
    assert _main_header(path) == 999, "discrimination: the main header must hold the 999 this test is about"
    assert storage_version_of(path) == 69 == engine_storage_version()


@pytest.mark.parametrize(("bumped_to", "number"), [("v1.5.0", 68), ("v2.0.0", 69)])
def test_a_file_bumped_in_place_reads_as_the_version_it_was_bumped_to(
    tmp_path: Path, bumped_to: str, number: int
) -> None:
    """A bump leaves the main header at the version the file was created with, at most 65 (GH-174)."""
    path = tmp_path / "x.duckdb"
    _write_at(path, "v1.2.0")
    _write_at(path, bumped_to)
    assert _main_header(path) == 65, "discrimination: the main header must understate the version"
    assert storage_version_of(path) == number


def test_a_newer_2x_format_reads_as_its_own_number(tmp_path: Path) -> None:
    """A future DuckDB records its version past 69 in the database header; 999 stays in the main one."""
    path = tmp_path / "x.duckdb"
    duckdb.connect(str(path)).close()
    _set_storage_field(path, 70)
    assert storage_version_of(path) == 70


def test_a_manifest_recording_999_opens_from_its_files_headers(tmp_path: Path) -> None:
    """sdmxlib 0.73.0 recorded 999 for a DuckDB 2.0 file; the reader reads the headers instead (GH-174)."""
    publication = publish(make_bundle(tmp_path / "b", 10))
    _edit(publication, duckdb_storage_version=999)
    with sl.LocalRegistry.edition(publication) as reg:
        assert reg.obs(FLOW.urn).count() == 10


def test_a_manifest_recording_999_over_a_newer_file_is_refused_unopened(tmp_path: Path) -> None:
    publication = publish(make_bundle(tmp_path / "b", 10))
    _edit(publication, duckdb_storage_version=999, duckdb_version="9.9.9")
    # The edited header no longer matches its checksum, so DuckDB could not open the file either.
    _set_storage_field(publication / "metadata.duckdb", 70)
    with pytest.raises(StoreFormatError, match=r"storage version 70 \(written by DuckDB 9\.9\.9\).*reads up to 69"):
        sl.LocalRegistry.edition(publication)


def _write_at(path: Path, storage: str) -> None:
    """A table written into `path` attached at `storage`, creating or bumping the file."""
    conn = duckdb.connect()
    conn.execute(f"ATTACH '{path}' AS x (STORAGE_VERSION '{storage}')")
    conn.execute("CREATE OR REPLACE TABLE x.t AS SELECT 1 AS v")
    conn.execute("CHECKPOINT x")
    conn.close()


def _main_header(path: Path) -> int:
    return int.from_bytes(path.read_bytes()[12:20], "little")


def _set_storage_field(path: Path, value: int) -> None:
    """Overwrite the storage field of `path`'s active database header, as a newer DuckDB would write it."""
    data = bytearray(path.read_bytes())
    active = max((4096, 8192), key=lambda start: int.from_bytes(data[start + 8 : start + 16], "little"))
    data[active + 56 : active + 64] = value.to_bytes(8, "little")
    path.write_bytes(bytes(data))


def test_a_file_that_is_not_duckdb_has_no_storage_version(tmp_path: Path) -> None:
    (tmp_path / "x.duckdb").write_bytes(b"\x00" * 64)
    with pytest.raises(StoreFormatError, match="not a DuckDB database file"):
        storage_version_of(tmp_path / "x.duckdb")


def test_a_newer_storage_version_is_refused_naming_both_and_unopened(tmp_path: Path) -> None:
    publication = publish(make_bundle(tmp_path / "b", 10))
    newer = engine_storage_version() + 1
    _edit(publication, duckdb_storage_version=newer, duckdb_version="9.9.9")
    _unopenable(publication)
    with pytest.raises(
        StoreFormatError, match=rf"storage version {newer} \(written by DuckDB 9\.9\.9\).*reads up to {newer - 1}"
    ):
        sl.LocalRegistry.edition(publication)


def test_an_older_storage_version_opens(tmp_path: Path) -> None:
    """Both DuckDB files rewritten at DuckDB 1.4's storage version, and
    republished: the manifest records it, and the bundle opens and reads."""
    started = make_bundle(tmp_path / "b", 200_000)
    for name in ("metadata.duckdb", "tables.ducklake"):
        _downgrade(started / name, "v1.4.0")
    manifest = seal(started, product_id="b", producer=_PRODUCER)
    assert manifest.duckdb_storage_version == 67 < engine_storage_version()
    with sl.LocalRegistry.edition(started) as reg:
        assert reg.obs(FLOW.urn).count() == 200_000


def test_the_storage_version_is_the_newest_among_the_files(tmp_path: Path) -> None:
    """The store at DuckDB 1.4's version and the lake catalog at this engine's:
    a reader must be able to open both, so the manifest records the newer."""
    started = make_bundle(tmp_path / "b", 10)
    _downgrade(started / "metadata.duckdb", "v1.4.0")
    assert storage_version_of(started / "metadata.duckdb") == 67
    manifest = seal(started, product_id="b", producer=_PRODUCER)
    assert (
        manifest.duckdb_storage_version == storage_version_of(started / "tables.ducklake") == engine_storage_version()
    )


def _downgrade(path: Path, storage: str) -> None:
    """`path` rewritten at an older storage version, through DuckDB's database copy."""
    older = path.with_name(f"older-{path.name}")
    conn = duckdb.connect()
    conn.execute(f"ATTACH '{path}' AS src")
    conn.execute(f"ATTACH '{older}' AS dst (STORAGE_VERSION '{storage}')")
    conn.execute("COPY FROM DATABASE src TO dst")
    conn.close()
    older.replace(path)


@pytest.mark.parametrize("spec", ["1.2", "2.0", "x"])
def test_a_newer_ducklake_format_is_refused_naming_both_and_unopened(tmp_path: Path, spec: str) -> None:
    publication = publish(make_bundle(tmp_path / "b", 10))
    _edit(publication, ducklake_spec_version=spec, ducklake_version="new")
    _unopenable(publication)
    with pytest.raises(
        StoreFormatError,
        match=rf"DuckLake catalog in format {spec} \(written by DuckLake new\).*formats up to {DUCKLAKE_SPEC_VERSION}",
    ):
        sl.LocalRegistry.edition(publication)


@pytest.mark.parametrize("spec", ["1.0", "0.3"])
def test_an_older_ducklake_format_opens(tmp_path: Path, spec: str) -> None:
    """DuckLake reads an older catalog read-only without migrating it; the bundle
    tier opens the census bundle's real 1.0 catalog this way."""
    publication = publish(make_bundle(tmp_path / "b", 10))
    _edit(publication, ducklake_spec_version=spec)
    with sl.LocalRegistry.edition(publication) as reg:
        assert reg.obs(FLOW.urn).count() == 10


def test_check_bundle_reports_an_unreadable_engine_and_stops(tmp_path: Path) -> None:
    publication = publish(make_bundle(tmp_path / "b", 10))
    _edit(publication, ducklake_spec_version="9.9")
    report = check_edition(publication, at="publish")
    assert report.checked == ("manifest", "files")
    assert [(f.check, f.subject) for f in report.findings] == [("manifest", "metadata.duckdb")]


def test_extending_a_publication_this_engine_cannot_read_is_refused(tmp_path: Path) -> None:
    publication = publish(make_bundle(tmp_path / "b", 10))
    _edit(publication, duckdb_storage_version=engine_storage_version() + 1)
    with pytest.raises(StoreFormatError):
        sl.LocalRegistry.build(publication.parent, previous=publication.name)
    assert sorted(p.name for p in publication.parent.iterdir()) == sorted(["data", publication.name])


# ── artefacts and bindings ────────────────────────────────────────────────


def _codelist(version: str, *, partial: bool = False) -> sl.Codelist:
    return sl.Codelist(
        id="CL",
        maintainer="AG",
        version=version,
        name={"en": "cl"},
        codes=[sl.Code(id="A", name={"en": "a"})],
        is_partial=partial,
    )


def test_artefacts_lists_every_artefact_but_the_bindings_with_its_stability(tmp_path: Path) -> None:
    extra = (_codelist("2.0"), _codelist("2.0.0"), _codelist("3.0.0", partial=True), _codelist("1.0.0-draft"))
    manifest = read_manifest(publish(make_bundle(tmp_path / "b", 10, extra=extra)))
    assert [(a.urn, a.stability, a.partial) for a in manifest.artefacts] == sorted(
        [
            (str(extra[0].urn), "legacy", False),
            (str(extra[1].urn), "semantic", False),
            (str(extra[2].urn), "semantic", True),
            (str(extra[3].urn), "legacy", False),
            (str(CONCEPTS.urn), "legacy", False),
            (str(DSD.urn), "legacy", False),
            (str(FLOW.urn), "legacy", False),
        ]
    )


def test_a_binding_records_the_structure_it_was_written_against(tmp_path: Path) -> None:
    (binding,) = read_manifest(publish(make_bundle(tmp_path / "b", 10))).bindings
    assert binding.structural_version == str(DSD.urn)


def test_a_binding_tag_that_is_not_agency_id_version_is_refused(tmp_path: Path) -> None:
    started = make_bundle(tmp_path / "b", 10)
    with sl.LocalRegistry.build(started.parent, resume=started.name) as builder:
        builder.connection.execute("UPDATE sdmx.dataflow_binding SET structural_version = 'whatever'")
    with pytest.raises(ManifestError, match=r"structural_version 'whatever', which is not AGENCY:ID\(VERSION\)"):
        seal(started, product_id="b", producer=_PRODUCER)


# ── tiers and md5 ─────────────────────────────────────────────────────────


def _with_extras(root: Path) -> Path:
    """An unpublished publication holding a search index and a sidecar database of its own."""
    started = make_bundle(root, 200_000)
    (started / "search").mkdir()
    (started / "search" / "index.json").write_text("{}")
    (started / "search" / "vectors.lance").write_bytes(b"v")
    (started / "extra.db").write_bytes(b"x")
    return started


def test_tiers_default_by_role(tmp_path: Path) -> None:
    started = _with_extras(tmp_path / "b")
    manifest = seal(started, product_id="b", producer=_PRODUCER)
    tiers = {(f.root, f.path): f.tier for f in manifest.files}
    assert {path: tier for (root, path), tier in tiers.items() if root == "edition"} == {
        "extra.db": "lazy",
        "tables.ducklake": "required",
        "metadata.duckdb": "required",
        "search/index.json": "lazy",
        "search/vectors.lance": "lazy",
    }
    assert {tier for (root, _), tier in tiers.items() if root == "data"} == {"remote"}


def test_a_tier_override_takes_the_longest_matching_prefix(tmp_path: Path) -> None:
    started = _with_extras(tmp_path / "b")
    manifest = seal(
        started,
        product_id="b",
        producer=_PRODUCER,
        tiers={"search/": "remote", "search/index": "required", "extra.db": "required"},
    )
    tiers = {f.path: f.tier for f in manifest.files if f.root == "edition"}
    assert (tiers["search/index.json"], tiers["search/vectors.lance"], tiers["extra.db"]) == (
        "required",
        "remote",
        "required",
    )


def test_every_file_carries_its_md5_and_a_carried_file_keeps_it(tmp_path: Path) -> None:
    first = publish(make_bundle(tmp_path / "b", 200_000))
    one = read_manifest(first)
    for entry in one.files:
        base = first if entry.root == "edition" else first.parent / "data"
        assert entry.digests["md5"] == hashlib.md5((base / entry.path).read_bytes(), usedforsecurity=False).hexdigest()
    with sl.LocalRegistry.build(first.parent, previous=first.name) as builder:
        second = builder.edition_dir
    two = seal(second, product_id="b", producer=_PRODUCER)
    carried = {f.path: f.digests for f in two.files if f.carried_from == one.edition_id}
    assert carried == {f.path: f.digests for f in one.files if f.root == "data"}
    assert carried, "the first publication's data file is carried"


def test_a_changed_md5_alone_is_a_files_finding(tmp_path: Path) -> None:
    publication = publish(make_bundle(tmp_path / "b", 10))
    document = json.loads((publication / MANIFEST_NAME).read_text())
    meta = next(f for f in document["files"] if f["path"] == "metadata.duckdb")
    meta["digests"]["md5"] = "0" * 32
    (publication / MANIFEST_NAME).write_text(json.dumps(document))
    assert [(f.check, f.subject) for f in check_edition(publication, at="publish").findings] == [
        ("files", "metadata.duckdb")
    ]


# ── copying by tier ───────────────────────────────────────────────────────


def test_copy_by_tier_leaves_out_the_other_tiers(tmp_path: Path) -> None:
    publication = publish(_with_extras(tmp_path / "src" / "b"))
    destination = tmp_path / "dst" / "b" / publication.name
    manifest = copy_edition(publication, destination, tiers=("required",))
    assert sorted(p.relative_to(destination).as_posix() for p in destination.rglob("*") if p.is_file()) == [
        MANIFEST_NAME,
        "metadata.duckdb",
        "tables.ducklake",
    ]
    assert not (destination.parent / "data").exists()
    missing = {f.subject for f in check_edition(destination, at="publish").findings if f.check == "files"}
    assert missing == {
        f.path if f.root == "edition" else f"{manifest.data_path}{f.path}"
        for f in manifest.files
        if f.tier != "required"
    }
