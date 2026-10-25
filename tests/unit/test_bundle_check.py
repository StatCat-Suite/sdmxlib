"""`check_bundle` at ``publish``: a published bundle against its manifest and itself (GH-67, GH-123).

Each test damages one thing in a bundle that checked clean and asserts the
check that owns it says so. A finding is compared by check and subject, so a
test fails if the right thing is reported under the wrong check.
"""

import json
from pathlib import Path

import duckdb
import pytest

import sdmxlib as sl
from sdmxlib.bundle import DATA_PATH, MANIFEST_NAME, Finding, Producer, Profile, Report, check_bundle, write_manifest
from tests.support.bundles import FLOW, make_bundle

pytestmark = pytest.mark.unit

_PRODUCER = Producer(name="test-builder", version="1.0")
_ALL = ("manifest", "files", "lake", "bindings", "references", "fingerprints")


def _published(root: Path, *, rows: int = 200_000, closed: bool = True) -> Path:
    """A publication published with `write_manifest`; `closed` adds the concept scheme its DSD names."""
    publication = make_bundle(root, rows, closed=closed)
    write_manifest(publication, bundle_id="b", producer=_PRODUCER)
    return publication


def _found(report: Report) -> set[tuple[str, str]]:
    return {(f.check, f.subject) for f in report.findings}


def _parquet(root: Path) -> Path:
    """The publication's one data file, in the bundle's shared ``data/``."""
    (file,) = (root.parent / "data").rglob("*.parquet")
    return file


def _listed(root: Path, file: Path) -> str:
    """`file` as a finding names a data file: under the manifest's ``data_path``."""
    return f"{DATA_PATH}{file.relative_to(root.parent / 'data').as_posix()}"


def test_a_published_bundle_checks_clean_on_every_check(tmp_path: Path) -> None:
    report = check_bundle(_published(tmp_path / "b"), at="publish")
    assert report.findings == ()
    assert report.ok
    assert report.checked == _ALL


def test_without_a_manifest_only_the_manifest_is_checked(tmp_path: Path) -> None:
    report = check_bundle(make_bundle(tmp_path / "b", 10), at="publish")
    assert report.checked == ("manifest",)
    assert _found(report) == {("manifest", MANIFEST_NAME)}


def test_a_missing_parquet_file_is_missing_to_the_manifest_and_to_the_lake(tmp_path: Path) -> None:
    root = _published(tmp_path / "b")
    file = _parquet(root)
    file.unlink()
    listed = _listed(root, file)
    assert {("files", listed), ("lake", listed)} <= _found(check_bundle(root, at="publish"))


def test_a_changed_file_of_the_same_size_fails_its_checksum(tmp_path: Path) -> None:
    root = _published(tmp_path / "b")
    file = _parquet(root)
    data = bytearray(file.read_bytes())
    data[len(data) // 2] ^= 0xFF
    file.write_bytes(bytes(data))
    findings = [f for f in check_bundle(root, at="publish").findings if f.check == "files"]
    assert findings == [
        Finding("files", _listed(root, file), "contents differ from the manifest's crc32c, md5, sha256")
    ]


def test_a_file_added_after_publishing_is_not_in_the_manifest(tmp_path: Path) -> None:
    root = _published(tmp_path / "b")
    (root / "notes.txt").write_text("x")
    assert _found(check_bundle(root, at="publish")) == {("files", "notes.txt")}


def test_another_publications_data_file_is_not_a_finding(tmp_path: Path) -> None:
    """``data/`` is shared by every publication of a bundle, so a data file this
    manifest does not list belongs to another one, and only garbage collection,
    reading every manifest, may call it unreferenced."""
    root = _published(tmp_path / "b")
    _parquet(root).with_name("ducklake-another.parquet").write_bytes(b"another publication's")
    assert check_bundle(root, at="publish").ok


def test_a_data_file_the_lake_references_and_the_manifest_omits_is_named(tmp_path: Path) -> None:
    """What garbage collection keeps is what the manifests list, so a file the
    catalog needs and the manifest leaves out would be deleted under it."""
    root = _published(tmp_path / "b")
    path = root / MANIFEST_NAME
    document = json.loads(path.read_text())
    document["files"] = [f for f in document["files"] if f["root"] == "publication"]
    path.write_text(json.dumps(document))
    findings = check_bundle(root, at="publish").findings
    assert findings == (
        Finding("lake", _listed(root, _parquet(root)), "referenced by the DuckLake catalog but not in the manifest"),
    )


def test_an_observation_count_that_disagrees_is_named(tmp_path: Path) -> None:
    root = _published(tmp_path / "b", rows=10)
    path = root / MANIFEST_NAME
    document = json.loads(path.read_text())
    document["bindings"][0]["observations"] = 11
    path.write_text(json.dumps(document))
    findings = check_bundle(root, at="publish").findings
    assert [f for f in findings if f.check == "bindings"] == [
        Finding("bindings", str(FLOW.urn), "10 observations, the manifest says 11")
    ]


def test_a_reference_outside_the_bundle_is_named_where_it_sits(tmp_path: Path) -> None:
    """The DSD names a concept scheme the store does not hold."""
    report = check_bundle(_published(tmp_path / "b", rows=10, closed=False), at="publish")
    references = [f for f in report.findings if f.check == "references"]
    assert len(references) == 2, "GEO and OBS_VALUE"
    assert all("Concept=T:CS(1.0)" in f.detail for f in references)


def test_an_extension_check_runs_and_reports_under_its_name(tmp_path: Path) -> None:
    def search(root: Path, manifest: object, reg: object, at: Profile) -> list[Finding]:
        return [Finding("search", "search/index", f"no index for {root.name} at {at}")]

    report = check_bundle(_published(tmp_path / "b", rows=10), extensions={"search": search}, at="publish")
    assert report.checked == (*_ALL, "search")
    assert _found(report) == {("search", "search/index")}


def test_an_extension_reads_the_registry_after_the_references_check(tmp_path: Path) -> None:
    """GH-115: the references check read the store through a cursor it then
    closed, and every extension after it got "Connection already closed".
    The extension reports what it read, so the finding proves the read worked."""

    def bindings(root: Path, manifest: object, reg: sl.BundleRegistry, at: Profile) -> list[Finding]:
        found = reg.get_all(sl.DataflowBinding)
        return [Finding("bindings-seen", str(b.dataflow.urn), "read after references") for b in found]

    report = check_bundle(_published(tmp_path / "b", rows=10), extensions={"bindings-seen": bindings}, at="publish")
    assert report.checked == (*_ALL, "bindings-seen")
    assert _found(report) == {("bindings-seen", str(FLOW.urn))}


def test_a_store_on_another_schema_ends_the_run(tmp_path: Path) -> None:
    root = _published(tmp_path / "b", rows=10)
    path = root / MANIFEST_NAME
    path.write_text(json.dumps(json.loads(path.read_text()) | {"schema_version": "2.19"}))
    report = check_bundle(root, at="publish")
    assert report.checked == ("manifest", "files")
    assert _found(report) == {("manifest", "meta.duckdb")}


def test_checking_changes_nothing(tmp_path: Path) -> None:
    """A check is a read: run it, and the bundle still checks clean."""
    root = _published(tmp_path / "b")
    assert check_bundle(root, at="publish").ok
    assert check_bundle(root, at="publish").ok
    with duckdb.connect(str(root / "meta.duckdb"), read_only=True) as conn:
        assert conn.execute("SELECT count(*) FROM sdmx.artefact").fetchone() == (4,)
