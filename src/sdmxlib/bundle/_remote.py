"""Verifying a publication's files where they are stored remotely, by size and Content-MD5 (GH-125).

A server reads tier-3 files from blob storage by byte range and never holds
them, so it cannot hash them. What a blob store reports is the file's size
and, when the uploader set it, its ``Content-MD5``: the base64 of the same
MD5 the manifest records in hex. The publish job sets ``Content-MD5`` on every
upload (design §4.5), so a blob without one is a publishing fault.

sdmxlib holds no blob-store client. The caller passes a lookup, an HTTP HEAD
against the store say, and `verify_remote` compares what it returns with the
manifest.
"""

import base64
import binascii
from collections.abc import Callable, Collection

from attrs import frozen

from sdmxlib.bundle._check import Finding
from sdmxlib.storage._manifest import BundleFile, Manifest, Tier


@frozen
class RemoteFile:
    """What a blob store reports for one file.

    Attributes:
        size: In bytes.
        content_md5: The blob's ``Content-MD5`` property, base64, or None when
            the store has none recorded.
    """

    size: int
    content_md5: str | None


def content_md5(entry: BundleFile) -> str:
    """The ``Content-MD5`` an upload of `entry` sets: the base64 of its md5's 16 bytes."""
    return base64.b64encode(bytes.fromhex(entry.md5)).decode("ascii")


def verify_remote(
    manifest: Manifest, lookup: Callable[[BundleFile], RemoteFile | None], *, tiers: Collection[Tier] = (3,)
) -> list[Finding]:
    """Check the manifest's files of `tiers` against what their blob store reports.

    Args:
        manifest: The publication's manifest.
        lookup: The store's size and ``Content-MD5`` for an entry, or None
            when the store has no such file. Called once per entry checked.
        tiers: Which placement tiers to check; tier 3 by default, the files a
            server reads remotely.

    Returns:
        A finding under the check ``remote`` for each file missing, of
        another size, without a ``Content-MD5``, or with one that differs
        from the manifest's md5.
    """
    findings: list[Finding] = []
    for entry in manifest.files:
        if entry.tier not in tiers:
            continue
        subject = f"{manifest.data_path}{entry.path}" if entry.root == "data" else entry.path
        detail = _detail(entry, lookup(entry))
        if detail is not None:
            findings.append(Finding("remote", subject, detail))
    return findings


def _detail(entry: BundleFile, remote: RemoteFile | None) -> str | None:
    """What is wrong with `remote` as a copy of `entry`, or None."""
    if remote is None:
        return "listed in the manifest but missing from the store"
    if remote.size != entry.size:
        return f"{remote.size} bytes in the store, the manifest says {entry.size}"
    if remote.content_md5 is None:
        return "the store records no Content-MD5; the publish job sets one on every upload"
    try:
        stored = base64.b64decode(remote.content_md5, validate=True).hex()
    except binascii.Error, ValueError:
        return f"the store's Content-MD5 {remote.content_md5!r} is not base64"
    if stored != entry.md5:
        return f"Content-MD5 {remote.content_md5} in the store, the manifest's md5 is {content_md5(entry)}"
    return None
