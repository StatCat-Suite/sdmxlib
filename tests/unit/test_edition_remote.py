"""Remote verification by size and a digest the store keeps, and the reserved signature file (GH-125, GH-181).

The lookup here reads the local files and reports them as a blob store would,
so a clean bundle verifies clean and each test changes one thing the store
reports.
"""

import base64
import hashlib
import json
import re
from collections.abc import Callable
from pathlib import Path

import google_crc32c
import msgspec
import pytest

from sdmxlib.edition import (
    DATA_PATH,
    MANIFEST_NAME,
    SIGNATURE_NAME,
    EditionFile,
    Finding,
    Manifest,
    ManifestError,
    Producer,
    RemoteFile,
    check_edition,
    content_crc32c,
    content_md5,
    copy_edition,
    list_files,
    read_manifest,
    seal,
    verify_remote,
)
from tests.support.bundles import make_bundle, publish

pytestmark = pytest.mark.unit


def _published(tmp_path: Path) -> tuple[Path, Manifest]:
    publication = make_bundle(tmp_path / "b", 200_000)
    return publication, seal(publication, product_id="b", producer=Producer(name="t", version="0"))


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


def _data(manifest: Manifest) -> EditionFile:
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
    assert verify_remote(manifest, lambda entry: store[entry.path], tiers=("required", "lazy", "remote")) == []


def test_only_the_tiers_asked_for_are_looked_up(tmp_path: Path) -> None:
    publication, manifest = _published(tmp_path)
    store = _store(publication, manifest)
    asked: list[str] = []

    def lookup(entry: EditionFile) -> RemoteFile:
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
    manifest = seal(publication, product_id="b", producer=Producer(name="t", version="0"))
    assert SIGNATURE_NAME not in {f.path for f in manifest.files}
    assert SIGNATURE_NAME not in {f.path for f in list_files(publication)}
    (publication / SIGNATURE_NAME).write_text("signed after")
    assert check_edition(publication, at="publish").ok


def test_copy_carries_the_signature_with_the_manifest(tmp_path: Path) -> None:
    publication = publish(make_bundle(tmp_path / "src" / "b", 10))
    (publication / SIGNATURE_NAME).write_text("sig")
    destination = tmp_path / "dst" / "b" / publication.name
    copy_edition(publication, destination)
    assert (destination / SIGNATURE_NAME).read_text() == "sig"
    assert check_edition(destination, at="publish").ok
    assert read_manifest(destination) == read_manifest(publication)


def test_any_other_unlisted_file_is_still_reported(tmp_path: Path) -> None:
    publication = publish(make_bundle(tmp_path / "b", 10))
    (publication / f"{SIGNATURE_NAME}.bak").write_text("x")
    assert check_edition(publication, at="publish").findings == (
        Finding("files", f"{SIGNATURE_NAME}.bak", "in the edition but not in the manifest"),
    )


def test_the_signature_name_is_the_manifests_with_sig() -> None:
    assert SIGNATURE_NAME == "manifest.json.sig"


def _as_stored(publication: Path, entry: EditionFile) -> bytes:
    file = publication / DATA_PATH / entry.path if entry.root == "data" else publication / entry.path
    return file.read_bytes()


def _b64(digest: bytes) -> str:
    return base64.b64encode(digest).decode()


def test_the_manifest_records_each_files_three_digests(tmp_path: Path) -> None:
    publication, manifest = _published(tmp_path)
    for entry in manifest.files:
        data = _as_stored(publication, entry)
        assert entry.digests == {
            "sha256": hashlib.sha256(data).hexdigest(),
            "md5": hashlib.md5(data, usedforsecurity=False).hexdigest(),
            "crc32c": f"{google_crc32c.value(data):08x}",
        }, entry.path


_ZEROS = {"sha256": "0" * 64, "md5": "0" * 32}


def test_content_crc32c_is_the_base64_gcs_and_s3_report() -> None:
    """``Nks/tw==`` is what the GCS emulator reported for the bytes ``abc``: CRC32C 0x364B3FB7."""
    entry = EditionFile(path="x", size=3, digests={**_ZEROS, "crc32c": "364b3fb7"}, root="data", tier="remote")
    assert content_crc32c(entry) == "Nks/tw=="


@pytest.mark.parametrize(
    ("digests", "detail"),
    [
        (_ZEROS, "records no crc32c; every file records sha256, md5, crc32c"),
        ({**_ZEROS, "crc32c": "364B3FB7"}, "crc32c '364B3FB7' is not lowercase hex"),
        ({**_ZEROS, "crc32c": ""}, "crc32c '' is not lowercase hex"),
    ],
)
def test_an_entry_without_every_digest_in_lowercase_hex_is_refused(digests: dict[str, str], detail: str) -> None:
    with pytest.raises(ValueError, match=re.escape(detail)):
        EditionFile(path="x", size=3, digests=digests, root="data", tier="remote")


def test_a_manifest_missing_a_digest_is_refused_on_read(tmp_path: Path) -> None:
    publication, _ = _published(tmp_path)
    document = json.loads((publication / MANIFEST_NAME).read_text())
    del document["files"][0]["digests"]["crc32c"]
    (publication / MANIFEST_NAME).write_text(json.dumps(document))
    with pytest.raises(ManifestError, match="records no crc32c"):
        read_manifest(publication)


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
    assert verify_remote(manifest, lambda e: store[e.path], tiers=("required", "lazy", "remote")) == []


def test_an_algorithm_beyond_the_three_is_compared_when_the_store_keeps_it(tmp_path: Path) -> None:
    """The map takes any algorithm: one a store adds (S3's CRC64NVME, say) needs no format change."""
    _, manifest = _published(tmp_path)
    entry = _data(manifest)
    extended = msgspec.structs.replace(entry, digests={**entry.digests, "crc64nvme": "00112233445566ff"})
    with_it = msgspec.structs.replace(manifest, files=(extended,))
    same = RemoteFile(entry.size, {"crc64nvme": _b64(bytes.fromhex("00112233445566ff"))})
    other = RemoteFile(entry.size, {"crc64nvme": _b64(bytes(8))})
    assert verify_remote(with_it, lambda e: same) == []
    (finding,) = verify_remote(with_it, lambda e: other)
    assert finding.detail.startswith("crc64nvme AAAAAAAAAAA= in the store")


def test_a_crc32c_the_file_does_not_have_is_a_publish_finding(tmp_path: Path) -> None:
    """The check compares every recorded digest: a wrong CRC32C alone is caught, sha256 and md5 still matching."""
    publication, manifest = _published(tmp_path)
    entry = _data(manifest)
    wrong = msgspec.structs.replace(entry, digests={**entry.digests, "crc32c": "00000000"})
    files = tuple(wrong if f is entry else f for f in manifest.files)
    (publication / MANIFEST_NAME).write_bytes(msgspec.json.encode(msgspec.structs.replace(manifest, files=files)))
    findings = check_edition(publication, at="publish").findings
    assert Finding("files", f"{DATA_PATH}{entry.path}", "contents differ from the manifest's crc32c") in findings
