# sdmxlib

**sdmxlib** is a Python library for fetching and navigating SDMX structural
metadata — Data Structure Definitions, Codelists, Dataflows, and related
artefacts — from any SDMX REST endpoint.

It is designed for analysts and engineers at national statistical agencies (NSAs)
who need to automate ETL pipelines, validate datasets, build data catalogs, or
inspect schema changes between DSD versions.

!!! note "Upgrading?"
    The registry API, the query language and the resolution modes all changed
    after 0.48, and every model class is frozen since 0.68.
    [Upgrading](upgrading.md) lists every breaking change with a before/after —
    there are no deprecation shims to lean on.

## What it does

- Connects to any SDMX 2.1 or 3.0 REST endpoint
- Returns fully resolved domain objects with plain attribute access — no XML,
  no URN chasing
- Filter collections with lambdas or the `item()` expression API
- Build SDMX artefacts locally — frozen, checked as they are built — and
  serialise to SDMX-ML 2.1, 3.0 and 3.1 or SDMX-JSON 2.0
- `LocalRegistry` — a DuckDB-backed persistent store with lazy codelists and
  hierarchies and direct SQL code maps; pass it as a `RestRegistry`'s `store=`
  and everything fetched is kept for offline use
- A path query language over both planes, on a `LocalRegistry` or straight
  against REST: `reg.query(sl.q.dataflow)` for structures,
  `reg.obs(flow)` for rows — see [Querying Metadata](query_tutorial.md)
  and [Querying Observations](data_query_tutorial.md)
- Uniform SKOS-aligned tree API on `Codelist`, `Hierarchy`,
  `CategoryScheme`, `MetadataStructure` (and their lazy SQL variants):
  `roots`/`leaves`/`narrower`/`broader`/`*_transitive`/`depth_of` plus
  bulk projections `depths`/`broader_map`/`ancestors_map`/`inherit_along_broader`.
  A codelist is keyed by code id; the other trees by path (`h["CA.ON"]`)
- Integrates with [Polars](https://pola.rs) for schema generation and data
  validation
- Fully typed — IDE completion and static analysis work throughout

## Observation data

`reg.obs(flow)` is the path query language over a dataflow's rows,
filtered by component — `obs.filter(obs.col("REF_AREA") == "CA")` — on a
`RestRegistry` (pushed into the data key) or a `LocalRegistry` (over a bound
table). Results are Polars frames typed from the DSD, with labels, `pivot()` to
reshape long-format flows to wide, `validate()`, and streaming sinks to
Parquet, CSV and SDMX-JSON. `reg.scan_csv(path)` reads a local file the same
way. See the [data tutorial](data_tutorial.md) and
[data cookbook](cookbook/data.md).

## Quick start

```python
import sdmxlib as sl

with sl.RestRegistry(sl.Provider.SDMX_GLOBAL) as reg:
    dsd = reg.get(sl.DataStructure.urn_for(agency="ESTAT", id="NAMAIN_10_GDP"), resolve=True)

    # What dimensions does this DSD have?
    for dim in dsd.dimensions:
        print(dim.id, "→", dim.representation().id if dim.is_enumerated else dim.id)
```

```text
FREQ        → CL_FREQ
REF_AREA    → CL_AREA_EW
UNIT        → CL_UNIT
NA_ITEM     → CL_NA_ITEM
TIME_FORMAT → CL_TIME_FORMAT
```

## Installation

```bash
uv add sdmxlib
# or: pip install sdmxlib
```

sdmxlib needs Python 3.14 or later.

## Key concepts

| Term          | Meaning                                                       |
| ------------- | ------------------------------------------------------------- |
| **DSD**       | Data Structure Definition — the schema of a dataset           |
| **Dimension** | A key component that identifies an observation                |
| **Codelist**  | A finite set of valid codes (e.g. `A`=Annual, `Q`=Quarterly)  |
| **Concept**   | The semantic meaning of a component                           |
| **Dataflow**  | A published data product backed by a DSD                      |
| **Ref\[T\]**  | A typed reference to another artefact; carries the URN, finds its target on access |
| **item()**    | Expression builder for filtering ItemList collections         |

See the [Building Tutorial](building_tutorial.md) to learn how to create SDMX
artefacts from scratch, and the [Cookbook](cookbook/inspecting.md) for practical recipes.
