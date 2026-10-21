# How the query language names things

Every accessor, hop and row kind in `sl.q` is named by one rule:

> **Use the SDMX information-model class name, or a transparent abbreviation of
> it. Never a general word standing in for a specific class, and never an
> invented descriptive term.**

This is written down because the alternative is re-deciding it per name, which
is how three of them drifted before anyone counted.

## Why not a general word

A general word does not identify the thing. `node` was the case that forced the
rule: `sdmx.hierarchy_node` rows were exposed as `q.node`, but "node" appears in
**zero** SDMX-ML schemas, 2.1 or 3.0 — the class is `HierarchicalCode`, which is
what sdmx1 and pysdmx both call it and what our own model class is called.

It also broke the pairing. Compare:

```python
q.codelist.codes        # Code
q.hierarchy.nodes       # HierarchicalCode  — nothing says these are related
q.node.code.id          # ...and this needs prose to explain itself

q.hierarchy.hcodes      # HierarchicalCode
q.hcode.code.id         # the Code this HierarchicalCode places
```

`code` and `node` share no morpheme, so nothing in the names carried the IM
relationship. That is the concrete cost of a general word, over and above the
discoverability one.

## Why not an invented descriptive word

`placement` was the runner-up, and it describes the row exactly — a
`HierarchicalCode` *is* the placement of a code in a hierarchy, which is why one
code may appear twice. It was rejected for the reason that also disqualified
`node`: it is a general word for positioning anything, so it does not signal
that this has to do with hierarchical codes. A reader who knows the spec is
stranded either way.

## Keep the head noun, qualify it

SDMX builds these names as *qualifier + head noun*, and so do we:

| IM class | ours | |
| --- | --- | --- |
| `Code` | `code` | head noun, unqualified |
| `HierarchicalCode` | `hcode` | head noun kept, qualifier abbreviated |
| `Attribute` (DSD) | `attributes` | the facet |
| `MetadataAttribute` | `metadata_attribute` | head noun kept, qualifier spelled |
| `ReportedAttribute` | `reported_attribute` | head noun kept, qualifier spelled |

Whether the qualifier is abbreviated is decided by **measurement, not
principle**: `hierarchical_code` put 35 repo lines past the 120-character limit,
`reported_attribute` puts 9. Same rule, different pressure. Say that plainly
rather than inventing a deeper reason.

`attribute` alone is unusable, which is why the spec qualifies two of the three:
SDMX has a DSD `Attribute`, an MSD `MetadataAttribute` and a reported
`ReportedAttribute`, and they are unrelated.

One wrinkle recorded so it is not "fixed" later: `ReportedAttribute` is the
**2.1** name. SDMX-ML 3.0 renamed the element to plain `Attribute` (type
`AttributeType`, with a child `Value`). 3.0's spelling is unavailable to us —
it would collide head-on with the DSD attribute — so the 2.1 name stands, and it
is also our model class and glossary term.

## Where a general word is correct

When the IM itself has a general class, the general word is the exact one:

- **`.scheme`** — `concept.scheme` and `category.scheme` are both `ItemScheme`,
  a real IM class. The hop is genuinely polymorphic, so `scheme` is right.
  The *root* was not: `q.scheme` meant ConceptScheme specifically, while
  CategoryScheme had no root at all. It is now `q.concept_scheme`.
- **`.structure`** — a `MetadataSet` reports against a Metadataflow *or* a
  MetadataProvisionAgreement, so `sl.q.metadata_set.structure` lands on
  `artefact` rows and `sl.q.metadata_set.structure.type` says which. (Storage
  keeps the class in `sdmx.metadata_set.structure_kind`; that column is not on
  the accessor.)
- **`parent` / `children` / `ancestors` / `descendants` / `described_by`** —
  these name *relationships*, not things. A general word is precise for a
  relationship, and every peer and ORM uses exactly these.

The test is whether the general word denotes something the model actually has.
`ItemScheme` exists, so `.scheme` is fine. There is **no** `Provider` class —
SDMX 3.0 has `DataProvider` and `MetadataProvider` with no common base — so
`.provider` was not fine, and is now `.data_provider`; `.metadata_provider`
(on a metadata provision agreement) then needed no special case.

## Abbreviations

Three, and only one lacks spec precedent:

| | | |
| --- | --- | --- |
| `dsd` | DataStructureDefinition | standard — used in the spec's own prose |
| `msd` | MetadataStructureDefinition | standard |
| `hcode` | HierarchicalCode | **ours**; the single unprecedented abbreviation in the surface |

`artefact` is not an abbreviation — it is the spec's own umbrella word
(`IdentifiableArtefact`, `MaintainableArtefact`).

## Shortenings that were reverted

`flows` → `dataflows`, `provisions` → `provision_agreements`, `set` →
`metadata_set`. Four to eight characters saved is not worth diverging from the
class name, and `set` had a second problem: as a hop it reads as an imperative
("set the id") before it reads as a noun.

`set` also broke a convention worth stating on its own: **every to-one hop names
its target class** — `code.codelist`, `component.dsd`, `component.concept`,
`hcode.hierarchy`, `hcode.level`, `metadataflow.msd`. `set` was the only one
that shortened.

## Physical names are not API

Storage keeps its own names. `sdmx.hierarchy_node` stays `hierarchy_node`
whatever the accessor is called; layout is not API, and renaming the table would
touch writers and readers for no gain. Likewise `spine`, the identity-split hop
— invented, but implementation-only, so it is exempt.

## Deviations, in full

`sl.q` has 48 accessors. 42 are the exact IM class name in snake case
(`component` for the abstract `Component`, `annotation_url` for
`AnnotationURL` among them). The deviations, roots and hops together:

| name | stands for | why |
| --- | --- | --- |
| `dsd`, `msd` | …Definition | standard spec abbreviations |
| `hcode` | HierarchicalCode | measured; see above |
| `artefact` | MaintainableArtefact | IM umbrella word |
| `.scheme` | ItemScheme | genuinely polymorphic |
| `.structure` | Metadataflow *or* MetadataProvisionAgreement | genuinely polymorphic |
| `metadata_target` | `Target` (3.0) | prefixed; bare `target` is generic |
| `hierarchy_applicability` | — | a derived view (which hierarchy applies to a component in a dataflow), not an IM class; named for the relationship it computes |
| `spine` | the identity split | internal plumbing, not public API |
| `parent`, `children`, `ancestors`, `descendants`, `described_by` | — | relationships, not things |

If a new name is not on that list and is not an IM class name, it is a defect.
