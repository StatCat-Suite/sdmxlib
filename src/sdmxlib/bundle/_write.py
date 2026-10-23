"""Publishing a bundle: inventory it, count it, and write its manifest last (GH-66)."""

import hashlib
import uuid
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path

from sdmxlib.local.registry import LocalRegistry
from sdmxlib.model.binding import DataflowBinding
from sdmxlib.storage._manifest import (
    FORMAT_VERSION,
    MANIFEST_NAME,
    BindingCount,
    BundleFile,
    Manifest,
    Producer,
    encode_manifest,
)
from sdmxlib.storage.schema import get_schema_version

#: Read size for checksums: large enough that a multi-gigabyte parquet file is
#: a sequential read, small enough not to matter in memory.
_CHUNK = 1 << 20


def sha256_of(path: Path) -> str:
    """The sha256 of a file, read in chunks so a large one stays out of memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def inventory(root: Path) -> tuple[BundleFile, ...]:
    """Every file under `root` but the manifest, sorted by its ``/``-separated path.

    Args:
        root: The bundle directory.

    Returns:
        Each file's path from the root, size and sha256.
    """
    skip = {MANIFEST_NAME, f"{MANIFEST_NAME}.tmp"}
    files = sorted(p for p in root.rglob("*") if p.is_file() and p.relative_to(root).as_posix() not in skip)
    return tuple(
        BundleFile(path=p.relative_to(root).as_posix(), size=p.stat().st_size, sha256=sha256_of(p)) for p in files
    )


def write_manifest(
    path: str | Path,
    *,
    bundle_id: str,
    producer: Producer,
    product: dict[str, object] | None = None,
    extensions: dict[str, dict[str, object]] | None = None,
) -> Manifest:
    """Publish the bundle at `path`: write its manifest, with a fresh token.

    Write it **last**, once nothing else in the directory will change: the
    inventory is the files as they are now, and a directory without a manifest
    is an unpublished bundle. Writing again replaces it with a new token, which
    is what tells every cache keyed on the old one to refresh.

    Observation counts come from the bundle's own lake, through each binding's
    observation query, so DuckLake answers them from its catalog. The
    manifest goes in through a temporary file and a rename, so a reader never
    sees half of one.

    Args:
        path: The bundle directory: ``meta.duckdb``, ``lake.ducklake``, ``parquet/``.
        bundle_id: The producer's stable name for the bundle, kept across rebuilds.
        producer: What built it.
        product: Producer-specific fields, carried and not interpreted.
        extensions: Sections owned by other packages, by name.

    Returns:
        The manifest written.
    """
    root = Path(path)
    with LocalRegistry._over_bundle(root).open() as reg:
        schema_version = get_schema_version(reg.connection) or ""
        bindings = sorted(reg.get_all(DataflowBinding), key=lambda b: str(b.dataflow.urn))
        counts = tuple(
            BindingCount(dataflow=str(b.dataflow.urn), observations=reg.obs(b.dataflow).count()) for b in bindings
        )
    manifest = Manifest(
        format_version=FORMAT_VERSION,
        bundle_id=bundle_id,
        token=str(uuid.uuid7()),
        created_at=datetime.now(UTC),
        producer=producer,
        sdmxlib_version=metadata.version("sdmxlib"),
        schema_version=schema_version,
        files=inventory(root),
        bindings=counts,
        product=product or {},
        extensions=extensions or {},
    )
    staged = root / f"{MANIFEST_NAME}.tmp"
    staged.write_bytes(encode_manifest(manifest))
    staged.replace(root / MANIFEST_NAME)
    return manifest
