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
  `TransitionError`, naming the `Rule`.
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
    HISTORY_DIR,
    HISTORY_FORMAT,
    HISTORY_NAME,
    INVENTORY_FORMAT,
    OPTED_IN_CATALOG_FORMAT,
    CatalogDecoder,
    CatalogDocument,
    CatalogHistory,
    ChangeItem,
    Current,
    EntryItem,
    EntryRecord,
    Event,
    EventKind,
    History,
    InventoryError,
    Kind,
    Leaf,
    LeafEntry,
    LeafHashes,
    LeafRef,
    Lifecycle,
    Marker,
    PartialRecord,
    Root,
    SegmentHeader,
    SegmentKind,
    SegmentLine,
    State,
    StructureSource,
    Tombstone,
    decode_catalog,
    decode_current,
    decode_history,
    decode_leaf,
    decode_root,
    decode_segment,
    encode,
    encode_segment,
    leaf_path,
    root_path,
    segment_line,
    segment_path,
    sha256_hex,
    validate_leaf,
    validate_root,
)
from sdmxlib.inventory._reader import Fetch, FullInventory, LiveEdition, LiveInventory, local_fetch
from sdmxlib.inventory._rules import (
    CREATED_AT_STEP,
    MAX_BASE_AHEAD,
    Rule,
    TransitionError,
    cancel,
    change_list,
    due,
    entry_record,
    head,
    leaf_ref,
    next_created_at,
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
    "CREATED_AT_STEP",
    "CURRENT_NAME",
    "HISTORY_DIR",
    "HISTORY_FORMAT",
    "HISTORY_NAME",
    "INVENTORY_FORMAT",
    "MAX_BASE_AHEAD",
    "OPTED_IN_CATALOG_FORMAT",
    "CatalogDecoder",
    "CatalogDocument",
    "CatalogHistory",
    "ChangeItem",
    "Current",
    "EntryItem",
    "EntryRecord",
    "Event",
    "EventKind",
    "Fetch",
    "FullInventory",
    "History",
    "InventoryError",
    "Kind",
    "Leaf",
    "LeafEntry",
    "LeafHashes",
    "LeafRef",
    "Lifecycle",
    "LiveEdition",
    "LiveInventory",
    "Marker",
    "PartialRecord",
    "Root",
    "Rule",
    "SegmentHeader",
    "SegmentKind",
    "SegmentLine",
    "State",
    "StructureSource",
    "Tombstone",
    "TransitionError",
    "cancel",
    "change_list",
    "decode_catalog",
    "decode_current",
    "decode_history",
    "decode_leaf",
    "decode_root",
    "decode_segment",
    "due",
    "encode",
    "encode_segment",
    "entry_record",
    "head",
    "leaf_path",
    "leaf_ref",
    "local_fetch",
    "next_created_at",
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
    "segment_line",
    "segment_path",
    "sha256_hex",
    "supersede_pending",
    "validate_leaf",
    "validate_root",
]


def schema(
    document: Literal[
        "catalog", "current", "root", "leaf", "catalog-2.0", "root-1.2", "history", "segment-header", "segment-line"
    ],
) -> bytes:
    """The shipped JSON Schema for one Inventory document (``schemas/<document>.schema.json``).

    Each is generated from its model, with the conditional rules msgspec
    cannot state from a type merged in. ``catalog`` and ``root`` describe
    every document of their major; ``catalog-2.0`` is an opted-in
    ``catalog.json``, and ``root-1.2`` a root with the history fields
    required. ``history`` is ``inventory/HISTORY``, and ``segment-header``
    and ``segment-line`` a history segment's lines (spec §6.1-§6.3, §6.9).
    """
    return resources.files(__package__).joinpath("schemas", f"{document}.schema.json").read_bytes()
