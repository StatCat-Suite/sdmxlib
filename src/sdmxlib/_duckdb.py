"""Private DuckDB helpers shared by the storage layer and the query language.

Lives at the package root because both ``storage/`` and ``query/`` need it, and
neither layer may import the other.
"""

import duckdb


def count(cursor: duckdb.DuckDBPyConnection) -> int:
    """Return the integer in the first cell of the cursor's first row, or 0.

    The ``__len__`` helper for ``SELECT COUNT(*)`` queries: centralises the
    ``row = cur.fetchone(); row[0] if row else 0`` idiom. Generalise to a typed
    ``scalar[T](cursor, *, default: T) -> T`` if a non-count scalar read ever
    appears.
    """
    row = cursor.fetchone()
    return int(row[0]) if row is not None else 0
