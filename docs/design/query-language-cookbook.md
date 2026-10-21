# The query language: worked solutions

Companion to [`query-language.md`](query-language.md) (the design),
[`query-language-examples.md`](query-language-examples.md) (the surface,
construct by construct), [`query-language-evidence.md`](query-language-evidence.md)
(what was run) and [`query-language-explorations.md`](query-language-explorations.md)
(branches rejected).

This page answers one question: **can the query language express what consumers
were hand-writing SQL for?** The 113 sites surveyed in #275 / #276 / #281 fall
into about a dozen shapes. Each is worked below in the spelling that ships,
followed by the shapes still out of reach and what to do instead. Results are
shown against the query tutorial's catalogue (described at the top of the
examples page) where they are shown at all.

```python
import polars as pl
import sdmxlib as sl

q = sl.q
```

---

## The surface in one page

```python
frame = (
    reg.query(q.dataflow)                       # any sl.q accessor; a plan
    .filter(q.dataflow.agency == "ECB")         # several arguments are ANDed
    .select(q.dataflow.id, label=q.dataflow.name["en"])
    .collect()                                  # the only executing call
)
```

- **Entry.** `reg.query(<accessor>)` on a `LocalRegistry` or a `RestRegistry`;
  `reg.obs(flow)` for observations; a lazy artefact's collections
  (`cl.codes`) and verbs (`cl.narrower_transitive("EU")`) are the same `Query`.
- **Paths are column references.** Every hop is a declared edge;
  `q.dataflow.dsd.dimensions.concept.id` type-checks hop by hop. Crossing a
  to-many hop makes the compiler emit `EXISTS` in a filter and a correlated
  aggregate in a projection.
- **Strictly lazy.** Terminals: `collect`, `ids`, `urns`, `count`, `exists`,
  `first`, `one`, `one_or_none`, `frame`, `stream`, `preview`, `page`.
- **Namespaces** keep the accessor small: `.str` for text matching, `["en"]` for
  localised labels.

---

## 1. Two columns, no objects — 29 sites

The most common shape by a distance, and the reason #275 exists: callers want a
frame, not a resolved artefact graph.

```python
reg.query(q.codelist).select(q.codelist.id, q.codelist.name["en"]).collect()
```

```sql
SELECT s0.resource_id AS id,
       (SELECT lt.text FROM sdmx.localized_text lt
         WHERE lt.owner_urn = s0.urn AND lt.field = ? AND lt.lang = ?) AS name_en
FROM sdmx.artefact s0 WHERE s0.artefact_type = ?
```

No resolution, no `Ref` walking, one statement. (A maintainable's name lives in
a side table keyed by owner and language, so the label is a scalar subquery; a
code's name is a map column read in place.) `frame()` gives every stored
column of the relation instead, without naming them.

## 2. Discovery: which flows use X — 22 sites

"Which dataflows use REF_AREA?" spans `dataflow`, `dsd_component` and `concept`.
It used to be `sl.artefact.uses_concept("REF_AREA")`, a bespoke predicate
written because the DSL could not express the join. It is now a path:

```python
reg.query(q.dataflow).filter(q.dataflow.dsd.dimensions.concept.id == "REF_AREA").ids()
reg.query(q.dataflow).filter(q.dataflow.dsd.dimensions.codelist.id == "CL_UNIT").ids()   # ['TRADE_Q']
reg.query(q.dataflow).filter(q.dataflow.categorisations.category.id == "ECON").ids()
```

`uses_codelist` and `in_category` dissolve the same way. Reaching through the
concept's own default representation is a different path, and a different
question: `q.dataflow.dsd.dimensions.concept.codelist.id == "CL_UNIT"`.

### Matched by either of two routes

`uses_concept` also matched a DataStructure *and* a Dataflow whose DSD carried
the concept — two roots. That is not a set operation (a `union` needs one
relation); it is an `|` of two `is_in` sub-queries, which is what SQLAlchemy,
ibis and polars all reach for:

```python
dsds = reg.query(q.dsd).filter(q.dsd.dimensions.codelist.id == "CL_UNIT").select(q.dsd.urn)
flows = reg.query(q.dataflow).filter(q.dataflow.dsd.dimensions.codelist.id == "CL_UNIT").select(q.dataflow.urn)
reg.query(q.artefact).filter(q.artefact.urn.is_in(dsds) | q.artefact.urn.is_in(flows)).ids()
# ['DSD_TRADE', 'TRADE_Q']
```

One statement: each one-column `Select` splices in as a subquery.

## 3. Dimensions of every flow, at catalogue scale — 21 sites

The shape that opened #280: 200 DSDs × 12 dimensions cost 265 ms by resolution
against 1 ms as one statement.

**Nested** — one row per dataflow, its dimensions collected:

```python
reg.query(q.dataflow).select(
    flow=q.dataflow.id,
    dims=q.dataflow.dsd.dimensions.id.list(),
    n=q.dataflow.dsd.dimensions.count(),
).collect()
```

```
┌─────────┬───────────────────────────────┬─────┐
│ flow    ┆ dims                          ┆ n   │
╞═════════╪═══════════════════════════════╪═════╡
│ CPI_A   ┆ ["FREQ", "REF_AREA"]          ┆ 2   │
│ CPI_M   ┆ ["FREQ", "REF_AREA"]          ┆ 2   │
│ TRADE_Q ┆ ["FREQ", "REF_AREA", "UNIT"]  ┆ 3   │
└─────────┴───────────────────────────────┴─────┘
```

**Flat** — one row per component, the shape `sdmxlib.polars.structure_frame`
returns for one DSD, reached for every DSD by rooting at the child:

```python
reg.query(q.component).filter(q.component.role == "Dimension").select(
    dsd=q.component.dsd.id,
    component=q.component.id,
    position=q.component.position,
    concept=q.component.concept.id,
    codelist=q.component.codelist.id,
    n_codes=q.component.codelist.codes.count(),
).order_by("dsd", "position").collect()
```

Choosing between them is a grain decision, not a style one. To get one row per
*(dataflow, dimension)*, nest the flows and explode in polars — a DSD backs many
dataflows, so `q.component.dsd.dataflows` is to-many and cannot be projected
bare:

```python
reg.query(q.component).filter(q.component.role == "Dimension").select(
    component=q.component.id,
    flows=q.component.dsd.dataflows.id.list(),
).collect().explode("flows")
```

## 4. Reference metadata for an artefact — 13 sites

Every QUALITY statement reported about one dataflow:

```python
ra = q.reported_attribute
reg.query(ra).filter(
    ra.metadata_set.targets.artefact.urn == str(flow.urn),
    ra.attribute_path.str.starts_with("QUALITY"),
).select(ra.attribute_path, text=ra.value["en"], set=ra.metadata_set.id).collect()
# [('QUALITY', None, 'Q_CPI'), ('QUALITY.ACCURACY', 'High.', 'Q_CPI')]
```

`reg.reported_attributes()` is the same query with the root already chosen:
`reg.reported_attributes().filter(ra.attribute_path.str.starts_with("QUALITY"))`.
The metadata sets themselves: `reg.query(q.metadata_set).filter(
q.metadata_set.targets.artefact.id == "CPI_M")`.

## 5. Labels and annotations per code — 12 sites

```python
reg.query(q.code).filter(q.code.codelist.id == "CL_FREQ").select(
    q.code.id,
    label=q.code.name["en"],
    order=q.code.annotations.list(q.code.annotations.text["en"], where=q.annotation.type == "ORDER"),
).collect().rows()
# [('A', 'Annual', ['1']), ('M', 'Monthly', []), ('Q', 'Quarterly', ['2'])]
```

Annotations are a to-many side table, so the list is a correlated subquery — no
join, row count preserved. `where=` picks which annotations; a code with none
gets `[]`.

## 6. Concept-driven discovery — 8 sites

```python
reg.query(q.concept).filter(
    q.concept.scheme.id == "CS",
    q.concept.name["en"].str.caseless.contains("area"),
).select(q.concept.id, scheme=q.concept.scheme.id).collect()
```

`caseless` ignores case; `unaccented` adds diacritics and `folded` punctuation,
on labels. `==` is exact except at the `caseless` rung (`q.code.id.str.caseless
== "fr"`).

## 7. Counts, groups and the top N

```python
reg.query(q.dataflow).group_by(q.dataflow.agency).len().order_by("agency").collect().rows()
# [('ECB', 2), ('ESTAT', 1)]

reg.query(q.code).group_by(q.code.codelist.id).agg(
    n=sl.len(),
    nested=q.code.id.count(where=q.code.depth >= 1),
).having(sl.len() > 2).collect().rows()
# [('CL_AREA', 6, 5), ('CL_FREQ', 3, 0)]

# the largest codelist — sorted and sliced in SQL, not after fetching every group
reg.query(q.code).group_by(q.code.codelist.id).agg(n=q.code.id.count()).order_by(
    q.code.id.count().desc()
).limit(1).collect().rows()
# [('CL_AREA', 6)]
```

A per-row count is a fan-out aggregate in `select`, with no grouping:

```python
c = q.codelist.codes
reg.query(q.codelist).select(q.codelist.id, n=c.count(), roots=c.count(where=q.code.depth == 0))
```

**A fan-out inside a grouped aggregate is refused** —
`group_by(q.dataflow.agency).agg(n=q.dataflow.dsd.dimensions.id.n_unique())`
cannot compile, because a correlated subquery has no value to aggregate across a
group. Root at the child instead, where the path is to-one:

```python
reg.query(q.component).filter(q.component.role == "Dimension").group_by(
    q.component.dsd.agency
).agg(n=q.component.id.n_unique())
```

## 8. Trees — ancestry as a relationship

Every tree relation (`q.code`, `q.concept`, `q.category`, `q.hcode`,
`q.metadata_attribute`) carries `parent`, `children`, `ancestors`,
`descendants`, `depth` and `position`:

```python
cl.codes.filter(q.code.ancestors.id == "W").ids()                     # everything under W
cl.codes.filter(q.code.ancestors.id == "W", q.code.depth <= 1).ids()  # ...one level: ['EU', 'AS']
cl.codes.filter(q.code.ancestors.name["en"] == "Europe").ids()        # under a label: ['FR', 'DE']
cl.codes.filter(q.code.children.count() == 0).ids()                  # leaves: ['FR', 'DE', 'JP']
cl.codes.select(q.code.id, chain=q.code.ancestors.id.list())          # each code's ancestors
cl.codes.filter(q.code.depth == 1).select(
    q.code.id, kids=q.code.children.id.list(), n_below=q.code.descendants.count(),
).collect().rows()
# [('EU', ['FR', 'DE'], 2), ('AS', ['JP'], 1)]
```

**The depth bound is a predicate, not a parameter** — `== 2`, `>= 3` and
`is_in([1, 3])` are free. None of it is a recursive query: the writer bakes each
row's ancestors, so `ancestors` is a membership test over a stored list. The
lazy codelist's named verbs say the common ones in a word:
`cl.narrower_transitive("EU")`, `cl.roots()`, `cl.leaves()`.

## 9. The latest version of each artefact

```python
reg.query(q.codelist).qualify(
    q.codelist.version.rank(descending=True).over(q.codelist.agency, q.codelist.id) == 1
).select(q.codelist.agency, q.codelist.id, q.codelist.version).collect()
```

`qualify` runs after the window whatever the call order; `filter` runs before
it. `Version` orders by version, so `1.10` outranks `1.9`. "The three newest" is
`.row_number(descending=True).over(...).le(3)`.

## 10. What nothing uses

```python
# codelists no component references
used = reg.query(q.component).select(q.component.codelist.urn)
reg.query(q.codelist).filter(~q.codelist.urn.is_in(used)).ids()    # ['CL_RETIRED']

# structures no dataflow is built on
reg.query(q.dsd).filter(q.dsd.dataflows.count() == 0).ids()
```

## 11. Data-quality universals

"Every dimension is coded" is a universal, and the empty case is vacuously true:

```python
reg.query(q.dsd).filter(q.dsd.dimensions.every(q.component.codelist.id.is_not_null())).ids()
```

A child whose column is NULL counts as a violation, not a pass — so
`q.dsd.components.every(q.component.codelist.id != "CL_UNIT")` is false for
every DSD here, because each `OBS_VALUE` measure has no codelist. To ask about
the coded ones only, restrict the fan-out:
`q.dsd.dimensions.every(...)`, or `q.dsd.components.count(where=...) == 0` for
"none of them is like this".

## 12. Two predicates on the *same* child

```python
reg.query(q.dsd).filter(
    q.dsd.agency == "ECB",
    q.dsd.dimensions.any(
        q.component.id == "REF_AREA",
        q.component.codelist.id == "CL_AREA",
    ),
)
```

Written as two dotted predicates this asks "some dimension is REF_AREA, and some
dimension uses CL_AREA", which is not the question. See
`query-language-examples.md` §3.

## 13. Shapes that look like self-joins

"A DSD with two dimensions sharing a concept" reads as a self-join and is a
grouping at the child's grain:

```python
reg.query(q.component).filter(q.component.role == "Dimension").group_by(
    q.component.dsd.id, q.component.concept.id
).agg(n=sl.len()).having(sl.len() > 1).collect()
```

Two that compare a row with a neighbour are **not expressible in the path
language** today — a comparison's right-hand side must be a literal
(`query-language.md` §13):

```python
reg.query(q.component).filter(q.component.codelist.id != q.component.concept.codelist.id)   # refused
reg.query(q.code).filter(q.code.parent.name["en"] == q.code.name["en"])                       # refused
```

Use the escape hatch, with `sl.schema` names so the SQL is checked against the
layout:

```python
cmp, con = sl.schema.dsd_component, sl.schema.concept
reg.sql(f"""
    SELECT {cmp.dsd_urn}, {cmp.component_id}
    FROM {cmp} JOIN {con} ON {con.concept_urn} = {cmp.concept_urn}
    WHERE {cmp.codelist_urn} IS DISTINCT FROM {con.codelist_urn}
""").collect()
```

## 14. Label with a fallback

`fill_null` and `sl.when(...)` were designed and **not built**. Project both and
coalesce in polars, which is one call:

```python
reg.query(q.dataflow).select(q.dataflow.id, q.dataflow.name["fr"]).collect().with_columns(
    label=pl.coalesce("name_fr", "id")
)
```

There is no default language anywhere, so the fallback language is always named.

## 15. As of a date

`valid_from` / `valid_to` are columns on every maintainable:

```python
import datetime as dt

cutoff = dt.datetime(2025, 1, 1, tzinfo=dt.UTC)
reg.query(q.codelist).filter((q.codelist.valid_to > cutoff) | q.codelist.valid_to.is_null())
```

## 16. Paging a large catalogue

```python
ordered = reg.query(q.dataflow).order_by(q.dataflow.agency, q.dataflow.id)
page = ordered.page(100)            # Page(urns, cursor)
nxt = ordered.after(page.cursor).page(100)
```

Keyset, not offset: stable at any depth, and the cursor is tied to the order it
came from — a cursor from a differently shaped order is refused rather than
silently skipping rows. Reading everything once is `stream()`, which needs no
order.

## 17. Observations

```python
obs = reg.obs(flow)
obs.filter(obs.col("REF_AREA").is_in(["FR", "DE"])).frame(labels="en")

# the highest value per area
obs.qualify(obs.col("OBS_VALUE").rank(descending=True).over(obs.col("REF_AREA")) == 1).frame()

# a mean per area
obs.group_by(obs.col("REF_AREA")).agg(m=obs.col("OBS_VALUE").mean()).collect()
```

Columns are named by component id (`obs.col("REF_AREA")`) because they come from
the DSD at runtime; `obs.components` lists them. See
[`data-plane-query.md`](data-plane-query.md).

## 18. The same question over SDMX REST

`RestRegistry.query` takes the same queries. It plans what the endpoint can
answer, fetches that (with `references=` chosen to close over every relation the
paths reach), materialises it, and runs the query locally:

```python
with sl.RestRegistry(sl.Provider.ECB) as ecb:
    exr = ecb.query(q.dataflow).filter(
        q.dataflow.agency == "ECB",
        q.dataflow.dsd.dimensions.id == "CURRENCY",
    )
    exr.ids()        # ['EXR']
    exr.explain()    # the SQL, and a line saying the rows are fetched first
```

The one thing REST asks for that a local store does not: **a scan that leaves
anything to filter locally must name an agency**, or the terminal raises
`PushdownError` naming the column to add — rather than downloading every
agency's dataflows to keep three. A bare listing, or equality on id or version,
needs none. Pass `store=` to keep what is fetched across sessions. The rules are
in [`query-pushdown.md`](query-pushdown.md).

Observations over REST push key-dimension `==` / `is_in` into the data key and
attribute filters into SDMX 3.0's `c[...]`; the rest runs locally on what came
back ([`data-plane-pushdown.md`](data-plane-pushdown.md)).

---

## What is still out of reach

| shape | status | do instead |
| --- | --- | --- |
| comparing two columns of one row | refused — literal right-hand side only | `reg.sql` with `sl.schema` (§13 above) |
| any-language label search | no spelling | one `name["xx"]` per language, ORed |
| regex over ids or labels | no operator | `reg.sql`, or polars after `collect()` |
| joins over a caller's own tables | no accessor of theirs can dot into ours | `is_in` with their one-column `SqlRelation`, or `reg.sql` on a shared connection |
| range joins (overlapping validity) | every edge is an equi-join | `reg.sql` |
| `PIVOT`, `fill_null`, `when/then` | not built | polars, after `collect()` |
| federation across registries in one query | open (`query-pushdown.md` §14) | `RestRegistry(store=...)` into one store |

**Cross-schema joins** are the consumer shape the language reaches only
partially. A caller's relation can *filter* ours — `is_in` accepts any
one-column `SqlRelation`, including producers from other libraries, compiled
into the same statement — but a caller cannot declare an accessor whose rows
dot through to `code.name["en"]`: the column and edge factories are not public
and the edge table is closed. That was prototyped and type-checked; it is open
in `query-language.md` §13.

**`PIVOT`** is a reshaping of a materialised frame, and polars does it in one
call. Following the rule that kept server-side `explode` out: do not build what
polars already gives the caller for free.
