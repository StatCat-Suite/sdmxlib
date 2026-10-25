"""The bundle tier's census copy: the source bundle republished in today's layout, schema and conformance.

The source was built by statcan-catalog-builder in the single-directory layout
before sdmxlib owned the manifest, on store schema 2.48, and it stores "no
value" as an empty string in three coded attributes (``NOTE``, ``PROV_TERR``,
``TOPIC``). The copy is what StatCat-Suite/cpt-builder will publish: the
publication layout, the current schema, and ``NULL`` where an attribute is
absent, which `write_manifest` requires of a coded column (GH-194). The source
is never written.

This module decides what the cached copy holds, so its source is part of the
cache key (`_bundle._PUBLISHING`).
"""

import shutil
import time
from pathlib import Path

import duckdb

import sdmxlib as sl
from sdmxlib.bundle import Producer, write_manifest
from sdmxlib.storage.schema import create_schema
from tests.bench._bundle import threads

#: Notes for the tier's summary: what the republish did, and how long it took.
NOTES: list[str] = []


def republish(bundle: Path, root: Path) -> Path:
    """`bundle`, as a publication under `root`, published."""
    with sl.LocalRegistry.build(root) as started:
        publication = started.publication
    for name in ("meta.duckdb", "lake.ducklake"):
        shutil.copyfile(bundle / name, publication / name)
    shutil.copytree(bundle / "parquet", root / "data", dirs_exist_ok=True)
    with duckdb.connect(str(publication / "meta.duckdb")) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO sdmx._meta (key, value) VALUES ('publication_token', ?)", [publication.name]
        )
        # 2.49 only adds tables (`availability`, `availability_value`), so the
        # schema's own idempotent DDL brings a 2.48 store forward; see
        # `_bundle.UPGRADABLE`. A bump that changes a table is not covered.
        create_schema(conn)
    started = time.perf_counter()
    with sl.LocalRegistry.build(root, resume=publication.name) as builder:
        builder.connection.execute(f"SET threads = {threads()}")
        cleared = absent_as_null(builder)
    NOTES.append(f"bundle tier: set {cleared:,} empty coded values to NULL ({time.perf_counter() - started:.0f} s)")
    # Published on the copy (GH-66): sdmxlib owns `bundle.manifest.json`.
    # Republishing runs `write_manifest` at full scale: checksums over every
    # file, counts from the real lake, every fingerprint, and the availability
    # scan that refuses a non-code value.
    # Snapshots expire so the manifest lists the rewritten table's files, not
    # the ones the rewrite superseded as well.
    write_manifest(
        publication, bundle_id=bundle.name, producer=Producer(name="sdmxlib-bench", version="0"), expire_snapshots=True
    )
    return publication


def absent_as_null(registry: sl.LocalRegistry) -> int:
    """Set every empty string in a bound table's text attribute columns to ``NULL``; return the cells changed.

    A rewrite, not an ``UPDATE``: updating 1.07 billion of the census
    table's rows in place ran DuckDB out of memory (an 8 GiB allocation at
    19.1 GiB). ``CREATE TABLE ... AS SELECT`` streams the rows into new data
    files in their order, and the rewritten table takes the old one's name.
    """
    changed = 0
    for binding in registry.get_all(sl.DataflowBinding):
        located = registry.locate(binding)
        table = f"{located.database_role}.{located.table_location}"
        types = dict(registry.connection.execute(f"SELECT column_name, column_type FROM (DESCRIBE {table})").fetchall())
        text = [c for c in located.attribute_column_map.values() if types.get(c) == "VARCHAR"]
        if not text:
            continue
        counts = registry.connection.execute(
            "SELECT " + ", ".join(f"count(*) FILTER (WHERE \"{c}\" = '')" for c in text) + f" FROM {table}"
        ).fetchone()
        found = sum(int(n) for n in (counts or ()))
        if not found:
            continue
        changed += found
        rewritten = f"{table}__absent_as_null"
        replaced = ", ".join(f'NULLIF("{c}", \'\') AS "{c}"' for c in text)
        registry.connection.execute(f"CREATE TABLE {rewritten} AS SELECT * REPLACE ({replaced}) FROM {table}")
        registry.connection.execute(f"DROP TABLE {table}")
        registry.connection.execute(f"ALTER TABLE {rewritten} RENAME TO {table.rsplit('.', 1)[1]}")
    return changed
