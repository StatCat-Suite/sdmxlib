# The query language: evidence

Companion to `query-language.md`, which carries the design and the decisions.
This file carries what was **run**: peer surveys, schema measurements,
benchmarks, and the prototype log. Each entry states what it tested and what it
showed, so a decision in the design file can be checked against the thing that
justified it rather than restated.

Two conventions from the parent file are kept. Anything **recalled rather than
run** says so. And a measurement that later turned out to answer the wrong
question is corrected in place rather than deleted, because the correction is
usually the more useful record.

## Peer surveys

### Peer survey: how other libraries name a column

Run rather than recalled; each row was executed and its output inspected.

| library | naming a column | bound to |
| --- | --- | --- |
| ibis | `a = con.table("artefact"); a.agency_id` | a table handle |
| SQLAlchemy | `Dsd.agency` → `InstrumentedAttribute` | a mapped class |
| polars | `pl.col("agency_id")` | nothing — a string |
| Django | `"dsd__agency"` | nothing — a string path |
| pandas / PySpark | `df.col` | the frame |
| **sdmxlib** | `sl.artefact.agency_id` | **a module-level singleton** |

We are the only one. The cause is structural rather than stylistic: predicates
here are built standalone and handed to `.filter()`, so the accessor has
nothing to be bound to. Every other library binds the column to the relation it
came from, which is what lets them check joins and infer scope.

### Peer survey: how a fixed schema resolves join ambiguity

The sharper question, because our schema *is* fixed and theirs mostly is not.

| library | spelling | ambiguity resolved by |
| --- | --- | --- |
| SQLAlchemy | `select(Component).join(Component.dsd)` | the relationship attribute |
| Django | `filter(dsd__agency="ESTAT")` | the FK path |
| GraphQL | nested selection along named fields | impossible — every hop is named |
| Cypher | `MATCH (f)-[:HAS_COMPONENT]->(c)` | the named relationship type |

**Nobody joins tables.** Every one of them names the *edge*. Verified for
SQLAlchemy by constructing the ambiguous case — `dsd` reachable from both
`Component` and `Flow` — where `join(Component.dsd)` and `join(Flow.dsd)`
produce different, unambiguous SQL.

A prototype that joined by *table* and picked the first matching edge was
written and works, but it silently chooses a path when two exist. That is the
one design in the survey with no precedent, and it is why it felt wrong.

### What `in` means elsewhere, measured

`__contains__` deserves its own note, because the survey found something
stronger than "it executes eagerly". Run, not recalled:

| library | `x in relation` | what it means | row-existence idiom |
| --- | --- | --- | --- |
| polars `DataFrame` | supported | **column name** — `"a" in df` True, `1 in df` False | filter + `.height` |
| polars `LazyFrame` | supported, **warns** | **column name**, and says resolving the schema is expensive | `.filter(...).collect()` |
| ibis `Table` | supported | **column name** — `"id" in t` True, `"CA" in t` False | `col.isin([...]).any()` |
| SQLAlchemy `Select` | **not implemented** | `TypeError: argument of type 'Select' is not iterable` | `select(q.exists())` |
| **sdmxlib today** | supported | **row id** | — |

**Not one of the three uses `in` for row membership.** Two use it for the
opposite axis — column names — and the third refuses it. So `"CA" in cl.codes`
is not just eager; it means something a polars or ibis reader would not expect,
and closer to the reverse.

That makes it a fifth instance of the hazard `query-composition.md` names —
borrowing polars' vocabulary without its laws, the seam behind #222, #255, #261
and #292 — caught before shipping rather than after.

`.exists()` is SQLAlchemy's answer and the only one that suits a lazy query.
Repurposing `in` to *column* membership for parity is deliberately not
proposed: columns here are `sl.q.code.*` references rather than strings, so the
polars meaning would have nothing useful to check.

**3. `Ref.__getattr__` resolves eagerly**, and should keep doing so. It is the
object graph rather than the row world, and it is CLAUDE.md's headline promise.
What matters is that it be *named* as the one eager subsystem rather than left
implicit.

## The schema, measured

### The Ref graph is not the join graph

```
domain Ref fields          : 45
storage *_urn join columns : 65
```

They overlap and neither contains the other:

- **Storage has containment edges the model expresses as lists.**
  `code.codelist_urn`, `concept.scheme_urn`, `category.scheme_urn` are
  `Codelist.codes` / `ConceptScheme.concepts` in the model — `ItemList`
  membership, with no `Ref` to derive from. These are the most common joins.
- **Storage has side tables with no domain counterpart**: `annotation
  .owner_urn`, `localized_text.owner_urn`, `artefact_ref.source/target_urn`.
- **Names differ where they do correspond**: `Dimension.representation` is
  `dsd_component.codelist_urn`.

So deriving edges from `Ref` fields would be *wrong*, not merely redundant. The
worry about "declaring the Ref graph twice" dissolves: it is a different graph
that happens to overlap. What the query layer needs is the storage graph, and
the storage schema is ours and moves — three tables arrived in #276/#281 —
which is exactly what a `DESCRIBE` guard covers.

### Every consumer shape, against the design

The 113 hand-written sites from #275/#276/#281, by shape:

| sites | shape | verdict |
| --- | --- | --- |
| 29 | projection of two columns, no objects | works today (#275) |
| 22 | discovery: flows matching a concept | works today (#276) |
| 21 | dimensions of a flow, at catalogue scale | **needs this design** — nested fan-out |
| 13 | reference metadata for an artefact | works today (#281) |
| 12 | labels / spine / annotations | works today |
| 8 | concept-driven discovery | works with the design |
| — | aggregation (`GROUP BY`, counts) | works today (#275) |
| — | array aggregation (`list(...)`) | **now possible** — see above |
| — | label fallback (`COALESCE`) | **still open** — `.fill_null()` / `when-then` were proposed and never built; fall back in polars after `collect()` |
| — | facet counts over a caller table | still missing: cross-schema join |

Two gaps survive: joins over tables we do not own, and expression-valued
projection. The second was recorded here as **closed** by the polars branch,
on the reasoning that `fill_null` / `when-then-otherwise` and `.over()` come
free with an expression language rather than needing a feature. That was
wrong as a statement of what shipped: `.over()` was built (windows, through
`qualify`), but `fill_null` and `sl.when` never were. A label fallback is
done in polars on the collected frame. *(Corrected in place; the row above
said "closed".)*

## Path uniqueness, measured three ways

The same question asked at three granularities, over time. Consolidated here
because the three numbers were quoted in different places and read as though
they measured one thing.

| granularity | edges | reachable pairs | unique | note |
| --- | --- | --- | --- | --- |
| **tables** | 11 curated | 32 | **0** | `DataStructure`, `Dataflow`, `Codelist`, `Concept` are all rows of `artefact`, so everything collapses |
| **query-model types**, with hubs | 15 | 99 | **24** (24 %) | `annotation.owner_urn` / `localized_text.owner_urn` point at any artefact and connect everything to everything |
| **query-model types**, no hubs | 15 | 33 | **23** (70 %) | |
| **minimal FK-only**, no hubs, no shortcut edges | 13 | 31 | **26** (84 %) | what an implementation would actually declare |

**The 0-of-32 figure was the wrong measurement for the question it was used to
answer.** It is at table granularity, while the query model's namespace is
finer. At the granularity that matters, most pairs *are* unique — and some of
the remaining ambiguity in the middle rows was self-inflicted by declaring
convenience edges alongside the real FK ones.

The irreducible residue, under the minimal model:

```
component -> codelist   ['codelist', 'concept.codelist']
component -> code       ['codelist.codes', 'concept.codelist.codes']
dataflow  -> codelist   ['dsd.components.codelist', 'dsd.components.concept.codelist']
dsd       -> codelist   ['components.codelist', 'components.concept.codelist']
dsd       -> code       ['components.codelist.codes', 'components.concept.codelist.codes']
```

All five trace to **one** root cause: `dsd_component.codelist_urn` (a
component's own representation) versus `concept.repr_codelist_urn` (its
concept's default). SDMX lets a component override its concept's
representation, so these are two different codelists and two legitimate
questions. Demonstrated with rows under "Two routes to a codelist" below.

**What these numbers do and do not decide.** They bear on *form A* (bare
columns after a `join()`) and on any *contextual* resolution scheme where a
bare reference auto-joins. They do **not** bear on form B, where every
reference is rooted and the row-kind phantom keeps roots apart structurally.

### Two routes to a codelist, with rows

Fixture: concept `REF_AREA` defaults to `CL_GEO`; `CPI` uses it as a dimension
but **overrides** the codelist to `CL_AREA`; `TRADE` uses it as an attribute,
keeping `CL_GEO`.

```
dsd -> codelist, asking "which DSDs reference CL_GEO"
  components.codelist          (component's own representation)  -> ['TRADE']
  components.concept.codelist  (the concept's default)           -> ['CPI', 'TRADE']
```

The answers differ **only** on the structure that overrides. Drop the override
from the fixture and both return `['TRADE']` — so this is invisible in test
data where nobody overrides and wrong in production where they do.

A second case is **contingent** rather than inherent:

```
dsd -> concept, asking "which DSDs use REF_AREA"
  dimensions.concept  -> ['CPI']
  attributes.concept  -> ['TRADE']
  components.concept  -> ['CPI', 'TRADE']
```

Three answers only if `dimensions` / `attributes` / `measures` are declared as
separate edges. With a single `components` edge it resolves uniquely. Three is
the likely choice — `DataStructure` carries three `ItemList`s and the consumer
survey's largest bucket is role-specific — but it is a decision, not a fact.

## Performance

### Cost of the path machinery

`Node.__getattribute__` runs on every attribute access, which looked like a
hot-path risk worth checking before adopting. Measured:

```
plain Col == 'x'  (no Node)                      76 ns
Component.id == 'x'           (0 hops)           82 ns
Component.dsd.agency == 'x'   (1 hop)           201 ns
Component.flow.dsd.spine...   (3 hops)          788 ns

for scale
  today's `code.id == "C1"` predicate build      388 ns
  one compiled + executed query              223,000 ns
```

The concern was unfounded. A three-hop path is 0.35 % of one query, and the
*current* DSL's predicate build is slower than the new one-hop path. Worth
noting the cost is superlinear — ~293 ns per extra hop, because each hop
rebuilds child nodes — which is irrelevant at SDMX's one-to-three hops and
would matter on deep paths.

### Fan-out and N+1, which is what consumers actually hit

The common client shape is not a filter but a *fan-out they iterate*: for each
codelist, its codes. Written as N+1 today. Measured, 60 codelists x 40 codes:

```
N+1  (resolve each, then .codes.ids())    39.8 ms    120 queries
one nested query                           3.3 ms      1 query      12x
```

It scales with N, so production is worse than the fixture. The spelling:

```python
(
    reg.codelists()
    .filter(sl.q.codelist.agency == "ESTAT")
    .select(
        codelist=sl.q.codelist.id,
        codes=sl.q.codelist.codes.list(sl.q.code.id),
        n=sl.q.codelist.codes.count(),
    )
)
```

and the caller iterates without returning to the database, because `codes` is
a `list[str]` already in memory:

```python
for urn, codes, n in frame.iter_rows(): ...
```

The property that makes this composable rather than a one-off: `.list()` and
`.count()` compile to **correlated subqueries, not joins**, so asking for two
fan-outs at once (codes *and* annotations) cannot produce a cartesian product
the way two joins would.

## The peer sweep across the proposed surface

`query-composition.md` says it plainly: *when a DSL decision has a counterpart
in polars or SQLAlchemy, check it.* Four of the five borrowed-names defects on
record (#222, #255, #261, #292) were found by tests after shipping; the `in`
finding above was found by running the peers side by side. This section does
that sweep across the operators the design proposes but had not checked.

**Everything below was run.** polars 1.x, SQLAlchemy 2.x, ibis 12 (duckdb),
Django 6.x, DuckDB. Where a row says the peers disagree, they disagreed when
executed.

### Terminals

| ours | SQLAlchemy | Django | ibis | polars |
| --- | --- | --- | --- | --- |
| `collect()` | `.all()` / `.scalars().all()` | iterate / `list()` | `.execute()` / `.to_pyarrow()` | `.collect()` |
| `one()` | **`.one()`** | `.get()` | — | — |
| `one_or_none()` | **`.one_or_none()`** | — | — | — |
| `first()` | **`.first()`** | **`.first()`** | `.head(1)` | `.first()` |
| `count()` | `func.count()` | **`.count()`** | **`.count()`** | `.count()` |
| `exists()` | **`select(q.exists())`** | **`.exists()`** | `.isin(...).any()` | — |
| `explain()` | `str(stmt)` / `.compile()` | **`.explain()`** | `.compile()` / `ibis.to_sql` | **`.explain()`** |
| `preview(n)` | — | — | `.head(n)` | `.head(n)` |

`one` / `one_or_none` / `first` are SQLAlchemy's names exactly, and `exists` /
`count` / `explain` are Django's. **No invention required** — every terminal
the design proposed exists under that name in at least one peer, most in two.

### `[]` and `in`, confirmed across four

| | `q[slice]` | `q[0]` | `x in q` |
| --- | --- | --- | --- |
| polars `LazyFrame` | **lazy refine** | `TypeError` | column name (warns) |
| Django `QuerySet` | **lazy refine** (`QuerySet`) | executes → model | row membership |
| ibis `Table` | `.limit()` | — | column name |
| SQLAlchemy | `.limit()/.offset()` | — | **not implemented** |

polars and Django agree independently: **a slice refines lazily, an index
extracts.** That is two peers, not one, behind the design's terminal rule (as built:
`q[1:3]` refines, `q["FR"]` looks a row up by id, and `in` raises). Django is the lone
peer whose `in` means row membership, and it is also the one whose `in` is
documented to fall back on a full fetch.

### Nullable edges: the candidate join-kind rule was right, and incomplete

The sharpest result of the sweep. Django, nullable FK, join kind by context:

```
filter(cl__name='Areas')   -> INNER JOIN       orphan dropped
values('cid','cl__name')   -> LEFT OUTER JOIN  orphan kept, cl__name = None
exclude(cl__name='Areas')  -> LEFT OUTER JOIN  orphan RETURNED
```

The first two confirm the design's candidate rule. **The third was not anticipated and
corrects it.** A *negative* filter takes `LEFT`, because excluding rows that
match must still return rows that cannot match — under `INNER` the orphan would
vanish from a query asking for everything that is not "Areas", which is wrong.

So the rule is not "filter → INNER, projection → LEFT". It is:

> **The join kind follows from whether a null-extended row can satisfy the
> predicate.** Positive filter: it cannot, so `INNER`. Projection or negation:
> it can, so `LEFT`.

**And SQLAlchemy does not infer this at all** — `join(...)` is `INNER` and
`isouter=True` is explicit, verified by the orphan appearing only in the second:

```
select(Code).join(Code.cl)                -> ['x', 'y']
select(Code).join(Code.cl, isouter=True)  -> ['x', 'y', 'orphan']
```

So this is a genuine fork rather than a settled idiom: Django infers per
context, SQLAlchemy makes the caller say. Inference is right for a
path-expression DSL — there is no join clause to hang `isouter` on — but it
means the design had to specify the *negation* case, which no draft did.
*(Since resolved differently: a to-many hop compiles to a correlated
`EXISTS`, never a join, and `~` over it is `NOT EXISTS` — a true complement.)*

### `list()` alignment: the projection hazard, now measured

Asserted earlier, verified here. Two correlated `list()` subqueries over the
same child table, DuckDB:

```sql
SELECT (SELECT list(id)  FROM code WHERE cl=cl.id) AS ids,
       (SELECT list(lab) FROM code WHERE cl=cl.id) AS labs FROM cl
-> ids [x, y, z]   labs [X, Y, Z]      aligned
```

They align — **incidentally**. `list()` has no defined order without
`ORDER BY`, and the correspondence is a property of the plan, not of the data.
Change one subquery's order and it breaks silently:

```sql
       (SELECT list(lab ORDER BY pos) FROM code WHERE cl=cl.id)
-> ids [x, y, z]   labs [Y, X, Z]      MISALIGNED, no error
```

The struct form cannot misalign, because there is one list:

```sql
list({'id': id, 'lab': lab} ORDER BY pos)
-> [{'id': y, 'lab': Y}, {'id': x, 'lab': X}, {'id': z, 'lab': Z}]
```

**Both peers with an equivalent already return the aligned form.** polars
`group_by().agg(pl.col("id"), pl.col("lab"))` and ibis
`group_by().agg(ids=t.id.collect(), labs=t.lab.collect())` each keep the two
lists element-aligned — but each does so from a *single* grouping pass, which
is exactly the structural guarantee two independent correlated subqueries lack.
So the peers are not evidence that two `list()` calls are safe; they are
evidence that everyone else gets alignment from construction. Structs are how
SQL gets it too.

### Where the peers already agree with us

Checked because a silent divergence here would be the expensive kind:

| behaviour | polars | SQLAlchemy | Django | ibis | ours |
| --- | --- | --- | --- | --- | --- |
| `x != v` drops nulls | yes | yes (SQL) | yes (SQL) | yes | yes |
| `is_in([])` → no rows | yes | yes — emits `1 != 1` | yes | yes | yes (#273) |
| `select(name=expr)` kwargs | **yes** | `.label()` | `.annotate()` | **yes** | proposed (since built) |
| `.any()` on a to-many → `EXISTS` | — | **yes** | no — `INNER JOIN` (dupes) | — | yes |

Two notes. `select(alias=expr)` as keyword arguments is polars' and ibis's
spelling and validates the design's `select(flow=…, dims=…)` (since built). And the `.any()` row
restates the measured Django footgun from "To-many edges" — SQLAlchemy is the
peer we match, deliberately.

### Array aggregation is named four ways

`list()` (DuckDB / SQL `array_agg`), `collect()` (ibis), `agg()` / `implode()`
(polars), `ArrayAgg` (Django, PostgreSQL only). No convergence to borrow, so
the name is ours to choose; `list()` matches the SQL we emit and the DuckDB
function, which is the tiebreak used elsewhere in this file.

### What the sweep did not cover

- `PIVOT` and window functions — then the `query-composition.md` gaps, and no
  peer comparison changed that they were unimplemented. *(Windows have since
  been built — `qualify(col.rank(...).over(...) == 1)`; `PIVOT` has not.)*
- Streaming / chunked collection. polars has `collect(engine="streaming")` and
  ibis `.to_pyarrow_batches()`; nothing in this design said what happens when a
  result does not fit in memory, and its terminal list was silent on it.
  *(Since built: `stream()` returns Arrow record batches.)*
- `group_by` / `agg` shapes beyond naming.
- The data plane. Its operator mapping to `c[...]` was checked against
  a downstream SDMX REST service, but not against these peers, and its namespace is dynamic so
  most rows above would not apply.

## Typing hazards found while prototyping

### The `.str` namespace shadows the builtin — a real hazard

Found building this, and worth keeping whichever branch wins, because any
namespace named for a builtin has it.

```python
class A:
    @property
    def str(self) -> StrNS: ...
    def f(self, x: list[str]) -> None: ...   # `str` is now the property
```

Confirmed at runtime rather than argued:

```
typing.get_type_hints(A.f)  ->  {'x': list[<property object at 0x...>], ...}
basedpyright                ->  0 errors
pyrefly                     ->  2 errors
```

The annotation genuinely resolves to the property. **basedpyright misses it
entirely; pyrefly catches it** — the third instance this session, and a data
point for what later made pyrefly the only type checker (#425; CLAUDE.md,
*Typing conventions*).

It is not only a typing nicety: anything doing runtime introspection through
`get_type_hints` sees a property object where a type belongs, and **attrs does
exactly that**. The fix is `import builtins` and `builtins.str` in any class
carrying a `.str` namespace — which is what the prototype does.

## Hostile-review measurements

Three things checked while trying to break the chosen design. The first found a
hole; the second contradicted a claim the design was leaning on; the third
corrected a claim about polars.

### The conjunction ambiguity, with rows

```python
reg.query(q.dsd).filter(q.dsd.dimensions.concept.id == "REF_AREA",
                        q.dsd.dimensions.codelist.id == "CL_GEO")
```

Fixture: DSD `A` has one dimension carrying both values; DSD `B` has two
dimensions carrying one each.

```
(a) two EXISTS  — any dimensions, possibly different  -> ['A', 'B']
(b) one EXISTS  — the same dimension satisfies both   -> ['A']
```

Different answers, and a bare dot-through path cannot say which is meant.

**Django distinguishes them by chaining**, measured:

```
filter(a=1, b=2)         SAME related object   -> ['A']       1 JOIN
filter(a=1).filter(b=2)  INDEPENDENT objects   -> ['A', 'B']  2 JOINs
```

That resolution is unavailable here: `query-composition.md` states, and
generated property tests assert, that `filter` chaining is AND and identical to
passing both at once — which is also polars' behaviour. Buying Django's
distinction would mean breaking a law with tests behind it.

### Correlated subqueries: linear in fan-out columns

The design leans on "correlated subqueries compose, joins do not". True of
correctness, false of cost. 2,000 codelists × 50 codes = 100k child rows:

```
1 correlated list() subquery      9.7 ms
2                                21.4 ms
3                                29.8 ms
4                                40.6 ms        ~10 ms each, linear
1 struct list() with 4 fields    56.6 ms        <- the alignment-safe form
GROUP BY equivalent (4 lists)    31.8 ms        <- what polars and ibis do
```

Three findings. Cost is **linear in the number of fan-out columns**, so a
five-column projection pays five passes. The **struct form recommended for
alignment safety is the slowest**. And **`GROUP BY` — rejected because it
changes row count — is faster than either**.

None of this makes the design wrong; it makes the *compiler* load-bearing in a
way the prose did not admit. Multiple fan-outs over one edge must be coalesced
into a single pass rather than emitted independently, and that is the
shared-prefix deduplication which has never been prototyped.

### polars can express the same-child conjunction

An earlier claim that polars cannot do this was wrong. It can, and the
mechanism is `pl.element()` — a **global bound by the `.list.eval()` context**:

```python
pl.col("dims").list.eval(
    (pl.element().struct.field("concept") == "REF_AREA")
    & (pl.element().struct.field("codelist") == "CL_GEO")
).list.any()                                        # -> ['A']  same element

# two separate .list.eval(...).list.any() conjoined  -> ['A','B']  independent
```

So a binder is not a departure from polars — polars ships one. The difference
is that `pl.element()` is untyped while a child-rooted accessor carries its row
kind and is checked.

### Gap-analysis probes

Three claims in "What the design still cannot express" checked rather than
asserted.

**Any-language label search.** `name` is `MAP(VARCHAR, VARCHAR)`, and
`name['en']` picks one language. Matching in *any* language needs map
traversal:

```sql
SELECT id FROM code
 WHERE len(list_filter(map_values(name), x -> x ILIKE '%lindo%')) > 0
-> ['MX']      matched on the Spanish label alone
```

Sharper than it looks, given the project rule that no API has a default
language: a search box over a multilingual catalogue is the normal case, not an
exotic one.

**Array-column predicates.** `ancestor_ids` is `TEXT[]`. `descendants_of(x)`
covers a single ancestor; "under any of these" is a different operator:

```sql
SELECT id FROM code WHERE list_has_any(ancestor_ids, ['EUR','AFR'])
-> ['FR']
```

**"The latest version of each artefact" needs more than `.over()`.** Window
framing is not sufficient — it needs a window *function* and a filter on its
result:

```sql
SELECT agency,id,version FROM (
  SELECT *, row_number() OVER (PARTITION BY agency,id ORDER BY version DESC) rn
  FROM art) WHERE rn = 1
-> ESTAT/GDP 1.0, ESTAT/CPI 2.0
```

So `row_number` / `rank` / `lag` are a separate requirement from `.over()`, and
filtering on a windowed column is a third.

### Cross-schema and cross-plane probes

**A downstream accessor with an edge into the sdmx graph type-checks.** The
caller declares its own row kind and accessor; `edge()` threads the *caller's*
row kind through our classes because they are generic over it:

```
my.dataset.code.name["en"]        -> Str[DatasetRow]
my.dataset.code.codelist.id       -> Str[DatasetRow]
reg.query(my.dataset).filter(q.code.id == "CA")
    error: Argument of type "Pred[CodeRow]"
```

basedpyright 0/0/0, pyrefly 0 errors. Nothing in sdmxlib changes — this is the
`RowKind` extension point reaching one level further.

**Labelling observations is one statement.** The DSD is known when a data query
is bound, so each dimension's codelist URN is a constant:

```sql
SELECT o.REF_AREA, ra.name['en'] AS REF_AREA_label,
       o.FREQ,     fr.name['en'] AS FREQ_label, o.value
FROM obs o
LEFT JOIN sdmx.code ra ON ra.codelist_urn='u:cl:AREA' AND ra.code_id=o.REF_AREA
LEFT JOIN sdmx.code fr ON fr.codelist_urn='u:cl:FREQ' AND fr.code_id=o.FREQ
```

`LEFT JOIN`, so an unmapped code keeps its row with a null label; no lookup and
no N+1, because the URN is resolved at bind time rather than per row.

### Set operations against the refinement principle

Which set operations narrow, measured against polars' equivalents:

```
union       [1,2,3] u [3,4] -> [1,2,3,3,4]   subset of a? False   WIDENS
intersect   semi-join       -> [3]           subset of a? True    narrows
difference  anti-join       -> [1,2]         subset of a? True    narrows
```

So `intersect` and `difference` are refinements and may be methods on a query;
`union` is not, and a `union` method would let a later caller undo an earlier
helper's scoping — the exact failure `query-composition.md`'s principle exists
to prevent. Hence `sl.union(a, b)` as a free function.

### Filtering on a window expression

polars permits it directly:

```
df.filter(pl.col("version").rank("ordinal", descending=True).over("agency","id") == 1)
-> ESTAT/CPI 2.0, ESTAT/GDP 1.0
```

SQL does not — it needs a subquery wrapper, so this is a compiler obligation
rather than a syntax question:

```sql
SELECT * FROM (SELECT *, rank() OVER (PARTITION BY agency,id ORDER BY version DESC) rn
               FROM art) WHERE rn = 1
```

### Two new features that do not commute

Checked against the third composition law ("every ordering either commutes or
is refused"). Both fail it, and neither was refused:

```
filter(p).filter(rank().over(...) == 1)   -> 3 rows
filter(rank().over(...) == 1).filter(p)   -> 2 rows       commute? False

filter(keep).unique(agency)               -> 1 row
unique(agency).filter(keep)               -> 0 rows       commute? False
```

The first is inherent — a rank is computed over whatever set precedes it, and
SQL runs `WHERE` before window functions. DuckDB supports `QUALIFY`, which
names the stage so the order is fixed by vocabulary rather than by call order:

```sql
SELECT * FROM art WHERE version <> '2.0'
 QUALIFY rank() OVER (PARTITION BY agency,id ORDER BY version DESC) = 1
```

The second is worse: `unique(subset)` also leaves *which* row survives
unspecified. Full-row `unique()` has neither problem, so the subset form is
excluded rather than given a precondition.

**Both fixes are enforced by types, not discipline.** `.over()` returns
`Win[R]` whose comparisons give `WinPred[R]`, which is deliberately not a
`Pred[R]`:

```
filter(<WinPred>)      error: Argument of type "WinPred[DsdRow]"
qualify(<Pred>)        error: Argument of type "Pred[DsdRow]"
unique(q.dsd.agency)   error: Expected 0 positional
```

basedpyright 0/0/0, pyrefly 0 errors. pyrefly additionally required `@override`
on `Win.__eq__` / `Win.__ne__`, which override `Col`'s — a real base class
rather than `object`, which is the case CLAUDE.md keeps
`missing-override-decorator` for. basedpyright did not flag it.

## Prototype log

### What the prototype does and does not do

Stated so the demos are not read as more than they are:

- The join compiler handles depth-1 to-one paths against one hard-coded edge.
  Multi-hop paths, shared-prefix deduplication and alias collision are
  unimplemented — that remains the real work.
- The codelist->code fan-out timing above is of hand-written SQL in the shape
  the design would emit, not of generated output.
- No `order_by` / `limit` / `offset` / `group_by`, so none of
  `query-composition.md`'s refinement laws are exercised over a joined query.
- **Hierarchy fan-out is unexplored and is the harder case.** "Each node with
  its descendants" is recursive, and `hierarchy_node` bakes `depth` /
  `ancestor_ids` precisely so descendants need no recursion. Whether
  `.descendants.list(...)` maps onto the baked columns or needs a recursive CTE
  is open — and `narrower_transitive` already exists as a scoped verb, which is
  the replace-or-coexist question in concrete form.
  **Since answered:** no recursive CTE is ever emitted. An earlier
  correction here said that therefore `.descendants.list(...)` would not be
  built and `narrower_transitive` would stay the only spelling; that was
  overtaken. `ancestors` / `descendants` became declared edges over the baked
  closure (`query-tree-paths.md`), so `sl.q.code.descendants.id.list()` is an
  ordinary correlated list over stored columns, and `narrower_transitive` is
  a named filter over the same edge. Recursion is still never path syntax —
  the closure is read, not computed.
- Binding a URN list with `unnest(?)` is the ~48 us/value path #271 measured. *(That rate was duckdb with pandas absent; with pandas importable it is ~1-2 us — DuckDB retries `import pandas` per bound value when it is missing (#411). The shape holds either way. See `tests/unit/test_value_set.py`.)*
  Fine at 60 URNs; at 5,000 it is what `value_set` exists for.

### The three throwaway prototypes from the original pass

Three throwaway prototypes were written and run while producing this document:
join-by-table with declared edges; ibis-style handles with inferred joins; and
a component query with `is_in` composition. None is committed. They exist only
as evidence that the compiled SQL and the ergonomics were observed rather than
imagined, and the SQL each produced is quoted above.

### The typed prototypes, and what each settled

Roughly twenty throwaway prototypes were written and type-checked while
producing these documents. None is committed; they exist as evidence that the
typing was observed rather than imagined. Every result below is from a run.

| probe | question | result |
| --- | --- | --- |
| standalone query model | does a separate query model type where instrumenting attrs does not? | basedpyright 0/0/0; instrumenting loses all static checking |
| `edge()` witness | can a typed factory record a path with no annotation and no suppression? | yes; one `cast` inside the helper, 0 errors |
| lowercase singletons | does `sl.q.component` type as well as `sl.q.Component`? | both checkers clean, 9 `assert_type` pass; closed the "pyrefly unverified" caveat |
| two entry points | can `artefacts(T)` and `dataflows()` coexist? | yes; all three cross-combinations rejected |
| root-bound row kind | does `edge()` return the target's row kind or the root's? | **the root's** — the first version failed to compile |
| `Many` witness | `many("x", ComponentCols)` vs `many("x", ComponentRow)` | pyrefly caught the accessor/row-kind mismatch basedpyright accepted |
| generic helper | can one helper scope any root? | yes, at the cost of threading the accessor as an argument |
| path-is-proof | does an undeclared hop fail? | `q.dataflow.concept` → *Cannot access attribute "concept" for class "DataflowCols"* |
| parent-level `.any()` | does it type? | yes — **vacuously**; accepts `q.dsd.any(q.metadataflow.id == …)` |
| reachability union | can the root check be recovered? | yes — then killed by `Pred.__and__` making `Pred` invariant |
| `Fan[R]` marker | can the type force EXISTS/list? | **dead** — `Pred[Fan[Fan[R]]]` on nested fan-out |
| dot-through | child accessor at the parent's row kind | both checkers clean; nests to any depth; cross-root still rejected |
| `.every()` modifier | does `Pred[R].every()` type? | trivially; the SQL is where the trouble is |
| HOAS binder | does a callback give fresh aliases? | yes; self-join works by going **up** then down, not by nesting |
| polars-shaped surface | namespaces + expression methods, typed | both checkers clean |
| `.str` shadowing | does a `.str` namespace shadow the builtin? | yes, at runtime; basedpyright silent, pyrefly catches it |

**pyrefly caught three things basedpyright did not** across this work — the
`Many` witness mismatch, the `.str` shadowing, and the empty-body stubs. That
was a data point for what later made pyrefly the only type checker (#425;
CLAUDE.md, *Typing conventions*), and at the time an argument for running
both on any prototype before trusting it.
