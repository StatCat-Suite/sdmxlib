"""Publishing an edition: inventory it, count it, and write its manifest last (GH-66)."""

import hashlib
import posixpath
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Literal

import duckdb
import google_crc32c

from sdmxlib._duckdb import first
from sdmxlib.edition._availability import survey
from sdmxlib.edition._canonical import CANONICAL_FORM
from sdmxlib.edition._fingerprint import (
    References,
    artefact_fingerprint,
    bundle_fingerprint,
    structure_fingerprint,
    table_fingerprint,
)
from sdmxlib.local.registry import EditionRegistry, LocalRegistry
from sdmxlib.model.binding import DataflowBinding
from sdmxlib.model.datastructure import DataStructure
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.storage._manifest import (
    DATA_PATH,
    FORMAT_VERSION,
    MANIFEST_NAME,
    UNLISTED,
    ArtefactEntry,
    BindingCount,
    EditionFile,
    Manifest,
    ManifestError,
    Producer,
    Provenance,
    Tier,
    edition_storage_version,
    encode_manifest,
    read_manifest,
)
from sdmxlib.storage._trim import compact_for_publication
from sdmxlib.storage.schema import get_schema_version, meta_value
from sdmxlib.storage.writers import clear_availability, put_availability

#: Every data file and delete file the lake catalog references from any snapshot
#: it keeps, with the schema and table paths each one's path is relative to.
#: The table and schema rows are the ones live when the file was written.
#: ``__ducklake_metadata_{lake}`` is the database DuckLake attaches behind the
#: catalog's alias: ``lake`` in a builder, ``<prefix>__tables`` in a `EditionEngine`.
_LAKE_FILES = """
    SELECT s.path, s.path_is_relative, t.path, t.path_is_relative, f.path, f.path_is_relative
    FROM (
        SELECT table_id, begin_snapshot, path, path_is_relative FROM __ducklake_metadata_{lake}.ducklake_data_file
        UNION ALL
        SELECT table_id, begin_snapshot, path, path_is_relative FROM __ducklake_metadata_{lake}.ducklake_delete_file
    ) AS f
    JOIN __ducklake_metadata_{lake}.ducklake_table AS t
      ON t.table_id = f.table_id
     AND t.begin_snapshot <= f.begin_snapshot
     AND (t.end_snapshot IS NULL OR f.begin_snapshot < t.end_snapshot)
    JOIN __ducklake_metadata_{lake}.ducklake_schema AS s
      ON s.schema_id = t.schema_id
     AND s.begin_snapshot <= f.begin_snapshot
     AND (s.end_snapshot IS NULL OR f.begin_snapshot < s.end_snapshot)
"""

#: Whether a product's observations may still be revised: ``final`` ones are compacted.
type Lifecycle = Literal["revisable", "final"]

#: Read size for checksums: large enough that a multi-gigabyte parquet file is
#: a sequential read, small enough not to matter in memory.
_CHUNK = 1 << 20


def sha256_of(path: Path) -> str:
    """The sha256 of a file, read in chunks so a large one stays out of memory."""
    return digests_of(path)["sha256"]


def digests_of(path: Path) -> dict[str, str]:
    """A file's `REQUIRED_DIGESTS`, each lowercase hex, from one read in chunks."""
    sha256, md5, crc32c = hashlib.sha256(), hashlib.md5(usedforsecurity=False), google_crc32c.Checksum()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            sha256.update(chunk)
            md5.update(chunk)
            crc32c.update(chunk)
    return {"sha256": sha256.hexdigest(), "md5": md5.hexdigest(), "crc32c": crc32c.hexdigest().decode("ascii")}


def differing(entry: EditionFile, found: Mapping[str, str]) -> list[str]:
    """The algorithms whose digest in `found`, a file's, is not the one `entry` records, sorted."""
    return sorted(name for name, value in entry.digests.items() if found.get(name, value) != value)


def recorded(entry: EditionFile, found: Mapping[str, str]) -> bool:
    """Whether the digests `found` for a file agree with every one `entry` records that `found` holds."""
    return not differing(entry, found)


#: The edition files a server needs before it is ready (design §7.1).
_REQUIRED = frozenset({"metadata.duckdb", "tables.ducklake"})


def tier_of(path: str, root: str, tiers: Mapping[str, Tier]) -> Tier:
    """A file's placement tier: the longest prefix of `path` in `tiers`, else the default.

    The default is ``required`` for ``metadata.duckdb`` and
    ``tables.ducklake``, ``remote`` for a data file, and ``lazy`` for any
    other edition file, such as a search index.

    Args:
        path: The file's ``/``-separated path under its root.
        root: ``edition`` or ``data``.
        tiers: Overrides, by path prefix, for edition files.

    Returns:
        ``required``, ``lazy`` or ``remote``.
    """
    if root == "data":
        return "remote"
    matches = [prefix for prefix in tiers if path.startswith(prefix)]
    if matches:
        return tiers[max(matches, key=len)]
    return "required" if path in _REQUIRED else "lazy"


def list_files(root: Path, tiers: Mapping[str, Tier] | None = None) -> tuple[EditionFile, ...]:
    """Every file in the edition directory `root` but the manifest, sorted by ``/``-separated path.

    Args:
        root: The edition directory.
        tiers: Placement tier overrides by path prefix, as `seal`.

    Returns:
        Each file's path from the root, size, digests and tier.
    """
    files = sorted(p for p in root.rglob("*") if p.is_file() and p.relative_to(root).as_posix() not in UNLISTED)
    entries: list[EditionFile] = []
    for p in files:
        path = p.relative_to(root).as_posix()
        tier = tier_of(path, "edition", tiers or {})
        entries.append(EditionFile(path=path, size=p.stat().st_size, digests=digests_of(p), root="edition", tier=tier))
    return tuple(entries)


def lake_files(conn: duckdb.DuckDBPyConnection, data: Path | str, *, lake: str = "lake") -> list[str]:
    """Every data file the attached lake catalog references, as ``/``-separated paths under `data`, sorted.

    Read from DuckLake's metadata tables, so it covers every snapshot the
    catalog keeps, the current one and every one time travel can still reach:
    what garbage collection must keep while this edition lives.

    Args:
        conn: A connection with the edition's lake attached as `lake`.
        data: The lake's data path: a local directory, or a URI ending in
            ``/`` for data files in object storage.
        lake: The alias the lake is attached as.

    Returns:
        The paths, each once.

    Raises:
        ManifestError: If the catalog references a file outside `data`.
    """
    found: set[str] = set()
    for row in conn.execute(_LAKE_FILES.format(lake=lake)).fetchall():
        path = ""
        anchored = False
        for part, relative in zip(row[::2], row[1::2], strict=True):
            if relative:
                path = f"{path}{part}"
            else:
                path, anchored = str(part), True
        found.add(_under(data, path, anchored=anchored))
    return sorted(found)


def _under(data: Path | str, path: str, *, anchored: bool) -> str:
    """`path`, which DuckLake recorded, as a ``/``-separated path under `data`.

    `anchored` says DuckLake recorded it whole rather than relative to the
    data path.

    Raises:
        ManifestError: If it names a file outside `data`.
    """
    msg = f"the lake catalog references {path}, outside its data path {data}"
    if isinstance(data, Path):
        full = Path(path) if anchored else data / path
        try:
            return full.resolve().relative_to(data.resolve()).as_posix()
        except ValueError as exc:
            raise ManifestError(msg) from exc
    if anchored:
        if not path.startswith(data):
            raise ManifestError(msg)
        path = path[len(data) :]
    relative = posixpath.normpath(path)
    if relative.startswith(("../", "/")) or relative == "..":
        raise ManifestError(msg)
    return relative


def _data_inventory(
    data: Path, paths: list[str], carried: Mapping[str, EditionFile], previous: str | None
) -> list[EditionFile]:
    """The data files at `paths`, their digests carried from `carried` where the size still matches."""
    files: list[EditionFile] = []
    for path in paths:
        size = (data / path).stat().st_size
        earlier = carried.get(path)
        if earlier is not None and earlier.size == size:
            entry = EditionFile(
                path=path, size=size, digests=earlier.digests, root="data", tier="remote", carried_from=previous
            )
        else:
            entry = EditionFile(path=path, size=size, digests=digests_of(data / path), root="data", tier="remote")
        files.append(entry)
    return files


def _ducklake_versions(conn: duckdb.DuckDBPyConnection) -> tuple[str | None, str | None]:
    """The DuckLake extension's version, and the format the attached ``lake`` catalog records."""
    (extension,) = first(
        conn.execute("SELECT extension_version FROM duckdb_extensions() WHERE extension_name = 'ducklake'")
    ) or (None,)
    (spec,) = first(
        conn.execute("SELECT value FROM __ducklake_metadata_lake.ducklake_metadata WHERE key = 'version'")
    ) or (None,)
    return extension, spec


def artefact_entries(reg: EditionRegistry, partial_of: Mapping[str, str] | None = None) -> tuple[ArtefactEntry, ...]:
    """Every maintainable artefact in the store but the bindings, by URN, each fingerprinted.

    Read one at a time and dropped, so memory holds one artefact: on the
    census edition the largest, a 499,138-node hierarchy, sets the peak.
    `partial_of` gives a partial's `ArtefactEntry.partial_of`, by URN.

    Raises:
        ManifestError: If a `partial_of` URN names no partial in the store.
    """
    rows = reg.connection.execute(
        "SELECT urn, is_final, is_partial FROM sdmx.artefact WHERE artefact_type <> 'DataflowBinding' ORDER BY urn"
    ).fetchall()
    cuts = dict(partial_of or {})
    unknown = sorted(set(cuts) - {urn for urn, _, partial in rows if partial})
    if unknown:
        msg = f"partial_of names {', '.join(unknown)}, which the store holds as no partial artefact"
        raise ManifestError(msg)
    entries: list[ArtefactEntry] = []
    for urn, final, partial in rows:
        artefact = reg.get(SdmxUrn.parse(urn), eager=True)
        if artefact is None:
            msg = f"{urn} is listed in sdmx.artefact but reads as missing"
            raise ManifestError(msg)
        entries.append(
            ArtefactEntry(
                urn=urn,
                stability="semantic" if final else "legacy",
                partial=partial,
                fingerprint=artefact_fingerprint(artefact),
                partial_of=cuts.get(urn),
            )
        )
    return tuple(entries)


#: Larger than any data file, so a merge leaves one file per table.
_ONE_FILE = 1 << 62


def settle_lake(
    conn: duckdb.DuckDBPyConnection,
    *,
    expire_snapshots: bool,
    lifecycle: Lifecycle,
) -> None:
    """Bring the attached ``lake`` into the shape an edition ships (design §4.5).

    - Inlined rows are flushed into data files, so the catalog holds no rows.
    - Every table holding delete files is rewritten into new data files
      (``delete_threshold => 0``: DuckLake's default skips a table whose
      deleted rows are few, which is exactly a daily delta), so no delete file
      ships and every edition reads its rows from data files alone.
    - A ``final`` edition's tables have their files merged: nothing will
      be appended. DuckLake sorts what it merges by a table's ``SORTED BY``,
      which the producer sets (`write_observations`' ``sorted_by``); this sets
      no sort of its own.
    - With `expire_snapshots`, every snapshot but the current one is expired,
      so the catalog and the manifest hold only current files.

    Nothing is deleted from ``data/``: a file this edition stops
    referencing may be another's, and only garbage collection, reading every
    manifest, deletes.

    Args:
        conn: A connection with the edition's lake attached as ``lake``.
        expire_snapshots: Whether to expire every snapshot but the current one.
        lifecycle: ``final`` to compact; ``live`` otherwise.
    """
    conn.execute("CALL ducklake_flush_inlined_data('lake')")
    conn.execute("CALL ducklake_rewrite_data_files('lake', delete_threshold => 0)")
    if lifecycle == "final":
        conn.execute("CALL ducklake_merge_adjacent_files('lake', max_file_size => ?)", [_ONE_FILE])
    if expire_snapshots:
        conn.execute("CALL ducklake_expire_snapshots('lake', older_than => now())")


def _quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def binding_counts(reg: EditionRegistry, artefacts: tuple[ArtefactEntry, ...]) -> tuple[BindingCount, ...]:
    """Each binding's count, data structure and fingerprints, by dataflow URN."""
    fingerprints = {a.urn: a.fingerprint for a in artefacts}
    references = References(reg.connection, fingerprints)
    counts: list[BindingCount] = []
    for binding in sorted(reg.get_all(DataflowBinding), key=lambda b: str(b.dataflow.urn)):
        dataflow, structure = str(binding.dataflow.urn), _structure_urn(binding)
        counts.append(
            BindingCount(
                dataflow=dataflow,
                observations=reg.obs(binding.dataflow).count(),
                structural_version=structure,
                fingerprint=table_fingerprint(reg.connection, reg.locate(binding)),
                structure_fingerprint=structure_fingerprint(references.closure(dataflow, [structure]), fingerprints),
            )
        )
    return tuple(counts)


#: A binding's `structural_version` tag: ``AGENCY:ID(VERSION)``.
_TAG = re.compile(r"^(?P<agency>[^:]+):(?P<id>[^(]+)\((?P<version>[^)]+)\)$")


def _structure_urn(binding: DataflowBinding) -> str:
    """The URN of the data structure a binding was written against, from its tag."""
    found = _TAG.match(binding.structural_version)
    if found is None:
        msg = (
            f"the binding of {binding.dataflow.urn} records structural_version "
            f"{binding.structural_version!r}, which is not AGENCY:ID(VERSION)"
        )
        raise ManifestError(msg)
    return str(DataStructure.urn_for(agency=found["agency"], id=found["id"], version=found["version"]))


def seal(
    path: str | Path,
    *,
    product_id: str,
    producer: Producer,
    producer_fields: dict[str, object] | None = None,
    extensions: dict[str, dict[str, object]] | None = None,
    provenance: Provenance | None = None,
    tiers: Mapping[str, Tier] | None = None,
    lifecycle: Lifecycle = "revisable",
    expire_snapshots: bool = False,
    availability: bool = True,
    partial_of: Mapping[str, str] | None = None,
) -> Manifest:
    """Publish the edition at `path`, which `LocalRegistry.build` started: write its manifest.

    Write it **last**, once every builder has closed: the inventory is the
    files as they are now, and a directory without a manifest is unsealed.
    An edition is sealed once. A change is a new edition,
    ``LocalRegistry.build(product, previous=edition_id)``, with a new edition id, which
    is what tells every cache keyed on the old one to refresh.

    The files are the edition directory's, and every data file its lake
    catalog references in the shared ``data/``. A data file the previous
    edition's manifest lists at the same size keeps that manifest's sha256,
    marked with its edition id in ``carried_from``, so a republish hashes only the
    files it wrote. Observation counts come from the lake, through each
    binding's observation query, so DuckLake answers them from its catalog.
    Each binding's columns are scanned once. A value in a coded column that
    is not a code of its codelist or value list, an empty string standing for
    "no value" say, refuses the edition: an absent value is ``NULL``. The
    same scan gives the values each dimension holds, the SDMX 3.1
    `AvailabilityConstraint` for the unfiltered query, which is stored in
    ``metadata.duckdb`` so a server answers it without scanning the observations
    (`EditionRegistry.availability`).

    The manifest goes in through a temporary file and a rename, so a reader
    never sees half of one.

    Args:
        path: The edition directory, ``builder.edition_dir``.
        product_id: The producer's stable name for the edition, kept across rebuilds.
        producer: What built it.
        producer_fields: Producer-specific fields, carried and not interpreted.
        extensions: Sections owned by other packages, by name.
        provenance: Where a fetched edition came from; `fetch_edition` supplies it.
        tiers: Placement tiers for edition files, by path prefix, the
            longest matching prefix winning: ``{"search/": 2}``. A file no
            prefix matches is tier 1 for ``metadata.duckdb`` and ``tables.ducklake``
            and 2 otherwise; a data file is always 3.
        lifecycle: ``final`` for an edition whose observations will not be
            revised, which merges each table's files; ``live`` otherwise.
        expire_snapshots: Whether to expire every snapshot but the current
            one, so the manifest lists only the files it reads. Time travel
            to an earlier state is lost.
        availability: Whether to store each binding's availability. The
            coded columns are checked either way.
        partial_of: For each partial artefact the producer cut, by URN, the
            artefact fingerprint of the full one it was cut from, recorded as
            its `ArtefactEntry.partial_of`. `check_edition(at="publish",
            structures=...)` checks each against the full artefact.

    Returns:
        The manifest written.

    Raises:
        ManifestError: If the edition is already sealed, was not
            started by `LocalRegistry.build`, a builder still holds it open, a
            coded column holds a value that is not one of its codes, or a
            `partial_of` URN names no partial artefact in the store.
    """
    root = Path(path)
    data = root / DATA_PATH
    if (root / MANIFEST_NAME).exists():
        msg = (
            f"{root} is already sealed, and an edition is never rewritten. "
            f"Start a new one with LocalRegistry.build({str(root.parent)!r}, previous={root.name!r})."
        )
        raise ManifestError(msg)
    with LocalRegistry._over_edition(root, data).open() as reg:
        edition_id = meta_value(reg.connection, "edition_id")
        if edition_id is None:
            msg = f"{root} was not started by LocalRegistry.build, so it has no edition id to seal under"
            raise ManifestError(msg)
        previous = meta_value(reg.connection, "previous")
        schema_version = get_schema_version(reg.connection) or ""
        artefacts = artefact_entries(reg, partial_of)
        referenced: list[str] = []
        lake = (root / "tables.ducklake").exists()
        if lake:
            reg._data_plane()  # ATTACHes the lake
            settle_lake(reg.connection, expire_snapshots=expire_snapshots, lifecycle=lifecycle)
            referenced = lake_files(reg.connection, data)
        ducklake, spec = _ducklake_versions(reg.connection) if lake else (None, None)
        counts = binding_counts(reg, artefacts)
        # An edition extending another starts from its store, availability
        # included; it holds its own bindings' availability, or none (GH-203).
        clear_availability(reg.connection)
        if lake:
            _survey_bindings(root, reg, counts, store=availability)
    # Closing the last connection checkpoints a database, so a log left by a
    # builder that exited is gone by now. One that remains means another
    # connection still holds the database for writing, and its log holds
    # committed rows the database file's sha256 would not cover (GH-122).
    logs = sorted(p.relative_to(root).as_posix() for p in root.rglob("*.wal"))
    if logs:
        msg = (
            f"{root} still has a write-ahead log ({', '.join(logs)}): a connection holds one of its "
            f"databases open for writing. Close every builder before sealing."
        )
        raise ManifestError(msg)
    # Sealed, the store is never written: its keys enforce nothing, and
    # the ones the reads do not use leave it, in a fresh file (GH-165).
    compact_for_publication(root / "metadata.duckdb")
    manifest = Manifest(
        format_version=FORMAT_VERSION,
        product_id=product_id,
        edition_id=edition_id,
        previous=previous,
        created_at=datetime.now(UTC),
        producer=producer,
        sdmxlib_version=metadata.version("sdmxlib"),
        schema_version=schema_version,
        duckdb_version=duckdb.__version__,
        duckdb_storage_version=edition_storage_version(root),
        ducklake_version=ducklake,
        ducklake_spec_version=spec,
        data_path=DATA_PATH,
        files=(*_data_inventory(data, referenced, _carried(root, previous), previous), *list_files(root, tiers)),
        bindings=counts,
        artefacts=artefacts,
        canonical_form=CANONICAL_FORM,
        content_fingerprint=bundle_fingerprint(
            {a.urn: a.fingerprint for a in artefacts}, {b.dataflow: b.fingerprint for b in counts}
        ),
        producer_fields=producer_fields or {},
        extensions=extensions or {},
        provenance=provenance,
    )
    staged = root / f"{MANIFEST_NAME}.tmp"
    staged.write_bytes(encode_manifest(manifest))
    staged.replace(root / MANIFEST_NAME)
    return manifest


def _survey_bindings(root: Path, reg: LocalRegistry, counts: Iterable[BindingCount], *, store: bool) -> None:
    """Scan each binding: refuse a non-code value, and store each availability when `store`.

    Raises:
        ManifestError: If a coded column holds a value that is not a code.
    """
    observations = {count.dataflow: count.observations for count in counts}
    surveyed = [
        (binding, *survey(reg, binding, obs_count=observations.get(str(binding.dataflow.urn), 0)))
        for binding in reg.get_all(DataflowBinding)
    ]
    refused = [non_code for _, _, found in surveyed for non_code in found]
    if refused:
        lines = "\n".join(f"  {n.dataflow}: {n}" for n in refused)
        msg = (
            f"{root} was not sealed: {len(refused)} value(s) in coded columns are not codes. "
            f"An absent value is NULL.\n{lines}"
        )
        raise ManifestError(msg)
    if store:
        for binding, constraint, _ in surveyed:
            put_availability(reg.connection, str(binding.dataflow.urn), constraint)


def _carried(root: Path, previous: str | None) -> dict[str, EditionFile]:
    """The data files the previous edition's manifest lists, by path; empty if it is not beside `root`."""
    if previous is None:
        return {}
    try:
        earlier = read_manifest(root.parent / previous)
    except ManifestError:
        return {}
    return {f.path: f for f in earlier.files if f.root == "data"}
