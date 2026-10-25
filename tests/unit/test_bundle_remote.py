"""Remote verification by size and a digest the store keeps, and the reserved signature file (GH-125, GH-181).

The lookup here reads the local files and reports them as a blob store would,
so a clean bundle verifies clean and each test changes one thing the store
reports.
"""

import base64
import hashlib
from collections.abc import Callable
from pathlib import Path

import google_crc32c
import msgspec
import pytest

from sdmxlib.bundle import (
    DATA_PATH,
    MANIFEST_NAME,
    SIGNATURE_NAME,
    BundleFile,
    Finding,
    Manifest,
    Producer,
    RemoteFile,
    check_bundle,
    content_crc32c,
    content_md5,
    copy_bundle,
    inventory,
    read_manifest,
    verify_remote,
    write_manifest,
)
from tests.support.bundles import make_bundle, publish

pytestmark = pytest.mark.unit


def _published(tmp_path: Path) -> tuple[Path, Manifest]:
    publication = make_bundle(tmp_path / "b", 200_000)
    return publication, write_manifest(publication, bundle_id="b", producer=Producer(name="t", version="0"))


def _store(publication: Path, manifest: Manifest) -> dict[str, RemoteFile]:
    """Every listed file as a blob store reports it after an upload that set Content-MD5."""
    reported: dict[str, RemoteFile] = {}
    for entry in manifest.files:
        file = publication / DATA_PATH / entry.path if entry.root == "data" else publication / entry.path
        data = file.read_bytes()
        reported[entry.path] = RemoteFile(
            len(data), {"md5": base64.b64encode(hashlib.md5(data, usedforsecurity=False).digest()).decode()}
        )
    return reported


def _data(manifest: Manifest) -> BundleFile:
    (entry,) = (f for f in manifest.files if f.root == "data")
    return entry


def test_content_md5_is_the_base64_of_the_files_md5(tmp_path: Path) -> None:
    publication, manifest = _published(tmp_path)
    entry = _data(manifest)
    data = (publication / DATA_PATH / entry.path).read_bytes()
    assert content_md5(entry) == base64.b64encode(hashlib.md5(data, usedforsecurity=False).digest()).decode()


def test_a_store_holding_what_the_manifest_lists_verifies_clean(tmp_path: Path) -> None:
    publication, manifest = _published(tmp_path)
    store = _store(publication, manifest)
    assert verify_remote(manifest, lambda entry: store[entry.path], tiers=(1, 2, 3)) == []


def test_only_the_tiers_asked_for_are_looked_up(tmp_path: Path) -> None:
    publication, manifest = _published(tmp_path)
    store = _store(publication, manifest)
    asked: list[str] = []

    def lookup(entry: BundleFile) -> RemoteFile:
        asked.append(entry.path)
        return store[entry.path]

    assert verify_remote(manifest, lookup) == []
    assert asked == [_data(manifest).path], "tier 3 by default: the data file alone"


@pytest.mark.parametrize(
    ("change", "detail"),
    [
        (lambda f: None, "listed in the manifest but missing from the store"),
        (lambda f: RemoteFile(f.size + 1, f.digests), "bytes in the store, the manifest says"),
        (
            lambda f: RemoteFile(f.size),
            "the store keeps no digest and the manifest records crc32c, md5, sha256: none to compare",
        ),
        (
            lambda f: RemoteFile(f.size, {"md5": base64.b64encode(b"\x00" * 16).decode()}),
            "md5 AAAAAAAAAAAAAAAAAAAAAA== in the store, the manifest's is",
        ),
        (lambda f: RemoteFile(f.size, {"md5": "not base64!"}), "is not base64"),
        (
            lambda f: RemoteFile(f.size, {"crc64nvme": "AAAAAAAAAAA="}),
            "the store keeps crc64nvme and the manifest records crc32c, md5, sha256: none to compare",
        ),
        (
            lambda f: RemoteFile(f.size, {"sha256": "AAAA-3"}),
            "the store keeps sha256 (a composite of the parts)",
        ),
        (lambda f: RemoteFile(f.size, {"crc32c": "AAAAAA=="}), "crc32c AAAAAA== in the store, the manifest's is"),
    ],
    ids=["missing", "size", "no-digest", "other-md5", "malformed", "unknown-algorithm", "composite", "other-crc32c"],
)
def test_each_way_a_stored_file_can_differ_is_one_finding(
    tmp_path: Path, change: Callable[[RemoteFile], RemoteFile | None], detail: str
) -> None:
    publication, manifest = _published(tmp_path)
    store = _store(publication, manifest)
    entry = _data(manifest)
    changed = change(store[entry.path])
    (finding,) = verify_remote(manifest, lambda e: changed if e.path == entry.path else store[e.path])
    assert (finding.check, finding.subject) == ("remote", f"{DATA_PATH}{entry.path}")
    assert detail in finding.detail


def test_a_signature_beside_the_manifest_is_never_listed_or_reported(tmp_path: Path) -> None:
    publication = make_bundle(tmp_path / "b", 10)
    (publication / SIGNATURE_NAME).write_text("signed before publishing, by a producer that does")
    manifest = write_manifest(publication, bundle_id="b", producer=Producer(name="t", version="0"))
    assert SIGNATURE_NAME not in {f.path for f in manifest.files}
    assert SIGNATURE_NAME not in {f.path for f in inventory(publication)}
    (publication / SIGNATURE_NAME).write_text("signed after")
    assert check_bundle(publication, at="publish").ok


def test_copy_carries_the_signature_with_the_manifest(tmp_path: Path) -> None:
    publication = publish(make_bundle(tmp_path / "src" / "b", 10))
    (publication / SIGNATURE_NAME).write_text("sig")
    destination = tmp_path / "dst" / "b" / publication.name
    copy_bundle(publication, destination)
    assert (destination / SIGNATURE_NAME).read_text() == "sig"
    assert check_bundle(destination, at="publish").ok
    assert read_manifest(destination) == read_manifest(publication)


def test_any_other_unlisted_file_is_still_reported(tmp_path: Path) -> None:
    publication = publish(make_bundle(tmp_path / "b", 10))
    (publication / f"{SIGNATURE_NAME}.bak").write_text("x")
    assert check_bundle(publication, at="publish").findings == (
        Finding("files", f"{SIGNATURE_NAME}.bak", "in the bundle but not in the manifest"),
    )


def test_the_signature_name_is_the_manifests_with_sig() -> None:
    assert SIGNATURE_NAME == "bundle.manifest.json.sig"


def _as_stored(publication: Path, entry: BundleFile) -> bytes:
    file = publication / DATA_PATH / entry.path if entry.root == "data" else publication / entry.path
    return file.read_bytes()


def _b64(digest: bytes) -> str:
    return base64.b64encode(digest).decode()


def test_the_manifest_records_each_files_crc32c(tmp_path: Path) -> None:
    publication, manifest = _published(tmp_path)
    for entry in manifest.files:
        assert entry.crc32c == f"{google_crc32c.value(_as_stored(publication, entry)):08x}", entry.path


def test_content_crc32c_is_the_base64_gcs_and_s3_report() -> None:
    """``Nks/tw==`` is what the GCS emulator reported for the bytes ``abc``: CRC32C 0x364B3FB7."""
    entry = BundleFile(path="x", size=3, sha256="", md5="", crc32c="364b3fb7", root="data", tier=3)
    assert content_crc32c(entry) == "Nks/tw=="


def test_content_crc32c_refuses_an_entry_from_before_format_2_1() -> None:
    entry = BundleFile(path="x", size=3, sha256="", md5="", root="data", tier=3)
    with pytest.raises(ValueError, match=r"predates format 2\.1"):
        content_crc32c(entry)


@pytest.mark.parametrize(
    "keeps",
    [
        pytest.param(lambda data: {"md5": _b64(hashlib.md5(data, usedforsecurity=False).digest())}, id="azure"),
        pytest.param(
            lambda data: {
                "crc32c": _b64(google_crc32c.Checksum(data).digest()),
                "md5": _b64(hashlib.md5(data, usedforsecurity=False).digest()),
            },
            id="gcs",
        ),
        pytest.param(
            lambda data: {"crc32c": _b64(google_crc32c.Checksum(data).digest()), "sha256": "QUJD-12"},
            id="s3-multipart",
        ),
        pytest.param(lambda data: {"SHA256": _b64(hashlib.sha256(data).digest())}, id="s3-single-part"),
    ],
)
def test_each_stores_digests_verify_clean(tmp_path: Path, keeps: Callable[[bytes], dict[str, str]]) -> None:
    """What each store keeps after an upload: a composite is set aside when a whole-file digest is there too."""
    publication, manifest = _published(tmp_path)
    store = {e.path: RemoteFile(e.size, keeps(_as_stored(publication, e))) for e in manifest.files}
    assert verify_remote(manifest, lambda e: store[e.path], tiers=(1, 2, 3)) == []


def test_an_entry_without_crc32c_cannot_be_verified_by_crc32c_alone(tmp_path: Path) -> None:
    """A format-2.0 manifest's entries record no CRC32C, so a GCS composite object has nothing to match."""
    publication, manifest = _published(tmp_path)
    entry = msgspec.structs.replace(_data(manifest), crc32c=None)
    older = msgspec.structs.replace(manifest, files=(entry,))
    crc = _b64(google_crc32c.Checksum(_as_stored(publication, entry)).digest())
    (finding,) = verify_remote(older, lambda e: RemoteFile(e.size, {"crc32c": crc}))
    assert "the store keeps crc32c and the manifest records md5, sha256: none to compare" in finding.detail


def test_a_crc32c_the_file_does_not_have_is_a_publish_finding(tmp_path: Path) -> None:
    """The check compares every recorded digest: a wrong CRC32C alone is caught, sha256 and md5 still matching."""
    publication, manifest = _published(tmp_path)
    entry = _data(manifest)
    files = tuple(msgspec.structs.replace(f, crc32c="00000000") if f is entry else f for f in manifest.files)
    (publication / MANIFEST_NAME).write_bytes(msgspec.json.encode(msgspec.structs.replace(manifest, files=files)))
    findings = check_bundle(publication, at="publish").findings
    assert (
        Finding("files", f"{DATA_PATH}{entry.path}", "contents differ from the manifest's sha256, md5 or crc32c")
        in findings
    )
