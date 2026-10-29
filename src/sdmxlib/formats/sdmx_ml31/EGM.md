# sdmxlib.formats.sdmx_ml31

## Overview

This package binds SDMX-ML 3.1. Its wrappers are the same as 3.0's: they bind
`V3Reader` and `V3Writer` (with `generation=V3_1`) from
[`../sdmx_ml/EGM.md`](../sdmx_ml/EGM.md) to the 3.1 namespaces. It has no
metadata module.

## Interface

- `read(data, *, registry=None) -> StructureMessage` (`reader.py`): a
  `V3Reader` bound to the 3.1 URIs.
- `write(msg, *, pretty_print=False, metadata_links=None, base_url=None) ->
  bytes` and `write_kept(msg, *, pretty_print=False) -> bytes` (`writer.py`):
  a `V3Writer` with `V3_1`.
- `MES`, `STR`, `COM`, `MD`, `XML_LANG`, `NS` and `STRUCTURE_ROOT_NS`
  (`namespaces.py`): the 3.1 namespace URIs.

## Invariants

- All the logic lives in `sdmx_ml._v3_reader` and `sdmx_ml._v3_writer`. 3.1
  differs from 3.0 only in its namespaces and its `V3_1` flags.
- `V3_1` drops `Categorisation/@version`, `DataConstraint/@role` and
  ReleaseCalendar. It adds DimensionConstraint, `isPartialLanguage`,
  `evolvingStructure` and AvailabilityConstraints.

## Side Effects

- Creates the reader and writer singletons at import.
- `read` with `registry=` calls `add_all` on that registry.

## Verification

- Run: `just test` (unit tests) and `just check` (lint and typecheck).
