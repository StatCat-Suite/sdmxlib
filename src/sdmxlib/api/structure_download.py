"""What a whole-registry structure download did with each resource (GH-402).

`RestRegistry.download_all_structures` asks for every resource its API names
and records one `StructureDownload` each: the bytes it wrote, a 404 as
holding none, or anything else as refused. The records also go to
``downloads.json`` beside the files, so a copy says what was asked and what
came back.
"""

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from attrs import asdict, frozen

type DownloadStatus = Literal["written", "none-held", "refused"]

#: The record of the records, beside the files.
MANIFEST = "downloads.json"


@frozen
class StructureDownload:
    """One resource of a whole-registry structure download.

    Attributes:
        resource: The REST resource, such as ``"codelist"``.
        url: The query sent.
        status: ``"written"``; ``"none-held"`` for a 404, SDMX-REST's "no
            results"; or ``"refused"`` for any other answer, or a body not in
            the format asked for.
        path: The file written, when written.
        size: Its size in bytes, when written.
        sha256: Its SHA-256, hex, when written.
        http_status: The HTTP status, when not written.
        reason: The registry's text or the format mismatch, when refused.
    """

    resource: str
    url: str
    status: DownloadStatus
    path: Path | None = None
    size: int | None = None
    sha256: str | None = None
    http_status: int | None = None
    reason: str | None = None


def written(resource: str, url: str, path: Path) -> StructureDownload:
    """The record of a body written to `path`, with its size and digest."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return StructureDownload(
        resource=resource, url=url, status="written", path=path, size=path.stat().st_size, sha256=digest.hexdigest()
    )


def write_manifest(directory: Path, downloads: Sequence[StructureDownload]) -> Path:
    """Write `downloads` to ``downloads.json`` in `directory`, paths relative to it."""
    records = []
    for download in downloads:
        record = asdict(download)
        record["path"] = download.path.name if download.path is not None else None
        records.append(record)
    target = directory / MANIFEST
    target.write_text(json.dumps(records, indent=2) + "\n")
    return target
