"""A minimal Inventory writer over a local directory, for tests (GH-251, GH-252).

statcatlib owns the real writer. This one follows the spec's write protocol
(§6.6) closely enough to drive the reader and the rules: leaves first, then
the root created exclusively through `sdmxlib.storage.create_exclusive`, then ``CURRENT``.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from sdmxlib.inventory import (
    CATALOG_FORMAT,
    CATALOG_NAME,
    CURRENT_NAME,
    CatalogDocument,
    Current,
    Event,
    EventKind,
    FullInventory,
    Leaf,
    Root,
    encode,
    leaf_ref,
    local_fetch,
    next_root,
    root_path,
    sha256_hex,
)
from sdmxlib.storage import create_exclusive
from sdmxlib.storage._manifest import Producer

PRODUCER = Producer(name="tests", version="0")
NOW = datetime(2026, 10, 9, 12, 30, tzinfo=UTC)


def new_catalog(root: Path, catalog_id: str = "01a18e00-0000-7000-8000-000000000000", restore_epoch: int = 0) -> None:
    """Write ``catalog.json``."""
    root.mkdir(parents=True, exist_ok=True)
    document = CatalogDocument(format_version=CATALOG_FORMAT, catalog_id=catalog_id, restore_epoch=restore_epoch)
    (root / CATALOG_NAME).write_bytes(encode(document))


def write_version(
    root: Path,
    leaves: Mapping[str, Leaf],
    *,
    kind: EventKind = "seal",
    now: datetime = NOW,
    embargo: bool | None = None,
    restore_epoch: int | None = None,
    update_current: bool = True,
) -> Root:
    """Write one Inventory version holding `leaves` for the products they name, the rest carried.

    Returns:
        The root written.

    Raises:
        FileExistsError: If another writer created the version first.
    """
    inventory = FullInventory(local_fetch(root))
    try:
        previous: Root | None = inventory.refresh().root
    except ValueError:
        previous = None
    products = dict(previous.products) if previous is not None else {}
    for product_id, leaf in leaves.items():
        data, ref = leaf_ref(leaf)
        target = root / ref.leaf
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        products[product_id] = ref
    new = next_root(
        previous,
        products=products,
        event=Event(actor="tests", reason="test", kind=kind),
        producer=PRODUCER,
        now=now,
        embargo=embargo,
        restore_epoch=restore_epoch,
    )
    data = encode(new)
    create_exclusive(root / root_path(new.seq), data)
    if update_current:
        set_current(root, new.seq, sha256_hex(data), new.restore_epoch)
    return new


def set_current(root: Path, seq: int, sha256: str, restore_epoch: int = 0) -> None:
    """Write ``CURRENT`` (a test may write any value, to exercise the reader)."""
    (root / CURRENT_NAME).write_bytes(encode(Current(seq=seq, root_sha256=sha256, restore_epoch=restore_epoch)))


def leaf_of(root: Path, product_id: str) -> Leaf | None:
    """The product's leaf in the newest version, or None."""
    inventory = FullInventory(local_fetch(root))
    try:
        inventory.refresh()
    except ValueError:
        return None
    return inventory.leaf(product_id) if product_id in inventory.products() else None
