"""Reading a catalog's Inventory through a `fetch` callable, and the two views of it (GH-251).

sdmxlib holds no blob client (spec §11.5): a reader is given
``fetch(path) -> bytes | None``, a path relative to the catalog root to its
bytes, or None when nothing is there. `local_fetch` reads a directory; a
pod passes one over ``obstore`` or ``fsspec``.

The read protocol (spec §6.6):

1. read ``CURRENT``;
2. verify the root it names against its ``root_sha256``;
3. probe ``seq + 1``, ``seq + 2``, … until one is absent, with no listing;
4. never accept a lower ``seq`` than one already held, unless
   ``restore_epoch`` changed, which resets the floor.

With ``CURRENT`` present and naming a root that verifies, the reader reads
nothing else but roots. When ``CURRENT`` is missing, malformed or names a
root that fails, it falls back (stage 1 of Inventory 1.2): it reads
``catalog.json``, and on a 2.x catalog ``inventory/HISTORY``; takes the epoch
from ``HISTORY``, or from ``catalog.json`` on a 1.x catalog; walks back from
``CURRENT`` no further than its lower bound, the larger of the ``seq`` it
holds in that epoch and ``expired_through``; drops a held root at or below
``expired_through``; and probes from one above the bound. It fails closed
when it finds no root above the bound and holds none, and when a 2.x catalog
has no ``HISTORY``.

A root that does not validate is skipped and reported in `skipped`, and the
last valid version is kept.

Two views, constructed separately so a public reader holds nothing it must
filter:

- `LiveInventory` holds only ``live`` entries, at its schema version, and
  drops a product with no live edition.
- `FullInventory` holds every entry and the tombstones, for the release job
  and the operational objects.
"""

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Self

import msgspec
from msgspec import UnsetType

from sdmxlib.inventory._format import (
    CATALOG_NAME,
    CURRENT_NAME,
    HISTORY_NAME,
    Current,
    InventoryError,
    Kind,
    Leaf,
    LeafEntry,
    LeafRef,
    Lifecycle,
    PartialRecord,
    Root,
    Tombstone,
    decode_catalog,
    decode_current,
    decode_history,
    decode_leaf,
    decode_root,
    root_path,
    sha256_hex,
)
from sdmxlib.storage.schema import SCHEMA_VERSION

#: A path under the catalog root to its bytes, or None when nothing is there.
type Fetch = Callable[[str], bytes | None]


def local_fetch(root: str | Path) -> Fetch:
    """A `Fetch` over a local catalog directory."""
    base = Path(root)

    def fetch(path: str) -> bytes | None:
        try:
            return (base / path).read_bytes()
        except FileNotFoundError:
            return None

    return fetch


class LiveEdition(msgspec.Struct, frozen=True, kw_only=True):
    """A product's live edition at a schema version: what a public reader routes to.

    Attributes:
        product_id: The product.
        edition_id: Its live edition.
        manifest_sha256: The SHA-256 of that edition's ``manifest.json``, to
            verify before reading anything else.
        schema_version: The store schema it was written at.
        kind: ``data`` or ``structure``.
        lifecycle: ``final`` or ``revisable``.
        dataflows: The entry's ``dataflows``, or None when it lacks the
            routing fields (spec §6.3).
        pull_bytes: The entry's ``pull_bytes``, or None when it lacks the
            routing fields.
        artefacts: The entry's artefact records, or None when it carries none.
            A structure edition's records without ``partial_of`` are the
            artefacts it is the structure home of (spec §7.3).
    """

    product_id: str
    edition_id: str
    manifest_sha256: str
    schema_version: str
    kind: Kind
    lifecycle: Lifecycle
    dataflows: tuple[str, ...] | None = None
    pull_bytes: int | None = None
    artefacts: dict[str, PartialRecord] | None = None


class _Chain:
    """The read protocol over `fetch`: the newest valid root, never behind the floor."""

    def __init__(self, fetch: Fetch) -> None:
        self._fetch = fetch
        self._root: Root | None = None
        self._leaves: dict[str, Leaf] = {}
        self.skipped: list[str] = []

    def _root_at(self, seq: int, sha256: str | None, raw: bytes | None = None) -> Root | None:
        """The valid root at `seq`, or None when it is absent, invalid or not `sha256`'s."""
        if raw is None:
            raw = self._fetch(root_path(seq))
        if raw is None:
            return None
        if sha256 is not None and sha256_hex(raw) != sha256:
            self.skipped.append(f"{root_path(seq)}: its SHA-256 is not the one {CURRENT_NAME} names")
            return None
        try:
            root = decode_root(raw)
        except InventoryError as exc:
            self.skipped.append(f"{root_path(seq)}: {exc}")
            return None
        if root.seq != seq:
            self.skipped.append(f"{root_path(seq)}: it says it is version {root.seq}")
            return None
        return root

    def refresh(self) -> Self:
        """Read the newest valid Inventory version, by the read protocol.

        With ``CURRENT`` present and naming a root that verifies, it reads
        only ``CURRENT`` and roots. Otherwise it takes the fallback of spec
        §6.6 (`_fallback`).

        Raises:
            InventoryError: If no valid version is found and none is held,
                or the fallback fails closed.
        """
        held = self._root
        current = self._read_current()
        found = None if current is None else self._root_at(current.seq, current.root_sha256)
        if current is not None and found is not None:
            epoch = current.restore_epoch
            start = found.seq
        else:
            epoch, bound, held = self._fallback(held)
            self._root = held
            seq = current.seq - 1 if current is not None else bound
            while found is None and seq > bound:
                candidate = self._root_at(seq, None)
                found = candidate if candidate is not None and candidate.restore_epoch == epoch else None
                seq -= 1
            if found is None and held is not None and held.restore_epoch == epoch:
                found = held
            start = found.seq if found is not None else bound
        found = self._probe(start, epoch) or found
        if found is None:
            msg = f"the catalog has no valid Inventory version ({'; '.join(self.skipped) or 'none written'})"
            raise InventoryError(msg)
        if held is not None and held.restore_epoch == epoch and found.seq < held.seq:
            found = held
        self._root = found
        return self

    def _read_current(self) -> Current | None:
        """``CURRENT``, or None when it is missing or malformed (then reported in `skipped`)."""
        raw = self._fetch(CURRENT_NAME)
        if raw is None:
            return None
        try:
            return decode_current(raw)
        except InventoryError as exc:
            self.skipped.append(str(exc))
            return None

    def _probe(self, after: int, epoch: int) -> Root | None:
        """The newest valid root of `epoch` from ``after + 1`` until a ``seq`` is absent, or None."""
        found: Root | None = None
        seq = after + 1
        while (raw := self._fetch(root_path(seq))) is not None:
            later = self._root_at(seq, None, raw)
            if later is not None and later.restore_epoch == epoch:
                found = later
            seq += 1
        return found

    def _fallback(self, held: Root | None) -> tuple[int, int, Root | None]:
        """The epoch, the lower bound and the held root to keep, when ``CURRENT`` fails (spec §6.6).

        It reads ``catalog.json``, and on a 2.x document ``inventory/HISTORY``,
        taking the epoch from ``HISTORY`` or, on a 1.x catalog, from
        ``catalog.json``. The bound is the larger of the held ``seq`` in that
        epoch and ``expired_through`` (0 on a 1.x catalog); a held root at or
        below ``expired_through`` is dropped, and no longer served.

        Raises:
            InventoryError: If ``catalog.json`` is missing or malformed, or a
                2.x catalog has no valid ``inventory/HISTORY``.
        """
        raw = self._fetch(CATALOG_NAME)
        if raw is None:
            msg = f"{CURRENT_NAME} failed and the catalog has no {CATALOG_NAME}"
            raise InventoryError(msg)
        catalog = decode_catalog(raw)
        epoch, expired = catalog.restore_epoch, 0
        if catalog.opted_in:
            raw = self._fetch(HISTORY_NAME)
            if raw is None:
                msg = f"{CATALOG_NAME} is format {catalog.format_version} and the catalog has no {HISTORY_NAME}"
                raise InventoryError(msg)
            history = decode_history(raw)
            epoch, expired = history.restore_epoch, history.expired_through
        if held is not None and held.seq <= expired:
            held = None
        floor = held.seq if held is not None and held.restore_epoch == epoch else 0
        return epoch, max(floor, expired), held

    @property
    def _current(self) -> Root:
        if self._root is None:
            self.refresh()
        root = self._root
        if root is None:
            msg = "the catalog has no Inventory version"
            raise InventoryError(msg)
        return root

    @property
    def seq(self) -> int:
        """The Inventory version held."""
        return self._current.seq

    @property
    def restore_epoch(self) -> int:
        """The held version's ``restore_epoch``."""
        return self._current.restore_epoch

    def _leaf(self, ref: LeafRef) -> Leaf:
        cached = self._leaves.get(ref.leaf_sha256)
        if cached is not None:
            return cached
        raw = self._fetch(ref.leaf)
        if raw is None:
            msg = f"{ref.leaf} is missing"
            raise InventoryError(msg)
        if sha256_hex(raw) != ref.leaf_sha256:
            msg = f"{ref.leaf} does not hash to its name"
            raise InventoryError(msg)
        leaf = decode_leaf(raw)
        self._leaves[ref.leaf_sha256] = leaf
        return leaf


class FullInventory(_Chain):
    """Every entry of every product, and the tombstones: for the release job and the operational objects."""

    @property
    def root(self) -> Root:
        """The held version's root."""
        return self._current

    @property
    def embargo(self) -> bool:
        """The held version's embargo setting."""
        return self._current.embargo

    @property
    def retired(self) -> tuple[Tombstone, ...]:
        """The held version's tombstones."""
        return self._current.retired

    def products(self) -> list[str]:
        """Every recorded product, sorted."""
        return sorted(self._current.products)

    def leaf(self, product_id: str) -> Leaf:
        """The product's leaf.

        Raises:
            KeyError: If the version records no such product.
        """
        return self._leaf(self._current.products[product_id])

    def leaves(self) -> Mapping[str, Leaf]:
        """Every product's leaf, by product."""
        return {p: self._leaf(ref) for p, ref in sorted(self._current.products.items())}

    def entries(self, product_id: str) -> tuple[LeafEntry, ...]:
        """The product's entries in every state."""
        return self.leaf(product_id).editions


class LiveInventory(_Chain):
    """The live view: only ``live`` entries at one schema version, for the public catalog and the pods.

    It holds no embargoed, superseded or withdrawn entry, so nothing read
    through it needs filtering.
    """

    def __init__(self, fetch: Fetch, *, schema_version: str = SCHEMA_VERSION) -> None:
        """Read through `fetch`, routing to the live editions at `schema_version` (this sdmxlib's by default)."""
        super().__init__(fetch)
        self.schema_version = schema_version

    def _live(self, product_id: str, ref: LeafRef) -> LiveEdition | None:
        leaf = self._leaf(ref)
        entry = next((e for e in leaf.editions if e.state == "live" and e.schema_version == self.schema_version), None)
        if entry is None:
            return None
        return LiveEdition(
            product_id=product_id,
            edition_id=entry.edition_id,
            manifest_sha256=entry.manifest_sha256,
            schema_version=entry.schema_version,
            kind=leaf.kind,
            lifecycle=leaf.lifecycle,
            dataflows=None if isinstance(entry.dataflows, UnsetType) else entry.dataflows,
            pull_bytes=None if isinstance(entry.pull_bytes, UnsetType) else entry.pull_bytes,
            artefacts=entry.artefacts,
        )

    def editions(self) -> Mapping[str, LiveEdition]:
        """Each product's live edition, by product; a product with none is absent."""
        found = {p: self._live(p, ref) for p, ref in sorted(self._current.products.items())}
        return {p: e for p, e in found.items() if e is not None}

    def products(self) -> list[str]:
        """The products with a live edition, sorted."""
        return list(self.editions())

    def head(self, product_id: str) -> LiveEdition:
        """The product's live edition.

        Raises:
            KeyError: If the product has no live edition at this schema version.
        """
        ref = self._current.products.get(product_id)
        found = None if ref is None else self._live(product_id, ref)
        if found is None:
            msg = f"{product_id} has no live edition at schema {self.schema_version}"
            raise KeyError(msg)
        return found
