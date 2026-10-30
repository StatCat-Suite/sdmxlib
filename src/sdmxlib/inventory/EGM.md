# sdmxlib.inventory

## Overview

`sdmxlib.inventory` holds a catalog's Inventory: which edition of each
product is live (sharded catalog spec §6; GH-251). sdmxlib owns the
documents, their validation, the state-transition rules and a reader;
statcatlib owns the writer. sdmxlib holds no blob client: the reader takes a
`fetch(path) -> bytes | None` callable.

## Interface

- Documents (`_format.py`): `CatalogDocument` (`catalog.json`), `Current`
  (`CURRENT`), `Root` (`inventory/<seq>.json`), `Leaf` and `LeafEntry`
  (`inventory/leaves/<sha256>.json`), with `Event`, `LeafRef`, `Tombstone`,
  `StructureSource` and `PartialRecord`. `encode` writes one; `decode_catalog`,
  `decode_current`, `decode_root` and `decode_leaf` read one, refusing
  another major format with `InventoryError`; `validate_root` and
  `validate_leaf` check the format's own rules. `root_path(seq)`,
  `leaf_path(sha256)` and `sha256_hex` name and address them.
  `INVENTORY_FORMAT` and `CATALOG_FORMAT` are `"1.0"`.
- `schema(document)` (`__init__.py`): the shipped JSON Schema of `catalog`,
  `current`, `root` or `leaf`, under `schemas/`.
- Rules (`_rules.py`): `head`, `record`, `release`, `due`, `release_due`,
  `cancel`, `supersede_pending`, `rollback`, `retire`, `reinstate`,
  `leaf_ref` and `next_root`. Each takes a product's current leaf and
  returns the next, or raises `TransitionError` with its `rule`.
- Reading (`_reader.py`): `LiveInventory(fetch, schema_version=...)` and
  `FullInventory(fetch)`, each with `refresh()`, `seq`, `restore_epoch` and
  `skipped`; `LiveInventory.editions()`, `products()` and `head(product)`
  return `LiveEdition`s; `FullInventory.root`, `embargo`, `retired`,
  `products()`, `leaf()`, `leaves()` and `entries()`. `local_fetch(dir)`
  is a `Fetch` over a directory.

## Invariants

- A leaf lists its editions sorted by id, at most one `live` per
  `schema_version`, and a `release_at` only on an `embargoed` entry.
- An edition becomes `live` only when built on the live edition at its
  schema, or, with none there, on nothing or on a live edition at another
  schema (fast-forward). A rollback names the live edition it expects. A
  tombstoned edition is recorded again only by `reinstate`. Every
  transition time is time-zone aware.
- A reader never accepts a lower `seq` than one it holds unless
  `restore_epoch` changed; it verifies the root `CURRENT` names, falls back
  on a mismatch, probes `seq + 1` onward without listing, and skips an
  invalid root into `skipped`. A leaf must hash to its name.
- `LiveInventory` holds only `live` entries at its schema version and
  exposes no leaf, so a public reader has nothing embargoed to filter.

## Side Effects

- None: the rules are pure, and the readers read only through `fetch`,
  caching leaves by hash.

## Verification

- `uv run pytest -m unit tests/unit/test_inventory.py`; the writer in
  `tests/support/inventory.py` follows the spec's write protocol.
