# Structures as Frames

`collect()` on a query gives you SDMX artefacts — `Code` objects you navigate.
That is the right answer when the next step is *navigation*. When the next step
is *analysis*, you usually want two columns and a row per code, and building
that out of objects is a loop you should not have to write.

`select()` and `group_by()` are the frame-shaped branches of the same query.
They are **lazy**, like everything else in the DSL: they return another query,
and `collect()` is the only call that touches the database.

```python
import sdmxlib as sl
from sdmxlib.local import LocalRegistry
```

`sl.q` is the accessor. `sl.q.code` names the code relation, `sl.q.hcode` a
hierarchy placement, and every column and edge hangs off those.

## Projecting columns

`select()` takes columns — the same ones `filter()` compares:

```python
cl.codes.select(
    sl.q.code.id, sl.q.code.depth, sl.q.code.parent.id.alias("parent")
).collect()
```

```
┌─────┬───────┬────────┐
│ id  ┆ depth ┆ parent │
╞═════╪═══════╪════════╡
│ CAN ┆ 0     ┆ null   │
│ ON  ┆ 1     ┆ CAN    │
│ QC  ┆ 1     ┆ CAN    │
└─────┴───────┴────────┘
```

Columns are named the way you wrote them: `code.id` → `id`. The storage column
behind it is `code_id`, and that name belongs to the schema the path language
exists to hide — `reg.sql()` and `sl.schema` are where physical names live.

Note `parent.id` — a *hop* to the parent code, then its id — rather than a
`parent_id` column. The join trims away, because the id of the row you reached
by `parent_id` is the `parent_id` you started from. So there is one spelling,
and it costs no join.

It also needs the `alias`: `code.id` and `code.parent.id` both spell `id`, and
two columns of one name are refused rather than silently renamed. Either name
resolves it — `select(parent=sl.q.code.parent.id)` at the call site, or `alias`
on the expression. `alias` is the one that also works inside `list()`, whose
struct fields take their names from the columns given to it.

Localised labels carry their language, which is also what lets two of them
coexist in one frame:

```python
cl.codes.select(sl.q.code.name["en"], sl.q.code.name["fr"])  # name_en, name_fr
```

Rename with a keyword argument:

```python
cl.codes.select(geo=sl.q.code.id, label=sl.q.code.name["en"])
```

### Filters come before `select()`

Everything you would put before `collect()` goes before `select()`:

```python
(
    cl.codes.filter(sl.q.code.ancestors.id == "CAN")
    .filter(sl.q.code.depth == 1)
    .order_by(sl.q.code.id)
    .select(sl.q.code.id, sl.q.code.name["en"])
    .collect()
)
```

One statement, one round-trip.

### Building a label map

```python
labels = dict(cl.codes.select(sl.q.code.id, sl.q.code.name["en"]).collect().iter_rows())
```

### A projection composes as a subquery

Because `select()` stays lazy, a one-column projection is a `SqlRelation` — it
can be spliced into another query instead of being materialised and passed back
as a list:

```python
roots = cl.codes.filter(sl.q.code.parent.id.is_null()).select(sl.q.code.id)
cl.codes.filter(sl.q.code.parent.id.is_in(roots)).collect()
```

A projection splices; a bare `Query` selects every column of its relation,
and `IN` needs exactly one. Asking for the wrong one
says so, naming the columns it found.

## Counting and aggregating

`group_by()` takes the grouping keys and returns a pending group; `.agg()` takes
the aggregates. They are separate calls because one call cannot tell a key from
an aggregate — polars, which allows both in `group_by()`, silently treats an
aggregate there as a *key*.

`.len()` is the shorthand for counting rows per group, in a column named `len`:

```python
h.nodes.group_by(sl.q.hcode.level.id).len().collect()
```

```
┌──────┬─────┐
│ id   ┆ len │
╞══════╪═════╡
│ CD   ┆ 1   │
│ PR   ┆ 2   │
│ null ┆ 1   │
└──────┴─────┘
```

The null group holds the nodes without a level. Levels are optional in SDMX
3.0, so those nodes are counted rather than dropped.

`.agg()` takes named aggregates:

```python
cl.codes.group_by(sl.q.code.parent.id).agg(
    n=sl.q.code.id.count(),
).collect()
```

Keyword-only, because an aggregate has no name of its own to fall back on the
way a column does — `count()` over two different columns would collide.

The available aggregates are `count()`, `n_unique()`, `unique()`, `list()`,
`sum()`, `mean()`, `min()` and `max()`.

`list()` and `unique()` return whatever order the scan produced unless asked
otherwise. `sort()` orders by the collected column, `sort_by()` by another one
crossing the same fan-out:

```python
cl.codes.select(
    by_id=sl.q.code.children.id.sort().list(),
    newest_first=sl.q.code.children.id.sort_by(sl.q.code.children.position, descending=True).list(),
)
```

Ordering belongs to the expression rather than to the aggregate, so it composes
with whichever one follows — polars spells it the same way. `unique()` can only
be ordered by itself: a distinct aggregate collapses the rows another key would
have come from, so `sort()` works there and `sort_by()` is refused.

The last four say little about *structural* metadata: the only numeric column
in a codelist is `depth`, and its average is a fact about how someone drew the
tree rather than about the data. They exist because the same expression
language runs over observation values, where an average is meaningful. `min()` and `max()` order text too, so those two are useful here
— `sl.q.code.children.id.min()` is each code's alphabetically first child.

`sum` and `mean` need a numeric column and the compiler does not know column
types, so summing text is refused by DuckDB rather than by us: *"No function
matches the given name and argument types `sum(VARCHAR)`"*.

### Filtering the groups: `having()`

`filter()` asks about a row, and `group_by()` has replaced rows with groups —
so it refuses. The question about a *group* is `having()`:

```python
reg.query(sl.q.dataflow).group_by(sl.q.dataflow.agency).agg(
    n=sl.q.dataflow.id.count(),
).having(sl.q.dataflow.id.count() > 1).collect()
```

The aggregate is written out again rather than named by its output alias,
because `n` labels the *result*, while `HAVING` runs before the result exists, and SQL dialects disagree about whether the alias is visible there.
Writing the aggregate says what is computed without depending on it.

`having()` needs a `group_by()`. An aggregate over the root's own fan-out is a
question about a row, and `filter()` already takes it:

```python
reg.query(sl.q.codelist).filter(sl.q.codelist.codes.count() > 3).ids()
```

Both are the same `count()`; the clause decides whether it means a correlated
subquery over one row's children or a plain aggregate over a group's rows —
the same split `.over()` makes for windows.

### Ordering by an aggregate

`order_by()` on a projection takes one of its own items, named three ways: the
object itself, its output name, or the same expression written again.

```python
grouped = cl.codes.group_by(sl.q.code.parent.id).agg(n=sl.q.code.id.count())

grouped.order_by("n")                          # by output name
grouped.order_by(sl.q.code.id.count().desc())  # by the expression, newest first
```

`n_unique`, `unique` and `list` are the same question asked three ways of a
fan-out, and picking the wrong one is the common mistake:

| | answers | returns |
| --- | --- | --- |
| `list()` | which values, duplicates and all | a list per row |
| `unique()` | which values, each once | a list per row |
| `n_unique()` | how many distinct values | a number per row |

```python
cl.codes.select(
    sl.q.code.id,
    child_depths=sl.q.code.children.depth.list(),
    distinct_depths=sl.q.code.children.depth.unique(),
    how_many=sl.q.code.children.depth.n_unique(),
).collect()
```

All three need a path that fans out — here `children`. Over a to-one hop such
as `parent` there is one value per row and nothing to aggregate, so
`sl.q.code.parent.id.list()` is refused: project the column directly.

A null counts as a value in both: a fan-out whose collected column is all null
gives `unique()` → `[None]` and `n_unique()` → `1`, so `len(unique()) ==
n_unique()` holds on every row. That is polars' rule, not SQL's
`count(DISTINCT …)`, which skips nulls.

### Collecting a fan-out

`list()` is the one that changes shape. Reached through a to-many edge it
collects the children of each row without multiplying rows the way a join
would:

```python
cl.codes.select(sl.q.code.id, children=sl.q.code.children.id.list())
```

A row with no children collects to `[]`. SQL's own answer for an aggregate over
no rows is `NULL`, but these aggregates take polars' *names* — `list`, `unique`,
`n_unique`, `mean` — and so take its answers: `[]`.

That keeps `len(unique()) == n_unique()` true without a caller normalising, and
still distinguishes the two cases that matter: a row with no children collects
to `[]`, one whose children all have a null in the collected column to `[None]`.

Several columns are collected together by calling `list()` on the *edge*, which
produces a list of **structs**:

```python
cl.codes.select(
    sl.q.code.id,
    children=sl.q.code.children.list(sl.q.code.children.id, sl.q.code.children.name["en"]),
)
```

That is the recommended form whenever more than one field is wanted: two
separate `list()` calls over the same fan-out align only by accident, because
`list()` has no defined order without an `ORDER BY`.

### Referring back to an aggregate

Aggregates are referenced **by object**, not by name. Bind one and reuse it:

```python
n = sl.q.code.id.count()

(
    cl.codes.group_by(sl.q.code.parent.id)
    .agg(n=n)
    .order_by(n.desc())  # ORDER BY the count — `.desc()` marks the key
    .limit(10)
    .collect()
)
```

`order_by` on a projection takes one of its own items, which is what the
by-object reference buys: there is no name to mistype, and a key that was not
projected is refused rather than silently ordering by something else.

This is where sdmxlib deliberately parts company with polars. polars resolves
post-aggregation columns by *name*, which makes the same expression mean
different things either side of the boundary:

```python
lf = pl.LazyFrame({"g": ["a", "a", "b", "c"]})
counts = lf.group_by("g").agg(pl.len().alias("n"))

# polars — this keeps every group, including the ones with n == 1
assert counts.filter(pl.len() > 1).collect().height == 3

# what was meant
assert counts.filter(pl.col("n") > 1).collect().height == 1
```

`pl.len()` after `agg` is the length of the resulting frame, not of the group.
It raises no error and returns the wrong answer, which is why polars also needs
a separate `having()`.

### Restricting on a count, without grouping

An aggregate over a fan-out is an ordinary predicate, so the common "has more
than one child" question needs no `group_by` and no `HAVING`:

```python
cl.codes.filter(sl.q.code.children.count() > 1).ids()
cl.codes.filter(sl.q.code.children.count() == 0).ids()  # the leaves
```

Each compiles to a correlated *scalar* subquery in the `WHERE`, so it is
row-preserving — the query still returns codes, one row each. That is the
affirmative way to ask about an empty fan-out and the spelling the peers
converged on: polars `list.len() == 0`, ibis `length() == 0`, Django
`annotate(Count(...)).filter(n=0)`.

### Sorting and limiting happen in SQL

```python
n = sl.q.code.id.count()
cl.codes.group_by(sl.q.code.depth).agg(n=n).order_by(n.desc()).limit(10).collect()
```

The database returns ten rows. Aggregating first and sorting the frame
afterwards would have fetched every group.

Grouped results with no explicit sort come back ordered by their group keys, so
they are reproducible; a bare SQL `GROUP BY` guarantees no order at all.

## Distinct rows

**Two different `unique`s live in this language**, and the difference is what
they deduplicate. On a *column* it is the aggregate above — the distinct values
inside one row's fan-out. On a *projection* it is the one below — whole rows,
deduplicated against each other. polars divides `Expr.unique()` and
`DataFrame.unique()` the same way, which is where both names come from.

`unique()` is `SELECT DISTINCT` over the projection:

```python
cl.codes.select(sl.q.code.depth).unique().collect()
```

**It is full-row only and takes no arguments**, by design. A subset form — "distinct by agency, keep the rest" — fails twice
over. It would not commute with `filter`, since `filter(keep).unique(agency)`
and `unique(agency).filter(keep)` return different rows, and every other clause
in this DSL commutes with `filter` except slicing. And it leaves *which* row of
each group survives unspecified, so two runs may differ.

A caller who wants one row per group has `group_by(...)`, which says which one:

```python
# not: unique(agency) — which dataflow would you get?
reg.query(q.dataflow).group_by(q.dataflow.agency).agg(n=q.dataflow.id.count())
```

Polars draws the same line: `DataFrame.unique(subset=...)` takes a `keep=`
argument precisely because the answer is otherwise undefined, and a lazy query
has nowhere to put that choice.

A collection's *natural* order does not get in the way — `cl.codes` is in
declaration order, and `unique()` drops that, because the duplicates it
collapses had no single position between them. An order you asked for is a
different matter and is refused, naming the fix:

<!-- test: raises -->
```python
cl.codes.order_by(sl.q.code.position).select(sl.q.code.depth).unique().collect()
# PathError: cannot order a unique() projection by 'position': it is not among
# the projected columns ... Project it too, or drop it from order_by().
```

That one is caught when the query compiles — at `collect()` (or `to_sql()`),
not at `unique()`.

## What gets refused

**`group_by()` after `order_by()` or `limit()`.** A row sort cannot survive
grouping, and a limit before grouping would silently aggregate a truncated set:

<!-- test: raises -->
```python
cl.codes.order_by(sl.q.code.id).group_by(sl.q.code.depth)
# SliceError: cannot group_by() a query that is ordered or sliced ...
```

Sort and limit the *grouped* query instead. A filter on a group key belongs
before `group_by()`, where it narrows rows rather than groups — and is the
cheaper plan.

The order a collection carries *by default* is not an `order_by`: `cl.codes` is
in declaration order because a codelist has one, and grouping it is still
legal. Only an order you asked for blocks grouping, because only that one is a
decision the refinement laws have to protect.

**Filtering a projection.** `select()` changes what a row *is*, so a predicate
after it would be about the projection rather than about the relation:

<!-- test: raises -->
```python
cl.codes.select(sl.q.code.id).filter(sl.q.code.depth == 0)
# TypeError: cannot filter() a projected query ...
```

Filter first, then project.

**Projecting an annotation without collecting it.** Annotations live in a side
table and an annotation type may appear more than once on the same code, so
there is no single value to project or order by:

<!-- test: raises -->
```python
cl.codes.select(sl.q.code.annotations.title)
# PathError: 'annotations' crosses a to-many edge, so it has no single value
# per row. Collect it — .list() for the values, .count() for how many …
```

Note where that lands: at `select()` itself, before anything runs. A path
that crosses a to-many edge is known to have no single value the moment it is
projected, so the refusal comes at the call, like filtering a projection
does.

Collect it instead, which says which annotations and in what shape:

```python
cl.codes.select(sl.q.code.id, notes=sl.q.code.annotations.title.list())
```

Filtering on one needs no collection, because a filter asks *whether* rather
than *which*:

```python
cl.codes.filter(
    sl.q.code.annotations.any(
        sl.q.annotation.type == "geo_level",
        sl.q.annotation.title == "City",
    )
).select(sl.q.code.id).collect()
```

And `collect()` returns whole `Code` objects with their annotations populated —
there is nothing to opt into.

## Writing the SQL yourself

`sdmx.code` and `sdmx.hierarchy_node` are ordinary DuckDB tables on the same
connection, and `reg.sql(...)` is the supported hatch. Hand-written SQL gives
up three things the DSL keeps:

- **The schema is not frozen yet.** Column names and layout still move between
  versions. Queries written through the DSL move with them.
- **Predicates compose.** The tree walks and the annotation tests each compile
  to non-obvious SQL — correlated `EXISTS` clauses and pre-computed ancestor
  arrays — and they compose with each other and with a projection in one
  statement.
- **The scope filter is automatic.** A query reached through an artefact is
  already narrowed to the right URN. Forgetting that clause in hand-written SQL
  returns rows from every codelist in the edition, and nothing flags them as
  wrong.

The hatch is also a *terminal*: `reg.sql(...)` does not splice back into the
DSL, because the two carry different contracts and mixing them would blur which
one is in force.

## See also

- [Hierarchies](hierarchies.md) — `ancestor_at` and the transitive walks
- [Polars Integration](polars.md) — frames for *observation* data
- [Inspecting DSDs](inspecting.md) — navigating artefacts rather than framing them
