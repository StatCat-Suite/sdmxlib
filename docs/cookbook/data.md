# Fetching and Working with Observation Data

Recipes for fetching SDMX observation data, filtering, and converting to
Polars DataFrames. Every recipe goes through one object: the `ObsQuery` that
`reg.obs(flow)` returns — over a provider, a local store, or a file.

---

## Fetching data from a registry

`reg.obs(flow)` takes the dataflow — as a URN, a `Ref`, or a
`Dataflow` you already hold — and returns a lazy query. The registry fetches
the structure when it builds the query, and the data when a terminal such as
`frame()` runs.

```python
import sdmxlib as sl

with sl.RestRegistry(sl.Provider.ECB) as reg:
    obs = reg.obs(sl.Dataflow.urn_for(agency="ECB", id="EXR"))
    df = obs.filter(
        obs.col("FREQ") == "M",
        obs.col("CURRENCY") == "USD",
        obs.col("CURRENCY_DENOM") == "EUR",
        obs.col("EXR_TYPE") == "SP00",
        obs.col("EXR_SUFFIX") == "A",
        obs.col("TIME_PERIOD") >= "2020-01",
    ).frame()

    print(isinstance(obs, sl.ObsQuery))  # True
    print(obs.structure.dimensions.ids())  # ['FREQ', 'CURRENCY', 'CURRENCY_DENOM', 'EXR_TYPE', 'EXR_SUFFIX', 'TIME_PERIOD']
    print([m.id for m in obs.structure.measures])  # ['OBS_VALUE']
```

`obs.structure` is the dataflow's `DataStructure`, so everything the structure
pages describe — dimensions, codelists, labels — is one attribute away.

Or pass a `Dataflow` you already fetched:

```python
with sl.RestRegistry(sl.Provider.ECB) as reg:
    flow = reg.get(sl.Dataflow.urn_for(agency="ECB", id="EXR"), resolve=True)
    obs = reg.obs(flow)
    df = obs.filter(obs.col("FREQ") == "M", obs.col("CURRENCY") == "USD").frame()
```

---

## Filtering with named dimensions

A filter names components by id. Unnamed dimensions are wildcards, and calling
`.filter()` again adds to what is already there — a query is refined, never
mutated.

```python
with sl.RestRegistry(sl.Provider.ECB) as reg:
    obs = reg.obs(sl.Dataflow.urn_for(agency="ECB", id="EXR"))
    monthly = obs.filter(obs.col("FREQ") == "M", obs.col("CURRENCY").is_in(["USD", "GBP"]))

    # Equivalent chained form
    monthly = obs.filter(obs.col("FREQ") == "M").filter(obs.col("CURRENCY").is_in(["USD", "GBP"]))

    monthly.explain()  # what is sent to the provider, and what runs locally
    df = monthly.frame()
```

What the provider can answer — a code per key position, a period bound — is
**pushed down** into the REST key and `startPeriod`/`endPeriod`; anything else
runs locally over what came back. `explain()` says which is which before
anything is fetched.

---

## Pivoting long-format data to wide

Many flows expose indicators as a regular dimension — commonly named `MEASURE`,
`INDICATOR`, or `SUBJECT` — so the data arrives long, one `OBS_VALUE` per
indicator per row. `pivot(on=...)` spreads that dimension into a column per
code:

```python
# OECD KEI — "MEASURE" is a regular dimension, not a formal MeasureDimension
with sl.RestRegistry(sl.Provider.OECD) as reg:
    obs = reg.obs(sl.Dataflow.urn_for(agency="OECD.SDD.STES", id="DSD_KEI@DF_KEI"))
    wide = obs.filter(
        obs.col("REF_AREA").is_in(["USA", "DEU", "FRA"]),
        obs.col("MEASURE").is_in(["GDP", "GFCF"]),  # pushed down to the provider
    ).pivot(on="MEASURE")
```

The columns are the codes the codelist lists, in its order — a code with no
rows after the filter is still a column, so the frame's shape does not depend
on the data. Every other dimension forms the index.

For flows with a formal SDMX 2.1 `<MeasureDimension>`, `on=` is not needed —
`pivot()` reads the axis from the DSD. Without one, and without `on=`, the
error lists the enumerated dimensions that could be the axis:

```text
ValueError: pivot() needs an axis: DSD_KEI declares no measure dimension.
Pass on= one of its enumerated dimensions: REF_AREA, FREQ, MEASURE, UNIT_MEASURE, ACTIVITY.
```

---

## Streaming large data to a file

The sinks write a batch at a time, each typed from the DSD as `frame()` would
type it, so the file holds what a frame would without the frame ever existing:

```python
with sl.RestRegistry(sl.Provider.ECB) as reg:
    obs = reg.obs(sl.Dataflow.urn_for(agency="ECB", id="EXR"))
    monthly = obs.filter(obs.col("FREQ") == "M")
    monthly.sink_parquet("exr.parquet")
    monthly.sink_csv("exr.csv")
    monthly.sink_json("exr.json", lang="en")  # an SDMX-JSON 2.0 data message
```

A query over a provider **fetches first**, and by default into the registry's
own in-memory store — so for a multi-GB flow, give the registry a store on
disk. The fetch then lands in the file, and the sink streams out of it:

```python
with sl.LocalRegistry("lfs_store.duckdb") as local, sl.RestRegistry(sl.Provider.ESTAT, store=local) as reg:
    obs = reg.obs(sl.Dataflow.urn_for(agency="ESTAT", id="LFSI_EMP_A", version="1.0"))
    obs.sink_parquet("lfs.parquet")
```

---

## Downloading the provider's own bytes

`obs()` parses what arrives into a store it can query. When you want
the file the provider actually sent — to keep it, to hand it to a tool that reads
SDMX-ML, or to attach it to a bug report — use `download`, which parses
nothing.

```python
with sl.RestRegistry(sl.Provider.ESTAT) as reg:
    flow = sl.Dataflow.urn_for(agency="ESTAT", id="LFSI_EMP_A", version="1.0")

    # To memory.
    body = reg.download(flow, fmt=sl.DataFormat.SDMX_CSV_2_0_0, key="A.T.Y15-64")

    # Or straight to disk, in chunks — the form for a multi-GB flow.
    path = reg.download(flow, fmt=sl.DataFormat.SDMX_ML_3_0, to="lfs.xml")
```

**The format is asked for, never guessed.** `fmt` is required and becomes the
`Accept` header, so SDMX-ML and SDMX-JSON are reachable — before this they were
not, because the client only ever sent `text/csv`. Two checks keep the argument
honest:

- a destination whose extension contradicts `fmt` is refused before the
  request goes out (`.xml` holding CSV bytes misleads every later reader of the
  file). Extensions sdmxlib does not recognise — `.dat`, `.gz`, none at all —
  are left alone.
- a response whose `Content-Type` is not the format requested is refused.
  Providers that ignore `Accept` are common, and a download has no downstream
  step that would notice.

`sl.DataFormat` has one member per format the standard actually defines, which
is not a free cross-product — SDMX-ML 2.1 carries data under four distinct
media types, and `data+xml` only appears at 3.0:

| member | media type |
| --- | --- |
| `SDMX_CSV_1_0_0` | `application/vnd.sdmx.data+csv;version=1.0.0` |
| `SDMX_CSV_2_0_0` | `application/vnd.sdmx.data+csv;version=2.0.0` |
| `SDMX_CSV_2_1_0` | `application/vnd.sdmx.data+csv;version=2.1.0` |
| `SDMX_JSON_1_0_0` | `application/vnd.sdmx.data+json;version=1.0.0` |
| `SDMX_JSON_2_0_0` | `application/vnd.sdmx.data+json;version=2.0.0` |
| `SDMX_ML_2_1_GENERIC` | `application/vnd.sdmx.genericdata+xml;version=2.1` |
| `SDMX_ML_2_1_GENERIC_TIME_SERIES` | `application/vnd.sdmx.generictimeseriesdata+xml;version=2.1` |
| `SDMX_ML_2_1_STRUCTURE_SPECIFIC` | `application/vnd.sdmx.structurespecificdata+xml;version=2.1` |
| `SDMX_ML_2_1_STRUCTURE_SPECIFIC_TIME_SERIES` | `application/vnd.sdmx.structurespecifictimeseriesdata+xml;version=2.1` |
| `SDMX_ML_3_0` | `application/vnd.sdmx.data+xml;version=3.0.0` |
| `SDMX_ML_3_1` | `application/vnd.sdmx.data+xml;version=3.1.0` |

A download costs **exactly one request** — it needs no DSD, because there is no
key to widen and no columns to interpret. That is the sharpest way to see how
it differs from `obs()`, which resolves the structure first so it can
name columns by component id.

---

## Streaming a large query result

When you are **querying** a large local dataflow and want the rows in your own
code rather than in a file, use `stream()`, which yields
`pyarrow.RecordBatch` instead of materialising a frame:

```python
with sl.LocalRegistry("lake.duckdb") as local:
    obs = local.obs(flow_urn)
    for batch in obs.filter(obs.col("REF_AREA") == "CAN").stream():
        process(batch)          # one chunk at a time
```

Measured on 4M rows of three columns:

| terminal | peak RSS |
| --- | --- |
| `frame()` | 441 MB |
| `stream(chunk_rows=1_000)` | 5 MB |
| `stream(chunk_rows=50_000)` | 5 MB |
| `stream(chunk_rows=500_000)` | ~8 MB |

and the streamed figure does not move at 8M rows, where `frame()` roughly
doubles.

### `chunk_rows` is not the memory knob

Read that table again: 1,000 and 50,000 measure **identically**. Chunk size
decides the batch your consumer receives — size it for a writer's preferred
batch or an HTTP chunk — and starts costing anything only once it is large
enough to be a buffer in its own right.

### What *does* decide it: whether the query sorts

!!! warning "An ordered stream is not bounded"

    A sort has to materialise the whole result before the first batch can
    leave. So an ordered stream holds the entire result in memory no matter
    what `chunk_rows` says — measured at **210 MB on 4M rows and 368 MB on
    8M**, identical at `chunk_rows` of 1,000 and 50,000.

    This is the trap worth knowing, because there is no error and no warning.
    A caller who reaches for `stream()` because `frame()` ran out of memory,
    and then adds `.order_by(...)`, gets the memory back and no clue why —
    and `chunk_rows`, the obvious knob, does nothing.

```text
obs.filter(...).stream()                      # bounded — 5 MB
obs.filter(...).order_by(obs.col("TIME_PERIOD")).stream()   # not bounded
```

`stream()` drops the default key tiebreak for exactly this reason, so an
unordered stream really is unordered — do not rely on the row order of a
streamed result, even when it looks stable.

If you need both order and bounded memory, sort at the **sink** rather than in
the query: write the stream to Parquet and sort on read, or partition the query
and order within each partition.

### `frame()` can drop the tiebreak too — `order_by(None)`

`stream()` used to be the only terminal that could. Every other one sorted on
all of the dataflow's dimensions, whether the caller cared or not:

```sql
ORDER BY s0.FREQ NULLS LAST, s0.REF_AREA NULLS LAST, s0.CHARACTERISTIC NULLS LAST,
         s0.GENDER NULLS LAST, s0.STAT_MEASURE NULLS LAST, s0.TIME_PERIOD NULLS LAST
```

That clause exists so `limit`/`offset` paging is stable, and on a codelist it
is free. On observations it is not. Measured on a 4,261,081-row partition of
a 2021 Census Profile bundle:

| | ms |
| --- | --- |
| `select(...).collect()` | 337 |
| `order_by(None).select(...).collect()` | **75** |
| `frame()` — 27 columns | 1061 |
| `order_by(None).frame()` | **495** |

```python
with sl.LocalRegistry("lake.duckdb") as local:
    obs = local.obs(flow_urn)
    frame = obs.filter(obs.col("REF_AREA") == "CAN").order_by(None).frame()
```

Three states, and the middle one is not the last one:

```text
obs.order_by(obs.col("TIME_PERIOD"))   # sort, then the tiebreak
obs.order_by()                         # clear the keys, keep the tiebreak
obs.order_by(None)                     # no ORDER BY at all
```

!!! warning "An unordered query cannot be paged"

    `limit`, `offset`, `after` and `page` all **raise** on a query that called
    `order_by(None)`. A window over an unspecified scan order silently skips
    and repeats rows between calls, which is the whole reason the tiebreak is
    there. Order it — `order_by()` restores the tiebreak alone — or read it
    whole with `stream()`.

    Terminals that reduce rather than page (`count`, `exists`, `one`,
    `one_or_none`, `preview`) work unchanged. `first()` returns an arbitrary
    match, since "first" in no order is undefined.

---

## Loading a local SDMX-CSV file

A file read through a registry is the same query: `reg.scan_csv(path)` reads
the file's `STRUCTURE_ID` (or 1.0's `DATAFLOW`) column, resolves that dataflow
and its structure through the registry, and returns an `ObsQuery` over the
file's rows.

```python
with sl.RestRegistry(sl.Provider.ECB) as reg:
    obs = reg.scan_csv("ecb_exr.csv")  # reads the file's STRUCTURE_ID, fetches the DSD
    df = obs.frame()                   # and filter, pivot, validate, sink_* all apply
```

A path is read in place on every query; bytes are loaded once.
`reg.scan_json(source)` does the same for an SDMX-JSON data message, either
version.

---

## Filtering by code

`obs.col(id)` names a component, and an id the dataflow does not have raises
there — at the filter — rather than as an empty result later.

```python
with sl.RestRegistry(sl.Provider.ECB) as reg:
    obs = reg.obs(sl.Dataflow.urn_for(agency="ECB", id="EXR"))

    # Single code
    obs.filter(obs.col("FREQ") == "M").frame()

    # Multiple codes
    obs.filter(obs.col("CURRENCY").is_in(["USD", "GBP"])).frame()

    # Combined with a period bound
    obs.filter(obs.col("FREQ") == "M", obs.col("TIME_PERIOD") >= "2020-02").frame()
```

---

## Collecting with human-readable labels

`frame(labels="en")` adds a `{col}_label_en` column beside each enumerated
column and keeps the codes. The language is in the name, so several fit in one
frame.

```python
with sl.RestRegistry(sl.Provider.ECB) as reg:
    obs = reg.obs(sl.Dataflow.urn_for(agency="ECB", id="EXR"))

    df = obs.frame()
    # CURRENCY: pl.Enum([...]) → "USD"

    df_labelled = obs.frame(labels="en")
    # CURRENCY → "USD", CURRENCY_label_en → "US dollar"

    obs.frame(labels=["en", "fr"])  # CURRENCY, CURRENCY_label_en, CURRENCY_label_fr, ...
```

To filter on a label, label first and filter the frame:

```python
import polars as pl

with sl.RestRegistry(sl.Provider.ECB) as reg:
    obs = reg.obs(sl.Dataflow.urn_for(agency="ECB", id="EXR"))
    monthly = obs.frame(labels="en").filter(pl.col("FREQ_label_en") == "Monthly")
```

---

## Validating data against the DSD

`frame()` is typed from the DSD, so a code its codelist does not list stops the
read — on the first one. `validate()` reads the raw rows instead and names
every value that does not fit, with a count. Empty means clean.

```python
with sl.RestRegistry(sl.Provider.ECB) as reg:
    obs = reg.obs(sl.Dataflow.urn_for(agency="ECB", id="EXR"))
    report = obs.validate()

    if report.is_empty():
        print("All values valid")
    else:
        print(report)
        # ┌──────────┬───────────────┬───────┐
        # │ column   │ invalid_value │ count │
        # ╞══════════╪═══════════════╪═══════╡
        # │ FREQ     │ X             │ 12    │
        # └──────────┴───────────────┴───────┘
```

---

## Projecting and aggregating

`select` and `group_by` run in the query — locally, after the fetch — and
return a frame typed the same way:

```python
with sl.RestRegistry(sl.Provider.ECB) as reg:
    obs = reg.obs(sl.Dataflow.urn_for(agency="ECB", id="EXR"))

    # Projection, under names of your choosing
    obs.select(obs.col("CURRENCY"), period=obs.col("TIME_PERIOD"), value=obs.col("OBS_VALUE")).collect()

    # Aggregation
    obs.group_by(obs.col("CURRENCY")).agg(n=sl.len()).collect()
```

For anything else, `frame()` is a plain `polars.DataFrame` — from there it is
polars all the way down:

```python
with sl.RestRegistry(sl.Provider.ECB) as reg:
    obs = reg.obs(sl.Dataflow.urn_for(agency="ECB", id="EXR"))
    df = obs.frame(labels="en")
    df.sort("CURRENCY_label_en", "TIME_PERIOD")
```
