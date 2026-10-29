# Querying observations: evidence

> Records what was measured before and while the observation root was built:
> the data plane's SQL-text API and the one downstream call site that passed
> it user input (#297, opened 2026-08-10), the two fences built for option B and
> removed (#297; the text API itself went in #329 on 2026-08-17), and the
> sidecar-join cost on the 2021 Census Profile bundle (#388, 2026-08-28 to
> 2026-09-05). The API it describes no longer exists. The current design is
> [`data-plane-query.md`](../data-plane-query.md); section numbers below are
> that document's as they stood when this was written.

## 1. The text API this replaced

*(Written when it was. The API described here was removed in #329.)*

`DataStore.query` took `where: list[str]`, `columns: list[str]` and
`order_by: str`, and spliced all three into a statement as text.
`ComponentFilter` takes *values* but its `to_where_clauses` returns
*fragments*. [#257](https://github.com/StatCat-Suite/sdmxlib/blob/main/history/gitlab/issues/257.md) looked at this
and chose to document rather than validate, on a stated premise: consumers are
first-party and compose these from constants in application code.

[#297](https://github.com/StatCat-Suite/sdmxlib/blob/main/history/gitlab/issues/297.md) made measuring that premise a
prerequisite, and the downstream consumers were available to read, so it was
directly answerable.

| | |
| --- | --- |
| call sites passing `where` / `order_by` / `columns` **to the library** | **1** |
| of those, built from user input | **1** |

The site is a downstream SDMX REST service's HTTP data route: `where` comes
from `c[DIM]=op:value` query parameters, `order_by` from `sort=`. The premise
was false.

**That consumer had to build option C itself**, in application code: a fixed operator
table, component ids resolved through `binding.dimension_column_map` with a 400
on unknown, values bound as `?`, LIKE metacharacters escaped. It then
*bypasses* the library entirely for anything needing projection or
aggregation. Milestone %15's rule — every bypass is a specification — lands
here.

### A second consumer with its own query builder

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

## 2. The fences built and removed for option B

Both stronger fences were built, run against the suite, and removed. They were
kept as tests in `tests/unit/test_data_plane_fence.py`, asserting the
compositions that must keep working, until
[#329](https://github.com/StatCat-Suite/sdmxlib/blob/main/history/gitlab/issues/329.md) removed the text API they
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

That is the argument for C, which accepts no text at all.

## 3. The sidecar join, emitted unconditionally (#388)

*(From §5 of the design, "C3's reachability: four couplings", where it
followed the coupling "The sidecar join has no representation".)*

> **And once it did, it was emitted unconditionally —
> [#388](https://github.com/StatCat-Suite/sdmxlib/blob/main/history/gitlab/issues/388.md).** A hop join is
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
> **Declaring the key instead does not work.** Declaring
> the key so DuckDB elides the join itself would also help queries that *do*
> read a sidecar column and then aggregate. It does not: a `UNIQUE INDEX` on
> the sidecar's key left the hash join in the plan and 2.6 s on the clock, and
> a `PRIMARY KEY` is not even expressible — `ALT_GEO_CODE` is nullable on the
> real bundle. Whatever closes the referenced case, it is not a constraint.
