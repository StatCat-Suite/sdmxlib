"""A product directory's editions: listing, discarding, and the seal journal (GH-253, GH-254, GH-255, GH-259).

A product directory holds ``data/``, ``journal/`` and one directory per
edition, named by its edition id, a UUIDv7 (the sharded catalog spec §4.1).
An edition with ``manifest.json`` is sealed; one without is unsealed, and a
product has at most one.

Callers never read the directory themselves: `list_editions` reads it, and
`discard` removes an unsealed edition. The seal journal is what the narrow
repair reads (spec §6.7): `seal` writes ``journal/<edition_id>.json`` before
the manifest, and the writer that records the edition in the Inventory
removes it with `clear_journal`.
"""

import os
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

import msgspec

from sdmxlib.storage._manifest import MANIFEST_NAME, ManifestError, read_manifest

#: The directory of seal journal entries, in the product directory.
JOURNAL_DIR = "journal"

#: The UUID version an edition id is: a time-ordered UUIDv7.
_EDITION_UUID_VERSION = 7


class EditionInfo(msgspec.Struct, frozen=True, kw_only=True):
    """One edition directory of a product, as `list_editions` reads it.

    Attributes:
        edition_id: Its id, the directory's name.
        sealed: Whether it has a manifest.
        previous: Its manifest's ``previous``; None for an unsealed edition,
            which has no manifest, and for a product's first.
        created_at: When its build started, from the UUIDv7's timestamp.
    """

    edition_id: str
    sealed: bool
    previous: str | None
    created_at: datetime


class JournalEntry(msgspec.Struct, frozen=True, kw_only=True):
    """``journal/<edition_id>.json``: a seal this writer started.

    Attributes:
        product_id: The product.
        edition_id: The edition sealed.
        previous: The edition it was built on.
        sealed_at: When the seal wrote this entry.
    """

    product_id: str
    edition_id: str
    previous: str | None
    sealed_at: datetime


def _uuid7_time(name: str) -> datetime | None:
    """The time a UUIDv7 `name` carries, or None when `name` is not one."""
    try:
        parsed = uuid.UUID(name)
    except ValueError:
        return None
    if parsed.version != _EDITION_UUID_VERSION:
        return None
    return datetime.fromtimestamp((parsed.int >> 80) / 1000, tz=UTC)


def edition_dirs(product: Path) -> list[Path]:
    """The product's edition directories: those named by a UUIDv7, in id order."""
    if not product.is_dir():
        return []
    return sorted(p for p in product.iterdir() if p.is_dir() and _uuid7_time(p.name) is not None)


def list_editions(product: str | Path) -> list[EditionInfo]:
    """Every edition of the product at `product`, sealed or not, in id order.

    Reads manifests only and writes nothing.

    Args:
        product: The product directory.

    Returns:
        Each edition's id, whether it is sealed, its ``previous`` and when its
        build started.

    Raises:
        ManifestError: If an edition's manifest cannot be read.
    """
    found: list[EditionInfo] = []
    for edition in edition_dirs(Path(product)):
        created = _uuid7_time(edition.name)
        if created is None:
            continue
        sealed = (edition / MANIFEST_NAME).is_file()
        previous = read_manifest(edition).previous if sealed else None
        found.append(EditionInfo(edition_id=edition.name, sealed=sealed, previous=previous, created_at=created))
    return found


def refuse_second_unsealed(product: Path) -> None:
    """Refuse to start an edition while the product has an unsealed one (GH-253).

    Raises:
        ManifestError: If one exists, naming it, when its build started, and
            the two ways forward.
    """
    for info in list_editions(product):
        if not info.sealed:
            msg = (
                f"{product} has an unsealed edition, {info.edition_id}, started "
                f"{info.created_at.isoformat(timespec='seconds')}; a product has one at a time. Resume it with "
                f"LocalRegistry.build({str(product)!r}, resume={info.edition_id!r}), or throw it away with "
                f"sdmxlib.edition.discard({str(product / info.edition_id)!r})."
            )
            raise ManifestError(msg)


def discard(edition: str | Path) -> None:
    """Delete an unsealed edition's directory (GH-255).

    Data files it wrote under the product's shared ``data/`` stay: garbage
    collection deletes only files no manifest lists.

    Args:
        edition: The edition directory.

    Raises:
        ManifestError: If it is sealed, or is not an edition directory: its
            name is not a UUIDv7.
    """
    path = Path(edition)
    if _uuid7_time(path.name) is None or not path.is_dir():
        msg = f"{path} is not an edition directory: an edition is a directory named by its UUIDv7 edition id"
        raise ManifestError(msg)
    if (path / MANIFEST_NAME).exists():
        msg = f"{path} is sealed, and a sealed edition is never discarded; garbage collection retires it"
        raise ManifestError(msg)
    shutil.rmtree(path)


def journal_path(product: Path, edition_id: str) -> Path:
    """Where the seal journal entry of `edition_id` lives."""
    return product / JOURNAL_DIR / f"{edition_id}.json"


def write_journal(product: Path, entry: JournalEntry) -> None:
    """Write a seal's journal entry, complete before it is visible (GH-259)."""
    target = journal_path(product, entry.edition_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    staged.write_bytes(msgspec.json.encode(entry) + b"\n")
    staged.replace(target)


def read_journal(product: str | Path) -> list[JournalEntry]:
    """Every seal journal entry left in the product directory, in edition id order.

    Raises:
        ManifestError: If an entry cannot be read.
    """
    folder = Path(product) / JOURNAL_DIR
    if not folder.is_dir():
        return []
    entries: list[JournalEntry] = []
    for path in sorted(folder.glob("*.json")):
        try:
            entries.append(msgspec.json.decode(path.read_bytes(), type=JournalEntry))
        except msgspec.MsgspecError as exc:
            msg = f"{path} is not a seal journal entry: {exc}"
            raise ManifestError(msg) from exc
    return entries


def clear_journal(product: str | Path, edition_id: str) -> None:
    """Remove `edition_id`'s journal entry, once the writer has recorded the edition; absent is fine."""
    journal_path(Path(product), edition_id).unlink(missing_ok=True)
