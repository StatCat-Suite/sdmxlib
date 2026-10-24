"""What a published store leaves out, and puts back when a builder extends it (GH-165).

A published bundle is never written, so its keys enforce nothing; their
indexes cost it space on every tier-1 pull, and some buy reads. Measured on
the census bundle (1,022 MB rebuilt), by the bundle tier's read benchmarks
with each structure dropped, interleaved with the baseline:

| structure | size | read cost |
| --- | --- | --- |
| ``hierarchy_node``'s primary key and ``UNIQUE (hierarchy_urn, path)`` | 261 MB | none |
| ``artefact_ref``'s primary key | 184 MB | none |
| ``artefact_ref``'s two indexes | 76 MB | none |
| ``annotation``'s four indexes | 70 MB | a small hierarchy's eager read 2x |
| ``code``'s primary key | 69 MB | a small hierarchy's eager read 2x |

So `compact_for_publication` drops the first three and keeps the rest, and
writes the store into a fresh file, which also leaves out its free blocks.
`restore` puts them back on a store `LocalRegistry.build` copied to extend,
so a store being written always has its keys.

The tables are rebuilt from the schema's own definition: a fresh store is
read for each column's type, default and ``NOT NULL``, so a trimmed table
keeps every column constraint and only its keys go.
"""

import functools
from pathlib import Path

import duckdb

from sdmxlib.storage.schema import create_schema

#: Tables whose keys a published store leaves out.
TRIMMED_TABLES = ("artefact_ref", "hierarchy_node")
#: Explicit indexes a published store leaves out.
TRIMMED_INDEXES = ("idx_ref_role", "idx_ref_target")


def _identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


@functools.cache
def _definitions() -> tuple[dict[str, str], dict[str, str], dict[str, tuple[str, ...]], dict[str, str]]:
    """From a fresh store: each trimmed table's full and keyless DDL, each table's kept indexes, the trimmed ones."""
    conn = duckdb.connect()
    create_schema(conn).close()
    full: dict[str, str] = {}
    keyless: dict[str, str] = {}
    for table in TRIMMED_TABLES:
        (sql,) = conn.execute(
            "SELECT sql FROM duckdb_tables() WHERE schema_name = 'sdmx' AND table_name = ?", [table]
        ).fetchone() or ("",)
        full[table] = str(sql)
        columns = conn.execute(
            """
            SELECT column_name, data_type, column_default, is_nullable
            FROM duckdb_columns() WHERE schema_name = 'sdmx' AND table_name = ? ORDER BY column_index
            """,
            [table],
        ).fetchall()
        parts = [
            " ".join(
                [_identifier(name), str(kind)]
                + ([f"DEFAULT {default}"] if default is not None else [])
                + ([] if nullable else ["NOT NULL"])
            )
            for name, kind, default, nullable in columns
        ]
        keyless[table] = f"CREATE TABLE sdmx.{_identifier(table)} ({', '.join(parts)})"
    kept: dict[str, tuple[str, ...]] = {}
    trimmed: dict[str, str] = {}
    for table, name, sql in conn.execute(
        "SELECT table_name, index_name, sql FROM duckdb_indexes() WHERE schema_name = 'sdmx' ORDER BY ALL"
    ).fetchall():
        if name in TRIMMED_INDEXES:
            trimmed[str(name)] = str(sql)
        else:
            kept[str(table)] = (*kept.get(str(table), ()), str(sql))
    conn.close()
    return full, keyless, kept, trimmed


def _rebuild(conn: duckdb.DuckDBPyConnection, table: str, ddl: str) -> None:
    """Recreate `table` in the current catalog as `ddl` defines it, with its rows and kept indexes."""
    # The new table is built under another name and renamed once the old one
    # is dropped: a table with an index on it cannot be renamed.
    building = f"{table}__building"
    named = next((f"sdmx.{n}" for n in (_identifier(table), table) if f"sdmx.{n}" in ddl), None)
    if named is None:
        msg = f"the DDL for {table} does not name sdmx.{table}: {ddl[:80]}"
        raise ValueError(msg)
    conn.execute(ddl.replace(named, f"sdmx.{_identifier(building)}", 1))
    conn.execute(f"INSERT INTO sdmx.{_identifier(building)} SELECT * FROM sdmx.{_identifier(table)}")
    conn.execute(f"DROP TABLE sdmx.{_identifier(table)}")
    conn.execute(f"ALTER TABLE sdmx.{_identifier(building)} RENAME TO {_identifier(table)}")
    for index in _definitions()[2].get(table, ()):
        conn.execute(index)


def compact_for_publication(meta: Path) -> None:
    """Rewrite the store at `meta` without the trimmed keys and indexes, into a fresh file.

    Every other table, index, view, sequence and row is copied as it is,
    and the new file keeps the store's DuckDB storage version.
    """
    mid = meta.with_name(meta.name + ".trim")
    new = meta.with_name(meta.name + ".new")
    for leftover in (mid, new):
        leftover.unlink(missing_ok=True)
    conn = duckdb.connect()
    try:
        conn.execute(f"ATTACH '{meta}' AS src (READ_ONLY)")
        # Written at the storage version the store was written at, which a
        # producer may have chosen so that older readers open the bundle.
        (tag,) = conn.execute(
            "SELECT tags['storage_version'] FROM duckdb_databases() WHERE database_name = 'src'"
        ).fetchone() or (None,)
        storage = f" (STORAGE_VERSION '{str(tag).rstrip('+')}')" if tag else ""
        conn.execute(f"ATTACH '{mid}' AS mid{storage}")
        conn.execute("COPY FROM DATABASE src TO mid")
        conn.execute("USE mid.sdmx")
        keyless = _definitions()[1]
        for table in TRIMMED_TABLES:
            _rebuild(conn, table, keyless[table])
        conn.execute("CHECKPOINT mid")
        conn.execute(f"ATTACH '{new}' AS dst{storage}")
        conn.execute("COPY FROM DATABASE mid TO dst")
        conn.execute("CHECKPOINT dst")
    finally:
        conn.close()
    mid.unlink()
    new.replace(meta)


def restore(conn: duckdb.DuckDBPyConnection) -> None:
    """Put the trimmed keys and indexes back on the store `conn` reads, where they are missing.

    A store published before the trim still has them, and is left as it is.
    """
    full = _definitions()[0]
    for table in TRIMMED_TABLES:
        (keyed,) = conn.execute(
            """
            SELECT count(*) > 0 FROM duckdb_constraints()
            WHERE database_name = current_database() AND schema_name = 'sdmx'
              AND table_name = ? AND constraint_type = 'PRIMARY KEY'
            """,
            [table],
        ).fetchone() or (False,)
        if not keyed:
            _rebuild(conn, table, full[table])
    for name in TRIMMED_INDEXES:
        (present,) = conn.execute(
            "SELECT count(*) > 0 FROM duckdb_indexes() WHERE database_name = current_database() AND index_name = ?",
            [name],
        ).fetchone() or (False,)
        if not present:
            conn.execute(_definitions()[3][name])
