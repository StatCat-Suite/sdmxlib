"""A bundle as a published artefact: its manifest, and what identifies it.

A bundle is a directory holding one directory per publication, named by its
token, and the ``data/`` every publication shares.
``LocalRegistry.build(bundle, previous=token)`` starts a publication, minting
its token; `write_manifest` publishes it: a file inventory with a sha256 per
file, observation counts per binding, and the ``token``, the value a cache or
an ETag keys on. ``LocalRegistry.bundle(publication)`` opens it read-only.
`read_manifest` reads a manifest back, refusing a format it cannot read;
`check_bundle` checks a publication against it and against itself;
`copy_bundle` copies one elsewhere, manifest last, and `bundle_files` lists
what such a copy needs for another tool; `verify_remote` checks the
files a server reads from blob storage by size and Content-MD5; and `fetch_bundle` lands
one dataflow from an SDMX REST source as a published bundle, its provenance
recorded. `apply_overlays` changes display annotations without a rebuild,
`effective_token` is what a cache keys on when it does, and `fold_overlays`
writes overlays into a new publication for good. `canonical_json` is the
defined text of an artefact that a fingerprint hashes. The manifest's JSON Schema
ships beside it, for producers outside Python: `manifest_schema`.

From a shell, ``sdmxlib bundle check``, ``sdmxlib bundle copy``,
``sdmxlib bundle files`` and ``sdmxlib bundle fetch`` run these: they are the ``sdmxlib-cli`` package
(``uv tool install sdmxlib-cli``), released with this one, so the library
itself never depends on typer.

Example:
    from sdmxlib.bundle import Producer, write_manifest

    with sl.LocalRegistry.build("out/cenprof") as builder:
        builder.add_all(artefacts)
        publication = builder.publication
    manifest = write_manifest(publication, bundle_id="cenprof-2021",
                              producer=Producer(name="my-builder", version="1.2"))
    with sl.LocalRegistry.bundle(publication) as reg:
        reg.manifest.token
"""

from importlib import resources

from sdmxlib.bundle._canonical import CANONICAL_FORM, canonical_json
from sdmxlib.bundle._check import Check, Profile, Report, check_bundle, check_on_open
from sdmxlib.bundle._finding import Finding
from sdmxlib.bundle._copy import bundle_files, copy_bundle
from sdmxlib.bundle._remote import RemoteFile, content_md5, verify_remote
from sdmxlib.bundle._fetch import fetch_bundle
from sdmxlib.bundle._overlay import (
    OVERLAY_FORMAT,
    Merge,
    MergeHook,
    Overlay,
    OverlayAnnotation,
    OverlayEntry,
    OverlayError,
    OverlayText,
    applied,
    apply_overlays,
    effective_token,
    fold_overlays,
    overlay_digest,
    read_overlay,
)
from sdmxlib.bundle._write import Lifecycle, inventory, write_manifest
from sdmxlib.storage._manifest import (
    DATA_PATH,
    DUCKLAKE_SPEC_VERSION,
    FORMAT_VERSION,
    MANIFEST_NAME,
    ArtefactEntry,
    BindingCount,
    BundleFile,
    Manifest,
    ManifestError,
    Producer,
    Provenance,
    SIGNATURE_NAME,
    StoreFormatError,
    Tier,
    read_manifest,
)

__all__ = [
    "CANONICAL_FORM",
    "DATA_PATH",
    "DUCKLAKE_SPEC_VERSION",
    "FORMAT_VERSION",
    "MANIFEST_NAME",
    "OVERLAY_FORMAT",
    "SIGNATURE_NAME",
    "ArtefactEntry",
    "BindingCount",
    "BundleFile",
    "Check",
    "Finding",
    "Lifecycle",
    "Manifest",
    "ManifestError",
    "Merge",
    "MergeHook",
    "Overlay",
    "OverlayAnnotation",
    "OverlayEntry",
    "OverlayError",
    "OverlayText",
    "Producer",
    "Profile",
    "Provenance",
    "RemoteFile",
    "Report",
    "StoreFormatError",
    "Tier",
    "applied",
    "apply_overlays",
    "bundle_files",
    "canonical_json",
    "check_bundle",
    "check_on_open",
    "content_md5",
    "copy_bundle",
    "effective_token",
    "fetch_bundle",
    "fold_overlays",
    "inventory",
    "manifest_schema",
    "overlay_digest",
    "overlay_schema",
    "read_manifest",
    "read_overlay",
    "verify_remote",
    "write_manifest",
]


def overlay_schema() -> bytes:
    """The JSON Schema for an overlay document, as shipped (`overlay.schema.json`)."""
    return resources.files(__package__).joinpath("overlay.schema.json").read_bytes()


def manifest_schema() -> bytes:
    """The JSON Schema for ``bundle.manifest.json``, as shipped (`manifest.schema.json`)."""
    return resources.files(__package__).joinpath("manifest.schema.json").read_bytes()
