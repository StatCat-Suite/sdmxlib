"""Copying a publication: its listed files, verified, and its manifest last (GH-123).

How a server receives a bundle, how a mirror is made, and how another agency
receives one. The manifest goes last, so an interrupted copy leaves a
directory without one, which every reader and `check_bundle` treat as
unpublished.
"""

import os
import shutil
from collections.abc import Collection
from pathlib import Path

from sdmxlib.bundle._write import digests_of
from sdmxlib.storage._manifest import (
    MANIFEST_NAME,
    SIGNATURE_NAME,
    BundleFile,
    Manifest,
    ManifestError,
    Tier,
    read_manifest,
)


def copy_bundle(source: str | Path, destination: str | Path, *, tiers: Collection[Tier] = (1, 2, 3)) -> Manifest:
    """Copy the publication at `source` to `destination`, verify it, and write its manifest last.

    Copies the files the manifest lists: the publication's own into
    `destination`, its data files into the manifest's ``data_path`` resolved
    against `destination`, the ``data/`` beside it. A data file already there
    at the listed size and sha256 is kept, since another publication of the
    bundle shares it. Every copied file is checked against the manifest before
    the manifest itself is copied, byte for byte; a detached signature beside
    it is copied just before it.

    `tiers` limits the copy to the files of those placement tiers: a server
    pulls tiers 1 and 2 and reads tier 3 remotely, so ``tiers=(1, 2)`` is what
    it holds. A copy without every tier is incomplete by intent, and
    `check_bundle` reports the files it left out.

    Args:
        source: A published publication directory.
        destination: The publication directory to create, conventionally
            ``<bundle>/<token>/`` under another root.
        tiers: Which placement tiers to copy.

    Returns:
        The manifest copied.

    Raises:
        ManifestError: If `source` is not published, `destination` already
            is, or a copied file differs from the manifest.
    """
    src = Path(source)
    dst = Path(destination)
    manifest = read_manifest(src)
    if (dst / MANIFEST_NAME).exists():
        msg = f"{dst} is already published; a publication is never rewritten"
        raise ManifestError(msg)
    for entry in manifest.files:
        if entry.tier not in tiers:
            continue
        frm, to = _locations(src, dst, manifest, entry)
        if entry.root == "data" and _matches(to, entry):
            continue
        to.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(frm, to)
        if not _matches(to, entry):
            msg = f"{to} does not match the manifest after copying from {frm}: the source differs from its manifest"
            raise ManifestError(msg)
    if (src / SIGNATURE_NAME).is_file():
        shutil.copyfile(src / SIGNATURE_NAME, dst / SIGNATURE_NAME)
    staged = dst / f"{MANIFEST_NAME}.tmp"
    shutil.copyfile(src / MANIFEST_NAME, staged)
    staged.replace(dst / MANIFEST_NAME)
    return manifest


def _locations(src: Path, dst: Path, manifest: Manifest, entry: BundleFile) -> tuple[Path, Path]:
    """Where `entry` is read from and written to.

    The data path is normalised as text: ``<dst>/../data`` does not resolve
    on disk while ``<dst>`` does not exist yet, and a data file another
    publication already placed there would look missing.
    """
    if entry.root == "data":
        return (
            Path(os.path.normpath(src / manifest.data_path / entry.path)),
            Path(os.path.normpath(dst / manifest.data_path / entry.path)),
        )
    return src / entry.path, dst / entry.path


def _matches(file: Path, entry: BundleFile) -> bool:
    return file.is_file() and file.stat().st_size == entry.size and digests_of(file) == (entry.sha256, entry.md5)
