# Querying observations without writing SQL

The structural side of this library stopped accepting SQL text. The data plane
never did, and this is the design for closing that — #297's option C, written
after options A and B were tried and refuted rather than reasoned about.

> **Status: landed.** Option C3 is built. A dataflow's observations are a root
> of the same `Query` as every structural relation: `reg.obs(flow)`
> on a `LocalRegistry` or `RestRegistry`, or
> `DuckDBDataStore.obs(binding)`, returns an `ObsQuery`
> (`src/sdmxlib/query/observations.py`), and `obs.col("REF_AREA")` names a
> component. The SQL-text API is gone (#329); the escape hatch is
> `reg.sql(text, **params)`. §7 gives the status step by step. Not built:
> the projecting join to a caller's relation (§3.2), arithmetic over
> aggregates (§3.1) and the `Query`/`KeyedQuery` split (§6.2). The typed
> column accessors (§6.3) landed in #511.
>
> ```python
> obs = reg.obs(cpi)
> c = obs.col
> obs.filter(c("REF_AREA").is_in(["FR", "DE"]), c("TIME_PERIOD") >= "2024").frame()
> obs.group_by(c("REF_AREA")).agg(total=c("OBS_VALUE").sum(), m=c("OBS_VALUE").mean())
> obs.qualify(c("OBS_VALUE").row_number(descending=True).over(c("REF_AREA")).le(1))
> ```
>
> The sections below keep the design argument as it was made; notes mark
> where building it changed the answer.

## 1. Why this is open

*(Written when it was. The API described here was removed in #329.)*

`DataStore.query` took `where: list[str]`, `columns: list[str]` and
`order_by: str`, and spliced all three into a statement as text.
`ComponentFilter` takes *values* but its `to_where_clauses` returns
*fragments*. #257 looked at this
and chose to document rather than validate, on a stated premise: consumers are
first-party and compose these from constants in application code.

#297 made measuring that premise a prerequisite, and the downstream consumers
were available to read, so it was directly answerable.

| | |
| --- | --- |
| call sites passing `where` / `order_by` / `columns` **to the library** | **1** |
| of those, built from user input | **1** |

The site is a downstream SDMX REST service's HTTP data route: `where` comes
from `c[DIM]=op:value` query parameters, `order_by` from `sort=`. The premise
was false.

**And the second half of that finding matters more than the first.** That
consumer had to build option C itself, in application code: a fixed operator
table, component ids resolved through `binding.dimension_column_map` with a 400
on unknown, values bound as `?`, LIKE metacharacters escaped. It then
*bypasses* the library entirely for anything needing projection or
aggregation. Milestone %15's rule — every bypass is a specification — lands
here.

### The count above understates it

That measurement counts calls *into* `DataStore`. A second consumer, an LLM
agent built on sdmxlib, makes none: it carries its own query builder with the
same `where` / `columns` / `order_by` / `limit` parameters, rather than adopt
the library's.

So there are two bypass shapes, not one:

* **use the API and work around it** — pass text in, and hand-write SQL for
  everything the text cannot reach;
* **replace the API** — reimplement the builder rather than adopt it.

The second is the more damning, because nothing in the library's own surface
records that it happened. A design that only fences or types `DataStore.query`
would leave that consumer exactly where it is.

**A consumer that builds queries from user-supplied filter values needs
parameterised predicates.** An agent turning natural language into these calls
makes every value user input by construction, and with text as the only
interface on offer, its defence has to be an allow-list it writes itself.

## 2. What option B could not be

Both stronger fences were built, run against the suite, and removed. They were
kept as tests in `tests/unit/test_data_plane_fence.py`, asserting the
compositions that must keep working, until #329 removed the text API they
guarded and the file with it (a35ab7b).

**A table allow-list** — a fragment may name only the binding's own tables —
breaks relation-valued filters, which read `sdmx.code` on purpose, and
`Session.value_set`, whose whole job is splicing a caller's own table in by
name.

**A column allow-list**, which is what #297 proposed, rejects
`columns=["a AS x"], order_by="x"`. A projected alias is a legitimate sort key
and is a column of no table.

Both failures say one thing: **composition across tables and expressions is the
feature**, so no property of the text separates use from abuse. What shipped is
the only check that survives — the assembled statement must be one statement,
judged by DuckDB's own parser — and it closes statement injection and nothing
else.

That is the argument for C. Not a safer text API: no text.

## 3. What the vocabulary has to cover

Read off both consumers' builders rather than imagined. Each row is something
one of them writes as SQL today because the library offers no other way.
**A** is a downstream SDMX REST service, **B** an LLM agent built on sdmxlib.

The last column is what the structural language had when this was written;
*as built* notes say what the observation root offers now.

| shape | who | as written today | structural language already has |
| --- | --- | --- | --- |
| `=` `!=` `<` `<=` `>` `>=` | A B | an operator table; `OBS_VALUE >= ?` | yes — comparison operators |
| contains / starts / ends | A B | a LIKE-pattern helper; `REF_AREA LIKE ?` | yes — `.str.contains`, `.str.starts_with`, `.str.ends_with` |
| set membership | A B | `IN (?, ?)` | yes — `.is_in` |
| membership in a *relation* | A | `SqlRelation` subquery | yes — `.is_in(relation)` |
| null-aware exclusion | B | `OBS_STATUS IS NULL OR OBS_STATUS NOT IN (…)` | yes — null-safe comparison |
| time range | A B | `f"{time_col} >= ?"` | yes — comparisons |
| projection with alias | A B | `f"{col} AS {alias}"` | yes — `select(alias=col)` |
| computed group key | B | `substr(REF_AREA, 10, 2)` as a GROUP BY key | partly — computed columns exist |
| GROUP BY | A B | hand-written | yes — `group_by` |
| `sum` / `avg` / `min` / `max` | B | `f"{agg_func}(OBS_VALUE)"` | **no** — only `count`, `n_unique`, `list`. *As built: `sum`, `mean`, `min`, `max` (!337).* |
| distinct count | B | `COUNT(DISTINCT REF_AREA)` | yes — `n_unique` |
| `any_value(x ORDER BY y)` | A B | hand-written | no — ordered aggregate |
| per-group top-N | A B | `ROW_NUMBER() OVER (PARTITION BY … ORDER BY …)` then `<= ?` | **yes** — `qualify` + `.over()`; the bound is `.le(n)` |
| sort with `NULLS LAST` + tiebreak | B | `ORDER BY v DESC NULLS LAST, REF_AREA ASC` | yes — window ORDER BY emits NULLS LAST |
| multi-column sort with direction | A | a sort-string parser | yes — `order_by` |
| offset / limit | A B | hand-written | yes — `limit`, `offset`, `page` |
| **derived value across a pivot** | B | `MAX(CASE WHEN d = ? …) AS a, … , a/b AS derived` | no — see §3.1. *Still no.* |
| **join to a caller's relation** | B | `JOIN rollup r ON o.REF_AREA = r.REF_AREA` | no — see §3.2. *Still no.* |
| `printf(DECIMALS, value)` | A | hand-written | no — presentation |
| wide pivot `MAX(CASE WHEN …)` | A | hand-written | no — polars' job |

Most of it is already compiled by `sdmxlib.query`. That is the finding that
should drive the design: **the data plane does not need a new query language.
It needs the existing one rooted somewhere else.**

Three rows are honest exclusions. `printf` is value formatting for a CSV
renderer — presentation, not query. `PIVOT` as a *reshaping* the query
language design already ruled out, on the grounds that polars does it in one call: the rule
that killed server-side `explode`.

Two rows are real gaps that no amount of re-rooting closes, and both come from
consumer B. They are the reason this section was rewritten after reading a
second consumer.

### 3.1 Numeric aggregates, and derived values over them

The structural language has `count`, `n_unique` and `list` — enough for
counting artefacts, useless for measuring observations. `sum`, `avg`, `min`,
`max` are the point of a data plane and are not there.

On top of that, consumer B pivots two measures into `value_a` / `value_b` and
then computes a **third column from them** — a ratio, difference or share —
sorts on it, and limits. Arithmetic over columns exists in the expression
language (`tests/unit/test_query_arithmetic.py`); arithmetic over *aggregates*,
projected as a named column and then used as a sort key, is what this needs.
%15 lists exactly this shape as one it must stay sized for: `SUM(v*w)/SUM(w)`,
percent-of-total.

> **As built.** The four numeric aggregates landed (!337, named for polars:
> `mean`, not `avg`). Arithmetic over *aggregates* did not:
> `c("OBS_VALUE").sum() / c("OBS_VALUE").count()` raises `TypeError`. What
> does work is arithmetic between a row column and a window — a row's share of
> the total is `select(s=c("OBS_VALUE") / c("OBS_VALUE").sum().over())` — since
> an observation column offers `-`, `*` and `/` (§6.3). The derived value
> across a pivot is still done in polars.

### 3.2 Joining a caller's relation, projecting from it

Consumer B rolls geographies up a hierarchy and groups by the result. It gets
the rollup *from this library* — `h.inherit_along_broader(seeds)`, the #274
answer — and then has to bridge it into SQL by hand: build a polars frame,
`connection.register()` it as a view, `JOIN` the observations on `REF_AREA`,
`GROUP BY` the rollup's group key, and `unregister` in a `finally`.

**`is_in(relation)` cannot express this.** A relation-valued filter restricts
rows; this one *contributes a column* — the group key, which exists nowhere in
the observations. The query language design named
caller-table joins as the one consumer shape the language cannot reach and
proposed `Session.value_set` plus `is_in` as the likely spelling. That is not enough:
the mechanism is right, the spelling has to project.

This is the most valuable single find of the two surveys, because the library
supplies both halves — the hierarchy rollup and the observations — and nothing
to put them together.

## 4. Three ways to spell it

**C1 — its own vocabulary.** `ObsFilter` / `ObsProjection` / `ObsOrder`
alongside `ComponentFilter`, taking values and rendering SQL internally.
Smallest blast radius, no generics work, and a second expression language to
maintain with its own operator coverage, its own null semantics, its own bugs.
`ComponentFilter` is already the first quarter of this and has needed `[]`-vs-
`None` semantics (#256) argued from first principles once.

**C2 — a dataflow relation in the path language.** `q.obs.GEO == "CA"`.
Impossible as written: `q.code.id` is a static accessor generated from the DDL,
and a dataflow's columns are known only at runtime from its binding.

**C3 — the same expression language, a dynamically bound root.** The
expression types in `query/_expr.py` (`Col`, `Pred`, `Agg`, `Win`) do not know
where their columns come from; `query/_compile.py` turns them into SQL against
a scope. Bind a root to a `DataflowBinding` instead of to the edge graph, and
the whole language applies:

```python
# candidate spelling at the time, not the API — see "As built" below
obs = reg.data(flow_urn)                     # columns from the binding
(obs.query()
    .filter(obs["GEO"].is_in(reg.query(q.code).select(q.code.id)))
    .filter(obs["TIME_PERIOD"] >= "2020")
    .qualify(obs["TIME_PERIOD"].row_number().over(obs["GEO"]) <= 10)
    .select(geo=obs["GEO"], v=obs["OBS_VALUE"])
    .order_by(obs["TIME_PERIOD"])
    .collect())
```

`obs["GEO"]` resolves through `binding.dimension_column_map` and raises on an
unknown id — which is exactly the 400 the consumer hand-writes, moved to where
the map already lives.

**As built**, the query *is* the accessor, and a column is `obs.col(id)`
rather than `obs[id]` — `Query.__getitem__` already means "look a row up by
id", so the same spelling would have meant two things by root (on an
`ObsQuery` it raises, saying observations have no id):

```python
obs = reg.obs(flow)                  # an ObsQuery
c = obs.col
(obs
    .filter(c("GEO").is_in(reg.query(sl.q.code).filter(sl.q.code.codelist.id == "CL_GEO").select(sl.q.code.id)))
    .filter(c("TIME_PERIOD") >= "2020")
    .qualify(c("TIME_PERIOD").row_number().over(c("GEO")).le(10))
    .select(geo=c("GEO"), v=c("OBS_VALUE"))
    .order_by("geo")
    .collect())
```

`obs.col("GEO")` raises `KeyError` on an unknown id, listing the valid ones.
`obs.components` lists them up front.

### Checked against the two hardest real queries

Not "does it look nice" — does it reach the shapes §3.1 and §3.2 named.
(Written in the candidate spelling; each is followed by what was built.) Both of
these are consumer B's queries, written today as CTEs against a raw connection.

**Per-group top-N with a stable tiebreak.** Today: a `WITH _ranked AS (SELECT
*, ROW_NUMBER() OVER (…) …)` wrapper, `_rn <= ?`, then an outer `ORDER BY` and
`LIMIT`.

```python
rank = obs["OBS_VALUE"].row_number(descending=True).over(obs["GEO_LEVEL"])
(obs.query()
    .filter(obs["CHARACTERISTIC"] == characteristic)
    .qualify(rank <= n)
    .order_by(obs["GEO_LEVEL"], obs["OBS_VALUE"].desc(), obs["REF_AREA"])
    .limit(limit))
```

The tiebreak is the open detail: today's SQL orders the *window* by
`OBS_VALUE DESC NULLS LAST, REF_AREA ASC`, and `.over()` takes one sort key.
A window with a secondary sort key is a small extension, and it is load-bearing
— without it, equal values flip between runs.

*As built:* the spelling is
`c("OBS_VALUE").row_number(descending=True).over(c("GEO_LEVEL")).le(n)` — a
window comparison is `.le` / `.lt` / `.ge` / `.gt` / `==`, giving a `WinPred`
that only `qualify` takes. The secondary window sort key is **still not
built**: a window orders by its one source column.

**Rollup, group, derive.** Today: build a polars frame, `register` it, `JOIN`,
`GROUP BY`, `unregister` in a `finally`.

```python
rollup = session.relation(ancestor_map, on="REF_AREA", project="group_dguid")
(obs.query()
    .join(rollup)                                  # §3.2 — projects a column
    .group_by(rollup["group_dguid"])
    .select(
        area=rollup["group_dguid"],
        total=obs["OBS_VALUE"].sum(),               # §3.1 — numeric aggregate
        share=obs["OBS_VALUE"].sum() / obs["OBS_VALUE"].sum().over(),
    )
    .order_by(sl.desc("total"))
    .limit(limit))
```

`share` is the percent-of-total shape %15 asks for, and it is why §3.1 matters
beyond `sum`: an aggregate has to be composable as an *expression*, not just
projectable as a column.

*As built:* not expressible. There is no `join`, and an aggregate does not
compose arithmetically (§3.1). This query still runs as hand-written SQL
(`reg.sql`) or in polars.

## 5. Is C3 actually reachable? Four couplings

Run, not assumed. All four were fixed — §7 lists where.

**The join planner does not need the table declared.** A data-plane root has no
hops — every path is a bare column — and `plan_joins` on an undeclared table
with a single empty path returns a plan naming just the root:

```
plan_joins("memory.data.obs", [()], root_alias="s0")
  -> JoinPlan(root_alias='s0', root_table='memory.data.obs', joins=(), …)
```

So the edge graph, which is the structural-specific part, is simply not
consulted. `resolve_path("memory.data.obs", ["GEO"])` does raise — correctly,
because `GEO` is a column, not a hop.

**One hard coupling, and it is small.** `JoinPlan.to_sql()` renders
`f"sdmx.{self.root_table} {self.root_alias}"` — the schema is a literal. A
dataflow's table is `memory.data.obs` or a DuckLake catalog. The root's
qualification has to become a property of the root rather than a constant.
Structural *hops* can keep the literal: every declared edge targets an `sdmx`
table.

**The sidecar join has no representation.** `Join.to_sql()` emits `… ON …`;
the series-attributes sidecar is a `LEFT JOIN … USING (…)` whose keys
`SchemaInspector.plan` already derives. Either `Join` grows a USING form, or
the root carries the pre-rendered join. The first is cleaner and is the only
place the two planners would need to agree.

> **And once it did, it was emitted unconditionally — #388.** A hop join is
> planned from the paths a predicate names, so it exists only where it is
> needed. A root join has no path, so nothing decided whether to emit it. On
> the 2021 Census Profile bundle that made `count()` over 1.14 B observations **2598 ms against
> 1 ms**, for a join the statement never selected from; narrowing the
> projection did not help, because the join was not a projection decision.
>
> DuckDB keeps it correctly: a `LEFT JOIN` is row-preserving, and so elidable,
> only if the right side is unique on the join key, and a DuckLake table
> declares no such constraint. We know what it does not, so `_Scope` prunes
> the join when nothing in the statement reads a column on it. **Recorded, not
> derived** — `_Scope._use` is the only writer of the used-table set and the
> only way an alias reaches the SQL, so the set is complete by construction,
> where a static pass over the clauses would have to enumerate them and could
> fall behind a new one. The cost is an ordering obligation: the `FROM` is
> rendered last, and a reference compiled after it raises rather than naming
> an alias nothing joined.
>
> **The other direction does not work, and this is the measurement.** Declaring
> the key so DuckDB elides the join itself would also help queries that *do*
> read a sidecar column and then aggregate. It does not: a `UNIQUE INDEX` on
> the sidecar's key left the hash join in the plan and 2.6 s on the clock, and
> a `PRIMARY KEY` is not even expressible — `ALT_GEO_CODE` is nullable on the
> real bundle. Whatever closes the referenced case, it is not a constraint.

**A root's identity was read from its class**, which this survey missed and
sizing step 4 found: sixteen sites read `type(root).__table__` and friends.
Correct for every structural accessor, since each is a singleton whose relation
is a property of its type — and wrong for a dataflow, whose table comes from a
binding, so two roots of one class name two tables. Fixed in !338 by reading
the instance, which costs the singletons nothing.

That a fourth coupling appeared only when the code was written is the argument
for the staging below: keep each slice thin enough that the next one is still
cheap to change.

## 6. Row kinds — settled

The path language's safety comes from a phantom row-kind parameter: a
`Pred[CodeRow]` cannot be handed to a query rooted at `q.dataflow`, and the
checker says so at the call site. A dataflow's columns are discovered at
runtime, so there is no per-dataflow type to be the row kind.

**Decision: one `ObsRow` for every dataflow.** It catches the error that
matters — mixing observation predicates with structural ones — and permits the
rarer one, predicates from two different dataflows, which the binder still
catches at runtime because it resolves every column against a binding.

Rejected: `ObsRow[Flow]` keyed on a per-dataflow marker type, which buys static
separation between flows at the cost of an annotation nobody writing a script
will write; and runtime-only checking, which is weaker than the structural side
and inconsistent with it.

## 6.1 The key is the dimensions

`__key__` feeds keyset paging and the resolver. Observations have no surrogate
key — but they are not unkeyed, because the SDMX information model says an
observation is *identified by its dimension values*. So `__key__` is
`binding.dimension_column_map` in DSD order, and paging works for the same
reason it works structurally rather than by special case.

## 6.2 `ids()` and `urns()` are absent, not raising

> **Not built.** There is no `KeyedQuery`; `ObsQuery` is a plain
> `Query[ObsRow]` and still carries both terminals. Today `urns()` (and
> `collect()`, `page()`) raise `TypeError` — observations have no URN — and
> `ids()` is not refused at all: it compiles against a `resource_id` column
> the table does not have and fails in DuckDB with a binder error. That is the
> lie this section argues against, and `observations.py` says so in its own
> docstring.

An observation has no single id column, so those two terminals have nothing to
return. The first instinct was to raise, following `Query`'s existing
precedent: with no resolver, `.objects` raises `TypeError` naming the
alternative.

**That precedent does not transfer, and the difference is worth stating.**
Whether a resolver exists is *data-dependent* — the registry supplies one per
table, and the same root may have one in one process and not another. Whether a
root has an id is a property of the root's **type**, known statically. A
capability that is statically absent should be statically absent.

This codebase already argues the point, in `NOT_EDGES`: an edge was declined
there because it would "still *work* … while `.agency.name['en']` returned NULL
almost always: **an affordance that lies is worse than a plain column**". An
`ids()` that type-checks and always raises is the same lie.

**It is expressible, and cheaply.** Split the terminals: `Query[R]` carries
what every root supports — `filter`, `select`, `count`, `exists`, `collect`,
`frame` — and `KeyedQuery[R](Query[R])` adds `ids`, `urns`, `get`, `one`.
Every refining method returns `Self`, so a `KeyedQuery` stays one through the
whole chain; that is what makes the split cost two class statements rather than
a parallel API. `Registry.query` overloads on the root: an identified accessor
returns the keyed query, an observation root the plain one.

The cost is a matching split on the accessor side, since the overload needs a
static way to tell an identified root from one without an id. Every structural
accessor has `__id__` today, so they all land on the keyed side and none of
them changes.

## 6.3 Column types come from the DSD, not from `DESCRIBE`

> **Built (#511).** `obs.text(id)` / `obs.num(id)` are the typed accessors
> below, checked against the DSD — and, for a component it leaves untyped, the
> stored column's type, which §6.3 set aside as a *source* of types but which
> is the only answer for that "unknown" arm. `obs.col(id)` stays the
> permissive arm, now refusing a literal of the wrong kind as the predicate is
> built. Before #511, the permissive arm was all there was: `obs.col("GEO")` returns `Untyped[ObsRow]`
> (`query/_expr.py`), which offers what means one thing without knowing the
> type — comparison and ordering, `is_in`, `-` / `*` / `/`, and `.str`
> (`contains`, `starts_with`, `ends_with`, `caseless`) where the caller says
> to read it as text. `+` is absent, because it would be addition on a number
> and concatenation on text. `obs.col("REF_AREA") * 2` type-checks and fails
> at DuckDB; the DSD-checked accessors below are how that would be caught.

A column has to be *some* column class, and the language's classes
differ in what they offer — `Str` has `contains`, `Num` has arithmetic.

The first thought was `SchemaInspector`: it can `DESCRIBE` the table and knows
the physical types. **The information model is the better source, and it is
already modelled.** A component's `representation` is either a `Ref[Codelist]`
— enumerated — or a `Facet` whose `type` is one of `DataType`'s 39 members,
which is precisely the "how is this encoded" question. Measured, on a DSD built
three ways:

```
GEO            enumerated by CL_GEO
TIME_PERIOD    facet ObservationalTimePeriod   (inherited from the concept)
BARE           no representation at all
OBS_VALUE      facet Double
```

Three things follow.

**The IM's inheritance already works.** `TIME_PERIOD` declares no
representation of its own; `Component.__attrs_post_init__` falls back to the
concept's `core_representation`, which is what the model specifies. Nothing new
is needed to get it.

**`DESCRIBE` could not have answered.** A parquet column of codes is `VARCHAR`
whatever it means; only the DSD says *enumerated by CL_GEO*. That distinction
is what would later let an enumerated dimension reach its codelist — the
structural-to-data join both consumers hand-roll — so taking the physical type
would have closed a door for no gain.

**The DSD does not always answer.** `BARE` has no representation, and a
component whose concept is unresolved has none either. So the mapping needs an
"unknown" arm, and it should be the permissive one rather than an error: a
dataflow the library cannot type is still a dataflow a caller can query.

### The static half

Runtime typing is settled by the above; the *static* type of
`obs.col("GEO")` is one signature and cannot vary by key. Two spellings, and
they compose:

* `obs.col("GEO")` → `Untyped[ObsRow]`, the permissive form — **built**, as
  described in the note above. (Drafted as `obs["GEO"]` → `Col[ObsRow]`;
  `Col` alone carries only `==`, which is why #347 and #413 moved it to
  `Untyped`.)
* `obs.text("GEO")` / `obs.num("OBS_VALUE")` → `Str[ObsRow]` / `Num[ObsRow]`,
  exact static types, with the DSD **checked** at call time: asking for `num`
  on a codelist-enumerated dimension raises, naming what the DSD says it is.
  **Built in #511.**

The caller states the expectation and the model verifies it, which is the same
bargain `type[T]` witnesses make elsewhere in this library — recover the type,
do not erase it. The permissive form stays for the dynamic case, where a
component id is itself a variable.

## 7. Recommendation

**C3, staged, with C1's surface as the fallback if the probes go wrong.**

1. ~~**Make the root's qualification a property of the plan**~~ — !335.
2. ~~**A `USING` form for `Join`**~~ — !336.
3. ~~**Numeric aggregates**~~ — `sum`, `mean`, `min`, `max`, !337. `mean`
   rather than `avg`: the set is named for polars throughout. #327 keeps the
   ordered aggregate and the question of whether the compiler should know
   column types.
   ~~3.5~~ **Read a root's identity from the instance** — !338, found while
   sizing step 4.
4. ~~**A binding-backed root**~~ — landed: `Observations` and `ObsQuery` in
   `query/observations.py`, including the series-attributes sidecar as a root
   join (#332). A column is `obs.col("GEO")` → `Untyped[ObsRow]`, empty path,
   resolved through the binding's column maps, raising `KeyError` with the
   valid ids. `__key__` is the dimensions (§6.1); the row kind is one `ObsRow`
   (§6). The typed accessors and the `Query`/`KeyedQuery` split (§6.2, §6.3)
   did follow in no slice yet — **both unbuilt**.
5. ~~**Wire the existing terminals**~~ — landed: `filter`, `select`,
   `group_by`/`agg`, `qualify`, `order_by`, `limit`, `offset`, `frame`,
   `stream`, `count`, `exists`, plus `filter_components` for an SDMX REST
   `ComponentFilter` and `frame(labels=...)` for code labels. The plan said
   *delete nothing*; #329 deleted the text API anyway (§8), and the escape
   hatch became `reg.sql`, a separate terminal.
6. **A projecting join to a caller relation** (§3.2). **Not built.** Last because it is the
   least understood and the only one that needs a spelling nobody has drafted;
   first if consumer B is the one being unblocked, because it is the only
   item on this list that removes its `register`/`JOIN`/`unregister` dance.
7. **Re-measure the bypass count** in *both* consumers. *(No re-measurement
   is recorded in this repository.)* That is the acceptance
   test %15 actually sets, and the only one that says whether this worked.
   Consumer B is the harder target: it has to *adopt* an API it currently
   reimplements, so "no change in its SQL count" is a real possible outcome and
   would mean the vocabulary missed.

Staging 1–3 first is deliberate: all three are changes to structural code with
structural tests already covering them, so they land before any new surface
exists to get wrong.

## 8. What this does not do

**It does not merge the two planes' safety properties by accident.** #273
rejected sharing a representation because the structural DSLs are safe
*precisely* because they have no text-accepting path, and merging would give
them one. What C3 shares is the **expression language**, which accepts no text
either. The text API stays where it is, on the data plane, as an escape hatch —
it does not migrate to `q.`.

**It does not make the data plane's SQL safe from the caller.** A caller who
uses the escape hatch is composing SQL, and the statement fence is all that
guards it. The point of C3 is that nobody has to.

> **Landed in #329.** The escape hatch is now `reg.sql(text, **params)` on a
> `LocalRegistry` (it was `catalog.sql` until `FederatedCatalog` was removed) —
> one complete statement, `$name` binds, checked against DuckDB's own parse.
> `query` / `query_stream` and their `where` / `columns` / `order_by` text are
> gone, and so is the fence that guarded them: with no *assembled* statement
> there is nothing left to assemble injection into, and the caller's own
> statement is checked by the same rule as any other `reg.sql` call. The
> paragraph above still describes the escape hatch; it no longer describes the
> ordinary path, which is `obs()`.
>
> Two things fell out of the deletion rather than being designed:
>
> * **`DataStore` is now a write-side Protocol.** Reading needs a compiler, and
>   a Protocol cannot require one of an arbitrary backend, so reading goes
>   through `DuckDBDataStore.obs` (and `reg.obs(flow)`, which
>   builds the same `ObsQuery`). The second backend did arrive, and as
>   predicted it declares a `Capability`: SDMX REST's data endpoint, whose
>   `ObsQuery` pushes key-dimension predicates into the request
>   (`data-plane-pushdown.md`).
> * **An unprojected result had to learn about root joins.** `to_sql` defaulted
>   to `s0.*`, which is right for a path hop — a different relation's columns
>   are not part of the row — and silently wrong for a sidecar, whose columns
>   are. `_Scope.star` adds each root join's `* EXCLUDE (keys)`; the keys go
>   because `USING` already merged them, and a frame carrying `FREQ` twice is
>   one polars refuses.

**It does not address time semantics.** Half-open period ranges, FREQ-aware
aggregation and vintage comparison are %15 shapes that need SDMX meaning in the
compiler, not just a column vocabulary. They sit on top of this and are out of
scope until the vocabulary exists.

**It does not close consumer B's structural bypasses.** It writes SQL against
`sdmx.metadata_target`, `sdmx.metadata_value`, `sdmx.metadata_set`,
`sdmx.code` and `sdmx.hierarchy_node` directly, and calls `reg.query(q.…)` at
**none**. Every one of those relations exists in the path
language today. Whatever is keeping it out is a separate question from this
document — and worth its own, because a data plane nobody adopts for the same
unexamined reason would be a second miss.
