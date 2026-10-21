# Working with SDMX Observation Data

A practical walkthrough of fetching, exploring, and analysing SDMX
observation data with sdmxlib and Polars. We use the ECB Exchange Rate
dataset (EXR) — small enough to be interactive, familiar enough to
illustrate real patterns.

---

## Setup

```python
import polars as pl
import sdmxlib as sl
import sdmxlib.polars as slpl
```

---

## 1. Understanding the observation query

sdmxlib represents a dataflow's observations as an `ObsQuery` — the same
path language `reg.query(...)` speaks over structural metadata, rooted at one
dataflow. It is **lazy**: building it, filtering it and selecting from it
fetch nothing. Rows arrive only at a terminal — `frame`, `collect`, `stream`,
`pivot`, `validate`, or one of the `sink_*` methods.

The DSD is always present: it names the columns, types them, drives
validation, and supplies the labels.

```
ObsQuery
├── structure: DataStructure   ← the DSD, resolved by the registry
├── filter / select / group_by ← compiled, pushed to the server where it can be
└── frame / pivot / validate / sink_*  ← terminals: rows arrive here
```

---

## 2. Fetching data

`reg.obs()` takes the dataflow — a `Dataflow` you already hold, a
`Ref`, or its URN. Filter with predicates over `obs.col(...)`, then call a
terminal:

```python
reg = sl.RestRegistry(sl.Provider.ECB)  # closed in section 6; `with` works too

obs = reg.obs(sl.Dataflow.urn_for(agency="ECB", id="EXR"))
obs = obs.filter(
    obs.col("FREQ") == "M",
    obs.col("CURRENCY").is_in(["USD", "GBP", "JPY"]),
    obs.col("CURRENCY_DENOM") == "EUR",
    obs.col("EXR_TYPE") == "SP00",
    obs.col("EXR_SUFFIX") == "A",
)
df = obs.frame()
```

A predicate on a dimension becomes part of the SDMX REST key, so only the
matching rows come over the wire; anything the server cannot express runs
locally after the fetch. Unconstrained dimensions are wildcards. Calling
`.filter()` again adds to the predicates rather than replacing them.
`obs.explain()` says which side each predicate landed on.

The registry fetches the `Dataflow` with its structure resolved (codelists
included), then fetches the data as SDMX-CSV the first time a terminal runs.

### Inspect what you have

```python
print(obs.structure.dimensions.ids())
# ["FREQ", "CURRENCY", "CURRENCY_DENOM", "EXR_TYPE", "EXR_SUFFIX", "TIME_PERIOD"]

print([m.id for m in obs.structure.measures])
# ["OBS_VALUE"]

print(obs.components)
# every component id the query can name

print(df.schema)
# {"FREQ": Enum(categories=["A", "D", "M", "Q"]),
#  "CURRENCY": Enum(categories=["AED", "AFN", ...]),
#  "TIME_PERIOD": String,
#  "OBS_VALUE": String}
```

### Peek at the data

```python
print(df.head())
# shape: (5, 7)
# ┌──────┬──────────┬─────────────────┬──────────┬────────────┬─────────────┬───────────┐
# │ FREQ ┆ CURRENCY ┆ CURRENCY_DENOM  ┆ EXR_TYPE ┆ EXR_SUFFIX ┆ TIME_PERIOD ┆ OBS_VALUE │
# │ ---  ┆ ---      ┆ ---             ┆ ---      ┆ ---        ┆ ---         ┆ ---       │
# │ enum ┆ enum     ┆ enum            ┆ enum     ┆ enum       ┆ str         ┆ str       │
# ╞══════╪══════════╪═════════════════╪══════════╪════════════╪═════════════╪═══════════╡
# │ M    ┆ GBP      ┆ EUR             ┆ SP00     ┆ A          ┆ 2015-01     ┆ 0.7752    │
# │ M    ┆ GBP      ┆ EUR             ┆ SP00     ┆ A          ┆ 2015-02     ┆ 0.7467    │
```

Note that `FREQ`, `CURRENCY` etc. are `pl.Enum` — every column is typed from
the DSD, so a code its codelist does not list raises immediately rather than
reaching your analysis (section 5 shows how to get a report instead).

The DSD, not the values, decides each type. ECB declares `OBS_VALUE` as
`textType="String"`, so it arrives as `String`; a measure the DSD declares
numeric (`Double`, `Decimal`, `Numeric`, ...) arrives as `Float64`. Cast
before arithmetic (see the [full example](#9-full-example-exchange-rate-analysis)).

---

## 3. Filtering

### Predicates

`obs.col(id)` names a component and raises on a typo, listing the valid ids.
Comparisons, `is_in`, and ordering all work, and every predicate passed to one
`filter` call must hold:

```python
obs = reg.obs(sl.Dataflow.urn_for(agency="ECB", id="EXR"))

# USD/EUR monthly rate since 2020
usd = obs.filter(obs.col("CURRENCY") == "USD", obs.col("TIME_PERIOD") >= "2020-01")
usd.frame().shape  # (months, 7): one row per month

# Multiple currencies
major = obs.filter(obs.col("CURRENCY").is_in(["USD", "GBP", "JPY"]))
```

### Chaining

A query is immutable — each `filter` returns a new one, and nothing runs until
a terminal:

```python
recent_usd = obs.filter(obs.col("CURRENCY") == "USD").filter(obs.col("TIME_PERIOD") >= "2020-01")
print(recent_usd.explain())
result = recent_usd.frame()
```

### Projection

`select` keeps only the columns you name, optionally under a new name, and
`order_by` sorts — both compiled, so the work happens before the rows reach
Polars:

```python
series = (
    obs.filter(obs.col("CURRENCY") == "USD")
    .select(period=obs.col("TIME_PERIOD"), rate=obs.col("OBS_VALUE"))
    .order_by("period")
    .collect()
)
```

---

## 4. Codes vs labels

By default a terminal gives you codes as `pl.Enum`:

```python
df = obs.frame()
print(df["CURRENCY"].dtype)
# Enum(categories=["AED", "AFN", ..., "USD", ...])
```

For human-readable output, `frame(labels="en")` adds a `{col}_label_en`
column beside each enumerated column, keeping the codes. The language is in
the name, so `labels=["en", "fr"]` puts both in one frame:

```python
df_l = major.frame(labels="en")

print(df_l["CURRENCY"][0])  # GBP
print(df_l["CURRENCY_label_en"][0])  # 'Pound sterling'
print(df_l["FREQ_label_en"][0])  # Monthly
```

Time periods and observation values are unchanged — they have no codelist.
A projection takes `labels=` too: `obs.select(...).collect(labels="en")`.

---

## 5. Validation

A typed read stops at the first bad code. Before loading data into a pipeline,
ask for the full report instead — `validate()` reads the raw rows and names
every value its codelist does not list, with a count:

```python
report = obs.validate()

if report.is_empty():
    print("Clean — ready for ingest")
else:
    print(f"{len(report)} violation(s):")
    print(report)
```

`validate()` only checks enumerated columns with a resolved codelist.
Non-enumerated columns (free text, time periods, numeric values) are typed,
not validated.

---

## 6. Escaping to Polars

A terminal hands back a plain `pl.DataFrame`, and from there it is Polars all
the way — join it, reshape it, plot it:

```python
usd_ts = usd.frame().select("TIME_PERIOD", "OBS_VALUE").sort("TIME_PERIOD")
```

Labels work the same way: ask for them at the terminal, then filter on the
label column like any other:

```python
us_dollar = obs.frame(labels="en").filter(pl.col("CURRENCY_label_en") == "US dollar")
```

### Wide format — one currency per column

The ECB EXR data arrives in long/tidy format (one row per observation). To
compare currencies side by side, `pivot(on=)` spreads one dimension's values
into columns — in the order its codelist lists them — with every other
dimension as the index:

```python
wide = major.pivot(on="CURRENCY")

print(wide.select("TIME_PERIOD", "USD", "GBP", "JPY").head(3))
# ┌─────────────┬────────┬────────┬──────────┐
# │ TIME_PERIOD ┆ USD    ┆ GBP    ┆ JPY      │
# │ ---         ┆ ---    ┆ ---    ┆ ---      │
# │ str         ┆ str    ┆ str    ┆ str      │
# ╞═════════════╪════════╪════════╪══════════╡
# │ 2015-01     ┆ 1.1278 ┆ 0.7752 ┆ 134.61   │
# │ 2015-02     ┆ 1.1339 ┆ 0.7467 ┆ 135.00   │
# │ 2015-03     ┆ 1.0822 ┆ 0.7248 ┆ 129.12   │
# └─────────────┴────────┴────────┴──────────┘
```

For an SDMX 2.1 flow whose DSD declares a `MeasureDimension`, `pivot()` with
no argument spreads that — see the [Cookbook](cookbook/data.md) for the pattern.

---

## 7. Streaming large datasets

For results that do not fit in memory, write them straight to a file. The
`sink_*` methods read the query a batch at a time, typing each batch from the
DSD, so the file holds what `frame()` would without the frame:

```python
major.sink_parquet("exr_major.parquet")
major.sink_csv("exr_major.csv")
reg.close()
```

A REST query fetches before it streams, and by default the fetched rows land
in the registry's own in-memory store. To keep a multi-GB fetch off the heap,
give the registry a `LocalRegistry` on disk to write through:

```python
with sl.LocalRegistry("exr.duckdb") as store, sl.RestRegistry(sl.Provider.ECB, store=store) as reg:
    obs = reg.obs(sl.Dataflow.urn_for(agency="ECB", id="EXR"))
    obs.filter(obs.col("FREQ") == "M").sink_parquet("exr_monthly.parquet")
```

`stream()` is the raw-batch escape hatch — Arrow record batches, untyped,
for a writer of your own.

---

## 8. Loading a local SDMX-CSV

If you have a downloaded SDMX-CSV file, the registry reads it the same way:

```python
with sl.RestRegistry(sl.Provider.ECB) as reg:
    obs = reg.scan_csv("ecb_exr.csv")
    df = obs.frame()
```

`reg.scan_csv` reads the file's `STRUCTURE_ID` (or 1.0's `DATAFLOW`, e.g.
`ECB:EXR(1.0)`), fetches and resolves the Dataflow with its structure, then
returns an `ObsQuery` over the file — the same query `reg.obs(flow)`
returns, so `filter`, `pivot`, `validate` and the `sink_*` methods all apply.
The file is read in place on every query. A `LocalRegistry` holding the
dataflow and its structure does the same without a network.

---

## 9. Full example — exchange rate analysis

Putting it together: fetch monthly USD/EUR and GBP/EUR rates, validate,
produce a labelled summary, then save a wide-format Parquet for downstream use.

```python
import polars as pl
import sdmxlib as sl

with sl.RestRegistry(sl.Provider.ECB) as reg:
    obs = reg.obs(sl.Dataflow.urn_for(agency="ECB", id="EXR"))
    obs = obs.filter(
        obs.col("FREQ") == "M",
        obs.col("CURRENCY").is_in(["USD", "GBP"]),
        obs.col("CURRENCY_DENOM") == "EUR",
        obs.col("EXR_TYPE") == "SP00",
        obs.col("EXR_SUFFIX") == "A",
        obs.col("TIME_PERIOD") >= "2020-01",
    )

    # 1. Validate
    report = obs.validate()
    assert report.is_empty(), f"Data quality issues:\n{report}"

    # 2. Summary statistics by currency (labels in output)
    # ECB declares OBS_VALUE as a string (textType="String"), and columns are
    # typed from the DSD, so cast before doing arithmetic. Without it, min/max
    # compare as text ("1.10" < "1.9").
    value = pl.col("OBS_VALUE").cast(pl.Float64)
    summary = (
        obs.frame(labels="en")
        .group_by("CURRENCY_label_en")
        .agg(
            value.mean().alias("mean"),
            value.min().alias("min"),
            value.max().alias("max"),
        )
        .sort("CURRENCY_label_en")
    )
    print(summary)
    # ┌───────────────────┬────────┬────────┬────────┐
    # │ CURRENCY_label_en ┆ mean   ┆ min    ┆ max    │
    # │ ---               ┆ ---    ┆ ---    ┆ ---    │
    # │ str               ┆ f64    ┆ f64    ┆ f64    │
    # ╞═══════════════════╪════════╪════════╪════════╡
    # │ Pound sterling    ┆ 0.8612 ┆ 0.8321 ┆ 0.9239 │
    # │ US dollar         ┆ 1.0934 ┆ 0.9699 ┆ 1.2296 │
    # └───────────────────┴────────┴────────┴────────┘

    # 3. Wide format for charting / further analysis, and save
    obs.pivot(on="CURRENCY").write_parquet("exr_wide.parquet")
```
