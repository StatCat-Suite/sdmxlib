# sdmxlib.edition

## Overview

`sdmxlib.edition` seals, checks, copies, builds and fetches editions. A
product is a directory holding one subdirectory per edition, named by its
edition id, and a `data/` directory that every edition shares.
`seal` completes an edition that `LocalRegistry.build` started by
writing `manifest.json`. The manifest holds a file inventory (sha256,
md5 and crc32c for each file), per-binding counts, artefact fingerprints and a
content fingerprint. `check_edition` and `check_on_open` verify an edition
against its manifest. `docs/edition.md` specifies the format. statcatlib
seals through `seal(extensions=...)`.

## Interface

- `seal` (`_write.py`): seals a started edition. Writes the
  manifest last and refuses an edition that already has one.
- `digests_of` and `sha256_of` (`_write.py`): sha256, md5 and crc32c
  (`google_crc32c`) of a file, from one chunked read.
- `inventory`, `lake_files` and `tier_of` (`_write.py`): the edition's file
  inventory, the data files DuckLake references, and each file's placement
  tier (by longest prefix match).
- `settle_lake` (`_write.py`): flushes inlined data and rewrites delete files.
  For a final edition it also merges adjacent files, and on request it
  expires snapshots.
- `check_edition` (`_check.py`): checks an edition against its manifest under the
  profile `at="publish"`, `"receive"` or `"open"` and returns a `Report`.
  `Report.ok` says whether it passed. `Check` is the type an extension check
  implements, and `Profile` is the profile literal. Findings are `Finding`s
  (`_finding.py`). An argument the profile does not use (`structures=` at
  `open`, `remote=` at `publish`, `lookup=` or `fingerprints=` outside
  `receive`) raises `ValueError`.
- `check_on_open` (`_check.py`): runs one profile per call. Without a stamp
  for this `(product_id, edition id, manifest sha256)` it runs `receive` and writes
  the stamp when the report is clean; with one it runs `open`. An unreadable
  manifest, or a `product_id` that cannot name a stamp path, gets a `receive`
  check and no stamp.
- `copy_edition` and `edition_files` (`_copy.py`): copy the files of the chosen
  tiers and verify each one, and list the paths such a copy needs.
- `verify_remote`, `RemoteFile`, `content_md5` and `content_crc32c`
  (`_remote.py`): check files in an object store by size and digest. The
  digests are the base64 values used in headers.
- `build_edition` (`_build.py`): builds an edition from dataflow closures and
  producer observations. With `partials=True` (the default) it cuts codelists
  down to partials.
- `fetch_edition` (`_fetch.py`): fetches one dataflow over SDMX REST into a new
  sealed edition that records its `Provenance`. It calls `build_edition`
  with `partials=False`.
- `apply_overlays`, `fold_overlays`, `effective_token` and `read_overlay`
  (`_overlay.py`): display-annotation overlays. They are applied at read time
  or folded into a new edition, which keeps each partial's `partial_of`
  and takes the `lifecycle` to publish with (GH-272), and `effective_token`
  gives the edition id
  caches key on. `Overlay`, `OverlayEntry`, `OverlayAnnotation`,
  `OverlayText`, `OverlayError`, `Merge` and `MergeHook` are the overlay
  types; `applied`, `overlay_digest` and `OVERLAY_FORMAT` are exported with
  them.
- `canonical_json` and `CANONICAL_FORM` (`_canonical.py`): the canonical
  artefact text that fingerprints hash. `table_fingerprint`,
  `artefact_fingerprint`, `bundle_fingerprint` and `References`
  (`_fingerprint.py`) compute the fingerprints and the reference closure.
  `structure_fingerprint` (internal) hashes a dataflow's closure for each
  `BindingCount`.
- `survey` and `NonCode` (`_availability.py`, internal): scan one binding for
  its availability and for coded-column values outside their enumeration.
  `seal` and `check_edition(at="publish")` both call it.
- Manifest types, re-exported from `storage._manifest`: `read_manifest`,
  `Manifest`, `Producer`, `Provenance`, `EditionFile`, `ArtefactEntry`,
  `BindingCount`, `Tier`, `ManifestError`, `StoreFormatError`, and the
  constants `DATA_PATH`, `MANIFEST_NAME`, `SIGNATURE_NAME`, `FORMAT_VERSION`
  and `DUCKLAKE_SPEC_VERSION`. `Lifecycle` (`_write.py`) is the edition
  lifecycle literal.
- `manifest_schema` and `overlay_schema` (`__init__.py`): the shipped JSON
  Schemas (`manifest.schema.json`, `overlay.schema.json`) as bytes.

## Invariants

- An edition is never rewritten. `seal` raises `ManifestError`
  when `manifest.json` already exists.
- `seal` requires a `edition_id` in the meta store, which
  `LocalRegistry.build` sets. It then works in this order: `settle_lake` (when
  `tables.ducklake` exists), `clear_availability`, the binding survey (when
  `tables.ducklake` exists), the `*.wal` refusal, `compact_for_publication`,
  and the manifest. A `*.wal` file means a builder still has a database open
  for writing. An edition refused at the survey or at the `*.wal` check is
  left settled with its availability cleared; a rerun after fixing the cause
  redoes both.
- `seal` and `copy_edition` both write the manifest to a `.tmp` file
  and rename it into place.
- Data files are always tier 3. `metadata.duckdb` and `tables.ducklake` default to
  tier 1, and other files to tier 2.
- `copy_edition` skips a `data/` file already present with the same size and
  digest, raises when a copied file does not match, and refuses a destination
  that is already sealed.
- `check_on_open` writes a stamp only after a clean `receive` check, and only
  to a path made of exactly two plain components under the stamps directory.
- `settle_lake` never deletes files from `data/`.
- `check_edition`'s `tags` check, at `publish` and `receive`, reports every
  stored language tag `normalize_tag` would change or refuse: a store built
  before GH-280 from a source that sent one.
- An edition with a lake is refused when a coded column holds a value
  outside its codelist. The survey scans every binding before refusing, and
  stores availability only when nothing is refused.

## Side Effects

- `seal` writes `manifest.json` and calls
  `storage._trim.compact_for_publication`, which rewrites `metadata.duckdb` into a
  fresh file.
- `seal` and `settle_lake` run DuckLake procedures
  (`flush_inlined_data`, `rewrite_data_files`, `merge_adjacent_files`,
  `expire_snapshots`), which write new data files to `data/`.
- `seal` clears per-binding availability in `metadata.duckdb` and, when
  availability is enabled, stores it again.
- `copy_edition` creates directories and copies files with `shutil.copyfile`,
  then copies the detached signature and the manifest last.
- `check_on_open` writes the JSON stamp `<stamps>/<product_id>/<edition_id>` through
  a `.partial` file and a rename.
- `fetch_edition` makes SDMX REST requests and creates a product directory.
  `verify_remote` and `check_edition(remote=...)` read from object storage.

## Verification

- Run: `just test` (unit tests) and `just check` (lint and typecheck).
- `just ci` includes `bench-bundle-assertions`, which checks the published
  census edition named by `$SDMXLIB_BENCH_BUNDLE`.
