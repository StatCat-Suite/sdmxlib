"""Reading a catalog's Inventory through a `fetch` callable, and the two views of it (GH-251).

sdmxlib holds no blob client (spec §11.5): a reader is given
``fetch(path) -> bytes | None``, a path relative to the catalog root to its
bytes, or None when nothing is there. `local_fetch` reads a directory; a
pod passes one over ``obstore`` or ``fsspec``.

The read protocol (spec §6.6):

1. read ``CURRENT``;
2. verify the root it names against its ``root_sha256``, and fall back to
   the version before it on a mismatch;
3. probe ``seq + 1``, ``seq + 2``, … until one is absent, with no listing;
4. never accept a lower ``seq`` than one already held, unless
   ``restore_epoch`` changed, which resets the floor.

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

from sdmxlib.inventory._format import (
    CURRENT_NAME,
    InventoryError,
    Kind,
    Leaf,
    LeafEntry,
    LeafRef,
    Lifecycle,
    Root,
    Tombstone,
    decode_current,
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
    """

    product_id: str
    edition_id: str
    manifest_sha256: str
    schema_version: str
    kind: Kind
    lifecycle: Lifecycle


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

        Raises:
            InventoryError: If no valid version is found and none is held.
        """
        held = self._root
        raw = self._fetch(CURRENT_NAME)
        current = decode_current(raw) if raw is not None else None
        epoch = current.restore_epoch if current is not None else (held.restore_epoch if held else 0)
        floor = held.seq if held is not None and held.restore_epoch == epoch else 0
        found: Root | None = None
        if current is not None:
            found = self._root_at(current.seq, current.root_sha256)
            seq = current.seq - 1
            while found is None and seq > floor:
                found = self._root_at(seq, None)
                seq -= 1
        if found is None and floor > 0 and held is not None:
            found = held
        start = found.seq if found is not None else max(floor, 0)
        seq = start + 1
        while True:
            raw = self._fetch(root_path(seq))
            if raw is None:
                break
            later = self._root_at(seq, None, raw)
            if later is not None and later.restore_epoch == epoch:
                found = later
            seq += 1
        if found is None:
            msg = f"the catalog has no valid Inventory version ({'; '.join(self.skipped) or 'none written'})"
            raise InventoryError(msg)
        if held is not None and held.restore_epoch == epoch and found.seq < held.seq:
            found = held
        self._root = found
        return self

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
