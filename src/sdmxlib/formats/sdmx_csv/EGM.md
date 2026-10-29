# sdmxlib.formats.sdmx_csv

## Overview

This package holds a streaming SDMX-CSV 2.0.0 data writer and reader helpers
that read only the first row of a file to find its structure: the `DATAFLOW`
column in 1.0, or `STRUCTURE`/`STRUCTURE_ID` in 2.0. Reading the observations
is the registry's job (`scan_csv`). The package exports only
`write_sdmx_csv`.

## Interface

- `write_sdmx_csv(batches, dsd, *, structure_id, structure="dataflow",
  action="I")` (`writer.py`): a generator yielding SDMX-CSV 2.0.0 byte chunks
  from Arrow batches. `structure_id` is an `AGENCY:ID(VERSION)` string or a
  `Dataflow`, `DataStructure` or `ProvisionAgreement`, from which both the id
  and `structure` are derived. Raises `ValueError` when `structure` is not in
  `STRUCTURE_KINDS`.
- `STRUCTURE_KINDS` (`reader.py`): the three `STRUCTURE` values SDMX-CSV
  allows, `"dataflow"`, `"datastructure"` and `"dataprovision"`.
- `parse_dataflow_id(dataflow_id)` (`reader.py`): splits
  `"AGENCY:ID(VERSION)"` into a tuple and raises `ValueError` when it is
  malformed.
- Package-internal helpers in `reader.py`: `_peek_dataflow_id` (the 1.0
  `DATAFLOW` value, or `STRUCTURE_ID` when there is no `DATAFLOW` column),
  `_peek_structure` (the 2.0 `STRUCTURE`/`STRUCTURE_ID` pair, raising
  `SdmxParseError` on a decode or CSV error), `_dataflow_ref` and
  `_resolve_structure`. `_resolve_structure` looks the name up through
  `SupportsGet`, follows provision agreement to dataflow to data structure,
  and returns `(DataStructure, Ref[Dataflow] | None)`. It raises `ValueError`
  when the file names no structure or an unknown kind, or when a step is
  missing, and `TypeError` when the lookup finds something other than a data
  structure.

## Invariants

- Columns are written in this order: `STRUCTURE`, `STRUCTURE_ID`, `ACTION`,
  then the dimensions, measures and attributes in DSD declaration order.
- The header appears once, in the first chunk.
- Every batch must contain every component the DSD declares. A missing one
  raises `KeyError`.
- Rows are written with polars `write_csv` (through `formats._arrow.frame_of`).
- The envelope columns name the structure and are never DSD components.

## Side Effects

- The reader helpers read only the first 4096 characters, so a first data
  row longer than that is truncated. They open files as UTF-8 and decode
  bytes with `errors="replace"`.
- The writer consumes its batch iterator lazily.

## Verification

- Run: `just test` (unit tests) and `just check` (lint and typecheck).
