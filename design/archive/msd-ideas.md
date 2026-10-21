# MSD (Metadata Structure Definition) — Design Ideas

## sdmx1 vs pysdmx — MSD Design

### Structure Definition Layer

| Aspect                   | sdmx1                                                                                                                | pysdmx                                                                                          |
| ------------------------ | -------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| **MSD class**            | `MetadataStructureDefinition` with `report_structure` dict + `target` dict (2.1); flat `attributes` list (3.0)       | `MetadataStructure` with `components` sequence                                                  |
| **Attribute definition** | `MetadataAttribute` with `parent`/`child` pointers, `min_occurs`/`max_occurs`, `is_presentational`                   | `MetadataComponent` with nested `components`, `array_def: ArrayBoundaries`, `is_presentational` |
| **ReportStructure**      | Explicit class — `ComponentList` of attributes + `report_for: list[MetadataTarget]`                                  | Not modeled — implicit in the structure referenced by the flow                                  |
| **MetadataTarget**       | Full hierarchy: `DataSetTarget`, `DimensionDescriptorValuesTarget`, `IdentifiableObjectTarget`, `ReportPeriodTarget` | Just URN strings on `Metadataflow.targets` and `MetadataReport.targets`                         |
| **Metadataflow**         | `MetadataflowDefinition` (2.1) / `Metadataflow` (3.0) with `structure` ref                                           | `Metadataflow` with `structure` ref + `targets`                                                 |
| **Representation**       | Inherited from `Component` (concept identity)                                                                        | Dual: `concept` (inherited) or local override via `local_dtype`, `local_codes`, `local_facets`  |

### Instance/Reporting Layer

| Aspect                         | sdmx1                                                                                                                                | pysdmx                                                                            |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------- |
| **Report container**           | `MetadataSet` (NameableArtefact in 2.1, Maintainable in 3.0) containing `list[MetadataReport]`                                       | `MetadataReport` (Maintainable) directly                                          |
| **Report**                     | `MetadataReport` with `metadata: list[ReportedAttribute]`, `target`, `attaches_to: TargetObjectKey`                                  | `MetadataReport` with `attributes: list[MetadataAttribute]`, `targets: list[URN]` |
| **Reported values**            | **Discriminated union**: `EnumeratedAttributeValue`, `TextAttributeValue`, `XHTMLAttributeValue`, `OtherNonEnumeratedAttributeValue` | **Single class**: `MetadataAttribute` with `value: Any` — polymorphic             |
| **Value nesting**              | `ReportedAttribute` with `parent`/`child` pointers (mirrors definition tree)                                                         | `MetadataAttribute` with nested `attributes` sequence                             |
| **Navigation**                 | `report.get("attr_id")` with recursive search                                                                                        | `report["parent.child.grandchild"]` dot-notation                                  |
| **MetadataProvisionAgreement** | Not implemented                                                                                                                      | Implemented (URN-based linking)                                                   |

### Key Design Differences

#### 1. Complexity vs Pragmatism

sdmx1 faithfully models the full SDMX IM — separate `ReportStructure`, four
`MetadataTarget` subtypes, discriminated value types. pysdmx collapses these:
no `ReportStructure`, targets are URN strings, values are `Any`.

#### 2. Schema vs Instance naming

pysdmx makes a clean split: `MetadataComponent` = the definition (schema),
`MetadataAttribute` = the reported value (instance). sdmx1 uses
`MetadataAttribute` for the definition and `ReportedAttribute` subtypes for
values. pysdmx's naming is clearer.

#### 3. Nesting approach

sdmx1 uses bidirectional `parent`/`child` pointers. pysdmx uses
unidirectional `components`/`attributes` sequences (parent-to-child only). The
pysdmx approach is simpler and matches what we did with
`HierarchicalCode.children`.

#### 4. Value typing

sdmx1's union (`Enumerated | Text | XHTML | Other`) gives type safety at the
cost of complexity. pysdmx's `value: Any` is pragmatic — the component
definition tells you what type to expect. In practice most values are strings.

## What this means for sdmxlib

Lean toward a **pysdmx-flavored design** with a few sdmx1 ideas:

| Decision                       | Recommendation                                                                     | Why                                                                                     |
| ------------------------------ | ---------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| **Definition class**           | `MetadataStructure` (maintainable) with `attributes: ItemList[MetadataComponent]`  | Follows our existing patterns; pysdmx naming is clearer                                 |
| **Component nesting**          | `MetadataComponent` with `children: ItemList[MetadataComponent]`                   | Same pattern as `HierarchicalCode` — unidirectional, recursive                          |
| **Representation**             | `concept` ref + optional local `representation: Ref[Codelist] \| Facet \| None`    | Matches our DSD component pattern exactly                                               |
| **is_presentational**          | Include it — both libraries have it                                                | Useful for FMR/SIMS                                                                     |
| **Cardinality**                | `min_occurs: int`, `max_occurs: int \| None`                                       | Simple, covers arrays and optional                                                      |
| **Metadataflow**               | `Metadataflow` with `structure: Ref[MetadataStructure]`                            | Mirror our existing `Dataflow`                                                          |
| **MetadataTarget**             | URN strings (pysdmx approach), not typed subtypes                                  | The four target subtypes add complexity we won't use early; can always add later        |
| **ReportStructure**            | Skip for now                                                                       | pysdmx proves you don't need it; 3.0 dropped it                                         |
| **MetadataReport**             | Maintainable with `attributes: ItemList[MetadataAttribute]` + `targets: list[str]` | Clean, matches pysdmx                                                                   |
| **Reported values**            | `MetadataAttribute` with `value: str \| InternationalString \| None`               | More typed than pysdmx's `Any`, less complex than sdmx1's union. XHTML is just a string |
| **MetadataProvisionAgreement** | Include — simple dataclass                                                         | StatCan likely needs provider bindings                                                  |
| **Flat index**                 | Add `.all_attributes` flat index on `MetadataStructure` (like `Hierarchy.codes`)   | Proven useful pattern in our codebase                                                   |

The total scope would be ~5 new domain types (`MetadataStructure`,
`MetadataComponent`, `Metadataflow`, `MetadataReport`, `MetadataAttribute`)
plus `MetadataProvisionAgreement`.
