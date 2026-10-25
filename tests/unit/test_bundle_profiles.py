"""`check_bundle`'s ``receive`` and ``open`` profiles, and `check_on_open`'s stamp (GH-130).

``publish`` hashes everything (`test_bundle_check.py`). ``receive`` hashes the
publication's files and checks ``data/`` by size, or in a blob store by
``Content-MD5``. ``open`` checks the publication's files by size and runs no
check that reads the store. `check_on_open` runs ``receive`` once per token
and manifest, and ``open`` after. Hashing is counted through
`sdmxlib.bundle._check.digests_of`, so "hashes nothing" is asserted.
"""

import base64
import hashlib
import json
from pathlib import Path

import pytest

from sdmxlib.bundle import (
    DATA_PATH,
    MANIFEST_NAME,
    BundleFile,
    Finding,
    Manifest,
    Producer,
    Profile,
    RemoteFile,
    _check,
    check_bundle,
    check_on_open,
    read_manifest,
    write_manifest,
)
from sdmxlib.bundle._write import Digests
from sdmxlib.local.registry import BundleRegistry
from tests.support.bundles import FLOW, make_bundle

pytestmark = pytest.mark.unit

_PRODUCER = Producer(name="t", version="0")
_RECEIVE = ("manifest", "files", "lake", "bindings", "references")


def _published(root: Path, rows: int = 200_000) -> Path:
    publication = make_bundle(root, rows)
    write_manifest(publication, bundle_id="b", producer=_PRODUCER)
    return publication


def _parquet(root: Path) -> Path:
    (file,) = (root.parent / "data").rglob("*.parquet")
    return file


def _flip_a_byte(file: Path) -> None:
    data = bytearray(file.read_bytes())
    data[len(data) // 2] ^= 0xFF
    file.write_bytes(bytes(data))


def _edit_manifest(root: Path, **changes: object) -> None:
    path = root / MANIFEST_NAME
    path.write_text(json.dumps(json.loads(path.read_text()) | changes))


@pytest.fixture
def hashed(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every file `check_bundle` hashes, by name."""
    seen: list[str] = []
    original = _check.digests_of

    def counting(path: Path) -> Digests:
        seen.append(path.name)
        return original(path)

    monkeypatch.setattr(_check, "digests_of", counting)
    return seen


# ── receive ─────────────────────────────────────────────────────────────


def test_receive_hashes_the_publication_and_checks_data_by_size(tmp_path: Path, hashed: list[str]) -> None:
    root = _published(tmp_path / "b")
    _flip_a_byte(_parquet(root))
    report = check_bundle(root, at="receive")
    assert report.at == "receive"
    assert report.checked == _RECEIVE
    assert report.ok, "a same-size data file passes: receive does not hash data"
    assert sorted(hashed) == ["lake.ducklake", "meta.duckdb"]
    assert not check_bundle(root, at="publish").ok, "publish hashes it"


def test_receive_finds_a_data_file_of_another_size(tmp_path: Path) -> None:
    root = _published(tmp_path / "b")
    file = _parquet(root)
    file.write_bytes(file.read_bytes() + b"x")
    subject = f"{DATA_PATH}{file.relative_to(root.parent / 'data').as_posix()}"
    assert ("files", subject) in {(f.check, f.subject) for f in check_bundle(root, at="receive").findings}


def test_receive_finds_a_changed_publication_file(tmp_path: Path) -> None:
    root = _published(tmp_path / "b")
    _flip_a_byte(root / "lake.ducklake")
    findings = [f for f in check_bundle(root, at="receive").findings if f.check == "files"]
    assert findings == [Finding("files", "lake.ducklake", "contents differ from the manifest's sha256, md5 or crc32c")]


def _store(root: Path, manifest: Manifest) -> dict[str, RemoteFile]:
    """``data/``'s entries as a blob store reports them after an upload that set Content-MD5."""
    reported: dict[str, RemoteFile] = {}
    for entry in manifest.files:
        if entry.root == "data":
            data = (root / DATA_PATH / entry.path).read_bytes()
            md5 = base64.b64encode(hashlib.md5(data, usedforsecurity=False).digest()).decode()
            reported[entry.path] = RemoteFile(len(data), {"md5": md5})
    return reported


def test_receive_with_a_lookup_checks_data_in_the_blob_store(tmp_path: Path, hashed: list[str]) -> None:
    root = _published(tmp_path / "b")
    manifest = read_manifest(root)
    store = _store(root, manifest)
    asked: list[str] = []

    def lookup(entry: BundleFile) -> RemoteFile | None:
        asked.append(entry.path)
        return store[entry.path]

    report = check_bundle(root, at="receive", lookup=lookup)
    assert report.checked == ("manifest", "files", "remote", "lake", "bindings", "references")
    assert report.ok
    assert asked == sorted(store), "every data entry, and only those"
    assert sorted(hashed) == ["lake.ducklake", "meta.duckdb"]


def test_receive_with_a_lookup_names_a_blob_that_differs(tmp_path: Path) -> None:
    root = _published(tmp_path / "b")
    store = _store(root, read_manifest(root))
    (path,) = store
    store[path] = RemoteFile(store[path].size, {"md5": base64.b64encode(b"\x00" * 16).decode()})
    findings = list(check_bundle(root, at="receive", lookup=lambda e: store[e.path]).findings)
    assert [(f.check, f.subject) for f in findings] == [("remote", f"{DATA_PATH}{path}")]


def test_receive_recomputes_fingerprints_when_asked(tmp_path: Path) -> None:
    root = _published(tmp_path / "b", rows=10)
    _edit_manifest(root, content_fingerprint="0" * 64)
    assert check_bundle(root, at="receive").ok, "not recomputed by default"
    report = check_bundle(root, at="receive", fingerprints=True)
    assert report.checked == (*_RECEIVE, "fingerprints")
    assert [(f.check, f.subject) for f in report.findings] == [("fingerprints", MANIFEST_NAME)]


# ── open ────────────────────────────────────────────────────────────────


def test_open_checks_the_publication_by_size_and_hashes_nothing(tmp_path: Path, hashed: list[str]) -> None:
    root = _published(tmp_path / "b")
    _flip_a_byte(root / "lake.ducklake")
    _parquet(root).unlink()
    (root / "notes.txt").write_text("x")
    report = check_bundle(root, at="open")
    assert report.checked == ("manifest", "files")
    assert report.ok, "contents, data/ and unlisted files are receive's"
    assert hashed == []


def test_open_finds_a_publication_file_missing_or_of_another_size(tmp_path: Path) -> None:
    root = _published(tmp_path / "b")
    (root / "lake.ducklake").write_bytes((root / "lake.ducklake").read_bytes() + b"x")
    found = {(f.check, f.subject) for f in check_bundle(root, at="open").findings}
    assert found == {("files", "lake.ducklake")}
    (root / "lake.ducklake").unlink()
    (finding,) = check_bundle(root, at="open").findings
    assert finding == Finding("files", "lake.ducklake", "listed in the manifest but missing")


def test_open_still_refuses_a_store_on_another_schema(tmp_path: Path) -> None:
    root = _published(tmp_path / "b", rows=10)
    _edit_manifest(root, schema_version="2.19")
    assert {(f.check, f.subject) for f in check_bundle(root, at="open").findings} == {("manifest", "meta.duckdb")}


# ── what each profile takes ─────────────────────────────────────────────


@pytest.mark.parametrize("at", ["publish", "open"])
def test_a_lookup_is_for_receive(tmp_path: Path, at: Profile) -> None:
    with pytest.raises(ValueError, match="lookup= verifies data files at receive"):
        check_bundle(tmp_path, at=at, lookup=lambda e: None)


@pytest.mark.parametrize("at", ["publish", "open"])
def test_fingerprints_is_for_receive(tmp_path: Path, at: Profile) -> None:
    with pytest.raises(ValueError, match="fingerprints= opts a receive check"):
        check_bundle(tmp_path, at=at, fingerprints=True)


@pytest.mark.parametrize("at", ["publish", "receive", "open"])
def test_an_extension_is_told_the_profile(tmp_path: Path, at: Profile) -> None:
    def statcat(root: Path, manifest: Manifest, reg: BundleRegistry, profile: Profile) -> list[Finding]:
        return [Finding("statcat", "search", profile)]

    report = check_bundle(_published(tmp_path / "b", rows=10), at=at, extensions={"statcat": statcat})
    assert report.checked[-1] == "statcat"
    assert report.findings == (Finding("statcat", "search", at),)


# ── fingerprints at publish ─────────────────────────────────────────────


def test_publish_names_an_artefact_fingerprint_the_manifest_gets_wrong(tmp_path: Path) -> None:
    root = _published(tmp_path / "b", rows=10)
    document = json.loads((root / MANIFEST_NAME).read_text())
    document["artefacts"][0]["fingerprint"] = "0" * 32
    (root / MANIFEST_NAME).write_text(json.dumps(document))
    findings = [f for f in check_bundle(root, at="publish").findings if f.check == "fingerprints"]
    assert [f.subject for f in findings] == [document["artefacts"][0]["urn"]]


def test_publish_names_a_binding_fingerprint_the_manifest_gets_wrong(tmp_path: Path) -> None:
    root = _published(tmp_path / "b", rows=10)
    document = json.loads((root / MANIFEST_NAME).read_text())
    document["bindings"][0]["structure_fingerprint"] = "0" * 64
    (root / MANIFEST_NAME).write_text(json.dumps(document))
    findings = [f for f in check_bundle(root, at="publish").findings if f.check == "fingerprints"]
    assert [(f.subject, f.detail.split()[0]) for f in findings] == [(str(FLOW.urn), "structure_fingerprint")]


# ── check_on_open ───────────────────────────────────────────────────────


def test_the_first_open_receives_and_stamps_and_the_next_hashes_nothing(tmp_path: Path, hashed: list[str]) -> None:
    root = _published(tmp_path / "b")
    stamps = tmp_path / "verified"
    first = check_on_open(root, stamps=stamps)
    assert (first.at, first.ok) == ("receive", True)
    stamp = json.loads((stamps / "b" / root.name).read_text())
    assert stamp["manifest_sha256"] == hashlib.sha256((root / MANIFEST_NAME).read_bytes()).hexdigest()
    assert stamp["checked"] == list(_RECEIVE)
    hashed.clear()
    again = check_on_open(root, stamps=stamps)
    assert (again.at, again.ok) == ("open", True)
    assert hashed == [], "a restart at an unchanged token re-hashes nothing"


def test_a_changed_manifest_under_the_same_token_is_received_again(tmp_path: Path) -> None:
    root = _published(tmp_path / "b", rows=10)
    stamps = tmp_path / "verified"
    assert check_on_open(root, stamps=stamps).at == "receive"
    _edit_manifest(
        root, bindings=[json.loads((root / MANIFEST_NAME).read_text())["bindings"][0] | {"observations": 11}]
    )
    report = check_on_open(root, stamps=stamps)
    assert report.at == "receive"
    assert [f.check for f in report.findings] == ["bindings"]
    assert check_on_open(root, stamps=stamps).at == "receive", (
        "a failed receive leaves the old stamp, which no longer matches"
    )


def test_a_failed_receive_writes_no_stamp(tmp_path: Path) -> None:
    root = _published(tmp_path / "b")
    _flip_a_byte(root / "meta.duckdb")
    stamps = tmp_path / "verified"
    assert not check_on_open(root, stamps=stamps).ok
    assert not stamps.exists()


@pytest.mark.parametrize("bundle_id", ["..", "a/b", "/abs"])
def test_a_bundle_id_that_cannot_name_a_directory_is_not_stamped(tmp_path: Path, bundle_id: str) -> None:
    root = make_bundle(tmp_path / "b", 10)
    write_manifest(root, bundle_id=bundle_id, producer=_PRODUCER)
    stamps = tmp_path / "verified"
    report = check_on_open(root, stamps=stamps)
    assert report.at == "receive"
    assert [f.check for f in report.findings] == ["manifest"]
    assert not stamps.exists()
