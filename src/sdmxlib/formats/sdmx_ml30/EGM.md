# sdmxlib.formats.sdmx_ml30

## Overview

This package binds SDMX-ML 3.0. `reader.py` and `writer.py` are thin wrappers
that bind the shared `V3Reader` and `V3Writer` (with `V3_0`) from
[`../sdmx_ml/EGM.md`](../sdmx_ml/EGM.md) to the 3.0 namespaces at import.
`metadata.py` is a standalone reader and writer for `<mes:GenericMetadata>`
messages, which carry MetadataSets.

## Interface

- `read(data, *, registry=None) -> StructureMessage` (`reader.py`): calls the
  module-level `_READER`, a `V3Reader`.
- `write(msg, *, pretty_print=False, metadata_links=None, base_url=None) ->
  bytes` (`writer.py`): calls `_WRITER`, a `V3Writer` with
  `generation=V3_0`. `metadata_links` emits `<com:Link rel="metadata">`.
- `write_kept(msg, *, pretty_print=False) -> bytes` (`writer.py`): writes a
  message that `without_dropped` has already narrowed.
- `metadata.write(msg: MetadataMessage, *, pretty_print=False) -> bytes` and
  `metadata.read(data) -> MetadataMessage` (`metadata.py`): GenericMetadata.
  `read` raises `SdmxParseError` itself for a set with no structure ref and
  for a bad timestamp. lxml errors and `ValidationError`s propagate to
  `formats.parse`, which converts them.
- `MES`, `STR`, `COM`, `MD`, `XML_LANG`, `NS` and `STRUCTURE_ROOT_NS`
  (`namespaces.py`): the 3.0 namespace URIs.

## Invariants

- The reader and writer are stateless singletons, bound at import.
- In GenericMetadata, references are plain URN strings instead of `<Ref>`
  elements. The writer puts the MSD URN in
  `mes:Header/mes:Structure/com:Structure` when the set's structure ref
  resolves through to an MSD. Otherwise it writes the ref's own URN, in the
  element `_HEADER_ELEMENT` picks by class; a `MetadataProvisionAgreement` URN
  falls to `com:Structure`.
- `mes:MetadataSet` is in the message namespace and its children are in `md:`.
  `md:Attribute` nests recursively, and a repeated attribute is written as
  sibling attributes with the same id.
- The reader takes an attribute value from `md:Value`, `com:Text` or
  `com:StructuredText`. The writer emits only `md:Value` or `com:Text`, so a
  `com:StructuredText` value is written back as `com:Text`.
- `metadata.read` reads a header it cannot build as `None` and records its
  `ReadGap` in `MetadataMessage.lost` (GH-288).

## Side Effects

- Creates the `_READER` and `_WRITER` singletons at import.
- `read` with `registry=` calls `add_all` on that registry.

## Verification

- Run: `just test` (unit tests) and `just check` (lint and typecheck).
