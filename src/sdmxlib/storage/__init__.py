"""Shared DuckDB storage layer for SDMX structural metadata.

Provides a fully relational schema in the ``sdmx`` DuckDB namespace
for codelists, DSDs, dataflows, constraints, mappings, and all other
SDMX artefact types.  Used internally by ``Registry`` and
importable by cataloglib for shared-database workflows.

`create_exclusive` creates a file complete before it is visible and fsynced,
for the manifest, the Inventory's documents and any writer that must never
replace a file another created. `JsonValue` is the type of a free-form JSON
value in a manifest or an Inventory root.

Example:
    import duckdb
    from sdmxlib.storage import schema, writers, readers

    conn = duckdb.connect("store.duckdb")
    cursor = schema.create_schema(conn)
    writers.put_codelist(cursor, codelist)
    cm = readers.code_map(cursor, codelist_urn, lang="en")
"""

from sdmxlib.storage import lazy, readers, schema, writers
from sdmxlib.storage._files import create_exclusive
from sdmxlib.storage._manifest import JsonValue
from sdmxlib.storage.schema import create_schema

__all__ = [
    "JsonValue",
    "create_exclusive",
    "create_schema",
    "lazy",
    "readers",
    "schema",
    "writers",
]
