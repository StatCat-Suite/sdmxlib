"""The ``tags`` check in SQL: every language tag a store holds is in its normalised form (GH-280).

The store writers write what an `InternationalString` holds, which is
normalised, and the store readers trust it rather than check every row. A store
built before tags were normalised can hold another case, an empty tag or a
malformed one, and would then answer a Python read and a SQL lookup
differently. This finds each such tag, once per distinct value.

The columns are found in ``information_schema`` rather than listed, so a table
added later is covered: every ``MAP(VARCHAR, VARCHAR)`` is keyed by language
but for the binding's column maps, every ``lang`` column holds one, and a
sentinel value's struct holds a ``name`` and a ``description`` map.
"""

import duckdb

from sdmxlib.model.istring import normalize_tag

#: The ``MAP(VARCHAR, VARCHAR)`` columns keyed by something other than a
#: language: a binding's component id → physical column maps.
_NOT_LANGUAGE_MAPS = frozenset(
    {
        ("dataflow_binding", "dimension_column_map"),
        ("dataflow_binding", "measure_column_map"),
        ("dataflow_binding", "attribute_column_map"),
    }
)

_MAP = "MAP(VARCHAR, VARCHAR)"
_SENTINELS = 'STRUCT("value" VARCHAR, "name" MAP(VARCHAR, VARCHAR), description MAP(VARCHAR, VARCHAR))[]'


def _quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def stale_tags(conn: duckdb.DuckDBPyConnection) -> list[tuple[str, str]]:
    """Every stored tag `normalize_tag` would change or refuse, with the column holding it.

    Args:
        conn: A connection to the store.

    Returns:
        ``(table.column, tag)`` pairs, sorted; empty for a store that holds
        only normalised tags.
    """
    columns = conn.execute(
        """
        SELECT table_name, column_name, data_type
        FROM information_schema.columns
        WHERE table_catalog = current_database() AND table_schema = 'sdmx'
        """
    ).fetchall()
    # Identifiers come from the catalog and are quoted; the label each row
    # carries is a bound value.
    selects: list[str] = []
    labels: list[str] = []
    for table, column, data_type in columns:
        source = f"sdmx.{_quoted(table)}"
        col = _quoted(column)
        if data_type == _MAP and (table, column) not in _NOT_LANGUAGE_MAPS:
            selects.append(f"SELECT ?, unnest(map_keys({col})) FROM {source}")
        elif data_type == "VARCHAR" and column == "lang":
            selects.append(f"SELECT ?, {col} FROM {source} WHERE {col} IS NOT NULL")
        elif data_type == _SENTINELS:
            selects += [
                f"SELECT ?, unnest(map_keys(s.{field})) FROM (SELECT unnest({col}) AS s FROM {source})"
                for field in ("name", "description")
            ]
        labels += [f"{table}.{column}"] * (len(selects) - len(labels))
    if not selects:
        return []
    rows: list[tuple[str, str]] = conn.execute(
        f"SELECT DISTINCT * FROM ({' UNION ALL '.join(selects)})", labels
    ).fetchall()
    return sorted((where, tag) for where, tag in rows if not _normalised(tag))


def _normalised(tag: str) -> bool:
    try:
        return normalize_tag(tag) == tag
    except ValueError:
        return False
