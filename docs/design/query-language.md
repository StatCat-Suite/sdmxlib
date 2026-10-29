# The query language: naming relations and joining them

**Status: built.** This is the design of the path language as it ships in
`src/sdmxlib/query/` — what it is, the rules it follows, and the reasons for
them. The surface is `sl.q` (one accessor per stored relation, 48 of them),
`reg.query(root)`, and the `Query` / `Select` / `Grouped` / `Page` objects it
returns. It compiles to one DuckDB statement, serves `RestRegistry` by fetching
and then compiling, and reaches a dataflow's observations through
`reg.obs(flow)`.

> **The query-language pages.** This one carries the **design** — the rules,
> the arguments for them, and what is still open.
> [`query-language-examples.md`](query-language-examples.md) is the surface
> **construct by construct**, and
> [`query-language-cookbook.md`](query-language-cookbook.md) **applies** it to
> the questions consumers were hand-writing SQL for.
> [`evidence/query-language.md`](evidence/query-language.md) carries what was
> **run** while the design was argued — peer surveys, schema measurements,
> benchmarks, the prototype log.
> [`query-language-explorations.md`](https://github.com/StatCat-Suite/sdmxlib/blob/main/history/design/query-language-explorations.md)
> carries branches that were prototyped and **rejected**, kept because each
> failure is why a live choice is right.
>
> Neighbours: [`query-composition.md`](query-composition.md) owns how clauses
> compose *within* one relation (the refinement laws);
> [`query-pushdown.md`](query-pushdown.md) owns where a query runs;
> [`query-tree-paths.md`](query-tree-paths.md) owns the four tree edges;
> [`query-naming.md`](query-naming.md) owns what things are called;
> [`data-plane-query.md`](data-plane-query.md) and
> [`data-plane-pushdown.md`](data-plane-pushdown.md) own the observation side.
> For a guided introduction, read [the query tutorial](../query_tutorial.md)
> first.

Claims here cite the other files rather than restating them; a measurement
appears inline only where the argument turns on the number.

```python
import sdmxlib as sl

q = sl.q
frame = (
    reg.query(q.dataflow)                                  # a plan; nothing has run
    .filter(q.dataflow.dsd.dimensions.concept.id == "REF_AREA")
    .select(flow=q.dataflow.id, dims=q.dataflow.dsd.dimensions.id.list())
    .collect()                                             # one SELECT, a pl.DataFrame
)
```

---

## 1. The problem

Before #306 there were four query DSLs — `cl.codes` over `sl.code`, `h.nodes`
over `sl.node`, `reg.artefacts(T)` over `sl.artefact`, and
`reg.reported_attributes()` over `sl.meta` — sharing ~2,400 lines of
`_query_common`. Each was roughly 300 statements of `filter` / `order_by` /
`limit` / `select` repeated per relation, and none could cross from one
relation to another. When #276 needed another table it produced a *predicate*
compiling to a correlated `EXISTS` (`uses_concept`, `uses_codelist`,
`in_category`), leaving the row kind unchanged.

The question that broke it (#280) is the one 21 hand-written consumer sites ask:
*every dataflow × dimension × codelist as one frame*. A projection could not
span relations because the row kind was singular, and resolving each structure
instead cost 265 ms against 1 ms as one statement for 200 DSDs × 12 dimensions —
331x. Across the surveyed consumers (#275 / #276 / #281) there were 113
hand-written SQL sites; the shapes they fall into are the cookbook's chapters.

#306 replaced them with one language over the whole storage graph:

- **one entry point**, `reg.query(root)`, taking any `sl.q` accessor;
- **paths as column references** — `q.dataflow.dsd.dimensions.concept.id` is a
  column of the dataflow query, reached through declared edges;
- **one compiler**, producing a single SQL statement whatever the paths cross;
- **one plan**, which a second backend (SDMX REST) consumes without the
  language changing.

The four DSLs are deleted. What survived of `_query_common` is
`query/_common.py` — the refinement laws and `canonical_key`, the part that was
never about emitting SQL. `sdmxlib.sql` still publishes `SqlRelation`, which is
the composition that had external consumers; it no longer publishes
`Predicate` / `CompileContext` / `RowKind`, so a downstream library can inject a
relation but not a predicate.

---

## 2. The rules

Several rules were reached by correcting an earlier draft; the branches tried
and rejected are in
[`query-language-explorations.md`](https://github.com/StatCat-Suite/sdmxlib/blob/main/history/design/query-language-explorations.md).

### 2.1 A path appears exactly where there is a decision

Where the query model can reach a column without choosing anything, there is no
path. The 1:1 identity split between an extension table and `sdmx.artefact` is
physical layout, not a join — `sdmx.dataflow` is `urn PRIMARY KEY` against the
same `urn`, and there is no `codelist` table at all. So the **maintainable
surface** — `agency`, `id`, `version`, `is_final`, `valid_from`, `valid_to`,
`name`, `description`, `uri`, `urn`, `annotations` — is on every maintainable
accessor with no prefix, by inheritance from a `_Maintainable[R]` base. Every
accessor roots at the table holding its columns, which for a maintainable is
always `sdmx.artefact`; an edge that lives on the extension is declared as a
compound hop across the split (`_Dataflow.dsd` is `("dataflow", "dsd")`).

A reference edge — `component.dsd` vs `component.concept` vs
`component.codelist` — is a real choice and is always named.

*Guard:* hoisting is sound only while each identity split stays PK-to-PK and
mandatory. `tests/unit/test_query_edges.py::test_the_identity_split_is_one_to_one`
fails loudly if one becomes optional or non-unique, because hoisted columns
would otherwise silently multiply or drop rows.

The validity columns were stored from the schema's first version and became
reachable later: `valid_from` / `valid_to`
are `Temporal` columns, so as-of filtering is an ordinary comparison
(`q.codelist.valid_to < cutoff`, `q.codelist.valid_to.is_null()`).

### 2.2 Name the edge, not the table

Both peer surveys in `evidence/query-language.md` agree: every peer names the
*edge* it joins along — SQLAlchemy via the relationship attribute, Django
via the FK path, Cypher via the relationship type, GraphQL by making every hop a
field. A prototype that joined by table and picked the first matching edge
worked, and silently chose a route when two existed — the one design in the
survey with no precedent. The schema has two routes to almost everything (a
component reaches a codelist directly and through its concept), so
`q.component.codelist` and `q.component.concept.codelist` are two spellings for
two questions and neither is guessed.

### 2.3 Accessors are lowercase singletons, and `sl.q.` compiles

`sl.q.component`, not `sl.Component`. The accessor does four jobs — namespace
for column names, target of an edge, root anchor for `reg.query()`, and
row-kind witness — and only the last needs a *type*, which is kept separate
(`ComponentRow`, `CodeRow`, …). The accessor classes are private and only the
singletons are exported, so "never instantiated by a caller" needs no guard.

That spelling carries the law separating the two traversals of this graph.
`Ref.__getattr__` resolves eagerly and returns domain objects; `sl.q` compiles to
SQL. The discriminator is lexical, present at every call site:

> **`sl.q.` compiles. Everything else resolves.**

```python
dim.codelist.codes              # resolves now, returns Code objects
sl.q.component.codelist.codes   # compiles to SQL
```

An earlier draft used uppercase classes and made class-versus-instance the
discriminator, SQLAlchemy's rule (`User.name` is an expression, `user.name` a
value). That is a real rule but an implicit one, and implicit rules are what
#222, #255, #261 and #292 cost here. The prefix is visible. What it cannot do
is survive aliasing — `c = sl.q.component` removes it from the call site — the
same exposure `pl.col` aliasing has, accepted.

### 2.4 The root fixes the grain

`reg.query(q.component)` is one row per component; `reg.query(q.dataflow)` is
one row per dataflow. Edges *up* are to-one and denormalise onto each row; edges
*down* are to-many and never multiply rows (§3.3). Rooting at the child is what
reaches the flat `(dsd, component)` grain `sl.polars.structure_frame` returns,
which no single base relation expresses otherwise.

This is the only place in the library that traverses **upward**:
`dsd.dimensions`, `Ref.__getattr__` and GraphQL all descend.

### 2.5 The row kind belongs to the root, not to the column's relation

A predicate is about a **row of the query's root**, reached through joins. So
`q.component.dsd.agency` is a `Str[ComponentRow]` even though `agency_id` lives
on `artefact`, and every accessor is generic over the row kind with the root
binding it:

```
q.codelist.id                     Str[CodelistRow]    root
q.component.codelist.id           Str[ComponentRow]   the component's own representation
q.component.concept.codelist.id   Str[ComponentRow]   the concept's default
```

`q.codelist` and `q.component.codelist` are the same accessor class at different
row-kind parameters, which is the precise reason they cannot be confused. The
first prototype typed edges as returning the *target's* row kind, and the
positive cases failed to compile — a component query rejected its own path. So
the typing requires it.

A predicate built for one root and handed to another is refused by the checker
(`Pred[CodelistRow]` is not a `Pred[ComponentRow]`) and again at runtime. Each
column records the root accessor it was reached from, its `origin`, and
`filter` compares that origin's table and discriminator with the query's root
(#510). Two maintainable roots share `sdmx.artefact` and differ by
discriminator, so `reg.query(q.dataflow).filter(q.codelist.agency == "ESTAT")`
raises `TypeError`: `filter() got a predicate built from q.codelist, but this
query is over q.dataflow`, followed by the spelling from `q.dataflow`. An origin
with no discriminator, `q.artefact`, fits any maintainable root, and so does a
root with none (`reg.artefacts(T)`). A column reached by a declared hop
(`q.dataflow.dsd.id`) is about the root's rows and passes
(`query.py:_same_relation`, `Query.filter`).

### 2.6 Paths are deterministic path expressions

`sl.q.component` is a closed nominal type: the `dsd_component` relation. A path
is a single statically-known route compiling to a fixed join tree; nothing is
matched and the result is single-valued.

| | deterministic path expression | pattern / path-matching |
| --- | --- | --- |
| examples | JPQL `c.dsd.agency`, EF Core `c => c.Dsd.Agency`, Django `dsd__agency`, GraphQL selection | XPath, JSONPath, Cypher `MATCH`, SPARQL property paths, Gremlin, jq |
| wildcards / alternation / recursion | none | `*`, `//`, `p1\|p2`, `[:R*1..3]` |
| a path denotes | one route, known statically | a set of routes, resolved at match time |

sdmxlib is in the left column; the nearest precedents are JPQL, whose spec term for
`c.dsd.agency` is literally *path expression*, and EF Core navigation paths.
(That table is recalled rather than run, unlike the surveys.) The right column
is **forbidden** rather than deferred, by the same argument as §2.2: a wildcard
path over a graph with two routes to everything resolves to an ambiguous union,
and a SQL row shape would have to pick, union, or refuse.

### 2.7 Recursion is a named verb, never path syntax

A hierarchy's nodes, a codelist's codes and a scheme's categories are fan-outs
like any other. Recursion is handled differently: variable-length paths — Cypher's
`[:NARROWER*1..2]`, SPARQL's `narrower+` — never enter path syntax; the
transitive questions stay named domain verbs on the lazy artefact
(`cl.narrower_transitive("EU")`, `cl.roots()`, `cl.leaves()`), and the compiler
never emits a recursive CTE.

That costs almost nothing, because the transitive closure is **baked at write
time**: every tree relation stores `depth` and an `ancestor_ids` list, so
`q.code.ancestors` / `q.code.descendants` are ordinary to-many edges matched by
membership in a stored list — one containment test, not a recursion.
`q.code.descendants.id.list()` is an ordinary correlated list over stored
columns, and `cl.narrower_transitive("EU")` is a named filter over the
`ancestors` edge (`q.code.ancestors.id == "EU"`). The four
tree edges (`parent`, `children`, `ancestors`, `descendants`) are the subject of
[`query-tree-paths.md`](query-tree-paths.md) §5. The corollary is the property
the compiler rests on: **every path is finite and compiles to a fixed join
tree.**

### 2.8 A separate query model beside the domain model

The domain model already declares edges — 45 `Ref`-typed fields. The first
proposal was to make the query language *be* that graph, with
SQLAlchemy-style descriptors so `sl.Dataflow.agency == "ESTAT"` built a
predicate. Both were prototyped:

- *Instrumenting attrs* works at runtime, but a bare descriptor loses static
  checking (`Dataflow.version == "1.0"` infers as `bool`), and recovering it
  needs `Mapped[T]`-style annotations on every queryable field — query types in
  the domain model's signatures.
- *A standalone query model* types cleanly and can mirror storage honestly. That
  matters because the `Ref` graph and the storage join graph overlap without
  either containing the other: storage has containment edges the model
  expresses as `ItemList` membership, side tables with no domain counterpart
  (annotations, localised text), and different names where the two do
  correspond. Deriving edges from `Ref` fields would be wrong.

This is SQLAlchemy Core's split — `Table` / `Column` independent of any mapped
class — and it is why `sdmxlib.query` imports from the model but the model
imports nothing from it.

### 2.9 Every edge is named, and `.any()` is the quantifier

Naming the first edge of a fan-out leaves a path vague when the predicate inside
reaches further. In `q.dsd.dimensions.any(q.codelist.id == "CL_GEO")`,
`dimensions` pins dsd → component, and the predicate is about a codelist, so the
compiler would still have to choose a route from component to codelist: the hop
with two routes and two answers (`['TRADE']` against `['CPI', 'TRADE']`,
`evidence/query-language.md`, "Two routes to a codelist, with rows"). The rule is
therefore to **name every edge**. Once every edge is named, the predicate inside
`.any()` is rooted at the child — `q.component.codelist.id` — and `.any()`
refuses a predicate built from any other relation with a `TypeError` (§3.4).

That separates two jobs:

> Naming every edge is what makes a path strict. `.any()` is the
> **quantifier**.

A parent-level `q.dsd.any(...)` has the quantifier and names no edge, which is
why it was rejected
([`query-language-explorations.md`](https://github.com/StatCat-Suite/sdmxlib/blob/main/history/design/query-language-explorations.md),
"A parent-level `.any()`"). Dot-through reaches the same strictness with no
child accessor, because the path never leaves the parent's accessor:

```python
q.dsd.dimensions.codelist.id == "CL_GEO"          # the component's own representation
q.dsd.dimensions.concept.codelist.id == "CL_GEO"  # the concept's default
```

Both are `Pred[DsdRow]`, and the two readings a bare inner predicate left
ambiguous are two spellings. A dot-through comparison carries the existential
quantifier itself. `.any(*preds)` is needed where several predicates must land
on one child (§3.4), `.every(*preds)` for the universal (§3.5), and the
fan-out's aggregates for projections (§6).

---

## 3. Edges, fan-outs, and how a path compiles

### 3.1 Edges are declared once and read both ways

`query/_edges.py` declares one `Edge` per storage foreign key — 110 of them.
`dsd.components` (down) and `component.dsd` (up) are the same
`dsd_component.dsd_urn`, declared once with an inverse name (SQLAlchemy's
`back_populates`, Django's `related_name`), so the compiler keeps a single edge
table and the two directions cannot drift.

**Cardinality follows the direction, and the schema checks it.** A forward hop
is to-one and an inverse hop to-many, except that a membership hop (an id
matched inside a stored list, §2.7) is to-many both ways (`Hop.to_many` in
`_joins.py`). `tests/unit/test_query_edges.py` checks that rule, and
optionality, against the live schema's key constraints: every forward hop must
reach a unique column, and every exposed inverse leave a non-unique one —
because a mis-declared cardinality fails *silently*, multiplying or dropping
rows, where a missing column fails loudly. The case that motivated it
is in [`query-language-examples.md`](query-language-examples.md) §5: "each
component with its dataflow" was written as a to-one path in seven design
examples, and three real rows showed it duplicating one component and dropping
another. The same file also requires every reference-shaped column to be an
edge or carry a written reason for not being one.

An accessor attribute is the user-facing side of an edge: `edge(...)` for a
to-one hop, `many(...)` for a to-many one, whose target class mixes in `FanOut`
so the cardinality change is visible in the type. Some to-many attributes are
**facets** of one edge — `dsd.dimensions`, `dsd.attributes`, `dsd.measures` are
`dsd.components` restricted by `component_type`, and `dimensions` excludes the
time dimension.

### 3.2 To-one hops: one join tree, shared prefixes

A path is a tuple of attribute names; `_joins.py` resolves it against the edge
graph into hops, which is also where an undeclared route is caught — a path is a
proof the route exists. All the paths one scope needs become **one** join tree:
two paths off one root, `dimensions.concept.id` and `dimensions.codelist.id`,
produce one join to `dsd_component`, not two. That deduplication is a
requirement rather than an optimisation — the hostile review measured
correlated-subquery cost as linear in the number of fan-out columns
(`evidence/query-language.md`, "Cost of correlated subqueries per fan-out
column").

### 3.3 To-many hops compile to a correlated subquery

> **A path that crosses a to-many hop compiles to a correlated subquery, never a
> join.**

`EXISTS` in a filter, an aggregate subquery in a projection. That is what makes
`q.dsd.dimensions.concept.id == "REF_AREA"` mean *some* dimension while leaving
the row count alone — the property #254 chose `EXISTS` for — and the reason two
fan-outs in one filter cannot produce a cartesian product the way two joins
would.

Django is the counter-example: `Codelist.objects.filter(codes__cid__in=["A",
"B"])` is an `INNER JOIN` returning the codelist twice, and the documented fix
is `.distinct()`. Adopting Django's implicit spelling for to-many would import a
defect class we do not have.

So a to-many column has **no single value per row**, and the language says so
rather than picking one: projecting `q.dsd.dimensions.id` bare, grouping by it,
or using it inside a grouped aggregate raises `PathError` naming the fix —
collect it (`.list()`, `.count()`, `.n_unique()`) or root the query at the
child.

### 3.4 Dot-through for independent children, `.any()` for one child

Dotting through a to-many hop is the common case and needs no extra syntax. But two
predicates over one to-many edge are ambiguous in every path language:

```python
reg.query(q.dsd).filter(
    q.dsd.dimensions.concept.id == "REF_AREA",
    q.dsd.dimensions.codelist.id == "CL_GEO",
)
```

means either "some dimension has that concept **and** some dimension has that
codelist" or "one dimension has both", and they return different rows. Django
distinguishes them by chaining (`filter(a, b)` vs `filter(a).filter(b)`), which
is unavailable here because chained `filter` is AND by a law with property tests
behind it. The rule:

> **Predicates inside one `.any()` call land on one child. Predicates outside it
> are independent.**

```python
reg.query(q.dsd).filter(
    q.dsd.dimensions.any(
        q.component.concept.id == "REF_AREA",
        q.component.codelist.id == "CL_GEO",
    )
)
```

`.any(*preds)` takes child-rooted predicates (`Pred[ComponentRow]`, which is
what `q.component.*` produces); a parent-rooted one is a type error, and at
runtime `.any()` raises `TypeError` before any SQL runs, naming the child
relation and telling the caller to write the predicate from its accessor (#511).
`.every()` and the fan-out's `list` / `unique` / `count(where=)` apply the same
check. The child
accessor adds nothing when a fan-out holds one predicate and marks the grouping
when it holds several. polars makes the same distinction with
`pl.element()` bound by `.list.eval()`; ours differs only in being typed.

`.any()` is a **binder by position**: nothing in the text connects
`q.component` to `dimensions` except being that call's argument —
SQLAlchemy's shape (`Dsd.dimensions.any(Component.x == 1)`). Nested binders of
different relations compose, each `.any()` binding its own child. Two bindings
of the *same* relation at once are not expressible (§12).

### 3.5 `.every()` is null-safe and vacuously true

`FanOut.every(*preds)` is the universal and takes the same child-rooted
predicates as `.any()`:

```python
reg.query(q.dsd).filter(q.dsd.components.every(q.component.codelist.id.is_not_null()))
```

Its SQL has two traps, found on a fixture of A (all dimensions `CL_GEO`), B
(mixed), C (one dimension with a NULL codelist) and D (no dimensions):

```
some    -> ['A', 'B']
every   -> ['A', 'C', 'D']      naive NOT EXISTS (... AND NOT pred)
every   -> ['A', 'D']           null-safe
```

**C is wrong under the naive form**: `NOT(NULL)` is NULL, so a non-enumerated
dimension is not counted as a violation and silently satisfies "every dimension
is `CL_GEO`". The compiler negates with `IS NOT TRUE`, so a child whose column
is NULL counts as a violation. **D is vacuously true**, as in standard logic;
the behaviour is documented and kept.

`every` composes freely: inside another `every`, inside `any`, and across nested
to-many edges (`q.codelist.codes.every(q.code.annotations.type == "NOTE")`
crosses two). An earlier draft predicted refusals for zero or several to-many
hops; the compiler that shipped needs neither, and
`tests/unit/test_query_compile.py::test_every_composes_across_nested_to_many_edges`
keeps it so.

### 3.6 Join kind on nullable to-one edges

`component.codelist` is absent for a non-enumerated component, so this is the
common case. Measured against Django:

```
filter(cl__name='Areas')   -> INNER JOIN       orphan dropped
values('cid','cl__name')   -> LEFT OUTER JOIN  orphan kept, value None
exclude(cl__name='Areas')  -> LEFT OUTER JOIN  orphan RETURNED
```

The compiler joins a mandatory to-one hop with `JOIN` and an optional one with
`LEFT JOIN` (optionality, like cardinality, is read off the schema), so a
projection keeps the row with a null value, and a positive filter drops it
because a null cannot satisfy it. Negation follows SQL's three-valued logic: `~(q.component.codelist.id ==
"CL_FREQ")` and `!=` are *both* NULL for a component with no codelist, so
neither returns it. That is the documented reading of `~` (SQL's `NOT`) — a row
whose column is `NULL` satisfies neither `p` nor `~p` — and differs from
Django's `exclude`. To include the orphans, say so:
`(q.component.codelist.id != "CL_FREQ") | q.component.codelist.id.is_null()`.
`~` over a **to-many** edge is `NOT EXISTS`, a true complement.

### 3.7 An aggregate over a fan-out is also a predicate

`q.code.children.count() == 0` — "how many children, and is that zero" —
compiles to a correlated scalar subquery in the `WHERE`, row-preserving like
every other fan-out. This is the affirmative way to ask about an empty fan-out,
the one the peers converged on (polars `list.len() == 0`, ibis `length() == 0`,
Django `annotate(Count(...)).filter(n=0)`); Django's other spelling,
`children__isnull=True`, is a `LEFT JOIN` plus `IS NULL` and is deliberately not
copied. Such a comparison composes with `&` / `|` like any predicate.

---

## 4. Pushdown: one language, several backends

### The goal

**Where a query runs is a property of the backend, not of the language.** A
caller writing `reg.query(q.dataflow).filter(...).select(...)` should not have
to know whether the structures live in a local DuckDB or behind an SDMX REST
endpoint.

### The plan nodes

The design proposed a small node set — `Scan`, `Filter`, `Project`,
`Join(edge)`, `Nest` / `Explode`, `Aggregate`, `Sort`, `Limit` — small because
the schema is fixed. When the second backend was built, every one of them
turned out to exist already, and that measurement is why there is **no
separate IR** ([`query-pushdown.md`](query-pushdown.md) §2):

| node | where it lives |
| --- | --- |
| `Scan` | `Plan.root` — the accessor, carrying its table and discriminator |
| `Filter` | `Plan.predicates`; windowed filters in `Plan.qualifiers` |
| `Join(edge)` | not stored — derived from the paths, because a path *names* its route |
| `Sort` | `Plan.order_keys` / `default_order` |
| `Limit` | `Plan.limit_n` / `offset_n` / `cursor` |
| `Project`, `Aggregate`, `Nest` | `Select` — its items and group keys |
| `Explode` | not built: one `.explode()` call in polars (§6) |

`query/_plan.py`'s `Plan[R]` is the question with nothing about answering it;
`Query` is a `Plan` plus its wiring (a connection, a resolver, and for REST a
`prepare` hook that fetches before the first statement). Every clause evolves a
new frozen `Plan`, and `canonical_key` keys it by meaning, so
`filter(a).filter(b)` and `filter(b).filter(a)` are one question.

### Capability, program, and the REST backend

`query/_pushdown.py` puts a `Plan` and a backend's `Capability` together and
produces a `Program` — an ordered sequence of `Push` and `Residual` steps, not a
`(pushed, residual)` pair, because REST answers some questions in two calls
with a local step between. DuckDB's capability is total, so its program is one
`Push` and the emitted SQL is unchanged.

SDMX REST's structure plane filters on agency / id / version only, plus
`detail=` and `references=`. So `RestRegistry.query(...)` does not interpret a
query remotely at all: it **fetches what the plan needs, materialises it into a
local store, and runs the original query** through the compiler that already
exists (`query/_execute.py`). The critical part is the fetch's *closure*
(`query/_closure.py`): the fetch must contain every relation the query's paths
reach, counting **crossings** into other artefacts rather than hops, because a
fetch one relation short returns nothing and raises no error. `references=`
comes from there (`query/_rest.py`).

The closure is derived from the plan: its predicates, its order and any residual
steps, and on the root scan the paths a `Select` reads. So a REST query that
projects across a hop — `select(f=q.dataflow.id, d=q.dataflow.dsd.id)` — is
fetched with the hop's target and answers as a `LocalRegistry` does (GH-271).

### Refusing an unbounded fetch

The costly failure of partial pushdown is a filter REST cannot evaluate over an
unbounded scan: the client downloads a registry to keep three rows, the
800-resolutions problem, with no warning. The planner **refuses** instead: a scan that leaves anything to filter locally must be bounded by an
agency, or the terminal raises `PushdownError` naming the column to add. A bare
listing, or equality on id or version, needs none. The rule, the per-scan bound
and why `limit` cannot be pushed past a residual are
[`query-pushdown.md`](query-pushdown.md) §5.

`explain()` runs nothing and shows the SQL and its binds; for a query that will
fetch first it says so on its own line, because the SQL alone reads as a local
scan.

### The earlier no-IR decision, superseded

`query-composition.md` decided against an intermediate representation for the
structural DSLs. Its reasoning was about **expressiveness** — 13 of 18 collected
shapes were expressible, and the other five were feature decisions an IR would
not deliver — and that measurement stands. But it was taken with one backend,
so the only question an IR could answer was *what a query can say*. Two
backends ask *where a query runs*, which is orthogonal. In the event the IR was
the `Plan` the query already carried, so the conclusion "build no node set"
survived while its premise did not.

### Observations are the same plan, one plane over

`reg.obs(flow)` roots the same `Query` machinery at a dataflow's
observations (§9). Over REST it pushes key-dimension equality and membership
into the data key and attribute filters into SDMX 3.0's `c[...]`, and evaluates
the rest locally on what came back — the data plane is a strong pushdown target
where the structure plane is a weak one. See
[`data-plane-pushdown.md`](data-plane-pushdown.md).

---

## 5. Strict laziness

**Nothing executes until a terminal.** `reg.query(...)`, `filter`, `select`,
`order_by` and every expression build descriptions; the terminals run them:

| terminal | returns |
| --- | --- |
| `collect(annotations=True)` | artefacts / items (`Query`), or a `pl.DataFrame` (`Select`) |
| `ids()`, `urns()` | `list[str]`, no object construction |
| `count()`, `exists()` | `COUNT(*)` / `EXISTS`, fetching no rows |
| `first()`, `one()`, `one_or_none()` | one object, or `None`, or a raise |
| `frame()`, `stream()` | the relation's stored columns as polars / Arrow batches |
| `preview(n)` | a bounded head, for a REPL |
| `page(n)` | a `Page` of urns plus a cursor |

`explain()` and `to_sql()` run nothing, and neither does `Select.columns`,
which a projection knows from its own items. `Query.columns` asks the database:
it runs `DESCRIBE` over the compiled SQL, which plans without reading rows, and
over REST it fetches first, because the statement is described against the
materialised store. Every terminal leaves the query unchanged: a plan is a
value, refined and run as often as wanted.

### No implicit execution, anywhere

polars refuses `len(lf)`, `iter(lf)`, `lf[0]` and `bool(lf)` deliberately; the
old DSLs implemented all four, and each ran a query without saying so. `for c in
cl.codes` inside a loop over codelists is the N+1 shape measured at 12x in
`evidence/query-language.md` ("Fan-out and N+1"). So a `Query` has no
`__iter__`, `__len__` or `__contains__`, and each raises naming the terminal to
use instead. The middle position — keep the dunders where a receiver bounds the
scope (`cl.codes`) — does not hold, because bounded-per-receiver is exactly the
shape that produces N+1 *across* receivers.

**Uniform laziness is the precondition for pushdown.** A planner cannot push what has already been evaluated, and the REST `prepare` hook can
fetch at the terminal precisely because `reg.query(q.dataflow)` alone has not
yet said which agency it means. And `explain()` is total: the plan a caller
inspects is the whole of what will happen.

`in` had two further problems. Measured, polars' `DataFrame` and `LazyFrame`
and ibis all implement `__contains__` as *column-name* membership, and SQLAlchemy's `Select`
not at all, so `"CA" in cl.codes` would read to a polars user as "is there a
column called CA". And the old `CodeQuery.__contains__` looked the id up against
the table, ignoring the query's own filters. `q.filter(...).exists()` cannot
bypass them.

### Indexing with `[]`

- `q[1:3]` is `offset(1).limit(2)` and inherits their laws — polars' and
  Django's rule, *`[]` may refine*.
- `q[0]` raises: positional extraction needs a total order to mean anything,
  and it would run the query from an operator.
- `q["FR"]` **looks the row up by id** and returns the object (`KeyError` if
  absent); `q.get("FR")` returns `None` instead. This is `ItemList` parity —
  `codelist.codes` has to answer the same questions whether the codelist is a
  `Codelist` or a `LazyCodelist` — and it is unambiguous against the other two
  because an id is never a position.

The design at one point proposed the string form as lazy sugar for
`filter(id == ...)`. It shipped as an extracting lookup instead, because the
docs and callers use `codes["FREQ"]` for `ItemList` parity throughout; the
lazy reading is one `filter` away.

### Collections extract, plans refine

| family | operates on | filter | sort | terminal |
| --- | --- | --- | --- | --- |
| query — lazy | a plan | `filter(predicate)` | `order_by` | `collect()` |
| collection — materialised | objects in memory | `filter(lambda)` or `sl.item(...)` | `sort_by` | — |

**Lambdas are for materialised collections; predicates are for queries.** This
is the `sort_by` / `order_by` split `query-composition.md` already calls
deliberate — *the name is the signal* — extended to `filter`.

That retired the REST-backed `Query[T]` (`reg.query(sl.Dataflow, ...)`). Its
only distinguishing feature was `filter(lambda)` evaluated in Python over
everything fetched, and strict laziness bans exactly that — so once it was lazy
and compiled it *was* the path language under another name. Its behaviour was
also wrong: its `filter` / `limit` ran **after** the fetch, so
`query(Dataflow, agency="ECB").limit(5).fetch()` read as a bounded request and
was a full catalogue download trimmed in Python; and `filter` appending to the
receiver and returning `self` was #291. The project breaks cleanly pre-stability
rather than carrying both, and `RestRegistry.query(sl.q.X)` is the replacement.

Kept beside `reg.query`: `reg.artefacts(T)` (a class-to-discriminator mapping
over the shared maintainable surface, for helpers that cannot name a root) and
`reg.reported_attributes()` (every reported metadata value) — both return the
same `Query`. And the lazy artefacts' named verbs (`cl.narrower(...)`,
`cl.roots()`, …) return `Query`s too.

### Cost

*Discoverability*: a lazy object prints a plan, not data — the standing
complaint against ibis and polars, mitigated the same way, with a `repr` that
shows the plan, `explain()`, and `preview()`. *One word* at every site that used
to iterate: `.collect()`.

---

## 6. Projection and aggregation

`select(...)` turns a `Query` into a `Select`, whose `collect()` returns a
`pl.DataFrame` and whose `columns` are known without running anything.

```python
reg.query(q.dsd).select(
    q.dsd.id,
    dims=q.dsd.dimensions.id.list(),
    n=q.dsd.dimensions.count(),
)
```

- **Names.** A positional column projects under its own name; a localised one
  carries its language (`name["en"]` → `name_en`), so two languages cannot
  collide. Two columns that would share a name — `q.dataflow.id` and
  `q.dataflow.dsd.id` — are refused with a message asking for keywords. An
  aggregate must be named.
- **`Select` has no `filter`.** `select` changes what a row is, so a predicate
  afterwards would be about the projection; it raises "filter first". A `Select`
  refines with `order_by` (by column, aggregate or projected name), `limit`,
  `offset`, `unique` and, when grouped, `having`.
- **A one-column `Select` is an `SqlRelation`**, so it splices into `is_in` as a
  lazy subquery (§7.6).

### A fan-out nests by default

Graph APIs split two ways on fan-out. Cypher, Gremlin and SPARQL return tables
and multiply rows because the pattern says so; GraphQL returns trees. Cypher has
both, via `collect()`, and that maps onto this stack exactly:

```
DuckDB list(...)   ==   Cypher collect()   ==   polars List dtype
```

So a fan-out in a projection **nests**: nesting cannot silently multiply anyone's
row count, and exploding is an explicit request made in polars, where the caller
already knows the verb (`frame.explode("dims")`). Measured on 300 dataflows × 12
dimensions: exploded 0.7 ms / 3,600 rows; nested 0.9 ms / 300 rows. No
server-side `explode` is built.

### Array aggregation over side tables

`query-composition.md` once recorded `list(annotation.title)` per code as a
*refusal*: aggregating side-table rows "would mean the join that `EXISTS` exists
to avoid". The premise was false. A correlated list subquery aggregates with no
join and no grouping:

```sql
SELECT c.code_id,
       (SELECT list(an.title) FROM sdmx.annotation an
         WHERE an.owner_urn = c.code_urn AND an.annotation_type = ?) AS notes
FROM sdmx.code c
```

The row count is preserved, the property `EXISTS` was chosen for in #254.
Every projected fan-out compiles to this form.

### `list()` of several columns returns structs

Two `list()` calls over one child are two independent correlated subqueries, and
DuckDB's `list()` has no defined order without `ORDER BY`. So `labels[i]` is not
reliably the label of `codes[i]`, and the two diverge silently the moment either
gains an order (measured in `evidence/query-language.md`, "`list()` alignment").
The fix builds alignment into the result: the fan-out's own
`list()` takes several columns and returns **one list of structs**, in one
subquery.

```python
q.codelist.codes.list(q.codelist.codes.id, q.codelist.codes.name["en"])
# -> list[struct{id, en}]
```

polars' `group_by().agg()` and ibis's `collect()` keep two lists aligned only
because each comes from a single grouping pass; two separate calls have no
such guarantee.

**Order inside a fan-out** is a property of the expression, polars' shape:
`q.codelist.codes.id.sort_by(q.codelist.codes.position).list()`, or `.sort()` to
order by the value itself (the only order `unique()` accepts, since `DISTINCT`
collapses the rows any other key came from). Unsorted, an aggregate is
scan-ordered; an `ORDER BY` on every aggregate would sort when the caller has
not asked for it.

### One aggregate, three readings

The aggregates are polars' names — `list`, `unique`, `count`, `n_unique`, `sum`,
`mean`, `min`, `max`, and `sl.len()` for rows. The same expression means
different things in different clauses, decided by the clause that receives it:

| where it appears | reading | SQL |
| --- | --- | --- |
| `select(...)` / `filter(...)`, over a path crossing a to-many hop | per row, over that row's children | correlated subquery |
| `group_by(...).agg(...)`, or `Query.agg(...)` | over the group's rows | `GROUP BY` aggregate |
| followed by `.over(...)` | over the outer rows sharing a partition | window function |

That context-dependence is the polars-pipeline hazard `query-composition.md`
warns about, adopted deliberately: polars' `Expr` layer has it too (§7.5).

**`where=` restricts what one aggregate reads, in both readings** — SQL's
`FILTER (WHERE ...)` (#373). Over a fan-out it chooses which children
(`c.count(where=q.code.depth == 0)`); in a grouped aggregate it chooses which of
the group's rows. It is evaluated per candidate row, so the
same-child / different-child question `.any()` answers does not arise, and AND /
OR are ordinary `&` / `|`. A tuple is refused — a variadic form would only be a
second spelling of `&`. The fan-out's own `list(..., where=)` / `count(where=)`
types the predicate to the child's row kind, so prefer it to the column's.
Nothing matching gives `0` or `[]`.

### Grouping

`group_by(*keys)` returns a `Grouped`; `.agg(**named)` or `.len()` makes it a
`Select`. It is not a refinement — it changes shape — so it is refused after
`order_by` or a slice (a row sort key that is not a group key is not valid SQL,
and a limit before grouping aggregates a truncated set). A to-many path can be
neither a group key nor the column of a grouped aggregate (§3.3): a correlated
subquery has no value to aggregate across a group, and the language says so
rather than inventing a join. `having(...)` takes aggregate predicates on a
grouped `Select` and is refused on an ungrouped one, naming `filter` instead.

### `unique()` is full-row only

`Select.unique()` is `SELECT DISTINCT` over the projected row. The subset form,
`unique(agency)`, is excluded: measured, `filter(keep).unique(agency)` and
`unique(agency).filter(keep)` return different rows, and it leaves *which* row
survives unspecified. A caller wanting one row per group has
`group_by(...).agg(...)`, which says which. Excluding it keeps `unique` in the
commuting set, so the third composition law needs no carve-out.

The aggregate `Col.unique()` / `FanOut.unique(...)` is a different thing — the
distinct *values* inside one fan-out — and both names are polars', where
`DataFrame.unique()` and `Expr.unique()` divide the same way.

---

## 7. Composition: laws, set operations, windows, paging

### 7.1 The refinement laws hold across paths

Everything `query-composition.md` guarantees holds unchanged: `filter` ANDs,
`limit` takes the minimum, `offset` raises the floor, `order_by` replaces, and
`filter`, `qualify`, `order_by`, `intersect`, `difference`, `after`, `group_by`
and `agg` after a slice raise `SliceError` rather than silently running before
the slice (`group_by` and `agg` also refuse an ordered query, §6). The generated properties in
`tests/unit/test_properties_query_laws.py` — monotonicity, idempotence, and
*every ordering either commutes or is refused* — run over path predicates like
any other.

### 7.2 Set operations: `union` is a free function

```python
sl.union(a, b)          # a FREE FUNCTION, never a method
a.intersect(b)          # semi-join — a method
a.difference(b)         # anti-join — a method
```

The split follows the refinement principle: *nothing a later caller adds
can undo what an earlier call established*. Measured against polars,
`intersect` and `difference` return a subset of `a`; `union` does not.
`helper_scoped_to_estat.union(everything)` as a method would undo the helper's
scoping, which the principle forbids; as a free function it
composes two queries into a third rather than refining either. All three
require one root — `cannot combine a query over dsd with one over codelist` —
including two maintainable roots (`q.dsd` and `q.dataflow`), whose rows are
different relations even where their columns are shared.

### 7.3 Windows and `qualify`

`.over()` supplies the framing; the window **functions** are separate —
`rank`, `dense_rank`, `row_number`, `lag`, `lead` — and any aggregate can be
framed too (`q.component.id.count().over(q.component.dsd.id)`). "The latest
version of each artefact" needs both a window function and a filter on its
result, and that filter is a **separate clause**:

```python
reg.query(q.dataflow).qualify(
    q.dataflow.version.rank(descending=True).over(q.dataflow.agency, q.dataflow.id) == 1
)
```

`qualify` is SQL's own name for a post-window filter, and it exists because a
windowed predicate does **not** commute with an ordinary one: a rank is computed
over whatever set precedes it.

```
filter(p).filter(rank().over(...) == 1)   -> 3 rows
filter(rank().over(...) == 1).filter(p)   -> 2 rows       commute? False
```

Naming the stage moves the order-dependence into the vocabulary, so `filter`
commutes with `filter` and `qualify` is a later stage whatever the call order.
The type enforces it: `.over()` returns `Win[R]`, whose comparisons produce
`WinPred[R]`, deliberately not a `Pred[R]` — the checker rejects a `WinPred` in
`filter` and a `Pred` in `qualify`. A `Win` stays a `Col`, so a windowed column
is projectable; only the filter position is restricted. `==` / `!=` build the
predicate; the orderings are methods (`.le(3)`, `.lt`, `.ge`, `.gt`) — "the three
newest" is `row_number(...).over(...).le(3)`. `Version` columns order by the
version sort key baked beside them, so `1.10` ranks after `1.9` (#311).

### 7.4 Keyset paging

`limit` / `offset` degrade at depth. `page(n)` returns a `Page` of `n` urns plus
a `Cursor[R]`, and `after(cursor)` resumes from it; `after` narrows, so it is a
refinement like `offset`. `Cursor[R]` is parameterised on the row kind, so a
cursor from one root cannot be handed to another.

`after()` needs no precondition about order, because every query already emits
the root's key as a tiebreak whether or not the caller sorted — so the order is
always total and an unsorted query pages by key: arbitrary but stable, which is
all paging requires. What `after()` refuses is a cursor from a differently
*shaped* order (a different number of keys, or reversed), which would compare
the wrong columns and skip rows silently. `order_by(None)` declines the
tiebreak — for `stream()`, which reads everything once — and is therefore
refused together with `limit` or a cursor (#389).

The comparison is nested rather than SQL's row constructor: `(a, b) > (?, ?)`
cannot express keys whose directions disagree, and "newest version first, then
id ascending" is the common shape. Nulls follow the emitted `NULLS LAST`.

### 7.5 Composition stays a query, not a pipeline

`query-composition.md` names polars as the standing hazard: *"its vocabulary
is borrowed here, but it is a pipeline"*. The path language takes polars'
expression layer and keeps its own composition laws, because **polars has two
layers with different laws**:

| polars layer | semantics | what we took |
| --- | --- | --- |
| `Expr` — `pl.col("a") > 5` | declarative; construction order irrelevant, the receiving context gives meaning | **this** — paths as expressions, namespaces, column methods |
| `LazyFrame` — `.head(3).filter(p)` | a pipeline; order matters throughout | **not this** — the refinement laws stay |

So `filter` still ANDs, `limit` still takes the minimum, and filter-after-slice
is still refused. The vocabulary is polars'; the composition laws are ours.
That division avoids the "names without the laws" failure that produced #222,
#255, #261 and #292.

The two features that did not commute — windowed filters and `unique(subset)` —
were caught by the third law before either was settled, and each was fixed the
cheaper of two ways: the window by naming its stage (`qualify`), the subset
`unique` by excluding it. The path language does share context-dependent
meaning with polars' `Expr` (§6, "One aggregate, three readings"); that was
taken knowingly, and the receiving clause is always visible.

### 7.6 Relations inside `is_in`, and "matched by either of two routes"

`is_in` takes a list of literals or any one-column `SqlRelation`, compiled to
one `IN (SELECT ...)`. A `Select` of one column is one, so a structural query
filters another lazily without leaving the language:

```python
european = cl.narrower_transitive("EU").select(q.code.id)
cl.codes.filter(q.code.id.is_in(european))
```

Both sides must read one database: a relation from another registry is refused
with `CrossSourceError`, because the subquery would silently read *this*
database under the other one's names.

The same mechanism settles how "matched by either of two routes" is spelled —
`uses_concept` matched a DSD carrying a component *and* a dataflow whose DSD
did. Checked against the peers, SQLAlchemy writes `or_(exists(...),
exists(...))`, ibis `t.c.isin(a) | t.c.isin(b)`, polars an `is_in` over two
sets; none reaches for a set operation, because union is for stacking result
sets, not for one set matched two ways. So it is an `|` of two `is_in`
sub-queries, which composes because it is a predicate:

```python
dsds = reg.query(q.dsd).filter(q.dsd.dimensions.codelist.id == "CL_UNIT").select(q.dsd.urn)
flows = reg.query(q.dataflow).filter(q.dataflow.dsd.dimensions.codelist.id == "CL_UNIT").select(q.dataflow.urn)
reg.query(q.artefact).filter(q.artefact.urn.is_in(dsds) | q.artefact.urn.is_in(flows))
```

---

## 8. Columns, literals and text

### Column types

Each column is typed for what it can do, and the operators follow:

| type | e.g. | carries |
| --- | --- | --- |
| `Str` | `q.code.id` | `==`, `!=`, `is_in`, `.str`, `.len()`, `+` (concatenation) |
| `Label` | `q.code.name["en"]` | as `Str`, plus the `unaccented` / `folded` rungs |
| `Vocabulary` | `q.component.role` | as `Str`, with the legal values checked |
| `Version` | `q.dataflow.version` | compares as text, orders by version sort key |
| `Num` | `q.code.depth`, `q.code.position` | orderings and arithmetic |
| `Temporal` | `q.codelist.valid_to` | orderings; projects as a UTC instant |
| `Bool` | `q.dsd.is_final` | `==`, `!=` |
| `Arr` | `q.component.concept_roles` | `.has(value)` |
| `Untyped` | `obs.col("REF_AREA")` | comparisons and `.str`, for columns known only at runtime; `obs.text(id)` / `obs.num(id)` give `Str` / `Num` (#511) |

Every column has `is_null()`, `is_not_null()`, `is_in(...)`, `.desc()`, the
aggregates and the window functions.

### Literals are checked; comparisons are column against literal

`q.code.id == 1` raises `code_id holds str, got int. Did you mean "1"?`; `==
None` raises naming `is_null()` (`= NULL` is never true); `is_in("CA")` raises,
because a string is an iterable of characters (#261 — found only by placing the
API beside polars, which rejects it). A `Vocabulary` column refuses a value it
can never hold — `q.component.role == "dimension"` names `'Dimension'` and the
legal values — because `== "Codlist"` matches nothing and `!= "Codlist"`
matches everything, and neither is what the caller meant.

A comparison's right-hand side is a **literal**: comparing two columns
(`q.code.id == q.code.parent.id`) is refused by the same literal check. Computed
columns are columns, so `q.code.position + 1 == 2` and `q.code.id.len() == 2`
work, as does projecting `q.code.codelist.id + ":" + q.code.id`. Column-to-
column comparison is open (§13).

### Operators that cannot be overloaded

`and` / `or` / `not` cannot be overloaded in Python, so on an expression they
would silently drop an operand; `bool()` on a predicate raises instead, polars'
precedent. Predicates combine with `&`, `|`, `~`, and since Python binds `&` /
`|` tighter than `==`, `(a == 1) | b == 2` parses as `((pred) | column) == 2` —
refused on both arms, including the reflected one (`"A" | ...` calls
`str.__or__`, so a guard on `Pred` alone never sees it). Equality on a
predicate is refused too (it used to report two different columns equal); query
identity is `canonical_key()`.

### Text matching is a ladder, under `.str`

`caseless`, `unaccented` and `folded` came from #222 and #255:

```python
q.code.name["en"].str.contains("region")              # exact
q.code.name["en"].str.caseless.contains("region")     # ignore case
q.code.name["en"].str.unaccented.contains("Montreal") # + diacritics: finds "Montréal"
q.code.name["en"].str.folded.contains("Trois Rivieres")  # + case, punctuation
q.code.id.str.caseless == "cl_freq"                   # == only at this rung
```

Three things are kept exactly:

- **`caseless` carries `==` and `!=`; `unaccented` and `folded` do not.** The
  differential oracles encode it: both fold case for `contains` /
  `starts_with` / `ends_with` while `==` stays case-sensitive (#222).
- **`caseless` applies to ids, not only labels** — a caller typing an id in the
  wrong case is where #222 begins, and SDMX `IDType` treats `F` and `f` as
  distinct codes that can coexist. `unaccented` and `folded` exist on labels
  only.
- **They are properties, not methods**, matching polars' namespace convention.

`LIKE` metacharacters in a needle are escaped (#222): `"50%"` matches the text,
not a pattern.

Any namespace named after a builtin has this hazard: a `str` property shadows the builtin inside the class body, so
`def f(self, x: list[str])` annotates `list[<property>]`. The fix is
`builtins.str` (`evidence/query-language.md`, "The `.str` namespace shadows the
builtin").

---

## 9. Observations

`reg.obs(flow)` returns an `ObsQuery` — the same `Query` machinery
rooted at one dataflow's observations, on both `LocalRegistry` (via a
`DataflowBinding` that says which table holds them) and `RestRegistry` (fetched
as SDMX-CSV into a synthesised binding).

**The two planes share operators and the plan; they name columns differently.**
Structural column names are fixed and declarable, so `q.component.id` is an
attribute and type-checks. Observation column names come from the DSD at
runtime, so they are named by string — `obs.col("REF_AREA")` — and typed
`Untyped`: comparisons, orderings, aggregates and the `.str` rungs work, and a
typo is a `KeyError` listing the components that exist. `obs.text(id)` and
`obs.num(id)` (#511) return a typed `Str` / `Num` instead, checked against
what the DSD or the store says the component holds. The accessor is built
per binding, so the query carries it: `obs.col(...)`, `obs.components`.

`col()` rather than `obs["REF_AREA"]`, which was the first cut: `Query.__getitem__`
already means *look up a row by id* for a string, and overriding it on one root
would give one spelling two meanings depending on the root. An attribute form,
`obs.REF_AREA`, was rejected for looking statically checked without being so.

Everything else is the structural surface: `filter`, `qualify`, `group_by`,
`select`, `order_by`, the terminals. Labels are the one observation-specific
terminal option — `obs.frame(labels="en")` adds `<COMPONENT>_label_en` columns
for coded components, resolved in one statement against the stored codelists,
with the language always named. `filter_components(...)` applies a parsed SDMX
REST component filter, for code serving SDMX REST.

The design reasoning for the data plane — what consumers' hand-written SQL
needed, the binding, what pushes into a REST data key — is in
[`data-plane-query.md`](data-plane-query.md) and
[`data-plane-pushdown.md`](data-plane-pushdown.md).

---

## 10. The escape hatch

**The design keeps raw SQL.** The ban in `query-composition.md` is
narrow and still correct: *the structural DSL* must not accept SQL text, because
its safety property is that a caller supplies field references and literals and
nothing else. That is an argument against merging the two representations, not
against a separate, clearly-marked escape. The escape hatch also shows what to
build next: each use is evidence of a need the language does not meet.

```python
reg.sql("SELECT agency_id, count(*) FROM sdmx.artefact GROUP BY 1").collect()
reg.sql("SELECT code_id FROM sdmx.code WHERE code_id LIKE $p", p="E%").collect()
sl.schema.code.code_id                    # 'sdmx.code.code_id' — checked against the DDL
```

The rules it carries:

- **A different method on a different object.** `reg.sql` is visibly not
  `filter` at the call site, which is the property the ban protects.
- **Values are bound, never interpolated.** `$name` parameters are rewritten to
  `?` and checked against DuckDB's own parse. `reg.sql(...)` is lazy; `.collect()`
  runs it.
- **It is a terminal.** It briefly also spliced into `is_in` as a `SqlRelation`;
  that was withdrawn. A raw statement carries no row kind to check against the
  column it lands beside (the failure is an empty result, not an error); it
  cannot be pushed to a non-DuckDB backend, so one splice would pin a whole
  query to DuckDB; and a painless crossing hides the gap the hatch exists to
  report. `RawSql` does not satisfy `SqlRelation`, and `is_in` names both ways
  across: materialise (`reg.sql(...).collect()["col"]`), or use a query, which
  composes lazily.
- **Schema constants** (`sl.schema`) reflect the DDL, so a stale table or column
  is an `AttributeError` at the point of use listing what is there, rather than
  a SQL error naming neither the call site nor the fix.

`SqlRelation` itself is untouched, because it was never the raw-SQL seam: a
`Query` / `Select` satisfies it, and so do external producers (statcatlib's query
builders). Both are compiled relations; raw SQL text has no compiled plan.

---

## 11. The row kind's purpose and cost

`RowKind` is a pure phantom — a never-instantiated marker per relation
(`CodeRow`, `ComponentRow`, …). Its only job is to make a predicate built for one
root un-handable to another, and it is the price of **module-level accessors**.
Every peer binds a column to a relation handle — ibis `t = con.table(...)` then
`t.col`; SQLAlchemy `User.name` — and a free-floating predicate has no handle to
carry its relation, so the relation rides in the type instead. The alternative,
binding the accessor to the query (`c = reg.components(); c.filter(c.dsd.agency
== ...)`), removes the phantom and costs standalone predicates, which costs
helper composition.

Before #306 the row kind was a Protocol with behaviour (`column()`, `emit()`,
`urn_column`); the query model moved that onto the accessor, which already names
its table and columns, leaving the bare marker. Verified before the change that
nothing was lost: the check comes from the **parameter**, not the Protocol.
Dropping the parameter would be a different proposition — `Pred[R]` becoming
`Pred` would let a cross-root predicate type-check and emit a `WHERE` against a
table absent from the `FROM`.

**What it costs** is that a shared-surface predicate cannot be written once and
applied to two roots. A root-generic helper takes the accessor as an argument:

```python
def scope_to_agency(query, root, agency):
    return query.filter(root.agency == agency)

scope_to_agency(reg.query(q.dataflow), q.dataflow, "ESTAT")
scope_to_agency(reg.query(q.codelist), q.codelist, "ESTAT")
```

That extra parameter is the price, and annotating such a helper is harder:
the shared maintainable accessor class is private, so `root` has no public type
to name, and an unannotated helper is checked only at runtime, where `filter`
refuses a predicate from a mismatched root by its origin, including two
maintainable roots that share a table (§2.5). A `q.cols` on the query was
considered and not built — a second spelling for every column reference,
drifting toward the bound-accessor model.

**`Query[R, A]` carries a second parameter**, `A`, for what the object-building
terminals return — `reg.query(q.dataflow).collect()` is `list[Dataflow]`. It
comes off the root accessor (`Cols[R, A]`) and is invariant, which is why set
operations take `Query[R, A]`.

---

## 12. Risks carried

The design was attacked deliberately once it looked settled
(`evidence/query-language.md`, "Hostile-review measurements"). One objection
changed the shape — the same-child conjunction, answered by `.any(*preds)`
(§3.4). These risks remain open:

- **Context-dependent meaning is structural.** One expression is `EXISTS` in a
  filter, a correlated aggregate in a projection, a `GROUP BY` aggregate in
  `agg`, and a window after `.over()`. Deliberate (§7.5), and the receiving
  clause is always visible — but it is the hazard `query-composition.md` names.
- **Predicates cannot be reused across roots** (§11). A shared predicate library
  is impossible by construction.
- **Correlated subqueries are linear in fan-out columns.** Correct always; cost
  grows with the number of fan-out columns projected. Shared-prefix
  deduplication (§3.2) is what bounds it, and `GROUP BY` would beat it where row
  count may change.
- **Pushdown correctness is safety-critical.** Refuse-rather-than-fetch is only
  as good as the capability declaration; a permissive error silently restores
  the 800-resolution problem. `query-pushdown.md` §8 lists the gates.
- **Self-joins have no general spelling.** There is one module singleton per
  relation, so two rows of one relation cannot be bound at once (§13). The
  common cases are aggregates in disguise — "a DSD with two dimensions sharing a
  concept" is a group of components with `len() > 1` — and the cookbook works
  them.

---

## 13. Open questions

Questions the implementation answered are stated as decisions in the sections
above. These remain open.

1. **Column-to-column comparison.** `q.code.id == q.code.parent.id` and
   `q.component.codelist.id != q.component.concept.codelist.id` are refused by
   the literal check, so "a component overriding its concept's representation"
   and "a code sharing its parent's label" have no spelling. Comparing two
   aggregates (`count() > n_unique()`), or an aggregate with a column, raises
   `TypeError` naming the workaround: select both and compare them in polars
   (#511). Whether to admit a column operand, and with what null semantics, is
   open; it meets item 4.
2. **Self-joins.** Two bindings of one relation at once (a DSD with two
   dimensions sharing a concept, answered here only via grouping). A
   higher-order binder — `dimensions.any(lambda a: a.dsd.dimensions.any(lambda
   b: ...))` — was prototyped and type-checked, and is not built; it would
   change `.any()`'s signature and needs both edge directions declared, which
   they now are.
3. **Cross-schema joins over caller tables.** A caller's own relation can filter
   ours through `is_in` with any one-column `SqlRelation`, or through
   `reg.sql`. A caller declaring an accessor with an edge *into* our graph — so
   their dataset rows could dot through to `code.name["en"]` — was prototyped
   and type-checked, and is not public: the column and edge factories are not
   exported, and `EDGES` is a closed table. Reverse edges (ours to theirs) are
   further off still.
4. **Null-safe equality as an operator.** `every` negates its predicates with
   `IS NOT TRUE` internally (§3.5); callers comparing nullable values hit
   three-valued logic with no opt-out beyond writing the `is_null()` arm
   themselves (§3.6).
5. **Any-language label search.** `name["en"]` picks one language; "matches in
   any language", the natural behaviour for a search box, needs `map_values` and
   has no spelling; the rule that there is no default language makes it a
   common need.
6. **Regex matching.** Facets carry `repr_pattern`; matching ids against one, or
   searching by pattern, has no operator.
7. **Non-equi and range joins.** "Artefacts whose validity overlaps this one" is
   a range predicate between two relations; every declared edge is an equi-join.
8. **As-of queries across relations.** `valid_from` / `valid_to` filter one
   relation; "the catalogue as it stood on date X", consistently across several,
   is a different thing.
9. **Value-column coverage.** Reference-shaped columns are guarded — each is an
   edge or carries a written reason. Plain value columns are not: an accessor
   can omit a stored column and nothing fails. Report-only, or a ratchet like
   `tools/count_any.py`?
10. **Federation.** `FederatedRegistry` routes by agency, which is also §5 of
    `query-pushdown.md`'s bounding constraint and its natural materialisation
    unit; per-agency pushdown across registries is not prototyped
    ([`query-pushdown.md`](query-pushdown.md) §14).
11. **Struct access and `PIVOT`.** Reading back into a `list(struct)` column
    from an earlier stage has no spelling; `PIVOT` is a reshaping of a
    materialised frame, which polars does in one call — probably left there.
12. **Whether the `sl.q.` rule can be checked.** It holds at every call site
    that writes the prefix; nothing stops `c = sl.q.component`. Probably
    acceptable, as `pl.col` aliasing is.

---

## 14. Superseded and never built

Earlier versions of this page, the examples page and the cookbook presented
these as designed or answered. None exists; the reasoning behind the shipped
alternative is in the sections cited.

| was proposed | what exists instead |
| --- | --- |
| named roots — `reg.dataflows()`, `reg.components()`, `reg.codes()` | `reg.query(q.dataflow)` — one entry, the accessor is the witness (§5) |
| four accessors `sl.code` / `sl.artefact` / `sl.node` / `sl.meta`, `FrameQuery` | `sl.q.<relation>`, `Query`, `Select` |
| form A — `join(edge)` then bare columns | paths in the reference (form B); A needs `aliased()` the moment two routes are live, and lets a helper's bare predicate silently mean another join — [`query-language-explorations.md`](https://github.com/StatCat-Suite/sdmxlib/blob/main/history/design/query-language-explorations.md) |
| `Many` with verbs only (`.any(lambda c: ...)`, HOAS binders) | dot-through plus `.any(*preds)` / `.every(*preds)` on `FanOut` (§3.4) |
| a postfix universal `(pred).every()` | `FanOut.every(*preds)` (§3.5) |
| `.hier` (`descendants_of`, `narrower_transitive` on a column) | the four tree edges, plus the lazy artefact's named verbs (§2.7) |
| `.ann["NOTE"]` | `annotations` is a to-many edge like any other |
| `fill_null`, `sl.when(...).then(...).otherwise(...)` | not built; coalesce in polars after `collect()` |
| `q.obs[...]` / `q.obs.REF_AREA`, `flow.data()` | `reg.obs(flow)` and `obs.col(...)` (§9) |
| `.labelled(lang=)` on an observation query | `obs.frame(labels="en")` (§9) |
| a `KeyedQuery` split | not built; `obs.text(...)` / `obs.num(...)` were built (#511) and give typed columns |
| `cl.codes["CA"]` as lazy refinement | it looks up and returns the row (§5) |
| public `sl.col` / `sl.edge` / `sl.CodeCols` for caller accessors | the column and edge factories (`col`, `edge`, `many`) are not public, and no `CodeCols` exists (§13, item 3); the types a caller annotates with — `sl.Col`, `sl.Cols`, `sl.Str`, `sl.Num`, `sl.Untyped`, `sl.Pred`, `sl.RowKind` — are exported |
| `FederatedCatalog.from_parts(...).query(...)` | `RestRegistry.query` with `store=`; federation is open (§13, item 10) |
| `list()` of separate calls, aligned "incidentally" | `list(*columns)` returns structs (§6) |

Rejected branches with their full arguments — bare and contextual paths, a
parent-level `.any()`, the `Fan[R]` marker, a postfix `.every()` — are in
[`query-language-explorations.md`](https://github.com/StatCat-Suite/sdmxlib/blob/main/history/design/query-language-explorations.md).
The branch that was adopted, a strict path with a child-rooted inner
predicate, is §2.9.
