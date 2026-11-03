"""Files complete before they are visible, and on disk once they are (GH-335).

The sharded catalog spec §6.6 requires every Inventory document, and §6.7
the seal journal, to be complete before its name is visible: write a staged
file beside it, fsync it, put it in place, then fsync the directory. A process
kill leaves either the old name or the new file whole; the fsyncs extend that
to a power loss or a kernel crash, which can otherwise leave a visible file
empty or truncated.

- `create_exclusive` puts the file in place only if nothing is there, by
  ``os.link``: of two writers exactly one creates it. The manifest and an
  Inventory root are written this way.
- `replace_durably` replaces whatever is there, for a write that means to
  overwrite: the seal journal entry, which a retried seal rewrites, and the
  receive stamp. A file written by other means (the compacted store) takes
  `fsync_file` before its rename and `fsync_directory` after it.

A staged file is named ``.<name>.<pid>.<random>.tmp``, so two writers never
share one; `is_staged` recognises the name.
"""

import contextlib
import errno
import os
import re
import sys
import uuid
from pathlib import Path

_STAGED = re.compile(r"\.(?P<name>.+)\.\d+\.[0-9a-f]{32}\.tmp")

#: The errors ``fsync`` on a directory raises where the filesystem does not
#: support it (some FUSE, SMB and network mounts): ``EINVAL``, and ``ENOTSUP``
#: or ``EOPNOTSUPP``, which are one number on Linux (95) and two on macOS
#: (45 and 102).
_DIRECTORY_FSYNC_UNSUPPORTED = frozenset({errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP})


def is_staged(name: str, of: str) -> bool:
    """Whether the file name `name` is a staged copy of a file named `of`, which a crash can leave behind."""
    found = _STAGED.fullmatch(name)
    return found is not None and found["name"] == of


def fsync_directory(directory: Path) -> None:
    """Flush `directory`'s entries to disk, so a name just linked or renamed into it survives a power loss.

    On Windows a directory cannot be opened for ``fsync``, and NTFS journals
    its metadata, so this does nothing there. A filesystem that does not
    support ``fsync`` on a directory raises ``EINVAL``, ``ENOTSUP`` or
    ``EOPNOTSUPP``; this ignores those, since the name is already in place
    and the filesystem offers no way to flush it.

    Raises:
        OSError: Any other error opening or flushing the directory.
    """
    if sys.platform == "win32":
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    except OSError as exc:
        if exc.errno not in _DIRECTORY_FSYNC_UNSUPPORTED:
            raise
    finally:
        os.close(fd)


def fsync_file(path: Path) -> None:
    """Flush the file at `path` to disk, before a rename puts it in place of another."""
    fd = os.open(path, os.O_RDWR)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _stage(path: Path, data: bytes) -> Path:
    """`data` in a new staged file beside `path`, fsynced; the staged file's path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    with staged.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    return staged


def create_exclusive(path: Path, data: bytes) -> None:
    """Create `path` holding `data`, only if nothing is there, complete before it is visible.

    The steps of the sharded catalog spec §6.6: write a staged file beside
    `path`, fsync it, hard-link it to `path`, unlink the staged file, and fsync
    the directory. The link fails when `path` exists, so of two writers racing
    for one name exactly one creates it, and the name never holds part of a
    file. The parent directory is created when missing.

    The directory must be on a filesystem that supports hard links. exFAT,
    FAT and some FUSE and SMB mounts do not, and there the create fails.

    Args:
        path: The file to create.
        data: Its bytes.

    Raises:
        FileExistsError: If `path` exists: another writer created it first.
            Nothing is written.
        OSError: If the hard link fails for another reason, as on a
            filesystem without hard links; the error keeps the link's
            ``errno`` and names the requirement. Nothing is written.
    """
    staged = _stage(path, data)
    try:
        os.link(staged, path)
    except FileExistsError:
        raise
    except OSError as exc:
        msg = (
            f"cannot create {path}: hard-linking a staged file to it failed ({exc.strerror}). "
            f"{path.parent} must be on a filesystem that supports hard links, which exFAT, FAT "
            "and some FUSE and SMB mounts do not"
        )
        raise OSError(exc.errno, msg) from exc
    finally:
        with contextlib.suppress(FileNotFoundError):
            staged.unlink()
    fsync_directory(path.parent)


def replace_durably(path: Path, data: bytes) -> None:
    """Write `data` to `path`, replacing any file there, complete before it is visible.

    The steps of `create_exclusive`, with a rename that replaces in place of
    the link that refuses: for a file a writer means to overwrite.

    Args:
        path: The file to write.
        data: Its bytes.
    """
    staged = _stage(path, data)
    try:
        staged.replace(path)
    finally:
        # Gone after a rename; left behind when the rename failed.
        with contextlib.suppress(FileNotFoundError):
            staged.unlink()
    fsync_directory(path.parent)
