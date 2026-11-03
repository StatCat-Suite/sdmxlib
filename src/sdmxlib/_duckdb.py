"""Private DuckDB helpers shared by the storage layer and the query language.

Lives at the package root because both ``storage/`` and ``query/`` need it, and
neither layer may import the other.

Every single-row read goes through `first`. A thread's handle is shared by
every read on that thread, and DuckDB 2.0 refuses a relation call (`sql`,
`register`, `unregister`) on a handle holding an `execute()` result nobody
finished. `fetchone()` never finishes one, even on a one-row result, so a bare
`fetchone()` breaks the next relation call, usually in an unrelated module
(GH-75). `tests/unit/test_session.py` refuses a bare one in `src/`.
"""

from typing import Any

import duckdb


def first(cursor: duckdb.DuckDBPyConnection) -> tuple[Any, ...] | None:
    """The first row of the result `cursor` holds, or None, with the result finished.

    Typed as DuckDB's `fetchone()` is: a row's cells are whatever the query
    selected, and each caller narrows them, as it did reading `fetchone()`.

    Args:
        cursor: A handle that has just run ``execute()``.

    Returns:
        The first row, or None when the result has none.
    """
    row = cursor.fetchone()
    cursor.complete()
    return row


def count(cursor: duckdb.DuckDBPyConnection) -> int:
    """Return the integer in the first cell of the cursor's first row, or 0.

    The ``__len__`` helper for ``SELECT COUNT(*)`` queries.
    """
    row = first(cursor)
    return int(row[0]) if row is not None else 0


def quote_identifier(name: str) -> str:
    """`name` as a DuckDB quoted identifier: in double quotes, each inner double quote doubled.

    A catalog alias carries the product id (`EditionEngine.attach`), and a
    product named after a StatCan table id, ``14100287``, starts with a digit,
    which an unquoted identifier cannot. DuckDB resolves a quoted identifier
    without regard to case, as it does an unquoted one.
    """
    return '"' + name.replace('"', '""') + '"'


def located(catalog: str, location: str) -> str:
    """The relation ``<catalog>.<location>``, with `catalog` quoted.

    A binding names its table by its catalog (``database_role``) and its
    ``schema.table`` inside it (``table_location``). The catalog is an ATTACH
    alias the registry chose, quoted here; the location is the producer's and
    written as it stands.
    """
    return f"{quote_identifier(catalog)}.{location}"
