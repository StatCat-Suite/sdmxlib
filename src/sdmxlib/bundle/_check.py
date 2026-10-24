"""Checking a published bundle against its manifest and against itself (GH-67).

Three profiles, by where the check runs (GH-130). ``publish`` runs where the
bundle is built and hashes everything. ``receive`` runs where it lands: data
files by size, and with a blob-store lookup by ``Content-MD5``, so no data
file is hashed. ``open`` runs each time a server opens it, checking sizes
only. `check_on_open` runs ``receive`` once per token and manifest, records
a stamp, and runs ``open`` after that. Each check names what it found rather
than stopping at the first, so one run lists everything wrong.
"""

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Literal

import attrs
import duckdb
import msgspec
from attrs import frozen

from sdmxlib.bundle._finding import Finding
from sdmxlib.bundle._fingerprint import bundle_fingerprint
from sdmxlib.bundle._remote import RemoteFile, verify_remote
from sdmxlib.bundle._write import artefact_entries, binding_counts, digests_of, lake_files
from sdmxlib.local.registry import BundleRegistry, LocalRegistry
from sdmxlib.model.binding import DataflowBinding
from sdmxlib.storage._manifest import (
    MANIFEST_NAME,
    UNLISTED,
    BundleFile,
    Manifest,
    ManifestError,
    StoreFormatError,
    read_manifest,
)
from sdmxlib.storage._references import unresolved_references
from sdmxlib.storage.schema import StoreSchemaError

#: The ATTACH alias `LocalRegistry.bundle` gives the bundle's DuckLake catalog.
_LAKE = "lake"

#: Where a check runs: where the bundle is built, where it lands, or where a
#: server opens it.
type Profile = Literal["publish", "receive", "open"]

#: An extension's check: given the bundle root, its manifest, an open registry
#: over it and the profile, what is wrong with the extension's part of it.
type Check = Callable[[Path, Manifest, BundleRegistry, Profile], Iterable[Finding]]

#: A blob store's report of one file, as `verify_remote` takes it.
type Lookup = Callable[[BundleFile], RemoteFile | None]


@frozen
class Report:
    """What `check_bundle` checked, and what it found.

    Attributes:
        path: The bundle checked.
        at: The profile that ran.
        checked: The checks that ran, in order — a skipped or unreachable one
            is absent, so a clean report says how much it covered.
        findings: Everything found wrong, by check.
    """

    path: Path
    at: Profile
    checked: tuple[str, ...]
    findings: tuple[Finding, ...]

    @property
    def ok(self) -> bool:
        """Whether nothing was found wrong."""
        return not self.findings


def check_bundle(
    path: str | Path,
    *,
    at: Profile,
    lookup: Lookup | None = None,
    fingerprints: bool = False,
    extensions: Mapping[str, Check] | None = None,
) -> Report:
    """Check a published bundle against its manifest and against itself, as profile `at` asks.

    | check | publish | receive | open |
    | --- | --- | --- | --- |
    | manifest: parses, in a format and versions this build reads | yes | yes | yes |
    | files, the publication's: size, sha256 and md5, nothing unlisted | yes | yes | size only |
    | files, ``data/``'s listed entries | size, sha256 and md5 | size | no |
    | remote: ``data/``'s entries in the blob store, by size and ``Content-MD5`` | no | with `lookup` | no |
    | lake: current files at their recorded size, no delete file, every referenced file listed | yes | yes | no |
    | bindings: the manifest's, each with the count it gives | yes | yes | no |
    | references: every stored reference names something in the store | yes | yes | no |
    | fingerprints: recomputed, equal to the manifest's | yes | with `fingerprints` | no |
    | extensions | yes | yes | yes, told the profile |

    ``data/`` is shared by publications, so only its listed entries are
    checked. At ``receive`` with a `lookup`, ``data/`` is checked in the blob
    store in place of on disk; the lake and the bindings still read the data
    where the registry reads it.

    A manifest that cannot be read, or a store on another schema version or in
    a DuckDB or DuckLake format this engine cannot read, ends the run there:
    nothing after it can be checked against either.

    Args:
        path: The publication directory.
        at: ``publish``, ``receive`` or ``open``.
        lookup: At ``receive``, what the blob store reports for a ``data/``
            entry, or None for a file it does not hold (see `verify_remote`).
        fingerprints: At ``receive``, also recompute the fingerprints, which
            ``publish`` always does.
        extensions: More checks, by name, for parts of the bundle other
            packages own (statcatlib's search index, say).

    Returns:
        What was checked and what was found.

    Raises:
        ValueError: If `lookup` or `fingerprints` is given at a profile that
            does not use it.
    """
    _refuse_unused(at, lookup=lookup, fingerprints=fingerprints)
    root = Path(path)
    checked: list[str] = ["manifest"]
    try:
        manifest = read_manifest(root)
    except ManifestError as exc:
        return Report(root, at, tuple(checked), (Finding("manifest", MANIFEST_NAME, str(exc)),))
    findings = list(_files(root, manifest, at, remote=lookup is not None))
    checked.append("files")
    if lookup is not None:
        data_only = msgspec.structs.replace(manifest, files=tuple(f for f in manifest.files if f.root == "data"))
        findings += verify_remote(data_only, lookup, tiers=(1, 2, 3))
        checked.append("remote")
    try:
        reg = LocalRegistry.bundle(root)
    except (StoreSchemaError, StoreFormatError, ManifestError) as exc:
        return Report(root, at, tuple(checked), (*findings, Finding("manifest", "meta.duckdb", str(exc))))
    except duckdb.Error as exc:
        return Report(root, at, tuple(checked), (*findings, Finding("files", "meta.duckdb", f"cannot be read: {exc}")))
    with reg:
        for name, step in _steps(root, manifest, reg, at, fingerprints=fingerprints, extensions=extensions or {}):
            try:
                findings += step()
            except duckdb.Error as exc:
                # A damaged database or catalog: the files check names which
                # file, and nothing after it reads anything else.
                findings.append(Finding(name, "meta.duckdb, lake.ducklake", f"the store cannot be read: {exc}"))
                checked.append(name)
                break
            checked.append(name)
    return Report(root, at, tuple(checked), tuple(findings))


def _refuse_unused(at: Profile, *, lookup: Lookup | None, fingerprints: bool) -> None:
    """Raise for an argument the profile `at` does not use."""
    if lookup is not None and at != "receive":
        msg = f"lookup= verifies data files at receive; at={at!r} does not use it"
        raise ValueError(msg)
    if fingerprints and at != "receive":
        msg = f"fingerprints= opts a receive check into recomputing them; at={at!r} " + (
            "always recomputes them" if at == "publish" else "never does"
        )
        raise ValueError(msg)


def _steps(
    root: Path,
    manifest: Manifest,
    reg: BundleRegistry,
    at: Profile,
    *,
    fingerprints: bool,
    extensions: Mapping[str, Check],
) -> list[tuple[str, Callable[[], Iterable[Finding]]]]:
    """The checks that read the store, by name, in the order profile `at` runs them."""
    steps: list[tuple[str, Callable[[], Iterable[Finding]]]] = []
    if at != "open":
        if (root / "lake.ducklake").exists():
            steps.append(("lake", partial(_lake, root, manifest, reg)))
        steps += [("bindings", partial(_bindings, manifest, reg)), ("references", partial(_references, reg))]
    if at == "publish" or fingerprints:
        steps.append(("fingerprints", partial(_fingerprints, root, manifest)))
    return steps + [(name, partial(check, root, manifest, reg, at)) for name, check in extensions.items()]


def check_on_open(
    path: str | Path,
    *,
    stamps: str | Path,
    lookup: Lookup | None = None,
    extensions: Mapping[str, Check] | None = None,
) -> Report:
    """Check a bundle as a server opens it: ``receive`` once per token and manifest, ``open`` after.

    The stamp is ``<stamps>/<bundle_id>/<token>``, a JSON file holding the
    manifest's sha256 and what the receive check covered, written once that
    check finds nothing. With a stamp for this manifest the ``open`` profile
    runs, which hashes nothing. With none, or a stamp for another manifest
    under the same token, the ``receive`` profile runs first.

    Args:
        path: The publication directory.
        stamps: The directory holding the stamps, outside every bundle: local
            to the server, since a stamp says what this machine checked.
        lookup: As `check_bundle`'s, for the receive check.
        extensions: As `check_bundle`'s, for either profile.

    Returns:
        The report of the profile that ran; its ``at`` says which.
    """
    root = Path(path)
    try:
        digest = hashlib.sha256((root / MANIFEST_NAME).read_bytes()).hexdigest()
        manifest = read_manifest(root)
    except OSError, ManifestError:
        return check_bundle(root, at="receive", lookup=lookup, extensions=extensions)
    stamp = Path(stamps) / manifest.bundle_id / manifest.token
    if not _names_a_file_under(stamp, Path(stamps)):
        report = check_bundle(root, at="receive", lookup=lookup, extensions=extensions)
        detail = f"bundle_id {manifest.bundle_id!r} cannot name a stamp directory, so the check is not stamped"
        return attrs.evolve(report, findings=(*report.findings, Finding("manifest", MANIFEST_NAME, detail)))
    if _stamped(stamp, digest):
        return check_bundle(root, at="open", extensions=extensions)
    report = check_bundle(root, at="receive", lookup=lookup, extensions=extensions)
    if report.ok:
        _write_stamp(stamp, digest, report)
    return report


def _names_a_file_under(stamp: Path, stamps: Path) -> bool:
    """Whether `stamp` is two plain names below `stamps`: no separator, no ``..``, not absolute."""
    try:
        parts = stamp.relative_to(stamps).parts
    except ValueError:
        return False
    return len(parts) == 2 and all(part not in {".", ".."} for part in parts)


def _stamped(stamp: Path, digest: str) -> bool:
    """Whether `stamp` records a clean receive check of the manifest with sha256 `digest`."""
    try:
        recorded = json.loads(stamp.read_text())
    except OSError, ValueError:
        return False
    return isinstance(recorded, dict) and recorded.get("manifest_sha256") == digest


def _write_stamp(stamp: Path, digest: str, report: Report) -> None:
    """Record `report` under `stamp`, whole or not at all: a rename puts it in place."""
    stamp.parent.mkdir(parents=True, exist_ok=True)
    partial = stamp.with_name(f".{stamp.name}.partial")
    partial.write_text(
        json.dumps(
            {
                "manifest_sha256": digest,
                "at": report.at,
                "checked": list(report.checked),
                "verified_at": datetime.now(UTC).isoformat(),
            },
            indent=2,
        )
    )
    partial.replace(stamp)


def _files(root: Path, manifest: Manifest, at: Profile, *, remote: bool) -> Iterable[Finding]:
    on_disk = {
        p.relative_to(root).as_posix(): p
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.relative_to(root).as_posix() not in UNLISTED
    }
    data = root / manifest.data_path
    for entry in manifest.files:
        if entry.root == "data":
            if at == "open" or remote:
                continue
            subject = f"{manifest.data_path}{entry.path}"
            found = data / entry.path if (data / entry.path).is_file() else None
            hashed = at == "publish"
        else:
            subject = entry.path
            found = on_disk.pop(entry.path, None)
            hashed = at != "open"
        if found is None:
            yield Finding("files", subject, "listed in the manifest but missing")
        elif (size := found.stat().st_size) != entry.size:
            yield Finding("files", subject, f"{size} bytes, the manifest says {entry.size}")
        elif hashed and digests_of(found) != (entry.sha256, entry.md5):
            yield Finding("files", subject, "contents differ from the manifest's sha256 or md5")
    if at == "open":
        return
    for extra in on_disk:
        if extra.endswith(".wal"):
            # A database was not checkpointed before the manifest: its log holds
            # committed rows the database file's sha256 does not cover (GH-122).
            yield Finding("files", extra, "a write-ahead log: its database was not checkpointed before publishing")
        else:
            yield Finding("files", extra, "in the bundle but not in the manifest")


def _fingerprints(root: Path, manifest: Manifest) -> list[Finding]:
    """The fingerprints recomputed, against the manifest's.

    On a registry of its own: one that has read the bindings resolves the
    artefact reads after it many times slower (GH-150).
    """
    with LocalRegistry.bundle(root) as reg:
        artefacts = artefact_entries(reg)
        try:
            counts = binding_counts(reg, artefacts)
        except duckdb.Error as exc:
            return [Finding("fingerprints", MANIFEST_NAME, f"the bindings' tables cannot be read: {exc}")]
    found = {a.urn: a.fingerprint for a in artefacts}
    findings = _artefact_fingerprints(found, {a.urn: a.fingerprint for a in manifest.artefacts})
    declared = {b.dataflow: b for b in manifest.bindings}
    for count in counts:
        binding = declared.get(count.dataflow)
        if binding is None:
            continue
        for name in ("fingerprint", "structure_fingerprint"):
            ours, theirs = getattr(count, name), getattr(binding, name)
            if ours != theirs:
                findings.append(Finding("fingerprints", count.dataflow, f"{name} {ours}, the manifest says {theirs}"))
    content = bundle_fingerprint(found, {b.dataflow: b.fingerprint for b in counts})
    if content != manifest.content_fingerprint:
        findings.append(
            Finding(
                "fingerprints",
                MANIFEST_NAME,
                f"content_fingerprint {content}, the manifest says {manifest.content_fingerprint}",
            )
        )
    return findings


def _artefact_fingerprints(found: Mapping[str, str], recorded: Mapping[str, str]) -> list[Finding]:
    """Each artefact whose recomputed fingerprint, `found`, is not the manifest's, `recorded`."""
    findings: list[Finding] = []
    for urn in sorted(recorded.keys() | found.keys()):
        if urn not in found:
            findings.append(Finding("fingerprints", urn, "in the manifest's artefacts but not in the store"))
        elif urn not in recorded:
            findings.append(Finding("fingerprints", urn, "in the store but not in the manifest's artefacts"))
        elif found[urn] != recorded[urn]:
            detail = f"fingerprint {found[urn]}, the manifest says {recorded[urn]}"
            findings.append(Finding("fingerprints", urn, detail))
    return findings


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
            if delete_file is not None:
                # Publishing rewrites every table holding one (GH-128), so the
                # current snapshot reads its rows from data files alone.
                findings.append(
                    Finding(
                        "lake",
                        _relative(manifest.data_path, data, Path(delete_file)),
                        "a delete file in the current snapshot; write_manifest rewrites the table without one",
                    )
                )
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
    return [
        Finding("references", u.source, f"its {u.role} reference {u.target} is not in the bundle")
        for u in unresolved_references(reg.connection)
    ]
