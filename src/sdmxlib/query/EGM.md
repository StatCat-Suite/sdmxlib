# sdmxlib.query

## Overview

`sdmxlib.query` is the structural path language over the SDMX storage graph.
Each `sl.q` accessor yields typed column expressions that build an immutable
`Query[R, A]`. A query compiles to parameterised SQL by planning joins over a
declared edge graph, and its terminals run that SQL on DuckDB, returning
artefacts, polars frames or Arrow streams. The package also holds REST
pushdown planning, fetch-then-materialise execution, observation queries
(`ObsQuery`) and a raw-SQL escape hatch (`reg.sql()`).

## Interface

- `Query[R, A]` (`query.py`): immutable builder with `filter`, `qualify`,
  `order_by`, `after`, `limit`, `offset`, `intersect`, `difference`, `select`,
  `group_by` and `agg`. Its entry point is `reg.query(sl.q.<relation>)`.
  The module-level `union(first, *rest)` combines queries.
- Terminals on `Query` (`query.py`): `collect`, `first`, `one`,
  `one_or_none`, `get`, `count`, `exists`, `ids` and `urns` return artefacts or
  scalars. `frame` returns a polars DataFrame, `stream` a pyarrow
  `RecordBatchReader`, `preview` a preview frame and `page` a cursor-based
  `Page`.
- `Query.to_sql`, `explain`, `columns` and `canonical_key` (`query.py`):
  return the `CompiledQuery`; the compiled SQL with a `-- binds:` line and, for
  a REST-backed query, a line saying it fetches first (no DuckDB `EXPLAIN`);
  the column list (from `DESCRIBE`); and a cache key.
- Result types (`query.py`): `Select` is a projection, with its own
  `order_by`, `limit`, `offset`, `unique`, `having`, `to_sql`, `explain`,
  `columns`, `collect` (a polars frame) and `stream`; `Select.filter` raises.
  `Grouped` comes from `group_by` and offers `agg` and `len`. `Page` is one
  page that has already run, with its `urns`, next `cursor` and `collect`.
- `_Q` and the `*Row` row kinds (`accessors.py`): the `sl.q` root, with one
  lowercase singleton per relation (`dataflow`, `dsd`, `codelist`, `code`,
  `component` and more). A path carries the root's row kind. `_Many*` classes
  are to-many (`FanOut`) variants.
- `Col`, `Str`, `Num`, `Bool`, `Arr`, `Temporal`, `Label`, `Vocabulary`,
  `Version` and `Untyped` (`_expr.py`): typed column expressions that produce
  predicates.
- `Pred`, `Compare`, `InSet`, `InRelation`, `RowIn`, `BoolOp`, `Not` and
  `Quantified` (`_expr.py`): the predicate AST. `Agg`, `AggPred`, `Win`,
  `WinPred`, `Cursor` and `OrderKey` cover aggregates (with `where=`), windows
  and keyset pagination. `FanOut`, `many` and `edge` define to-many hops and
  late-bound edges. `FanOut` offers `.any()` (`EXISTS` over a satisfying
  child), `.every()` (`NOT EXISTS` over a violating child) and the `list`,
  `unique` and `count(where=)` aggregates. `Quantified.quantifier` is `"any"`
  or `"every"`.
- `Plan` and `scanned` (`_plan.py`): what a query says, kept apart from how it
  runs.
- `compile_select`, `compile_page`, `compile_projection`, `Sql` and
  `CompiledQuery` (`_compile.py`): predicates to SQL.
- `resolve_path`, `Hop`, `JoinPlan`, `plan_joins` and `PathError`
  (`_joins.py`): paths to hops to a join tree, with shared prefixes merged.
- `Edge`, `Facet`, `EDGES`, `FACETS`, `edges_from`, `edges_into` and
  `facets_of` (`_edges.py`): the declared edge graph, one `Edge` per storage
  foreign key. At runtime a hop's cardinality is read off its direction
  (`Hop.to_many`): forward is to-one, backward is to-many, and a `member_of`
  edge is to-many both ways. `tests/unit/test_query_edges.py` checks the
  declarations against the schema's key constraints.
- `ObsQuery`, `Observations` and `ObsRow` (`observations.py`): the path
  language over a dataflow's observations. Provides `col`/`text`/`num`,
  `pivot`, `validate`, `filter_components` and
  `sink_parquet`/`sink_csv`/`sink_json`. `ids`, `urns`, `page` and the
  artefact terminals raise `TypeError`, since an observation is a row with no
  id.
- `RawSql`, `RawSqlError` and `prepare` (`raw.py`): `reg.sql()`. `$name`
  becomes `?`, and the result is checked against DuckDB's own parse.
- `schema` and `Table` (`schema.py`): `sl.schema`, qualified names for raw SQL,
  reflected from the DDL.
- `plan_program`, `Capability`, `SDMX_REST`, `DUCKDB`, `Program`, `Push` and
  `PushdownError` (`_pushdown.py`): what a backend can evaluate, and the
  ordered work a `Plan` becomes for it. `_rest.py:requests_for` turns pushed
  work into SDMX REST structure requests; `DataRequest`, `data_request_for`
  and `requests_for_derived` turn an observation query into a `/data`
  request.
  `_closure.py` works out what a fetch must contain, and `_rebase.py` re-roots
  a predicate onto a shorter path.
- `fetch`, `fetch_into`, `StructureSource` and `Store` (`_execute.py`).
  `fetch` issues one request and returns the `StructureMessage`.
  `fetch_into` issues every request a plan needs, calls `store.add` on each
  artefact (skipping one that is not `SupportsUrn` or whose `add` raises
  `ValueError`) and returns the `Request`s. The query itself runs when
  `Query._cursor()` calls the `_prepare` hook that `api/registry.py`
  installs.
- `narrow_limit`, `advance_window` and `canonical_key` (`_common.py`): the
  refinement laws.

## Invariants

- Builder methods return a new query and run nothing. SQL runs only in
  terminals.
- A path that crosses a to-many hop compiles to a correlated `EXISTS`
  subquery, never a join, so the row count of the root relation is preserved.
  One exception skips the subquery: a forward membership hop with no facet,
  inside `any`, whose only leaf is an equality on the joined key compiles to
  `? = ANY(s0.ancestor_ids)` (`_member_collapse`).
- Predicates inside one `.any()` share one correlated subquery. Predicates
  outside it get their own.
- Each scope builds one join plan covering all its paths, merging shared
  prefixes.
- Values are always bound as parameters (`execute(sql, list(params))`).
  Structural identifiers come from the edge graph. The observation plane takes
  table and column names from the `DataflowBinding` and its sidecar, and
  `_partition_values` and `_scope_clause` interpolate binding and schema
  names.
- All compilation routes through `plan_program(self.plan, DUCKDB)`
  (`Query._program`). A program of more than one step raises
  `NotImplementedError`.
- Projecting across a to-many path is refused (`refuse_to_many_projection`).
  `CrossSourceError` is raised by `filter` with an `is_in(relation)` from
  another source, by `intersect`/`difference`, and by `union`. A relation
  that reports no source is trusted.
- A REST-backed query over an `artefact` root sees only what its own fetch
  brought: `_fetch_scope` keys the fetch by `canonical_key(plan)`, and the
  compiler adds a scope clause (`_scope_clause`).
- `RawSql` is a terminal and cannot be spliced into a structural query.
- A REST-backed query has no local evaluator. `fetch_into` fills the store,
  and the original query then runs there through the compiler. If a fetch leaves
  out part of the closure, the query silently returns empty, so
  `_closure.py` must cover every crossing.
- A terminal over a scanned SDMX-CSV file converts what DuckDB's CSV reader
  raises while the query runs (`@csv_errors`, and `_converting` on a
  stream): unreadable data is `SdmxParseError`, a value that does not fit is
  the "does not fit its component's type" `ValueError`. Any other DuckDB
  error keeps its type (`sdmxlib._read_errors`).
- The differential oracles in `tests/support/oracle.py` and
  `tests/support/hierarchy_oracle.py` share no code with this package.

## Side Effects

- Terminals run DuckDB queries through `self._cursor()`. They return Arrow
  readers or polars frames in `query.py` and `observations.py`.
- `_cursor()` calls the `_prepare` hook first, so every terminal on a
  REST-backed query, `columns` included, performs its HTTP fetch before the
  SQL runs.
- `Query.columns` and `RawSql.columns` run `DESCRIBE`.
- `schema._tables()` reads the DuckDB catalog once and caches it with
  `functools.cache`. `_accessor_roots` and `_accessor_names` are cached the
  same way.
- `_execute.fetch` makes REST requests through `StructureSource.get_artefact`
  and `get_all`. `fetch_into` adds the results through `Store.add`.
- `ObsQuery.sink_parquet`, `sink_csv` and `sink_json` write files.

## Verification

- Run: `just test` (unit tests) and `just check` (lint and typecheck).
- Run `just test-properties` after changing the query language. It runs the
  differential properties at 20x examples and is not part of `just ci`.
