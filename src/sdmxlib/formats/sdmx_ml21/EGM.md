# sdmxlib.formats.sdmx_ml21

## Overview

This package is the SDMX-ML 2.1 structure reader and writer (lxml), with the
translations between 2.1 and 3.x. Synthetic StructureSets and MSD report
layers, which the writer creates, carry marker annotations and are removed on
read. The writer merges metadata providers into data provider schemes, and the
reader splits them back out. The writer flattens extending codelists, and
converts a geographic codelist to a plain `Codelist` first only when it
extends others (`_plain`).

## Interface

- `read(data, *, registry=None) -> StructureMessage` (`reader.py`, re-exported
  by `__init__.py`): parses 2.1. Refs are bound to a per-document index with
  `document_binding`, and artefacts that contradict the schema go to
  `msg.refused`.
- `write(msg, *, pretty_print=False) -> bytes` (`writer.py`): applies
  `without_dropped(msg, "2.1")`, then calls `write_kept`.
- `write_kept(msg, *, pretty_print=False) -> bytes` (`writer.py`): writes a
  message that has already been narrowed. It flattens codelist extensions and
  applies `with_providers_merged` and `hierarchy_containers`.
- `with_providers_merged`, `split_provider_schemes`, `data_provider_urn` and
  `metadata_provider_urn` (`_providers.py`): fold metadata providers into a
  marked 2.1 `DataProviderScheme`, and split them back out on read. A
  metadata provision agreement is written as a `ProvisionAgreement` over its
  metadataflow, and any 2.1 agreement over a metadataflow is read as a
  metadata provision agreement.
- `SYNTHETIC_STRUCTURE_SET_TYPE`, `SYNTHETIC_STRUCTURE_SET_ID`,
  `SYNTHETIC_REPORT_LAYER_TYPE`, `SYNTHETIC_TARGET_ID`,
  `SYNTHETIC_TARGET_OBJECT`, `SYNTHETIC_REPORT_ID`,
  `METADATA_PROVIDER_SCHEME_TYPE`, `METADATA_PROVIDERS_MERGED_TYPE` and
  `METADATA_PROVIDER_TYPE` (`_synthetic.py`): the ids and annotation marks on
  what the writer creates or merges.
- `MES`, `STR`, `COM` and related constants (`namespaces.py`): the 2.1
  namespace URIs.

## Invariants

- On read, a StructureSet with the synthetic annotation is removed and its maps
  become top-level. StructureSets without the annotation are kept.
- On read, a synthetic MSD MetadataTarget or ReportStructure is removed, so a
  3.x to 2.1 to 3.x round trip returns the original.
- Drafts (`1.0.0-draft`) cannot be written in 2.1. `write` leaves them out,
  together with everything that references them.
- 2.1 has no codelist extension, so an extending codelist is written with the
  codes it inherits.
- A refused artefact is recorded and never partly built. The rest of the
  document is still read.
- A 2.1 `ContentConstraint` is read as a `DataConstraint`, unless it
  constrains metadata; that one is refused (`METADATA_CONSTRAINT`).
- Each `<str:HierarchicalCodelist>` is unwrapped into top-level `Hierarchy`
  artefacts and kept as a peer that references them. It is dropped when it
  only wraps one hierarchy and carries nothing of its own (`_wraps_only`).

## Side Effects

- `read` with `registry=` calls `add_all(msg.all_artefacts())`.
- No file or network I/O: bytes in, bytes out.

## Verification

- Run: `just test` (unit tests) and `just check` (lint and typecheck).
- Writer output is validated against the vendored 2.1 XSD
  (`tests/support/xsd.py`).
