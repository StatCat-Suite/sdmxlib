"""Custom-SQL seam for `DuckDBDataStore` — schema reflection + query planning.

Most callers should go through `DuckDBDataStore.obs`, and the ones
who cannot should reach for `LocalRegistry.sql`, which binds `$name`
parameters and checks the statement against DuckDB's own parse. This module is
lower than either: schema reflection, and the join planning that
`obs()` itself uses to find a sidecar.

Two pieces:

- `SchemaInspector` — reflect a live DuckDB session's schema
  (``DESCRIBE``-backed, cached) and turn a `DataflowBinding` into a `QueryPlan`
  so consumers reuse the store's table-name convention and sidecar join-key
  derivation instead of re-implementing it.
- `QueryPlan` — the resolved ``FROM`` side of a binding's query (main table,
  optional sidecar ``LEFT JOIN``, join keys). Splice your own projection and
  ``WHERE`` onto ``plan.from_clause``.

Obtain an inspector via ``store.inspect()`` (it shares the store's cache with
the internal query path) rather than constructing one directly.

**Run the SQL on a session, not on ``store.connection`` (#300).** This module
used to show ``store.connection.execute(...)``, which is the store's *base*
connection: a single object shared by every thread, and the one the library
itself stopped executing on in #75/#80 because `DuckDBPyConnection` is not
thread-safe. It also carries different session state from the handle queries
run on — until #300 it had ``search_path = 'sdmx'`` while the handles had
none, so the same SQL worked on one and failed on the other.

``session()`` gives you the same handle the DSL uses, so settings, registered
relations and temp tables are shared with it, and the scope makes the
handle's lifetime visible. ``store.connection`` remains for setup, DDL, and as
the stable object other components derive their own handles from.

Example:
    insp = store.inspect()
    plan = insp.plan(binding)
    sql = (
        f"SELECT REF_AREA, printf('%.2f', OBS_VALUE) AS value "
        f"FROM {plan.from_clause} WHERE GEO_LEVEL = ?"
    )
    with store.session() as active:
        rows = active.execute(sql, ["CMA"]).fetchall()

    # or, for a single statement and no handle to hold:
    rows = store.execute(sql, ["CMA"])
"""

import threading

import duckdb
from attrs import frozen

from sdmxlib._duckdb import first
from sdmxlib.model.binding import DataflowBinding
from sdmxlib.session import cursor_for


class SidecarNotUniqueError(ValueError):
    """A series-attributes sidecar holds more than one row per join key.

    The sidecar is joined as `LEFT JOIN … USING (<keys>)`, so every extra row
    per key multiplies each observation it matches. Nothing errors downstream:
    `count()`, `collect()` and every aggregate over a sidecar column come back
    inflated by the fan-out factor, and every row looks valid.

    A `ValueError` because it is the same kind of refusal `plan` already makes
    for a sidecar sharing no columns — the binding names a table that cannot
    serve as its series attributes — so a caller catching that catches this.
    """


@frozen(kw_only=True)
class QueryPlan:
    """The resolved ``FROM`` side of a `DataflowBinding`'s query.

    Produced by `SchemaInspector.plan`. Consumers build their custom SQL by
    splicing a projection and ``WHERE`` onto `from_clause`:

        f"SELECT {cols} FROM {plan.from_clause} WHERE {predicate}"

    Attributes:
        main: Fully-qualified main table (``<database_role>.<table_location>``).
        sidecar: Fully-qualified series-attributes table, or ``None`` when the
            binding has no ``series_attributes_location``.
        join_keys: Columns common to main and sidecar (in main order) that the
            ``LEFT JOIN ... USING (...)`` is keyed on. Empty when no sidecar.
    """

    main: str
    sidecar: str | None
    join_keys: tuple[str, ...]

    @property
    def from_clause(self) -> str:
        """The ``FROM`` fragment: ``main`` alone, or ``main LEFT JOIN sidecar USING (...)``."""
        if self.sidecar is None:
            return self.main
        keys_sql = ", ".join(self.join_keys)
        return f"{self.main} LEFT JOIN {self.sidecar} USING ({keys_sql})"


class SchemaInspector:
    """Cached schema reflection + binding→query planning for a DuckDB session.

    Bound to a single DuckDB connection. ``DESCRIBE`` results are memoized per
    table for the inspector's lifetime — the schema of a read table is stable,
    and re-running ``DESCRIBE`` on every query is pure redundant work. The
    memoisation is single-flight (a build lock, double-checked): concurrent
    callers for the same table run ``DESCRIBE`` once, not once per thread —
    the store's read path is concurrent (per-thread cursors). When a
    table is rewritten (e.g. `DuckDBDataStore.write_observations`), call
    `invalidate` so a stale column list isn't served.

    Obtain one via ``store.inspect()`` so the internal query path and your
    custom SQL share the same cache; constructing one directly is supported but
    gets a private cache.
    """

    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self._conn = conn
        #: table -> ((column, DuckDB type), ...), one `DESCRIBE` each.
        self._columns_cache: dict[str, tuple[tuple[str, str], ...]] = {}
        #: (table, keys) -> (rows, distinct keys), for `key_counts`. Shares
        #: `_columns_cache`'s lock and lifecycle: both describe a table as it
        #: stands, and `invalidate` is the one signal that it changed.
        self._key_counts_cache: dict[tuple[str, tuple[str, ...]], tuple[int, int]] = {}
        #: (catalog, schema.table) -> partition columns, read from the DuckLake
        #: catalog. Shares the lock and `invalidate`: a rewrite can repartition.
        self._partitions_cache: dict[tuple[str, str], tuple[str, ...]] = {}
        self._lock = threading.Lock()

    def columns(self, table: str) -> tuple[str, ...]:
        """Column names for ``table`` via DuckDB ``DESCRIBE`` (cached).

        Args:
            table: A table reference DuckDB can ``DESCRIBE`` — bare name or
                ``schema.table`` / ``database.schema.table``.
        """
        return tuple(name for name, _ in self._described(table))

    def column_types(self, table: str) -> dict[str, str]:
        """``{column: DuckDB type}`` for ``table``, from the same cached ``DESCRIBE`` as `columns`.

        Args:
            table: As `columns` takes it.
        """
        return dict(self._described(table))

    def _described(self, table: str) -> tuple[tuple[str, str], ...]:
        """``(column, type)`` for every column of ``table`` — one ``DESCRIBE``, cached."""
        cached = self._columns_cache.get(table)
        if cached is not None:
            return cached
        with self._lock:
            # Double-checked: a prior holder may have built it while we waited.
            cached = self._columns_cache.get(table)
            if cached is not None:
                return cached
            # On this thread's handle, never `_conn` itself: concurrent
            # `execute()` on one handle overwrite each other's rows (#75).
            rows = cursor_for(self._conn).execute(f"DESCRIBE {table}").fetchall()
            described = tuple((str(r[0]), str(r[1])) for r in rows)
            self._columns_cache[table] = described
            return described

    def shared_columns(self, table_a: str, table_b: str) -> tuple[str, ...]:
        """Columns present in both tables, in ``table_a`` order.

        These are the join keys for a main⋈sidecar ``LEFT JOIN``.
        """
        cols_b = set(self.columns(table_b))
        return tuple(c for c in self.columns(table_a) if c in cols_b)

    def key_counts(self, table: str, keys: tuple[str, ...]) -> tuple[int, int]:
        """``(rows, distinct key combinations)`` of ``table`` over ``keys`` (cached).

        Rows with a NULL in any key are left out of both counts: `USING` never
        matches NULL, so repeated NULL-keyed rows cannot multiply a join, and
        refusing a table for them would be refusing a harmless one.

        A scan of the table — ~5 ms on the 2021 Census Profile edition's 619-row geography
        sidecar, ~37 ms cold — so memoised single-flight beside `columns`, and
        dropped by `invalidate` with it.

        Args:
            table: The table to count, as `columns` takes it.
            keys: The columns whose combinations are counted.
        """
        cache_key = (table, keys)
        cached = self._key_counts_cache.get(cache_key)
        if cached is not None:
            return cached
        with self._lock:
            cached = self._key_counts_cache.get(cache_key)
            if cached is not None:
                return cached
            tuple_sql = ", ".join(keys)
            not_null = " AND ".join(f"{k} IS NOT NULL" for k in keys)
            row = first(
                cursor_for(self._conn).execute(
                    f"SELECT count(*), count(DISTINCT ({tuple_sql})) FROM {table} WHERE {not_null}"
                )
            )
            counts = (int(row[0]), int(row[1])) if row else (0, 0)
            self._key_counts_cache[cache_key] = counts
            return counts

    def partition_columns(self, catalog: str, table_location: str) -> tuple[str, ...]:
        """The columns DuckLake partitions ``catalog.table_location`` by, in order (cached).

        Read from the table's DuckLake catalog, where `write_observations`'
        ``partition_by`` records them, so the layout has one source of truth
        that ships in the edition. Only identity partitions: one keyed on a
        transform (``year(ts)``) does not make the rows disjoint on the column
        itself. A table outside DuckLake, or unpartitioned, has none.

        Args:
            catalog: The attached catalog holding the table (a binding's
                located ``database_role``).
            table_location: ``schema.table`` within it.
        """
        cache_key = (catalog, table_location)
        cached = self._partitions_cache.get(cache_key)
        if cached is not None:
            return cached
        with self._lock:
            cached = self._partitions_cache.get(cache_key)
            if cached is not None:
                return cached
            partitions = _ducklake_partitions(cursor_for(self._conn), catalog, table_location)
            self._partitions_cache[cache_key] = partitions
            return partitions

    def invalidate(self, table: str) -> None:
        """Drop everything cached about ``table`` (e.g. after a rewrite)."""
        with self._lock:
            self._columns_cache.pop(table, None)
            for cache_key in [k for k in self._key_counts_cache if k[0] == table]:
                del self._key_counts_cache[cache_key]
            for cache_key in [k for k in self._partitions_cache if f"{k[0]}.{k[1]}" == table]:
                del self._partitions_cache[cache_key]

    def invalidate_all(self) -> None:
        """Drop everything cached about every table."""
        with self._lock:
            self._columns_cache.clear()
            self._key_counts_cache.clear()
            self._partitions_cache.clear()

    def plan(self, binding: DataflowBinding) -> QueryPlan:
        """Resolve ``binding``'s main/sidecar tables into a `QueryPlan`.

        Mirrors what `DuckDBDataStore.query` builds for the ``FROM`` side:
        ``<database_role>.<table_location>`` for the main table, and — when
        ``binding.series_attributes_location`` is set — a ``LEFT JOIN`` onto the
        sidecar keyed on their shared columns.

        Args:
            binding: The dataflow binding to resolve.

        Raises:
            ValueError: If the binding names a sidecar that shares no columns
                with the main table (no derivable join keys).
            SidecarNotUniqueError: If the sidecar holds more than one row for
                some combination of those keys, which would multiply every
                observation the join matches.
        """
        main = f"{binding.database_role}.{binding.table_location}"
        sidecar_loc = binding.series_attributes_location
        if sidecar_loc is None:
            return QueryPlan(main=main, sidecar=None, join_keys=())
        sidecar = f"{binding.database_role}.{sidecar_loc}"
        join_keys = self.shared_columns(main, sidecar)
        if not join_keys:
            msg = (
                f"DataflowBinding.series_attributes_location {sidecar!r} "
                f"shares no columns with {main!r}; cannot derive JOIN keys"
            )
            raise ValueError(msg)
        rows, distinct = self.key_counts(sidecar, join_keys)
        if rows != distinct:
            keys = ", ".join(join_keys)
            msg = (
                f"series-attributes sidecar {sidecar!r} holds {rows:,} rows but only {distinct:,} distinct "
                f"({keys}) — joined on those keys, every observation of a repeated series would be counted "
                f"once per sidecar row, so counts and aggregates over its columns come back inflated (#412). "
                f"The keys are the columns the two tables share; make the sidecar unique on them, or drop the "
                f"shared columns that are not part of the series key."
            )
            raise SidecarNotUniqueError(msg)
        return QueryPlan(main=main, sidecar=sidecar, join_keys=join_keys)


def _ducklake_partitions(conn: duckdb.DuckDBPyConnection, catalog: str, table_location: str) -> tuple[str, ...]:
    """A DuckLake table's current identity partition columns, or ``()`` when `catalog` is not a DuckLake catalog."""
    lakes = conn.execute("SELECT database_name FROM duckdb_databases() WHERE type = 'ducklake'").fetchall()
    if (catalog,) not in lakes:
        return ()
    # DuckLake attaches the catalog's metadata under this name, not listed in
    # `duckdb_databases()` but readable.
    metadata = f"__ducklake_metadata_{catalog}"
    schema, _, table = table_location.rpartition(".")
    rows = conn.execute(
        f"""
        SELECT col.column_name, pc.transform
        FROM "{metadata}".ducklake_partition_info AS pi
        JOIN "{metadata}".ducklake_partition_column AS pc USING (partition_id, table_id)
        JOIN "{metadata}".ducklake_table AS t ON t.table_id = pi.table_id AND t.end_snapshot IS NULL
        JOIN "{metadata}".ducklake_schema AS s ON s.schema_id = t.schema_id AND s.end_snapshot IS NULL
        JOIN "{metadata}".ducklake_column AS col
          ON col.table_id = pi.table_id AND col.column_id = pc.column_id AND col.end_snapshot IS NULL
        WHERE pi.end_snapshot IS NULL AND s.schema_name = ? AND t.table_name = ?
        ORDER BY pc.partition_key_index
        """,
        [schema or "main", table],
    ).fetchall()
    # A transformed key does not partition the column itself; stop at the first.
    columns: list[str] = []
    for column, transform in rows:
        if transform != "identity":
            break
        columns.append(str(column))
    return tuple(columns)
