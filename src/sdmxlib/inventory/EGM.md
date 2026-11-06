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
  `validate_leaf` check the format's own rules. `decode_root` checks every
  rule `validate_root` does except the event kind. `EventKind` is the set a
  writer records (`EVENT_KINDS`, `check_event_kind`); `Event.kind` is a
  `str`. `root_path(seq)`,
  `leaf_path(sha256)` and `sha256_hex` name and address them.
  `INVENTORY_FORMAT` is `"1.2"`, `CATALOG_FORMAT` `"1.0"`.
- History documents (`_format.py`, Inventory 1.2, spec §6.1-§6.3, §6.9;
  GH-361): a root's history fields `previous_root_sha256` and `changes`
  (`ChangeItem`, `LeafHashes`, `EntryItem` with its `Marker`, `EntryRecord`),
  unset when absent, tested by `Root.has_history`; `CatalogDocument.history`
  (`CatalogHistory`), set exactly in `catalog.json` 2.x; `History`
  (`inventory/HISTORY`); `SegmentHeader` and `SegmentLine`. `decode_catalog`
  takes `decoder=`: a reader and a stage-2 writer read majors 1 and 2, a
  `stage-1-writer` refuses 2.x. `decode_history`, `decode_segment`,
  `encode_segment`, `segment_line` and `segment_path` read and write them.
- Routing fields (`_format.py`, `_routing.py`, spec §6.3, §7.3, §10.3;
  GH-348): `LeafEntry.dataflows` and `pull_bytes`, unset when absent, tested
  by `LeafEntry.has_routing`; `LeafEntry.full_artefacts`, the records without
  `partial_of`. `routing_fields(manifest)` computes `RoutingFields`
  (`dataflows`, `pull_bytes`, `artefacts`, the last by `manifest_records`)
  for a writer. `route(edition, manifest)` gives a `LiveEdition`'s `Route`
  (`dataflows`, `pull_bytes`, `homes`), calling `manifest()` only for a field
  the entry lacks and refusing bytes that do not hash to `manifest_sha256`.
  `verify_entry(entry, manifest)` lists each filled field that differs from
  the manifest, for statcatlib's `verify-catalog`. `ShardRouter` is
  statcat-serve's.
- `schema(document)` (`__init__.py`): the shipped JSON Schema of `catalog`,
  `current`, `root`, `leaf`, `catalog-2.0`, `root-1.2` (history fields
  required), `history`, `segment-header` or `segment-line`. `generate_schemas`
  builds all nine from the models, merging in the conditional rules msgspec
  cannot state from a type (`_DEFINITION_RULES`, `_SCHEMAS`).
- Rules (`_rules.py`): `head`, `record`, `release`, `due`, `release_due`,
  `cancel`, `supersede_pending`, `rollback`, `retire`, `reinstate`,
  `leaf_ref` and `next_root`. Each takes a product's current leaf and
  returns the next, or raises `TransitionError` with its `rule`, a `Rule`
  (exported, GH-339). `record` and `reinstate` take `artefacts`,
  `dataflows` and `pull_bytes`; every other rule copies them.
- History rules (`_rules.py`, spec §6.6): `change_list` diffs the base
  version, root `seq - 1`, and its leaves against the new version;
  `next_created_at` keeps `created_at` at least `CREATED_AT_STEP` past the
  base's and refuses a base more than `MAX_BASE_AHEAD` ahead; `next_root`
  takes `previous_bytes` and `changes` together and writes both history
  fields; `entry_record` is a change item's copy of a `LeafEntry`.
- Reading (`_reader.py`): `LiveInventory(fetch, schema_version=...,
  leaf_threads=...)` and `FullInventory(fetch, leaf_threads=...)`
  (`DEFAULT_LEAF_THREADS`, 64), each with `refresh()`, `seq`, `restore_epoch`
  and `skipped`; `LiveInventory.editions()`, `products()` and `head(product)`
  return `LiveEdition`s, which carry the entry's `dataflows`, `pull_bytes`
  and `artefacts` (None when absent); `FullInventory.root`, `embargo`,
  `retired`, `products()`, `leaf()`, `leaves()` and `entries()`. `local_fetch(dir)`
  is a `Fetch` over a directory. A `Fetch` may be called from several threads
  at once.
- `repairable(journal, leaves, retired)` (`_rules.py`): the seal-journal
  entries the narrow repair (spec §6.7) may record: those the newest version
  neither names nor tombstones.
- Conformance (`conformance.py`, GH-252): a `Backend` protocol (`head`,
  `commit` raising `ConflictError` on a stale base, `commit_interrupted`,
  `reader`, `hint_behind`, `restore_to`, `restore_with_new_epoch`), the
  `Snapshot` it returns, the checks in `CHECKS`, and `run(new)`, which runs
  every check against fresh backends. An implementation of the protocol
  (statcatlib's writer) runs it as a test. A writer of the history fields
  also provides a `HistoryBackend` (`stage_2`, `fetch`, `put`, `commit_at`)
  and runs `run_history`, the stage-1 checks in `HISTORY_CHECKS`. The §6.9
  cases for the lock, opt-in, expiry, restores and `verify-catalog` wait for
  stage 2.

## Invariants

- A leaf lists its editions sorted by id, at most one `live` per
  `schema_version`, and a `release_at` only on an `embargoed` entry.
- An edition becomes `live` only when built on the live edition at its
  schema, or, with none there, on nothing or on a live edition at another
  schema (fast-forward). A rollback names the live edition it expects. A
  tombstoned edition is recorded again only by `reinstate`. Every
  transition time is time-zone aware.
- A reader never accepts a lower `seq` than one it holds unless
  `restore_epoch` changed; it verifies the root `CURRENT` names, probes
  `seq + 1` onward without listing, and skips an invalid root into
  `skipped`. A leaf must hash to its name.
- `LiveInventory.editions()`, `LiveInventory.products()` and
  `FullInventory.leaves()` make one GET per leaf the reader does not hold. With
  two or more to fetch and `leaf_threads` above 1, the GETs run on a pool that
  ends before the call returns or raises. Only `fetch` runs on a worker: the
  calling thread checks, decodes and holds each leaf in sorted product order.
  The first bad leaf in that order raises and the leaves before it stay held,
  as in a sequential read. Fetches in flight then finish, and fetches not yet
  started are cancelled. A read of one product fetches in the calling thread.
- With `CURRENT` present and verifying, a reader fetches only `CURRENT` and
  roots. Otherwise it reads `catalog.json` and, on 2.x, `inventory/HISTORY`;
  takes the epoch from `HISTORY` (2.x) or `catalog.json` (1.x); bounds its
  walk back and starts its probe at max(held `seq` in that epoch,
  `expired_through`); drops a held root at or below `expired_through`; and
  fails closed with no root above the bound and none held, or on 2.x
  without `HISTORY` (spec §6.6).
- A leaf entry carries both routing fields or neither; `dataflows` is sorted
  without duplicates and `pull_bytes` is at least 0. No rule tests
  `format_version` for them, so an entry without them decodes and routes
  from its manifest.
- A root carries both history fields or neither; `previous_root_sha256` is
  None exactly at version 1; changes are sorted by product, entry items by
  edition, and an entry item carries `entry` exactly when `to` is a state.
- A reader accepts any `event.kind` within format 1.x; `next_root` and
  `validate_root` refuse a kind outside `EventKind`, and the shipped root
  schema keeps that enum (GH-338).
- `LiveInventory` holds only `live` entries at its schema version and
  exposes no leaf, so a public reader has nothing embargoed to filter.

## Side Effects

- None on the catalog: the rules are pure, the readers read only through
  `fetch`, caching leaves by hash, and `route` reads only through the
  `manifest` callable it is given. A read of every product starts up to
  `leaf_threads` threads for its own duration.

## Verification

- `uv run pytest -m unit tests/unit/test_inventory.py tests/unit/test_inventory_history.py tests/unit/test_inventory_reader_fallback.py tests/unit/test_inventory_history_schemas.py tests/unit/test_inventory_conformance.py tests/unit/test_inventory_routing.py tests/unit/test_inventory_concurrent_leaves.py`.
  The conformance suite runs against two backends in
  `tests/support/inventory_backends.py`: the file protocol, written by the
  reference writer in `tests/support/inventory.py` (it writes the history
  fields and refuses `catalog.json` 2.x), and a prototype over a
  Delta table (delta-rs, a dev dependency), which proves the spec's escape
  route. A Delta reader lists its log, so `hint_behind` has nothing to do
  there. Sabotaged backends show each check, and each history check, can fail.
