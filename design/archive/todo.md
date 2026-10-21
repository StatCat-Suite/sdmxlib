# sdmxlib — SDMX IM coverage gaps

Audit of what is missing or incomplete relative to the SDMX Information Model.

## High priority

- **HierarchicalCodelist** — a separate artefact that creates named hierarchical
  views over existing codelists (e.g. NAICS rolled up to sector level). Distinct
  from `Code.parent_id`, which is a flat parent reference within a single
  codelist. Very common for geographic and industry hierarchies.

- **Mapping layer completeness** — `StructureSet` exists but only `CodelistMap`
  and `CodeMap` are implemented. Missing: `ConceptSchemeMap`,
  `CategorySchemeMap`, `DataStructureMap`, `DataflowMap`.

## ContentConstraint is thin

- Missing `type` field (`Allowed` vs `Available`)
- Missing `isIncluded` on `CubeRegion`
- Missing `AvailableDateRange`

## SDMX 2.1 gaps

- **ReportingTaxonomy** — organises dataflows into a publication taxonomy tree
- **AttachmentConstraint** — constraints on metadata attachment
- **OrganisationUnitScheme** — third org type alongside Agency/DataProvider

## SDMX 3.0 only (lower priority)

- **ValueList** — sentinel/special values alongside codelists
- **MetadataStructure / MetadataFlow** — reference metadata (very different use case)
- **GeoFeatureSetScheme / GeographicCodelist** — geographic extensions
- **VTL / Transformations** — computational transforms

## Small gaps in existing types

- `validFrom` / `validTo` on maintainable artefacts
- `Concept.isCodified`
