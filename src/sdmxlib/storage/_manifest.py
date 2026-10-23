"""A bundle's manifest: what is in it, and which publication it is (GH-66).

`sdmxlib.bundle` is the public surface; the model lives here, below
`sdmxlib.local`, because `LocalRegistry.bundle` reads a manifest and the
manifest writer opens a `LocalRegistry` — defined in `sdmxlib.bundle`, it would
be a cycle.

**Identity is a token, integrity is per-file checksums.** Two builds from the
same inputs differ in every file's bytes (DuckDB's files and DuckLake's
UUID-named parquet files alike), so no hash of the files can say "same
content". The writer knows when it published, so it says so: `token` is a fresh
UUIDv7 per publication, and caches key on it — the snapshot-id model of
Iceberg, Delta and DuckLake. The sha256 per file is the OCI/DVC use of hashes:
proof the files are the ones the manifest describes, which the bundle checker
verifies (GH-67).
"""

from datetime import datetime
from pathlib import Path
from typing import Annotated

import msgspec

#: The manifest's file name, at the bundle root.
MANIFEST_NAME = "bundle.manifest.json"

#: The format this build writes. A reader takes any ``1.x``: a minor version
#: only adds fields, which a reader that predates them ignores. 1.1 added
#: `provenance` (GH-68).
FORMAT_VERSION = "1.1"

#: A JSON value of any kind: what `product` and `extensions` carry without interpreting.
#: Annotated so the generated schema can say so; `object` keeps it inert.
type JsonValue = Annotated[object, msgspec.Meta(extra_json_schema={"description": "A JSON value of any kind."})]


class ManifestError(ValueError):
    """A bundle manifest that is missing, malformed, or in a format this build cannot read."""


class Producer(msgspec.Struct, frozen=True, kw_only=True):
    """What built the bundle: a name and its version, e.g. a bundle builder."""

    name: str
    version: str


class BundleFile(msgspec.Struct, frozen=True, kw_only=True):
    """One file of the bundle: its path from the root (``/``-separated), size and sha256."""

    path: str
    size: int
    sha256: str


class BindingCount(msgspec.Struct, frozen=True, kw_only=True):
    """How many observations a dataflow's binding holds, as DuckLake counts them."""

    dataflow: str
    observations: int


class Provenance(msgspec.Struct, frozen=True, kw_only=True):
    """Where a fetched bundle came from, and what was asked for (GH-68).

    Attributes:
        source: The registry's stable id (`RestRegistry.id`), e.g. ``ECB``.
        endpoint: The REST endpoint it was fetched from.
        api: The SDMX REST API version spoken to it.
        retrieved_at: When the fetch started (UTC).
        dataflow: The dataflow's URN, at the version it resolved to.
        key: The SDMX key asked for, or None for every series.
        start: The inclusive ``startPeriod`` asked for, if any.
        end: The inclusive ``endPeriod`` asked for, if any.
        structures_only: Whether observations were left out on purpose.
        artefacts: Every structural artefact fetched, by URN, sorted.
    """

    source: str
    endpoint: str
    api: str
    retrieved_at: datetime
    dataflow: str
    key: str | None = None
    start: str | None = None
    end: str | None = None
    structures_only: bool = False
    artefacts: tuple[str, ...] = ()


class Manifest(msgspec.Struct, frozen=True, kw_only=True):
    """`bundle.manifest.json`: written last, so a directory without one is unpublished.

    Attributes:
        format_version: The manifest format, ``major.minor``.
        bundle_id: The producer's stable name for the bundle, kept across
            rebuilds. Identifies *which* bundle, never which publication.
        token: A UUIDv7 minted at each publication. Changes on every rebuild,
            never on a read; what a cache or an ETag keys on.
        created_at: When the manifest was written (UTC).
        producer: What built the bundle.
        sdmxlib_version: The sdmxlib that wrote the manifest.
        schema_version: The store schema of ``meta.duckdb``, checked before the
            file is opened (GH-57).
        files: Every file but the manifest, sorted by path.
        bindings: Observation counts per bound dataflow, sorted by URN.
        product: Producer-specific fields, carried and not interpreted.
        extensions: Sections owned by other packages (statcatlib's search
            index, say), by name; carried and not interpreted.
        provenance: Where a fetched bundle came from (format 1.1); None for
            one built from data the producer holds.
    """

    format_version: str
    bundle_id: str
    token: str
    created_at: datetime
    producer: Producer
    sdmxlib_version: str
    schema_version: str
    files: tuple[BundleFile, ...]
    bindings: tuple[BindingCount, ...]
    product: dict[str, JsonValue] = msgspec.field(default_factory=dict)
    extensions: dict[str, dict[str, JsonValue]] = msgspec.field(default_factory=dict)
    provenance: Provenance | None = None


class _FormatProbe(msgspec.Struct):
    """Just the format version, read before committing to a model."""

    format_version: str | None = None


def generate_schema() -> bytes:
    """The manifest's JSON Schema, generated from `Manifest`, as the shipped file holds it."""
    schema = msgspec.json.schema(Manifest)
    return msgspec.json.format(msgspec.json.encode(schema), indent=2) + b"\n"


def encode_manifest(manifest: Manifest) -> bytes:
    """The manifest as indented JSON, the form written to disk."""
    return msgspec.json.format(msgspec.json.encode(manifest), indent=2) + b"\n"


def read_manifest(root: Path) -> Manifest:
    """The manifest at a bundle's root.

    Args:
        root: The bundle directory.

    Returns:
        The manifest.

    Raises:
        ManifestError: If there is none, it is malformed, or its major format
            version is not one this build reads.
    """
    path = root / MANIFEST_NAME
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        msg = f"{root} has no {MANIFEST_NAME}, so it is not a published bundle"
        raise ManifestError(msg) from exc
    try:
        found = msgspec.json.decode(raw, type=_FormatProbe).format_version
    except msgspec.MsgspecError as exc:
        msg = f"{path} is not a bundle manifest: {exc}"
        raise ManifestError(msg) from exc
    if found is None:
        # A builder's own manifest from before this format existed. sdmxlib
        # owns the format, so it is refused rather than read around.
        msg = (
            f"{path} has no format_version, so it is not an sdmxlib bundle manifest (a builder's own "
            f"format?). Republish the bundle with sdmxlib.bundle.write_manifest."
        )
        raise ManifestError(msg)
    if found.partition(".")[0] != FORMAT_VERSION.partition(".")[0]:
        msg = f"{path} is manifest format {found}; this sdmxlib reads {FORMAT_VERSION.partition('.')[0]}.x"
        raise ManifestError(msg)
    try:
        return msgspec.json.decode(raw, type=Manifest)
    except msgspec.MsgspecError as exc:
        msg = f"{path} is not a valid format {found} manifest: {exc}"
        raise ManifestError(msg) from exc
