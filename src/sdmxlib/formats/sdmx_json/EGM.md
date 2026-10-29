# sdmxlib.formats.sdmx_json

## Overview

This package reads and writes SDMX-JSON 2.0 structure and metadata messages,
and SDMX-JSON data messages in versions 1 and 2. It uses typed msgspec Structs
derived from `_dto.WireStruct` and encodes everything through the shared
`_dto.ENCODER`. The structure, metadata and data readers and writers each live
in their own module. The package's `__all__` exports only the TypedDict value
serialisers in `structure_values.py`.

## Interface

- `read(data, *, registry=None) -> StructureMessage` (`reader.py`): parses
  structure bytes inside `document_binding(index)`. An artefact that
  contradicts the schema is refused into `msg.refused`.
- `write(msg, *, pretty_print=False, metadata_links=None, base_url=None) ->
  bytes` (`writer.py`): applies `without_dropped(msg, "json")`, then calls
  `write_kept`.
- `write_kept(msg, ...)` (`writer.py`): applies `with_derived_targets`,
  expands `%` wildcard codelists into the codes they match, runs
  `_refuse_unwritable`, then encodes `{meta, data}` with `ENCODER`.
- `read(data) -> MetadataMessage` and `write(msg, *, pretty_print=False) ->
  bytes` (`metadata.py`): metadata messages, through `MetadataMessageJson`,
  `MetadataSetJson` and `ReportedAttributeJson`. `write` stamps `$schema`
  with `METADATA_SCHEMA_URL`, the 2.1 metadata schema URL. `read` records in
  `MetadataMessage.lost` a `meta` it cannot build and each metadata set's
  keys no struct declares (`_losses.undeclared`, walked eagerly).
- `read_observations(source) -> (pl.DataFrame, dataflow_urn | None)`
  (`data.py`): reads a data message of either version through the `_Envelope`
  Struct. `data_v1.py` and `data_v2.py` each provide `write_sdmx_json_data`
  and `read_observations` for their version. Both read every data set
  through `read_data_sets` (`_data_common.py`); a 2.0 data set is decoded
  against the structure its `structure` index names. The data sets read as
  one frame when they decode to the same columns and name at most one
  dataflow; a `Delete` data set, differing columns, two dataflows or a
  structure index outside `data.structures` raise `SdmxParseError` (GH-264).
  Each data set's `setID` and reporting and validity dates are not read
  (GH-284).
- `ENCODER`, `WireStruct`, `_camel_rename`/`_WIRE_NAMES`,
  `emit_istr`/`ingest_istr`, `emit_annotations`/`ingest_annotations`, `Link`,
  `Annotation`, `Meta` and `Sender` (`_dto.py`): the shared wire layer.
  `_camel_rename` maps snake_case names to camelCase on the wire, with
  `agencyID` and `isMultiLingual` as special cases.
- `to_meta`, `from_meta`, `from_metadata_meta`, `MetaJson` and `PartyJson`
  (`_header.py`): convert a `Header` to and from the `meta` envelope.
  `from_meta` can return a `ReadGap`.
- `ComponentTable`, `AnnotationPool`, `encode_batch`, `decode_observations`, `read_data_sets`
  and `rows_to_lazyframe` (`_data_common.py`): code shared by the data
  messages.
- `raw_sections`, `unread_sections`, `undeclared` and `assumed`
  (`_losses.py`): record the sections and fields the reader did not read, and
  the assumptions it made.
- `serialize_code`, `serialize_codes`, `serialize_concept` and
  `serialize_concepts` (`structure_values.py`): the public `__all__`; each
  returns TypedDicts.
- `resolve_urn` and `resolve_any_urn` (`_resolve.py`), and `zoneless_periods`
  (`_periods.py`): resolve URNs to Refs, and find date-time periods that have
  no time zone.

## Invariants

- No module here imports stdlib `json` or `orjson`. Encoding goes through
  `_dto.ENCODER` and decoding through typed `msgspec.json.decode` or
  `msgspec.convert`.
- `WireStruct` sets `omit_defaults=True` and `gc=False`. Wire DTOs must
  therefore be acyclic trees, because with `gc=False` a reference cycle leaks.
- `id` and `agencyID` are declared required, with no default. `version` is
  optional on `MaintainableJson`; a missing version is held as 1.0 and
  recorded as a `ReadAssumption`, except on the versionless organisation
  schemes.
- Unknown wire fields are skipped on decode and recorded as `ReadGap`s on
  `msg.lost`. Unknown top-level sections are refused.
- `msg.lost` is lazy. The message holds the document's bytes until `lost` is
  first read, and the walk for undeclared fields runs then.
- When the one typed decode of the document fails, each entry is decoded on
  its own, and an entry its struct cannot decode is refused alone.
- `write` passes the message through `without_dropped(msg, "json")`.
  `_refuse_unwritable` then raises `ValidationError` for a version that fails
  `check_writable(..., "json")`, or for a constraint with a date-time period
  that has no zone. Both checks finish before any encoding.
- Pretty printing uses `msgspec.json.format(indent=2)`.
- A singular `name` with no `names` map is read under the `_default`
  language.
- `read_observations` (`data.py`) raises `SdmxParseError` for bytes that are
  not a readable data message, with msgspec's error as `__cause__`: a wrong
  shape, an observation key or attribute position outside its declaration, a
  repeated component id, or the series layout, which the reader does not
  support. Each column is built with `pl.Series(..., strict=False)`, so a
  measure of integers and decimals is one `Float64` column.

## Side Effects

- `reader.read` with `registry=` calls `registry.add_all(msg.all_artefacts())`.
- `reader.read` binds Refs to the document's index through
  `document_binding`.
- The data readers call `Path(source).read_bytes()` when given a path.
- `write` raises `ValueError` when `metadata_links` is given without
  `base_url`, and `ValidationError` for content it cannot write.

## Verification

- Run: `just test` (unit tests) and `just check` (lint and typecheck).
