# Polars Integration

Recipes for using sdmxlib with [Polars](https://pola.rs) — schema
generation, validation, and label enrichment.

---

## Generating a Polars schema from a DSD

`sdmxlib.polars.schema` returns a `dict[str, pl.DataType]` you can pass
directly to `pl.read_csv` or `pl.scan_csv`. Enumerated dimensions become
`pl.Enum` (which enforces valid codes and saves memory); time periods map to
`pl.String`.

```python
import polars as pl
import sdmxlib as sl
import sdmxlib.polars as slpl

with sl.RestRegistry(sl.Provider.SDMX_GLOBAL) as reg:
    dsd = reg.get(sl.DataStructure.urn_for(agency="ESTAT", id="NAMAIN_10_GDP"), resolve=True)
    sdmx_schema = slpl.schema(dsd)

    for col, dtype in sdmx_schema.items():
        print(f"  {col:20s}  {dtype}")
```

```
  FREQ                  Enum(categories=['A', 'D', 'M', 'N', 'Q', 'S', 'W'])
  REF_AREA              Enum(categories=['AT', 'BE', 'BG', 'CY', ...])
  UNIT                  Enum(categories=['CLV05_MEUR', 'CLV05_MNAC', ...])
  NA_ITEM               Enum(categories=['B1GQ', 'D1', 'D11', ...])
  TIME_FORMAT           Enum(categories=['P1D', 'P1M', 'P1Q', 'P1Y', 'P6M'])
  TIME_PERIOD           String
  OBS_VALUE             Float64
```

Load a CSV file with the generated schema:

```python
df = pl.read_csv("namain_gdp.csv", schema_overrides=sdmx_schema)
print(df.schema)
```

```
Schema({'FREQ': Enum(categories=['A', 'D', 'M', 'N', 'Q', 'S', 'W']),
        'REF_AREA': Enum(categories=['AT', 'BE', 'BG', ...]),
        'TIME_PERIOD': String,
        'OBS_VALUE': Float64})
```

---

## Validating a DataFrame against a DSD

<!-- prettier-ignore -->
!!! warning "Unresolved codelists are skipped"
    If a dimension's codelist could not be resolved (e.g. the registry
    returned an unresolved `Ref`), `validation_report` silently skips that
    column. Check `dim.is_enumerated and dim.representation.resolved` before relying on validation
    coverage for a critical pipeline.

Before loading data into your warehouse, check that every code value in the
extract is valid according to the DSD's codelists.

```python
import polars as pl
import sdmxlib as sl
import sdmxlib.polars as slpl

with sl.RestRegistry(sl.Provider.SDMX_GLOBAL) as reg:
    dsd = reg.get(sl.DataStructure.urn_for(agency="ESTAT", id="NAMAIN_10_GDP"), resolve=True)

    df = pl.read_csv("namain_gdp_extract.csv")
    report = slpl.validation_report(dsd, df)

    if report.is_empty():
        print("All values valid — ready for ingest")
    else:
        print(f"{len(report)} violation(s) found:")
        print(report.sort("column", "count"))
```

```
3 violation(s) found:
shape: (3, 3)
┌──────────┬───────────────┬───────┐
│ column   ┆ invalid_value ┆ count │
│ ---      ┆ ---           ┆ ---   │
│ str      ┆ str           ┆ u32   │
╞══════════╪═══════════════╪═══════╡
│ FREQ     ┆ X             ┆ 12    │
│ REF_AREA ┆ XX            ┆ 4     │
│ REF_AREA ┆ ZZ            ┆ 1     │
└──────────┴───────────────┴───────┘
```

---

## Adding human-readable labels to a DataFrame

After validation, enrich your dataset by adding a label column for every
enumerated dimension. Labels are looked up from the codelist and appended
beside each code column as `{column}_label_{lang}` — `FREQ_label_en`.

```python
import polars as pl
import sdmxlib as sl
import sdmxlib.polars as slpl

with sl.RestRegistry(sl.Provider.SDMX_GLOBAL) as reg:
    dsd = reg.get(sl.DataStructure.urn_for(agency="ESTAT", id="NAMAIN_10_GDP"), resolve=True)

    df = pl.read_csv("namain_gdp_extract.csv")
    labelled = slpl.label(df.lazy(), dsd, lang="en").collect()

    print(labelled.select("FREQ", "FREQ_label_en", "REF_AREA", "REF_AREA_label_en").head(5))
```

The language is part of the column name, always — so asking for a second one
later *adds* a column instead of renaming the first, and whatever read
`FREQ_label_en` keeps working. `slpl.label(df, dsd, lang=["en", "fr"])` gives
both in one pass. `Query.frame(labels="en")` and `Select.collect(labels="en")`
both come through this one function, so they name their label columns the
same way.

```
shape: (5, 4)
┌──────┬───────────────┬──────────┬───────────────────┐
│ FREQ ┆ FREQ_label_en ┆ REF_AREA ┆ REF_AREA_label_en │
│ ---  ┆ ---           ┆ ---      ┆ ---               │
│ str  ┆ str           ┆ str      ┆ str               │
╞══════╪═══════════════╪══════════╪═══════════════════╡
│ Q    ┆ Quarterly     ┆ DE       ┆ Germany           │
│ Q    ┆ Quarterly     ┆ FR       ┆ France            │
│ A    ┆ Annual        ┆ DE       ┆ Germany           │
│ A    ┆ Annual        ┆ IT       ┆ Italy             │
│ M    ┆ Monthly       ┆ ES       ┆ Spain             │
└──────┴───────────────┴──────────┴───────────────────┘
```
