"""The routing fields of a leaf entry, computed from a manifest and read back for a router (GH-348).

The sharded catalog spec's §6.3 defines them: ``dataflows`` and ``pull_bytes``,
both or neither, and ``artefacts``, which a writer fills in every structure
entry. §7.3 routes from them:

- a ``/data`` request's dataflow to the entry whose ``dataflows`` holds it;
- a ``/structure`` request's artefact to the structure entry whose
  ``artefacts`` holds it without ``partial_of``, its structure home;
- an entry without the fields to what its manifest gives, read after its
  ``manifest_sha256`` verifies.

`routing_fields` computes the three from a manifest for a writer to pass to
`record` and `reinstate`. `route` gives a router one live edition's routes,
reading the manifest only for a field the entry lacks. `verify_entry` is
``verify-catalog``'s check that the filled fields equal the manifest's
(spec §10.3). The router itself, ``ShardRouter``, is statcat-serve's.
"""

from collections.abc import Callable
from pathlib import Path

import msgspec
from msgspec import UnsetType

from sdmxlib.inventory._format import InventoryError, LeafEntry, PartialRecord, sha256_hex
from sdmxlib.inventory._reader import LiveEdition
from sdmxlib.storage._manifest import MANIFEST_NAME, Manifest, decode_manifest


class RoutingFields(msgspec.Struct, frozen=True, kw_only=True):
    """What a writer records in a leaf entry from its manifest (spec §6.3).

    Attributes:
        dataflows: Each ``bindings[].dataflow``, sorted and without
            duplicates; empty for a structure edition.
        pull_bytes: The sum of ``size`` over the ``files`` whose ``tier`` is
            ``required``.
        artefacts: The artefact records: every full artefact with its
            fingerprint, and every partial with its fingerprint and
            ``partial_of``. A partial without ``partial_of`` has no record.
    """

    dataflows: tuple[str, ...]
    pull_bytes: int
    artefacts: dict[str, PartialRecord]


class Route(msgspec.Struct, frozen=True, kw_only=True):
    """One live edition's routes, as a router maps them (spec §7.3).

    Attributes:
        dataflows: The dataflow URNs ``ShardRouter.shard_for`` maps to the edition.
        pull_bytes: The size of the required tier the router pulls before
            attaching the edition.
        homes: The artefact URNs ``ShardRouter.shard_for_artefact`` maps to the
            edition: a structure edition's full artefacts, sorted; empty for a
            data edition.
    """

    dataflows: tuple[str, ...]
    pull_bytes: int
    homes: tuple[str, ...]


def manifest_records(manifest: Manifest) -> dict[str, PartialRecord]:
    """The manifest's artefact records, by URN, as a leaf entry's ``artefacts`` holds them."""
    found: dict[str, PartialRecord] = {}
    for artefact in manifest.artefacts:
        if not artefact.partial:
            found[artefact.urn] = PartialRecord(fingerprint=artefact.fingerprint)
        elif artefact.partial_of is not None:
            found[artefact.urn] = PartialRecord(fingerprint=artefact.fingerprint, partial_of=artefact.partial_of)
    return found


def routing_fields(manifest: Manifest) -> RoutingFields:
    """The routing fields and artefact records of the edition `manifest` describes (spec §6.3)."""
    return RoutingFields(
        dataflows=tuple(sorted({b.dataflow for b in manifest.bindings})),
        pull_bytes=sum(f.size for f in manifest.files if f.tier == "required"),
        artefacts=manifest_records(manifest),
    )


def _homes(records: dict[str, PartialRecord]) -> tuple[str, ...]:
    return tuple(sorted(urn for urn, record in records.items() if record.partial_of is None))


def route(edition: LiveEdition, manifest: Callable[[], bytes]) -> Route:
    """The routes of a live edition, from its leaf entry, or its manifest for a field the entry lacks.

    Args:
        edition: The live edition, as `LiveInventory` gives it.
        manifest: Fetches the bytes of the edition's ``manifest.json``. It is
            called only when the entry lacks the routing fields, or is a
            structure entry without ``artefacts``.

    Returns:
        The edition's routes.

    Raises:
        InventoryError: If the manifest's SHA-256 is not the entry's
            ``manifest_sha256``.
        ManifestError: If the manifest is malformed.
    """
    structure = edition.kind == "structure"
    dataflows, pull_bytes, records = edition.dataflows, edition.pull_bytes, edition.artefacts
    if dataflows is None or pull_bytes is None or (structure and records is None):
        fields = _read(edition, manifest())
        if dataflows is None or pull_bytes is None:
            dataflows, pull_bytes = fields.dataflows, fields.pull_bytes
        if records is None:
            records = fields.artefacts
    homes = _homes(records) if structure and records is not None else ()
    return Route(dataflows=dataflows, pull_bytes=pull_bytes, homes=homes)


def _read(edition: LiveEdition, raw: bytes) -> RoutingFields:
    """The routing fields of the manifest `raw`, once its SHA-256 is the entry's."""
    if sha256_hex(raw) != edition.manifest_sha256:
        msg = (
            f"{edition.product_id}'s {edition.edition_id}: the manifest's SHA-256 is not the "
            f"{edition.manifest_sha256} its leaf entry records"
        )
        raise InventoryError(msg)
    path = Path("products", edition.product_id, edition.edition_id, MANIFEST_NAME)
    return routing_fields(decode_manifest(raw, path))


def verify_entry(entry: LeafEntry, manifest: Manifest) -> list[str]:
    """Each filled field of `entry` that differs from what `manifest` gives: ``verify-catalog``'s check (spec §10.3).

    The caller has verified that `manifest` is the one ``manifest_sha256``
    names. An entry without a field has nothing to check for it.

    Returns:
        One message per differing field, naming it; empty when all match.
    """
    expected = routing_fields(manifest)
    where = f"{manifest.product_id}'s {entry.edition_id}"
    found: list[str] = []
    if entry.artefacts is not None and entry.artefacts != expected.artefacts:
        found.append(f"{where}: artefacts differ from its manifest's records")
    if not isinstance(entry.dataflows, UnsetType) and entry.dataflows != expected.dataflows:
        found.append(f"{where}: dataflows {list(entry.dataflows)} are not its manifest's {list(expected.dataflows)}")
    if not isinstance(entry.pull_bytes, UnsetType) and entry.pull_bytes != expected.pull_bytes:
        found.append(f"{where}: pull_bytes {entry.pull_bytes} is not its manifest's {expected.pull_bytes}")
    return found
