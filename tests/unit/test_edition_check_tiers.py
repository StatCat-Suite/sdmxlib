"""`check_edition` and `check_on_open` on a server holding some placement tiers (GH-384).

A pod pulls the required tier, checks it with ``tiers=("required",)`` and
reports ready, then pulls the lazy tier and checks again. The edition here has
two lazy files under ``search/`` and data files in ``data/``; ``file://``
stands in for the object store, as in `test_object_store.py`.
"""

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from sdmxlib.edition import (
    MANIFEST_NAME,
    Finding,
    Producer,
    Profile,
    Tier,
    _check,
    check_edition,
    check_on_open,
    copy_edition,
    read_manifest,
    seal,
)
from tests.support.bundles import make_bundle

pytestmark = pytest.mark.unit

_SEARCH = ("search/en.idx", "search/fr.idx")
_MISSING = [Finding("files", path, "listed in the manifest but missing") for path in _SEARCH]


@pytest.fixture(scope="module")
def published(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A sealed edition with data files, and ``search/`` at the lazy tier."""
    edition = make_bundle(tmp_path_factory.mktemp("tiers") / "b", 200_000)
    (edition / "search").mkdir()
    for path in _SEARCH:
        (edition / path).write_bytes(path.encode() * 64)
    seal(edition, product_id="b", producer=Producer(name="t", version="0"))
    return edition


@pytest.fixture
def pod(published: Path, tmp_path: Path) -> Path:
    """What a pod holds before it is ready: the required tier and the manifest."""
    return _pull(published, tmp_path / "pod" / "b" / published.name, ("required",))


def _pull(published: Path, destination: Path, tiers: tuple[Tier, ...]) -> Path:
    copy_edition(published, destination, tiers=tiers)
    return destination


def _pull_search(published: Path, pod: Path) -> None:
    """The background pull of the lazy tier into a pod that already holds the manifest."""
    shutil.copytree(published / "search", pod / "search")


def _remote(published: Path) -> str:
    return f"file://{published}/"


def _flip_a_byte(file: Path) -> None:
    data = bytearray(file.read_bytes())
    data[len(data) // 2] ^= 0xFF
    file.write_bytes(bytes(data))


def _stamp(stamps: Path, pod: Path) -> dict[str, object]:
    record: dict[str, object] = json.loads((stamps / "b" / pod.name).read_text())
    return record


@pytest.fixture
def hashed(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every file `check_edition` hashes, by name."""
    seen: list[str] = []
    original = _check.digests_of

    def counting(path: Path) -> dict[str, str]:
        seen.append(path.name)
        return original(path)

    monkeypatch.setattr(_check, "digests_of", counting)
    return seen


# ── the fixture ─────────────────────────────────────────────────────────


def test_the_edition_has_files_of_every_tier_and_the_pod_holds_the_required_one(published: Path, pod: Path) -> None:
    """The tests below are vacuous unless ``search/`` is lazy and the pod lacks it."""
    tiers = {f.path: f.tier for f in read_manifest(published).files if f.root == "edition"}
    assert tiers == {
        "metadata.duckdb": "required",
        "tables.ducklake": "required",
        "search/en.idx": "lazy",
        "search/fr.idx": "lazy",
    }
    assert [f.tier for f in read_manifest(published).files if f.root == "data"] == ["remote"]
    assert sorted(p.name for p in pod.iterdir()) == [MANIFEST_NAME, "metadata.duckdb", "tables.ducklake"]
    assert not (pod.parent / "data").exists()


# ── acceptance ──────────────────────────────────────────────────────────


def test_a_pod_holding_the_required_tier_passes_check_on_open(published: Path, pod: Path, tmp_path: Path) -> None:
    report = check_on_open(pod, stamps=tmp_path / "stamps", remote=_remote(published), tiers=("required",))
    assert report.at == "receive"
    assert report.findings == ()
    assert "files" in report.checked


@pytest.mark.parametrize("at", ["receive", "open"])
def test_check_edition_accepts_the_absent_lazy_tier_at_each_server_profile(
    published: Path, pod: Path, at: Profile
) -> None:
    assert check_edition(pod, at=at, remote=_remote(published), tiers=("required",)).findings == ()


def test_the_default_tiers_report_each_missing_search_file(published: Path, pod: Path, tmp_path: Path) -> None:
    report = check_on_open(pod, stamps=tmp_path / "stamps", remote=_remote(published))
    assert report.at == "receive"
    assert list(report.findings) == _MISSING
    assert list(check_edition(pod, at="open", remote=_remote(published)).findings) == _MISSING


def test_a_present_file_outside_tiers_with_another_digest_is_reported(published: Path, pod: Path) -> None:
    _pull_search(published, pod)
    _flip_a_byte(pod / "search/en.idx")
    report = check_edition(pod, at="receive", remote=_remote(published), tiers=("required",))
    assert list(report.findings) == [
        Finding("files", "search/en.idx", "contents differ from the manifest's crc32c, md5, sha256")
    ]


@pytest.mark.parametrize("at", ["receive", "open"])
def test_a_present_file_outside_tiers_of_another_size_is_reported(published: Path, pod: Path, at: Profile) -> None:
    _pull_search(published, pod)
    size = (pod / "search/fr.idx").stat().st_size
    (pod / "search/fr.idx").write_bytes(b"x")
    report = check_edition(pod, at=at, remote=_remote(published), tiers=("required",))
    assert list(report.findings) == [Finding("files", "search/fr.idx", f"1 bytes, the manifest says {size}")]


def test_an_unlisted_file_is_reported_whatever_the_tiers(published: Path, pod: Path) -> None:
    (pod / "stray").write_bytes(b"x")
    report = check_edition(pod, at="receive", remote=_remote(published), tiers=("required",))
    assert list(report.findings) == [Finding("files", "stray", "in the edition but not in the manifest")]


# ── the stamp ───────────────────────────────────────────────────────────


def test_the_stamp_records_the_tiers_its_check_covered(published: Path, pod: Path, tmp_path: Path) -> None:
    stamps = tmp_path / "stamps"
    check_on_open(pod, stamps=stamps, remote=_remote(published), tiers=("required",))
    assert _stamp(stamps, pod)["tiers"] == ["required"]
    again = check_on_open(pod, stamps=stamps, remote=_remote(published), tiers=("required",))
    assert (again.at, again.findings) == ("open", ())


def test_a_default_call_writes_a_stamp_for_every_tier(published: Path, tmp_path: Path) -> None:
    whole = _pull(published, tmp_path / "whole" / "b" / published.name, ("required", "lazy", "remote"))
    stamps = tmp_path / "stamps"
    assert check_on_open(whole, stamps=stamps).ok
    assert _stamp(stamps, whole)["tiers"] == ["required", "lazy", "remote"]
    assert check_on_open(whole, stamps=stamps, tiers=("required",)).at == "open"


def test_a_call_with_more_tiers_than_the_stamp_receives_and_hashes_the_pulled_files(
    published: Path, pod: Path, tmp_path: Path, hashed: list[str]
) -> None:
    stamps = tmp_path / "stamps"
    check_on_open(pod, stamps=stamps, remote=_remote(published), tiers=("required",))
    _pull_search(published, pod)
    hashed.clear()
    report = check_on_open(pod, stamps=stamps, remote=_remote(published), tiers=("required", "lazy"))
    assert (report.at, report.findings) == ("receive", ())
    assert sorted(hashed) == ["en.idx", "fr.idx", "metadata.duckdb", "tables.ducklake"]
    assert _stamp(stamps, pod)["tiers"] == ["required", "lazy"]
    hashed.clear()
    for tiers in (("required", "lazy"), ("required",), ("lazy",)):
        assert check_on_open(pod, stamps=stamps, remote=_remote(published), tiers=tiers).at == "open"
    assert hashed == []


def test_a_required_tier_stamp_does_not_pass_a_damaged_lazy_pull(published: Path, pod: Path, tmp_path: Path) -> None:
    stamps = tmp_path / "stamps"
    check_on_open(pod, stamps=stamps, remote=_remote(published), tiers=("required",))
    _pull_search(published, pod)
    _flip_a_byte(pod / "search/en.idx")
    report = check_on_open(pod, stamps=stamps, remote=_remote(published), tiers=("required", "lazy"))
    assert report.at == "receive"
    assert [(f.check, f.subject) for f in report.findings] == [("files", "search/en.idx")]
    assert _stamp(stamps, pod)["tiers"] == ["required"], "a failed receive leaves the earlier stamp"


def test_a_default_call_after_a_required_tier_stamp_receives_and_reports(
    published: Path, pod: Path, tmp_path: Path
) -> None:
    stamps = tmp_path / "stamps"
    check_on_open(pod, stamps=stamps, remote=_remote(published), tiers=("required",))
    report = check_on_open(pod, stamps=stamps, remote=_remote(published))
    assert report.at == "receive"
    assert list(report.findings) == _MISSING


def test_a_clean_receive_keeps_the_tiers_of_the_stamp_it_replaces(published: Path, pod: Path, tmp_path: Path) -> None:
    stamps = tmp_path / "stamps"
    check_on_open(pod, stamps=stamps, remote=_remote(published), tiers=("required",))
    _pull_search(published, pod)
    assert check_on_open(pod, stamps=stamps, remote=_remote(published), tiers=("lazy",)).at == "receive"
    assert _stamp(stamps, pod)["tiers"] == ["required", "lazy"]
    assert check_on_open(pod, stamps=stamps, remote=_remote(published), tiers=("required",)).at == "open"


def test_a_stamp_for_another_manifest_gives_the_new_stamp_no_tiers(published: Path, pod: Path, tmp_path: Path) -> None:
    stamps = tmp_path / "stamps"
    check_on_open(pod, stamps=stamps, remote=_remote(published), tiers=("required",))
    stamp = stamps / "b" / pod.name
    stamp.write_text(json.dumps(_stamp(stamps, pod) | {"manifest_sha256": "0" * 64, "tiers": ["required", "lazy"]}))
    assert check_on_open(pod, stamps=stamps, remote=_remote(published), tiers=("required",)).at == "receive"
    assert _stamp(stamps, pod)["tiers"] == ["required"]


def _write_stamp(stamps: Path, pod: Path, **extra: object) -> None:
    """The literal record 0.88's `_write_stamp` wrote, with `extra` keys."""
    stamp = stamps / "b" / pod.name
    stamp.parent.mkdir(parents=True)
    record: dict[str, object] = {
        "manifest_sha256": hashlib.sha256((pod / MANIFEST_NAME).read_bytes()).hexdigest(),
        "at": "receive",
        "checked": ["manifest", "files", "remote", "lake", "bindings", "references", "tags"],
        "verified_at": "2026-11-04T00:00:00+00:00",
    }
    stamp.write_text(json.dumps(record | extra))


@pytest.mark.parametrize("tiers", [("required", "lazy", "remote"), ("required",)])
def test_a_stamp_without_tiers_records_every_tier(
    published: Path, pod: Path, tmp_path: Path, tiers: tuple[Tier, ...]
) -> None:
    """A stamp 0.88 wrote came from a check of every tier, so it satisfies any `tiers`."""
    _pull_search(published, pod)
    stamps = tmp_path / "stamps"
    _write_stamp(stamps, pod)
    assert check_on_open(pod, stamps=stamps, remote=_remote(published), tiers=tiers).at == "open"


@pytest.mark.parametrize("recorded", ["required", None, {"required": True}, [1, 2]])
def test_a_stamp_whose_tiers_cannot_be_read_covers_none(
    published: Path, pod: Path, tmp_path: Path, recorded: object
) -> None:
    stamps = tmp_path / "stamps"
    _write_stamp(stamps, pod, tiers=recorded)
    report = check_on_open(pod, stamps=stamps, remote=_remote(published), tiers=("required",))
    assert (report.at, report.findings) == ("receive", ())
    assert _stamp(stamps, pod)["tiers"] == ["required"]


# ── tiers and remote= ───────────────────────────────────────────────────


def test_remote_checks_every_data_file_in_the_store_whatever_the_tiers(
    published: Path, pod: Path, tmp_path: Path
) -> None:
    store = _pull(published, tmp_path / "store" / "b" / published.name, ("required", "lazy", "remote"))
    (file,) = (store.parent / "data").rglob("*.parquet")
    file.unlink()
    report = check_edition(pod, at="receive", remote=_remote(store), tiers=("required",))
    assert {f.check for f in report.findings} == {"remote", "lake"}
    assert all(f.subject.endswith(file.name) for f in report.findings)


def test_without_remote_the_lake_check_still_reads_the_data_files(pod: Path) -> None:
    """`tiers` without ``remote`` quiets the ``files`` check alone: the lake check reads ``data/`` on disk."""
    quiet = check_edition(pod, at="receive", tiers=("required",))
    assert "files" not in {f.check for f in quiet.findings}
    assert "lake" in {f.check for f in quiet.findings}
    default = check_edition(pod, at="receive")
    assert [f.subject.rsplit(".", 1)[-1] for f in default.findings if f.check == "files"] == ["parquet", "idx", "idx"]


def test_remote_skips_the_data_files_on_disk_when_tiers_holds_the_remote_tier(published: Path, pod: Path) -> None:
    report = check_edition(pod, at="receive", remote=_remote(published), tiers=("required", "remote"))
    assert report.findings == ()
    assert "remote" in report.checked
