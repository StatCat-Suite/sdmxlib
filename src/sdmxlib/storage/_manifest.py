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

import tempfile
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

import duckdb
import msgspec

#: The manifest's file name, at the bundle root.
MANIFEST_NAME = "bundle.manifest.json"

#: Reserved for a detached signature of the manifest, written after it, so
#: the manifest never lists it and the files check never reports it.
SIGNATURE_NAME = f"{MANIFEST_NAME}.sig"

#: What a publication directory holds beside its listed files.
UNLISTED = frozenset({MANIFEST_NAME, f"{MANIFEST_NAME}.tmp", SIGNATURE_NAME})

#: The format this build writes. A reader takes any ``2.x``: a minor version
#: only adds fields, which a reader that predates them ignores. 2.0 moved a
#: publication into a token directory beside a shared ``data/`` (GH-123).
FORMAT_VERSION = "2.0"

#: Where a publication's data files live, relative to its directory: the
#: ``data/`` every publication of a bundle shares.
DATA_PATH = "../data/"

#: A JSON value of any kind: what `product` and `extensions` carry without interpreting.
#: Annotated so the generated schema can say so; `object` keeps it inert.
type JsonValue = Annotated[object, msgspec.Meta(extra_json_schema={"description": "A JSON value of any kind."})]


#: The newest DuckLake catalog format this build reads: the one its DuckLake
#: writes. An older catalog attaches read-only unmigrated and reads; a newer
#: one is refused before the attach. `tests/unit/test_bundle_engine_versions.py`
#: asserts a fresh catalog records it, so a DuckLake upgrade that changes it
#: fails there first.
DUCKLAKE_SPEC_VERSION = "1.1"

#: A DuckDB file's main header: an 8-byte checksum, the ``DUCK`` magic, then
#: a little-endian 64-bit version number.
_MAGIC = slice(8, 12)
_MAIN_VERSION = slice(12, 20)
#: The main header's version from DuckDB 2.0 on, which says to read the
#: database header instead (`MainHeader::DEPRECATED_VERSION_NUMBER`).
_DEPRECATED = 999
#: DuckDB 2.0's storage version, the first a database header records directly.
_V2_0_0 = 69
#: Where the two database headers start. The one with the higher iteration
#: count is active; after its 8-byte checksum come six 64-bit fields
#: (iteration first) and then the storage field.
_DATABASE_HEADERS = (4096, 8192)
_ITERATION = 8
_STORAGE_FIELD = 56
#: Before DuckDB 2.0 a database header's storage field holds a serialization
#: number, which names the storage version (`SetStorageVersionInDatabaseHeader`
#: in DuckDB's `single_file_block_manager.cpp`). 0 and 64 are values old
#: releases wrote by mistake.
_SERIALIZATION = {0: 64, 1: 64, 2: 64, 3: 64, 64: 64, 4: 65, 5: 66, 6: 67, 7: 68}


class ManifestError(ValueError):
    """A bundle manifest that is missing, malformed, or in a format this build cannot read."""


class StoreFormatError(Exception):
    """A bundle's files are in a DuckDB or DuckLake format this build's engine cannot read.

    Raised from the manifest, before any file is opened.
    """


def storage_version_of(path: Path) -> int:
    """The storage version a DuckDB file's headers record, read without opening it as a database.

    DuckDB numbers its file formats: 64 for files compatible with 1.0, 65 with
    1.2, 66 with 1.3, 67 with 1.4, 68 with 1.5, 69 with 2.0. A release reads
    every number up to its own. The number is read as DuckDB reads it, from
    the active database header. The main header's own number is 999 from 2.0
    on, and stays at the version a file was created with, at most 65, when
    the file is later bumped to a newer one (GH-174).

    Args:
        path: A DuckDB database file.

    Returns:
        The number.

    Raises:
        StoreFormatError: If the file is not a DuckDB database, or records a
            storage field no DuckDB release writes.
    """
    with path.open("rb") as handle:
        header = handle.read(_DATABASE_HEADERS[-1] + _STORAGE_FIELD + 8)
    if header[_MAGIC] != b"DUCK" or len(header) < _DATABASE_HEADERS[-1] + _STORAGE_FIELD + 8:
        msg = f"{path} is not a DuckDB database file"
        raise StoreFormatError(msg)

    def field(start: int, offset: int) -> int:
        return int.from_bytes(header[start + offset : start + offset + 8], "little")

    active = max(_DATABASE_HEADERS, key=lambda start: field(start, _ITERATION))
    stored = field(active, _STORAGE_FIELD)
    main = int.from_bytes(header[_MAIN_VERSION], "little")
    if main == _DEPRECATED or stored >= _V2_0_0:
        return stored
    if stored not in _SERIALIZATION:
        msg = f"{path} records storage field {stored}, which no DuckDB release writes"
        raise StoreFormatError(msg)
    return _SERIALIZATION[stored]


def publication_storage_version(root: Path) -> int:
    """The newest storage version among a publication's DuckDB files, which a reader must open."""
    return max(storage_version_of(root / name) for name in ("meta.duckdb", "lake.ducklake") if (root / name).exists())


def engine_storage_version() -> int:
    """The storage version this DuckDB writes by default, the newest it reads.

    DuckDB has no SQL function for it, so a database file is created in a
    temporary directory and its header read: about 5 ms, paid per `bundle()`
    open rather than cached.
    """
    with tempfile.TemporaryDirectory(prefix="sdmxlib-storage-") as directory:
        probe = Path(directory) / "probe.duckdb"
        duckdb.connect(str(probe)).close()
        return storage_version_of(probe)


def refuse_engine_mismatch(manifest: Manifest, root: Path) -> None:
    """Refuse a publication whose files this engine cannot read, from its manifest alone.

    DuckDB reads every older storage version and refuses a newer one, so a
    storage version newer than the running DuckDB writes is refused. DuckLake
    likewise reads an older catalog format read-only, without migrating it
    (the census bundle's 1.0 catalog under DuckLake 1.1), so only a format
    newer than `DUCKLAKE_SPEC_VERSION` is refused.

    Args:
        manifest: The publication's manifest.
        root: The publication directory: named in the message, and its DuckDB
            files' headers read when the manifest records 999.

    Raises:
        StoreFormatError: Naming the bundle's version and the engine's.
    """
    engine = engine_storage_version()
    recorded = manifest.duckdb_storage_version
    if recorded == _DEPRECATED:
        # sdmxlib 0.73.0 recorded the main header's 999 for a file in a DuckDB
        # 2.x format, which names no version (GH-174). The database headers
        # do; reading them opens no database.
        recorded = publication_storage_version(root)
    if recorded > engine:
        msg = (
            f"{root} holds DuckDB storage version {recorded} (written by DuckDB "
            f"{manifest.duckdb_version}), and DuckDB {duckdb.__version__} here reads up to {engine}, so it was "
            f"not opened. Read it with DuckDB {manifest.duckdb_version} or later."
        )
        raise StoreFormatError(msg)
    spec = manifest.ducklake_spec_version
    if spec is not None and _spec_key(spec) > _spec_key(DUCKLAKE_SPEC_VERSION):
        msg = (
            f"{root} holds a DuckLake catalog in format {spec} (written by DuckLake "
            f"{manifest.ducklake_version}), and this sdmxlib reads formats up to {DUCKLAKE_SPEC_VERSION}, so it "
            f"was not opened. Read it with an sdmxlib and DuckDB as new as the ones that wrote it."
        )
        raise StoreFormatError(msg)


def _spec_key(spec: str) -> tuple[int, ...]:
    """A DuckLake format version, ``1.1``, as a comparable tuple; an unreadable one sorts above every other."""
    try:
        return tuple(int(part) for part in spec.split("."))
    except ValueError:
        return (1 << 31,)


class Producer(msgspec.Struct, frozen=True, kw_only=True):
    """What built the bundle: a name and its version, e.g. a bundle builder."""

    name: str
    version: str


#: Where a file is placed: 1 pulled before a server is ready, 2 pulled lazily
#: to local disk, 3 read remotely by byte range.
type Tier = Literal[1, 2, 3]


class BundleFile(msgspec.Struct, frozen=True, kw_only=True):
    """One file of the bundle: where it is, its digests, and its placement tier.

    Attributes:
        path: ``/``-separated, relative to the directory `root` names.
        size: In bytes.
        sha256: Of the file's contents, lowercase hex.
        md5: Of the file's contents, lowercase hex. The ``Content-MD5`` an
            upload sets is the base64 of the same digest.
        root: ``publication`` for a file in the publication directory,
            ``data`` for a data file under the manifest's `data_path`.
        tier: The producer's default placement: 1 pulled before a server is
            ready, 2 pulled lazily to local disk, 3 read remotely by byte range.
        carried_from: The token of the manifest `size`, `sha256` and `md5`
            were copied from, for a data file an earlier publication listed;
            None for one hashed by this publication.
    """

    path: str
    size: int
    sha256: str
    md5: str
    root: Literal["publication", "data"]
    tier: Tier
    carried_from: str | None = None


class BindingCount(msgspec.Struct, frozen=True, kw_only=True):
    """How many observations a dataflow's binding holds, as DuckLake counts them.

    Attributes:
        dataflow: The bound dataflow's URN.
        observations: The row count of the binding's table.
        structural_version: The URN of the data structure the binding was
            written against.
        fingerprint: The table fingerprint of the binding's table,
            ``<sum>:<count>``: what the observations are, whatever files
            hold them.
        structure_fingerprint: The structure fingerprint of the dataflow:
            what its structural closure is, the artefacts referring to it
            included.
    """

    dataflow: str
    observations: int
    structural_version: str
    fingerprint: str
    structure_fingerprint: str


class ArtefactEntry(msgspec.Struct, frozen=True, kw_only=True):
    """One maintainable artefact in the store.

    Attributes:
        urn: Its URN, at its pinned version.
        stability: ``semantic`` for a final semantic version (three numeric
            parts, no extension, major at least 1), which SDMX 3.0 holds
            immutable; ``legacy`` for any other.
        partial: Whether it is a partial item scheme, a subset of the full one.
        fingerprint: The SHA-256 of its canonical text, lowercase hex.
    """

    urn: str
    stability: Literal["semantic", "legacy"]
    partial: bool
    fingerprint: str


class Provenance(msgspec.Struct, frozen=True, kw_only=True):
    """Where a fetched bundle came from, and what was asked for.

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
        token: A UUIDv7 minted when the publication's build starts, and the
            name of its directory. Changes on every rebuild, never on a read;
            what a cache or an ETag keys on.
        previous_token: The publication this one extends, or None for the
            first of its bundle.
        created_at: When the manifest was written (UTC).
        producer: What built the bundle.
        sdmxlib_version: The sdmxlib that wrote the manifest.
        schema_version: The store schema of ``meta.duckdb``, checked before the
            file is opened.
        duckdb_version: The DuckDB release that wrote the DuckDB files.
        duckdb_storage_version: The newest storage version among the DuckDB
            files, as DuckDB reads it from each file's active database header
            (`storage_version_of`). A reader refuses one newer than its engine
            before opening any file. sdmxlib 0.73.0 recorded 999 for a file in
            DuckDB 2.0's format; a reader reads the headers for that.
        ducklake_version: The DuckLake extension that wrote ``lake.ducklake``,
            or None for a publication without one.
        ducklake_spec_version: The DuckLake format the catalog records, or
            None for a publication without one. A reader refuses one newer
            than its own.
        data_path: Where the data files live, relative to the publication
            directory: `DATA_PATH`.
        files: Every file of the publication but the manifest, and every data
            file its lake catalog references, sorted by root and path.
        bindings: Observation counts per bound dataflow, sorted by URN.
        artefacts: Every maintainable artefact in the store but the
            bindings, sorted by URN.
        canonical_form: The version of the canonical text every fingerprint
            here hashes. Fingerprints at different forms are never compared.
        content_fingerprint: The bundle fingerprint: every artefact
            fingerprint and every table fingerprint, so two publications
            with equal content have equal ones.
        product: Producer-specific fields, carried and not interpreted.
        extensions: Sections owned by other packages (statcatlib's search
            index, say), by name; carried and not interpreted.
        provenance: Where a fetched bundle came from; None for
            one built from data the producer holds.
    """

    format_version: str
    bundle_id: str
    token: str
    previous_token: str | None
    created_at: datetime
    producer: Producer
    sdmxlib_version: str
    schema_version: str
    duckdb_version: str
    duckdb_storage_version: int
    ducklake_version: str | None
    ducklake_spec_version: str | None
    data_path: str
    files: tuple[BundleFile, ...]
    bindings: tuple[BindingCount, ...]
    artefacts: tuple[ArtefactEntry, ...]
    canonical_form: str
    content_fingerprint: str | None = None
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
