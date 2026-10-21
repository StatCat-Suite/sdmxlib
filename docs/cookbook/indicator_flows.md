# Indicator Flows — Long-Format Data and `pivot(on=)`

Many SDMX providers publish data in **long format**: instead of one column
per indicator, all indicators share a single dimension (often named
`MEASURE`, `INDICATOR`, or `SUBJECT`) and a single `OBS_VALUE` column.
Each row carries one indicator value.

This recipe shows how to fetch, filter, and pivot these flows into a
wide analytical table with `reg.obs(flow)` and `pivot(on=)`.

---

## Why `on=` is needed

SDMX 2.1 has a formal `<MeasureDimension>` element, but almost no major
provider uses it. In practice, providers put indicator codes in an ordinary
dimension. sdmxlib cannot know which dimension carries indicator codes, so
you name it when you pivot:

```python
obs = reg.obs(flow)
wide = obs.pivot(on="MEASURE")
```

When the DSD *does* declare a `MeasureDimension`, `obs.pivot()` uses it
without being told — `obs.structure.measure_dimension` says whether there
is one:

```python
print(obs.structure.measure_dimension)  # None
```

### Discovering the right dimension name

If you call `pivot()` with no axis on a structure that declares none, sdmxlib
raises a `ValueError` that names every enumerated dimension as a candidate:

```python
try:
    obs.pivot()
except ValueError as exc:
    print(exc)
# pivot() needs an axis: DSD_KEI declares no measure dimension. Pass on= one
# of its enumerated dimensions: REF_AREA, FREQ, MEASURE, UNIT_MEASURE, ACTIVITY.
```

You can also inspect the DSD directly before fetching any data:

```python
with sl.RestRegistry(sl.Provider.OECD) as reg:
    urn = sl.DataStructure.urn_for(agency="OECD.SDD.STES", id="DSD_KEI")
    dsd = reg.get(urn, resolve=True)

    for dim in dsd.dimensions:
        if dim.is_enumerated:
            cl = dim.representation()
            print(f"{dim.id}: {cl.id} ({len(cl.codes)} codes)")
```

```
REF_AREA:       CL_KEI_REF_AREA (54 codes)
FREQ:           CL_FREQ (6 codes)
MEASURE:        CL_KEI_MEASURE (50 codes)
UNIT_MEASURE:   CL_UNIT_MEASURE (12 codes)
ACTIVITY:       CL_KEI_ACTIVITY (3 codes)
ADJUSTMENT:     CL_KEI_ADJUSTMENT (3 codes)
TRANSFORMATION: CL_KEI_TRANSFORMATION (6 codes)
```

The dimension with many codes covering economic indicator names is the one
to pass as `on=`.

---

## Fetching a long-format flow

```python
import sdmxlib as sl

reg = sl.RestRegistry(sl.Provider.OECD)
flow = reg.get(sl.Dataflow.urn_for(agency="OECD.SDD.STES", id="DSD_KEI@DF_KEI"))
obs = reg.obs(flow)
kei = obs.filter(
    obs.col("REF_AREA").is_in(["USA", "DEU", "FRA", "GBR"]),
    obs.col("FREQ") == "Q",
    obs.col("MEASURE").is_in(["GDP", "GFCF", "EMP"]),
)
long = kei.frame()
```

`obs(flow)` is lazy: nothing is fetched until a terminal such as
`frame()` runs — so a query needs its registry open, and the sections below
keep using this `reg`. Close it (`reg.close()`), or use `with`, when done. The filters on dimensions are pushed into the SDMX REST key,
so only matching rows come over the wire — the `MEASURE` filter included,
without you needing to know its position in the key. `kei.explain()` shows
what is pushed and what runs locally.

---

## Inspecting what you have

```python
print(kei.structure.dimensions.ids())  # ["REF_AREA", "FREQ", "MEASURE", "UNIT_MEASURE", "ACTIVITY", "TIME_PERIOD"]
print(kei.structure.measures.ids())  # ["OBS_VALUE"]

# Peek at the raw long format
long.head(5)
# ┌──────────┬──────┬─────────┬─────────────┬───────────┐
# │ REF_AREA ┆ FREQ ┆ MEASURE ┆ TIME_PERIOD ┆ OBS_VALUE │
# │ ---      ┆ ---  ┆ ---     ┆ ---         ┆ ---       │
# │ enum     ┆ enum ┆ enum    ┆ str         ┆ f64       │
# ╞══════════╪══════╪═════════╪═════════════╪═══════════╡
# │ DEU      ┆ Q    ┆ EMP     ┆ 2015-Q1     ┆ 39.8      │
# │ DEU      ┆ Q    ┆ GDP     ┆ 2015-Q1     ┆ 772.3     │
# ...
```

Coded columns are typed from the DSD: each is a `pl.Enum` over its codelist.

---

## Pivoting to wide format

`pivot(on="MEASURE")` reshapes long → wide, one column per indicator, in the
order the `MEASURE` codelist lists them. Every other dimension forms the
index. Returns a `pl.DataFrame`.

```python
wide = kei.pivot(on="MEASURE")

print(wide.columns)  # ["REF_AREA", "FREQ", "UNIT_MEASURE", "ACTIVITY", "TIME_PERIOD", "GDP", "GFCF", "EMP"]
```

The dimension columns that are constant for your filter are still present —
drop them after pivoting if not needed:

```python
import polars as pl

wide = kei.pivot(on="MEASURE").select("REF_AREA", "TIME_PERIOD", "GDP", "GFCF", "EMP").sort("REF_AREA", "TIME_PERIOD")
```

A code the codelist lists but the data lacks is still a column, filled with
nulls: the structure decides the columns, not whichever rows arrived.

---

## Writing to Parquet

The long format streams to a file a batch at a time, typed as `frame()` is,
without the whole result in memory:

```python
kei.sink_parquet("kei_long.parquet")
```

A pivot needs every row of a series before it can emit one, so the wide
table is a frame you write:

```python
kei.pivot(on="MEASURE").write_parquet("kei_wide.parquet")
```

---

## Filtering further

An `ObsQuery` refines: each `filter` returns a new query, and the fetch
happens at the terminal.

```python
# By code
usa = kei.filter(kei.col("REF_AREA") == "USA")

# Any comparison on a component
recent_usa = usa.filter(usa.col("TIME_PERIOD") >= "2015-Q2")

# Collect with human-readable labels beside the codes
recent_usa.frame(labels="en")
```

`labels="en"` keeps each code column and adds `REF_AREA_label_en` and the
like beside it.

---

## Validating indicator data

Before loading into a pipeline, check that all codes are valid for their
codelists. A typed read stops at the first bad code; `validate()` reads the
raw rows and names every one, with a count:

```python
report = kei.validate()

if report.is_empty():
    print("Clean — ready for ingest")
else:
    print(report.sort("count", descending=True))
    # ┌──────────┬───────────────┬───────┐
    # │ column   │ invalid_value │ count │
    # └──────────┴───────────────┴───────┘
```

---

## Full example — G7 quarterly GDP comparison

Fetch quarterly GDP for G7 countries, validate, reshape to one column per
country, and save.

```python
import sdmxlib as sl

G7 = ["USA", "DEU", "FRA", "GBR", "ITA", "JPN", "CAN"]

with sl.RestRegistry(sl.Provider.OECD) as reg:
    flow = reg.get(sl.Dataflow.urn_for(agency="OECD.SDD.STES", id="DSD_KEI@DF_KEI"))
    obs = reg.obs(flow)
    gdp = obs.filter(
        obs.col("REF_AREA").is_in(G7),
        obs.col("FREQ") == "Q",
        obs.col("MEASURE") == "GDP",
    )

    # 1. Validate
    report = gdp.validate()
    assert report.is_empty(), f"Unexpected codes:\n{report}"

    # 2. Pivot on the country instead — one GDP column per country
    wide = gdp.pivot(on="REF_AREA").select("TIME_PERIOD", *G7).sort("TIME_PERIOD")

print(wide.columns)  # ["TIME_PERIOD", "USA", "DEU", "FRA", "GBR", "ITA", "JPN", "CAN"]

# 3. Save
wide.write_parquet("g7_gdp_quarterly.parquet")
```

The axis is whichever dimension you name: `on="REF_AREA"` spreads the
countries, and `MEASURE`, filtered to one indicator, stays in the index.
