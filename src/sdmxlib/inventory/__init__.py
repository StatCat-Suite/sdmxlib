"""The Inventory: which edition of each product in a catalog is live.

The sharded catalog spec's §6 defines it. sdmxlib owns its documents, their
validation, the state-transition rules and a reader; statcatlib writes it.

- **Documents** (`CatalogDocument`, `Current`, `Root`, `Leaf`, `LeafEntry`):
  ``catalog.json``, ``CURRENT``, ``inventory/<seq>.json`` and
  ``inventory/leaves/<sha256>.json``, with `encode` and the ``decode_*``
  functions, which refuse another major format and a document that breaks
  the format's rules. `schema` gives each document's shipped JSON Schema.
- **Rules** (`record`, `release`, `release_due`, `cancel`,
  `supersede_pending`, `rollback`, `retire`, `reinstate`, `repairable`,
  `next_root`):
  pure functions over a product's leaf that return the next one or raise
  `TransitionError`, naming the rule.
- **Reading** (`LiveInventory`, `FullInventory`): the read protocol over a
  ``fetch(path) -> bytes | None`` callable, since sdmxlib holds no blob
  client. `LiveInventory` holds only live entries; `FullInventory` every
  state, for the release job and the operational objects.
"""

from importlib import resources
from typing import Literal

from sdmxlib.inventory._format import (
    CATALOG_FORMAT,
    CATALOG_NAME,
    CURRENT_NAME,
    INVENTORY_FORMAT,
    CatalogDocument,
    Current,
    Event,
    EventKind,
    InventoryError,
    Kind,
    Leaf,
    LeafEntry,
    LeafRef,
    Lifecycle,
    PartialRecord,
    Root,
    State,
    StructureSource,
    Tombstone,
    decode_catalog,
    decode_current,
    decode_leaf,
    decode_root,
    encode,
    leaf_path,
    root_path,
    sha256_hex,
    validate_leaf,
    validate_root,
)
from sdmxlib.inventory._reader import Fetch, FullInventory, LiveEdition, LiveInventory, local_fetch
from sdmxlib.inventory._rules import (
    TransitionError,
    cancel,
    due,
    head,
    leaf_ref,
    next_root,
    record,
    reinstate,
    release,
    release_due,
    repairable,
    retire,
    rollback,
    supersede_pending,
)

__all__ = [
    "CATALOG_FORMAT",
    "CATALOG_NAME",
    "CURRENT_NAME",
    "INVENTORY_FORMAT",
    "CatalogDocument",
    "Current",
    "Event",
    "EventKind",
    "Fetch",
    "FullInventory",
    "InventoryError",
    "Kind",
    "Leaf",
    "LeafEntry",
    "LeafRef",
    "Lifecycle",
    "LiveEdition",
    "LiveInventory",
    "PartialRecord",
    "Root",
    "State",
    "StructureSource",
    "Tombstone",
    "TransitionError",
    "cancel",
    "decode_catalog",
    "decode_current",
    "decode_leaf",
    "decode_root",
    "due",
    "encode",
    "head",
    "leaf_path",
    "leaf_ref",
    "local_fetch",
    "next_root",
    "record",
    "reinstate",
    "release",
    "release_due",
    "repairable",
    "retire",
    "rollback",
    "root_path",
    "schema",
    "sha256_hex",
    "supersede_pending",
    "validate_leaf",
    "validate_root",
]


def schema(document: Literal["catalog", "current", "root", "leaf"]) -> bytes:
    """The shipped JSON Schema for one Inventory document (``schemas/<document>.schema.json``)."""
    return resources.files(__package__).joinpath("schemas", f"{document}.schema.json").read_bytes()
