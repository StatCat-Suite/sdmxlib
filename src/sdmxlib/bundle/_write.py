"""Publishing a bundle: inventory it, count it, and write its manifest last (GH-66)."""

import hashlib
import posixpath
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Literal, NamedTuple

import duckdb
import google_crc32c

from sdmxlib.bundle._canonical import CANONICAL_FORM
from sdmxlib.bundle._fingerprint import (
    References,
    artefact_fingerprint,
    bundle_fingerprint,
    structure_fingerprint,
    table_fingerprint,
)
from sdmxlib.local.registry import BundleRegistry, LocalRegistry
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
    BundleFile,
    Manifest,
    ManifestError,
    Producer,
    Provenance,
    Tier,
    encode_manifest,
    publication_storage_version,
    read_manifest,
)
from sdmxlib.storage._trim import compact_for_publication
from sdmxlib.storage.schema import get_schema_version, meta_value

#: Every data file and delete file the lake catalog references from any snapshot
#: it keeps, with the schema and table paths each one's path is relative to.
#: The table and schema rows are the ones live when the file was written.
#: ``__ducklake_metadata_{lake}`` is the database DuckLake attaches behind the
#: catalog's alias: ``lake`` in a builder, ``<prefix>__lake`` in a `BundleEngine`.
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

#: Whether a publication's observations may still be revised: ``final`` ones are compacted.
type Lifecycle = Literal["live", "final"]

#: Read size for checksums: large enough that a multi-gigabyte parquet file is
#: a sequential read, small enough not to matter in memory.
_CHUNK = 1 << 20


class Digests(NamedTuple):
    """A file's digests as the manifest records them, each lowercase hex."""

    sha256: str
    md5: str
    crc32c: str


def sha256_of(path: Path) -> str:
    """The sha256 of a file, read in chunks so a large one stays out of memory."""
    return digests_of(path).sha256


def digests_of(path: Path) -> Digests:
    """The sha256, md5 and CRC32C of a file, from one read in chunks."""
    sha256, md5, crc32c = hashlib.sha256(), hashlib.md5(usedforsecurity=False), google_crc32c.Checksum()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            sha256.update(chunk)
            md5.update(chunk)
            crc32c.update(chunk)
    return Digests(sha256.hexdigest(), md5.hexdigest(), crc32c.hexdigest().decode("ascii"))


def recorded(entry: BundleFile, found: Digests) -> bool:
    """Whether `found` are the digests `entry` records; a format-2.0 entry has no CRC32C to compare."""
    return (found.sha256, found.md5) == (entry.sha256, entry.md5) and entry.crc32c in {None, found.crc32c}


#: The publication files a server needs before it is ready (design §7.1).
_TIER_1 = frozenset({"meta.duckdb", "lake.ducklake"})


def tier_of(path: str, root: str, tiers: Mapping[str, Tier]) -> Tier:
    """A file's placement tier: the longest prefix of `path` in `tiers`, else the default.

    The default is 1 for ``meta.duckdb`` and ``lake.ducklake``, 3 for a data
    file, and 2 for any other publication file, such as a search index.

    Args:
        path: The file's ``/``-separated path under its root.
        root: ``publication`` or ``data``.
        tiers: Overrides, by path prefix, for publication files.

    Returns:
        1, 2 or 3.
    """
    if root == "data":
        return 3
    matches = [prefix for prefix in tiers if path.startswith(prefix)]
    if matches:
        return tiers[max(matches, key=len)]
    return 1 if path in _TIER_1 else 2


def inventory(root: Path, tiers: Mapping[str, Tier] | None = None) -> tuple[BundleFile, ...]:
    """Every file in the publication directory `root` but the manifest, sorted by ``/``-separated path.

    Args:
        root: The publication directory.
        tiers: Placement tier overrides by path prefix, as `write_manifest`.

    Returns:
        Each file's path from the root, size, digests and tier.
    """
    files = sorted(p for p in root.rglob("*") if p.is_file() and p.relative_to(root).as_posix() not in UNLISTED)
    entries: list[BundleFile] = []
    for p in files:
        path = p.relative_to(root).as_posix()
        found = digests_of(p)
        tier = tier_of(path, "publication", tiers or {})
        entries.append(
            BundleFile(
                path=path,
                size=p.stat().st_size,
                sha256=found.sha256,
                md5=found.md5,
                crc32c=found.crc32c,
                root="publication",
                tier=tier,
            )
        )
    return tuple(entries)


def lake_files(conn: duckdb.DuckDBPyConnection, data: Path | str, *, lake: str = "lake") -> list[str]:
    """Every data file the attached lake catalog references, as ``/``-separated paths under `data`, sorted.

    Read from DuckLake's metadata tables, so it covers every snapshot the
    catalog keeps, the current one and every one time travel can still reach:
    what garbage collection must keep while this publication lives.

    Args:
        conn: A connection with the publication's lake attached as `lake`.
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
    data: Path, paths: list[str], carried: Mapping[str, BundleFile], token: str | None
) -> list[BundleFile]:
    """The data files at `paths`, their digests carried from `carried` where the size still matches.

    An entry from a format-2.0 manifest records no CRC32C, so its file is
    hashed once more to gain one.
    """
    files: list[BundleFile] = []
    for path in paths:
        size = (data / path).stat().st_size
        earlier = carried.get(path)
        if earlier is not None and earlier.size == size and earlier.crc32c is not None:
            files.append(
                BundleFile(
                    path=path,
                    size=size,
                    sha256=earlier.sha256,
                    md5=earlier.md5,
                    crc32c=earlier.crc32c,
                    root="data",
                    tier=3,
                    carried_from=token,
                )
            )
        else:
            found = digests_of(data / path)
            files.append(
                BundleFile(
                    path=path,
                    size=size,
                    sha256=found.sha256,
                    md5=found.md5,
                    crc32c=found.crc32c,
                    root="data",
                    tier=3,
                )
            )
    return files


def _ducklake_versions(conn: duckdb.DuckDBPyConnection) -> tuple[str | None, str | None]:
    """The DuckLake extension's version, and the format the attached ``lake`` catalog records."""
    (extension,) = conn.execute(
        "SELECT extension_version FROM duckdb_extensions() WHERE extension_name = 'ducklake'"
    ).fetchone() or (None,)
    (spec,) = conn.execute(
        "SELECT value FROM __ducklake_metadata_lake.ducklake_metadata WHERE key = 'version'"
    ).fetchone() or (None,)
    return extension, spec


def artefact_entries(reg: BundleRegistry) -> tuple[ArtefactEntry, ...]:
    """Every maintainable artefact in the store but the bindings, by URN, each fingerprinted.

    Read one at a time and dropped, so memory holds one artefact: on the
    census bundle the largest, a 499,138-node hierarchy, sets the peak.
    """
    rows = reg.connection.execute(
        "SELECT urn, is_final, is_partial FROM sdmx.artefact WHERE artefact_type <> 'DataflowBinding' ORDER BY urn"
    ).fetchall()
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
            )
        )
    return tuple(entries)


#: Larger than any data file, so a merge leaves one file per table.
_ONE_FILE = 1 << 62


def settle_lake(
    conn: duckdb.DuckDBPyConnection,
    bindings: Iterable[DataflowBinding],
    *,
    expire_snapshots: bool,
    lifecycle: Lifecycle,
) -> None:
    """Bring the attached ``lake`` into the shape a publication ships (design §4.5).

    - Inlined rows are flushed into data files, so the catalog holds no rows.
    - Every table holding delete files is rewritten into new data files
      (``delete_threshold => 0``: DuckLake's default skips a table whose
      deleted rows are few, which is exactly a daily delta), so no delete file
      ships and every publication reads its rows from data files alone.
    - A ``final`` publication is compacted to one data file per table, each
      bound table sorted by its binding's ``sort_order``, else its dimension
      columns in key order, unless the producer set a sort already.
    - With `expire_snapshots`, every snapshot but the current one is expired,
      so the catalog and the manifest hold only current files.

    Nothing is deleted from ``data/``: a file this publication stops
    referencing may be another's, and only garbage collection, reading every
    manifest, deletes.

    Args:
        conn: A connection with the publication's lake attached as ``lake``.
        bindings: The publication's bindings.
        expire_snapshots: Whether to expire every snapshot but the current one.
        lifecycle: ``final`` to compact; ``live`` otherwise.
    """
    conn.execute("CALL ducklake_flush_inlined_data('lake')")
    conn.execute("CALL ducklake_rewrite_data_files('lake', delete_threshold => 0)")
    if lifecycle == "final":
        for binding in bindings:
            if binding.database_role == "lake":
                _sort(conn, binding)
        conn.execute("CALL ducklake_merge_adjacent_files('lake', max_file_size => ?)", [_ONE_FILE])
    if expire_snapshots:
        conn.execute("CALL ducklake_expire_snapshots('lake', older_than => now())")


def _sort(conn: duckdb.DuckDBPyConnection, binding: DataflowBinding) -> None:
    """Sort a bound table by its binding's key, unless the producer sorted it already."""
    schema, table = binding.table_location.split(".")
    (sorted_already,) = conn.execute(
        """
        SELECT count(*) > 0
        FROM __ducklake_metadata_lake.ducklake_sort_info AS i
        JOIN __ducklake_metadata_lake.ducklake_table AS t ON t.table_id = i.table_id AND t.end_snapshot IS NULL
        JOIN __ducklake_metadata_lake.ducklake_schema AS s ON s.schema_id = t.schema_id AND s.end_snapshot IS NULL
        WHERE i.end_snapshot IS NULL AND s.schema_name = ? AND t.table_name = ?
        """,
        [schema, table],
    ).fetchone() or (False,)
    if sorted_already:
        return
    columns = [binding.dimension_column_map.get(c, c) for c in binding.sort_order] or list(
        binding.dimension_column_map.values()
    )
    if columns:
        relation = f"lake.{_quoted(schema)}.{_quoted(table)}"
        conn.execute(f"ALTER TABLE {relation} SET SORTED BY ({', '.join(map(_quoted, columns))})")


def _quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def binding_counts(reg: BundleRegistry, artefacts: tuple[ArtefactEntry, ...]) -> tuple[BindingCount, ...]:
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


def write_manifest(
    path: str | Path,
    *,
    bundle_id: str,
    producer: Producer,
    product: dict[str, object] | None = None,
    extensions: dict[str, dict[str, object]] | None = None,
    provenance: Provenance | None = None,
    tiers: Mapping[str, Tier] | None = None,
    lifecycle: Lifecycle = "live",
    expire_snapshots: bool = False,
) -> Manifest:
    """Publish the publication at `path`, which `LocalRegistry.build` started: write its manifest.

    Write it **last**, once every builder has closed: the inventory is the
    files as they are now, and a directory without a manifest is unpublished.
    A publication is published once. A change is a new publication,
    ``LocalRegistry.build(bundle, previous=token)``, with a new token, which
    is what tells every cache keyed on the old one to refresh.

    The files are the publication directory's, and every data file its lake
    catalog references in the shared ``data/``. A data file the previous
    publication's manifest lists at the same size keeps that manifest's sha256,
    marked with its token in ``carried_from``, so a republish hashes only the
    files it wrote. Observation counts come from the lake, through each
    binding's observation query, so DuckLake answers them from its catalog.
    The manifest goes in through a temporary file and a rename, so a reader
    never sees half of one.

    Args:
        path: The publication directory, ``builder.publication``.
        bundle_id: The producer's stable name for the bundle, kept across rebuilds.
        producer: What built it.
        product: Producer-specific fields, carried and not interpreted.
        extensions: Sections owned by other packages, by name.
        provenance: Where a fetched bundle came from; `fetch_bundle` supplies it.
        tiers: Placement tiers for publication files, by path prefix, the
            longest matching prefix winning: ``{"search/": 2}``. A file no
            prefix matches is tier 1 for ``meta.duckdb`` and ``lake.ducklake``
            and 2 otherwise; a data file is always 3.
        lifecycle: ``final`` for a publication whose observations will not be
            revised, which compacts each table to one sorted data file;
            ``live`` otherwise.
        expire_snapshots: Whether to expire every snapshot but the current
            one, so the manifest lists only the files it reads. Time travel
            to an earlier state is lost.

    Returns:
        The manifest written.

    Raises:
        ManifestError: If the publication is already published, was not
            started by `LocalRegistry.build`, or a builder still holds it open.
    """
    root = Path(path)
    data = root / DATA_PATH
    if (root / MANIFEST_NAME).exists():
        msg = (
            f"{root} is already published, and a publication is never rewritten. "
            f"Start a new one with LocalRegistry.build({str(root.parent)!r}, previous={root.name!r})."
        )
        raise ManifestError(msg)
    with LocalRegistry._over_bundle(root, data).open() as reg:
        token = meta_value(reg.connection, "publication_token")
        if token is None:
            msg = f"{root} was not started by LocalRegistry.build, so it has no token to publish under"
            raise ManifestError(msg)
        previous = meta_value(reg.connection, "previous_token")
        schema_version = get_schema_version(reg.connection) or ""
        artefacts = artefact_entries(reg)
        referenced: list[str] = []
        lake = (root / "lake.ducklake").exists()
        if lake:
            reg._data_plane()  # ATTACHes the lake
            settle_lake(
                reg.connection,
                reg.get_all(DataflowBinding),
                expire_snapshots=expire_snapshots,
                lifecycle=lifecycle,
            )
            referenced = lake_files(reg.connection, data)
        ducklake, spec = _ducklake_versions(reg.connection) if lake else (None, None)
        counts = binding_counts(reg, artefacts)
    # Closing the last connection checkpoints a database, so a log left by a
    # builder that exited is gone by now. One that remains means another
    # connection still holds the database for writing, and its log holds
    # committed rows the database file's sha256 would not cover (GH-122).
    logs = sorted(p.relative_to(root).as_posix() for p in root.rglob("*.wal"))
    if logs:
        msg = (
            f"{root} still has a write-ahead log ({', '.join(logs)}): a connection holds one of its "
            f"databases open for writing. Close every builder before publishing."
        )
        raise ManifestError(msg)
    # Published, the store is never written: its keys enforce nothing, and
    # the ones the reads do not use leave it, in a fresh file (GH-165).
    compact_for_publication(root / "meta.duckdb")
    manifest = Manifest(
        format_version=FORMAT_VERSION,
        bundle_id=bundle_id,
        token=token,
        previous_token=previous,
        created_at=datetime.now(UTC),
        producer=producer,
        sdmxlib_version=metadata.version("sdmxlib"),
        schema_version=schema_version,
        duckdb_version=duckdb.__version__,
        duckdb_storage_version=publication_storage_version(root),
        ducklake_version=ducklake,
        ducklake_spec_version=spec,
        data_path=DATA_PATH,
        files=(*_data_inventory(data, referenced, _carried(root, previous), previous), *inventory(root, tiers)),
        bindings=counts,
        artefacts=artefacts,
        canonical_form=CANONICAL_FORM,
        content_fingerprint=bundle_fingerprint(
            {a.urn: a.fingerprint for a in artefacts}, {b.dataflow: b.fingerprint for b in counts}
        ),
        product=product or {},
        extensions=extensions or {},
        provenance=provenance,
    )
    staged = root / f"{MANIFEST_NAME}.tmp"
    staged.write_bytes(encode_manifest(manifest))
    staged.replace(root / MANIFEST_NAME)
    return manifest


def _carried(root: Path, previous: str | None) -> dict[str, BundleFile]:
    """The data files the previous publication's manifest lists, by path; empty if it is not beside `root`."""
    if previous is None:
        return {}
    try:
        earlier = read_manifest(root.parent / previous)
    except ManifestError:
        return {}
    return {f.path: f for f in earlier.files if f.root == "data"}
