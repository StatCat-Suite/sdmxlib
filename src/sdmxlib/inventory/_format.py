"""The Inventory's documents: ``catalog.json``, ``CURRENT``, roots, leaves and history (GH-251, GH-361).

The sharded catalog spec §6 defines them. A catalog's Inventory is a chain of
versions; each version is a root, ``inventory/<seq>.json``, naming one leaf
per product, ``inventory/leaves/<sha256>.json``, content-addressed by the
SHA-256 of its bytes. ``CURRENT`` names the newest root as a hint, and
``catalog.json`` holds the catalog's identity and its ``restore_epoch``.

From Inventory 1.2 a root may carry the history fields: the SHA-256 of root
``seq - 1`` and the change list against it (§6.2, §6.3). A catalog that opts in
to expiry (``catalog.json`` 2.0) moves old roots into history segments under
``history/`` and records how far in ``inventory/HISTORY`` (§6.9).

sdmxlib owns these formats and their validation; statcatlib writes them. A
minor version only adds optional fields, which a reader ignores; a reader
refuses another major version.
"""

import hashlib
from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Literal, get_args

import msgspec
from msgspec import UNSET, UnsetType

from sdmxlib.storage._manifest import JsonValue, Producer

#: The Inventory format this build writes and reads (``1.x``): roots and leaves.
INVENTORY_FORMAT = "1.2"

#: The ``catalog.json`` format of a catalog that has not opted in to expiry (``1.x``).
CATALOG_FORMAT = "1.0"

#: The ``catalog.json`` format of a catalog that has opted in to expiry (``2.x``, spec §6.1).
OPTED_IN_CATALOG_FORMAT = "2.0"

#: The format of ``inventory/HISTORY`` and of a history segment (spec §6.9).
HISTORY_FORMAT = "1.0"

#: Where each document lives, relative to the catalog root.
CATALOG_NAME = "catalog.json"
CURRENT_NAME = "CURRENT"
INVENTORY_DIR = "inventory"
LEAVES_DIR = "inventory/leaves"
HISTORY_NAME = "inventory/HISTORY"
HISTORY_DIR = "history"

_SHA256 = r"^[0-9a-f]{64}$"
_SEGMENT_PATH = r"^history/[0-9]+/[0-9]{12}-[0-9]{12}-[0-9a-f-]{36}\.jsonl$"
_MAJOR_1 = r"^1\.[0-9]+$"

type Sha256 = Annotated[str, msgspec.Meta(pattern=_SHA256)]
type _SegmentPath = Annotated[str, msgspec.Meta(pattern=_SEGMENT_PATH)]
type _Seq = Annotated[int, msgspec.Meta(ge=1)]
type _Epoch = Annotated[int, msgspec.Meta(ge=0)]

#: An entry's state (spec §6.4). Retirement is a tombstone in the root, never a state.
type State = Literal["embargoed", "live", "superseded", "withdrawn"]

#: What an Inventory version records it was written for (spec §6.2). ``describe`` records a
#: change to the catalog description in ``extensions.statcat`` and nothing else. A writer
#: records one of these (`next_root` and `validate_root` refuse any other); a reader accepts
#: any string within format 1.x, so adding a kind is a minor-version change (GH-338).
type EventKind = Literal[
    "seal",
    "release",
    "rollback",
    "withdraw",
    "retire",
    "reinstate",
    "embargo-change",
    "restore",
    "correct",
    "describe",
]

#: The kinds a writer may record, as a set.
EVENT_KINDS: frozenset[str] = frozenset(get_args(EventKind.__value__))

#: A product holding observations, or one holding only full structures (spec §8).
type Kind = Literal["data", "structure"]

#: Whether a product's observations may still be revised.
type Lifecycle = Literal["final", "revisable"]

#: An edition's mark in a change list (spec §6.3): its state, or ``tombstone`` when the
#: version's ``retired`` list holds it. ``tombstone`` marks the list and is no fifth state.
type Marker = Literal["embargoed", "live", "superseded", "withdrawn", "tombstone"]

#: What a history segment records: roots expiry removed, or roots a restore discarded.
type SegmentKind = Literal["expiry", "restore"]


class InventoryError(ValueError):
    """An Inventory document that is malformed, of a format this sdmxlib does not read, or breaks a rule."""


class CatalogHistory(msgspec.Struct, frozen=True, kw_only=True):
    """``catalog.json``'s ``history``: where expiry may start (spec §6.1).

    Attributes:
        since_seq: The lowest ``seq`` from which every root carries both
            history fields; roots below it never expire.
    """

    since_seq: _Seq


class CatalogDocument(msgspec.Struct, frozen=True, kw_only=True):
    """``catalog.json``: a catalog's identity, written when it is created.

    Attributes:
        format_version: `CATALOG_FORMAT`, or `OPTED_IN_CATALOG_FORMAT` once
            the catalog has opted in to expiry.
        catalog_id: A UUIDv7. A full export keeps it; a live-only export
            takes a new one.
        restore_epoch: Starts at 0; a whole-catalog restore or failover bumps
            it, and readers reset their ``seq`` floor when it changes.
        history: Present exactly in a 2.x document: where expiry may start.
    """

    format_version: str
    catalog_id: str
    restore_epoch: int = 0
    history: CatalogHistory | UnsetType = UNSET

    @property
    def opted_in(self) -> bool:
        """Whether the catalog has opted in to expiry: a 2.x document."""
        return isinstance(self.history, CatalogHistory)


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
        kind: What the version records. A writer records an `EventKind`; a
            root read back may carry a kind added in a later 1.x format, which
            an audit or alerting consumer treats as other.
    """

    actor: str
    reason: str
    # A `str` so a reader takes a kind added after this build; the shipped schema keeps the
    # enum, which is what a writer meets.
    kind: Annotated[str, msgspec.Meta(extra_json_schema={"enum": sorted(EVENT_KINDS)})]


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


class EntryRecord(msgspec.Struct, frozen=True, kw_only=True):
    """A change item's copy of a `LeafEntry`: its seven required fields and no optional one (spec §6.3).

    ``structure_source``, ``artefacts`` and every later optional field are
    left out: ``manifest_sha256`` pins what the manifest holds.

    Attributes:
        edition_id: The edition.
        previous: Its manifest's ``previous``.
        state: Its state in this version.
        manifest_sha256: The SHA-256 of its ``manifest.json``.
        schema_version: The store schema it was written at.
        state_changed_at: When it took its state.
        release_at: For an embargoed entry, when it may be released; None otherwise.
    """

    edition_id: str
    previous: str | None
    state: State
    manifest_sha256: str
    schema_version: str
    state_changed_at: datetime
    release_at: datetime | None


class EntryItem(msgspec.Struct, frozen=True, kw_only=True):
    """One edition whose entry or tombstone a version changed (spec §6.3).

    An item whose ``from_`` equals ``to`` records an entry that changed and
    kept its state, such as a rescheduled ``release_at``.

    Attributes:
        edition_id: The edition.
        from_: Its `Marker` in the base version, or None when the base
            version neither records nor tombstones it. Written ``from``.
        to: Its `Marker` in this version, or None when this version drops
            its tombstone.
        entry: The new entry, present exactly when ``to`` is a state.
    """

    edition_id: str
    from_: Marker | None = msgspec.field(name="from")
    to: Marker | None
    entry: EntryRecord | UnsetType = UNSET


class LeafHashes(msgspec.Struct, frozen=True, kw_only=True):
    """A product's leaf hash in the base version and in this one.

    The two are equal when only the product's tombstones changed.

    Attributes:
        from_: In the base version, or None for a product new to the
            Inventory. Written ``from``.
        to: In this version, or None for a product removed from it.
    """

    from_: Sha256 | None = msgspec.field(name="from")
    to: Sha256 | None


class ChangeItem(msgspec.Struct, frozen=True, kw_only=True):
    """One product whose leaf or tombstones differ from the base version's (spec §6.3).

    Attributes:
        product_id: The product.
        leaf_sha256: Its leaf hash before and after.
        entries: One item per edition whose entry or tombstone differs,
            sorted by ``edition_id``.
    """

    product_id: str
    leaf_sha256: LeafHashes
    entries: tuple[EntryItem, ...]


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
        previous_root_sha256: A history field: the SHA-256 of the stored
            bytes of the base version, root ``seq - 1``, or None for version
            1. Unset in a root without the history fields.
        changes: A history field: one item per product whose leaf or
            tombstones differ from the base version's, sorted by
            ``product_id``. Unset in a root without the history fields.
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
    previous_root_sha256: Sha256 | UnsetType | None = UNSET
    changes: tuple[ChangeItem, ...] | UnsetType = UNSET

    @property
    def has_history(self) -> bool:
        """Whether the root carries both history fields (spec §6.2): the test every history rule makes."""
        return not isinstance(self.previous_root_sha256, UnsetType) and not isinstance(self.changes, UnsetType)


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
        artefacts: The artefact records of its manifest, by URN: every full
            artefact with its fingerprint, and every partial with its
            fingerprint and ``partial_of``. Optional in 1.x; a writer fills it
            in every structure entry, and an entry without it is read from
            its manifest (spec §6.3).
        dataflows: A routing field: the URNs of its manifest's bindings,
            sorted and without duplicates; empty for a structure edition.
            Unset in an entry without the routing fields.
        pull_bytes: A routing field: the sum of ``size`` over its manifest's
            ``required``-tier files. Unset in an entry without the routing
            fields.
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
    dataflows: tuple[str, ...] | UnsetType = UNSET
    pull_bytes: Annotated[int, msgspec.Meta(ge=0)] | UnsetType = UNSET

    @property
    def has_routing(self) -> bool:
        """Whether the entry carries both routing fields (spec §6.3): the test every routing rule makes."""
        return not isinstance(self.dataflows, UnsetType) and not isinstance(self.pull_bytes, UnsetType)

    @property
    def full_artefacts(self) -> tuple[str, ...] | None:
        """The URNs of its full artefacts, sorted: the records without ``partial_of``.

        In a structure entry these are the artefacts its edition is the
        structure home of (spec §7.3). None when the entry carries no
        ``artefacts``, which a reader then reads from its manifest.
        """
        if self.artefacts is None:
            return None
        return tuple(sorted(urn for urn, record in self.artefacts.items() if record.partial_of is None))


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


class History(msgspec.Struct, frozen=True, kw_only=True):
    """``inventory/HISTORY``: an opted-in catalog's epoch, expiry mark and segment pointers (spec §6.9).

    Attributes:
        format_version: `HISTORY_FORMAT`.
        catalog_id: ``catalog.json``'s.
        restore_epoch: The catalog's epoch. A restore writes the new epoch
            here before it bumps ``catalog.json``, and the reader fallback
            takes the epoch from here.
        expired_through: The highest ``seq`` expiry removed from the head
            chain, whatever the root's epoch; 0 at opt-in.
        newest_segment: The newest segment's path, relative to the catalog
            root, or None before the first.
        newest_segment_sha256: The SHA-256 of that segment's bytes, or None
            with ``newest_segment``.
        cleaned_through: The newest segment whose roots and leaves are
            deleted, or None before the first.
    """

    format_version: Annotated[str, msgspec.Meta(pattern=_MAJOR_1)]
    catalog_id: str
    restore_epoch: _Epoch
    expired_through: _Epoch
    newest_segment: _SegmentPath | None
    newest_segment_sha256: Sha256 | None
    cleaned_through: _SegmentPath | None


class SegmentHeader(msgspec.Struct, frozen=True, kw_only=True):
    """The first line of a history segment: its links and its plan (spec §6.9).

    Attributes:
        format_version: `HISTORY_FORMAT`.
        catalog_id: ``catalog.json``'s.
        kind: ``expiry`` or ``restore``. The crash rule and
            ``expired_through`` consider ``expiry`` segments only.
        restore_epoch: The epoch of the roots it records, which names its
            directory.
        first: The lowest ``seq`` in ``roots``.
        last: The highest ``seq`` in ``roots``.
        previous_segment: ``HISTORY.newest_segment`` when it was written, or
            None for the catalog's first segment.
        previous_segment_sha256: The SHA-256 of that segment's bytes, or None
            with ``previous_segment``.
        expired_at: When it was written, in storage time.
        actor: The writer's identity, as ``event.actor`` takes it.
        roots: The ``seq`` of each root the run deletes, ascending: the plan.
        leaves: Each leaf the run plans to delete; empty in a ``restore``
            segment.
        unread: Each root in ``roots`` a restore deletes and could not read;
            empty in an ``expiry`` segment.
    """

    format_version: Annotated[str, msgspec.Meta(pattern=_MAJOR_1)]
    catalog_id: str
    kind: SegmentKind
    restore_epoch: _Epoch
    first: _Seq
    last: _Seq
    previous_segment: _SegmentPath | None
    previous_segment_sha256: Sha256 | None
    expired_at: datetime
    actor: str
    roots: Annotated[tuple[_Seq, ...], msgspec.Meta(min_length=1, extra_json_schema={"uniqueItems": True})]
    leaves: Annotated[tuple[Sha256, ...], msgspec.Meta(extra_json_schema={"uniqueItems": True})]
    unread: Annotated[tuple[_Seq, ...], msgspec.Meta(extra_json_schema={"uniqueItems": True})]


class SegmentLine(msgspec.Struct, frozen=True, kw_only=True):
    """One line after a segment's header: a removed root without ``products`` and ``retired`` (spec §6.9).

    It keeps the root's other fields in the root's order, and adds
    ``root_sha256`` last. A line carries both history fields or neither, and
    a line in an ``expiry`` segment carries both.

    Attributes:
        format_version: The root's.
        seq: The root's.
        previous_inventory: The root's.
        restore_epoch: The root's.
        created_at: The root's.
        producer: The root's.
        embargo: The root's.
        event: The root's.
        extensions: The root's.
        previous_root_sha256: The root's, or unset.
        changes: The root's, or unset.
        root_sha256: The SHA-256 of the removed root's stored bytes.
    """

    format_version: Annotated[str, msgspec.Meta(pattern=_MAJOR_1)]
    seq: int
    previous_inventory: int | None
    restore_epoch: int
    created_at: datetime
    producer: Producer
    embargo: bool
    event: Event
    extensions: dict[str, dict[str, JsonValue]] = msgspec.field(default_factory=dict)
    previous_root_sha256: Sha256 | UnsetType | None = UNSET
    changes: tuple[ChangeItem, ...] | UnsetType = UNSET
    root_sha256: Sha256

    @property
    def has_history(self) -> bool:
        """Whether the line carries both history fields."""
        return not isinstance(self.previous_root_sha256, UnsetType) and not isinstance(self.changes, UnsetType)


class _LineProbe(msgspec.Struct):
    products: msgspec.Raw | UnsetType = UNSET
    retired: msgspec.Raw | UnsetType = UNSET


class _FormatProbe(msgspec.Struct):
    format_version: str | None = None


#: Which ``catalog.json`` majors a decoder takes (spec §6.1): a reader takes 1 and 2; a
#: writer without stage-2 support (the catalog lock and the ``expired_through`` check) only 1.
type CatalogDecoder = Literal["reader", "stage-1-writer", "stage-2-writer"]


def root_path(seq: int) -> str:
    """Where the root of version `seq` lives: ``inventory/<seq>.json``, the number zero-padded to 12 digits."""
    if seq < 1:
        msg = f"an Inventory version's seq starts at 1, not {seq}"
        raise InventoryError(msg)
    return f"{INVENTORY_DIR}/{seq:012d}.json"


def leaf_path(sha256: str) -> str:
    """Where the leaf whose bytes hash to `sha256` lives."""
    return f"{LEAVES_DIR}/{sha256}.json"


def segment_path(restore_epoch: int, first: int, last: int, suffix: str) -> str:
    """Where a history segment lives: ``history/<restore_epoch>/<first>-<last>-<suffix>.jsonl``.

    `first` and `last` are padded as `root_path` pads a ``seq``, and `suffix`
    is a UUIDv7, so a rerun never collides with an orphan (spec §6.9).
    """
    return f"{HISTORY_DIR}/{restore_epoch}/{first:012d}-{last:012d}-{suffix}.jsonl"


def sha256_hex(data: bytes) -> str:
    """The lowercase hex SHA-256 of `data`: how a root names a leaf, and ``CURRENT`` a root."""
    return hashlib.sha256(data).hexdigest()


def encode(document: CatalogDocument | Current | Root | Leaf | History) -> bytes:
    """The document as written: indented JSON with a final newline, keys in field order."""
    return msgspec.json.format(msgspec.json.encode(document), indent=2) + b"\n"


def encode_segment(header: SegmentHeader, lines: Sequence[SegmentLine]) -> bytes:
    """A history segment's bytes: the header, then one line per root, compact JSON each ended by a newline."""
    return b"".join(msgspec.json.encode(part) + b"\n" for part in (header, *lines))


def segment_line(root: Root, stored: bytes) -> SegmentLine:
    """The segment line recording `root`, whose stored bytes are `stored`.

    Raises:
        InventoryError: If `stored` is not `root`'s bytes.
    """
    if decode_root(stored) != root:
        msg = f"Inventory version {root.seq}: the bytes given are not this root's"
        raise InventoryError(msg)
    return SegmentLine(
        format_version=root.format_version,
        seq=root.seq,
        previous_inventory=root.previous_inventory,
        restore_epoch=root.restore_epoch,
        created_at=root.created_at,
        producer=root.producer,
        embargo=root.embargo,
        event=root.event,
        extensions=root.extensions,
        previous_root_sha256=root.previous_root_sha256,
        changes=root.changes,
        root_sha256=sha256_hex(stored),
    )


def _major(raw: bytes, what: str) -> tuple[str | None, str]:
    """The document's ``format_version`` and its major."""
    try:
        found = msgspec.json.decode(raw, type=_FormatProbe).format_version
    except msgspec.MsgspecError as exc:
        msg = f"{what} is not JSON: {exc}"
        raise InventoryError(msg) from exc
    return found, (found or "").partition(".")[0]


def _decode[D: (CatalogDocument, Root, Leaf, History, SegmentHeader, SegmentLine)](
    raw: bytes, kind: type[D], expected: str, what: str
) -> D:
    found, major = _major(raw, what)
    if found is None or major != expected.partition(".")[0]:
        msg = f"{what} is format {found}; this sdmxlib reads {expected.partition('.')[0]}.x"
        raise InventoryError(msg)
    try:
        return msgspec.json.decode(raw, type=kind)
    except msgspec.MsgspecError as exc:
        msg = f"{what} is not a valid format {found} document: {exc}"
        raise InventoryError(msg) from exc


def decode_catalog(raw: bytes, *, decoder: CatalogDecoder = "reader") -> CatalogDocument:
    """``catalog.json`` from its bytes.

    A reader and a writer with stage-2 support decode majors 1 and 2. A
    writer without stage-2 support refuses a 2.x document, so it stays off a
    catalog that has opted in to expiry (spec §6.1). A writer decodes
    ``catalog.json`` on every commit.

    Args:
        raw: The bytes.
        decoder: Who decodes: ``reader``, ``stage-1-writer`` or
            ``stage-2-writer``.

    Raises:
        InventoryError: If it is malformed, of a major format `decoder` does
            not take, or carries ``history`` in a 1.x document or lacks it in
            a 2.x one.
    """
    found, major = _major(raw, CATALOG_NAME)
    if found is None or major not in {"1", "2"}:
        msg = f"{CATALOG_NAME} is format {found}; this sdmxlib reads 1.x and 2.x"
        raise InventoryError(msg)
    if major == "2" and decoder == "stage-1-writer":
        msg = (
            f"{CATALOG_NAME} is format {found}: the catalog has opted in to expiry, and a writer without "
            "stage-2 support (the catalog lock and the expired_through check) does not write to it"
        )
        raise InventoryError(msg)
    expected = CATALOG_FORMAT if major == "1" else OPTED_IN_CATALOG_FORMAT
    document = _decode(raw, CatalogDocument, expected, CATALOG_NAME)
    if document.opted_in != (major == "2"):
        msg = f"{CATALOG_NAME} is format {found}; history is present exactly in a 2.x document"
        raise InventoryError(msg)
    return document


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
    """A root from its bytes, validated as a reader validates it.

    A reader checks every rule `validate_root` checks except the event kind:
    it accepts any ``event.kind`` within format 1.x (spec §6.2).

    Raises:
        InventoryError: If it is malformed, of another major format, or breaks a rule.
    """
    root = _decode(raw, Root, INVENTORY_FORMAT, "an Inventory root")
    _check_root(root)
    return root


def decode_leaf(raw: bytes) -> Leaf:
    """A leaf from its bytes, validated (`validate_leaf`).

    Raises:
        InventoryError: If it is malformed, of another major format, or breaks a rule.
    """
    leaf = _decode(raw, Leaf, INVENTORY_FORMAT, "an Inventory leaf")
    validate_leaf(leaf)
    return leaf


def decode_history(raw: bytes) -> History:
    """``inventory/HISTORY`` from its bytes.

    Raises:
        InventoryError: If it is malformed or of another major format, or
            names a segment hash or ``cleaned_through`` without
            ``newest_segment``, or ``newest_segment`` without its hash.
    """
    history = _decode(raw, History, HISTORY_FORMAT, HISTORY_NAME)
    if history.newest_segment is None and (
        history.newest_segment_sha256 is not None or history.cleaned_through is not None
    ):
        msg = f"{HISTORY_NAME} names no segment, and a segment hash or cleaned_through"
        raise InventoryError(msg)
    if history.newest_segment is not None and history.newest_segment_sha256 is None:
        msg = f"{HISTORY_NAME} names {history.newest_segment} without its SHA-256"
        raise InventoryError(msg)
    return history


def decode_segment(raw: bytes) -> tuple[SegmentHeader, tuple[SegmentLine, ...]]:
    """A history segment from its bytes: the header and its lines (spec §6.9).

    Raises:
        InventoryError: If a line is malformed or of another major format,
            the bytes do not end with a newline, the header breaks a rule
            (`SegmentHeader`), or the lines are not one per root in ``roots``
            outside ``unread``, ascending, of the header's epoch, without
            ``products`` and ``retired``, and in an ``expiry`` segment with
            the history fields.
    """
    if not raw.endswith(b"\n"):
        msg = "a history segment does not end with a newline"
        raise InventoryError(msg)
    first, *rest = raw[:-1].split(b"\n")
    header = _decode(first, SegmentHeader, HISTORY_FORMAT, "a segment header")
    _check_header(header)
    lines = tuple(_decode(line, SegmentLine, HISTORY_FORMAT, "a segment line") for line in rest)
    for line, data in zip(lines, rest, strict=True):
        probe = msgspec.json.decode(data, type=_LineProbe)
        if not isinstance(probe.products, UnsetType) or not isinstance(probe.retired, UnsetType):
            msg = f"the segment line of version {line.seq} carries products or retired"
            raise InventoryError(msg)
        if line.restore_epoch != header.restore_epoch:
            msg = f"the segment line of version {line.seq} is of epoch {line.restore_epoch}, not {header.restore_epoch}"
            raise InventoryError(msg)
        if header.kind == "expiry" and not line.has_history:
            msg = f"the expiry segment line of version {line.seq} lacks the history fields"
            raise InventoryError(msg)
        if line.has_history != (
            not isinstance(line.previous_root_sha256, UnsetType) or not isinstance(line.changes, UnsetType)
        ):
            msg = f"the segment line of version {line.seq} carries one history field without the other"
            raise InventoryError(msg)
    unread = set(header.unread)
    expected = [seq for seq in header.roots if seq not in unread]
    if [line.seq for line in lines] != expected:
        msg = f"the segment's lines record versions {[line.seq for line in lines]}, and its header plans {expected}"
        raise InventoryError(msg)
    return header, lines


def _check_header(header: SegmentHeader) -> None:
    roots = list(header.roots)
    if roots != sorted(set(roots)):
        msg = "a segment header lists its roots out of order or twice"
        raise InventoryError(msg)
    if (header.first, header.last) != (roots[0], roots[-1]):
        msg = f"a segment header says {header.first}-{header.last} and lists roots {roots[0]}-{roots[-1]}"
        raise InventoryError(msg)
    if not set(header.unread) <= set(roots):
        msg = "a segment header lists an unread root outside its roots"
        raise InventoryError(msg)
    if header.kind == "expiry" and header.unread:
        msg = "an expiry segment lists unread roots"
        raise InventoryError(msg)
    if header.kind == "restore" and header.leaves:
        msg = "a restore segment plans leaf deletions"
        raise InventoryError(msg)
    if (header.previous_segment is None) != (header.previous_segment_sha256 is None):
        msg = "a segment header names a previous segment without its hash, or a hash without one"
        raise InventoryError(msg)


def validate_root(root: Root) -> None:
    """Refuse a root that breaks the format's own rules, as a writer must meet them.

    Raises:
        InventoryError: If ``seq`` is below 1, ``previous_inventory`` is not
            None for version 1 or not below ``seq`` for a later one, a product's
            leaf path does not name its hash, a tombstone repeats, a history
            field breaks a rule (`decode_root`), or ``event.kind`` is not an
            `EventKind`.
    """
    _check_root(root)
    check_event_kind(root.event, root.seq)


def check_event_kind(event: Event, seq: int) -> None:
    """Refuse an event whose kind is not an `EventKind`: the writer's half of spec §6.2.

    Raises:
        InventoryError: If it is not, naming the version `seq` and the kinds a writer records.
    """
    if event.kind not in EVENT_KINDS:
        msg = (
            f"Inventory version {seq}: event kind {event.kind!r} is not one a writer records "
            f"({', '.join(sorted(EVENT_KINDS))})"
        )
        raise InventoryError(msg)


def _check_root(root: Root) -> None:
    """The rules a reader and a writer share: every one `validate_root` checks but the event kind."""
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
    _check_history_fields(root.seq, root.previous_root_sha256, root.changes)


def _check_history_fields(
    seq: int, previous_root_sha256: str | UnsetType | None, changes: tuple[ChangeItem, ...] | UnsetType
) -> None:
    """The rules of the history fields (spec §6.2, §6.3), when a root or line carries them."""
    if isinstance(previous_root_sha256, UnsetType) and isinstance(changes, UnsetType):
        return
    if isinstance(previous_root_sha256, UnsetType) or isinstance(changes, UnsetType):
        msg = f"Inventory version {seq} carries one history field without the other"
        raise InventoryError(msg)
    if (seq == 1) != (previous_root_sha256 is None):
        msg = f"Inventory version {seq}: previous_root_sha256 {previous_root_sha256} (None only for version 1)"
        raise InventoryError(msg)
    products = [c.product_id for c in changes]
    if products != sorted(set(products)):
        msg = f"Inventory version {seq} lists its changes out of order or twice"
        raise InventoryError(msg)
    for change in changes:
        editions = [e.edition_id for e in change.entries]
        if editions != sorted(set(editions)):
            msg = f"Inventory version {seq}: {change.product_id}'s change lists its editions out of order or twice"
            raise InventoryError(msg)
        for item in change.entries:
            _check_item(seq, change.product_id, item)


def _check_item(seq: int, product_id: str, item: EntryItem) -> None:
    where = f"Inventory version {seq}: {product_id}'s change to {item.edition_id}"
    if item.from_ is None and item.to is None:
        msg = f"{where} goes from nothing to nothing"
        raise InventoryError(msg)
    is_state = item.to not in {None, "tombstone"}
    if isinstance(item.entry, UnsetType):
        if is_state:
            msg = f"{where} goes to {item.to} without its entry"
            raise InventoryError(msg)
        return
    if not is_state:
        msg = f"{where} goes to {item.to} and carries an entry"
        raise InventoryError(msg)
    if (item.entry.edition_id, item.entry.state) != (item.edition_id, item.to):
        msg = f"{where} carries the entry of {item.entry.edition_id} in state {item.entry.state}"
        raise InventoryError(msg)


def validate_leaf(leaf: Leaf) -> None:
    """Refuse a leaf that breaks the format's own rules.

    Raises:
        InventoryError: If its editions are not sorted by id or repeat one,
            more than one is live at one ``schema_version``, a
            ``release_at`` is set on an entry that is not embargoed, or an
            entry breaks a routing-field rule: one routing field without the
            other, ``dataflows`` unsorted or repeating a URN, or a negative
            ``pull_bytes`` (spec §6.3).
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
        _check_routing(leaf.product_id, entry)


def _check_routing(product_id: str, entry: LeafEntry) -> None:
    """The rules of the routing fields (spec §6.3), when an entry carries them."""
    dataflows, pull_bytes = entry.dataflows, entry.pull_bytes
    if isinstance(dataflows, UnsetType) and isinstance(pull_bytes, UnsetType):
        return
    where = f"{product_id}'s {entry.edition_id}"
    if isinstance(dataflows, UnsetType) or isinstance(pull_bytes, UnsetType):
        msg = f"{where} carries one routing field without the other (dataflows and pull_bytes)"
        raise InventoryError(msg)
    if list(dataflows) != sorted(set(dataflows)):
        msg = f"{where} lists its dataflows out of order or twice"
        raise InventoryError(msg)
    if pull_bytes < 0:
        msg = f"{where} has a negative pull_bytes, {pull_bytes}"
        raise InventoryError(msg)


# ── JSON Schemas ─────────────────────────────────────────────────────────────
#
# Every shipped schema is msgspec's schema of a model, with the rules below merged in: the
# conditional rules msgspec cannot state from a type. The Python checks above state the same
# rules for a decode.

type _Schema = dict[str, object]

_NULL: _Schema = {"type": "null"}
_STATE_ENUM: list[object] = ["embargoed", "live", "superseded", "withdrawn"]
_MARKER_ENUM: _Schema = {"enum": [*_STATE_ENUM, "tombstone", None]}

#: Rules merged into a definition, wherever a schema holds it.
_DEFINITION_RULES: dict[str, _Schema] = {
    "EntryItem": {
        # One enum with null among its values, where msgspec writes an anyOf.
        "properties": {"from": _MARKER_ENUM, "to": _MARKER_ENUM},
        "not": {"properties": {"from": {"const": None}, "to": {"const": None}}},
        "if": {"properties": {"to": {"enum": _STATE_ENUM}}},
        "then": {"required": ["entry"]},
        "else": {"not": {"required": ["entry"]}},
    },
    "History": {
        "if": {"properties": {"newest_segment": _NULL}},
        "then": {"properties": {"newest_segment_sha256": _NULL, "cleaned_through": _NULL}},
        "else": {"properties": {"newest_segment_sha256": {"type": "string", "pattern": _SHA256}}},
    },
    "SegmentHeader": {
        "allOf": [
            {
                "if": {"properties": {"kind": {"const": "expiry"}}},
                "then": {"properties": {"unread": {"maxItems": 0}}},
            },
            {
                "if": {"properties": {"kind": {"const": "restore"}}},
                "then": {"properties": {"leaves": {"maxItems": 0}}},
            },
            {
                "if": {"properties": {"previous_segment": _NULL}},
                "then": {"properties": {"previous_segment_sha256": _NULL}},
                "else": {"properties": {"previous_segment_sha256": {"type": "string", "pattern": _SHA256}}},
            },
        ]
    },
    "SegmentLine": {
        "dependentRequired": {"changes": ["previous_root_sha256"], "previous_root_sha256": ["changes"]},
        "not": {"anyOf": [{"required": ["products"]}, {"required": ["retired"]}]},
    },
    "Root": {"dependentRequired": {"changes": ["previous_root_sha256"], "previous_root_sha256": ["changes"]}},
    "LeafEntry": {
        "dependentRequired": {"dataflows": ["pull_bytes"], "pull_bytes": ["dataflows"]},
        "properties": {"dataflows": {"uniqueItems": True}},
    },
}

#: Each shipped schema: its model, and rules merged into the model's own definition.
_SCHEMAS: dict[str, tuple[type, _Schema]] = {
    "catalog": (CatalogDocument, {}),
    "current": (Current, {}),
    "root": (Root, {}),
    "leaf": (Leaf, {}),
    "catalog-2.0": (
        CatalogDocument,
        {
            "properties": {"format_version": {"pattern": r"^2\.[0-9]+$"}, "restore_epoch": {"minimum": 0}},
            "required": ["restore_epoch", "history"],
        },
    ),
    "root-1.2": (
        Root,
        {"properties": {"format_version": {"pattern": _MAJOR_1}}, "required": ["previous_root_sha256", "changes"]},
    ),
    "history": (History, {}),
    "segment-header": (SegmentHeader, {}),
    "segment-line": (SegmentLine, {}),
}


def _merged(base: _Schema, rules: _Schema) -> _Schema:
    """`base` with `rules` merged in: objects key by key, ``required`` appended."""
    merged = dict(base)
    for key, value in rules.items():
        current = merged.get(key)
        if key == "required" and isinstance(current, list) and isinstance(value, list):
            merged[key] = [*current, *(v for v in value if v not in current)]
        elif isinstance(current, dict) and isinstance(value, dict) and value is not _MARKER_ENUM:
            merged[key] = _merged({str(k): v for k, v in current.items()}, {str(k): v for k, v in value.items()})
        else:
            merged[key] = value
    return merged


def generate_schemas() -> dict[str, bytes]:
    """Each document's JSON Schema, generated from the model, as the shipped files hold them."""
    generated: dict[str, bytes] = {}
    for name, (model, own) in _SCHEMAS.items():
        document = msgspec.json.schema(model)
        definitions = document["$defs"]
        for title, rules in _DEFINITION_RULES.items():
            if title in definitions:
                definitions[title] = _merged(definitions[title], rules)
        definitions[model.__name__] = _merged(definitions[model.__name__], own)
        generated[name] = msgspec.json.format(msgspec.json.encode(document), indent=2) + b"\n"
    return generated
