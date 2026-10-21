"""`sl.schema` — qualified column names for raw SQL, checked against the DDL.

The escape hatch's third piece. A raw query has to name tables and columns, and
naming them as string literals is how they rot: the DDL moves, the string does
not, and nothing says so until a query returns nothing or fails at the database
with a message that names neither the call site nor the fix.

    f"SELECT {sl.schema.artefact.agency_id} FROM {sl.schema.artefact}"

Every attribute is resolved against the schema `storage.schema.create_schema`
builds, so a table or column that no longer exists is an `AttributeError` at
the point of use, listing what is there. That makes this a drift guard rather
than a tidier way to spell a string.

Reflected once, lazily, from an in-memory DuckDB — the DDL is the only source
of truth and a hand-written copy would be the thing this exists to prevent.
Nothing is built at import time, so the cost is paid by callers who use it.
"""

import functools
from typing import override


@functools.cache
def _tables() -> dict[str, frozenset[str]]:
    """Every `sdmx` table and its columns, from a freshly built schema."""
    import duckdb  # noqa: PLC0415 — deferred so importing sdmxlib does not build a schema

    from sdmxlib.storage.schema import create_schema  # noqa: PLC0415

    conn = duckdb.connect()
    create_schema(conn)
    rows = conn.execute(
        """
        SELECT table_name, column_name
        FROM information_schema.columns WHERE table_schema = 'sdmx'
        """
    ).fetchall()
    out: dict[str, set[str]] = {}
    for table, column in rows:
        out.setdefault(str(table), set()).add(str(column))
    conn.close()
    return {table: frozenset(columns) for table, columns in out.items()}


class Table:
    """One relation. `str()` is its qualified name; attributes are its columns."""

    __slots__ = ("_columns", "_name")

    def __init__(self, name: str, columns: frozenset[str]) -> None:
        self._name = name
        self._columns = columns

    @property
    def name(self) -> str:
        """Qualified table name — `sdmx.artefact`."""
        return f"sdmx.{self._name}"

    @property
    def columns(self) -> frozenset[str]:
        """Every column of this relation."""
        return self._columns

    def __getattr__(self, column: str) -> str:
        if column in self._columns:
            return f"sdmx.{self._name}.{column}"
        known = ", ".join(sorted(self._columns))
        msg = f"sdmx.{self._name} has no column {column!r}. It has: {known}"
        raise AttributeError(msg)

    def __dir__(self) -> list[str]:
        return sorted(self._columns)

    @override
    def __str__(self) -> str:
        return self.name

    @override
    def __repr__(self) -> str:
        return f"<Table {self.name}>"


class Schema:
    """`sl.schema` — the `sdmx` namespace, resolved against the live DDL."""

    __slots__ = ()

    def __getattr__(self, table: str) -> Table:
        tables = _tables()
        if table in tables:
            return Table(table, tables[table])
        known = ", ".join(sorted(tables))
        msg = f"there is no sdmx.{table}. Tables are: {known}"
        raise AttributeError(msg)

    def __dir__(self) -> list[str]:
        return sorted(_tables())

    @override
    def __repr__(self) -> str:
        return f"<Schema sdmx, {len(_tables())} tables>"


schema = Schema()
"""Qualified names for raw SQL. Exported as ``sl.schema``."""
