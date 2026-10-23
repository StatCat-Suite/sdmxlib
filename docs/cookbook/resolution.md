# Resolution Modes

SDMX REST endpoints let you control how much metadata comes back in one
request. sdmxlib exposes that as **one parameter** — `resolve=` on
`reg.get(...)` — plus a `partial` flag that trims codelist items to the values a
dataflow actually constrains.

Choosing well avoids downloading what you will not read: a catalogue of 500
dataflows as fully resolved objects is megabytes; the same catalogue as
identity stubs is milliseconds.

Collections are a different question. `reg.query(...)` decides its own request
from what the query *reads* — a projection of ids is served by stubs — so there
is no mode to choose there. See "Catalogue scanning" below.

---

## The three modes at a glance

| Call                                   | Wire params                                          | What you get                                                                     |
| -------------------------------------- | ---------------------------------------------------- | -------------------------------------------------------------------------------- |
| `get(urn)`                             | `detail=full` + `references=none`                    | Complete artefact. Refs present but not followed.                                |
| `get(urn, resolve=True)`               | `detail=referencepartial` + `references=descendants` | Artefact plus all referenced artefacts, codelists trimmed to constrained values. |
| `get(urn, resolve=True, partial=False)`| `detail=full` + `references=descendants`             | Same, but codelists contain every code rather than the constrained ones.         |
| `get(urn, resolve=[sl.Codelist])`      | `detail=referencepartial` + `references=codelist`    | Only references of the types you name.                                           |

**The default differs between registries because the cost does.**
`RestRegistry.get` does not resolve, because following a reference is another
round trip; `LocalRegistry.get` takes no `resolve=` at all, because locally a
reference finds its target in the store when you call it — a join away.

---

## Catalogue scanning

Listing what a registry contains is a *query*, not a resolution mode, and the
planner decides the request from what the query reads: a projection of ids is
served by `detail=allstubs`, so item collections never come over the wire at
all. There is no mode to pass.

```python
import sdmxlib as sl

with sl.RestRegistry(sl.Provider.ECB) as reg:
    stubs = reg.query(sl.q.dataflow).filter(sl.q.dataflow.agency == "ECB").collect()

    for df in stubs:
        print(df.id, df.name.get("en", ""))
```

```
ECB_EXR1  Exchange Rates
ECB_BSI1  Balance Sheet Items
ECB_MIR1  MFI Interest Rates
...
```

A stub's references point at artefacts that were never fetched, so calling
`df.structure()` on one raises `sl.UnresolvedRef`. Stubs are for building
lists — call `get(urn, resolve=True)` when you need to navigate inside an
artefact, or let the query reach the DSD (below) so the planner fetches it.

`collect()` returns a plain list, so narrow it with Python. SDMX REST takes one
constraint on a collection request — the agency — so any other narrowing
happens after the download however it is written.

```python
with sl.RestRegistry(sl.Provider.ECB) as reg:
    stubs = reg.query(sl.q.dataflow).filter(sl.q.dataflow.agency == "ECB").collect()
    exr = sorted((df for df in stubs if "EXR" in df.id), key=lambda df: df.id)
```

---

## No resolution — the artefact's own fields

`get(urn)` against a `RestRegistry` downloads the complete artefact and follows
no outgoing reference. A dimension's `representation` is a `Ref[Codelist]` whose
`.resolved` is `False`. Use it when you need the artefact's own fields and will
not navigate outward.

```python
with sl.RestRegistry(sl.Provider.ECB) as reg:
    dsd = reg.get(sl.DataStructure.urn_for(agency="ECB", id="ECB_EXR1"))

    # Own fields are populated
    print(dsd.id, dsd.name.get("en"))
    for dim in dsd.dimensions.filter(sl.item("is_enumerated")):
        print(f"  {dim.id}  resolved={dim.representation.resolved}")
```

```
ECB_EXR1  Exchange Rates
  FREQ     resolved=False
  CURRENCY resolved=False
  ...
```

Calling `dim.representation()` on a `Ref` whose target is not held raises
`sl.UnresolvedRef`. Fetch the target separately — `reg.get(dim.representation)`
takes the `Ref` itself, and the ref finds what it fetched on the next call — or
ask for them all up front with `resolve=True`.

---

## `resolve=True` — the full graph, codelists trimmed

`resolve=True` fetches the artefact and all its referenced artefacts in one
request. By default (`partial=True`) the server trims codelists to only the
codes that are valid for this dataflow — the result is compact and immediately
usable for building data-entry forms or validating keys.

```python
with sl.RestRegistry(sl.Provider.ECB) as reg:
    dsd = reg.get(sl.DataStructure.urn_for(agency="ECB", id="ECB_EXR1"), resolve=True)

    for dim in dsd.dimensions:
        if dim.is_enumerated:
            cl = dim.representation()
            print(f"{dim.id:<20} {len(cl.codes)} codes")
```

```
FREQ                 2 codes   ← only D and M (daily and monthly have data)
CURRENCY             42 codes  ← only currencies with actual observations
CURRENCY_DENOM       1 code    ← only EUR
EXR_TYPE             1 code    ← only SP00
EXR_SUFFIX           1 code
```

The trimmed counts reflect a `DataConstraint` (2.1's `ContentConstraint`)
attached to the dataflow on the server. This is the right mode for building queries and validating keys.

---

## `partial=False` — full codelists, for audits

Pass `partial=False` to receive every code in every referenced codelist, not
just the constrained subset. Use this when you need the authoritative master
list — for example, comparing what a registry publishes against what your
database contains.

```python
with sl.RestRegistry(sl.Provider.ECB) as reg:
    dsd = reg.get(sl.DataStructure.urn_for(agency="ECB", id="ECB_EXR1"), resolve=True, partial=False)

    freq_cl = dsd.dimensions["FREQ"].representation()
    print(f"Constrained codes: 2  Full codelist: {len(freq_cl.codes)}")
    # Constrained codes: 2  Full codelist: 8
```

The full codelist includes codes like `A` (annual), `B` (business day), and `H`
(semi-annual) that exist in the registry but have no ECB exchange-rate data.

---

## `resolve=[...]` — only the types you name

`reg.get()` takes a type, to fetch only that kind of referenced artefact —
useful when you want a DSD but not its codelists yet.

```python
with sl.RestRegistry(sl.Provider.ECB) as reg:
    dsd = reg.get(sl.DataStructure.urn_for(agency="ECB", id="ECB_EXR1"), resolve=[sl.Codelist])
```

For a *collection*, `reg.query()` derives the depth from what the query
reaches, and asks for that. Fetching one level short would not
raise — the query would evaluate against a relation that was never downloaded
and answer nothing — so it is derived rather than left to the caller.

```python
with sl.RestRegistry(sl.Provider.ECB) as reg:
    # Reaches the DSD, so the DSDs are fetched with the dataflows.
    flows = reg.query(sl.q.dataflow).filter(
        sl.q.dataflow.agency == "ECB",
        sl.q.dataflow.dsd.agency == "ECB",
    ).collect()

    for df in flows:
        print(df.id, len(df.structure().dimensions), "dims")
```

See `cookbook/catalog.md` for what an agency filter is required on, and why.

---

## Choosing

```
Listing many artefacts, or reading one?
│
├─ Many → reg.query(...)          the planner picks the request from what
│                                 the query reads; stubs when ids suffice
└─ One  → reg.get(urn, ...)
   │
   ├─ Do you need referenced artefacts (codelists, DSDs)?
   │  │
   │  ├─ No  → get(urn)                                (refs unresolved)
   │  │
   │  └─ Yes
   │     │
   │     ├─ Only some kinds of them?
   │     │  └─ get(urn, resolve=[sl.Codelist])
   │     │
   │     ├─ Building a query or validating keys?
   │     │  └─ get(urn, resolve=True)                  (trimmed codelists)
   │     │
   │     └─ Auditing against master lists?
   │        └─ get(urn, resolve=True, partial=False)   (full codelists)
```

---

## Caching across calls

sdmxlib caches every fetched artefact inside the `RestRegistry`. A codelist
resolved in one call does not trigger a second HTTP request if the same codelist
is referenced by a different DSD.

```python
with sl.RestRegistry(sl.Provider.ECB) as reg:
    # First call — fetches DSD + all codelists
    dsd1 = reg.get(sl.DataStructure.urn_for(agency="ECB", id="ECB_EXR1"), resolve=True)

    # Second call — DSD already cached, no HTTP request made
    dsd1_again = reg.get(sl.DataStructure.urn_for(agency="ECB", id="ECB_EXR1"), resolve=True)

    # All artefacts fetched so far are available in reg.registry
    all_codelists = reg.registry.get_all(sl.Codelist)
    print(f"{len(all_codelists)} codelists accumulated")
```

The `.registry` property exposes the accumulated `InMemoryRegistry` — every
reference fetched so far is bound to it — useful for bulk inspection after a
series of requests.
