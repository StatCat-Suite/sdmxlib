"""The Inventory's documents: ``catalog.json``, ``CURRENT``, roots and leaves (GH-251).

The sharded catalog spec §6 defines them. A catalog's Inventory is a chain of
versions; each version is a root, ``inventory/<seq>.json``, naming one leaf
per product, ``inventory/leaves/<sha256>.json``, content-addressed by the
SHA-256 of its bytes. ``CURRENT`` names the newest root as a hint, and
``catalog.json`` holds the catalog's identity and its ``restore_epoch``.

sdmxlib owns these formats and their validation; statcatlib writes them. A
minor version only adds optional fields, which a reader ignores; a reader
refuses another major version.
"""

import hashlib
from datetime import datetime
from typing import Literal

import msgspec

from sdmxlib.storage._manifest import JsonValue, Producer

#: The Inventory format this build writes and reads (``1.x``): roots and leaves.
INVENTORY_FORMAT = "1.0"

#: The ``catalog.json`` format this build writes and reads (``1.x``).
CATALOG_FORMAT = "1.0"

#: Where each document lives, relative to the catalog root.
CATALOG_NAME = "catalog.json"
CURRENT_NAME = "CURRENT"
INVENTORY_DIR = "inventory"
LEAVES_DIR = "inventory/leaves"

#: An entry's state (spec §6.4). Retirement is a tombstone in the root, never a state.
type State = Literal["embargoed", "live", "superseded", "withdrawn"]

#: What an Inventory version records it was written for. The set is frozen in 1.0.
type EventKind = Literal["seal", "release", "rollback", "withdraw", "retire", "embargo-change", "restore", "correct"]

#: A product holding observations, or one holding only full structures (spec §8).
type Kind = Literal["data", "structure"]

#: Whether a product's observations may still be revised.
type Lifecycle = Literal["final", "revisable"]


class InventoryError(ValueError):
    """An Inventory document that is malformed, of a format this sdmxlib does not read, or breaks a rule."""


class CatalogDocument(msgspec.Struct, frozen=True, kw_only=True):
    """``catalog.json``: a catalog's identity, written when it is created.

    Attributes:
        format_version: `CATALOG_FORMAT`.
        catalog_id: A UUIDv7. An export keeps it.
        restore_epoch: Starts at 0; a whole-catalog restore or failover bumps
            it, and readers reset their ``seq`` floor when it changes.
    """

    format_version: str
    catalog_id: str
    restore_epoch: int = 0


class Current(msgspec.Struct, frozen=True, kw_only=True):
    """``CURRENT``: a hint naming the newest root, which a reader verifies and probes beyond.

    Attributes:
        seq: The newest root's sequence number, as last written.
        root_sha256: The SHA-256 of that root's bytes, lowercase hex.
        restore_epoch: ``catalog.json``'s, when this was written.
    """

    seq: int
    root_sha256: str
    restore_epoch: int = 0


class Event(msgspec.Struct, frozen=True, kw_only=True):
    """Who wrote an Inventory version, and why.

    Attributes:
        actor: The acting principal, from the writer's identity, never from a caller.
        reason: Why, in the actor's words.
        kind: What the version records.
    """

    actor: str
    reason: str
    kind: EventKind


class LeafRef(msgspec.Struct, frozen=True, kw_only=True):
    """A root's pointer to one product's leaf.

    Attributes:
        leaf: The leaf's path, ``inventory/leaves/<leaf_sha256>.json``.
        leaf_sha256: The SHA-256 of the leaf's bytes.
    """

    leaf: str
    leaf_sha256: str


class Tombstone(msgspec.Struct, frozen=True, kw_only=True):
    """A retired edition, kept so it stays distinct from one never recorded.

    Attributes:
        product_id: Its product.
        edition_id: The edition.
        retired_at: When it was retired.
    """

    product_id: str
    edition_id: str
    retired_at: datetime


class Root(msgspec.Struct, frozen=True, kw_only=True):
    """``inventory/<seq>.json``: one Inventory version.

    Attributes:
        format_version: `INVENTORY_FORMAT`.
        seq: This version's sequence number; the first is 1.
        previous_inventory: The ``seq`` this version was built from, or None
            for version 1. Usually ``seq - 1``; a correction builds from an
            older, known-good one.
        restore_epoch: ``catalog.json``'s, when written.
        created_at: When it was written, in UTC.
        producer: The library that wrote it.
        embargo: Whether a seal releases its edition in the same version
            (False) or records it embargoed (True).
        products: Each product's leaf, by ``product_id``.
        retired: Tombstones within the tombstone window, sorted by product
            then edition.
        event: Who acted, and why.
        extensions: Sections owned by other packages, by name.
    """

    format_version: str
    seq: int
    previous_inventory: int | None
    restore_epoch: int
    created_at: datetime
    producer: Producer
    embargo: bool
    products: dict[str, LeafRef]
    retired: tuple[Tombstone, ...]
    event: Event
    extensions: dict[str, dict[str, JsonValue]] = msgspec.field(default_factory=dict)


class StructureSource(msgspec.Struct, frozen=True, kw_only=True):
    """Where an edition's structures came from when a registry is authoritative.

    Attributes:
        registry: The registry's URL.
        query: The query that fetched the closure.
        retrieved_at: When.
        fingerprints: The artefact fingerprints retrieved, by URN.
    """

    registry: str
    query: str
    retrieved_at: datetime
    fingerprints: dict[str, str] = msgspec.field(default_factory=dict)


class PartialRecord(msgspec.Struct, frozen=True, kw_only=True, omit_defaults=True):
    """One artefact of an edition as its manifest fingerprints it.

    Attributes:
        fingerprint: The artefact fingerprint.
        partial_of: For a partial, the fingerprint of the full artefact it was cut from.
    """

    fingerprint: str
    partial_of: str | None = None


class LeafEntry(msgspec.Struct, frozen=True, kw_only=True):
    """One edition of a product, as the Inventory records it.

    Attributes:
        edition_id: The edition.
        previous: Its manifest's ``previous``.
        state: Its state (spec §6.4).
        manifest_sha256: The SHA-256 of its ``manifest.json``.
        schema_version: The store schema it was written at.
        state_changed_at: When it took its state.
        release_at: For an embargoed entry, when it may be released, or None
            for no scheduled release; None in every other state.
        structure_source: Where its structures came from, when a registry is
            authoritative.
        artefacts: Its manifest's artefacts and partials, by URN; optional
            in 1.0.
    """

    edition_id: str
    previous: str | None
    state: State
    manifest_sha256: str
    schema_version: str
    state_changed_at: datetime
    release_at: datetime | None = None
    structure_source: StructureSource | None = None
    artefacts: dict[str, PartialRecord] | None = None


class Leaf(msgspec.Struct, frozen=True, kw_only=True):
    """``inventory/leaves/<sha256>.json``: one product's editions that are not retired.

    Attributes:
        format_version: `INVENTORY_FORMAT`.
        product_id: The product.
        kind: ``data`` or ``structure``.
        lifecycle: ``final`` or ``revisable``.
        editions: Its entries, sorted by ``edition_id``.
    """

    format_version: str
    product_id: str
    kind: Kind
    lifecycle: Lifecycle
    editions: tuple[LeafEntry, ...]


class _FormatProbe(msgspec.Struct):
    format_version: str | None = None


def root_path(seq: int) -> str:
    """Where the root of version `seq` lives: ``inventory/<seq>.json``, the number zero-padded to 12 digits."""
    if seq < 1:
        msg = f"an Inventory version's seq starts at 1, not {seq}"
        raise InventoryError(msg)
    return f"{INVENTORY_DIR}/{seq:012d}.json"


def leaf_path(sha256: str) -> str:
    """Where the leaf whose bytes hash to `sha256` lives."""
    return f"{LEAVES_DIR}/{sha256}.json"


def sha256_hex(data: bytes) -> str:
    """The lowercase hex SHA-256 of `data`: how a root names a leaf, and ``CURRENT`` a root."""
    return hashlib.sha256(data).hexdigest()


def encode(document: CatalogDocument | Current | Root | Leaf) -> bytes:
    """The document as written: indented JSON with a final newline, keys in field order."""
    return msgspec.json.format(msgspec.json.encode(document), indent=2) + b"\n"


def _decode[D: (CatalogDocument, Root, Leaf)](raw: bytes, kind: type[D], expected: str, what: str) -> D:
    try:
        found = msgspec.json.decode(raw, type=_FormatProbe).format_version
    except msgspec.MsgspecError as exc:
        msg = f"{what} is not JSON: {exc}"
        raise InventoryError(msg) from exc
    if found is None or found.partition(".")[0] != expected.partition(".")[0]:
        msg = f"{what} is format {found}; this sdmxlib reads {expected.partition('.')[0]}.x"
        raise InventoryError(msg)
    try:
        return msgspec.json.decode(raw, type=kind)
    except msgspec.MsgspecError as exc:
        msg = f"{what} is not a valid format {found} document: {exc}"
        raise InventoryError(msg) from exc


def decode_catalog(raw: bytes) -> CatalogDocument:
    """``catalog.json`` from its bytes.

    Raises:
        InventoryError: If it is malformed or of another major format.
    """
    return _decode(raw, CatalogDocument, CATALOG_FORMAT, CATALOG_NAME)


def decode_current(raw: bytes) -> Current:
    """``CURRENT`` from its bytes.

    Raises:
        InventoryError: If it is malformed.
    """
    try:
        return msgspec.json.decode(raw, type=Current)
    except msgspec.MsgspecError as exc:
        msg = f"{CURRENT_NAME} is not a valid pointer: {exc}"
        raise InventoryError(msg) from exc


def decode_root(raw: bytes) -> Root:
    """A root from its bytes, validated (`validate_root`).

    Raises:
        InventoryError: If it is malformed, of another major format, or breaks a rule.
    """
    root = _decode(raw, Root, INVENTORY_FORMAT, "an Inventory root")
    validate_root(root)
    return root


def decode_leaf(raw: bytes) -> Leaf:
    """A leaf from its bytes, validated (`validate_leaf`).

    Raises:
        InventoryError: If it is malformed, of another major format, or breaks a rule.
    """
    leaf = _decode(raw, Leaf, INVENTORY_FORMAT, "an Inventory leaf")
    validate_leaf(leaf)
    return leaf


def validate_root(root: Root) -> None:
    """Refuse a root that breaks the format's own rules.

    Raises:
        InventoryError: If ``seq`` is below 1, ``previous_inventory`` is not
            None for version 1 or not below ``seq`` for a later one, a product's
            leaf path does not name its hash, or a tombstone repeats.
    """
    if root.seq < 1:
        msg = f"Inventory version {root.seq}: seq starts at 1"
        raise InventoryError(msg)
    if (root.seq == 1) != (root.previous_inventory is None):
        msg = f"Inventory version {root.seq}: previous_inventory {root.previous_inventory} (None only for version 1)"
        raise InventoryError(msg)
    if root.previous_inventory is not None and root.previous_inventory >= root.seq:
        msg = f"Inventory version {root.seq} is built from {root.previous_inventory}, which is not earlier"
        raise InventoryError(msg)
    for product_id, ref in root.products.items():
        if ref.leaf != leaf_path(ref.leaf_sha256):
            msg = f"Inventory version {root.seq}: {product_id}'s leaf {ref.leaf} is not {leaf_path(ref.leaf_sha256)}"
            raise InventoryError(msg)
    keys = [(t.product_id, t.edition_id) for t in root.retired]
    if len(set(keys)) != len(keys):
        msg = f"Inventory version {root.seq} tombstones an edition twice"
        raise InventoryError(msg)


def validate_leaf(leaf: Leaf) -> None:
    """Refuse a leaf that breaks the format's own rules.

    Raises:
        InventoryError: If its editions are not sorted by id or repeat one,
            more than one is live at one ``schema_version``, or a
            ``release_at`` is set on an entry that is not embargoed.
    """
    ids = [e.edition_id for e in leaf.editions]
    if ids != sorted(set(ids)):
        msg = f"{leaf.product_id}'s leaf lists its editions out of order or twice"
        raise InventoryError(msg)
    live: dict[str, str] = {}
    for entry in leaf.editions:
        if entry.state == "live":
            other = live.setdefault(entry.schema_version, entry.edition_id)
            if other != entry.edition_id:
                msg = (
                    f"{leaf.product_id} has two live editions at schema {entry.schema_version}: "
                    f"{other} and {entry.edition_id}"
                )
                raise InventoryError(msg)
        if entry.release_at is not None and entry.state != "embargoed":
            msg = f"{leaf.product_id}'s {entry.edition_id} is {entry.state} and has a release_at"
            raise InventoryError(msg)


def generate_schemas() -> dict[str, bytes]:
    """Each document's JSON Schema, generated from the model, as the shipped files hold them."""
    return {
        name: msgspec.json.format(msgspec.json.encode(msgspec.json.schema(kind)), indent=2) + b"\n"
        for name, kind in (("catalog", CatalogDocument), ("current", Current), ("root", Root), ("leaf", Leaf))
    }
