# Shared Storage Layer — sdmxlib ↔ cataloglib

This document specifies a shared DuckDB storage schema and access layer
that allows sdmxlib and cataloglib to operate on the same database. The
goal is one database file where catalog discovery queries and SDMX
structural navigation both work, against the same tables.

## Motivation

sdmxlib's `LocalRegistry` and cataloglib's `CatalogStore` store
overlapping data:

| Data                | sdmxlib LocalRegistry | cataloglib CatalogStore |
|---------------------|-----------------------|-------------------------|
| Dataflows           | ✓ (as JSON blobs)     | ✓ (as fact table rows)  |
| DSDs                | ✓ (as JSON blobs)     | ✓ (as dimension bindings)|
| Codelists / codes   | ✓ (normalized tables) | ✓ (codes + code index)  |
| ConceptSchemes      | ✓ (as JSON blobs)     | —                       |
| Catalog metadata    | —                     | ✓ (star schema)         |
| Themes / keywords   | —                     | ✓ (bridge tables)       |
| Distributions       | —                     | ✓ (child table)         |

The normalized code tables are essentially identical. The DSD component
bindings map to dataset_dimensions + dataset_codes. Maintaining two
separate databases with two schemas for the same structural metadata
creates synchronization complexity and prevents cross-system queries.

## Architecture

```
sdmxlib/
├── storage/                    # NEW: shared storage layer
│   ├── __init__.py
│   ├── schema.py               # DDL definitions, table creation
│   ├── readers.py              # read domain objects from tables
│   └── writers.py              # write domain objects to tables
├── registry.py                 # Registry (in-memory, unchanged)
├── local_registry.py           # LocalRegistry (refactored to use storage/)
└── ...

cataloglib/
├── store/
│   └── store.py                # CatalogStore (uses sdmxlib.storage + own tables)
└── ...
```

**Dependency direction**: cataloglib depends on sdmxlib. The shared schema
lives in `sdmxlib.storage` because it describes SDMX structural artifacts.
cataloglib imports and extends it with catalog-specific tables.

**sdmxlib remains independent**: `sdmxlib.storage` has no knowledge of
cataloglib. It defines tables for SDMX artifacts only. cataloglib adds
its own tables (datasets fact, agents, themes, bridges) alongside them.

## Shared Schema (`sdmxlib.storage.schema`)

These tables hold SDMX structural metadata in a normalized, queryable form.
They replace the current `LocalRegistry` SQLite schema.

### Artefact identity table

Every SDMX maintainable artefact gets a row here. This is the master
index that both libraries use for lookup.

```sql
CREATE TABLE IF NOT EXISTS artefacts (
    urn             VARCHAR PRIMARY KEY,   -- full SDMX URN
    artefact_type   VARCHAR NOT NULL,      -- 'Codelist', 'DataStructure', etc.
    agency          VARCHAR NOT NULL,
    id              VARCHAR NOT NULL,
    version         VARCHAR NOT NULL DEFAULT '1.0',
    name_en         VARCHAR,
    name_fr         VARCHAR,
    description_en  VARCHAR,
    description_fr  VARCHAR,
    is_final        BOOLEAN DEFAULT FALSE,
    _document       VARCHAR,               -- full JSON for complex fields
    UNIQUE (artefact_type, agency, id, version)
);
```

The `_document` column stores the full serialized sdmxlib domain object
for artefact types that have complex structure not captured by the
normalized tables below (e.g., ConceptSchemes, AgencySchemes, StructureSets).
For Codelists and DSDs, the normalized tables are authoritative and
`_document` serves as a secondary cache for fast full-object reconstruction.

### Codelist / codes tables

```sql
CREATE TABLE IF NOT EXISTS codelists (
    urn             VARCHAR PRIMARY KEY,   -- FK → artefacts.urn
    agency          VARCHAR NOT NULL,
    id              VARCHAR NOT NULL,
    version         VARCHAR NOT NULL DEFAULT '1.0',
    name_en         VARCHAR,
    name_fr         VARCHAR,
    is_partial      BOOLEAN DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS codes (
    codelist_urn    VARCHAR NOT NULL,       -- FK → codelists.urn
    code_id         VARCHAR NOT NULL,
    label_en        VARCHAR,
    label_fr        VARCHAR,
    description_en  VARCHAR,
    parent_code_id  VARCHAR,               -- self-ref for hierarchy
    valid_from      TIMESTAMP,
    valid_to        TIMESTAMP,
    position        INTEGER,               -- sort order within codelist
    PRIMARY KEY (codelist_urn, code_id)
);
```

### Data Structure Definition tables

```sql
CREATE TABLE IF NOT EXISTS data_structures (
    urn             VARCHAR PRIMARY KEY,   -- FK → artefacts.urn
    agency          VARCHAR NOT NULL,
    id              VARCHAR NOT NULL,
    version         VARCHAR NOT NULL DEFAULT '1.0',
    name_en         VARCHAR,
    name_fr         VARCHAR
);

CREATE TABLE IF NOT EXISTS dsd_components (
    dsd_urn         VARCHAR NOT NULL,      -- FK → data_structures.urn
    component_id    VARCHAR NOT NULL,
    component_type  VARCHAR NOT NULL,      -- 'Dimension', 'TimeDimension',
                                           -- 'Attribute', 'Measure',
                                           -- 'MeasureDimension'
    position        INTEGER,               -- dimension position (NULL for attrs)
    concept_id      VARCHAR,               -- concept identity
    concept_name_en VARCHAR,
    concept_name_fr VARCHAR,
    codelist_urn    VARCHAR,               -- FK → codelists.urn (NULL if free-text)
    attachment_level VARCHAR,              -- for Attributes: 'Observation', 'Series', etc.
    PRIMARY KEY (dsd_urn, component_id)
);
```

### Dataflow table

```sql
CREATE TABLE IF NOT EXISTS dataflows (
    urn             VARCHAR PRIMARY KEY,   -- FK → artefacts.urn
    agency          VARCHAR NOT NULL,
    id              VARCHAR NOT NULL,
    version         VARCHAR NOT NULL DEFAULT '1.0',
    name_en         VARCHAR,
    name_fr         VARCHAR,
    description_en  VARCHAR,
    description_fr  VARCHAR,
    dsd_urn         VARCHAR                -- FK → data_structures.urn
);
```

### Concept scheme table

```sql
CREATE TABLE IF NOT EXISTS concept_schemes (
    urn             VARCHAR PRIMARY KEY,   -- FK → artefacts.urn
    agency          VARCHAR NOT NULL,
    id              VARCHAR NOT NULL,
    version         VARCHAR NOT NULL DEFAULT '1.0',
    name_en         VARCHAR,
    name_fr         VARCHAR
);

CREATE TABLE IF NOT EXISTS concepts (
    scheme_urn      VARCHAR NOT NULL,      -- FK → concept_schemes.urn
    concept_id      VARCHAR NOT NULL,
    name_en         VARCHAR,
    name_fr         VARCHAR,
    description_en  VARCHAR,
    PRIMARY KEY (scheme_urn, concept_id)
);
```

---

## Reader API (`sdmxlib.storage.readers`)

Functions that reconstruct sdmxlib domain objects from the normalized tables.
These are what `LocalRegistry` calls internally.

```python
def get_codelist(conn: duckdb.DuckDBPyConnection, urn: str) -> Codelist | None:
    """Reconstruct a Codelist from codelists + codes tables.

    Returns a LazyCodelist or full Codelist depending on implementation.
    Codes are loaded from the codes table, parent_code_id is used to
    reconstruct the hierarchy.
    """

def get_data_structure(conn: duckdb.DuckDBPyConnection, urn: str) -> DataStructure | None:
    """Reconstruct a DataStructure from data_structures + dsd_components.

    Component codelists are returned as Ref[Codelist] pointing at the
    codelist_urn. If the codelist exists in the codes table, the Ref
    can be resolved from the same database.
    """

def get_dataflow(conn: duckdb.DuckDBPyConnection, urn: str) -> Dataflow | None:
    """Reconstruct a Dataflow from dataflows table.

    The structure Ref[DataStructure] points at dsd_urn.
    """

def get_artefact(conn: duckdb.DuckDBPyConnection, urn: str) -> MaintainableArtefact | None:
    """Generic lookup. Dispatches to type-specific reader based on
    artefacts.artefact_type. Falls back to _document deserialization
    for types without normalized tables.
    """

def get_all(conn: duckdb.DuckDBPyConnection, cls: type[T]) -> list[T]:
    """Return all artefacts of a given type."""

def code_map(conn: duckdb.DuckDBPyConnection, codelist_urn: str, lang: str) -> dict[str, str]:
    """Direct SQL: {code_id: label} without constructing Python objects.

    SELECT code_id, label_{lang} FROM codes WHERE codelist_urn = ?
    """

def code_map_for_dsd(
    conn: duckdb.DuckDBPyConnection, dsd_urn: str, lang: str
) -> dict[str, dict[str, str]]:
    """Direct SQL: {dimension_id: {code_id: label}} for all enumerated
    dimensions of a DSD.

    JOIN dsd_components → codes on codelist_urn.
    """
```

## Writer API (`sdmxlib.storage.writers`)

Functions that flatten sdmxlib domain objects into the normalized tables.

```python
def put_codelist(conn: duckdb.DuckDBPyConnection, cl: Codelist) -> None:
    """Write a Codelist to codelists + codes tables.

    Upserts the codelist row, then bulk inserts/replaces all codes.
    Also writes to artefacts table.
    """

def put_data_structure(conn: duckdb.DuckDBPyConnection, dsd: DataStructure) -> None:
    """Write a DataStructure to data_structures + dsd_components.

    Flattens each component (dimensions, attributes, measures) into
    dsd_components rows. Also writes to artefacts table.
    """

def put_dataflow(conn: duckdb.DuckDBPyConnection, flow: Dataflow) -> None:
    """Write a Dataflow to dataflows table + artefacts table."""

def put_artefact(conn: duckdb.DuckDBPyConnection, artefact: MaintainableArtefact) -> None:
    """Generic writer. Dispatches to type-specific writer if available,
    otherwise serializes to _document in artefacts table.
    """

def ingest_registry(conn: duckdb.DuckDBPyConnection, registry: Registry) -> None:
    """Bulk ingest all artefacts from an in-memory Registry.

    Iterates registry.get_all() for each supported type, calls the
    appropriate put_* function. Wraps in a transaction.
    """
```

---

## LocalRegistry Refactoring

`LocalRegistry` becomes a thin wrapper around the storage module:

```python
class LocalRegistry:
    """SQLite/DuckDB-backed persistent artefact store.

    Refactored to use sdmxlib.storage internally. The public API
    is unchanged.
    """

    def __init__(self, path_or_connection: str | duckdb.DuckDBPyConnection = ":memory:"):
        """Accept either a file path (creates new connection) or an
        existing DuckDB connection (shares database with cataloglib).
        """
        if isinstance(path_or_connection, str):
            self._conn = duckdb.connect(path_or_connection)
            self._owns_connection = True
        else:
            self._conn = path_or_connection
            self._owns_connection = False
        schema.create_tables(self._conn)

    @property
    def connection(self) -> duckdb.DuckDBPyConnection:
        """Expose the connection for sharing with cataloglib."""
        return self._conn

    def get(self, urn: str | SdmxUrn) -> MaintainableArtefact | None:
        return readers.get_artefact(self._conn, str(urn))

    def get_all(self, cls: type[T]) -> list[T]:
        return readers.get_all(self._conn, cls)

    def add(self, artefact: MaintainableArtefact) -> None:
        writers.put_artefact(self._conn, artefact)

    def add_all(self, artefacts: Iterable[MaintainableArtefact]) -> None:
        with self._conn.begin():
            for a in artefacts:
                writers.put_artefact(self._conn, a)

    def ingest(self, registry: Registry) -> None:
        writers.ingest_registry(self._conn, registry)

    def code_map(self, codelist_urn: str, lang: str = "en") -> dict[str, str]:
        return readers.code_map(self._conn, codelist_urn, lang)

    def drop(self, urn: str) -> None: ...
    def replace(self, artefact: MaintainableArtefact) -> None: ...
    def __contains__(self, urn: str) -> bool: ...
    def __len__(self) -> int: ...
```

**Key change**: The constructor accepts an existing `DuckDBPyConnection`.
When cataloglib passes its own connection, both operate on the same
database. When used standalone, LocalRegistry creates its own connection.

---

## cataloglib Integration

cataloglib's `CatalogStore` creates the SDMX tables AND its own catalog
tables on the same connection:

```python
class CatalogStore:
    def __init__(self, path: str = ":memory:"):
        self._conn = duckdb.connect(path)
        # Shared SDMX structural tables
        sdmxlib.storage.schema.create_tables(self._conn)
        # Catalog-specific tables (datasets fact, agents, themes, bridges)
        self._create_catalog_tables()

    @property
    def connection(self) -> duckdb.DuckDBPyConnection:
        """Expose for sdmxlib LocalRegistry sharing."""
        return self._conn

    def local_registry(self) -> sl.LocalRegistry:
        """Return an sdmxlib LocalRegistry sharing this database."""
        return sl.LocalRegistry(self._conn)
```

### ingest_sdmx() uses the shared tables

```python
def ingest_sdmx(self, registry: Registry, publisher: Agent | None = None, ...) -> list[StatisticalDataset]:
    # 1. Write all SDMX artefacts to shared structural tables
    sdmxlib.storage.writers.ingest_registry(self._conn, registry)

    # 2. For each dataflow, create a StatisticalDataset in the catalog
    datasets = []
    for flow in registry.get_all(Dataflow):
        ds = dataset_from_dataflow(flow, dsd, publisher=publisher)
        self.put(ds)
        datasets.append(ds)

    # 3. Populate dataset_codes bridge from the SAME codes table
    #    No separate code extraction needed — join dsd_components
    #    to codes to get the code membership per dataset
    self._conn.execute("""
        INSERT INTO dataset_codes (dataset_id, dimension_id, code_id)
        SELECT df.id, comp.component_id, c.code_id
        FROM dataflows df
        JOIN dsd_components comp ON df.dsd_urn = comp.dsd_urn
        JOIN codes c ON comp.codelist_urn = c.codelist_urn
        WHERE comp.codelist_urn IS NOT NULL
    """)

    return datasets
```

The code index is now a derived view of the shared structural tables — one
SQL INSERT that joins dataflows → dsd_components → codes. No Python-side
iteration over codelists needed.

### Structural queries go through shared tables

```python
def get_structure(self, ds: StatisticalDataset) -> DataStructure | None:
    """Resolve the full DSD from the shared structural tables."""
    if not ds.sdmx_dataflow_id:
        return None
    flow = sdmxlib.storage.readers.get_dataflow(self._conn, ...)
    if flow and flow.structure:
        return sdmxlib.storage.readers.get_data_structure(
            self._conn, str(flow.structure.urn)
        )
    return None
```

---

## Usage

```python
import sdmxlib as sl
import cataloglib as cl

# One database, both libraries
store = cl.CatalogStore("statistics.duckdb")

# Ingest SDMX data
with sl.RestRegistry(sl.Provider.STATCAN) as reg:
    flows = reg.query(sl.Dataflow, agency="StatCan").resolve()
    store.ingest_sdmx(reg.registry, publisher=cl.Agent(
        name=cl.InternationalString.of({"en": "Statistics Canada"}),
    ))

# Catalog discovery
results = store.search("labour force quarterly")
results = (
    store.query(cl.StatisticalDataset)
    .filter(has_code=("GEO", "ON"))
    .with_facets("publisher", "frequency")
    .execute()
)

# SDMX structural access — same database
local = store.local_registry()
dsd = local.get("urn:sdmx:org.sdmx.infomodel....")
dsd.dimensions["GEO"].codelist().codes        # reads from shared codes table
local.code_map("urn:...:CL_GEO(1.0)", "en")  # direct SQL on same codes table

# Or use the convenience method
dsd = store.get_structure(results.items[0])

# Standalone sdmxlib usage (no cataloglib) still works
local = sl.LocalRegistry("structural.duckdb")
local.add(codelist)
local.code_map(codelist.urn, "en")
```

---

## Implementation Order

This feature spans both libraries. Recommended sequence:

1. **`sdmxlib.storage.schema`** — DDL definitions, `create_tables()` function.
   Test that tables are created correctly in `:memory:` and persistent DBs.

2. **`sdmxlib.storage.writers`** — `put_codelist`, `put_data_structure`,
   `put_dataflow`, `put_artefact`, `ingest_registry`. Test round-trip:
   write an artefact, verify rows in tables.

3. **`sdmxlib.storage.readers`** — `get_codelist`, `get_data_structure`,
   `get_dataflow`, `get_artefact`, `code_map`. Test: write with writers,
   read back with readers, verify domain objects match originals.

4. **Refactor `LocalRegistry`** — Replace internal SQLite schema with
   `sdmxlib.storage`. Accept `DuckDBPyConnection` in constructor. Verify
   all existing `LocalRegistry` tests pass unchanged.

5. **cataloglib `CatalogStore`** — Call `sdmxlib.storage.schema.create_tables()`
   in constructor. Implement `ingest_sdmx()` using shared tables.
   Implement `local_registry()` and `get_structure()`.

Steps 1-4 are sdmxlib changes. Step 5 is cataloglib. sdmxlib can ship
the storage module independently — it's a pure improvement to LocalRegistry
regardless of whether cataloglib exists.

---

## Migration / Backwards Compatibility

- `LocalRegistry(path)` where `path` is a string continues to work
  exactly as before. The only change is the internal schema moves from
  SQLite to DuckDB. If existing users have SQLite-based LocalRegistry
  databases, a one-time migration utility should be provided.
- `LocalRegistry(connection)` is new — accepts a shared DuckDB connection.
- The public API of `LocalRegistry` (`get`, `add`, `add_all`, `code_map`,
  `drop`, `replace`, `ingest`, `__contains__`, `__len__`) is unchanged.
- `sdmxlib.storage` is a new public module but all functions take a raw
  DuckDB connection — no new abstractions or base classes.
