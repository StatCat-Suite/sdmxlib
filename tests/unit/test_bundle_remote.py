"""Remote verification by size and Content-MD5, and the reserved signature file (GH-125).

The lookup here reads the local files and reports them as a blob store would,
so a clean bundle verifies clean and each test changes one thing the store
reports.
"""

import base64
import hashlib
from collections.abc import Callable
from pathlib import Path

import pytest

from sdmxlib.bundle import (
    DATA_PATH,
    SIGNATURE_NAME,
    BundleFile,
    Finding,
    Manifest,
    Producer,
    RemoteFile,
    check_bundle,
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
            len(data), base64.b64encode(hashlib.md5(data, usedforsecurity=False).digest()).decode()
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
        (lambda f: RemoteFile(f.size + 1, f.content_md5), "bytes in the store, the manifest says"),
        (lambda f: RemoteFile(f.size, None), "the store records no Content-MD5; the publish job sets one"),
        (
            lambda f: RemoteFile(f.size, base64.b64encode(b"\x00" * 16).decode()),
            "Content-MD5 AAAAAAAAAAAAAAAAAAAAAA== in the store",
        ),
        (lambda f: RemoteFile(f.size, "not base64!"), "is not base64"),
    ],
    ids=["missing", "size", "no-content-md5", "other-md5", "malformed"],
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
    assert check_bundle(publication, references=False).ok


def test_copy_carries_the_signature_with_the_manifest(tmp_path: Path) -> None:
    publication = publish(make_bundle(tmp_path / "src" / "b", 10))
    (publication / SIGNATURE_NAME).write_text("sig")
    destination = tmp_path / "dst" / "b" / publication.name
    copy_bundle(publication, destination)
    assert (destination / SIGNATURE_NAME).read_text() == "sig"
    assert check_bundle(destination, references=False).ok
    assert read_manifest(destination) == read_manifest(publication)


def test_any_other_unlisted_file_is_still_reported(tmp_path: Path) -> None:
    publication = publish(make_bundle(tmp_path / "b", 10))
    (publication / f"{SIGNATURE_NAME}.bak").write_text("x")
    assert check_bundle(publication, references=False).findings == (
        Finding("files", f"{SIGNATURE_NAME}.bak", "in the bundle but not in the manifest"),
    )


def test_the_signature_name_is_the_manifests_with_sig() -> None:
    assert SIGNATURE_NAME == "bundle.manifest.json.sig"
