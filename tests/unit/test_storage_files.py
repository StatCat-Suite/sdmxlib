"""Files complete before they are visible, and fsynced once they are (GH-335; sharded catalog spec §6.6, §6.7)."""

import os
import stat
import sys
import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from sdmxlib.edition import MANIFEST_NAME, ManifestError, Producer, list_files, read_journal, read_manifest, seal
from sdmxlib.storage import create_exclusive
from sdmxlib.storage._files import fsync_directory, is_staged, replace_durably
from tests.support.bundles import make_bundle

pytestmark = pytest.mark.unit

_PRODUCER = Producer(name="tests", version="0")

#: One fsync as recorded: the inode flushed, and whether it is a directory.
type Synced = tuple[int, bool]


@pytest.fixture
def synced(monkeypatch: pytest.MonkeyPatch) -> list[Synced]:
    """Every fsync from here on, as the inode it flushed and whether that is a directory."""
    calls: list[Synced] = []
    real: Callable[[int], None] = os.fsync

    def recording(fd: int) -> None:
        found = os.fstat(fd)
        calls.append((found.st_ino, stat.S_ISDIR(found.st_mode)))
        real(fd)

    monkeypatch.setattr(os, "fsync", recording)
    return calls


def _entry(path: Path) -> Synced:
    return path.stat().st_ino, path.is_dir()


def _left(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir())


def test_create_exclusive_writes_the_file_then_fsyncs_it_and_its_directory(
    tmp_path: Path, synced: list[Synced]
) -> None:
    target = tmp_path / "inventory" / "000000000001.json"
    create_exclusive(target, b"{}\n")
    assert target.read_bytes() == b"{}\n"
    assert _left(target.parent) == [target.name], "the staged file is gone"
    assert synced == [_entry(target), _entry(target.parent)], "the file, then the directory naming it"


def test_create_exclusive_refuses_a_name_that_exists_and_leaves_it_alone(tmp_path: Path) -> None:
    target = tmp_path / "CURRENT"
    target.write_bytes(b"first")
    with pytest.raises(FileExistsError):
        create_exclusive(target, b"second")
    assert target.read_bytes() == b"first"
    assert _left(tmp_path) == ["CURRENT"], "the staged file is gone"


def test_of_writers_racing_for_one_name_exactly_one_creates_it(tmp_path: Path) -> None:
    target = tmp_path / "root.json"
    writers = 8
    start = threading.Barrier(writers)
    won: list[bytes] = []
    lost: list[bytes] = []

    def write(data: bytes) -> None:
        start.wait()
        try:
            create_exclusive(target, data)
        except FileExistsError:
            lost.append(data)
        else:
            won.append(data)

    threads = [threading.Thread(target=write, args=(f"writer {n}".encode() * 1000,)) for n in range(writers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert (len(won), len(lost)) == (1, writers - 1)
    assert target.read_bytes() == won[0]
    assert _left(tmp_path) == ["root.json"]


def test_replace_durably_overwrites_then_fsyncs_the_file_and_its_directory(
    tmp_path: Path, synced: list[Synced]
) -> None:
    target = tmp_path / "journal" / "entry.json"
    replace_durably(target, b"old")
    replace_durably(target, b"new")
    assert target.read_bytes() == b"new"
    assert _left(target.parent) == ["entry.json"]
    assert synced[-2:] == [_entry(target), _entry(target.parent)]
    assert len(synced) == 4


def test_on_windows_a_directory_is_not_opened_for_fsync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows cannot open a directory for fsync, so the step is skipped there."""
    opened: list[object] = []

    def refuse(path: object, *_args: object) -> int:
        opened.append(path)
        raise PermissionError(path)

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(os, "open", refuse)
    fsync_directory(tmp_path)
    assert opened == []
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(PermissionError):
        fsync_directory(tmp_path)
    assert opened == [tmp_path]


def test_a_staged_name_is_recognised_for_its_file_alone() -> None:
    staged = f".{MANIFEST_NAME}.4242.{'a1' * 16}.tmp"
    assert is_staged(staged, MANIFEST_NAME)
    assert not is_staged(staged, "CURRENT")
    assert not is_staged(f"{MANIFEST_NAME}.tmp", MANIFEST_NAME)
    assert not is_staged(f".{MANIFEST_NAME}.4242.tmp", MANIFEST_NAME)


def test_a_staged_manifest_a_crash_left_is_never_listed(tmp_path: Path) -> None:
    started = make_bundle(tmp_path / "p", 10)
    (started / f".{MANIFEST_NAME}.4242.{'0' * 32}.tmp").write_bytes(b"{")
    (started / f"{MANIFEST_NAME}.tmp").write_bytes(b"{")
    (started / ".other.4242.0.tmp").write_bytes(b"{")
    assert [f.path for f in list_files(started) if f.path.endswith(".tmp")] == [".other.4242.0.tmp"]


def test_a_seal_fsyncs_its_journal_entry_its_store_and_its_manifest(tmp_path: Path, synced: list[Synced]) -> None:
    started = make_bundle(tmp_path / "p", 10)
    seal(started, product_id="p", producer=_PRODUCER)
    journal = tmp_path / "p" / "journal" / f"{started.name}.json"
    for path in (journal, journal.parent, started / "metadata.duckdb", started / MANIFEST_NAME, started):
        assert _entry(path) in synced, f"{path.relative_to(tmp_path)} was not fsynced"


def test_a_seal_that_loses_the_manifest_to_another_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The manifest is created exclusively: a seal never replaces one another process wrote."""
    started = make_bundle(tmp_path / "p", 10)

    def raced(meta: Path) -> None:
        (meta.parent / MANIFEST_NAME).write_bytes(b"theirs")

    monkeypatch.setattr("sdmxlib.edition._write.compact_for_publication", raced)
    with pytest.raises(ManifestError, match="sealed by another process while this seal ran"):
        seal(started, product_id="p", producer=_PRODUCER)
    assert (started / MANIFEST_NAME).read_bytes() == b"theirs"


def test_a_seal_retried_after_a_kill_rewrites_its_journal_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = make_bundle(tmp_path / "p", 10)
    with monkeypatch.context() as patched:

        def killed(_meta: Path) -> None:
            raise KeyboardInterrupt

        patched.setattr("sdmxlib.edition._write.compact_for_publication", killed)
        with pytest.raises(KeyboardInterrupt):
            seal(started, product_id="p", producer=_PRODUCER)
    (first,) = read_journal(tmp_path / "p")
    manifest = seal(started, product_id="p", producer=_PRODUCER)
    (entry,) = read_journal(tmp_path / "p")
    assert entry.sealed_at == manifest.created_at == read_manifest(started).created_at
    assert entry.sealed_at > first.sealed_at
