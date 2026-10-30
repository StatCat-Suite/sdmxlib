# sdmxlib.formats

## Overview

`sdmxlib.formats` owns all SDMX wire-format parsing and serialisation; the
model imports nothing from it. `__init__.py` works out which format a message
uses (SDMX-ML 2.1, 3.0 or 3.1, or SDMX-JSON) and whether it is a
`StructureMessage` or a `MetadataMessage`, then calls the matching reader or
writer. It refuses SDMX Error messages. The helper modules are shared by the
XML and JSON readers and writers. `gaps.py` is the one table listing what each
target format cannot carry.

## Interface

- `read(source, *, version=None, registry=None, strict=True, lossless=False)`
  (`__init__.py`): reads a path or bytes and returns a `StructureMessage` or a
  `MetadataMessage`.
- `parse(data, *, version, registry, strict, lossless)` (`__init__.py`):
  parses bytes, detecting the version and message kind. A `ValidationError`
  raised outside any per-artefact refusal becomes an `SdmxParseError`. With
  `lossless=True` it raises `LossyReadError` for a `StructureMessage` with
  `lost` or `assumed` entries, and for a `MetadataMessage` with `lost`
  entries.
- `serialize(msg, *, version="2.1", pretty_print=False, validate=True)`
  (`__init__.py`): writes bytes, picking the writer from the message type.
  For a `StructureMessage` it raises `ValueError` for an unknown version, then
  runs `without_dropped(msg, version)`, then validates.
  `to_xml(msg, *, version="2.1", ...)` writes SDMX-ML, and always writes a
  `MetadataMessage` as 3.0. `to_json(msg, ...)` writes SDMX-JSON 2.0.
- `frame_of(batch)` (`_arrow.py`): Arrow `RecordBatch` to a polars DataFrame,
  zero-copy through `pl.from_arrow` where possible.
- `match_dimension_case(holders, dimension_ids)` (`_attachment.py`): changes a
  dimension reference to the DSD's spelling when the two differ only in case.
- `to_enum`, `to_int`, `to_float`, `to_datetime` and `to_date(raw, *, what)`
  (`_coerce.py`): convert a scalar and raise `SdmxParseError` on bad input.
- `lookup(index, urn)` (`_index.py`): looks a URN up in a reader's index,
  finding item URNs inside their schemes.
- `check_positions(stated, *, element)` (`_positions.py`): raises
  `SdmxConformanceError` when a stated dimension position is not an integer
  or contradicts document order.
- `build_each`, `build_each_xml`, `refuse_xml` and `xml_identity`
  (`_refuse.py`): build one artefact per element and record a `Refusal` for
  each one that fails.
- `DataFormat` (`data_format.py`): StrEnum of the 11 SDMX data media types,
  with `.media_type`, `.base`, `.version` and `.suffix`.
- `RENDER_VERSION` and `render_version(media_type)` (`render.py`): a read-only
  map from media type to the first 16 hex digits of the sha256 of that
  format's golden file.
- `unrepresentable(artefact, target)`, `declared_gaps()`, `Gap` and `TARGETS`
  (`gaps.py`): what writing to `"2.1"`, `"3.0"`, `"3.1"`, `"json"` or
  `"storage"` would lose. `dropped(msg, target)`, `without_dropped`,
  `with_derived_targets`, `DroppedArtefact` and `availability_label` list the
  whole artefacts a write leaves out, each with the reason.
- Subpackages:
  - [`sdmx_ml/`](./sdmx_ml/EGM.md): the SDMX-ML code shared by all versions,
    including the v3 reader and writer and the closed-world check.
  - [`sdmx_ml21/`](./sdmx_ml21/EGM.md): the SDMX-ML 2.1 reader and writer.
  - [`sdmx_ml30/`](./sdmx_ml30/EGM.md): the SDMX-ML 3.0 bindings and
    `<mes:GenericMetadata>`.
  - [`sdmx_ml31/`](./sdmx_ml31/EGM.md): the SDMX-ML 3.1 bindings.
  - [`sdmx_json/`](./sdmx_json/EGM.md): SDMX-JSON 2.0 structure, metadata and
    data, on msgspec.
  - [`sdmx_json_1_0/`](./sdmx_json_1_0/EGM.md): the SDMX-JSON 1.0
    `contentConstraint` writer behind `/availability`.
  - [`sdmx_csv/`](./sdmx_csv/EGM.md): the SDMX-CSV 2.0.0 writer, and helpers
    that find a CSV file's structure.

## Invariants

- Each artefact build catches only `SdmxConformanceError` and
  `ValidationError`. Any other exception propagates, so a bug cannot silently
  shorten a message.
- A refused artefact is never partly built. References to it stay unresolved.
- A malformed value in a payload raises `SdmxParseError`, never a bare
  `ValueError`.
- Every language tag is read through `_tags.py`, so a malformed tag, an
  empty `xml:lang` or two texts of one tag refuse the artefact carrying them
  (GH-280). An SDMX-ML `xml:lang` has its whitespace collapsed, as the
  schema's `xs:language` does, and is English when absent. In a header, such
  a tag reads as no header and a `ReadGap`, as any header the model cannot
  hold does.
- Gaps in dimension positions are accepted, with a base of 0 or 1. Only
  positions that contradict document order, or a repeated position, are
  refused.
- A case-insensitive dimension match is applied only when it matches exactly
  one dimension.
- `gaps.py` is the single list of what each format cannot carry. The
  round-trip property tests read it (as strict xfails).
- `RENDER_VERSION` must match the golden files
  (`tests/unit/test_render_version.py`).
- `parse` catches lxml, msgspec and Unicode decode failures
  (`_SHELL_FAILURES`) at its entry point.

## Side Effects

- `read()` reads files with `Path.read_bytes`.
- `read` and `parse` with `registry=` call `add_all` on that registry, for a
  `StructureMessage` only, after the `strict` and `lossless` checks pass. With
  an `InMemoryRegistry`, the read binds its references to that registry's
  resolver as it builds.
- Refusals are appended to the `refused` list the caller passes in.

## Verification

- Run: `just test` (unit tests) and `just check` (lint and typecheck).
