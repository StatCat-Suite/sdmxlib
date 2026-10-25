"""Verifying a publication's files where they are stored remotely, by size and a digest the store keeps.

A server reads tier-3 files from object storage by byte range and never holds
them, so it cannot hash them. What a store reports is the file's size and the
digests it keeps, each base64 (GH-125, GH-181):

- **Azure Blob Storage** keeps ``Content-MD5`` when the uploader sets it, on a
  block upload through ``x-ms-blob-content-md5``.
- **Amazon S3** keeps the checksum the uploader picks. For a file uploaded in
  parts only a CRC is of the whole file (``CRC32C``, ``CRC32``,
  ``CRC64NVME``); a SHA-256 or MD5 is a composite of the parts, written with a
  ``-<parts>`` suffix.
- **Google Cloud Storage** always keeps CRC32C, and MD5 except for a composite
  object or an XML multipart upload.

The manifest records sha256, md5 and crc32c for every file (`BundleFile.digests`),
so every store keeps one of them for any upload. The publish job sets one on every upload
(design §4.5), and a file whose store keeps none the manifest has is a
publishing fault.

sdmxlib holds no blob-store client. The caller passes a lookup, an HTTP HEAD
against the store say, and `verify_remote` compares what it returns with the
manifest.
"""

import base64
import binascii
import re
from collections.abc import Callable, Collection, Mapping

from attrs import field, frozen

from sdmxlib.bundle._finding import Finding
from sdmxlib.storage._manifest import BundleFile, Manifest, Tier

#: A composite checksum: S3's digest of the parts' digests, with the part count.
_COMPOSITE = re.compile(r"-\d+$")


def _no_digests() -> Mapping[str, str]:
    return {}


@frozen
class RemoteFile:
    """What an object store reports for one file.

    Attributes:
        size: In bytes.
        digests: The digests the store keeps, by algorithm in lowercase
            (``md5``, ``sha256``, ``crc32c``, or another the store names),
            each base64 as the store reports it: Azure's ``Content-MD5``,
            S3's ``x-amz-checksum-crc32c``, GCS's ``crc32c`` and ``md5Hash``.
    """

    size: int
    digests: Mapping[str, str] = field(factory=_no_digests)


def content_md5(entry: BundleFile) -> str:
    """The ``Content-MD5`` an upload of `entry` sets: the base64 of its md5's 16 bytes."""
    return base64.b64encode(bytes.fromhex(entry.digests["md5"])).decode("ascii")


def content_crc32c(entry: BundleFile) -> str:
    """The CRC32C an upload of `entry` sets (S3's ``x-amz-checksum-crc32c``): the base64 of its 4 bytes."""
    return base64.b64encode(bytes.fromhex(entry.digests["crc32c"])).decode("ascii")


def verify_remote(
    manifest: Manifest, lookup: Callable[[BundleFile], RemoteFile | None], *, tiers: Collection[Tier] = (3,)
) -> list[Finding]:
    """Check the manifest's files of `tiers` against what their object store reports.

    Every algorithm both the store and the manifest hold is compared; the
    others are ignored.

    Args:
        manifest: The publication's manifest.
        lookup: The store's size and digests for an entry, or None when the
            store has no such file. Called once per entry checked.
        tiers: Which placement tiers to check; tier 3 by default, the files a
            server reads remotely.

    Returns:
        A finding under the check ``remote`` for each file missing, of another
        size, whose store keeps no digest the manifest records, or with a
        digest that differs from the manifest's.
    """
    findings: list[Finding] = []
    for entry in manifest.files:
        if entry.tier not in tiers:
            continue
        subject = f"{manifest.data_path}{entry.path}" if entry.root == "data" else entry.path
        findings += [Finding("remote", subject, detail) for detail in _details(entry, lookup(entry))]
    return findings


def _details(entry: BundleFile, remote: RemoteFile | None) -> list[str]:
    """What is wrong with `remote` as a copy of `entry`: nothing when it is one."""
    if remote is None:
        return ["listed in the manifest but missing from the store"]
    if remote.size != entry.size:
        return [f"{remote.size} bytes in the store, the manifest says {entry.size}"]
    recorded = entry.digests
    reported = {name.lower(): value for name, value in remote.digests.items()}
    # A composite is a digest of the parts' digests, so it says nothing about
    # the file; another digest the store keeps may still.
    whole = {name: value for name, value in reported.items() if not _COMPOSITE.search(value)}
    shared = sorted(recorded.keys() & whole.keys())
    if not shared:
        kept = ", ".join(f"{n} (a composite of the parts)" if n not in whole else n for n in sorted(reported))
        return [
            (
                f"the store keeps {kept or 'no digest'} and the manifest records {', '.join(sorted(recorded))}: "
                "none to compare; the publish job sets one the store keeps on every upload"
            )
        ]
    details: list[str] = []
    for name in shared:
        value = whole[name]
        try:
            stored = base64.b64decode(value, validate=True).hex()
        except binascii.Error, ValueError:
            details.append(f"the store's {name} {value!r} is not base64")
            continue
        if stored != recorded[name]:
            expected = base64.b64encode(bytes.fromhex(recorded[name])).decode("ascii")
            details.append(f"{name} {value} in the store, the manifest's is {expected}")
    return details
