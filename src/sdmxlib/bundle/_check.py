"""Checking a published bundle against its manifest and against itself (GH-67).

Run once, by whoever builds or receives a bundle — never on the path that
opens one to serve it, which reads only the manifest. Each check names what it
found rather than stopping at the first, so one run lists everything wrong.
"""

from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

import duckdb
from attrs import frozen

from sdmxlib.bundle._write import digests_of, lake_files
from sdmxlib.local.registry import BundleRegistry, LocalRegistry
from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.binding import DataflowBinding
from sdmxlib.model.metadataset import MetadataSet
from sdmxlib.model.validation import reference_issues
from sdmxlib.storage._manifest import (
    MANIFEST_NAME,
    UNLISTED,
    Manifest,
    ManifestError,
    StoreFormatError,
    read_manifest,
)
from sdmxlib.storage.schema import StoreSchemaError

#: The ATTACH alias `LocalRegistry.bundle` gives the bundle's DuckLake catalog.
_LAKE = "lake"


@frozen
class Finding:
    """One thing a check found wrong.

    Attributes:
        check: Which check: ``manifest``, ``files``, ``lake``, ``bindings``,
            ``references``, or an extension's name.
        subject: What it is about: a path from the bundle root, a URN, or the
            artefact a reference sits in.
        detail: What is wrong with it.
    """

    check: str
    subject: str
    detail: str

    def __str__(self) -> str:
        """``[check] subject: detail``."""
        return f"[{self.check}] {self.subject}: {self.detail}"


@frozen
class Report:
    """What `check_bundle` checked, and what it found.

    Attributes:
        path: The bundle checked.
        checked: The checks that ran, in order — a skipped or unreachable one
            is absent, so a clean report says how much it covered.
        findings: Everything found wrong, by check.
    """

    path: Path
    checked: tuple[str, ...]
    findings: tuple[Finding, ...]

    @property
    def ok(self) -> bool:
        """Whether nothing was found wrong."""
        return not self.findings


#: An extension's check: given the bundle root, its manifest and an open
#: registry over it, what is wrong with the extension's part of it.
type Check = Callable[[Path, Manifest, BundleRegistry], Iterable[Finding]]


def check_bundle(path: str | Path, *, references: bool = True, extensions: Mapping[str, Check] | None = None) -> Report:
    """Check a published bundle: its manifest, files, lake, bindings and references.

    - **manifest** — it parses, in a format this build reads.
    - **files** — every file the manifest lists is present with its size,
      sha256 and md5, and nothing else is in the publication directory. The shared
      ``data/`` holds other publications' files too, so only its listed
      files are checked.
    - **lake** — every file the current snapshot references exists at the
      size DuckLake recorded, and every file the catalog references from any
      snapshot is in the manifest, which is what garbage collection keeps.
    - **bindings** — the store's dataflow bindings are the manifest's, and each
      holds the observations the manifest counts.
    - **references** — every reference in the store resolves within it: the
      structural closure is complete. Loads every artefact, so it is the costly
      one (about 30 s and 5 GB on a 2.6 GB census bundle); ``references=False``
      skips it.

    A manifest that cannot be read, or a store on another schema version or in
    a DuckDB or DuckLake format this engine cannot read, ends the run there:
    nothing after it can be checked against either.

    Args:
        path: The publication directory.
        references: Whether to check the structural closure.
        extensions: More checks, by name, for parts of the bundle other
            packages own (statcatlib's search index, say).

    Returns:
        What was checked and what was found.
    """
    root = Path(path)
    checked: list[str] = ["manifest"]
    try:
        manifest = read_manifest(root)
    except ManifestError as exc:
        return Report(root, tuple(checked), (Finding("manifest", MANIFEST_NAME, str(exc)),))
    findings = list(_files(root, manifest))
    checked.append("files")
    try:
        reg = LocalRegistry.bundle(root)
    except (StoreSchemaError, StoreFormatError, ManifestError) as exc:
        return Report(root, tuple(checked), (*findings, Finding("manifest", "meta.duckdb", str(exc))))
    with reg:
        if (root / "lake.ducklake").exists():
            findings += _lake(root, manifest, reg)
            checked.append("lake")
        findings += _bindings(manifest, reg)
        checked.append("bindings")
        if references:
            findings += _references(reg)
            checked.append("references")
        for name, check in (extensions or {}).items():
            findings += check(root, manifest, reg)
            checked.append(name)
    return Report(root, tuple(checked), tuple(findings))


def _files(root: Path, manifest: Manifest) -> Iterable[Finding]:
    on_disk = {
        p.relative_to(root).as_posix(): p
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.relative_to(root).as_posix() not in UNLISTED
    }
    data = root / manifest.data_path
    for entry in manifest.files:
        if entry.root == "data":
            subject = f"{manifest.data_path}{entry.path}"
            found = data / entry.path if (data / entry.path).is_file() else None
        else:
            subject = entry.path
            found = on_disk.pop(entry.path, None)
        if found is None:
            yield Finding("files", subject, "listed in the manifest but missing")
        elif (size := found.stat().st_size) != entry.size:
            yield Finding("files", subject, f"{size} bytes, the manifest says {entry.size}")
        elif digests_of(found) != (entry.sha256, entry.md5):
            yield Finding("files", subject, "contents differ from the manifest's sha256 or md5")
    for extra in on_disk:
        if extra.endswith(".wal"):
            # A database was not checkpointed before the manifest: its log holds
            # committed rows the database file's sha256 does not cover (GH-122).
            yield Finding("files", extra, "a write-ahead log: its database was not checkpointed before publishing")
        else:
            yield Finding("files", extra, "in the bundle but not in the manifest")


def _lake(root: Path, manifest: Manifest, reg: BundleRegistry) -> list[Finding]:
    """What DuckLake records against what is on disk and in the manifest."""
    reg._data_plane()  # ATTACHes the lake
    conn = reg.connection
    data = root / manifest.data_path
    findings: list[Finding] = []
    tables = conn.execute(
        "SELECT schema_name, table_name FROM duckdb_tables() WHERE database_name = ? ORDER BY ALL", [_LAKE]
    ).fetchall()
    for schema, table in tables:
        listed = conn.execute(
            "SELECT data_file, data_file_size_bytes, delete_file, delete_file_size_bytes "
            "FROM ducklake_list_files(?, ?, schema => ?)",
            [_LAKE, table, schema],
        ).fetchall()
        for data_file, data_size, delete_file, delete_size in listed:
            for file, size in ((data_file, data_size), (delete_file, delete_size)):
                if file is not None:
                    findings += _lake_file(manifest.data_path, data, Path(file), size)
    listed = {f.path for f in manifest.files if f.root == "data"}
    try:
        referenced = lake_files(conn, data)
    except ManifestError as exc:
        return [*findings, Finding("lake", "lake.ducklake", str(exc))]
    findings += [
        Finding("lake", f"{manifest.data_path}{path}", "referenced by the DuckLake catalog but not in the manifest")
        for path in referenced
        if path not in listed
    ]
    return findings


def _lake_file(data_path: str, data: Path, file: Path, size: int) -> list[Finding]:
    subject = _relative(data_path, data, file)
    if not file.is_file():
        return [Finding("lake", subject, "referenced by the DuckLake catalog but missing")]
    if (actual := file.stat().st_size) != size:
        return [Finding("lake", subject, f"{actual} bytes, DuckLake recorded {size}")]
    return []


def _relative(data_path: str, data: Path, file: Path) -> str:
    """`file` as the manifest's ``files`` name it: under `data_path` when it is in `data`."""
    try:
        return f"{data_path}{file.resolve().relative_to(data.resolve()).as_posix()}"
    except ValueError:
        return str(file)


def _bindings(manifest: Manifest, reg: BundleRegistry) -> list[Finding]:
    stored = {str(b.dataflow.urn): b for b in reg.get_all(DataflowBinding)}
    declared = {b.dataflow: b.observations for b in manifest.bindings}
    findings = [
        Finding("bindings", urn, "bound in the store but not in the manifest")
        for urn in sorted(stored.keys() - declared.keys())
    ]
    findings += [
        Finding("bindings", urn, "in the manifest but not bound in the store")
        for urn in sorted(declared.keys() - stored.keys())
    ]
    for urn in sorted(stored.keys() & declared.keys()):
        try:
            count = reg.obs(stored[urn].dataflow).count()
        except (duckdb.Error, ValueError) as exc:
            findings.append(Finding("bindings", urn, f"its observations cannot be read: {exc}"))
            continue
        if count != declared[urn]:
            findings.append(Finding("bindings", urn, f"{count} observations, the manifest says {declared[urn]}"))
    return findings


def _references(reg: BundleRegistry) -> list[Finding]:
    loaded = [a for a in reg.to_registry()._store.values() if isinstance(a, MaintainableArtefact)]
    structural = [a for a in loaded if not isinstance(a, MetadataSet)]
    metadata = [a for a in loaded if isinstance(a, MetadataSet)]
    return [
        Finding("references", issue.source, f"refers to {issue.target_urn}, which is not in the bundle")
        for issue in reference_issues(structural, also=metadata)
    ]
