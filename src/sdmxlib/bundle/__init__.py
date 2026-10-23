"""A bundle as a published artefact: its manifest, and what identifies it.

`LocalRegistry.bundle` opens the layout — ``meta.duckdb``, ``lake.ducklake``,
``parquet/`` — and this names what a given bundle is. `write_manifest`
publishes one: a file inventory with a sha256 per file, observation counts per
binding, and a fresh ``token``, the value a cache or an ETag keys on.
`read_manifest` reads it back, refusing a format it cannot read, and
`check_bundle` checks a bundle against it and against itself, and
`fetch_bundle` lands one dataflow from an SDMX REST source as a published
bundle, its provenance recorded. `apply_overlays` changes display annotations
without a rebuild, `effective_token` is what a cache keys on when it does, and
`fold_overlays` writes overlays into a bundle for good. The manifest's
JSON Schema ships beside it, for producers outside Python: `manifest_schema`.

From a shell, ``sdmxlib bundle check PATH`` and ``sdmxlib bundle fetch`` run
these: they are the
``sdmxlib-cli`` package (``uv tool install sdmxlib-cli``), released with this one,
so the library itself never depends on typer.

Example:
    from sdmxlib.bundle import Producer, write_manifest

    manifest = write_manifest("out/cenprof", bundle_id="cenprof-2021",
                              producer=Producer(name="my-builder", version="1.2"))
    with sl.LocalRegistry.bundle("out/cenprof", require_manifest=True) as reg:
        reg.manifest.token
"""

from importlib import resources

from sdmxlib.bundle._check import Check, Finding, Report, check_bundle
from sdmxlib.bundle._fetch import fetch_bundle
from sdmxlib.bundle._overlay import (
    OVERLAY_FORMAT,
    Merge,
    Overlay,
    OverlayAnnotation,
    OverlayEntry,
    OverlayError,
    apply_overlays,
    effective_token,
    fold_overlays,
    overlay_digest,
    read_overlay,
)
from sdmxlib.bundle._write import inventory, write_manifest
from sdmxlib.storage._manifest import (
    FORMAT_VERSION,
    MANIFEST_NAME,
    BindingCount,
    BundleFile,
    Manifest,
    ManifestError,
    Producer,
    Provenance,
    read_manifest,
)

__all__ = [
    "FORMAT_VERSION",
    "MANIFEST_NAME",
    "OVERLAY_FORMAT",
    "BindingCount",
    "BundleFile",
    "Check",
    "Finding",
    "Manifest",
    "ManifestError",
    "Merge",
    "Overlay",
    "OverlayAnnotation",
    "OverlayEntry",
    "OverlayError",
    "Producer",
    "Provenance",
    "Report",
    "apply_overlays",
    "check_bundle",
    "effective_token",
    "fetch_bundle",
    "fold_overlays",
    "inventory",
    "manifest_schema",
    "overlay_digest",
    "overlay_schema",
    "read_manifest",
    "read_overlay",
    "write_manifest",
]


def overlay_schema() -> bytes:
    """The JSON Schema for an overlay document, as shipped (`overlay.schema.json`)."""
    return resources.files(__package__).joinpath("overlay.schema.json").read_bytes()


def manifest_schema() -> bytes:
    """The JSON Schema for ``bundle.manifest.json``, as shipped (`manifest.schema.json`)."""
    return resources.files(__package__).joinpath("manifest.schema.json").read_bytes()
