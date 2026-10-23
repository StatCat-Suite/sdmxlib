# sdmxlib

> **Note:** This library has been written extensively with AI assistance
> (Claude Code). Users who are not comfortable with AI-generated code should
> take that into account before adopting it.

SDMX for Python: structures, reference metadata and observation data, from
REST, files or a local DuckDB store. Navigate Data Structure Definitions,
Codelists, Dataflows and related artefacts with plain attribute access, query
catalogues and observations with one path language, and read and write SDMX-ML,
SDMX-JSON and SDMX-CSV.

## Quick start

```python
import sdmxlib as sl

with sl.RestRegistry(sl.Provider.BIS) as reg:
    urn = sl.DataStructure.urn_for(agency="BIS", id="WS_CBPOL")
    dsd = reg.get(urn, resolve=True)

    for dim in dsd.dimensions:
        if dim.is_enumerated:
            print(f"{dim.id}: {dim.codelist.id} ({len(dim.code_ids())} codes)")
```

```text
FREQ: CL_FREQ (7 codes)
REF_AREA: CL_BIS_IF_REF_AREA (44 codes)
INSTR_ASSET: CL_INSTR_ASSET (12 codes)
BORROWER_CTY: CL_BIS_IF_REF_AREA (44 codes)
```

## Installation

```bash
uv add sdmxlib
# or: pip install sdmxlib
```

sdmxlib needs Python 3.14 or later.

## What it does

- Connects to any SDMX 2.1 or 3.0 REST endpoint; built-in shortcuts for ECB,
  BIS, ESTAT, IMF, OECD, and the SDMX Global Registry via `sl.Provider`
- One read, with resolution depth as a parameter: `get(urn)` for a skeleton,
  `get(urn, resolve=True)` for the full reference graph, `resolve=[sl.Codelist]`
  for named types only, and `partial=False` for untrimmed schemes
- Returns fully resolved domain objects with plain attribute access — no XML,
  no URN chasing
- Filter collections with lambdas or the `item()` expression API
- Build SDMX artefacts locally — frozen, checked as they are built — and
  serialise to SDMX-ML 2.1, 3.0 and 3.1 or SDMX-JSON 2.0
- `LocalRegistry` — a DuckDB-backed persistent store with lazy codelists and
  hierarchies and direct SQL code maps; pass it as a `RestRegistry`'s
  `store=` and everything fetched is kept for offline use
- A path query language over both planes, on a `LocalRegistry` or straight
  against REST: `reg.query(sl.q.dataflow)` for structures,
  `reg.obs(flow)` for rows, `reg.sql(...)` as the escape hatch
- Uniform SKOS-aligned tree API (`roots`/`leaves`/`narrower`/`broader`/
  `*_transitive`/`depth_of` plus bulk projections `depths`/`broader_map`/
  `ancestors_map`/`inherit_along_broader`) on `Codelist`, `Hierarchy`,
  `CategoryScheme`, `MetadataStructure` — and their lazy SQL variants
  `LazyCodelist`/`LazyHierarchy`. A codelist is keyed by code id; the other
  trees by path (`h["CA.ON"]`), since a node id may repeat across branches
- Reference metadata via `MetadataStructure` (MSD), `Metadataflow`, and
  `MetadataSet` — attach typed footnotes, methodology notes, or quality
  reports to any identifiable artefact. Definitions ride in
  `StructureMessage`; populated payloads ride in `MetadataMessage`
  (`<mes:GenericMetadata>` in SDMX-ML 3.0; metadata-schema envelope in
  SDMX-JSON 2.0). Both message kinds round-trip through
  `LocalRegistry`, JSON, and XML.
- Integrates with [Polars](https://pola.rs) for schema generation and data
  validation
- Fully typed — IDE completion and static analysis work throughout

## For AI assistants

Two condensed reference files at the repo root are written for LLM tools
(Claude Code, Cursor, Copilot, etc.):

- [`llms.txt`](llms.txt) — the whole library in one pass: registries and
  `get`, navigation, building artefacts, the local store, trees, observation
  data, reading and writing SDMX, reference metadata, and the type table
- [`llms-dsl.txt`](llms-dsl.txt) — the path query language in depth:
  `sl.q`, `Query` / `Select`, projection and aggregation, edges, windows,
  observation queries (`ObsQuery`), and `reg.sql()`

Every Python block in both is executed by the test suite, so they track the
library's actual surface. Point your assistant at them.

## Upgrading

sdmxlib is pre-stability and breaks cleanly rather than carrying two spellings
of the same idea. [Upgrading](https://statcat-suite.github.io/sdmxlib/upgrading/)
lists every breaking change since 0.48, with the old and new spelling of each —
`sl.Registry` is now `sl.LocalRegistry`, `get()` takes a URN and a resolution
depth, the four query DSLs are one path language, and every model class is
frozen.

## Documentation

See the [docs](https://statcat-suite.github.io/sdmxlib/) for the full cookbook,
including:

- Connecting to registries and choosing a resolution depth
- Persisting artefacts in a local DuckDB store with `LocalRegistry`
- Querying structures and observations with the `sl.q` path language
- Fetching observation data and pivoting to wide format
- Inspecting DSDs and building code maps for ETL pipelines
- Generating Polars schemas from SDMX Data Structure Definitions
- Browsing data catalogs and filtering dataflows
- Working with constraints, hierarchies, and serialisation

## Development

```bash
just check           # lint + typecheck
just test            # unit tests
just test-integration  # end-to-end tests, replayed from recorded cassettes
just ci              # the full gate: lint, typecheck, unit, docs, integration
```

Issues are tracked on [GitHub](https://github.com/StatCat-Suite/sdmxlib/issues).
The project started on GitLab, so a bare `#492` in code, comments or docs means
an issue in the [GitLab tracker](https://gitlab.com/pinax-suite/sdmxlib/-/issues),
which stays readable as an archive. GitHub issues are written `GH-12`.
