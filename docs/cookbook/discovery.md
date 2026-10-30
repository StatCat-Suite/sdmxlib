# Catalogue Discovery

The other cookbook pages answer questions *inside* an artefact — which codes a
codelist has, which levels a hierarchy defines. This one answers questions
*across* the catalogue: which dataflows exist, which of them use a concept,
which codelists nothing references.

These are the questions a new user tries first, and the obvious way to ask them
is to load everything:

```python
[df for df in reg.get_all(sl.Dataflow) if "REF_AREA" in df.structure().dimensions]
```

That resolves every dataflow and its DSD to answer a question about one column.
`reg.query(...)` compiles the same question to a single SQL statement.

```python
import sdmxlib as sl
from sdmxlib.local import LocalRegistry
from sdmxlib.query.accessors import q
```

## Listing and filtering

`reg.query(q.<relation>)` returns a lazy query. Nothing runs until you
materialise it:

```python
reg.query(q.dataflow).ids()
reg.query(q.dataflow).filter(q.dataflow.agency == "ESTAT").ids()
reg.query(q.codelist).filter(q.codelist.id.str.starts_with("CL_")).urns()
```

Every maintainable artefact shares a surface — `id`, `agency`, `version`,
`urn`, `uri`, `is_final`, `valid_from`, `valid_to`, plus `name[lang]`,
`description[lang]` and `annotations`:

```python
(
    reg.query(q.codelist)
    .filter(q.codelist.agency == "AG")
    .filter(q.codelist.name["en"].str.folded.contains("geography"))
    .order_by(q.codelist.id)
    .collect()
)
```

`q.artefact` is the same surface without a type constraint — "everything ESTAT
maintains", whatever it is:

```python
reg.query(q.artefact).filter(q.artefact.agency == "ESTAT").urns()
reg.query(q.artefact).filter(q.artefact.type == "Codelist").ids()
```

When the type is a *variable* rather than written at the call site,
`reg.artefacts(T)` applies that discriminator for you:

```python
def summarise(kind: type) -> list[str]:
    return reg.artefacts(kind).filter(q.artefact.agency == "ESTAT").ids()
```

### Matching strength

Every string comparison is **exact by default** — case, accents and
punctuation all significant. Anything looser is a rung you name, under `.str`:

| Spelling | Ignores | Available on |
| --- | --- | --- |
| `id.str.contains(x)` | nothing | every string field |
| `id.str.caseless.contains(x)` | case | every string field |
| `name["en"].str.unaccented.contains(x)` | case + diacritics | label fields |
| `name["en"].str.folded.contains(x)` | case + diacritics + punctuation | label fields |

Each rung matches everything the one above it does, so moving down the table
can only add rows. `caseless` also carries `==` and `!=`, which the accent
rungs deliberately do not:

```python
q.codelist.id == "cl_freq"               # exact — finds nothing if the id is CL_FREQ
q.codelist.id.str.caseless == "cl_freq"  # finds CL_FREQ
```

Only `caseless` gets equality because only case has a real question behind it —
a caller typing an id in the wrong case. `is_in` is always exact.

Accent and punctuation folding is offered on labels only: an id's punctuation
is significant, so `REF_AREA` and `REF AREA` are different ids.

### Annotations

Annotations are a fan-out like any other child relation, so they are reached
with an ordinary path rather than a bespoke predicate:

```python
reg.query(q.artefact).filter(q.artefact.annotations.type == "tier").ids()
reg.query(q.artefact).filter(~(q.artefact.annotations.type == "tier")).ids()  # has none
```

Dotting through a fan-out means *some* annotation, and compiles to a correlated
`EXISTS`. `NOT EXISTS` is total, so those two partition the catalogue.

Because it is a fan-out, asking two things of the **same** annotation needs
`.any()` — otherwise the two conditions may be satisfied by different rows:

```python
# one annotation that is both a tier and titled "core"
reg.query(q.artefact).filter(
    q.artefact.annotations.any(q.annotation.type == "tier", q.annotation.title == "core")
).ids()
```

`.any()` exists for that distinction; see
[Structures as Frames](structures_as_frames.md).

### Combining predicates

Chained `filter()` calls AND together. For anything else, combine predicates
with `&`, `|` and `~`:

```python
# Either agency
reg.query(q.dataflow).filter(
    (q.dataflow.agency == "ESTAT") | (q.dataflow.agency == "ECB")
).ids()

# One agency, minus a draft naming convention
reg.query(q.codelist).filter(
    (q.codelist.agency == "AG") & ~q.codelist.id.str.starts_with("TMP_")
).ids()
```

Parenthesise each comparison: Python binds `&` and `|` more tightly than `==`,
so `q.dataflow.agency == "A" | q.dataflow.agency == "B"` raises a `TypeError`
rather than running. And `~` compiles to SQL `NOT (...)`, so under three-valued
logic a row whose column is `NULL` — an artefact with no French name, say —
satisfies neither `p` nor `~p`.

### Filtering by a large set of ids

`is_in` with a Python list binds the list as one parameter, and DuckDB's Python
client still converts its elements one at a time. Past a hundred or so values
that dominates the query itself — without pandas installed a 5000-code filter
spends hundreds of milliseconds binding and a few querying. Register the set
instead:

```python
with reg.session() as active, active.value_set(ids) as wanted:
    codes.filter(sl.q.code.id.is_in(wanted)).order_by(sl.q.code.id).limit(50).collect()
```

`register` hands DuckDB a pointer to an Arrow buffer rather than converting
values one at a time, so the registered path is very nearly flat while binding
grows linearly. *How* steeply depends on whether pandas is installed: for each
bound value DuckDB checks whether it is `pandas.NA`, and with pandas absent that
check retries `import pandas` every time. Median of 7, duckdb 1.5.5:

```
    N        bound, no pandas    bound, pandas    registered
    100          13.7 ms             4.3 ms          5.4 ms
    1000         85.6 ms             5.4 ms          5.5 ms
    5000          445 ms            10.0 ms          6.5 ms
    50000        4121 ms            59.1 ms         13.6 ms
```

**Worth it from about a hundred values without pandas, about a thousand with
it.** sdmxlib does not depend on pandas, so unless your environment brings it
in, the first column is yours. Below the crossover a plain list is as fast and
simpler; above it the advantage keeps widening, because the shape — linear
against flat — is the same in both.

`is_in` also takes another **query**, which is the lazy form: the sub-query is
spliced as `IN (SELECT ...)` rather than fetched and re-bound.

```python
estat = reg.query(q.codelist).filter(q.codelist.agency == "ESTAT").select(urn=q.codelist.urn)
reg.query(q.code).filter(q.code.codelist.urn.is_in(estat)).count()
```

### Ordering

`order_by` takes any number of keys and **replaces** whatever order was already
set. Mark a key with `.desc()` for descending; an unmarked key is ascending:

```python
reg.query(q.dataflow).order_by(q.dataflow.agency, q.dataflow.id.desc()).ids()
```

Replacing rather than appending means a caller stating an order gets the order
they stated. Appending would make the *first* caller's key dominate, so a
helper sorting by a unique column would silently void yours.

Every query appends a deterministic tiebreak of its own whether or not you
sorted, so the order is always total — which is what makes the pages below
stable across calls.

### Paging

`limit(n)` caps the result; `offset(n)` skips from the front:

```python
def page(query, number, size=50):
    return query.offset(number * size).limit(size)

page(reg.query(q.dataflow).order_by(q.dataflow.id), 2).ids()
```

Both narrow and never widen, so a helper's cap survives a caller: `offset` only
raises the floor (`offset(5).offset(10)` skips ten, not fifteen and not five)
and `limit` only lowers the ceiling. Filtering *after* a slice is refused
outright, because it would silently change which rows the slice had chosen.

**Order matters between these two, and nowhere else in the DSL.**
`offset(5).limit(10)` is ten rows starting at five; `limit(10).offset(5)` is
five rows — of the first ten, skip five.

For deep paging, `page()` returns a keyset cursor rather than an offset, which
does not degrade as the offset grows:

```python
first = reg.query(q.dataflow).order_by(q.dataflow.id).page(50)
second = reg.query(q.dataflow).order_by(q.dataflow.id).after(first.cursor).page(50)
```

### Four ways to materialise, and their costs

| call | returns | cost |
| --- | --- | --- |
| `urns()` / `ids()` | `list[str]` | one statement, one column |
| `select(...).collect()` | `polars.DataFrame` | one statement, the columns you named |
| `frame()` | `polars.DataFrame` | one statement, every column — **builds nothing** |
| `collect()` | `list[Dataflow]` | one statement **plus** an artefact built per match |

`collect()` is the expensive one — it builds each match as a domain object.
Reach for `urns()` or a projection when the answer is a list of identifiers
rather than a graph to navigate; this API exists alongside `get_all()` for that
difference.

**`collect()` is for object graphs, `frame()` is for volume**, and the gap is
large: on a real edition's 579,951-code codelist, `frame()` is **158 ms** and
`collect()` is **2372 ms**. Both run the same statement over the same columns;
the difference is 579,951 Python objects. If you are about to loop over
`collect()` pulling two fields out of each artefact, `select()` those two
columns instead.

## Concept-driven discovery

The most common question asked of an SDMX catalogue, and the one that spans
three tables. From the DSD, it is one path:

```python
reg.query(q.dsd).filter(q.dsd.components.concept.id == "REF_AREA").ids()
```

From the dataflow, one hop further — through the DSD it is built on:

```python
reg.query(q.dataflow).filter(q.dataflow.dsd.components.concept.id == "REF_AREA").ids()
```

### Structures and dataflows that use a concept

"Every artefact that uses this concept" spans two roots — a DataStructure
carrying such a component, and a Dataflow whose DSD does. That is an `|` of two
sub-queries, not a set operation:

```python
direct = reg.query(q.dsd).filter(q.dsd.components.concept.id == "REF_AREA").select(urn=q.dsd.urn)
via = (
    reg.query(q.dataflow)
    .filter(q.dataflow.dsd.components.concept.id == "REF_AREA")
    .select(urn=q.dataflow.urn)
)
reg.query(q.artefact).filter(q.artefact.urn.is_in(direct) | q.artefact.urn.is_in(via)).ids()
```

`union` is deliberately refused here: a set operation compares rows of *one*
relation, and these are two. Checked against the peers before settling it —
SQLAlchemy writes `or_(exists, exists)`, ibis an `isin` disjunction, polars an
`is_in` over two sets. None reaches for a union.

### Which codelists are actually used

The same shape read from the other end:

```python
reg.query(q.dsd).filter(q.dsd.components.codelist.id == "CL_GEO").ids()
```

Note there are **two** routes to a codelist and they answer differently: a
component's own representation, and its concept's default. Both are spellable
and neither is guessed:

```python
q.dsd.components.codelist.id == "CL_GEO"          # the component's own
q.dsd.components.concept.codelist.id == "CL_GEO"  # its concept's default
```

They agree until a structure overrides, which is exactly when the difference
matters.

### Components as rows

Everything above reaches components to filter *artefacts*. The other direction
— getting the components themselves out, across many DSDs at once — is
`q.component`:

```python
(
    reg.query(q.component)
    .filter(q.component.dsd.agency == "ESTAT")
    .select(
        q.component.id,
        dsd=q.component.dsd.id,
        codelist=q.component.codelist.id,
    )
    .collect()
)
```

One statement for the whole catalogue. Reaching it through resolved artefacts
instead — `dsd.dimensions` per DSD — costs one resolution per DSD.

`role` says which list a component came from: `Dimension`, `TimeDimension`,
`MeasureDimension`, `Attribute` or `Measure`.

```python
reg.query(q.component).filter(q.component.role == "Attribute").ids()
```

`position` is declaration order across the whole DSD — dimensions, then
attributes, then measures. For a dimension that order is *semantic*: it is the
order of the observation key.

### Representation: the two arms

A component's representation is `Ref[Codelist | ValueList] | Facet | None` —
enumerated, or free text with bounds. (`sl.Representation` names that union.) Both arms are reachable and they read symmetrically:

```python
q.component.codelist.id == "CL_GEO"        # enumerated — an edge
q.component.facet.max_length > 100         # free text — a namespace
```

`facet` is a **zero-hop namespace**, not an edge: the bounds live on the
component's own row, so reaching them adds no join and cannot multiply rows.
It exists to group fifteen columns that only mean anything together, and to
keep them from crowding a surface where `position` and `role` live.

```python
# free-text components long enough to hold prose
reg.query(q.component).filter(q.component.facet.max_length > 100).ids()

# components whose values are pattern-constrained
reg.query(q.component).filter(q.component.facet.pattern.is_not_null()).ids()

# what type each free-text component carries
reg.query(q.component).select(q.component.id, dtype=q.component.facet.text_type).collect()
```

The full set is `text_type`, `min_length`, `max_length`, `min_value`,
`max_value`, `decimals`, `pattern`, `is_sequence`, `start_value`, `end_value`,
`start_time`, `end_time`, `interval`, `time_interval` and `is_multilingual` —
the fields of the IM's `Facet`, under its own name.

The same namespace hangs off every relation that carries a representation, so
the spelling does not change with the owner:

```python
q.concept.facet.max_length              # a concept's default representation
q.metadata_attribute.facet.text_type    # an MSD slot's
```

### By category

```python
reg.query(q.artefact).filter(q.artefact.categorisations.category.id == "ECON").ids()
```

### With reference metadata

```python
reg.query(q.artefact).filter(q.artefact.described_by.metadata_set.id.is_not_null()).ids()
```

Narrowed to one reported attribute, via a sub-query:

```python
quality = (
    reg.query(q.reported_attribute)
    .filter(q.reported_attribute.attribute_path.str.starts_with("QUALITY"))
    .select(urn=q.reported_attribute.metadata_set.urn)
)
reg.query(q.artefact).filter(q.artefact.described_by.metadata_set.urn.is_in(quality)).ids()
```

## Counting the catalogue

`select` and `group_by` work exactly as they do on codes — see
[Structures as Frames](structures_as_frames.md):

```python
reg.query(q.dataflow).group_by(q.dataflow.agency).len().collect()
```

```
┌────────┬─────┐
│ agency ┆ len │
╞════════╪═════╡
│ AG     ┆ 2   │
│ ESTAT  ┆ 1   │
└────────┴─────┘
```

Projected columns are named the way you wrote them: `q.dataflow.id` gives
`id`, `q.dataflow.agency` gives `agency`. The storage columns behind them
(`resource_id`, `agency_id`) belong to the schema the path language exists to
hide, and only `reg.sql()` and `sl.schema` should ever name them.

Two of one name is refused rather than renamed, because only you know which is
which:

<!-- test: raises -->

```python
reg.query(q.dataflow).select(q.dataflow.id, q.dataflow.dsd.id)
# TypeError: select() would produce more than one column called 'id' …
```

Name either side to resolve it — as a keyword at the call site, or with
`alias` on the expression:

```python
reg.query(q.dataflow).select(flow=q.dataflow.id, dsd=q.dataflow.dsd.id)
reg.query(q.dataflow).select(q.dataflow.id, q.dataflow.dsd.id.alias("dsd"))
```

`alias` is the one that reaches inside `list`, whose struct fields take their
names from the columns given to it:

```python
reg.query(q.codelist).select(
    codes=q.codelist.codes.list(
        q.codelist.codes.id, q.codelist.codes.parent.id.alias("parent")
    )
)
```

## Asking for exactly one artefact

`collect()` returns a list whatever the answer's size, which is the wrong shape
when a query is meant to identify a single thing — the caller ends up writing
`[0]` and finding out at the wrong moment that there were two.

```python
# Exactly one, or raise. Use when zero is a bug.
dsd = reg.query(q.dsd).filter(q.dsd.agency == "AG", q.dsd.id == "DSD").one()

# At most one. Use when absent is an ordinary answer.
maybe = reg.query(q.dsd).filter(q.dsd.id == "MISSING").one_or_none()   # None
```

The difference is only what happens at zero: `one()` raises `ValueError`,
`one_or_none()` returns `None`. Both raise when there is more than one, because
"I expected a single artefact" is wrong either way and silently taking the
first would hide it. Note that an id is not unique on its own — two agencies,
or two versions, share one — so a query meant to identify an artefact usually
names an agency too.

`preview(n)` is the REPL terminal — a bounded head, so a mistyped filter costs
nothing:

```python
reg.query(q.dataflow).preview(5)      # a DataFrame, at most 5 rows
```

## Seeing the plan without running it

Nothing executes until a terminal, which is what makes `explain()` total: there
is no query whose plan is unavailable because part of it already ran.

```python
print(reg.query(q.dataflow).filter(q.dataflow.agency == "AG").explain())
```

```
SELECT s0.* FROM sdmx.artefact s0
WHERE (s0.artefact_type = ? AND s0.agency_id = ?)
ORDER BY s0.urn NULLS LAST
-- binds: 'Dataflow', 'AG'
```

Values are bound rather than interpolated, so what you see is what runs. A
projection explains too:

```python
reg.query(q.dataflow).group_by(q.dataflow.agency).len().explain()
```

Reach for it when a filter returns nothing and the question is *why* — the
plan says which joins the path resolved to, and a path crossing an edge you
did not expect is visible there and nowhere else.

## Data-quality questions

Because a label compiles to a scalar subquery rather than an existence test, it
can be checked for *absence* — which artefacts are missing a translation:

```python
reg.query(q.codelist).filter(q.codelist.name["fr"].is_null()).ids()
```

Combine with a count for a catalogue-wide view:

```python
(
    reg.query(q.artefact)
    .filter(q.artefact.name["en"].is_null())
    .group_by(q.artefact.type)
    .len()
    .collect()
)
```

## The latest version of each artefact

Artefacts are versioned, and a catalogue holds every version. "The current
one" is a ranking question, not a filter — you cannot write it as a `WHERE`,
because which row wins depends on the other rows.

`qualify` is SQL's `QUALIFY`: a filter over a *window*.

```python
latest = (
    q.dataflow.version
    .rank(descending=True)
    .over(q.dataflow.agency, q.dataflow.id) == 1
)

reg.query(q.dataflow).qualify(latest).collect()
```

Read it as: rank each dataflow's versions within its `(agency, id)` group,
newest first, and keep the winners. One statement — the database never
materialises the versions it discards.

### A version *range*, which is an ordinary filter

"The latest" needs a window because the answer depends on the other rows.
"At 2.0 or later" does not:

```python
reg.query(sl.q.dataflow).filter(sl.q.dataflow.version >= "2.0").ids()
```

Comparison is on the sort key baked beside the column, not on the text, so
`1.9` is correctly *earlier* than `1.10` — the same ordering `order_by` and
`rank()` use. Equality is unaffected: `version == "1.10"` compares what the
registry published.

### As of a date

Every maintainable carries a validity window, and both ends are columns:

```python
import datetime as dt

cutoff = dt.datetime(2025, 1, 1)
reg.query(sl.q.dataflow).filter(sl.q.dataflow.valid_to.is_null()).ids()      # still current
reg.query(sl.q.dataflow).filter(sl.q.dataflow.valid_to < cutoff).ids()       # retired by then
```

`valid_to` is null for an artefact with no stated end, which is the common
case — so "still current" is a null test rather than a comparison.

The cutoff can be a tz-aware `datetime`, a naive one (read as UTC, as the store
reads it) or a `date` (midnight UTC); all three mean the same instant whatever
zone your machine or DuckDB session is in. Anything else — an `int`
year, a string — is refused with a `TypeError` naming the column.

`qualify` is a **separate clause from `filter`, and the type system enforces
it.** `.over()` returns a window expression whose comparisons build a
`WinPred` rather than a `Pred`, so `filter(rank == 1)` and
`qualify(id == "X")` are both rejected by the checker, each naming the other
clause.

**Call order does not decide which set the window sees.** Every clause
compiles into one `SELECT`, and SQL evaluates `WHERE` before the window, so
these two are the *same* query — the rank is computed over the filtered rows
either way:

```python
reg.query(q.dataflow).filter(q.dataflow.agency == "ESTAT").qualify(latest)
reg.query(q.dataflow).qualify(latest).filter(q.dataflow.agency == "ESTAT")
```

The other reading — rank across the whole catalogue, *then* keep the ESTAT
rows, which may be none — is a **different query**, not a different call
order, and it is spelled by nesting:

```python
ranked = reg.query(q.dataflow).qualify(latest).select(urn=q.dataflow.urn)

(
    reg.query(q.dataflow)
    .filter(q.dataflow.urn.is_in(ranked), q.dataflow.agency == "ESTAT")
    .collect()
)
```

Now the window really does see the whole catalogue, and the result is empty
whenever ESTAT holds no globally-newest flow. That is the same shape
filter-after-slice uses, and for the same reason: when two readings are both
meaningful, the DSL spells them differently rather than making one of them
depend on call order.

!!! note "Version ranking is numeric"

    `version` is stored as text, and ranking text would put `1.9` after
    `1.10` and call `1.9` the latest. So a sortable key is baked beside it at
    write time, and `order_by`, `rank()` and keyset paging all use it.
    Comparison is unaffected — `version == "1.10"` matches what the
    registry published, not the key.

    Legacy (`1`, `1.0`) and semantic (`1.0.0`) versions interleave correctly,
    and a prerelease sorts before its release. The one stated limit is
    ordering *among* prereleases, which is lexical rather than full SemVer
    precedence, so `1.0.0-alpha.10` sorts before `1.0.0-alpha.9`.

`rank()` is not the only window. `dense_rank()`, `row_number()`, `lag()` and
`lead()` are available on the same `.over(...)`, and the framing is a separate
call because "what is computed" and "over which rows" are separate decisions.

## Composing queries

A one-column projection is a `SqlRelation`, so it splices into another query as
a subquery rather than being materialised:

```python
estat = reg.query(q.codelist).filter(q.codelist.agency == "ESTAT").select(urn=q.codelist.urn)
reg.query(q.code).filter(q.code.codelist.urn.is_in(estat)).ids()
```

Whole queries also compose as sets, when both sides are the *same* relation:

```python
a = reg.query(q.codelist).filter(q.codelist.agency == "ESTAT")
b = reg.query(q.codelist).filter(q.codelist.id.str.starts_with("CL_"))
a.intersect(b).ids()
a.difference(b).ids()
```

### Joining a query into SQL of your own

The DSL roots at an sdmxlib relation and navigates sdmxlib's edges, and
`is_in` takes a relation only as a one-column membership filter. To select
columns from your table **and** `sdmx.*`, compile the sdmx half with
`to_sql()`, which returns a standalone statement, and join it:

```python
conn = reg.connection
conn.execute("CREATE OR REPLACE TABLE my_datasets (name TEXT, flow_urn TEXT)")

applicable = reg.query(q.hierarchy_applicability).filter(q.hierarchy_applicability.rank == 1)
compiled = applicable.select(dataflow_urn=q.hierarchy_applicability.dataflow.urn).to_sql()

sql = f"SELECT d.name FROM my_datasets d LEFT JOIN ({compiled.sql}) h ON h.dataflow_urn = d.flow_urn"
print(conn.execute(sql, compiled.params).fetchall())
```

```
[]
```

Two contracts make this work:

- **`to_sql()` output is standalone.** It references no temporary the caller
  does not own, so it can be spliced anywhere the `sdmx.*` tables are visible.
  That is why a wide `is_in` still binds its values rather than registering
  them — registering would hand you a statement naming a relation that does not
  exist outside the call.
- **The parameters come with it.** `compiled.params` are positional, so splice
  the SQL and concatenate the binds in the same order.

The projection can carry an aggregate, so one statement replaces a walk:

```python
under = q.hcode.ancestors.any(q.hcode.id == "CA")
pairs = applicable.select(
    dataflow_urn=q.hierarchy_applicability.dataflow.urn,
    codes=q.hierarchy_applicability.hierarchy.hcodes.code.id.list(where=under),
).to_sql()
print("codes" in pairs.sql)
```

```
True
```

!!! note "The supported seam between schemas"

    The path language deliberately leaves out cross-schema joins, which would
    mean knowing about tables it does not own. `reg.sql()`,
    `sl.schema` and this splice are the supported way across, and
    `sdmxlib.sql.SqlRelation` is the protocol they agree on.

### Keying a cache on a query

Two queries built by different call sequences can mean the same thing —
`filter(a).filter(b)` and `filter(b).filter(a)` differ as values and emit their
clauses in a different order. `canonical_key()` is what they agree on:

```python
p1 = q.codelist.agency == "ESTAT"
p2 = q.codelist.id.str.starts_with("CL_")

a = reg.query(q.codelist).filter(p1).filter(p2)
b = reg.query(q.codelist).filter(p2).filter(p1)

assert a.canonical_key() == b.canonical_key()
assert a.to_sql() != b.to_sql()          # the text still differs
```

The key is **deterministic across processes** (no `hash()`, no `id()`, no set
iteration), hashable, and comparable — so it can be persisted, compared
between workers, or used as a cache key by a downstream service.

Do not use `==` on a query or a predicate for this: it raises, and it raises
deliberately, because the version that did not raise was silently wrong.

Raw SQL is a **terminal**, not a step: `reg.sql(...)` returns a lazy `RawSql`
that does not splice into a structural query. Nothing runs until
`.collect()`, which returns a polars frame.

```python
reg.sql("SELECT urn FROM sdmx.artefact WHERE resource_id LIKE $p", p="CL_%").collect()
```

Values are bound — `$name` is rewritten to a placeholder and checked against
DuckDB's own parser, so a query that would not parse fails at `reg.sql()`
rather than at execution. The layout is unprotected: a renamed column turns a
working statement into a runtime error.

`sl.schema` covers the layout. It is reflected from the DDL, so a stale name is
an `AttributeError` at import rather than a broken query in production:

<!-- test: raises -->
```python
sl.schema.code.code_id       # "sdmx.code.code_id"
sl.schema.code.nope          # AttributeError, listing the columns that exist
```


## See also

- [Structures as Frames](structures_as_frames.md) — `select` / `group_by`
- [Inspecting DSDs](inspecting.md) — navigating one resolved artefact
- [Local Registry](../local_registry_tutorial.md) — building the catalogue this queries
