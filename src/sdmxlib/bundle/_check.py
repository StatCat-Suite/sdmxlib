"""Checking a published bundle against its manifest and against itself (GH-67).

Run once, by whoever builds or receives a bundle — never on the path that
opens one to serve it, which reads only the manifest. Each check names what it
found rather than stopping at the first, so one run lists everything wrong.
"""

from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

import duckdb
from attrs import frozen

from sdmxlib.bundle._write import sha256_of
from sdmxlib.local.registry import LocalRegistry
from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.binding import DataflowBinding
from sdmxlib.model.metadataset import MetadataSet
from sdmxlib.model.validation import reference_issues
from sdmxlib.storage._manifest import MANIFEST_NAME, Manifest, ManifestError, read_manifest
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
type Check = Callable[[Path, Manifest, LocalRegistry], Iterable[Finding]]


def check_bundle(path: str | Path, *, references: bool = True, extensions: Mapping[str, Check] | None = None) -> Report:
    """Check a published bundle: its manifest, files, lake, bindings and references.

    - **manifest** — it parses, in a format this build reads.
    - **files** — every file the manifest lists is present with its size and
      sha256, and nothing else is in the directory.
    - **lake** — every file DuckLake references exists at the size it recorded,
      and DuckLake reports no orphan in its directories.
    - **bindings** — the store's dataflow bindings are the manifest's, and each
      holds the observations the manifest counts.
    - **references** — every reference in the store resolves within it: the
      structural closure is complete. Loads every artefact, so it is the costly
      one (about 30 s and 5 GB on a 2.6 GB census bundle); ``references=False``
      skips it.

    A manifest that cannot be read, or a store on another schema version, ends
    the run there: nothing after it can be checked against either.

    Args:
        path: The bundle directory.
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
    except (StoreSchemaError, ManifestError) as exc:
        return Report(root, tuple(checked), (*findings, Finding("manifest", "meta.duckdb", str(exc))))
    with reg:
        if (root / "lake.ducklake").exists():
            findings += _lake(root, reg)
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
        if p.is_file() and p.name not in {MANIFEST_NAME, f"{MANIFEST_NAME}.tmp"}
    }
    for entry in manifest.files:
        found = on_disk.pop(entry.path, None)
        if found is None:
            yield Finding("files", entry.path, "listed in the manifest but missing")
        elif (size := found.stat().st_size) != entry.size:
            yield Finding("files", entry.path, f"{size} bytes, the manifest says {entry.size}")
        elif sha256_of(found) != entry.sha256:
            yield Finding("files", entry.path, "contents differ from the manifest's sha256")
    for extra in on_disk:
        yield Finding("files", extra, "in the bundle but not in the manifest")


def _lake(root: Path, reg: LocalRegistry) -> list[Finding]:
    """What DuckLake records against what is on disk, asked through its own functions."""
    reg._data_plane()  # ATTACHes the lake
    conn = reg.connection
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
                    findings += _lake_file(root, Path(file), size)
    orphans = conn.execute(
        "SELECT path FROM ducklake_delete_orphaned_files(?, dry_run => true, cleanup_all => true)", [_LAKE]
    ).fetchall()
    findings += [
        Finding("lake", _relative(root, Path(path)), "not referenced by the DuckLake catalog") for (path,) in orphans
    ]
    return findings


def _lake_file(root: Path, file: Path, size: int) -> list[Finding]:
    if not file.is_file():
        return [Finding("lake", _relative(root, file), "referenced by the DuckLake catalog but missing")]
    if (actual := file.stat().st_size) != size:
        return [Finding("lake", _relative(root, file), f"{actual} bytes, DuckLake recorded {size}")]
    return []


def _relative(root: Path, file: Path) -> str:
    try:
        return file.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(file)


def _bindings(manifest: Manifest, reg: LocalRegistry) -> list[Finding]:
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


def _references(reg: LocalRegistry) -> list[Finding]:
    loaded = [a for a in reg.to_registry()._store.values() if isinstance(a, MaintainableArtefact)]
    structural = [a for a in loaded if not isinstance(a, MetadataSet)]
    metadata = [a for a in loaded if isinstance(a, MetadataSet)]
    return [
        Finding("references", issue.source, f"refers to {issue.target_urn}, which is not in the bundle")
        for issue in reference_issues(structural, also=metadata)
    ]
