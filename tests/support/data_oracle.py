"""A Polars reference interpreter for `ComponentFilter` (#258).

The two structural DSLs have oracle-backed differential properties (#248/#249).
The data plane — the builder that turns SDMX query parameters into
``SELECT … WHERE col IN (?)`` over an observation table — had none, despite
being *more* differential-testable than they are: "which observations match
this filter" is a one-line Polars expression over the same frame the fixture
built.

`rest.py` was at 100% line coverage while the empty-``IN ()`` bug (#256) sat in
it untouched. Same lesson as `code_query`, on a module nobody had pointed a
generative test at.

## The semantics being modelled

Deliberately small, because the IR is deliberately small: **AND across
components, OR within a component.** There is no `Or` or `Not` combinator to
model, so this is not a recursive ADT interpreter — building one would be
modelling expressiveness the library does not have.

- ``None`` on a dimension is a wildcard: no constraint.
- ``[]`` is *not* a wildcard. It is the empty permitted set, which no
  observation satisfies, so it selects nothing (#256).
- A `SqlRelation` value resolves to the id set the relation produces, and is
  then an ordinary OR set.

## NULL semantics, which the library had not decided

A nullable attribute filtered with ``IN (...)`` does not match rows whose value
is NULL, because SQL's ``NULL IN (…)`` is NULL rather than false. That is the
same three-valued-logic trap the structural side already met — ``parent_id != x``
excludes roots because ``NULL != ?`` is NULL — and #258 predicted the data
plane would have it too.

Measured rather than assumed: it does, and this interpreter matches it by
using `pl.Expr.is_in`, which drops nulls the same way. Written down here
because nothing else in the codebase states it, and a reader would reasonably
expect "GEO_LEVEL is not FED" to include rows with no GEO_LEVEL at all.

Shares no code with `rest.py`, per the oracle rules in CLAUDE.md.

Test-only; not packaged with sdmxlib.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

from sdmxlib.sql import SqlRelation

if TYPE_CHECKING:
    import duckdb

    from sdmxlib.rest import ComponentFilter


def resolve(value: list[str] | SqlRelation, conn: duckdb.DuckDBPyConnection) -> list[str]:
    """The permitted set for one component, eager or lazy.

    A relation is executed here rather than modelled, because the *relation's*
    correctness is `code_query`'s problem and already has its own differential
    property. What this file is testing is whether the data plane splices it in
    correctly — so the oracle needs the set, not a second implementation of how
    it was computed.
    """
    if isinstance(value, SqlRelation):
        sql, params = value.to_sql()
        return [row[0] for row in conn.execute(sql, params).fetchall()]
    return list(value)


def matching_rows(
    frame: pl.DataFrame,
    cf: ComponentFilter,
    *,
    dimension_column_map: dict[str, str],
    attribute_column_map: dict[str, str],
    conn: duckdb.DuckDBPyConnection,
) -> pl.DataFrame:
    """Rows of *frame* the filter selects, by direct evaluation.

    One `pl.Expr` per constrained component, ANDed. No SQL, no query builder,
    no shared helper with the thing under test.
    """
    mask = pl.lit(value=True)

    for dim_id, allowed in zip(cf.dimension_ids, cf.dimensions, strict=True):
        if allowed is None:
            continue  # wildcard
        column = dimension_column_map[dim_id]
        mask = mask & pl.col(column).is_in(resolve(allowed, conn))

    for attr_id, allowed in cf.attributes.items():
        column = attribute_column_map[attr_id]
        # `is_in` is false for null, matching SQL's `NULL IN (...)` being NULL
        # and therefore not-true. See the module docstring.
        mask = mask & pl.col(column).is_in(resolve(allowed, conn))

    return frame.filter(mask)
