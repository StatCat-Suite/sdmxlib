# Data Layer Design

## What is shipped (Phase 1)

The data layer is Polars-first: observation data is always a `pl.LazyFrame`
paired with a resolved `DataStructure`. There are no intermediate observation
objects — the DSD drives the Polars schema directly.

### Core stack

```
RestRegistry.data() → DataQuery → SdmxClient.get_data() / stream_data_to()
                                            ↓
                            formats/sdmx_csv/reader.scan_csv()
                                            ↓
                              Dataset(structure, data: pl.LazyFrame)
                              ↙         ↓          ↓         ↘
                     .filter()    .collect()   .pivot()   .sink_parquet()
                                            (→ pl.LazyFrame, then collect/sink)
```

### Key classes

| Class / function              | Location                     | Role                                                                                         |
| ----------------------------- | ---------------------------- | -------------------------------------------------------------------------------------------- |
| `Dataset`                     | `model/dataset.py`           | Lazy frame + DSD; `measure_dim_hint` for regular-dim pivot                                   |
| `DataQuery`                   | `api/query.py`               | Fluent builder — `.slice()`, `.measures()`, `fetch()`, `fetch_to()`, `pivot()`, `pivot_to()` |
| `DimFilter` / `dim()`         | `api/filters.py`             | Server-side key expressions: `sl.dim("FREQ") == "M"`, `.isin([...])`                         |
| `scan_csv`                    | `formats/sdmx_csv/reader.py` | Parses SDMX-CSV bytes or file path into a `Dataset`                                          |
| `SdmxClient.get_data()`       | `api/client.py`              | In-memory HTTP fetch (`text/csv`)                                                            |
| `SdmxClient.stream_data_to()` | `api/client.py`              | Chunked HTTP streaming to file                                                               |
| `RestRegistry.data()`         | `api/registry.py`            | Entry point; `measure_dim=` hint for indicator-axis dims                                     |
| `RestRegistry.scan_csv()`     | `api/registry.py`            | Reads a local SDMX-CSV, auto-resolves DSD from `DATAFLOW` column                             |

### Dataset API

```python
ds.dimensions          # ordered dimension ids, including MeasureDimension and TIME_PERIOD
ds.measures            # ["OBS_VALUE"] or multi-measure for SDMX 3.0
ds.wide_dimension      # measure axis id — from DSD MeasureDimension or measure_dim_hint
ds.measure_dim_hint    # user-supplied hint when DSD has no formal MeasureDimension
ds.schema              # dict[str, pl.DataType] — DSD-derived Polars schema

ds.code("FREQ")        # validated pl.Expr — early error on typos
ds.label("FREQ")       # pl.Expr mapping codes → labels via codelist

ds.filter(predicate)   # → Dataset (stays lazy)
ds.collect()           # → pl.DataFrame (terminal)
ds.collect(with_labels=True, lang="fr")
ds.lazy()              # → pl.LazyFrame (escape hatch)
ds.lazy(with_labels=True)

ds.pivot()             # → pl.LazyFrame (wide; uses measure axis, raises with candidates if absent)
ds.pivot().collect()   # → pl.DataFrame
ds.pivot().sink_parquet("wide.parquet")

ds.validate()          # → pl.DataFrame of codelist violations
ds.sink_csv(path)      # streaming terminal
ds.sink_parquet(path)  # streaming terminal
```

### DataQuery API

```python
reg.data(flow, measure_dim="MEASURE")        # declare indicator axis for regular dims
    .slice(sl.dim("FREQ") == "M")            # server-side key filter (accumulates)
    .slice(sl.dim("REF_AREA").isin(["AT"]))  # chained or variadic
    .measures(["EMP", "UNE"])                # server-side indicator filter
    .fetch()                                 # → Dataset (in-memory)
    .fetch_to("raw.csv")                     # → Dataset (file-backed, streaming)
    .pivot()                                 # → pl.LazyFrame (fetch + pivot)
    .pivot_to("wide.parquet")               # stream → pivot → sink (never fully in memory)
```

### Measure axis resolution

`Dataset.pivot()` resolves the pivot column from (in priority order):

1. The DSD's formal `<MeasureDimension>` element (SDMX 2.1 cross-sectional — rare).
2. The `measure_dim=` hint passed to `reg.data()` (the common path for providers
   using a regular dimension named `MEASURE`, `INDICATOR`, `SUBJECT`, etc.).
3. If neither is present, raises `ValueError` listing enumerated dimensions as
   candidates with a corrective example.

`on_columns` for `LazyFrame.pivot` is sourced from the resolved codelist of
the pivot dimension (preferred), falling back to a mini-collect of unique values
if the codelist is unavailable.

---

## Remaining work

### 1. SDMX-JSON data reader

Wire format: `application/vnd.sdmx.data+json`. Used by some agencies that
don't offer SDMX-CSV (or where JSON is preferred for small key queries).

```
formats/sdmx_json/
  data_reader.py   # parse SDMX-JSON → Dataset
```

- Accept header: `application/vnd.sdmx.data+json;version=2.0`
- SDMX-JSON data structure is nested (series → observations), not tabular —
  needs flattening into a `pl.DataFrame` before wrapping in a `LazyFrame`.
- Add `SdmxClient.get_data_json()` alongside the existing CSV path.
- Wire into `DataQuery` as an optional format (`DataQuery.fetch(format="json")`
  or content-negotiation by server capability).

### 2. `slpl.scan_parquet(path, structure)` — local round-trip

After `ds.sink_parquet("cache.parquet")` the user has no way to reload the
file back into a `Dataset` — they get a raw `pl.LazyFrame` with no DSD
attached.

```python
import sdmxlib.polars as slpl

ds = slpl.scan_parquet("cache.parquet", dsd)
# → Dataset backed by pl.scan_parquet, DSD re-attached
```

Simpler than a full store: just `Dataset(structure=dsd, data=pl.scan_parquet(path))`.
Add as a companion to the existing `slpl.scan_csv`.

### 3. `ParquetStore` — managed local cache

A key-value store for observation data on disk, keyed by
`(agency, id, version, key, start_period, end_period)`. Avoids re-fetching
unchanged data across sessions.

Design sketch:

```python
store = sl.ParquetStore("~/.sdmxlib/cache")

with sl.RestRegistry(sl.Provider.ESTAT) as reg:
    ds = store.get_or_fetch(reg, sl.Dataflow, agency="ESTAT", id="LFSI_EMP_A")
    # cache hit → scan_parquet; miss → fetch_to + sink_parquet + scan_parquet

store.invalidate(agency="ESTAT", id="LFSI_EMP_A")
store.list()   # → list of cached (agency, id, version, key) tuples
```

Backed by a directory of Parquet files with a manifest JSON. A DeltaLake or
Iceberg variant could support incremental appends (new periods only) but that
is a separate concern.

### 4. Multi-provider / availability queries

Some use cases need to know what data is actually available before fetching
(date ranges, key combinations). SDMX REST supports
`/availability/dataflow/{agency}/{id}/{version}/{key}` for this.

```python
reg.data(sl.Dataflow, agency="ESTAT", id="LFSI_EMP_A").availability()
# → AvailabilityReport with date ranges and key combinations present on the server
```

This is lower priority — `start_period` / `end_period` on `fetch()` already
covers the common case.

---

## Design constraints (carry forward)

- `Dataset.data` is always `pl.LazyFrame` — no exceptions. Tiers differ only
  in what backs the frame (bytes in memory vs file on disk vs Parquet).
- The DSD is always fully resolved before a `Dataset` is created. No lazy
  resolution inside `Dataset`.
- `Dataset` is not a `MaintainableArtefact` — it has no URN. It is a plain
  `@define` class.
- Core model (`sdmxlib.model`) never imports Polars. All Polars coupling lives
  in `sdmxlib/polars.py` and `sdmxlib/formats/sdmx_csv/`.
