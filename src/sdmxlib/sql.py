"""The cross-library SQL seam — used across the StatCat Suite.

**One primitive.** `SqlRelation`: a producer of ``(sql, params)`` for use as a
subquery. A 50k-row hierarchy traversal compiles to one ``IN (SELECT …)``
clause with no parameter explosion, and that is the load-bearing pattern for
cross-library lazy composition.

`Predicate`, `CompileContext`, `RowKind` and `ColumnSql` were here too, and
#306 retired all four. They were the seam of the four hand-written DSLs: a
foreign object exposing ``to_sql(ctx)`` could be handed to ``filter()``, and
`CompileContext` was the state threaded through the pass it joined. The path
language has no such seam — `filter()` takes `Pred[R]`, whose row kind is
checked by the checker and whose compilation is one declared edge graph, so
there is nothing for a foreign predicate to be assignable to.

That is a real narrowing and worth naming rather than burying: a downstream
library can no longer inject a *predicate*. It can still inject a *relation*,
which is the composition that was actually used — `SqlRelation` has consumers
in `rest.py`, `is_in`, and statcatlib — and which works precisely because a
single-column subquery carries no row kind to disagree about.

A bundle laid out as a single DuckDB file co-locating ``sdmx.*``,
``statcat.*``, and the DuckLake catalog tables is what makes `SqlRelation`
composition routinely useful across sdmxlib + statcatlib: subquery tables are
always visible on the same connection.
"""

from typing import Protocol, runtime_checkable

__all__ = ["HasSource", "SqlRelation"]


@runtime_checkable
class SqlRelation(Protocol):
    """A lazy relation usable inside ``WHERE col IN (...)`` or as a join input.

    Producers — `sdmxlib.query.query.Select`, `AncestorMap`, and the statcatlib
    query builders — expose their compiled SELECT as a ``(sql, params)`` pair.
    The consumer splices the SQL inline as a subquery and concatenates the
    params with the outer query's binds, so an N-row traversal becomes one
    ``IN (SELECT ...)`` clause with no parameter explosion.

    The subquery's tables must be visible on the consumer's connection. For
    `DuckDBDataStore` (which shares its connection with `Registry`), codelist
    relations compose automatically because both sides see the same
    ``sdmx.code`` table. For statcatlib's ``Catalog``, the same applies when the
    bundle uses Topology B (one DuckDB file holding sdmx.*, statcat.*, and the
    DuckLake catalog).
    """

    @property
    def columns(self) -> tuple[str, ...]:
        """Names of the columns ``to_sql`` produces, in order.

        Declared so arity is **checkable** rather than promised in prose. A
        single-column relation is what ``IN (<subquery>)`` needs; a rollup is
        two columns and is used as a join. Consumers that require one column
        should say so and reject the rest, instead of emitting SQL that fails
        at the database with a column-count error.
        """
        ...

    def to_sql(self) -> tuple[str, list[object]]:
        """Return ``(sql, params)`` — a SELECT producing `columns`."""
        ...


@runtime_checkable
class HasSource(Protocol):
    """A relation that can say *which database* its SQL reads.

    `SqlRelation` requires the subquery's tables to be visible on the
    consumer's connection, and said so only in prose above — so a relation
    built on one database and spliced into a query on another produced a
    plausible wrong answer rather than an error. Measured before it was fixed:
    two registries whose codelists disagreed, and the filter silently returned
    the *consumer's* answer.

    Optional by design, and deliberately not folded into `SqlRelation`. Making
    it required would break every existing producer, and a foreign relation
    that cannot answer is not necessarily wrong — Topology B puts ``sdmx.*``
    and ``statcat.*`` on one connection precisely so cross-library composition is
    legitimate. So: a producer that *can* identify its source gets checked, and
    one that cannot is trusted, which is exactly the state before this existed.
    """

    @property
    def source(self) -> object:
        """An opaque token identifying the database this relation reads.

        Compared with ``is``, never parsed. Two relations sharing a token can
        be composed; two with *different* tokens cannot, and a consumer must
        refuse rather than emit SQL that reads one of them.
        """
        ...
