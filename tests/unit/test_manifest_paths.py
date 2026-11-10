"""A manifest whose paths could reach outside its product is refused on decode (GH-380).

`copy_edition` and `check_edition` join each listed path to an edition or
data directory, so a `files` entry such as ``../../x`` or a `data_path` such
as ``../../elsewhere/`` would name a file outside the product. The decoder
refuses both, with the rule statcatlib's pull applies (`_is_safe_path` and
`_refuse_unsafe_files` in its ``store/blob.py``).
"""

import json
from pathlib import Path

import pytest

from sdmxlib.edition import (
    DATA_PATH,
    MANIFEST_NAME,
    ManifestError,
    check_edition,
    copy_edition,
    decode_manifest,
    read_manifest,
)
from sdmxlib.testing import Dim, Shape, synthetic_edition

pytestmark = pytest.mark.unit

_SHAPE = Shape(dimensions=(Dim(codes=20, depth=2, fanout=(2, 4)), Dim(codes=5)), observations=300, periods=4)

#: A `files` path that is empty, absolute, or holds an empty, ``.``, ``..``,
#: backslash or NUL segment.
_BAD_FILE_PATHS = (
    "",
    "/etc/passwd",
    "//server/share/x",
    "C:/Windows/x",
    "C:x",
    "a//b",
    "a/",
    ".",
    "./metadata.duckdb",
    "a/./b",
    "..",
    "../x",
    "a/../../x",
    "a\\b",
    "..\\x",
    "a\0b",
)

#: A `data_path` that is absolute, a URL, holds a backslash or NUL, or
#: leaves the product directory once resolved against the edition directory.
_BAD_DATA_PATHS = (
    "/srv/data/",
    "C:/data/",
    "s3://bucket/data/",
    "../../elsewhere/",
    "../..",
    "../data/../../x/",
    "..\\..\\x\\",
    "../data\0/",
)


@pytest.fixture(scope="module")
def edition(tmp_path_factory: pytest.TempPathFactory) -> Path:
    product = tmp_path_factory.mktemp("paths") / "census"
    return product / synthetic_edition(product, _SHAPE, seed=7).edition_id


@pytest.fixture(scope="module")
def document(edition: Path) -> dict[str, object]:
    raw: dict[str, object] = json.loads((edition / MANIFEST_NAME).read_bytes())
    return raw


def _with_file_path(document: dict[str, object], root: str, path: str) -> bytes:
    """The manifest with its first `root` entry's path set to `path`."""
    changed = json.loads(json.dumps(document))
    entry = next(f for f in changed["files"] if f["root"] == root)
    entry["path"] = path
    return json.dumps(changed).encode()


def _with_data_path(document: dict[str, object], data_path: str) -> bytes:
    changed = json.loads(json.dumps(document))
    changed["data_path"] = data_path
    return json.dumps(changed).encode()


def _write(directory: Path, raw: bytes) -> Path:
    """An edition directory holding only the manifest `raw`."""
    directory.mkdir(parents=True)
    (directory / MANIFEST_NAME).write_bytes(raw)
    return directory


def test_every_sealed_edition_decodes_with_a_data_file_in_a_subdirectory(edition: Path) -> None:
    """The refusal tests are vacuous unless a sealed manifest passes the same check."""
    manifest = read_manifest(edition)
    data = [f.path for f in manifest.files if f.root == "data"]
    assert data, "the edition has no data files"
    assert all("/" in path for path in data), data
    assert manifest.data_path == DATA_PATH
    assert decode_manifest((edition / MANIFEST_NAME).read_bytes(), "sealed") == manifest


@pytest.mark.parametrize("root", ["edition", "data"])
@pytest.mark.parametrize("path", _BAD_FILE_PATHS)
def test_a_files_path_that_is_not_relative_plain_segments_is_refused(
    document: dict[str, object], tmp_path: Path, root: str, path: str
) -> None:
    raw = _with_file_path(document, root, path)
    with pytest.raises(ManifestError, match="files lists the path"):
        decode_manifest(raw, "bad")
    with pytest.raises(ManifestError, match="files lists the path"):
        read_manifest(_write(tmp_path / "edition", raw))


def test_an_edition_entry_naming_the_manifest_is_refused(document: dict[str, object]) -> None:
    with pytest.raises(ManifestError, match=r"files lists the path 'manifest\.json'"):
        decode_manifest(_with_file_path(document, "edition", MANIFEST_NAME), "bad")


def test_a_data_entry_may_be_named_like_the_manifest(document: dict[str, object]) -> None:
    """The manifest's own name is refused under the edition root only, where a copy would overwrite it."""
    assert decode_manifest(_with_file_path(document, "data", f"d/{MANIFEST_NAME}"), "ok").files


@pytest.mark.parametrize("data_path", _BAD_DATA_PATHS)
def test_a_data_path_that_leaves_the_product_is_refused(
    document: dict[str, object], tmp_path: Path, data_path: str
) -> None:
    raw = _with_data_path(document, data_path)
    with pytest.raises(ManifestError, match="data_path"):
        decode_manifest(raw, "bad")
    with pytest.raises(ManifestError, match="data_path"):
        read_manifest(_write(tmp_path / "edition", raw))


@pytest.mark.parametrize("data_path", ["../data/", "../data", "data/", "../", "../other/../data/"])
def test_a_data_path_inside_the_product_decodes(document: dict[str, object], data_path: str) -> None:
    assert decode_manifest(_with_data_path(document, data_path), "ok").data_path == data_path


@pytest.mark.parametrize(
    ("root", "path", "data_path"),
    [
        pytest.param("edition", "../escaped.bin", DATA_PATH, id="edition-file"),
        pytest.param("data", "../../escaped.bin", DATA_PATH, id="data-file"),
        pytest.param(None, None, "../../elsewhere/", id="data-path"),
    ],
)
def test_copy_and_check_write_and_read_nothing_for_such_a_manifest(
    edition: Path, document: dict[str, object], tmp_path: Path, root: str | None, path: str | None, data_path: str
) -> None:
    if root is None or path is None:
        raw = _with_data_path(document, data_path)
    else:
        raw = _with_file_path(document, root, path)
    source = tmp_path / "source" / "census" / edition.name
    source.mkdir(parents=True)
    for file in edition.iterdir():
        if file.is_file() and file.name != MANIFEST_NAME:
            (source / file.name).write_bytes(file.read_bytes())
    (source / MANIFEST_NAME).write_bytes(raw)
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))

    destination = tmp_path / "destination" / "census" / edition.name
    with pytest.raises(ManifestError, match=r"files lists the path|data_path"):
        copy_edition(source, destination)
    assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == before

    report = check_edition(source, at="receive")
    assert not report.ok
    assert report.checked == ("manifest",)
    assert [f.check for f in report.findings] == ["manifest"]
