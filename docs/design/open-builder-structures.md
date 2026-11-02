# Open builders and structure products

*   **Status:** built for [GH-347](https://github.com/StatCat-Suite/sdmxlib/issues/347)
*   **Date:** 2026-11-02
*   **Read against:** sdmxlib `a7d3ea05`, statcatlib `41d4b88`
*   **Suite design:** [`sharded-bundle-catalog.md`](sharded-bundle-catalog.md) §6.5 item 4 and §8, and Phase 3 of [`sharded-bundle-catalog-plan.md`](sharded-bundle-catalog-plan.md#phase-3-structure-products-sdmxlib-then-statcatlib-statcat-suitestatcatlib51-and-an-sdmxlib-issue-to-file-spec-m6)

## Problem

`build_edition` (`edition/_build.py`) builds a product's first edition. It
refuses a directory that is not empty, opens its own `LocalRegistry.build`
builder, gathers structures only from dataflow closures, and seals. Phase 3
needs three things it cannot do:

1. statcatlib's `WorkingCopy` opens the builder itself, on any edition of a
   product (`LocalRegistry.build(product_dir, previous=)` or `resume=`), and
   seals it itself with its `statcat` section. Its ingest needs the closure
   walk and the partial cut on that builder.
2. A structure product holds full artefacts and no dataflow. The owner split
   StatCan's structure products by change rate on 2026-11-02 (plan Q2,
   specification open question 8): `structure-geo`, `structure-classifications`
   and `structure-concepts`. The geography is one codelist among many, so a
   product is chosen by explicit artefacts as well as by agency and family.
3. A data product's partials come from codelists in more than one structure
   product, so `check_edition(structures=)` takes several structure editions.

## Goals

- A caller fills a builder it opened with dataflow closures, observations and
  partials, and seals it with `seal(partial_of=...)`.
- A caller fills a builder it opened with a structures-only set chosen by
  family, agency, id pattern and explicit URN.
- `check_edition(structures=)` checks each partial against whichever of
  several structure editions holds its full artefact.
- `build_edition` keeps its signature and its behaviour, as a wrapper over the
  first function.

## Non-goals

- No change to a cross-repo contract. The manifest stays 3.0
  (`partial_of` already exists), the store schema stays 2.52, the Inventory
  and `catalog.json` stay 1.0.
- The cross-product rules (specification §6.5 items 4, 7 and 8 across
  products) stay with statcatlib's Inventory writer, as the plan assigns them.
- Fetching a structures-only set over SDMX REST. `fill_structures` reads a
  registry that holds the artefacts; a producer ingests an FMR's structures
  into a `LocalRegistry` first.

## Design

All three live in `sdmxlib.edition` beside `build_edition`.

### `fill_edition`

```python
def fill_edition(
    builder: LocalRegistry,
    source: RestRegistry | EditionRegistry,
    dataflows: Sequence[SdmxUrn[Dataflow]],
    *,
    observations: Mapping[str, WriteSource] | None = None,
    partials: bool = True,
) -> dict[str, str]
```

It adds each dataflow's closure at `source` to `builder`, writes and binds the
observations it is given, and, with `partials`, cuts the builder's codelists.
It returns `partial_of`: the full fingerprint of each codelist it cut, by URN,
for the caller to pass to `seal`. It neither opens, closes nor seals the
builder. The body is `build_edition`'s, moved; `build_edition` becomes:

```python
root.mkdir(...)                      # after the not-empty refusal, as now
with LocalRegistry.build(root) as builder:
    cut = fill_edition(builder, source, dataflows, observations=..., partials=...)
    stored = ...                     # the provenance's artefact list, as now
return seal(builder.edition_dir, ..., partial_of=cut)
```

A builder opened with `previous=` already holds artefacts, bindings and
tables. Three parts of the body change so that a filled builder that held
nothing before gives exactly what `build_edition` gave, and a builder that held
something keeps it:

- **The closure from a `RestRegistry`.** Today every fetched artefact goes in
  and what the closure does not reach comes out. That would drop the
  previous edition's other structures. Now an artefact the builder already
  held is left alone during the walk, the fetched artefacts outside the
  closure come out, and then each closure member the builder held before is
  replaced by the fetched one. In an empty builder the first and last steps
  do nothing. An `EditionRegistry` source already copies the closure alone.
- **The cut covers the whole builder.** A codelist two dataflows share is cut
  to the codes both use. In a republish of one of them the other's binding
  and table are already in the builder, so the cut reads every
  `DataflowBinding` the builder holds, plus the dataflows of this call that
  have no binding (their codelists stay full, as now). In an empty builder
  the bindings are the ones this call wrote, so nothing changes.
- **A partial already in the builder is kept.** The cut skips a codelist
  stored as a partial, as now. When the closure brings the full codelist, the
  store keeps the full one (IM §3.5.3.1, GH-298), and the cut makes a new
  partial from it with a new `partial_of`.

### `seal` keeps a partial's `partial_of`

A builder opened with `previous=` holds the previous edition's partials, and
today `seal` records `partial_of` only for what its caller names. A republish
that does not re-cut a partial would lose its `partial_of`, and the partials
check would skip it. `seal` now takes `partial_of` from the previous edition's
manifest for each artefact that is still a partial and that `partial_of=` does
not name. `fold_overlays` already passes the previous manifest's `partial_of`
for every partial, so its result is unchanged. The manifest format is
unchanged: the field exists and keeps its meaning.

### `fill_structures`

```python
def fill_structures(
    builder: LocalRegistry,
    source: EditionRegistry,
    *,
    families: Collection[type[MaintainableArtefact]] = (),
    agency: str | None = None,
    ids: Collection[str] = (),
    artefacts: Collection[SdmxUrn[MaintainableArtefact] | str] = (),
) -> tuple[str, ...]
```

It copies full artefacts from `source` into `builder` and returns their URNs,
sorted. An artefact is selected when `artefacts` names its URN, or when its
class is in `families`, its agency is `agency` (any agency when `None`) and
its id matches one of the `ids` patterns (`fnmatch.fnmatchcase`, so `SGC*`;
every id when `ids` is empty). For StatCan's split:

```python
fill_structures(b, fmr, families=[sl.ConceptScheme], agency="STC")            # structure-concepts
fill_structures(b, fmr, families=[sl.Codelist], agency="STC", ids=["SGC*"])   # structure-geo
fill_structures(b, fmr, artefacts=[naics_urn, noc_urn], ...)                  # structure-classifications
```

The selection runs over `sdmx.artefact` (`artefact_type`, `agency_id`,
`resource_id`, `is_partial`) and copies with `get(..., eager=True)` and
`add_all`, as `build_edition` copies a closure from an `EditionRegistry`.
`families` takes the stored classes (`storage._kinds.CLASS_TO_NAME`), so
`Codelist` selects geographic codelists too. Refusals, raised before anything is
written:

- a family that is not a stored artefact class (a `GeographicCodelist`
  names `Codelist` in its message), or is `Dataflow` or `DataflowBinding`
  (`TypeError`): a structure product binds no dataflow
  (specification §8);
- a URN in `artefacts` that names a dataflow or a binding, that the source
  does not hold, or that it holds as a partial (`ValueError`);
- nothing selected at all (`ValueError`).

Family selection skips partial artefacts, so a data edition can serve as a
source. Nothing is added for what the selected artefacts reference: invariant
7 gives each full artefact one home, so a concept scheme whose concepts name a
codelist in another structure product keeps that reference open, and
`check_edition`'s `references` check reports it (open question 1).

### `check_edition(structures=)` over several editions

`structures` takes `str | Path | Sequence[str | Path]`. Each partial that
records `partial_of` is checked against the structure editions holding a full
artefact at its URN:

- none holds one: a finding naming the editions given;
- one with that fingerprint holds it: its items are compared, as now;
- every holder has another fingerprint: a finding naming each edition and its
  fingerprint.

With one edition the findings read exactly as now. Two editions that hold one
URN as full artefacts with different fingerprints break invariant 7; that is
the Inventory writer's refusal (§6.5 items 4 and 7), and here only the
matching one is used.

## Tests

`tests/unit/test_edition_fill.py`:

- `fill_edition` on a builder the test opened: the closure and the cut land,
  the builder stays open, the returned `partial_of` sealed through `seal`
  checks clean against the source.
- A republish: a builder opened with `previous=` on an edition of two
  dataflows sharing a codelist, refilled for one of them with new codes, keeps
  the other's structures and binding, cuts the codelist to the codes both use,
  and checks clean.
- The same republish from a `RestRegistry` (a mocked provider serving every
  artefact for any request) keeps the other dataflow and leaves out a fetched
  codelist outside the closure.
- A republish that refills nothing keeps each partial's `partial_of` (the
  `seal` carry), and a `partial_of=` given to `seal` wins over it.
- `fill_structures` by family and agency, by id pattern, and by explicit URN;
  family selection skipping partials; each refusal above, writing nothing.
- `check_edition(structures=[a, b])`: a partial whose full artefact is in the
  second structure product checks clean; a partial with an extra code is a
  finding naming the code; a URN in neither is a finding naming both; a
  fingerprint differing in both names both.

`tests/unit/test_edition_build.py` stays as it is and pins `build_edition`.

## Open questions

1. A structure product's references to artefacts in another structure product
   (a concept's core representation naming a codelist in
   `structure-classifications`) are `references` findings at
   `check_edition(at="publish")`. statcatlib's seal of a `structure-concepts`
   edition will see them. Either `check_edition` takes the other structure
   editions for the `references` check too, or the specification lets a
   structure product's references point to the catalog's other structure
   products. Both need a specification decision.
2. `fill_edition` returns `partial_of`, and the caller keeps it until it
   seals. A `WorkingCopy` resumed in another process (`resume=True`) has lost
   it. statcatlib can keep it with the working copy, or sdmxlib can record it
   in the build store's `sdmx._meta` for `seal` to read; the second puts a new
   key in `metadata.duckdb` and is a store contract statcatlib reads, so it
   waits for the statcatlib design.
