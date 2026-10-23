# API Reference

---

## REST registries

The entry point for connecting to an SDMX REST endpoint over HTTP.

::: sdmxlib.api.registry.RestRegistry

---

::: sdmxlib.api.registry.FmrRegistry

---

::: sdmxlib.api.registry.BearerToken

---

::: sdmxlib.api.providers.Provider

---

::: sdmxlib.api.client.ResponseTooLargeError

---

## Federation

Routing across multiple registries by agency. Every registry — REST, local, in-memory, federated — satisfies the `RegistryReader` protocol.

::: sdmxlib.api.federated.FederatedRegistry

---

::: sdmxlib.api.policy.RegistryUnavailable

---

::: sdmxlib.model.registry.RegistryReader

---

## Local registry & storage

Persistent DuckDB-backed structural store, the in-memory registry, and the data plane that shares the store's connection.

::: sdmxlib.local.registry.LocalRegistry

---

::: sdmxlib.storage.schema.StoreSchemaError

---

### Bundles

A bundle published as an artefact: its manifest, a `token` per publication, and a checksum per file (GH-66).

::: sdmxlib.bundle

---

::: sdmxlib.bundle.write_manifest

---

::: sdmxlib.bundle.read_manifest

---

::: sdmxlib.bundle.check_bundle

---

::: sdmxlib.bundle.Report

---

::: sdmxlib.bundle.Finding

---

::: sdmxlib.bundle.fetch_bundle

---

::: sdmxlib.bundle.Provenance

---

::: sdmxlib.bundle.Manifest

---

::: sdmxlib.bundle.ManifestError

---

::: sdmxlib.model.in_memory_registry.InMemoryRegistry

---

::: sdmxlib.session.Session

---

::: sdmxlib.session.ValueSet

---

::: sdmxlib.session.SessionHook

---

::: sdmxlib.data_store.DataStore

---

::: sdmxlib.data_store.WriteSource

---

::: sdmxlib.local.data_store.DuckDBDataStore

---

::: sdmxlib.local.data_store.DuckLakeVersionError

---

::: sdmxlib.local.sql.SchemaInspector

---

::: sdmxlib.local.sql.QueryPlan

---

::: sdmxlib.versions

---

::: sdmxlib.model.version_rewrite.RewrittenVersion

---

::: sdmxlib.model.version_rewrite.VersionRewrite

---

## Catalog

One row of a `LocalRegistry.browse()` result — a bound dataflow with its binding and provenance.

::: sdmxlib.model.binding.DataflowEntry

---

## Structural queries

The path language. `sl.q` is the accessor — `sl.q.code`, `sl.q.dsd`,
`sl.q.hcode`, one lowercase singleton per relation — and `reg.query(sl.q.code)`
is the entry point. `sl.schema` names the physical tables and columns for raw
SQL, reflected from the DDL. See the
[Structures as Frames](cookbook/structures_as_frames.md) and
[Catalogue Discovery](cookbook/discovery.md) cookbook pages.

::: sdmxlib.query.query.Query

---

::: sdmxlib.query.query.Select

---

::: sdmxlib.query.query.Grouped

---

::: sdmxlib.query.query.Page

---

::: sdmxlib.query.query.union

---

::: sdmxlib.len

---

::: sdmxlib.query._compile.CompiledQuery

---

::: sdmxlib.query.raw.RawSql

---

::: sdmxlib.query.raw.RawSqlError

---

### Query refusals

What a query raises when it cannot be written, planned or served — each names the fix.

::: sdmxlib.query._joins.PathError

---

::: sdmxlib.query.query.SliceError

---

::: sdmxlib.query.query.CrossSourceError

---

::: sdmxlib.query._pushdown.PushdownError

---

::: sdmxlib.local.sql.SidecarNotUniqueError

---

::: sdmxlib.query.accessors.q

---

::: sdmxlib.query.schema.Schema

---

### Expressions and row kinds

The typed pieces a path is built from. You rarely name these — `sl.q` hands them out — but they appear in signatures.

::: sdmxlib.query._expr.Col

---

::: sdmxlib.query._expr.Cols

---

::: sdmxlib.query._expr.Agg

---

::: sdmxlib.query._expr.Win

---

::: sdmxlib.query._expr.WinFn

---

::: sdmxlib.query._expr.Pred

---

::: sdmxlib.query._expr.AnyPred

---

::: sdmxlib.query._expr.AggPred

---

::: sdmxlib.query._expr.WinPred

---

::: sdmxlib.query._expr.Cursor

---

::: sdmxlib.query._expr.RowKind

---

::: sdmxlib.query._expr.Untyped

---

::: sdmxlib.query._common.Descending

---

::: sdmxlib.query.accessors.ArtefactRow

---

::: sdmxlib.query.accessors.CodeRow

---

::: sdmxlib.query.accessors.HCodeRow

---

::: sdmxlib.query.accessors.ReportedAttributeRow

---

### Observation queries

`LocalRegistry.obs(flow)` returns an `ObsQuery` — the path language rooted at a dataflow's observations.

::: sdmxlib.query.observations.ObsQuery

---

::: sdmxlib.query.observations.ObsRow

---

## Domain model

---

Every domain object is an attrs frozen class: a change is an `attrs.evolve` copy, and every rule an artefact can check on its own runs as it is built.

---

### Data structures

::: sdmxlib.model.datastructure.DataStructure

---

::: sdmxlib.model.datastructure.Dimension

---

::: sdmxlib.model.datastructure.TimeDimension

---

::: sdmxlib.model.datastructure.MeasureDimension

---

::: sdmxlib.model.datastructure.Attribute

---

::: sdmxlib.model.datastructure.Measure

---

::: sdmxlib.model.datastructure.Group

---

::: sdmxlib.model.datastructure.AttachmentLevel

---

::: sdmxlib.model.datastructure.AttributeUsage

---

::: sdmxlib.model.datastructure.ComponentRole

---

::: sdmxlib.model.datastructure.MetadataAttributeUsage

---

### Codelists

::: sdmxlib.model.codelist.Codelist

---

::: sdmxlib.model.codelist.Code

---

::: sdmxlib.model.codelist.CodelistExtension

---

::: sdmxlib.model.codelist.CodeSelection

---

::: sdmxlib.model.codelist.MemberValue

---

::: sdmxlib.model.codelist.CascadeSelection

---

::: sdmxlib.model.codelist.InclusionMode

---

::: sdmxlib.model.codelist.GeographicCodelist

---

::: sdmxlib.model.codelist.GeoFeatureSetCode

---

::: sdmxlib.model.codelist.GeoGridCodelist

---

::: sdmxlib.model.codelist.GeoGridCode

---

::: sdmxlib.model.codelist.CodelistLike

---

::: sdmxlib.model.codelist.EnumerationLike

---

::: sdmxlib.storage.lazy.LazyCodelist

---

### Value lists

SDMX 3.x's enumeration that is not a codelist; a component's enumerated representation is a `Ref[Codelist | ValueList]`.

::: sdmxlib.model.valuelist.ValueList

---

::: sdmxlib.model.valuelist.ValueItem

---

### Hierarchies

::: sdmxlib.model.hierarchy.Hierarchy

---

::: sdmxlib.model.hierarchy.HierarchicalCode

---

::: sdmxlib.model.hierarchy.Level

---

::: sdmxlib.model.hierarchy.HierarchyAssociation

---

::: sdmxlib.model.hierarchy.HierarchicalCodelist

---

::: sdmxlib.model.hierarchy.AmbiguousHierarchyError

---

::: sdmxlib.storage.lazy.LazyHierarchy

---

::: sdmxlib.model.rollup.AncestorMap

---

### Dataflows

::: sdmxlib.model.dataflow.Dataflow

---

### Concepts

::: sdmxlib.model.concept.ConceptScheme

---

::: sdmxlib.model.concept.Concept

---

::: sdmxlib.model.concept.ISOConceptReference

---

### Categories

::: sdmxlib.model.category.CategoryScheme

---

::: sdmxlib.model.category.Category

---

::: sdmxlib.model.category.Categorisation

---

### Constraints

::: sdmxlib.model.constraint.DataConstraint

---

::: sdmxlib.model.constraint.AvailabilityConstraint

---

::: sdmxlib.model.constraint.AttachmentConstraint

---

::: sdmxlib.model.constraint.ConstraintType

---

::: sdmxlib.model.constraint.ConstraintAttachment

---

::: sdmxlib.model.constraint.CubeRegion

---

::: sdmxlib.model.constraint.KeyValue

---

::: sdmxlib.model.constraint.ComponentValueSet

---

::: sdmxlib.model.constraint.RegionValue

---

::: sdmxlib.model.constraint.TimeRange

---

::: sdmxlib.model.constraint.TimeBound

---

::: sdmxlib.model.constraint.DataKeySet

---

::: sdmxlib.model.constraint.DataKey

---

::: sdmxlib.model.constraint.DataSetReference

---

::: sdmxlib.model.constraint.QueryableDataSource

---

::: sdmxlib.model.constraint.ReferencePeriod

---

::: sdmxlib.model.constraint.ReleaseCalendar

---

### Provision agreements

::: sdmxlib.model.provision.ProvisionAgreement

---

::: sdmxlib.model.metadata_provision.MetadataProvisionAgreement

---

### Organisation schemes

::: sdmxlib.model.organisation.AgencyScheme

---

::: sdmxlib.model.organisation.Agency

---

::: sdmxlib.model.organisation.DataProviderScheme

---

::: sdmxlib.model.organisation.DataProvider

---

::: sdmxlib.model.organisation.DataConsumerScheme

---

::: sdmxlib.model.organisation.DataConsumer

---

::: sdmxlib.model.organisation.MetadataProviderScheme

---

::: sdmxlib.model.organisation.MetadataProvider

---

::: sdmxlib.model.organisation.OrganisationUnitScheme

---

::: sdmxlib.model.organisation.OrganisationUnit

---

::: sdmxlib.model.organisation.Contact

---

### Structure maps

::: sdmxlib.model.mapping.StructureSet

---

::: sdmxlib.model.mapping.StructureMap

---

::: sdmxlib.model.mapping.ComponentMap

---

::: sdmxlib.model.mapping.FixedValueMap

---

::: sdmxlib.model.mapping.DatePatternMap

---

::: sdmxlib.model.mapping.EpochMap

---

::: sdmxlib.model.mapping.EpochPeriod

---

::: sdmxlib.model.mapping.FrequencyFormatMapping

---

::: sdmxlib.model.mapping.ResolvePeriod

---

::: sdmxlib.model.mapping.RepresentationMap

---

::: sdmxlib.model.mapping.RepresentationMapping

---

::: sdmxlib.model.mapping.SourceValue

---

::: sdmxlib.model.mapping.ToValueType

---

::: sdmxlib.model.mapping.ItemMap

---

::: sdmxlib.model.mapping.ConceptSchemeMap

---

::: sdmxlib.model.mapping.CategorySchemeMap

---

::: sdmxlib.model.mapping.OrganisationSchemeMap

---

### Annotations and links

::: sdmxlib.model.annotations.Annotation

---

::: sdmxlib.model.annotations.Annotations

---

::: sdmxlib.model.annotations.AnnotationURL

---

::: sdmxlib.model.link.Link

---

### Shared bases

::: sdmxlib.model.base.MaintainableArtefact

---

::: sdmxlib.model.istring.InternationalString

---

## Reference metadata (MSD)

::: sdmxlib.model.metadatastructure.MetadataStructure

---

::: sdmxlib.model.metadatastructure.MetadataAttribute

---

::: sdmxlib.model.metadataflow.Metadataflow

---

::: sdmxlib.model.metadataset.MetadataSet

---

::: sdmxlib.model.metadataset.ReportedAttribute

---

::: sdmxlib.model.metadataset.ReportedAttributeValue

---

::: sdmxlib.model.metadataset.MetadataSection

---

## Messages

What a read returns and a write takes. A `StructureMessage` carries definitions; a `MetadataMessage` carries populated reference metadata.

::: sdmxlib.model.message.StructureMessage

---

::: sdmxlib.model.message.MetadataMessage

---

::: sdmxlib.model.header.Header

---

::: sdmxlib.model.header.MetadataHeader

---

::: sdmxlib.model.header.Party

---

::: sdmxlib.model.header.Sender

---

::: sdmxlib.model.header.ActionType

---

## Collections

::: sdmxlib.model.collections.ItemList

---

::: sdmxlib.model.collections.ArtefactList

---

::: sdmxlib.model.collections.Maintainable

---

## References and identity

A `Ref` always carries its target's URN and finds the target on access, through the registry or document that bound it. Calling one whose target is not there raises `UnresolvedRef`.

::: sdmxlib.model.ref.Ref

---

::: sdmxlib.model.ref.SdmxRef

---

::: sdmxlib.model.ref.UnresolvedRef

---

::: sdmxlib.model.ref.SupportsUrn

---

::: sdmxlib.model.ref.artefact_urn

---

::: sdmxlib.model.urn.SdmxUrn

---

::: sdmxlib.model.rest_api.ApiVersion

---

## Representation

A component's representation is `Ref[Codelist | ValueList] | Facet | None` — enumerated, free text with bounds, or absent.

::: sdmxlib.model.representation.Representation

---

::: sdmxlib.model.representation.DataType

---

::: sdmxlib.model.representation.Facet

---

::: sdmxlib.model.representation.SentinelValue

---

## Formats

Reading and writing SDMX-ML 2.1, 3.0 and 3.1 and SDMX-JSON.

::: sdmxlib.formats.read

---

::: sdmxlib.formats.parse

---

::: sdmxlib.formats.serialize

---

::: sdmxlib.formats.to_xml

---

::: sdmxlib.formats.to_json

---

::: sdmxlib.formats.data_format.DataFormat

---

### What a format cannot carry

`unrepresentable(artefact, target)` names what writing one artefact to a format would silently lose; `dropped` names what a message would leave out.

::: sdmxlib.formats.gaps.unrepresentable

---

::: sdmxlib.formats.gaps.dropped

---

::: sdmxlib.formats.gaps.Gap

---

::: sdmxlib.formats.gaps.DroppedArtefact

---

### Read errors and outcomes

What a read refused, could not hold, or assumed — reported on the message, and raised under `strict=` / `lossless=`.

::: sdmxlib.model.errors.SdmxParseError

---

::: sdmxlib.model.validation.SdmxConformanceError

---

::: sdmxlib.model.validation.LossyReadError

---

::: sdmxlib.model.validation.SdmxErrorMessage

---

::: sdmxlib.model.errors.Refusal

---

::: sdmxlib.model.errors.ReadGap

---

::: sdmxlib.model.errors.ReadAssumption

---

## Polars integration

::: sdmxlib.polars.schema

---

::: sdmxlib.polars.validation_report

---

::: sdmxlib.polars.label

---

::: sdmxlib.polars.structure_frame

---

## Dataflow bindings

A `DataflowBinding` ties a `Dataflow` to a concrete data-plane table
(local file, DuckDB table, or DuckLake catalog) so a registry can answer
observation queries. Drift detection is exposed on every registry
(see `RestRegistry`, `FederatedRegistry`, `InMemoryRegistry` above) as
two complementary methods:

- `check_binding_drift(binding) -> list[DriftIssue]` —
  inspection; returns the list of mismatches (empty if clean).
- `validate_binding(binding) -> None` — enforcement; raises
  `DriftError` (with `issues` attached) if any drift is detected.

::: sdmxlib.model.binding.DataflowBinding

---

::: sdmxlib.model.binding.DriftIssue

---

::: sdmxlib.model.binding.DriftError

---

## REST facade kit

For services *publishing* SDMX REST endpoints — parses inbound URL slices into typed filters. See the [REST Facade cookbook](cookbook/rest_facade.md).

::: sdmxlib.rest.parse_components

---

::: sdmxlib.rest.ComponentFilter

---

::: sdmxlib.rest.widen_key

---

## Validation

Every rule an artefact can check on its own runs as it is built, so a bad
artefact is refused at construction. `validate()` keeps what only a message
can know — that its references resolve within it and no artefact appears
twice.

::: sdmxlib.model.validation.validate

---

::: sdmxlib.model.validation.validate_references

---

::: sdmxlib.model.errors.ValidationIssue

---

::: sdmxlib.model.errors.ValidationError

---

::: sdmxlib.model.validation.ReferenceIssue

---

::: sdmxlib.model.validation.ReferenceValidationError

---

::: sdmxlib.model.errors.SdmxWarning

---

::: sdmxlib.model.errors.ImRuleWarning

---

## Dict conversion

Round-trip artefacts through plain Python dicts (JSON-friendly, no SDMX wire format).

::: sdmxlib.model.convert.to_dict

---

::: sdmxlib.model.convert.from_dict

---

## SQL surface

The protocol a one-column projection satisfies, so it splices into another query (or a `ComponentFilter`) as a subquery.

::: sdmxlib.sql.SqlRelation

---

## Filter expressions

`item()` builds predicates for `ItemList.filter()`.

::: sdmxlib.model.expr.item

---

::: sdmxlib.model.expr.ItemExpr

---

::: sdmxlib.model.expr.ItemPredicate
