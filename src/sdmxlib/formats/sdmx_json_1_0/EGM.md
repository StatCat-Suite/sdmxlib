# sdmxlib.formats.sdmx_json_1_0

## Overview

This package is the SDMX-JSON 1.0 writer behind the REST `/availability`
endpoint. It writes an SDMX 3-style `DataConstraint` as an SDMX-JSON 1.0
`contentConstraint`, which adds a `type` field and uses `isIncluded`. The DTOs
are msgspec Structs encoded with the shared `formats/sdmx_json/_dto.py`
`ENCODER`.

## Interface

- `write_contentconstraint(cc, *, prepared=None, message_id="SDMXLIB",
  sender_id="SDMXLIB", pretty_print=False)` (`writer.py`): returns SDMX-JSON 1.0
  `meta`/`data`/`errors` bytes with the constraint under
  `data.contentConstraints`.
- `ContentConstraintJson`, `CubeRegionJson`, `KeyValueJson`,
  `ConstraintAttachmentJson` and `SetReferenceJson` (`writer.py`): the wire
  DTOs.
- `_unsayable_in_1_0` and `_unsayable_attachment` (`writer.py`): collect the
  content that SDMX-JSON 1.0 cannot express.

## Invariants

- Output is deterministic: key values are sorted by dimension id, and each
  value list is sorted.
- No wall-clock timestamp is written. `meta.prepared` appears only when the
  caller passes `prepared`.
- Content that 1.0 cannot express raises `ValidationError(issues)` before
  anything is written. A date-time period with no time zone is one of those
  issues (`zoneless_periods`).
- A naive `prepared` raises `ValueError`, since SDMX-JSON requires a zone.

## Side Effects

- None. The writer is a pure function that returns bytes.

## Verification

- Run: `just test` (unit tests) and `just check` (lint and typecheck).
