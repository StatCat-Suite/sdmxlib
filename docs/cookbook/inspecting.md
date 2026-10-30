# Inspecting DSDs

Recipes for exploring and analysing Data Structure Definitions.

---

## Inspecting a Data Structure Definition

A DSD defines the schema of a dataset — which dimensions, attributes, and
measures it has, and which codelists enumerate their values.

```python
with sl.RestRegistry(sl.Provider.SDMX_GLOBAL) as reg:
    dsd = reg.get(sl.DataStructure.urn_for(agency="BIS", id="WS_CBPOL"), resolve=True)

    print(f"DSD: {dsd.maintainer.id}:{dsd.id} v{dsd.version}")
    print(f"Dimensions ({len(dsd.dimensions)}):")
    # `dsd.dimensions` is in declaration order, which *is* the key order —
    # so the position is the index rather than an attribute on the component.
    for position, dim in enumerate(dsd.dimensions, start=1):
        if dim.is_enumerated:
            cl = dim.representation()
            print(f"  [{position}] {dim.id:20s}  CL={cl.id}  ({len(cl.code_ids())} codes)")
        else:
            print(f"  [{position}] {dim.id:20s}  text")
    print(f"Time dimension: {dsd.time_dimension.id}")
    print(f"Measure: {dsd.measures[0].id}")
```

```
DSD: BIS:WS_CBPOL v1.0
Dimensions (4):
  [1] FREQ                  CL=CL_FREQ         (7 codes)
  [2] REF_AREA              CL=CL_BIS_IF_REF_AREA  (44 codes)
  [3] INSTR_ASSET           CL=CL_INSTR_ASSET  (12 codes)
  [4] BORROWER_CTY          CL=CL_BIS_IF_REF_AREA  (44 codes)
Time dimension: TIME_PERIOD
Measure: OBS_VALUE
```

---

## Exporting the DSD structure as a table

`sdmxlib.polars.structure_frame` converts a DSD into a tidy Polars DataFrame,
useful for documentation, diffing, or storing in a metadata database.

```python
import sdmxlib as sl
import sdmxlib.polars as slpl

with sl.RestRegistry(sl.Provider.SDMX_GLOBAL) as reg:
    dsd = reg.get(sl.DataStructure.urn_for(agency="BIS", id="WS_CBPOL"), resolve=True)
    meta = slpl.structure_frame(dsd, lang="en")
    print(meta)
```

```
shape: (7, 6)
┌──────────────┬────────────────┬──────────┬──────────────────────────┬──────────────────────────┬─────────┐
│ component    ┆ role           ┆ position ┆ concept                  ┆ codelist                 ┆ n_codes │
│ ---          ┆ ---            ┆ ---      ┆ ---                      ┆ ---                      ┆ ---     │
│ str          ┆ str            ┆ i32      ┆ str                      ┆ str                      ┆ i32     │
╞══════════════╪════════════════╪══════════╪══════════════════════════╪══════════════════════════╪═════════╡
│ FREQ         ┆ dimension      ┆ 1        ┆ Frequency                ┆ CL_FREQ                  ┆ 7       │
│ REF_AREA     ┆ dimension      ┆ 2        ┆ Reference area           ┆ CL_BIS_IF_REF_AREA       ┆ 44      │
│ INSTR_ASSET  ┆ dimension      ┆ 3        ┆ Instrument/Asset type    ┆ CL_INSTR_ASSET           ┆ 12      │
│ BORROWER_CTY ┆ dimension      ┆ 4        ┆ Borrower country         ┆ CL_BIS_IF_REF_AREA       ┆ 44      │
│ TIME_PERIOD  ┆ time_dimension ┆ null     ┆ Time period              ┆ null                     ┆ 0       │
│ OBS_STATUS   ┆ attribute      ┆ null     ┆ Observation status       ┆ CL_OBS_STATUS            ┆ 13      │
│ OBS_VALUE    ┆ measure        ┆ null     ┆ Observation value        ┆ null                     ┆ 0       │
└──────────────┴────────────────┴──────────┴──────────────────────────┴──────────────────────────┴─────────┘
```

---

## Building code maps for ETL pipelines

When loading a CSV or database extract, you often need a mapping from code ID
to human-readable label for each dimension. `dsd.code_map` builds this in one
call.

```python
with sl.RestRegistry(sl.Provider.SDMX_GLOBAL) as reg:
    dsd = reg.get(sl.DataStructure.urn_for(agency="ESTAT", id="NAMAIN_10_GDP"), resolve=True)

    # dict[dim_id, dict[code_id, label]]
    code_maps = dsd.code_map(lang="en")

    # Translate a raw dimension value
    freq_label = code_maps["FREQ"].get("Q", "Q")
    area_label = code_maps["REF_AREA"].get("DE", "DE")
    print(f"Frequency: {freq_label}")
    print(f"Reference area: {area_label}")

    # Or build it yourself for a single dimension
    freq_codes = {code.id: code.name.as_dict().get("en", code.id) for code in dsd.dimensions["FREQ"].representation().codes}
    print(freq_codes)
```

```
Frequency: Quarterly
Reference area: Germany
{'A': 'Annual', 'S': 'Semi-annual', 'Q': 'Quarterly', 'M': 'Monthly',
 'W': 'Weekly', 'D': 'Daily', 'N': 'Non-periodicirregular'}
```

---

## Comparing DSD versions

When a data provider updates a DSD, you need to know which dimensions or
codelists changed before updating your pipeline.

```python
with sl.RestRegistry(sl.Provider.SDMX_GLOBAL) as reg:
    old = reg.get(sl.DataStructure.urn_for(agency="ESTAT", id="NAMAIN_10_GDP", version="1.0"), resolve=True)
    new = reg.get(sl.DataStructure.urn_for(agency="ESTAT", id="NAMAIN_10_GDP", version="2.0"), resolve=True)

    old_dims = old.dimensions.to_dict()
    new_dims = new.dimensions.to_dict()

    added = set(new_dims) - set(old_dims)
    removed = set(old_dims) - set(new_dims)
    print(f"Added dimensions:   {added or 'none'}")
    print(f"Removed dimensions: {removed or 'none'}")

    for dim_id in set(old_dims) & set(new_dims):
        old_dim = old_dims[dim_id]
        new_dim = new_dims[dim_id]
        if old_dim.is_enumerated and new_dim.is_enumerated:
            old_codes = set(old_dim.representation().codes.ids())
            new_codes = set(new_dim.representation().codes.ids())
            added_codes = new_codes - old_codes
            removed_codes = old_codes - new_codes
            if added_codes or removed_codes:
                print(f"\n  {dim_id}:")
                if added_codes:
                    print(f"    + {sorted(added_codes)}")
                if removed_codes:
                    print(f"    - {sorted(removed_codes)}")
```

```
Added dimensions:   none
Removed dimensions: none

  REF_AREA:
    + ['AL', 'BA', 'ME', 'MK', 'RS', 'XK']

  UNIT:
    + ['CLV_I15', 'CP_I15']
    - ['CLV_I10', 'CP_I10']
```

---

## Using pattern matching to inspect components

Pattern matching on `Dimension` objects lets you branch cleanly on whether a
component is enumerated or free-text. An enumerated representation is a
`Ref[Codelist | ValueList]` — SDMX 3.x lets a component be enumerated by a
`ValueList`, which has `items` rather than `codes` — so match on what the
reference unwraps to, or use `code_ids()`, which both carry:

```python
with sl.RestRegistry(sl.Provider.SDMX_GLOBAL) as reg:
    dsd = reg.get(sl.DataStructure.urn_for(agency="BIS", id="WS_CBPOL"), resolve=True)

    for dim in dsd.dimensions:
        match dim.representation:
            case sl.Ref():
                match dim.representation():
                    case sl.ValueList() as vl:
                        print(f"  {dim.id:20s}  values  {vl.id} ({len(vl.items)} values)")
                    case enum:
                        print(f"  {dim.id:20s}  enum    {enum.id} ({len(enum.code_ids())} codes)")
            case sl.Facet(type=dtype):
                print(f"  {dim.id:20s}  text    {dtype}")
            case _:
                print(f"  {dim.id:20s}  (no representation)")
```

```
  FREQ                  enum    CL_FREQ (7 codes)
  REF_AREA              enum    CL_BIS_IF_REF_AREA (44 codes)
  INSTR_ASSET           enum    CL_INSTR_ASSET (12 codes)
  BORROWER_CTY          enum    CL_BIS_IF_REF_AREA (44 codes)
```

---

## Finding the largest codelists

Sort dimensions by codelist size to understand which dimensions will dominate
memory and query performance.

```python
with sl.RestRegistry(sl.Provider.SDMX_GLOBAL) as reg:
    dsd = reg.get(sl.DataStructure.urn_for(agency="ESTAT", id="BSBS_INSTGRP"), resolve=True)

    coded, uncoded = dsd.dimensions.partition(lambda d: d.is_enumerated)
    biggest = coded.sort_by(lambda d: len(d.representation().code_ids()), descending=True)

    print(f"{'Dimension':<20} {'Codelist':<30} Codes")
    print("-" * 58)
    for dim in biggest:
        cl = dim.representation()
        print(f"{dim.id:<20} {cl.id:<30} {len(cl.code_ids())}")
    print(f"\nUncoded dimensions: {uncoded.ids()}")
```

```
Dimension            Codelist                       Codes
──────────────────────────────────────────────────────────
COUNTERPART_AREA     CL_AREA                        250
REF_AREA             CL_AREA                        250
INSTR_ASSET          CL_INSTR_ASSET                  12
FREQ                 CL_FREQ                          7
DECIMALS             CL_DECIMALS                      5

Uncoded dimensions: []
```

`dsd.dimensions` holds the key dimensions only; the time dimension is
`dsd.time_dimension`, so it never appears in either half of the partition.

---

## Filtering with item() expressions

`item()` builds Polars-style predicates for `ItemList.filter()`. Use it
instead of a lambda when the condition is a simple attribute test.

```python
with sl.RestRegistry(sl.Provider.SDMX_GLOBAL) as reg:
    dsd = reg.get(sl.DataStructure.urn_for(agency="ESTAT", id="BSBS_INSTGRP"), resolve=True)

    # Equality — find a specific dimension
    freq = dsd.dimensions.filter(sl.item("id") == "FREQ")

    # Membership — keep only certain dimensions
    key_dims = dsd.dimensions.filter(sl.item("id").is_in(["FREQ", "REF_AREA"]))

    # Truthy — enumerated dimensions only
    coded = dsd.dimensions.filter(sl.item("is_enumerated"))

    # Negation — free-text dimensions only
    free = dsd.dimensions.filter(~sl.item("is_enumerated"))

    # None check — attributes with no codelist
    untyped_attrs = dsd.attributes.filter(sl.item("representation").is_null())

    # Combine with & and | — parenthesise each comparison, because Python
    # binds & and | more tightly than ==
    coded_freq = dsd.dimensions.filter((sl.item("id") == "FREQ") & sl.item("is_enumerated"))
    either = dsd.dimensions.filter((sl.item("id") == "FREQ") | (sl.item("id") == "REF_AREA"))

    print("Enumerated:", coded.ids())
    print("Free-text: ", free.ids())
```

Lambdas and `item()` expressions are interchangeable — use whichever reads
more clearly for the condition at hand. `&`, `|` and `~` accept any of the
three, so an expression can be combined with a lambda.

---

## Hierarchical codelists

Some codelists have parent-child structure. Use the SKOS-aligned
vocabulary — `roots()`, `narrower(id)`, `broader(id)`, and the
`*_transitive` variants — that's shared across `Codelist`, `Hierarchy`,
`CategoryScheme`, and `MetadataStructure`.

```python
with sl.RestRegistry(sl.Provider.SDMX_GLOBAL) as reg:
    # NACE Rev.2 industry classification — sections contain divisions
    cl = reg.get(sl.Codelist.urn_for(agency="ESTAT", id="CL_NACE2"), resolve=True)

    # Top-level entries only
    sections = cl.roots()
    print(f"{len(sections)} sections")

    # Direct children of a specific code (SKOS narrower)
    section_a = cl.codes["A"]
    divisions = cl.narrower("A")
    print(f"Section A — {section_a.name['en'].text}: {divisions.ids()}")

    # All descendants (transitive)
    all_under_a = cl.narrower_transitive("A")  # ItemList[Code]
    print(f"{len(all_under_a)} codes under Section A")

    # Bulk views — one pass each
    cl.depths()  # {code_id: depth}
    cl.broader_map()  # {code_id: parent_id | None}
    cl.inherit_along_broader({"A": "primary"})
    # → {"A": "primary", "01": "primary", "02": "primary", ...}
```

```
21 sections
Section A — Agriculture, forestry and fishing: ['01', '02', '03']
```

For cross-codelist hierarchies that span multiple codelists, see the
[Hierarchies](hierarchies.md) cookbook.
