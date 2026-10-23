# Query composition: the refinement principle

> Companion: `query-language.md` covers how relations are *named and joined*.
> This file covers how clauses compose *within one query*.

There is one query class, `Query[R]` (`src/sdmxlib/query/query.py`), and every
relation in `sl.q` — codes, hierarchy placements, the artefact spine,
reported metadata values, observations — is a root of it. Before #306 there
were four DSLs, each with a composition law per clause and no stated
principle, so each new clause was a fresh argument and the answers drifted.
This file states the rule the individual decisions are instances of. Having
one class makes it hold in one place: a clause added for one root is added
for every root.

It was written down because the rule was already *implemented* and never
*written*, so nobody could check the behaviour against it. #264 shipped a first implementation that broke it and was caught only
by a hand-written test.

## The principle

> **Every row-set composition refines the query. Nothing a later caller adds
> can undo what an earlier call established.**

Queries here are built by more than one party — a
helper scopes a query to an agency, a federation layer caps it, a caller pages
it — and refinement is what makes that safe. Under last-wins semantics a
caller's `.limit(10_000)` silently undoes a helper's `.limit(100)`, which is
the wrong direction for the mistake to run.

| clause | law | why |
| --- | --- | --- |
| `filter(p)` | AND | narrows rows |
| `qualify(w)` | AND | narrows rows, by a window predicate |
| `intersect(other)` / `difference(other)` | semi-join / anti-join | narrows rows |
| `after(cursor)` | raises the floor | narrows rows (keyset paging) |
| `limit(n)` | `min` | lowers the ceiling |
| `offset(n)` | raises `start`, shrinks the length | raises the floor |
| `select(...)` | not a row refinement — returns a `Select`, whose `filter` raises |
| `group_by(...)` | not a refinement at all, changes shape — raises after `order_by`/`limit` |

A tree walk is not a clause of its own. What `descendants_of(id)` used to be
is a filter over an edge — `cl.codes.filter(sl.q.code.ancestors.id == "EU")`
is every code under `EU` — so it is governed by the `filter` row and needs no
law of its own (`query-tree-paths.md`).

The last two rows are the test of whether the principle is real: both errors
predate it and both fall out of it, rather than being bolted on afterwards.
`select` is refused with *"Filter first, then select()"*; `group_by` with
*"a limit before grouping aggregates a truncated set"*.

The window arithmetic is `narrow_limit` and `advance_window` in
`src/sdmxlib/query/_common.py`. Each clause evolves a new frozen `Plan`
(`_plan.py`), so a query is a value and composing one never mutates another.

## The three laws

Generated in `tests/unit/test_properties_query_laws.py` over three roots of
`Query` — `q.code` (through `LazyCodelist.codes`), `q.hcode` and
`q.artefact` — chosen because they have generative strategies. Reaching more
roots would test new strategies rather than the laws, which are one
implementation.

**Monotonicity.** `rows(c(q)) ⊆ rows(q)`. The one that matters, and the one
#264's first `(start, length)` window broke: `base.offset(8)` returned rows
`base` had excluded.

**Idempotence.** `c(c(q)) == c(q)` for the same argument. Stating a constraint
twice states it once.

**Every ordering either commutes or is refused.** The strongest usable form,
and stronger than "these pairs commute": it leaves no third category where
order changes the answer without saying so.

`test_every_composition_method_is_classified` guards the class rather than
the instances: every public `Query` method returning `Self` must be assigned a
law (row-set or preserving), so the next `limit`-shaped method cannot arrive
untested.

## Two exceptions to the principle

**Ordering is not a row-set property.** `order_by` permutes; it does not change
membership. Applying refinement to it by symmetry was a category error, and
#294 was the resulting defect: appending keys made a caller's `order_by` a
silent no-op whenever the earlier key was unique.

It now **replaces**, takes several keys in one call, and clears when called
with no arguments — matching Django's `order_by`. Direction is per key
(`sl.q.code.depth.desc()`) rather than a `descending=[...]` list parallel to
the keys: SQLAlchemy, ibis and Django all mark the key, and a parallel array
puts the direction at a distance from what it describes.

`with_annotations` was the second preserving clause and had the same shape.
#306 removed it — `collect()` hydrates whole artefacts in one batch, so what
is loaded is no longer a query clause — leaving `order_by` as the only one, and
it is asserted to be row-set *preserving*, which is stronger than monotone.

**A default order is neither.** `codelist.codes` comes back in declaration
order because a codelist *has* one, and that is a fact about the relation
rather than a decision by a caller: it does not block `group_by`, and the
first `order_by` replaces it outright. Conflating the two would have made
`codelist.codes.group_by(...)` impossible, which is how the distinction was
found.

**And declining an order is a third thing again — `order_by(None)` (#389).**
Clearing the keys leaves the root-key *tiebreak*, so an unsorted result is
stable between calls rather than merely unspecified; paging depends on that,
and #264 is what happens without it. That clause is free on a codelist and was
**5.4x the scan it decorated** on a 4.26 M-row observation partition — 337 ms
against 62 ms, for an order most callers of `frame()` never look at.

So there are three states, not two:

```python
q.order_by(sl.q.code.id)   # sort by id, then the tiebreak
q.order_by()               # clear the keys, keep the tiebreak
q.order_by(None)           # no ORDER BY at all
```

`order_by(None)` is SQLAlchemy's spelling — *"All existing ORDER BY criteria
may be cancelled by passing `None`"* — and the survey behind this table is why
it is not a flag on `frame()`: **no** Python query or dataframe library puts an
ordering knob on a materialisation call. Not SQLAlchemy's `.all()`, Django's
`list()`, ibis's `.to_pandas()`, polars's `.collect()`, DuckDB's `.fetchall()`,
PySpark's `.collect()` or Dask's `.compute()` — those carry execution concerns
only. The reset is universally the *same builder method with an empty or `None`
argument*, which is also why it is not a new verb like `unordered()`.

It is a `Plan` field (`unordered`), so it composes, keys under
`canonical_key`, and shows in `repr` — and it **closes the query to `limit`,
`offset`, `after` and `page`**, because a window over an unspecified scan
order silently skips and repeats rows between calls, which is precisely what
the tiebreak exists to prevent. The two are made exclusive rather than
documented apart. A later `order_by(...)` reopens it.

**`limit` and `offset` do not commute.** Narrowing, idempotence and
commutativity are not simultaneously available for that pair. SQL buys
commutativity by making the limit a length measured from the offset — which is
exactly what lets a later `offset` slide the window past rows an earlier
`limit` excluded. We chose narrowing:

```python
q.offset(5).limit(10)   # ten rows starting at five
q.limit(10).offset(5)   # five rows — of the first ten, skip five
```

Both read left to right and both are useful. It is the only order-sensitive
pair in the language, and it is asserted *non*-commutative rather than merely
excluded from the commutativity property, because a carve-out nobody checks is
a place to hide a bug.

## The guard that makes the principle true

The principle was **false** before #296. Every clause compiles into one
`SELECT`, so a `filter` added after a `limit` lands in the `WHERE` and runs
before it:

```
q.limit(1)                    ['0']
q.limit(1).filter(id != '0')  ['1']    ← a row the query being refined did not contain
```

That violates the principle. So `filter`, `qualify` and `order_by` raise
`SliceError` on an already-sliced query. Both readings of the composition are
defensible ("filter the whole set, then cap" and "of the first n, the ones
matching"), and the language refuses rather than picking one silently.

Django does the same, for the same reason — `Cannot filter a query once a slice
has been taken`. Unlike Django we can name the other reading, because `is_in`
already expresses it — a sliced query is a relation, and
`base.filter(sl.q.code.id.is_in(sliced.select(sl.q.code.id)))` is "of the
first n, the ones matching" — so the error points at a spelling that works
rather than only saying no.

`limit` and `offset` after a slice stay legal: they *are* refinements, and
paging is made of them.

## Two families, two names

`order_by` is the SQL query language. `ItemList` and `ArtefactList` keep
**`sort_by`**, by design: they
sort in Python, take a key function, and mirror `sorted(key=, reverse=)` —
including a `descending=` flag, which the SQL side replaced with a per-key
`.desc()`.

```python
dsd.dimensions.sort_by(lambda d: d.id, descending=True)   # in memory
cl.codes.order_by(sl.q.code.id.desc())                    # compiled to SQL
```

Different mechanisms, different vocabularies, and the name is the signal. The
one place they can be confused is `order_by("id")` on a `Query` — a habit
from the string-keyed family, and from pandas — which raises a `TypeError`
naming what a column reference looks like. (A `Select`'s `order_by` *does*
take a string: there it names an output column of the projection, which has
no accessor.)

## The intermediate representation (#273)

**The `Plan` is the intermediate representation.** `src/sdmxlib/query/_plan.py` holds what a query
*says* — root, predicates, qualifiers, order keys, default order, the
`unordered` flag, cursor, limit, offset — and `Query` holds a `Plan` plus how
to run it. `query-pushdown.md` §2 argues why that is enough: the one
transformation a backend is owed (evaluating a path prefix separately and
applying it as set membership, §9.1 there) consumes `Plan`s and produces
`Plan`s, so no separate node set was needed.

This section once answered *no*, and that answer was right for its premise.
It is kept in outline because the reasoning still explains what an IR is
*not* for here.

The original argument was about **expressiveness**. #273 proposed the node set
`Scan | Filter | Project | Join | Aggregate | Sort | Limit | Distinct` on the
grounds that canonicalization, normalization and rewrites had nowhere to live.
Measured against the query shapes collected from six consuming applications,
13 of 18 were already expressible, and the five that remained were feature
decisions an IR would not deliver:

| gap | what it needed | now |
| --- | --- | --- |
| `COALESCE(name[?], id) AS label` | expression-valued projection | not built; label in polars after `collect()` |
| `list(annotation.title)` per code | array aggregation over a side table | `c.list(...)` — a correlated list subquery, no join, no grouping |
| cross-schema join (`statcat.dataset ⋈ sdmx.code`) | joins over caller tables | not in the language; `reg.sql()`, or a `SqlRelation` into `is_in` |
| `PIVOT` | a reshaping node | not built; pivot in polars |
| `ROW_NUMBER() OVER` | window functions | `qualify(col.rank(...).over(...) == 1)` |

#270's canonical form was then built without an IR (`canonical_key` in
`_common.py`) and fitted, which settled the expressiveness question.

**Two things changed the premise, neither of them expressiveness.**

*Cross-relation projection within the sdmx schema* was a sixth gap the table
above missed — its "cross-schema join" row is about *caller* tables. A
projection now spans relations through paths:
`reg.query(sl.q.dataflow).select(sl.q.dataflow.id, dsd=sl.q.dataflow.dsd.id)`
reads two relations through a to-one hop, and a to-many hop projects only
through an aggregate, so it never multiplies rows.

*The library has two query backends.* With DuckDB and SDMX REST as
simultaneous targets the question became where a query **runs** — pushdown,
capability declaration, and a local residual — which is orthogonal to
expressiveness. That is what extracting `Plan` from `Query` was for, and
`query-pushdown.md` is the design as built.

### The data plane

#273 correctly identified the data plane as where SQL text was a hazard:
`local/data_store._build_query` interpolated caller-supplied `where`,
`columns` and `order_by` **as SQL text**, a statement-injection surface #257
had decided to document rather than validate. That method is gone. A
dataflow's observations are now a root of the same `Query`
(`reg.obs(flow)` → `ObsQuery`, `src/sdmxlib/query/observations.py`),
so the data plane accepts field references and literals and nothing else,
like every other root (`data-plane-query.md`). `ComponentFilter` reaches it
structurally through `obs.filter_components(...)`;
`ComponentFilter.to_where_clauses` survives only inside
`builders/contentconstraint.py`, rendering bound parameters.

The structural language's safety property is that it **never accepts SQL text
in the language** — a caller supplies field references and literals, and #272
type-checks even those. Raw SQL is not banned; it is a separate, visibly
different terminal, `reg.sql(...)` (`query/raw.py`), which does not splice
back into `sl.q`. Splicing text in would give the safe surface a
text-accepting path, which is what the ban protects against, whether the
text arrives as a string or wrapped in an object.

### Where normalization sits, and its laws

Nowhere yet, and it needs no home until a second rewrite exists. One of the
brief's six is done (`In(d, []) → False`, compiled as `1 = 0`), one is
outstanding (`In(d, [c]) → Eq(d, c)`; we emit `= ANY(?)` with a one-element
list), and four have nothing to act on. When there are enough to be worth a
pass, it belongs between construction and emission, with
`normalize(normalize(q)) == normalize(q)` as the law and
`tests/support/sql_corpus.py` as the equivalence check.

## Alternatives considered

Prior art was checked rather than recalled, and it does not settle the
question — among declarative peers exactly one law is unanimous:

| clause | SQLAlchemy 2.0 | Django 6.1 | ibis 12 | here |
| --- | --- | --- | --- | --- |
| `filter` / `where` | AND | AND | AND | AND |
| `order_by` | append | replace | **prepend** | replace (#294) |
| cancel the order | `order_by(None)` | `order_by()` | — | both, and they differ (#389) |
| `limit` | last-wins | clamp | nest subquery | `min` |
| `offset` | last-wins | accumulate (slice) | nest | raise the floor |

So there is nothing to copy, and an internal principle is required rather than
a reference. Two observations that did carry weight:

- **Django clamps its limit** (`qs[:5][:10]` is five rows), so refinement is
  not our invention. SQLAlchemy's last-wins is the outlier, and it is a
  consequence of being a statement builder where each clause is a slot — the
  tell is that `where(None)` compiles to `AND NULL`.
- **polars is not the reference for composition.** Its vocabulary is borrowed
  here, but it is a *pipeline*: `lf.head(3).filter(p)` and `lf.filter(p).head(3)`
  differ, where the equivalents here do not (the second is refused). Borrowing
  the names without the laws is the standing hazard — see #222, #255, #261 and
  #292, four defects found in exactly that seam.
