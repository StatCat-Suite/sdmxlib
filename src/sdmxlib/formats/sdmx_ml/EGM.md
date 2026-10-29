# sdmxlib.formats.sdmx_ml

## Overview

This package holds the SDMX-ML code shared by every version, parameterised by
namespace URIs. It contains the element read/write helpers used by 2.1 and 3.x
(`_common.py`), header reading and writing (`_header.py`), a closed-world
schema check driven by tables generated from the vendored XSDs
(`_closed_world.py`, `_allowed_children.py`), the record of what a read lost or
assumed (`_losses.py`), and the one v3 reader and writer that `sdmx_ml30` and
`sdmx_ml31` both bind to (`_v3_reader.py`, `_v3_writer.py`). `__init__.py`
holds only a docstring.

## Interface

- `V3Reader` (`_v3_reader.py`): v3 structure reader built from the `com`,
  `str_` and `mes` URIs. `.read(data, *, registry=None) -> StructureMessage`.
- `V3Writer` (`_v3_writer.py`): v3 structure writer built from the namespace
  URIs and a generation. Provides `.write(msg, *, pretty_print,
  metadata_links, base_url)` and `.write_kept(msg, *, pretty_print,
  metadata_links, base_url)`; both raise `ValueError` when `metadata_links` is
  given without `base_url`.
- `V3Generation`, `V3_0` and `V3_1` (`_v3_writer.py`): capability flags
  (`categorisation_version`, `data_constraint_role`, `release_calendar`,
  `dimension_constraint`, `partial_language`, `evolving_structure`,
  `availability_constraint`). `.target` is `"3.0"` or `"3.1"`.
- `ReaderHelpers` and `WriterHelpers` (`_common.py`): helpers for reading and
  writing international strings, maintainable attributes and other shared
  elements. `ReaderHelpers` takes `has_is_final` and `has_links`;
  `WriterHelpers` takes `grammar`.
- `SectionEntry`, `section_entry`, `ReferenceFields`, `hierarchy_containers`,
  `flag`, `optional_flag`, `required_flag`, `xsd_num`, `sdmx_ml_version` and
  `component_id` (`_common.py`): more shared helpers.
- `write_header`, `read_header` and `read_metadata_header` (`_header.py`):
  write and read `mes:Header`. Each read returns
  `(Header | None, ReadGap | None)`. `write_header(header=None)` writes the
  fixed `PLACEHOLDER` values, and both readers read that header back as
  `None`.
- `check_artefact`, `check_children`, `frame_refusals`, `required`,
  `SdmxMlVersion` and `BY_STRUCTURE_NS` (`_closed_world.py`): refuse any
  element or attribute that the schema allows nowhere under its parent.
- `ALLOWED`, `ATTRIBUTES`, `NAMESPACES`, `OPEN` and `OPEN_ATTRIBUTES`
  (`_allowed_children.py`): tables generated for 2.1, 3.0 and 3.1.
- `account`, `Loss`, `Assumption`, `LOSSES` and `ASSUMPTIONS` (`_losses.py`):
  `account` appends `ReadGap` entries to `msg.lost`, `ReadAssumption` entries
  to `msg.assumed` and refusals to `msg.refused`. It skips the
  `AvailabilityConstraints` container, StructureSets sdmxlib invented on a 2.1
  write, and 2.1 elements the reader built as another class (`_FOLDED`).
  `LOSSES` and `ASSUMPTIONS` are the declared tables of `Loss` and
  `Assumption` rows, each naming a construct the reader cannot hold or fills
  in, and an XPath or callable that counts it.

## Invariants

- 3.0 and 3.1 differ only in namespace URIs and `V3Generation` flags. The
  shared helpers in `_common.py` branch on 2.1 against 3.x, through a
  `version` argument, `WriterHelpers.grammar`, or the version
  `BY_STRUCTURE_NS` maps an element's namespace to.
- `_allowed_children.py` is generated. Regenerate it with
  `just generate-allowed-children` (`tools/generate_allowed_children.py`)
  instead of editing it by hand.
- The closed-world check accepts any child the schema allows under that parent
  in any context. It does not check open (`xs:any`) parents or their subtrees,
  it admits any attribute on an element in `OPEN_ATTRIBUTES`, and it always
  allows `xsi:` attributes.
- Every child of every container under `mes:Structures` comes back as an
  artefact or a `Refusal`. `account` refuses any child that is neither.
- Content the model cannot hold, found inside an artefact that was built, is
  recorded as a `ReadGap` on `msg.lost`, and the artefact is kept.
- `V3Writer` uses `WriterHelpers(grammar="3.0")` for both 3.0 and 3.1, because
  3.1 uses 3.0's version grammar.
- In 3.1, an Actual `DataConstraint` that fits an `AvailabilityConstraint` is
  written as one.
- `V3Reader` sets `has_is_final=False`, because 3.x removed `isFinal`.

## Side Effects

- Converts the allowed-children tables to Clark notation at import
  (`_ALLOWED_CLARK`).
- `read` with `registry=` calls `add_all()` on that registry.
- No file or network I/O: bytes in, bytes out, through lxml.

## Verification

- Run: `just test` (unit tests) and `just check` (lint and typecheck).
- Writer round-trip tests validate output against the XSDs vendored in
  `tests/schemas/`.
