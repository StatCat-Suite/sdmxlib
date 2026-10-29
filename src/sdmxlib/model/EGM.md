# sdmxlib.model

## Overview

`sdmxlib.model` is the SDMX information model: about 47 modules of attrs
`@frozen` artefacts (codelists, concepts, data structures, dataflows,
constraints, mappings, hierarchies, categories, metadata structures and sets,
and the messages that carry them). It also holds typed `SdmxUrn` identity,
`Ref[T]` references that find their targets through a bound resolver, the
in-memory registry, construction-time rule checks and dict conversion. It
depends on the standard library, attrs, `typing_extensions` and
`sdmxlib._time.as_utc` (16 modules), and imports
nothing from `sdmxlib.formats`. One module is behavioural rather than data:
`rollup.py`'s `AncestorMap` imports `duckdb`, `polars`, `sdmxlib.session` and
`sdmxlib.sql`, holds a DuckDB connection and returns polars frames, for
hierarchical roll-ups.

## Interface

- `SdmxUrn[T]` (`urn.py`): generic typed SDMX URN. `ParsesUrn` gives a class a
  `parse_urn` classmethod that returns its own URN type.
- `Ref[T]` (`ref.py`): frozen reference that always carries a URN. Build it
  with `Ref.to`, `Ref.of`, `Ref.unresolved` or `Ref(urn)`, and call it to
  unwrap the target. On access it asks its bound resolver first, then the
  object it was built with. `Ref.resolved` says whether the target can be
  found now, and `Ref.bound` whether the Ref has a resolver. `UnresolvedRef`
  is raised when you call a Ref or read an attribute through it and no target
  is found, including when its registry is closed.
- `SdmxRef` (`ref.py`): partial identity (agency, id, version, each optional)
  for `ArtefactList` lookup and filtering.
- `to_ref`, `to_optional_ref`, `to_refs` and `refuse_urn_text` (`ref.py`):
  attrs converters and validators that turn URNs into Refs and refuse raw URN
  text. `urn_of`, `artefact_urn`, `short_urn` and `target_urn` are URN helpers.
- `MaintainableArtefact` (`base.py`): base for top-level artefacts. It
  annotates the `sdmx_package`, `sdmx_class` and `msg_field` ClassVars, and
  `urn_for(...)` builds its typed URN.
- `ItemList[T: SupportsId]` and `ArtefactList[T: Maintainable]`
  (`collections.py`): sequences. `ItemList` is indexed by item id.
  `ArtefactList` is indexed by int, slice, bare id, `"AG:ID"`, `"AG:ID(ver)"`,
  `SdmxUrn` or `SdmxRef`. The module also defines the `SupportsId`,
  `SdmxItem`, `Maintainable` and `SupportsUri` protocols.
- `item()`, `ItemExpr` and `ItemPredicate` (`expr.py`): filter expressions
  for `ItemList.filter`.
- `SupportsGet`, `SupportsGetAll`, `SupportsAddAll` and `RegistryReader`
  (`registry.py`): the protocols every registry implements.
- `InMemoryRegistry` (`in_memory_registry.py`): URN-keyed store with `add`,
  `add_all`, `get`, `get_all`, `ref` and `__contains__`. `get` and `in` also
  answer an item URN (a `Code` inside a held codelist) through
  `resolve_item`, and `get` takes `expect=` to narrow the result by
  `isinstance`. `ref` returns a Ref bound to the registry's own resolver.
  `applicable_hierarchies`, `unique_hierarchy`, `check_binding_drift` and
  `validate_binding` answer graph and drift questions. `add_all` can rewrite
  SDMX 2.1 versions (`version_rewrite.py`).
- `Resolver`, `binding()` and `current()` (`_binding.py`), plus `MapResolver`
  and `document_binding` (`_resolvers.py`): the ContextVar binding that
  attaches every Ref built inside a block to a resolver.
- `register`, `register_advisory`, `enforce`, `issues_of` and `advisories_of`
  (`_invariants.py`): per-class rules checked when an artefact is built.
  `id_type`, `version_type`, `named`, `maintained_by`, `positive_integer` and
  the other validators in `_rules.py` check single field values.
- `DataStructure`, `Dimension`, `TimeDimension`, `MeasureDimension`,
  `Attribute`, `Measure`, `Group`, `AttachmentLevel` and `AttributeUsage`
  (`datastructure.py`): the DSD and its components.
- `Facet`, `DataType`, `SentinelValue`, `to_representation` and
  `enumeration_ref` (`representation.py`): representations. A representation
  is a `Ref[Codelist | ValueList]`, a `Facet`, or `None`.
- `SdmxParseError`, `Refusal`, `ReadGap`, `ReadAssumption`,
  `ValidationIssue`, `ValidationError`, `SdmxWarning` and `ImRuleWarning`
  (`errors.py`), plus the `SdmxParseError` subclasses `SdmxConformanceError`
  and `LossyReadError` (`validation.py`): errors, diagnostics and warnings.
- `StructureMessage` and `MetadataMessage` (`message.py`): message containers
  holding `ArtefactList`s.
- Other artefact classes: `Codelist`/`Code` (`codelist.py`),
  `ConceptScheme`/`Concept` (`concept.py`), `Dataflow` (`dataflow.py`),
  `CategoryScheme`/`Categorisation` (`category.py`), `DataConstraint`
  and `AttachmentConstraint` (`constraint.py`), `StructureMap`,
  `StructureSet`, `RepresentationMap`, `ConceptSchemeMap`,
  `CategorySchemeMap` and `OrganisationSchemeMap` (`mapping.py`),
  `Hierarchy`, `HierarchyAssociation` and `HierarchicalCodelist`
  (`hierarchy.py`), `ValueList` (`valuelist.py`), the agency, provider,
  consumer and unit schemes (`organisation.py`), `ProvisionAgreement`
  (`provision.py`), `MetadataProvisionAgreement` (`metadata_provision.py`),
  `MetadataStructure` (`metadatastructure.py`), `Metadataflow`
  (`metadataflow.py`), `MetadataSet` and `ReportedAttribute`
  (`metadataset.py`), and `Header` (`header.py`).
- Shared value types: `InternationalString` (`istring.py`), `Annotation` and
  `Annotations` (`annotations.py`) and `Link` (`link.py`).
- `PathIndex` and `PathNavigation` (`_tree.py`): nested items keyed by path,
  shared by `CategoryScheme`, `Hierarchy` and `MetadataStructure`.
- `ApiVersion` (`rest_api.py`): the SDMX-REST generation (`"2.1"` or `"3.0"`), which changes
  `DataStructure.key_slot_ids`.
- `validate` and `validate_references` (`validation.py`), `to_dict` and
  `from_dict` (`convert.py`), and `DataflowBinding`, `DriftIssue` and
  `DriftError` (`binding.py`).
- `sort_key`, `compliance`, `unwritable`, `is_final` and `unreferenceable`
  (`version.py`): version ordering, and which SDMX grammar (2.1 or 3.x)
  accepts a version.

## Invariants

- Every domain class is attrs `@frozen`. An artefact or item is
  `@frozen(unsafe_hash=False)`, so it compares by value and cannot be hashed;
  key collections by `urn`. To change one, make a copy with `attrs.evolve`.
  Derived state set in `__attrs_post_init__` goes through
  `object.__setattr__`.
- A `Ref` is built only from an `SdmxUrn`. `__attrs_post_init__` rejects
  anything else and suggests `Ref.to` (for an artefact) or `SdmxUrn.make` (for
  a child item).
- A Ref compares and hashes by URN alone, because `_target` and `_resolver` are
  `eq=False`. The factories (`Ref.unresolved`, `Ref.of`, `Ref.to`, and the
  `to_ref` converters through them) bind the resolver `current()` returns. A
  bare `Ref(urn)` has no resolver, and `InMemoryRegistry.ref` binds the
  registry's own. A Ref is never rebound in place. `InMemoryRegistry.add`
  stores a rebound copy (`_rebind.py`) and leaves the caller's object
  untouched.
- `InMemoryRegistry._intern` replaces an entry by URN in `_store` and in the
  new object's type bucket of `_by_type`. It removes the URN from no other
  bucket, so re-adding a URN as a different class (a `GeographicCodelist`
  over a `Codelist`) leaves the stale copy in `get_all` of the old class
  while `get` returns the new one (GH-263).
- The model holds SDMX 3.x versions. `add` refuses a version that only SDMX 2.1
  accepts. `add_all` can rewrite such versions instead.
- A `MaintainableArtefact` subclass is expected to set `sdmx_package`,
  `sdmx_class` and `msg_field`. Nothing enforces it.
- `enforce()` runs as each artefact is built. It raises `ValidationError` with
  every broken rule, before any advisory is checked. When every rule passes,
  it emits one `ImRuleWarning` per artefact carrying every broken advisory.
  It is skipped when attrs validators are disabled. When several classes in the MRO register
  checks, only the first one's checks run.
- The model imports nothing from `sdmxlib.formats` and holds no XML, JSON
  or HTTP code. Its artefacts do no I/O; `rollup.AncestorMap` is the one
  class that queries DuckDB. Public signatures carry `Any` in five places:
  `from_dict`'s `data: dict[str, Any]`, `Ref.__getattr__`'s return, the
  `ItemList.to_dict` and `ArtefactList.to_dict` return `dict[Any, T]`, and
  `ItemExpr.is_in`'s `Collection[Any]`.
- Modules whose names start with `_` are internal to sdmxlib.
  `formats/`, `storage/`, `local/`, `bundle/`, `api/` and `query/` import
  them. `_label_match.py` holds string-matching SQL fragments used only by
  `query/`.

## Side Effects

- The ContextVar `sdmxlib_ref_binding` (`_binding.py`) holds the current
  resolver.
- `register()` fills the `_CHECKS` and `_ADVISORIES` dicts in `_invariants.py`
  at import time.
- `functools.cache` and `lru_cache` keep class-shape analysis (`_refshape.py`,
  `_items.py`). `MaintainableArtefact.__init_subclass__` increments a counter
  that invalidates the `_items` cache.
- `enforce()` warns through `warnings.warn(..., skip_file_prefixes=...)`, so
  the warning points at the caller's line.
- `InMemoryRegistry` keeps its state in memory: `_store`, `_by_type`,
  `_rewritten`, and `_resolver`, a `MapResolver` over `_store` whose
  item-lookup cache every `_intern` clears through `forget_items()`.
- No file, network or thread I/O, apart from `rollup.AncestorMap`'s DuckDB
  queries.

## Verification

- Run: `just test` (unit tests) and `just check` (lint and typecheck).
