# Querying Observations

The [metadata tutorial](query_tutorial.md) asks questions about the catalogue —
which codelists exist, what a structure contains. This one asks questions about
the *numbers*: which rows, aggregated how, labelled in which language.

It is the same path language, rooted at a dataflow instead of a structural
relation. If you have read the metadata tutorial, most of this will look
familiar; the differences are the interesting part, and they come from one
fact — an observation table is not part of the SDMX graph, so the library has
to be told where it is.

## What a binding is

Structural artefacts live in the registry's own tables. Observations do not:
they are usually in a lake, a Parquet file, or a table someone else loads. A
**`DataflowBinding`** records where a dataflow's rows are and which physical
column each SDMX component maps to.

```python
print(binding.table_location)                # 'data.cpi'
print(sorted(binding.dimension_column_map))  # ['FREQ', 'REF_AREA', 'TIME_PERIOD']
print(binding.measure_column_map)            # {'OBS_VALUE': 'OBS_VALUE'}
```

That map is what lets you write `REF_AREA` and have it work even when the
column is called `geo_code`. You will not usually build a binding by hand —
whatever loads the observations records one beside them, and a published
bundle (`sl.LocalRegistry.bundle(path)`) carries its own — but knowing it
exists explains the shape of everything below.

## The catalogue used here

One dataflow, `OECD:CPI`, with annual consumer-price inflation for three
countries over three years:

| FREQ | REF_AREA | TIME_PERIOD | OBS_VALUE | OBS_STATUS |
| --- | --- | --- | --- | --- |
| A | CA | 2021 · 2022 · 2023 | 3.4 · 6.8 · 3.9 | A · A · P |
| A | US | 2021 · 2022 · 2023 | 4.7 · 8.0 · 5.3 | A · A · P |
| A | MX | 2021 · 2022 · 2023 | 5.7 · 7.9 · 5.6 | A · A · P |

`store` is an open `DuckDBDataStore`, `binding` its binding, `dsd` the
structure.

---

## 1. The first query

```python
obs = store.obs(binding, dsd=dsd)
print(obs.frame().columns)   # ['FREQ', 'REF_AREA', 'TIME_PERIOD', 'OBS_VALUE', 'OBS_STATUS']
print(obs.frame().height)    # 9
```

`store.obs(...)` is the root, and like a structural query it is lazy —
`.frame()` is what runs it.

`obs` is reusable. Every refinement below returns a new query and leaves `obs`
alone, so you can keep it around as "all the rows" and branch from it.

## 2. Components, not columns

Filters name **SDMX component ids**, not physical columns:

```python
print(obs.filter(obs.col("REF_AREA") == "CA").frame().height)   # 3
```

`obs.col("REF_AREA")` goes through the binding's map. If the table's column
were `geo_code`, this line would be unchanged — which is the point: your code
talks about the DSD, and the binding absorbs whatever the storage calls things.

Get the id wrong and you are told, with the alternatives:

```python
try:
    obs.col("COUNTRY")
except KeyError as err:
    print(err)
# "'COUNTRY' is not a component of this dataflow. It has: FREQ, OBS_STATUS, OBS_VALUE, REF_AREA, TIME_PERIOD"
```

That check is the reason this API exists. The interface it replaced took SQL
text — `where=["REF_AREA = 'CA'"]` — so a typo became either a database error
at the wrong layer or, with values from a request, something worse.

## 3. Filtering

Several predicates in one `filter` are ANDed:

```python
rows = obs.filter(obs.col("REF_AREA") == "CA", obs.col("TIME_PERIOD") >= "2022").frame()
print(rows.height)   # 2
```

Comparisons work as you would expect on any column, including the time
dimension — `TIME_PERIOD` is a string in SDMX and orders correctly as one for
the common period formats.

```python
print(obs.filter(obs.col("OBS_STATUS") == "P").frame().height)   # 3
```

Attributes filter exactly like dimensions. Nothing in the query language cares
which role a component plays; the binding knows, and that is enough.

### Matching text

`.str` reads a column as text and matches inside it. These are exactly the
string operators SDMX-REST allows in a component filter, so a service can hand
`c[REF_AREA]=sw:M` straight through:

| SDMX-REST | query |
| --- | --- |
| `co:x` | `col.str.contains("x")` |
| `nc:x` | `~col.str.contains("x")` |
| `sw:x` | `col.str.starts_with("x")` |
| `ew:x` | `col.str.ends_with("x")` |

```python
area = obs.col("REF_AREA")
print(obs.filter(area.str.starts_with("M")).frame().height)   # 3
print(obs.filter(~area.str.contains("A")).frame().height)     # 6
```

Pass the value as the caller sent it: `%`, `_` and `\` in it match literally,
because the query language escapes them and states the `ESCAPE` clause itself.
Matching is case-sensitive, like SQL's `LIKE`; `.str.caseless.contains("a")`
is the `ILIKE` form. And a missing value neither contains nor fails to contain,
so it is left out of `nc:` as well as `co:` — add `| area.is_null()` if a
request means "anything without x, including no value".

## 4. Projecting

```python
rows = (
    obs.filter(obs.col("TIME_PERIOD") == "2023")
    .select(area=obs.col("REF_AREA"), inflation=obs.col("OBS_VALUE"))
    .collect()
)
print(rows.to_dicts())
# [{'area': 'CA', 'inflation': 3.9}, {'area': 'MX', 'inflation': 5.6}, {'area': 'US', 'inflation': 5.3}]
```

`.frame()` gives you every column; `.select()` gives you the ones you name,
under the names you choose.

## 5. Aggregating

```python
rows = (
    obs.group_by(obs.col("REF_AREA"))
    .agg(peak=obs.col("OBS_VALUE").max(), n=obs.col("OBS_VALUE").count())
    .order_by("REF_AREA")
    .collect()
)
print(rows.to_dicts())
# [{'REF_AREA': 'CA', 'peak': 6.8, 'n': 3}, {'REF_AREA': 'MX', 'peak': 7.9, 'n': 3}, {'REF_AREA': 'US', 'peak': 8.0, 'n': 3}]
```

The grouping key keeps its component id as the column name unless you alias it.

### Several answers in one scan

With no grouping, `agg` folds the whole query into one row — the shape of
SDMX-REST's `/availability`, which asks which values each dimension takes under
a filter, and how many observations there are:

```python
recent = obs.filter(obs.col("TIME_PERIOD") >= "2022")
row = recent.agg(
    areas=obs.col("REF_AREA").sort().unique(),
    statuses=obs.col("OBS_STATUS").sort().unique(),
    n=sl.len(),
).collect()
print(row.to_dicts())
# [{'areas': ['CA', 'MX', 'US'], 'statuses': ['A', 'P'], 'n': 6}]
```

`sl.len()` counts rows, as polars' `pl.len()` does. `unique()` is unordered
unless you `.sort()` first, and keeps a missing value if there is one. One scan
is not always faster than one `select(...).unique()` per column — on a large
unfiltered table it can lose — so reach for it when a filter narrows the scan.

### The result is a polars frame

Once you have collected, you are in polars — so anything the query language
does not do, polars does:

```python
rows = obs.group_by(obs.col("REF_AREA")).agg(mean=obs.col("OBS_VALUE").mean()).order_by("REF_AREA").collect()
print(rows.with_columns(pl.col("mean").round(2)).to_dicts())
# [{'REF_AREA': 'CA', 'mean': 4.7}, {'REF_AREA': 'MX', 'mean': 6.37}, {'REF_AREA': 'US', 'mean': 5.6}]
```

Rounding a float is a display concern, and pushing it into the query would buy
nothing. The boundary is worth knowing deliberately: **filtering and
aggregating happen in the database, over rows you never materialise;
everything after `.collect()` happens in memory, over rows you did.**

## 6. Labels

An observation row holds codes. `labels=` attaches the human-readable name for
each enumerated component:

```python
rows = obs.filter(obs.col("TIME_PERIOD") == "2023").select(area=obs.col("REF_AREA")).collect(labels="en")
print(rows.columns)     # ['area', 'area_label_en']
print(rows.to_dicts())
# [{'area': 'CA', 'area_label_en': 'Canada'}, {'area': 'MX', 'area_label_en': 'Mexico'}, {'area': 'US', 'area_label_en': 'United States'}]
```

**The language is always in the column name**, including when you ask for only
one. Adding a second language later therefore cannot rename the first and break
whatever was reading it:

```python
rows = obs.filter(obs.col("REF_AREA") == "CA").select(area=obs.col("REF_AREA")).collect(labels=["en", "fr"])
print(rows.columns)   # ['area', 'area_label_en', 'area_label_fr']
```

A column projected under an alias is labelled under that alias — the name in
the frame is the name you chose, not the component id.

!!! note "Labels need the structure"
    Names live on codelists, which live in the registry — not in the
    observation table. That is why `store.obs()` takes `dsd=`, and
    why `reg.obs(flow)` on a `LocalRegistry` is the easier entry
    point when the registry holds the binding: it looks the binding up and
    resolves the structure for you. Ask for labels without one and the error
    says so rather than returning unlabelled columns.

## 7. Ordering and limits

```python
rows = (
    obs.order_by(obs.col("OBS_VALUE").desc())
    .limit(2)
    .select(area=obs.col("REF_AREA"), year=obs.col("TIME_PERIOD"), value=obs.col("OBS_VALUE"))
    .collect()
)
print(rows.to_dicts())
# [{'area': 'US', 'year': '2022', 'value': 8.0}, {'area': 'MX', 'year': '2022', 'value': 7.9}]
```

The worst two years of inflation in the set. `.desc()` marks the key, as it
does on the structural side.

## 8. Results too large to hold

`.frame()` and `.collect()` build the whole result in memory. When that is not
an option, `.stream()` hands back an Arrow reader instead:

```python
reader = obs.stream()
print(sum(batch.num_rows for batch in reader))   # 9
```

Nine rows do not need streaming. Nine hundred million do, and the query is
written identically — which is the point of showing it here rather than in a
performance note.

## 9. Filtering from a request

When the component ids and values arrive together — from an HTTP query string,
say — `ComponentFilter` takes them as parallel arrays:

```python
selection = sl.ComponentFilter(dimension_ids=["REF_AREA"], dimensions=[["CA", "US"]])
print(obs.filter_components(selection).frame().height)   # 6
```

An empty value list means "no values match", not "no filter" — so a request
that narrows to nothing returns nothing, rather than silently returning
everything.

## 10. When you need SQL

The same escape hatch as the structural side, and the same rules — `$name`
parameters, never interpolation:

```python
rows = store.connection.execute(
    "SELECT REF_AREA, count(*) AS n FROM data.cpi GROUP BY REF_AREA ORDER BY REF_AREA"
).fetchall()
print(rows)   # [('CA', 3), ('MX', 3), ('US', 3)]
```

Reach for it when you need something the query language does not express —
`PIVOT`, window frames over irregular periods, value formatting. Everything
else is better asked in components, because a component id is checked and a
column name is not.

---

## Where to go next

- [Querying structural metadata](query_tutorial.md) — the catalogue side, same
  path language
- [Fetching observation data](cookbook/data.md) — getting rows from a REST
  service in the first place
- [Querying observations without SQL](design/data-plane-query.md) — why this
  API exists, and what it replaced
- [Pushing an observation query to REST](design/data-plane-pushdown.md) — what
  happens when the rows are not local
