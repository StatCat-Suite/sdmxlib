"""An edition as a sealed artefact: its manifest, and what identifies it.

A product is a directory holding one directory per edition, named by its
edition id, and the ``data/`` every edition shares.
``LocalRegistry.build(product, previous=edition_id)`` starts an edition,
minting its edition id; `seal` seals it: a file list with a sha256 per file,
observation counts per binding, and the ``edition_id``, the value a cache or
an ETag keys on. ``LocalRegistry.edition(edition_dir)`` opens it read-only.
`read_manifest` reads a manifest back, refusing a format it cannot read;
`check_edition` checks an edition against it and against itself;
`copy_edition` copies one elsewhere, manifest last, and `edition_files` lists
what such a copy needs for another tool; `verify_remote` checks the
files a server reads from object storage by size and a digest the store keeps; `build_edition`
builds one from dataflows' closures at a registry and the observations a
producer holds, cutting codelists to partials; and `fetch_edition` lands
one dataflow from an SDMX REST source as a sealed edition, its provenance
recorded. `apply_overlays` changes display annotations without a rebuild,
`effective_token` is what a cache keys on when it does, and `fold_overlays`
writes overlays into a new edition for good. `canonical_json` is the
defined text of an artefact that a fingerprint hashes. The manifest's JSON Schema
ships beside it, for producers outside Python: `manifest_schema`.

From a shell, ``sdmxlib edition check``, ``sdmxlib edition copy``,
``sdmxlib edition files`` and ``sdmxlib edition fetch`` run these: they are the ``sdmxlib-cli`` package
(``uv tool install sdmxlib-cli``), released with this one, so the library
itself never depends on typer.

Example:
    from sdmxlib.edition import Producer, seal

    with sl.LocalRegistry.build("out/cenprof") as builder:
        builder.add_all(artefacts)
        edition = builder.edition_dir
    manifest = seal(edition, product_id="cenprof-2021",
                              producer=Producer(name="my-builder", version="1.2"))
    with sl.LocalRegistry.edition(edition) as reg:
        reg.manifest.edition_id
"""

from importlib import resources

from sdmxlib.edition._canonical import CANONICAL_FORM, canonical_json
from sdmxlib.edition._check import Check, Profile, Report, check_edition, check_on_open
from sdmxlib.edition._finding import Finding
from sdmxlib.edition._copy import edition_files, copy_edition
from sdmxlib.edition._remote import RemoteFile, content_crc32c, content_md5, verify_remote
from sdmxlib.edition._build import build_edition
from sdmxlib.edition._fetch import fetch_edition
from sdmxlib.edition._overlay import (
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
from sdmxlib.edition._write import Lifecycle, list_files, seal
from sdmxlib.storage._manifest import (
    DATA_PATH,
    DUCKLAKE_SPEC_VERSION,
    FORMAT_VERSION,
    MANIFEST_NAME,
    ArtefactEntry,
    BindingCount,
    EditionFile,
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
    "Check",
    "EditionFile",
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
    "build_edition",
    "canonical_json",
    "check_edition",
    "check_on_open",
    "content_crc32c",
    "content_md5",
    "copy_edition",
    "edition_files",
    "effective_token",
    "fetch_edition",
    "fold_overlays",
    "list_files",
    "manifest_schema",
    "overlay_digest",
    "overlay_schema",
    "read_manifest",
    "read_overlay",
    "seal",
    "verify_remote",
]


def overlay_schema() -> bytes:
    """The JSON Schema for an overlay document, as shipped (`overlay.schema.json`)."""
    return resources.files(__package__).joinpath("overlay.schema.json").read_bytes()


def manifest_schema() -> bytes:
    """The JSON Schema for ``manifest.json``, as shipped (`manifest.schema.json`)."""
    return resources.files(__package__).joinpath("manifest.schema.json").read_bytes()
