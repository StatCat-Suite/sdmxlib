# sdmxlib.builders

## Overview

`sdmxlib.builders` holds pure, synchronous functions that build SDMX artefacts
from a registry (a published `EditionRegistry` or a `LocalRegistry`) and a
DuckDB connection. A producer can build an artefact ahead of time and store
it, and a consumer can compute the same artefact live with byte-identical
output.

## Interface

- `build_dataflow_constraint(registry, dataflow_urn, *, name, conn=None,
  constraint_id=None, version="1.0")` (`contentconstraint.py`): runs a
  `SELECT DISTINCT` for each bound dimension and returns one inclusive
  CubeRegion `DataConstraint` of type ACTUAL.
- `build_component_availability(registry, dataflow_urn, *, name, key,
  component_id, api, mode="exact" | "available", conn=None, ...)`
  (`contentconstraint.py`): the availability of one component for a key, in
  the given mode. `component_id="all"` covers every bound dimension. A
  dimension comes back as a `KeyValue` and an attribute as a
  `ComponentValueSet` in the one CubeRegion.
- `build_dsd_metadata_map(registry, *, conn=None)` (`metadata_annotation.py`):
  maps each DSD URN to a list of `DsdMetadataLink`. The links combine the
  targets of populated MetadataSets with those declared by Metadataflows and
  metadata provision agreements, with duplicates on `(dsd_urn, msd_urn)`
  removed. Each DSD's MSDs are ordered `*_DATAFLOW_INFO` first, then by URN.
  Wildcarded target URNs are expanded against the dataflows in Python.
- `build_metadata_target_links` (`metadata_annotation.py`): the target links
  that the SDMX-JSON writer's `metadata_links=` parameter takes.
- `annotate_structure_message(message, dsd_metadata_map)`
  (`metadata_annotation.py`): returns a copy of the message whose matching DSDs
  carry `Annotation(type="METADATA", title=msd_urn)`.
- `DsdMetadataLink`, `METADATA_ANNOTATION_TYPE = "METADATA"` and
  `NUM_QUERIES = 3` (`metadata_annotation.py`).

## Invariants

- `annotate_structure_message` is idempotent and leaves its input unchanged.
  It builds new DSDs with `attrs.evolve`, keeps existing annotations in order,
  and returns the input message as is when the map is empty or no DSD gains
  an annotation.
- `build_dsd_metadata_map` runs exactly `NUM_QUERIES` (3) SQL round trips,
  however many DSDs or targets there are. A unit test asserts the count.
- Dimension sets and physical column names come from the dataflow's
  `DataflowBinding`. The column is quoted with `_quote_identifier`; the table
  name `<database_role>.<table_location>` is interpolated from the binding
  unquoted.
- `build_component_availability` over a published `EditionRegistry` reads its
  stored availability: it drops a key constraint that covers every held value
  of its dimension, and takes an unconstrained dimension's held values without
  a scan. Over a `LocalRegistry` it always scans.
- An availability result built live must be byte-identical to the one built
  ahead of time.

## Side Effects

- Read-only. The builders run DuckDB `SELECT`s on the given connection, or on
  one taken from the registry, and write nothing.

## Verification

- Run: `just test` (unit tests) and `just check` (lint and typecheck).
