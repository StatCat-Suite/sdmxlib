"""Copying an edition: its listed files, verified, and its manifest last (GH-123).

How a server receives an edition, how a mirror is made, and how another agency
receives one. The manifest goes last, so an interrupted copy leaves a
directory without one, which every reader and `check_edition` treat as
unsealed.

`edition_files` lists the same files for a copy tool sdmxlib does not run
(rclone, azcopy, a cloud SDK), as paths under the product directory, the
manifest left out (GH-182). Those tools copy several files at once, so a
list's order cannot put the manifest last; the caller copies the listed files,
then the manifest, in two steps.
"""

import os
import posixpath
import shutil
from collections.abc import Collection
from pathlib import Path

from sdmxlib.edition._write import digests_of, recorded
from sdmxlib.storage._files import create_exclusive, fsync_directory, fsync_file
from sdmxlib.storage._manifest import (
    MANIFEST_NAME,
    SIGNATURE_NAME,
    EditionFile,
    Manifest,
    ManifestError,
    Tier,
    read_manifest,
)


def copy_edition(
    source: str | Path, destination: str | Path, *, tiers: Collection[Tier] = ("required", "lazy", "remote")
) -> Manifest:
    """Copy the edition at `source` to `destination`, verify it, and write its manifest last.

    Copies the files the manifest lists: the edition's own into
    `destination`, its data files into the manifest's ``data_path`` resolved
    against `destination`, the ``data/`` beside it. A data file already there
    at the listed size and sha256 is kept, since another edition of the
    edition shares it. Every copied file is checked against the manifest before
    the manifest itself is copied, byte for byte; a detached signature beside
    it is copied just before it. Each copied file, the signature included, and
    each directory the copy created or wrote into is fsynced before the
    manifest is created, so after a power loss a sealed copy never lists a
    file whose bytes were not on disk.

    `tiers` limits the copy to the files of those placement tiers: a server
    pulls tiers 1 and 2 and reads tier 3 remotely, so ``tiers=("required", "lazy")`` is what
    it holds. A copy without every tier is incomplete by intent, and
    `check_edition` reports the files it left out.

    Args:
        source: A sealed edition directory.
        destination: The edition directory to create, conventionally
            ``<product>/<edition_id>/`` under another root.
        tiers: Which placement tiers to copy.

    Returns:
        The manifest copied.

    Raises:
        ManifestError: If `source` is not sealed, `destination` already
            is, or a copied file differs from the manifest.
    """
    src = Path(source)
    dst = Path(destination)
    manifest = read_manifest(src)
    if (dst / MANIFEST_NAME).exists():
        msg = f"{dst} is already sealed; an edition is never rewritten"
        raise ManifestError(msg)
    copied: list[Path] = []
    written: set[Path] = set()
    _make_directory(dst, written)
    for entry in manifest.files:
        if entry.tier not in tiers:
            continue
        frm, to = _locations(src, dst, manifest, entry)
        if entry.root == "data" and _matches(to, entry):
            continue
        _make_directory(to.parent, written)
        shutil.copyfile(frm, to)
        copied.append(to)
        if not _matches(to, entry):
            msg = f"{to} does not match the manifest after copying from {frm}: the source differs from its manifest"
            raise ManifestError(msg)
    if (src / SIGNATURE_NAME).is_file():
        shutil.copyfile(src / SIGNATURE_NAME, dst / SIGNATURE_NAME)
        copied.append(dst / SIGNATURE_NAME)
    for file in copied:
        fsync_file(file)
        written.add(file.parent)
    # Deepest first: a directory's entries before the entry naming it.
    for directory in sorted(written, key=lambda d: len(d.parts), reverse=True):
        fsync_directory(directory)
    try:
        create_exclusive(dst / MANIFEST_NAME, (src / MANIFEST_NAME).read_bytes())
    except FileExistsError as exc:
        msg = f"{dst} was sealed by another process while this copy ran; an edition is never rewritten"
        raise ManifestError(msg) from exc
    return manifest


def edition_files(manifest: Manifest, *, tiers: Collection[Tier] = ("required", "lazy", "remote")) -> list[str]:
    """The files a copy of the edition's `tiers` needs, as ``/``-separated paths under the product directory.

    The product directory holds the edition's directory, named by its
    edition id, and the ``data/`` its ``data_path`` names: so
    ``<edition_id>/metadata.duckdb`` and ``data/<schema>/<table>/ducklake-….parquet``.
    Data files come first, then the edition's own, each group sorted.

    The manifest is left out: it is ``<edition_id>/manifest.json``, and a
    copy writes it after every listed file, so a copy that stops part way
    leaves an edition readers refuse.

    Args:
        manifest: The edition's manifest.
        tiers: Which placement tiers to list: ``(1, 2)`` is what a server
            reading tier 3 remotely holds.

    Returns:
        The paths, each once.

    Raises:
        ManifestError: If the manifest's ``data_path`` leads outside the
            product directory.
    """
    data: list[str] = []
    own: list[str] = []
    for entry in manifest.files:
        if entry.tier not in tiers:
            continue
        if entry.root == "data":
            path = posixpath.normpath(posixpath.join(manifest.edition_id, manifest.data_path, entry.path))
            if path.startswith(("../", "/")):
                msg = f"data_path {manifest.data_path!r} leads outside the product directory, at {path}"
                raise ManifestError(msg)
            data.append(path)
        else:
            own.append(f"{manifest.edition_id}/{entry.path}")
    return sorted(data) + sorted(own)


def _locations(src: Path, dst: Path, manifest: Manifest, entry: EditionFile) -> tuple[Path, Path]:
    """Where `entry` is read from and written to.

    The data path is normalised as text: ``<dst>/../data`` does not resolve
    on disk while ``<dst>`` does not exist yet, and a data file another
    edition already placed there would look missing.
    """
    if entry.root == "data":
        return (
            Path(os.path.normpath(src / manifest.data_path / entry.path)),
            Path(os.path.normpath(dst / manifest.data_path / entry.path)),
        )
    return src / entry.path, dst / entry.path


def _make_directory(directory: Path, written: set[Path]) -> None:
    """Create `directory` and its missing parents; add each one created, and its parent, to `written`."""
    created: list[Path] = []
    at = directory
    while not at.exists():
        created.append(at)
        at = at.parent
    directory.mkdir(parents=True, exist_ok=True)
    for made in created:
        written.update((made, made.parent))


def _matches(file: Path, entry: EditionFile) -> bool:
    return file.is_file() and file.stat().st_size == entry.size and recorded(entry, digests_of(file))
