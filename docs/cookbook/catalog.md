# Data Catalog

Recipes for browsing an SDMX registry's catalog — listing dataflows,
filtering by dimension, and navigating category schemes.

---

## Browsing your agency's data catalog

List all dataflows published by an agency, with their DSD and the number of
dimensions.

```python
with sl.RestRegistry(sl.Provider.SDMX_GLOBAL) as reg:
    flows = reg.query(sl.q.dataflow).filter(sl.q.dataflow.agency == "OECD.SDD.NAD").collect()

    print(f"{'ID':<30} {'Name':<45} Dims")
    print("-" * 82)
    for df in flows:
        name = df.name.get("en", "")[:44]
        n_dims = len(df.structure().dimensions) if df.structure.resolved else "?"
        print(f"{df.id:<30} {name:<45} {n_dims}")
```

```
ID                             Name                                          Dims
──────────────────────────────────────────────────────────────────────────────────
NAMAIN_10_GDP                  National Accounts Main Aggregates — GDP       6
SNA_TABLE1                     National Accounts - Expenditure Approach      5
SNA_TABLE5                     National Accounts - Output Approach           5
SNA_TABLE6                     National Accounts - Capital Formation         6
SNA_TABLE7                     National Accounts - Financial Accounts        7
```

---

## Filtering dataflows by dimension

Find all dataflows whose DSD contains a `REF_AREA` dimension enumerated by a
specific codelist — useful when building a cross-domain input validation layer.

```python
with sl.RestRegistry(sl.Provider.SDMX_GLOBAL) as reg:
    flows = (
        reg.query(sl.q.dataflow)
        .filter(
            sl.q.dataflow.agency == "BIS",
            sl.q.dataflow.dsd.agency == "BIS",
        )
        .collect()
    )

    area_flows = []
    for df in flows:
        if not df.structure.resolved:
            continue
        dsd = df.structure()
        if "REF_AREA" in dsd.dimensions:
            dim = dsd.dimensions["REF_AREA"]
            if dim.is_enumerated:
                cl = dim.representation()
                area_flows.append((df.id, cl.id, len(cl.codes)))

    area_flows.sort(key=lambda r: r[2], reverse=True)
    for flow_id, cl_id, n in area_flows[:5]:
        print(f"{flow_id:<30} {cl_id:<30} {n} codes")
```

```
WS_CBPOL                       CL_BIS_IF_REF_AREA             44 codes
WS_LBS_D_PUB                   CL_BIS_IF_REF_AREA             44 codes
WS_EER                         CL_BIS_IF_REF_AREA             44 codes
WS_LONG_CPI                    CL_BIS_IF_REF_AREA             44 codes
WS_CREDIT_GAP                  CL_BIS_IF_REF_AREA             44 codes
```

---

## Narrowing a catalogue search

`reg.query()` returns the path language's `Query`, and it refines: `.filter()`,
`.order_by()`, `.limit()`, `.select()` all compose.

What changed in #306 is *where each clause runs*, and that it is no longer a
secret. SDMX REST accepts one constraint on a collection request — the agency —
so the planner pushes that and runs everything else over the materialised rows.
The old `Query[T]` offered the same-looking `.filter()` over a fetched list,
which made a full catalogue download read as a bounded request; now the split
is planned rather than implied, and a query that would push nothing is refused
instead.

```python
with sl.RestRegistry(sl.Provider.SDMX_GLOBAL) as reg:
    priced = (
        reg.query(sl.q.dataflow)
        .filter(
            sl.q.dataflow.agency == "ESTAT",
            sl.q.dataflow.name["en"].str.caseless.contains("price"),
        )
        .collect()
    )

    for df in sorted(priced, key=lambda df: df.id)[:5]:
        print(df.id, df.name.get("en", ""))
```

The name search runs locally — no SDMX REST URL can express it — but the
*agency* filter is pushed, so one request is issued and the search runs over
one agency's dataflows rather than every registry's. Leave the agency out and
the query is **refused** rather than quietly downloading a catalogue to find
three rows in it. The error names the column to add.

### The query decides the resolution depth

This is the part that changed most. There used to be three terminals —
`browse()`, `fetch()`, `resolve()` — and picking the wrong one meant either a
missing reference or a download you did not need.

Now the depth follows from the question. The planner reads which relations your
filters and projections reach, and covers each one — sometimes by deepening
`references=`, sometimes by scanning that relation separately:

```python
# Touches nothing outside the dataflow.
#   -> 1 request:  dataflow?references=none
reg.query(sl.q.dataflow).filter(sl.q.dataflow.agency == "ECB").ids()

# Reaches the DSD itself. The DSD is a maintainable with its own endpoint, so
# it is scanned rather than pulled through the dataflow.
#   -> 2 requests: datastructure?references=none
#                  dataflow?references=none
reg.query(sl.q.dataflow).filter(
    sl.q.dataflow.agency == "ECB",
    sl.q.dataflow.dsd.agency == "ECB",
    sl.q.dataflow.dsd.id == "ECB_EXR1",
).collect()

# Reaches the DSD's *components*, which have no endpoint of their own and come
# back inside their owner.
#   -> 2 requests: datastructure?references=children
#                  dataflow?references=none
reg.query(sl.q.dataflow).filter(
    sl.q.dataflow.agency == "ECB",
    sl.q.dataflow.dsd.agency == "ECB",
    sl.q.dataflow.dsd.dimensions.any(sl.q.component.concept.id == "CURRENCY"),
).collect()
```

The count is a property of the plan, not of the data: a query whose request
count grew with the number of rows returned would be the N+1 problem wearing a
planner's clothes.

Fetching one level short would not raise — the query would evaluate against a
relation that was never downloaded and answer *nothing* — which is exactly why
this is derived rather than left to the caller.

**An agency is needed on every relation the query touches**, not just the
root. A dataflow's agency does not imply its DSD's: cross-agency reuse of
structures is normal SDMX, so the planner will not guess, and the examples
above state both.

### When you want one named artefact, ask for it

Do **not** fetch a catalogue and pick one out of it. `reg.get()` is a single
request for a single artefact:

```python
# One request, by identity
exr = reg.get(sl.Dataflow.urn_for(agency="ECB", id="ECB_EXR1"), resolve=True)
```

`Query.one()` / `Query.one_or_none()` remain for the case where the filters
really do select one artefact — they say "I expect exactly one" (or "at most
one") — but if you know the id, `reg.get()` is both cheaper and clearer.

---

## Navigating from a Categorisation to its dataflow

Category schemes let registries organise dataflows into a hierarchical catalog.
Walk from the category scheme to its categorised dataflows.

```python
with sl.RestRegistry(sl.Provider.SDMX_GLOBAL) as reg:
    scheme = reg.get(sl.CategoryScheme.urn_for(agency="ESTAT", id="DATAFLOWS"), resolve=True)

    def walk(cats, indent=0):
        for cat in cats:
            print("  " * indent + f"[{cat.id}] {cat.name.get('en', '')}")
            walk(cat.categories, indent + 1)

    walk(scheme.categories)
```

```
[ECONOMY] Economy
  [NATIONAL_ACCOUNTS] National Accounts
    [GDP] Gross Domestic Product
    [GFCF] Gross Fixed Capital Formation
  [PRICES] Prices
    [CPI] Consumer Price Index
    [PPI] Producer Price Index
[DEMOGRAPHY] Demography
  [POPULATION] Population
```
