# Querying Structural Metadata

SDMX metadata is a graph. A dataflow points at a data structure, which has
dimensions, which are enumerated by codelists, whose codes have parents,
annotations and names in several languages.

You can walk that graph with attribute access — `dsd.dimensions["FREQ"].codes` —
and for one artefact that is the right tool. But some questions are about the
*catalogue*: which codelists is nothing using, which dataflows share a
structure, how deep does this hierarchy go. Walking Python objects to answer
those means loading everything first.

The query language asks the catalogue instead.

```python
reg.query(sl.q.codelist).filter(sl.q.codelist.id.str.starts_with("CL_A")).ids()
```

This tutorial builds up from that one line. It starts with queries you could
have written by hand, then introduces the ideas — paths, fan-outs, projections
— that make the harder questions short.

## The catalogue used here

Every example runs against this, and the outputs in the comments are real:

| | |
| --- | --- |
| **Codelists** | `CL_FREQ` (A, Q, M) · `CL_AREA` (a tree: W → EU → FR, DE; W → AS → JP) · `CL_UNIT` (EUR, USD) · `CL_RETIRED` (X) |
| **Data structures** | `DSD_PRICES` (FREQ, REF_AREA) · `DSD_TRADE` (FREQ, REF_AREA, UNIT) |
| **Dataflows** | `ECB:CPI_M`, `ECB:CPI_A` on `DSD_PRICES` · `ESTAT:TRADE_Q` on `DSD_TRADE` |

`CL_RETIRED` is referenced by nothing. That is deliberate — several questions
later on are about finding exactly that.

`reg` is an open `sl.LocalRegistry` holding those artefacts.

---

## 1. Three parts

Every query has the same shape:

```python
reg.query(sl.q.codelist).filter(sl.q.codelist.id == "CL_FREQ").ids()
```

- **a root** — `sl.q.codelist`, the relation you are asking about;
- **refinements** — `.filter(...)`, and later `.order_by`, `.select`, `.limit`;
- **a terminal** — `.ids()`, which runs it.

Nothing runs until the terminal. A query is a description, so you can build one
up, pass it around, and reuse it.

```python
codelists = reg.query(sl.q.codelist)          # nothing has run
print(codelists.ids())                        # ['CL_AREA', 'CL_FREQ', 'CL_RETIRED', 'CL_UNIT']
print(codelists.count())                      # 4
```

Two terminals on the same query object; the query was not consumed by the
first.

## 2. Filtering

Comparisons build predicates:

```python
print(reg.query(sl.q.dataflow).filter(sl.q.dataflow.agency == "ECB").ids())   # ['CPI_A', 'CPI_M']
```

Several `filter` calls are ANDed, which usually reads better than one long
expression:

```python
q = sl.q.dataflow
print(reg.query(q).filter(q.agency == "ECB").filter(q.id.str.ends_with("_M")).ids())   # ['CPI_M']
```

For `or` and `not`, use `|` and `~` — and parenthesise each comparison, because
Python's operator precedence puts `|` tighter than `==`:

```python
q = sl.q.codelist
print(reg.query(q).filter((q.id == "CL_FREQ") | (q.id == "CL_UNIT")).ids())   # ['CL_FREQ', 'CL_UNIT']
print(reg.query(q).filter(~q.id.str.starts_with("CL_R")).ids())               # ['CL_AREA', 'CL_FREQ', 'CL_UNIT']
```

!!! warning "`and` / `or` / `not` do not work"
    Python cannot overload them, so they would silently evaluate one side and
    drop the other. The DSL raises rather than letting that happen — use `&`,
    `|`, `~`.

## 3. Terminals

What you ask for at the end decides what you get back.

```python
q = sl.q.codelist
print(reg.query(q).ids())                                  # ['CL_AREA', 'CL_FREQ', 'CL_RETIRED', 'CL_UNIT']
print(reg.query(q).count())                                # 4
print(reg.query(q).filter(q.id == "NOPE").exists())        # False
print(reg.query(q).filter(q.id == "CL_FREQ").one().id)     # 'CL_FREQ'
```

- `.ids()` / `.urns()` — identifiers only, no artefacts built
- `.count()` / `.exists()` — answered in the database
- `.one()` — the artefact, and raises unless there is exactly one
- `.one_or_none()` — the same, but `None` rather than raising when there are none
- `.first()` — the first, or `None`
- `.collect()` — the artefacts themselves, built, their refs bound to the registry
- `.frame()` — every column of the root as a `polars.DataFrame`, building none
- `.select(...).collect()` — a `polars.DataFrame` of the columns you named
- `.stream()` — an Arrow batch reader, for results too large to hold

`.one()` raises on anything but exactly one, because a lookup that silently
returns the first of several hides a bug until much later. Reach for
`.one_or_none()` when absence is an ordinary answer, and `.first()` only when
you genuinely mean "any of them".

### What each root builds is typed

`collect()` returns the artefact the root names, not a bare `object`, and a
type checker knows which:

```python
reg.query(sl.q.code).collect()      # list[Code]
reg.query(sl.q.dataflow).collect()  # list[Dataflow]
reg.query(sl.q.artefact).collect()  # list[Maintainable] — the root is polymorphic
```

Two of them build lazy artefacts. `sl.q.codelist` and `sl.q.hierarchy` build
a `LazyCodelist` and a `LazyHierarchy` — the same objects a `LocalRegistry`
hands back from `get`, whose `codes` / `nodes` are queries rather than
materialised lists. The declared type names them, so a type checker knows
`codes` is a query.

Roots that name a *part* of an artefact — `sl.q.component`, `sl.q.concept`,
`sl.q.annotation` and eighteen others — build nothing, because a dimension has
no independent identity to return. `collect()` on one raises `TypeError`
naming what to use instead, and their declared element type is `Never`, so any
use of the result is also a type error.

### `collect()` is for object graphs, `frame()` is for volume

The two differ by more than their return type, and the difference grows with
the row count. `collect()` builds a Python object per row and loads what it
references; `frame()` hands the columns to polars and builds nothing. Measured
on a real edition's largest codelist and largest hierarchy:

| | rows | `.count()` | `.ids()` | `.frame()` | `.collect()` |
| --- | --- | --- | --- | --- | --- |
| `q.code` | 579,951 | 23 ms | 103 ms | **158 ms** | 2372 ms |
| `q.hcode` | 499,143 | 18 ms | 94 ms | **176 ms** | 2311 ms |

So: reach for `collect()` when the next step is *navigation* — following
`code.parent`, reading `dimension.codelist()`. Reach for `frame()` or
`select(...)` when the next step is *analysis*, and for anything at this
scale. `count()` and `ids()` never leave the database at all.

`collect(annotations=False)` skips the annotation read where you do not need
it — half of `q.code`'s 2372 ms above. Annotations are read *by default*
because a silently empty `.annotations` is wrong data; skipping them must be a
choice the caller makes.

## 4. Paths

So far every filter has been about the root itself. Most questions involve
*related* things: codes of a codelist, dimensions of a structure,
the structure behind a dataflow.

You write those as attribute access, and the query language turns them into
joins:

```python
q = sl.q.code
print(reg.query(q).filter(q.codelist.id == "CL_FREQ").ids())   # ['A', 'M', 'Q']
```

`q.code.codelist.id` is a **path**: start at a code, hop to its codelist, read
its id. You never name a join or a key. Hops chain:

```python
q = sl.q.dataflow
print(reg.query(q).filter(q.dsd.id == "DSD_TRADE").ids())          # ['TRADE_Q']
print(reg.query(q).filter(q.dsd.components.id == "UNIT").ids())    # ['TRADE_Q']
```

The second one crosses two hops — dataflow → structure → components — and asks
for the flows whose structure has a `UNIT` dimension. Written by hand that is a
three-table join.

The root is what you get back. Both queries above return **dataflows**,
because that is what `reg.query()` was given; the path only describes *which*
ones.

```python
print(reg.query(sl.q.component).filter(sl.q.component.dsd.id == "DSD_PRICES").ids())
# ['FREQ', 'OBS_VALUE', 'REF_AREA']
```

Same relationship, opposite direction: start at components, return components.

## 5. Projecting columns

`.select()` says which columns you want, and gives you a frame instead of ids:

```python
q = sl.q.code
rows = reg.query(q).filter(q.codelist.id == "CL_FREQ").select(code=q.id, label=q.name["en"]).collect()
print(rows.to_dicts())
# [{'code': 'A', 'label': 'Annual'}, {'code': 'M', 'label': 'Monthly'}, {'code': 'Q', 'label': 'Quarterly'}]
```

The keyword is the column name in the result. Columns can come from anywhere on
the path, not just the root:

```python
q = sl.q.component
rows = reg.query(q).filter(q.dsd.id == "DSD_TRADE").select(dim=q.id, structure=q.dsd.name["en"]).collect()
print(rows.to_dicts()[0])   # {'dim': 'FREQ', 'structure': 'Trade balance'}
```

### Localised text

`name` is not a column — it is a language map. Subscript it:

```python
q = sl.q.code
print(reg.query(q).filter(q.id == "FR").select(en=q.name["en"], fr=q.name["fr"]).collect().to_dicts())
# [{'en': 'France', 'fr': 'France'}]
```

Every multilingual API in sdmxlib takes the language explicitly. `name` alone
will not project, because a guessed language produces a report in the wrong
one.

A projected label is text, one column per language. Unnamed, the column takes
the tag with `_` for `-`, as `sl.tag_alias` writes it: `select(q.name["fr-CA"])`
projects `name_fr_ca`. The model differs here: `code.name["fr"]` on a `Code`
returns a `sl.LocalisedString`, the text with its tag, and `.text` reads the
text.

## 6. Searching text

String columns carry a `.str` namespace:

```python
q = sl.q.code
print(reg.query(q).filter(q.name["en"].str.contains("an")).ids())        # ['DE', 'FR', 'JP']
print(reg.query(q).filter(q.id.str.starts_with("E")).ids())              # ['EU', 'EUR']
print(reg.query(q).filter(q.name["en"].str.ends_with("ly")).ids())       # ['M', 'Q']
```

`contains` matched Germany, France and Japan — a substring test on the
*label*. `starts_with("E")` matched `EU` and `EUR`, which are ids from two
different codelists: a query over `sl.q.code` spans the whole catalogue unless
you narrow it.

Matching is case-sensitive by default. `.caseless` folds both sides:

```python
q = sl.q.code
print(reg.query(q).filter(q.name["en"].str.caseless.contains("EUROPE")).ids())   # ['EU']
```

!!! note "`==` stays case-sensitive"
    `.caseless` applies to the `.str` operations. An id comparison is an
    identity check, and folding case there would make `FR` and `fr` the same
    artefact.

## 7. Fan-outs — when a hop reaches many

`q.code.codelist` reaches exactly one codelist. `q.code.annotations` reaches
*many* annotations, and a many-hop cannot be compared directly — there is no
single value to compare. Instead you say what must be true of the set:

```python
print(reg.query(sl.q.code).filter(sl.q.code.annotations.any(sl.q.annotation.type == "ORDER")).ids())
# ['A', 'Q']
```

`.any(...)` — at least one annotation with that type. There is also `.every()`,
and `.count()` for the size:

```python
q = sl.q.code
rows = reg.query(q).filter(q.codelist.id == "CL_FREQ").select(id=q.id, anns=q.annotations.count()).collect()
print(rows.to_dicts())   # [{'id': 'A', 'anns': 1}, {'id': 'M', 'anns': 0}, {'id': 'Q', 'anns': 1}]
```

A fan-out never multiplies your rows. Three codes in, three rows out — the
condition is evaluated *about* each code rather than joined onto it.

## 8. Hierarchies

A codelist's codes can form a tree, and the tree is queryable without
materialising it.

```python
q = sl.q.code
print(reg.query(q).filter(q.codelist.id == "CL_AREA").filter(q.depth == 0).ids())   # ['W']
print(reg.query(q).filter(q.depth == 2).ids())                                      # ['DE', 'FR', 'JP']
```

`depth` is 0 for a root. `parent` and `children` hop one step:

```python
q = sl.q.code
print(reg.query(q).filter(q.parent.id == "EU").ids())   # ['DE', 'FR']

rows = reg.query(q).filter(q.codelist.id == "CL_AREA").select(id=q.id, kids=q.children.count()).collect()
print(rows.to_dicts())
# [{'id': 'AS', 'kids': 1}, {'id': 'DE', 'kids': 0}, {'id': 'EU', 'kids': 2}, {'id': 'FR', 'kids': 0}, {'id': 'JP', 'kids': 0}, {'id': 'W', 'kids': 2}]
```

`ancestors` and `descendants` are the transitive versions, and they are
fan-outs — so they take `.any()`:

```python
q = sl.q.code
print(reg.query(q).filter(q.ancestors.any(q.id == "EU")).ids())     # ['DE', 'FR']
print(reg.query(q).filter(q.descendants.any(q.id == "JP")).ids())   # ['AS', 'W']
```

"Everything under Europe" and "everything Japan sits beneath". Both run as one
statement against the stored tree, at any depth.

## 9. Order, limits and paging

```python
q = sl.q.dataflow
print(reg.query(q).order_by(q.id).ids())                    # ['CPI_A', 'CPI_M', 'TRADE_Q']
print(reg.query(q).order_by(q.id.desc()).limit(2).ids())    # ['TRADE_Q', 'CPI_M']
```

Direction is per key — `.desc()` marks the key, rather than a flag beside the
list — so `order_by(a.desc(), b)` means what it looks like.

For walking a large result, `.page()` returns rows *and* a cursor:

```python
codes = reg.query(sl.q.code).order_by(sl.q.code.id)
first = codes.page(4)
print([str(u).rsplit(".", 1)[-1] for u in first.urns])   # ['A', 'AS', 'DE', 'EU']

second = codes.after(first.cursor).page(4)
print([str(u).rsplit(".", 1)[-1] for u in second.urns])  # ['EUR', 'FR', 'JP', 'M']
```

Cursor paging, not `offset` — the cursor is built from the sort keys, so
inserting a row between two pages cannot make you skip or repeat one.
`.cursor` is `None` exactly when the page came back short.

## 10. Grouping

```python
q = sl.q.code
rows = reg.query(q).group_by(q.codelist.id).agg(n=q.id.count()).order_by("id").collect()
print(rows.to_dicts())
# [{'id': 'CL_AREA', 'n': 6}, {'id': 'CL_FREQ', 'n': 3}, {'id': 'CL_RETIRED', 'n': 1}, {'id': 'CL_UNIT', 'n': 2}]
```

`group_by` takes the keys, `agg` the aggregates. `having` filters the groups:

```python
q = sl.q.code
rows = reg.query(q).group_by(q.codelist.id).agg(n=q.id.count()).having(q.id.count() > 2).collect()
print(rows.to_dicts())   # [{'id': 'CL_AREA', 'n': 6}, {'id': 'CL_FREQ', 'n': 3}]
```

Besides `count()` there are `n_unique()`, `min()`, `max()`, `list()` and
`unique()`.

## 11. Windows — ranking within a group

A window aggregate is computed per partition without collapsing the rows:

```python
q = sl.q.code
rows = (
    reg.query(q)
    .filter(q.codelist.id == "CL_AREA")
    .select(id=q.id, rank=q.id.rank().over(q.codelist.id))
    .collect()
)
print(rows.to_dicts()[:3])   # [{'id': 'AS', 'rank': 1}, {'id': 'DE', 'rank': 2}, {'id': 'EU', 'rank': 3}]
```

To *filter* on a window you need `qualify`, because a `WHERE` cannot see one:

```python
q = sl.q.code
print(reg.query(q).qualify(q.id.rank().over(q.codelist.id).le(1)).order_by(q.id).ids())
# ['A', 'AS', 'EUR', 'X']
```

The first code of each codelist, alphabetically — one query, no grouping.

!!! note "`.le(1)`, not `<= 1`"
    A window comparison is a method rather than an operator. `<=` on a window
    would have to return something that is both a predicate and a value, and
    the DSL refuses rather than guessing which you meant.

## 12. Subqueries and set operations

A query can be used as a value inside another one:

```python
q = sl.q
used = reg.query(q.component).select(urn=q.component.codelist.urn)
print(reg.query(q.codelist).filter(q.codelist.urn.is_in(used)).ids())    # ['CL_AREA', 'CL_FREQ', 'CL_UNIT']
```

Negate it for the complement — *codelists nothing uses*:

```python
q = sl.q
used = reg.query(q.component).select(urn=q.component.codelist.urn)
print(reg.query(q.codelist).filter(~q.codelist.urn.is_in(used)).ids())   # ['CL_RETIRED']
```

That is the question `CL_RETIRED` was put in the catalogue for, and it is the
shape most "what is orphaned" questions take.

Whole queries combine directly:

```python
q = sl.q.codelist
everything = reg.query(q)
areas = reg.query(q).filter(q.id.str.starts_with("CL_A"))
print(everything.difference(areas).ids())   # ['CL_FREQ', 'CL_RETIRED', 'CL_UNIT']
print(everything.intersect(areas).ids())    # ['CL_AREA']
```

## 13. When a relation goes only one way

`q.component.codelist` exists. `q.codelist.components` does not — the edge
graph exposes each foreign key from the side that holds it.

So "which codelists does `DSD_TRADE` use" cannot be written as a hop from
codelist. Write it as a subquery from the side that does have the edge:

```python
q = sl.q
trade = reg.query(q.component).filter(q.component.dsd.id == "DSD_TRADE").select(urn=q.component.codelist.urn)
print(reg.query(q.codelist).filter(q.codelist.urn.is_in(trade)).ids())   # ['CL_AREA', 'CL_FREQ', 'CL_UNIT']
```

When a path does not exist, the question is usually still one query, asked
from the other end.

## 14. Seeing what actually runs

A query will show you its SQL:

```python
q = sl.q.dataflow
print(reg.query(q).filter(q.agency == "ECB").to_sql().sql.splitlines()[0])   # 'SELECT s0.* FROM sdmx.artefact s0'
```

`.explain()` gives the database's plan, and `.preview(n)` runs a limited
version and returns a frame — useful when you are building a long chain and
want to see the shape before committing to it.

```python
print(reg.query(sl.q.code).preview(2).height)   # 2
```

## 15. The same query against a REST service

`reg.query(...)` is the same call on a `RestRegistry`. The query is analysed,
the fetch it needs is issued as an SDMX REST request, the result is
materialised locally, and *the original query* is then run over it.

```python
with sl.LocalRegistry(":memory:") as store, sl.RestRegistry(sl.Provider.ECB, store=store) as remote:
    print(remote.query(sl.q.dataflow).filter(sl.q.dataflow.agency == "ECB").ids())   # ['CPI_A', 'CPI_M']
```

The `store=` is where the fetched artefacts land, so a second query does not
re-ask for what the first already pulled.

The query is written the same way; only the source of the rows changes, so
the earlier sections did not need to mention it.

## 16. When you need SQL

Some questions are not worth expressing in any DSL. `reg.sql()` runs raw SQL
against the store, with `$name` parameters:

```python
rows = reg.sql(
    "SELECT artefact_type, count(*) AS n FROM sdmx.artefact GROUP BY artefact_type ORDER BY artefact_type"
).collect()
print(rows.to_dicts())
# [{'artefact_type': 'Codelist', 'n': 4}, {'artefact_type': 'DataStructure', 'n': 2}, {'artefact_type': 'Dataflow', 'n': 3}]
```

Use `sl.schema` for qualified names so a renamed column fails loudly rather
than at runtime. Never interpolate values into the string — pass them as
parameters.

---

## Where to go next

- [The surface, construct by construct](design/query-language-examples.md) —
  every operation, as reference rather than narrative
- [Worked solutions](design/query-language-cookbook.md) — longer questions
  answered end to end
- [Pushdown — where a query runs](design/query-pushdown.md) — what happens to
  a query against a REST service
- [Querying observations](design/data-plane-query.md) — the data plane, which
  uses the same ideas over observation rows
