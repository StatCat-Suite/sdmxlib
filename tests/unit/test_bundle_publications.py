"""Publications in token directories over a shared ``data/``, and copying one (GH-123).

A bundle is ``<bundle>/data/`` plus one ``<bundle>/<token>/`` per publication.
A publication is written once: the next one copies its catalog and extends it,
writing only new data files, and the earlier one keeps serving untouched.
`copy_bundle` moves a publication elsewhere with its manifest last.
"""

import hashlib
import shutil
from pathlib import Path

import pytest

import sdmxlib as sl
from sdmxlib.bundle import (
    MANIFEST_NAME,
    ManifestError,
    Producer,
    check_bundle,
    copy_bundle,
    read_manifest,
    write_manifest,
)
from tests.support.bundles import FLOW, make_bundle, publish

pytestmark = pytest.mark.unit

_PRODUCER = Producer(name="tests", version="0")


def _digests(directory: Path) -> dict[str, str]:
    """Every file under `directory`, by relative path, to its sha256."""
    return {
        p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }


def _extend(first: Path, rows: int) -> Path:
    """A publication extending `first` with `rows` more observations, published."""
    with sl.LocalRegistry.build(first.parent, previous=first.name) as builder:
        builder._data_plane()  # ATTACHes the lake
        builder.connection.execute(
            "INSERT INTO lake.d.obs SELECT 'G' || (range % 7), range::DOUBLE FROM range(?)", [rows]
        )
        publication = builder.publication
    write_manifest(publication, bundle_id="b", producer=_PRODUCER)
    return publication


def test_a_reader_on_one_publication_keeps_answering_while_the_next_is_written(tmp_path: Path) -> None:
    first = publish(make_bundle(tmp_path / "b", 200_000))
    before = _digests(first)
    with sl.LocalRegistry.bundle(first) as reader:
        assert reader.obs(FLOW.urn).count() == 200_000
        second = _extend(first, 1_000)
        assert reader.obs(FLOW.urn).count() == 200_000
    with sl.LocalRegistry.bundle(second) as reader:
        assert reader.obs(FLOW.urn).count() == 201_000
    assert _digests(first) == before, "the earlier publication is byte-identical"
    assert check_bundle(first, at="publish").ok
    assert check_bundle(second, at="publish").ok


def test_the_next_publication_names_the_one_it_extends_and_carries_its_data_files(tmp_path: Path) -> None:
    first = publish(make_bundle(tmp_path / "b", 200_000))
    second = _extend(first, 1_000)
    one, two = read_manifest(first), read_manifest(second)
    assert two.previous_token == one.token
    assert second.name == two.token != one.token
    earlier = {f.path: f for f in one.files if f.root == "data"}
    carried = [f for f in two.files if f.root == "data" and f.path in earlier]
    written = [f for f in two.files if f.root == "data" and f.path not in earlier]
    assert [(f.path, f.size, f.digests, f.carried_from) for f in carried] == [
        (f.path, f.size, f.digests, one.token) for f in earlier.values()
    ]
    assert len(written) == 1, "the delta is one new data file"
    assert written[0].carried_from is None


def test_a_republish_writes_only_the_delta_into_data(tmp_path: Path) -> None:
    """The upload a republish costs: its publication directory and the data
    files no earlier manifest listed. Asserted as files, since a size bound
    would move with DuckDB's compression."""
    first = publish(make_bundle(tmp_path / "b", 200_000))
    data = tmp_path / "b" / "data"
    held = set(_digests(data))
    second = _extend(first, 1_000)
    new = set(_digests(data)) - held
    assert held <= set(_digests(data)), "nothing in data/ is deleted or rewritten"
    assert new == {f.path for f in read_manifest(second).files if f.root == "data" and f.carried_from is None}
    assert sum((data / path).stat().st_size for path in new) < (data / next(iter(held))).stat().st_size / 50


def test_a_data_file_only_an_earlier_snapshot_references_is_still_listed(tmp_path: Path) -> None:
    """The catalog keeps every snapshot until a producer expires it, and time
    travel reads them, so garbage collection must keep their files: the
    manifest lists every file the catalog reaches, not only the current
    snapshot's."""
    first = publish(make_bundle(tmp_path / "b", 200_000))
    (original,) = (f.path for f in read_manifest(first).files if f.root == "data")
    with sl.LocalRegistry.build(first.parent, previous=first.name) as builder:
        builder._data_plane()  # ATTACHes the lake
        builder.connection.execute("DELETE FROM lake.d.obs")
        builder.connection.execute("INSERT INTO lake.d.obs SELECT 'G1', range::DOUBLE FROM range(200000)")
        current = {
            Path(path).name
            for (path,) in builder.connection.execute(
                "SELECT data_file FROM ducklake_list_files('lake', 'obs', schema => 'd')"
            ).fetchall()
        }
        publication = builder.publication
    assert Path(original).name not in current, "the current snapshot no longer reads the first file"
    second = write_manifest(publication, bundle_id="b", producer=_PRODUCER)
    assert original in {f.path for f in second.files if f.root == "data"}
    assert check_bundle(publication, at="publish").ok


def test_build_without_previous_starts_an_empty_publication(tmp_path: Path) -> None:
    first = publish(make_bundle(tmp_path / "b", 10))
    with sl.LocalRegistry.build(tmp_path / "b") as builder:
        assert builder.get_all(sl.Dataflow) == []
        publication = builder.publication
    assert publication.parent == first.parent
    assert publication != first


def test_extending_an_unpublished_publication_is_refused(tmp_path: Path) -> None:
    started = make_bundle(tmp_path / "b", 10)
    with pytest.raises(ManifestError, match="not a published bundle"):
        sl.LocalRegistry.build(started.parent, previous=started.name)


def test_resuming_a_published_publication_is_refused_naming_previous(tmp_path: Path) -> None:
    first = publish(make_bundle(tmp_path / "b", 10))
    with pytest.raises(ManifestError, match=rf"published.*previous='{first.name}'"):
        sl.LocalRegistry.build(first.parent, resume=first.name)


def test_resuming_a_directory_build_did_not_start_is_refused(tmp_path: Path) -> None:
    (tmp_path / "b" / "elsewhere").mkdir(parents=True)
    with sl.LocalRegistry(tmp_path / "b" / "elsewhere" / "meta.duckdb") as reg:
        reg.add(FLOW)
    with pytest.raises(ManifestError, match=r"not a publication LocalRegistry\.build started"):
        sl.LocalRegistry.build(tmp_path / "b", resume="elsewhere")
    with pytest.raises(ManifestError, match=r"not a publication LocalRegistry\.build started"):
        sl.LocalRegistry.build(tmp_path / "b", resume="missing")


def test_previous_and_resume_together_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not both"):
        sl.LocalRegistry.build(tmp_path, previous="a", resume="b")


def test_a_registry_build_did_not_open_has_no_publication(tmp_path: Path) -> None:
    with (
        sl.LocalRegistry(tmp_path / "meta.duckdb") as reg,
        pytest.raises(ValueError, match=r"not a publication; LocalRegistry\.build"),
    ):
        _ = reg.publication


def test_copy_places_the_data_files_beside_the_destination_and_checks_clean(tmp_path: Path) -> None:
    first = publish(make_bundle(tmp_path / "src" / "b", 200_000))
    destination = tmp_path / "dst" / "b" / first.name
    manifest = copy_bundle(first, destination)
    assert manifest == read_manifest(first)
    assert (destination / MANIFEST_NAME).read_bytes() == (first / MANIFEST_NAME).read_bytes()
    assert _digests(tmp_path / "dst" / "b" / "data") == _digests(tmp_path / "src" / "b" / "data")
    report = check_bundle(destination, at="publish")
    assert report.ok, "\n".join(map(str, report.findings))
    with sl.LocalRegistry.bundle(destination) as reg:
        assert reg.obs(FLOW.urn).count() == 200_000


def test_copying_the_next_publication_copies_only_its_new_data_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = publish(make_bundle(tmp_path / "src" / "b", 200_000))
    second = _extend(first, 1_000)
    copy_bundle(first, tmp_path / "dst" / "b" / first.name)
    copied: list[Path] = []
    original = shutil.copyfile

    def counting(source: Path, destination: Path) -> Path:
        copied.append(Path(destination))
        return original(source, destination)

    monkeypatch.setattr(shutil, "copyfile", counting)
    copy_bundle(second, tmp_path / "dst" / "b" / second.name)
    manifest = read_manifest(second)
    publication = sum(f.root == "publication" for f in manifest.files)
    delta = sum(f.root == "data" and f.carried_from is None for f in manifest.files)
    assert len(copied) == publication + delta + 1, "its own files, the new data file, and the manifest"
    assert check_bundle(tmp_path / "dst" / "b" / second.name, at="publish").ok


def test_an_interrupted_copy_leaves_an_unpublished_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = publish(make_bundle(tmp_path / "src" / "b", 200_000))
    destination = tmp_path / "dst" / "b" / first.name
    original = shutil.copyfile
    calls = 0

    def failing(source: Path, target: Path) -> Path:
        nonlocal calls
        calls += 1
        if calls == 3:
            msg = "disk full"
            raise OSError(msg)
        return original(source, target)

    monkeypatch.setattr(shutil, "copyfile", failing)
    with pytest.raises(OSError, match="disk full"):
        copy_bundle(first, destination)
    assert not (destination / MANIFEST_NAME).exists()
    report = check_bundle(destination, at="publish")
    assert report.checked == ("manifest",)
    assert [f.detail for f in report.findings] == [
        f"{destination} has no {MANIFEST_NAME}, so it is not a published bundle"
    ]
    with pytest.raises(ManifestError, match="not a published bundle"):
        sl.LocalRegistry.bundle(destination)
    monkeypatch.setattr(shutil, "copyfile", original)
    copy_bundle(first, destination)
    assert check_bundle(destination, at="publish").ok, "and a retry completes it"


def test_copy_refuses_a_published_destination(tmp_path: Path) -> None:
    first = publish(make_bundle(tmp_path / "src" / "b", 10))
    destination = tmp_path / "dst" / "b" / first.name
    copy_bundle(first, destination)
    with pytest.raises(ManifestError, match="already published"):
        copy_bundle(first, destination)


def test_copy_refuses_a_source_that_differs_from_its_manifest(tmp_path: Path) -> None:
    first = publish(make_bundle(tmp_path / "src" / "b", 200_000))
    (data_file,) = (tmp_path / "src" / "b" / "data").rglob("*.parquet")
    damaged = bytearray(data_file.read_bytes())
    damaged[len(damaged) // 2] ^= 0xFF
    data_file.write_bytes(bytes(damaged))
    destination = tmp_path / "dst" / "b" / first.name
    with pytest.raises(ManifestError, match="does not match the manifest"):
        copy_bundle(first, destination)
    assert not (destination / MANIFEST_NAME).exists()
