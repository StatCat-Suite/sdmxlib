# SDMX Coverage Gaps — Implementation Plan

Four features to close gaps in SDMX 2.1/3.0 coverage, ordered by dependency.

---

## Phase 1: `valid_from` / `valid_to` on MaintainableArtefact

**Model:** Add `valid_from: datetime | None = None` and `valid_to: datetime | None = None` to every concrete maintainable dataclass (Codelist, ConceptScheme, DataStructure, Dataflow, CategoryScheme, Categorisation, AgencyScheme, DataProviderScheme, ProvisionAgreement, ContentConstraint, StructureSet).

**Readers (all 3 formats):** Add a `_read_datetime` helper. XML: parse `validFrom`/`validTo` attributes on every maintainable element. JSON: parse `"validFrom"`/`"validTo"` string keys.

**Writers (all 3 formats):** Emit `validFrom`/`validTo` when not None, as ISO 8601.

**Tests:** Extend existing roundtrip tests. No new test files.

---

## Phase 2: ContentConstraint completion

**Model (`constraint.py`):** Add two new dataclasses:

- `ReferencePeriod` — `start_period: datetime`, `end_period: datetime`
- `ReleaseCalendar` — `periodicity: str`, `offset: str`, `tolerance: str`

Add to `ContentConstraint`: `reference_period: ReferencePeriod | None = None`, `release_calendar: ReleaseCalendar | None = None`.

**Readers:** Parse `<str:ReferencePeriod>` and `<str:ReleaseCalendar>` children (XML) / `"referencePeriod"` and `"releaseCalendar"` keys (JSON).

**Writers:** Emit when not None.

**Exports:** Add `ReferencePeriod`, `ReleaseCalendar` to `__init__.py`.

---

## Phase 3: StructureSet completion

**Model (`mapping.py`):** Add 7 new types following the existing `CodelistMap`/`CodeMap` pattern:

| Container type      | Item map type  | Typed refs            |
| ------------------- | -------------- | --------------------- |
| `ConceptSchemeMap`  | `ConceptMap`   | `Ref[ConceptScheme]`  |
| `CategorySchemeMap` | `CategoryMap`  | `Ref[CategoryScheme]` |
| `DataStructureMap`  | `ComponentMap` | `Ref[DataStructure]`  |
| `DataflowMap`       | _(none)_       | `Ref[Dataflow]`       |

Add 4 new tuple fields to `StructureSet` with `()` defaults.

**Readers/Writers:** Mirror the existing `CodelistMap` read/write pattern for each new type.

**Exports:** All 7 new types.

---

## Phase 4: Hierarchy

**New file `model/hierarchy.py`:**

- `HierarchicalCode` — tree node with `id`, `code: Ref[Code] | None`, `name`, `children: ItemList[HierarchicalCode]`, `valid_from`, `valid_to`
- `Hierarchy` — maintainable with:
  - `tree: ItemList[HierarchicalCode]` — top-level roots (tree entry points)
  - `codes: ItemList[HierarchicalCode]` — recursive flat index over all nodes (computed in `__post_init__`)

```python
hierarchy.codes["OTTAWA"]                  # flat O(1) lookup — any node
hierarchy.tree["CANADA"].children["ON"]    # tree navigation
```

URN registration: `sdmx_package = "codelist"`, `sdmx_class = "Hierarchy"`, `msg_field = "hierarchies"`.

**Message (`message.py`):** Add `hierarchies: ArtefactList[Hierarchy]` field, include in `all_artefacts()`.

**SDMX-ML 2.1 reader:** Map `<str:HierarchicalCodelist>` → one `Hierarchy` per inner `<str:Hierarchy>` element, inheriting agency/version from the outer wrapper. Recursive `<str:HierarchicalCode>` nesting maps to the tree.

**SDMX-ML 3.0 reader:** Direct mapping — `<str:Hierarchy>` maps 1:1.

**SDMX-JSON reader:** Parse `"hierarchies"` array with nested `"hierarchicalCodes"`.

**Writers:** Reverse of readers. 2.1 writer wraps in `<str:HierarchicalCodelist>` container.

**Validation:** Check unique ids in flat index, validate code Ref URNs.

**Tests:** New `tests/unit/test_hierarchy.py` + extend roundtrip, format, validation, and public API tests.

---

## Design decisions

- **Tree + flat:** Hierarchy provides both `tree` (structural navigation) and `codes` (flat O(1) lookup). Tree nodes have `children: ItemList[HierarchicalCode]`.
- **Distinct map types:** StructureSet uses separate typed classes per map kind (not a generic `ArtefactMap[S, T]`), matching the typed-field-per-artefact pattern on `StructureMessage`.
- **SDMX 3.0 domain model:** Domain types follow 3.0 semantics (e.g. `Hierarchy` not `HierarchicalCodelist`). The 2.1 reader maps into 3.0 types; the 2.1 writer maps back out.
- **Full ReleaseCalendar:** ContentConstraint includes periodicity, offset, tolerance — not just date ranges.
