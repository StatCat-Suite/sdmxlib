"""Column-level reads of a bound table: which columns it has, and how many rows hold given values.

`table` is ``<database_role>.<schema>.<table>`` from a binding, and `column`
a name from its column maps, both sdmxlib-controlled; values are bound.
"""

import duckdb


def table_columns(conn: duckdb.DuckDBPyConnection, table: str) -> set[str]:
    """The column names of `table`."""
    return {str(row[0]) for row in conn.execute(f"SELECT column_name FROM (DESCRIBE {table})").fetchall()}


def value_counts(conn: duckdb.DuckDBPyConnection, table: str, column: str, values: list[str]) -> list[tuple[str, int]]:
    """How many rows of `table` hold each of `values` in `column`, compared as text, sorted by value."""
    marks = ", ".join("?" for _ in values)
    text = f'CAST("{column}" AS VARCHAR)'
    rows = conn.execute(
        f"SELECT {text} AS v, count(*) FROM {table} WHERE {text} IN ({marks}) GROUP BY v ORDER BY v",
        values,
    ).fetchall()
    return [(str(value), int(count)) for value, count in rows]
