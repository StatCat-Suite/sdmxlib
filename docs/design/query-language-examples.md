# The query language: the surface, construct by construct

Companion to [`query-language.md`](query-language.md) (the design — read it for
the *why*), [`query-language-cookbook.md`](query-language-cookbook.md) (the
same surface applied to real questions),
[`query-language-evidence.md`](query-language-evidence.md) (what was run) and
[`query-language-explorations.md`](query-language-explorations.md) (branches
rejected).

This page explains what each construct *means* and what it costs; the cookbook
answers "how do I ask X". Every spelling here exists in `src/sdmxlib/query/`.
Lines marked **refused** or **not built** are counter-examples and say so.

```python
import sdmxlib as sl

q = sl.q
```

Results are shown against the catalogue [the query tutorial](../query_tutorial.md)
builds: codelists `CL_FREQ` (A, Q, M), `CL_AREA` (W › EU › FR, DE; W › AS ›
JP), `CL_UNIT` and the unreferenced `CL_RETIRED`; structures `DSD_PRICES`
(FREQ, REF_AREA; measure OBS_VALUE) and `DSD_TRADE` (adds UNIT); dataflows
`CPI_M` and `CPI_A` (ECB, on `DSD_PRICES`) and `TRADE_Q` (ESTAT, on
`DSD_TRADE`). Where a concept is involved, the concept scheme the components
reference is assumed stored too.

---

## 1. What the accessor names: a row

`q.component` denotes **the row under consideration** — a range variable, in the
relational sense. Every query language binds one:

| language | binding | column off it |
| --- | --- | --- |
| SQL | `FROM dsd_component c` | `c.component_id` |
| JPQL | `SELECT c FROM Component c` | `c.dsd.agency` |
| ibis | `t = con.table("dsd_component")` | `t.component_id` |
| here | `reg.query(q.component)` | `q.component.id` |

Ours differs only in that the binding is implicit: the accessor is a module
singleton rather than a variable the caller names. There are 48 —
`q.artefact`, `q.dataflow`, `q.dsd`, `q.codelist`, `q.code`, `q.concept`,
`q.component`, `q.hierarchy`, `q.hcode`, `q.category`, `q.annotation`,
`q.metadata_set`, `q.reported_attribute`, the constraints, maps and
organisation schemes among them. `reg.query(root)` takes any of them.

**A path is a possessive chain.**

```python
q.component.dsd.id        # "this component's DSD's id"
q.component.dsd.agency    # "this component's DSD's agency"
q.dsd.id                  # "this DSD's id"
```

### Why `component.dsd.id` and `dsd.id` both exist

The same stored column read at two **grains**; the root fixes which:

```
reg.query(q.dsd)                     reg.query(q.component)
one row per DSD                      one row per component
┌────────────┬────────┐              ┌───────────┬──────────────────┐
│ id         │ agency │              │ id        │ dsd              │
├────────────┼────────┤              ├───────────┼──────────────────┤
│ DSD_PRICES │ ECB    │              │ FREQ      │ DSD_PRICES       │
│ DSD_TRADE  │ ECB    │              │ REF_AREA  │ DSD_PRICES       │
└────────────┴────────┘              │ FREQ      │ DSD_TRADE        │
                                     │ …         │ …                │
                                     └───────────┴──────────────────┘
```

`component.dsd.id` is the parent's value **denormalised onto each child row**.
That is what a to-one edge is, and why dotting through one never changes the
answer's shape.

### `q.codelist` never means the nested form

`q.codelist` is the *root* accessor. The nested forms are paths, and they are
the same accessor class at a different row-kind parameter:

```
q.codelist.id                    -> Str[CodelistRow]     root
q.component.codelist.id          -> Str[ComponentRow]    component's own representation
q.component.concept.codelist.id  -> Str[ComponentRow]    concept's default representation
```

so mixing them is a type error and, at runtime, a refusal naming the fix:

```python
reg.query(q.component).filter(q.codelist.id == "CL_AREA")    # refused
# TypeError: filter() got a predicate about 'artefact', but this query is rooted
# at 'dsd_component'. ... reach it by a declared hop (q.<root>.<hop>.resource_id).
```

The second and third lines above are two genuinely different questions — SDMX
lets a component override its concept's representation — and both are spellable
without guessing.

### Edge names describe roles, not entities

The edge is `dsd` and the root accessor is `q.dsd`; a codelist is reached as
`component.codelist`, `code.codelist` or `concept.codelist`. An edge name says
what role the target plays for this row, as SQLAlchemy relationship attributes
and Django's `related_name` do. Some to-many attributes are **facets** of one
edge: `q.dsd.dimensions`, `attributes` and `measures` are `q.dsd.components`
restricted by role, and `dimensions` excludes the time dimension.

---

## 2. To-one paths

```python
reg.query(q.component).filter(q.component.dsd.agency == "ECB").ids()
```

```sql
SELECT s0.* FROM sdmx.dsd_component s0
JOIN sdmx.artefact s0_1 ON s0_1.urn = s0.dsd_urn
WHERE s0_1.agency_id = ?
ORDER BY s0.dsd_urn NULLS LAST, s0.component_id NULLS LAST
```

A mandatory to-one edge is an inner `JOIN`; an optional one (a component's
codelist) is a `LEFT JOIN` (§5). The trailing `ORDER BY` is the root's key, a
tiebreak every query carries so paging is stable.

Several routes from a component to an artefact exist, and each is its own
spelling:

```python
q.component.dsd.agency               # to-one — via the DSD
q.component.concept.scheme.agency    # to-one — via the concept's scheme
q.component.codelist.agency          # to-one, nullable — via the representation
q.component.dsd.dataflows            # to-MANY — the dataflows the DSD backs
```

Multi-hop stays readable at the depth SDMX uses, and the hops share one join
tree whatever else the query names:

```python
reg.query(q.component).filter(
    q.component.concept.scheme.id == "CS",
    q.component.codelist.id == "CL_AREA",
)
```

**These are path expressions, not patterns.** `dsd` is one declared edge and a
path is one statically-known route — no wildcards, no alternation, no recursion
in path syntax. Recursion is a named domain operation, and the transitive tree
edges read a closure baked at write time (§6).

---

## 3. To-many: dotting through, `.any()`, `.every()`

A to-many hop is an ordinary hop; what changes is what the compiler emits — a
correlated `EXISTS` in a filter, a correlated aggregate in a projection, never a
join.

```python
# EXISTS — one row per DSD, however many dimensions match
reg.query(q.dsd).filter(q.dsd.dimensions.codelist.id == "CL_UNIT").ids()   # ['DSD_TRADE']

# the two routes stay distinct, because every edge is named
reg.query(q.dsd).filter(q.dsd.dimensions.codelist.id == "CL_AREA")
reg.query(q.dsd).filter(q.dsd.dimensions.concept.codelist.id == "CL_AREA")
```

Two fan-outs in one filter cannot produce a cartesian product, because each is
its own subquery:

```python
reg.query(q.codelist).filter(
    q.codelist.codes.id.str.starts_with("E"),
    q.codelist.codes.annotations.type == "ORDER",
)
```

A to-many column has no single value per row, so projecting it bare is
**refused** with the fix in the message:

```python
reg.query(q.dsd).select(q.dsd.id, dims=q.dsd.dimensions.id)                # refused
# PathError: 'dimensions' crosses a to-many edge, so it has no single value per
# row. Collect it — .list() for the values, .count() for how many, .n_unique()
# for distinct — or root the query at the child instead.
```

### Independent children versus the same child

> **Predicates inside one `.any()` call land on one child. Predicates outside it
> are independent.**

```python
# INDEPENDENT — some dimension is FREQ, some dimension uses CL_UNIT
reg.query(q.dsd).filter(
    q.dsd.dimensions.id == "FREQ",
    q.dsd.dimensions.codelist.id == "CL_UNIT",
).ids()                                                    # ['DSD_TRADE']

# SAME CHILD — one dimension is FREQ *and* uses CL_UNIT
reg.query(q.dsd).filter(
    q.dsd.dimensions.any(
        q.component.id == "FREQ",
        q.component.codelist.id == "CL_UNIT",
    )
).ids()                                                    # []
```

The fan-out accessor *is* the child accessor at the parent's row kind, which is
why dotting through it works; `.any(*preds)` takes child-rooted predicates
(`Pred[ComponentRow]`, what `q.component.*` builds). A parent-rooted predicate
inside `.any()` is a type error. polars draws the same line with `pl.element()`
bound by `.list.eval()`.

### The universal, and why its SQL needs care

`.every(*preds)` takes the same child-rooted predicates:

```python
reg.query(q.dsd).filter(q.dsd.dimensions.every(q.component.codelist.id != "CL_UNIT")).ids()
# ['DSD_PRICES']
```

Against a fixture of A (all dimensions `CL_GEO`), B (mixed), C (one dimension
with a NULL codelist) and D (no dimensions at all):

```
some    -> ['A', 'B']
every   -> ['A', 'C', 'D']      naive NOT EXISTS (... AND NOT pred)
every   -> ['A', 'D']           null-safe — what the compiler emits
```

`C` satisfies the naive form because `NOT(NULL)` is NULL, so a non-enumerated
dimension is not counted as a violation. The compiler negates with `IS NOT TRUE`,
so a child whose column is NULL counts as one: here
`q.dsd.components.every(q.component.codelist.id != "CL_UNIT")` is `[]`, because
each DSD's `OBS_VALUE` measure has no codelist. `D` is vacuous truth — correct
logic, guaranteed surprise.

`every` and `any` nest freely, including across two to-many edges:

```python
reg.query(q.codelist).filter(q.codelist.codes.every(q.code.annotations.type == "ORDER"))
```

### Asking about the count

An aggregate over a fan-out compared to a literal is a predicate — a correlated
scalar subquery, still row-preserving:

```python
cl.codes.filter(q.code.children.count() == 0).ids()                       # leaves: ['FR', 'DE', 'JP']
reg.query(q.codelist).filter(q.codelist.codes.count(where=q.code.depth > 0) == 0).ids()
# ['CL_FREQ', 'CL_RETIRED', 'CL_UNIT'] — flat codelists
reg.query(q.dsd).filter(q.dsd.dataflows.count() == 0)                     # structures nothing uses
```

`~` over a to-many edge is `NOT EXISTS`, a true complement:

```python
reg.query(q.code).filter(q.code.codelist.id == "CL_FREQ", ~(q.code.annotations.type == "ORDER")).ids()
# ['M']
```

---

## 4. Projection: nesting, structs, aggregates

`select` makes a `Select`; `collect()` makes a `pl.DataFrame`.

```python
reg.query(q.dsd).select(
    q.dsd.id,
    dims=q.dsd.dimensions.id.list(),
    n=q.dsd.dimensions.count(),
).collect().rows()
# [('DSD_PRICES', ['FREQ', 'REF_AREA'], 2), ('DSD_TRADE', ['FREQ', 'REF_AREA', 'UNIT'], 3)]
```

```sql
SELECT s0.resource_id AS id,
       (SELECT COALESCE(list(s1.component_id), []) FROM sdmx.dsd_component s1
         WHERE (s0.urn = s1.dsd_urn AND s1.component_type = ?)) AS dims,
       (SELECT count(*) FROM sdmx.dsd_component s2
         WHERE (s0.urn = s2.dsd_urn AND s2.component_type = ?)) AS n
FROM sdmx.artefact s0 WHERE s0.artefact_type = ?
```

**Nesting is the default** — it cannot multiply rows — and exploding is one
polars call, `frame.explode("dims")`. Measured, 300 dataflows × 12 dimensions:
exploded 0.7 ms / 3,600 rows; nested 0.9 ms / 300 rows. No server-side explode
is built.

Names: a positional column keeps its own (`id`); a localised one carries its
language (`q.code.name["fr"]` → `name_fr`); an aggregate must be named; two
columns that would collide are refused:

```python
reg.query(q.dataflow).select(q.dataflow.id, q.dataflow.dsd.id)             # refused
# TypeError: select() would produce more than one column called 'id' ...
reg.query(q.dataflow).select(flow=q.dataflow.id, dsd=q.dataflow.dsd.id)   # fine
```

### `list()` alignment

Two `list()` calls over the same child are two independent correlated
subqueries, and DuckDB's `list()` has no defined order without `ORDER BY`:

```sql
(SELECT list(id)  FROM code WHERE cl = cl.id) AS ids,
(SELECT list(lab) FROM code WHERE cl = cl.id) AS labs
-> ids [x, y, z]   labs [X, Y, Z]      aligned — incidentally
```

Give one an `ORDER BY` and it breaks with no error (`labs [Y, X, Z]`). So the
fan-out's own `list()` takes several columns and returns **structs**, which
cannot misalign:

```python
c = q.codelist.codes
reg.query(q.codelist).filter(q.codelist.id == "CL_FREQ").select(
    codes=c.list(c.id, c.name["en"]),
).collect().rows()
# [([{'id': 'A', 'en': 'Annual'}, {'id': 'Q', 'en': 'Quarterly'}, {'id': 'M', 'en': 'Monthly'}],)]
```

polars' `group_by().agg()` and ibis's `collect()` both keep two lists aligned,
but each from a *single* grouping pass — the structural guarantee two correlated
subqueries lack.

**Order inside a fan-out** is part of the expression:

```python
c.id.sort_by(c.position).list()     # declaration order: ['W', 'EU', 'FR', 'DE', 'AS', 'JP']
c.id.sort().list()                  # by value:          ['AS', 'DE', 'EU', 'FR', 'JP', 'W']
c.id.sort().unique()                # distinct values; sort() is the only order unique() takes
```

### The aggregates and `where=`

`list`, `unique`, `count`, `n_unique`, `sum`, `mean`, `min`, `max` on a column,
and `sl.len()` for rows. `where=` restricts what *one* aggregate reads — SQL's
`FILTER (WHERE ...)`:

```python
c = q.codelist.codes
reg.query(q.codelist).select(
    q.codelist.id,
    n=c.count(),
    roots=c.count(where=q.code.depth == 0),
    level1=c.list(c.id, where=q.code.depth == 1),
).order_by("id").collect().rows()
# [('CL_AREA', 6, 1, ['EU', 'AS']), ('CL_FREQ', 3, 3, []), ('CL_RETIRED', 1, 1, []), ('CL_UNIT', 2, 2, [])]
```

Nothing matching gives `0` or `[]`. One predicate per `where=`; combine with `&`
and `|` (a tuple is refused). Prefer the fan-out's form (`c.count(where=...)`,
`c.list(..., where=...)`) over the column's: it types the predicate to the
child's row kind.

### One expression, three readings

| written in | means | SQL |
| --- | --- | --- |
| `select` / `filter`, over a to-many path | per row, over its children | correlated subquery |
| `group_by(...).agg(...)` or `Query.agg(...)` | over the group's rows | `GROUP BY` |
| after `.over(...)` | over rows sharing a partition | window |

```python
cl.codes.group_by(q.code.depth).len().order_by("depth").collect().rows()
# [(0, 1), (1, 2), (2, 3)]

reg.query(q.code).group_by(q.code.codelist.id).agg(
    n=sl.len(),
    nested=q.code.id.count(where=q.code.depth >= 1),     # where= in the grouped reading
).order_by("id").collect().rows()
# [('CL_AREA', 6, 5), ('CL_FREQ', 3, 0), ('CL_RETIRED', 1, 0), ('CL_UNIT', 2, 0)]

cl.codes.agg(n=sl.len(), roots=q.code.id.count(where=q.code.depth == 0)).collect().rows()
# [(6, 1)]

reg.query(q.component).select(
    q.component.id, dsd=q.component.dsd.id, n_in_dsd=q.component.id.count().over(q.component.dsd.id),
)
```

Grouping is a change of shape, not a refinement, so it is **refused** after
`order_by` or a slice. A to-many path is **refused** as a group key and inside
a grouped aggregate — a correlated subquery has no value to aggregate across a
group; collect it first or root at the child.

```python
reg.query(q.dataflow).group_by(q.dataflow.agency).agg(n=q.dataflow.dsd.dimensions.id.n_unique())  # refused
reg.query(q.component).group_by(q.component.dsd.agency).agg(n=q.component.id.n_unique())         # root at the child
```

`having` filters groups and needs a `group_by`:

```python
reg.query(q.code).group_by(q.code.codelist.id).agg(n=sl.len()).having(sl.len() > 2)
reg.query(q.code).group_by(q.code.codelist.id).agg(n=q.code.id.count()).order_by(
    q.code.id.count().desc()
).limit(1)                                                  # the top-N shape: ('CL_AREA', 6)
```

### A `Select` refines, but does not filter

`order_by` (a column, an aggregate, or a projected name as a string), `limit`,
`offset`, `unique` (full row only) and `having`. `filter` on a `Select` is
**refused** — `select()` changed what a row is, so filter first. `unique` takes
no subset (`unique(agency)` is **not built**: it neither commutes with `filter`
nor says which row survives; `group_by` does).

---

## 5. The cardinality trap

The obvious spelling for "each component with its dataflow" is **wrong**, and
the design's rules are what catch it. Written as though a component had one
dataflow:

```python
reg.query(q.component).select(component=q.component.id, flow=q.component.dsd.dataflows.id)  # refused
# PathError: 'dsd.dataflows.spine' crosses a to-many edge, so it has no single value per row. ...
```

`sdmx.dataflow` has no unique constraint on `dsd_urn`, so a DSD backs many
flows. Before cardinality was derived, this path was written as to-one in seven
design examples, and compiled as a join. Run against three components of two
DSDs, where CPI backs two flows:

```
component.id  component.dsd.id  flow.id     flow.agency
FREQ          CPI               PRC_HICP    ESTAT
FREQ          CPI               PRICES      OECD      <- FREQ twice
REF_AREA      CPI               PRC_HICP    ESTAT
REF_AREA      CPI               PRICES      OECD
                                                      <- GDP/REF_AREA gone
```

Two defects in one path: rows **multiplied** (one component twice) and rows
**dropped** (GDP backs no dataflow and the join was inner). Neither is
announced.

**The lesson for the edge declaration.** Cardinality cannot be inferred from a
name, and getting it wrong is silent. So it is not declared at all: `_edges.py`
declares each foreign key once, and `tests/unit/test_query_edges.py` derives
to-one / to-many from the live schema's key constraints — a mis-declared
cardinality fails there rather than in someone's frame. The honest spellings:

```python
# one row per component, its flows nested
reg.query(q.component).filter(q.component.role == "Dimension").select(
    component=q.component.id, dsd=q.component.dsd.id, flows=q.component.dsd.dataflows.id.list(),
).collect().explode("flows")

# one row per DSD, how many flows use it
reg.query(q.dsd).select(q.dsd.id, n=q.dsd.dataflows.count())   # [('DSD_PRICES', 2), ('DSD_TRADE', 1)]
```

### Nullable to-one edges

`component.codelist` is absent for a non-enumerated component — here, each
`OBS_VALUE` measure. An optional to-one hop compiles to `LEFT JOIN` (a mandatory
one to `JOIN`), so:

```python
reg.query(q.component).filter(q.component.dsd.id == "DSD_PRICES").select(
    q.component.id, cl=q.component.codelist.id,
).collect().rows()
# [('FREQ', 'CL_FREQ'), ('OBS_VALUE', None), ('REF_AREA', 'CL_AREA')]   projection keeps the row

reg.query(q.component).filter(q.component.codelist.id.is_null()).ids()   # asks for it directly
```

A positive filter cannot be true of a null, so it drops the row. Negation follows
SQL's three-valued logic: `q.component.codelist.id != "CL_FREQ"` and
`~(q.component.codelist.id == "CL_FREQ")` are both NULL for `OBS_VALUE`, so
neither returns it. Django's `exclude()` *would* (it switches to `LEFT OUTER
JOIN` and returns the orphan); here, ask for the orphans explicitly:

```python
(q.component.codelist.id != "CL_FREQ") | q.component.codelist.id.is_null()
```

---

## 6. Columns, text and trees

### Typed columns, checked literals

```python
q.code.id                  # Str
q.code.name["en"]          # Label — projects as name_en
q.code.depth               # Num — 0 at a root
q.code.position            # Num — declaration order
q.dataflow.version         # Version — orders by version, not text
q.codelist.valid_to        # Temporal — a UTC instant
q.dsd.is_final             # Bool
q.component.role           # Vocabulary — Dimension, TimeDimension, Attribute, Measure, ...
q.component.concept_roles  # Arr — .has("...")
```

```python
q.code.id == 1                        # refused: code_id holds str, got int. Did you mean "1"?
q.code.id == None                     # refused: use is_null()
q.code.id.is_in("CA")                 # refused: a string is an iterable of characters
q.component.role == "dimension"       # refused: never holds 'dimension'. Did you mean 'Dimension'?
q.code.id == q.code.parent.id         # refused: the right-hand side must be a literal
```

Computed columns are columns, in filters and projections:

```python
cl.codes.filter(q.code.position + 1 == 2)
cl.codes.select(q.code.id, qualified=q.code.codelist.id + ":" + q.code.id, n=q.code.id.len())
```

### `.str`: exact, then a ladder of folds

```python
q.code.id.str.starts_with("E")                          # exact; also contains, ends_with
q.code.id.str.caseless == "fr"                          # == / != only at this rung
q.code.name["fr"].str.caseless.contains("allem")
q.code.name["fr"].str.unaccented.contains("Etats")      # labels only
q.code.name["fr"].str.folded.contains("ALLEM")          # labels only: + case, punctuation
```

`unaccented` and `folded` carry no `==` — the differential oracles encode that
`==` stays case- and accent-sensitive (#222) — and exist only on labels;
`q.code.id.str.folded` is an `AttributeError`. `LIKE` metacharacters in a needle
are escaped.

### Trees: four edges, no recursion

Every tree relation (`q.code`, `q.concept`, `q.category`, `q.hcode`,
`q.metadata_attribute`) has the same four:

```python
q.code.parent                # direct, to-one
q.code.children              # direct, to-many
q.code.ancestors             # transitive, to-many
q.code.descendants           # transitive, to-many
```

```python
cl.codes.filter(q.code.parent.id == "EU").ids()                    # ['FR', 'DE']
cl.codes.filter(q.code.ancestors.id == "W", q.code.depth <= 1).ids()  # ['EU', 'AS']
cl.codes.filter(q.code.ancestors.any(q.code.id == "EU", q.code.depth == 1)).ids()  # ['FR', 'DE']
cl.codes.select(q.code.id, chain=q.code.ancestors.id.list())
```

**The depth bound is a predicate, not a parameter** — `depth` is a column, so
`== 2`, `>= 3` and `is_in([1, 3])` are free where a `depth=` keyword only ever
meant `<=` (django-mptt, django-treebeard and Postgres `ltree` agree). None of
this is a recursive query: the writer bakes each row's ancestors, so
`ancestors` is a membership test against a stored list. There is no `parent_id`
column; `parent` is the hop. See [`query-tree-paths.md`](query-tree-paths.md).

A hierarchy's placements, `q.hcode`, are keyed by path: `id` is the node's own
id, `nested_id` its path from the root (`"W.EU.FR"`), `code` the code it
places, `level` its level.

The named verbs on a lazy artefact are the same language, returning `Query`s:

```python
cl.narrower("W").ids()               # ['EU', 'AS']
cl.narrower_transitive("EU").ids()   # ['FR', 'DE']
cl.roots().ids(), cl.leaves().ids()  # ['W'], ['FR', 'DE', 'JP']
```

### Annotations are an edge

```python
reg.query(q.code).filter(q.code.annotations.type == "ORDER").ids()     # ['A', 'Q']
reg.query(q.code).filter(q.code.codelist.id == "CL_FREQ").select(
    q.code.id,
    order=q.code.annotations.list(q.code.annotations.text["en"], where=q.annotation.type == "ORDER"),
)
```

---

## 7. Composition

Everything [`query-composition.md`](query-composition.md) guarantees holds across
paths:

```python
base = reg.query(q.component).filter(q.component.dsd.agency == "ECB")

base.filter(q.component.role == "Dimension")      # AND
base.limit(100).limit(5)                          # -> limit 5 (the minimum)
base[2:4]                                         # offset(2).limit(2)
base.limit(10).filter(q.component.id == "FREQ")   # refused: SliceError — filter after a slice
base.count()                                      # runs; `base` is unchanged
```

### Set operations

```python
cl.codes.intersect(cl.narrower("EU"))            # narrows — a method
cl.codes.difference(cl.narrower_transitive("W")) # narrows — a method: ['W']
sl.union(cl.narrower("EU"), cl.roots())          # WIDENS — a free function
sl.union(reg.query(q.dsd), reg.query(q.codelist))  # refused: one relation per set operation
```

A `union` *method* would let a later caller undo an earlier helper's scoping,
which refinement forbids. Measured: `union` is not a subset of its first
argument; `intersect` and `difference` are.

### Windows: `qualify`

```python
latest = reg.query(q.dataflow).qualify(
    q.dataflow.version.rank(descending=True).over(q.dataflow.agency, q.dataflow.id) == 1
)
three_per_list = reg.query(q.code).qualify(
    q.code.position.row_number().over(q.code.codelist.id).le(3)
)
```

`.over()` returns a `Win`, whose `==` / `!=` and `.le` / `.lt` / `.ge` / `.gt`
build a `WinPred` — not a `Pred` — so `filter(<WinPred>)` and `qualify(<Pred>)`
are type errors. A windowed predicate does not commute with an ordinary one (a
rank is computed over whatever set precedes it), so it gets its own clause, which
always runs after the `WHERE`. A window is still a column: project it.

### Relations inside `is_in`

```python
european = cl.narrower_transitive("EU").select(q.code.id)     # a one-column Select
cl.codes.filter(q.code.id.is_in(european)).ids()               # ['FR', 'DE'] — one statement

used = reg.query(q.component).select(q.component.codelist.urn)
reg.query(q.codelist).filter(~q.codelist.urn.is_in(used)).ids()  # ['CL_RETIRED']
```

A whole `Query` has every stored column, so it needs a `select` of one first.
`reg.sql(...)` is **refused** here (§10), and so is a relation from another
registry's database (`CrossSourceError`).

### Keyset paging

```python
ordered = cl.codes.order_by(q.code.id)
page = ordered.page(2)              # Page(urns=(... AS, ... DE), cursor=Cursor(...))
page.collect()                      # the two Code objects
ordered.after(page.cursor).ids()    # ['EU', 'FR', 'JP', 'W']
```

---

## 8. Strict laziness

Nothing executes until a terminal.

```python
qy = reg.query(q.dataflow).filter(q.dataflow.agency == "ECB")   # nothing has run

qy.collect()        # list[Dataflow] — whole artefacts, annotations included
qy.ids()            # ['CPI_A', 'CPI_M']
qy.urns()           # list[str]
qy.count()          # COUNT(*), fetches no rows
qy.exists()         # EXISTS, fetches no rows
qy.first()          # Dataflow | None
qy.one()            # raises unless exactly one
qy.one_or_none()    # Dataflow | None, raises on two
qy.frame()          # pl.DataFrame of the relation's stored columns
qy.stream()         # the same rows as Arrow batches
qy.preview()        # a bounded head, for a REPL
qy.explain()        # str — the SQL and binds; runs nothing
qy.to_sql()         # CompiledQuery(sql, params); runs nothing
```

`collect()` builds domain objects only where a root has a builder — artefacts,
codes, hierarchical codes, categories and the like. A root with none
(`q.component`, `q.concept`, `q.annotation`) **refuses** `collect()` and names
`.ids()`, `.frame()` and `.select()` instead.

### `[]` and the removed dunders

```python
cl.codes["FR"]           # the Code — looks up by id; KeyError if absent
cl.codes.get("XX")       # None
cl.codes[1:3]            # a query: offset(1).limit(2)
cl.codes[0]              # refused — positional extraction; slice or use first()
len(cl.codes)            # refused — use count()
list(cl.codes)           # refused — use collect()
"FR" in cl.codes         # refused — use filter(...).exists()
```

The string form looks up because `codelist.codes` has to answer the same
questions as `ItemList` whether the codelist is eager or lazy; an id is never a
position, so it cannot be confused with slicing. An early draft made it lazy
sugar for `filter(id == ...)` — **not built**.

### The boundary with the object world

`Ref` navigation stays eager, and laziness makes the boundary visible. Over a
`RestRegistry` (`rest`):

```python
dsd = rest.get(sl.DataStructure.urn_for(agency="ECB", id="DSD_PRICES"), resolve=True)

dsd.dimensions                       # eager ItemList — in memory, no query
dsd.dimensions["FREQ"].codelist()    # eager Ref resolution
dsd.dimensions["FREQ"].codes         # ItemList[Code] — materialised, on purpose (#398)
dsd.dimensions["FREQ"].code_ids()    # list[str] — the cheap path
```

From a `LocalRegistry` the dimension's codelist is already a `LazyCodelist`, so
`.codes` there is the same materialised `ItemList` and `.code_ids()` the same
cheap path.

> **Objects and `ItemList`s are already in memory; anything ending in a plural
> query accessor is a plan.** `.collect()` is the seam.

Collections keep the lambda vocabulary because they are materialised — the
`sort_by` / `order_by` split again:

```python
dsd.dimensions.filter(lambda d: d.is_enumerated)               # in memory, lambda
dsd.dimensions.filter(sl.item("id").is_in(["FREQ", "UNIT"]))   # in memory, expression
reg.query(q.dataflow).filter(q.dataflow.agency == "ECB")       # a plan, predicate
```

---

## 9. Observations

```python
obs = reg.obs(flow)                       # ObsQuery, on Local- and RestRegistry
obs.components                                     # ('FREQ', 'REF_AREA', 'OBS_VALUE')
fr = obs.filter(obs.col("REF_AREA") == "FR")
fr.frame()
obs.frame(labels="en")                             # adds FREQ_label_en, REF_AREA_label_en
obs.group_by(obs.col("REF_AREA")).agg(m=obs.col("OBS_VALUE").mean())
obs.qualify(obs.col("OBS_VALUE").rank(descending=True).over(obs.col("REF_AREA")) == 1)
obs.col("NOPE")                                    # refused: KeyError listing the components
```

Columns come from the DSD at runtime, so they are named by string and typed
`Untyped`: comparisons, orderings, aggregates, windows and `.str` work; static
checking of the name does not. `obs["REF_AREA"]` is **not** a column — on a
`Query` a string subscript looks a row up by id, and observations have none.
**Not built:** `q.obs`, `obs.text()` / `obs.num()`, `.labelled()`.

Over REST, `==` and `is_in` on a key dimension push into the data key, attribute
filters into SDMX 3.0's `c[...]`, and the rest runs locally on what came back.
See [`data-plane-query.md`](data-plane-query.md).

---

## 10. Raw SQL

```python
reg.sql("SELECT code_id FROM sdmx.code WHERE code_id LIKE $p ORDER BY 1", p="E%").collect()
sl.schema.code.code_id        # 'sdmx.code.code_id'
sl.schema.code.nope           # AttributeError: sdmx.code has no column 'nope'. It has: ...
```

A terminal, not a splice:

```python
cl.codes.filter(q.code.id.is_in(reg.sql("SELECT 'FR'")))                 # refused
cl.codes.filter(q.code.id.is_in(reg.sql("SELECT 'FR' AS c").collect()["c"]))   # materialise the crossing
```

---

## 11. The two traversals, side by side

```python
dim.codelist.codes             # resolves now, returns Code objects
q.component.codelist.codes     # compiles to SQL
```

Same edges, same words. The discriminator is lexical and present at every call
site:

> **`sl.q.` compiles. Everything else resolves.**

---

## 12. What working the examples found

Kept because each changed the design before it shipped.

1. **`list()` returns structs.** Two `list()` calls align only incidentally and
   diverge silently under `ORDER BY`. (§4)
2. **Cardinality is derived from the schema, never declared** — the trap in §5
   reached seven examples before real rows caught it.
3. **Nullable edges** — the obvious rule ("positive filter `INNER`, projection
   `LEFT`") was incomplete until Django's `exclude` was run; the shipped
   compiler uses `LEFT JOIN` and SQL's three-valued logic for `~`. (§5)
4. **`.every()` needs null-safe negation**, or a non-enumerated dimension
   silently satisfies a universal. (§3)
5. **Strict laziness needs terminals added, not only dunders removed** —
   `count` / `exists` / `one` / `one_or_none` did not exist, and two of them are
   faster than what they replaced. (§8)
6. **A `.str` namespace shadows the builtin**; use `builtins.str`. Applies to any
   namespace named for one.
7. **The data plane cannot share the structural naming mechanism**, only the
   operators and the plan — its column names come from the DSD at runtime. (§9)
