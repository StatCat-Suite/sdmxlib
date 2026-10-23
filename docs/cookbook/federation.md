# Federation

Recipes for the federation stack — composing a remote SDMX registry
with local cache and Parquet-backed observations behind one registry.

The headline use case: **structures live at a remote registry (FMR,
ECB, ESTAT), observations live in your own Parquet store**, and one
`LocalRegistry` walks both planes.

---

## Cache-mode federation

A `RestRegistry` configured with `store=local` writes every
fetched artefact through to a `LocalRegistry`. Subsequent fetches of
the same pinned URN skip HTTP entirely.

```python
import sdmxlib as sl

with (
    sl.LocalRegistry("cache.duckdb", source_registry="local") as local,
    sl.RestRegistry(sl.Provider.ECB, store=local) as reg,
):
    dsd = reg.get(sl.DataStructure.urn_for(agency="ECB", id="ECB_EXR1"), resolve=True)
```

What happened:

- `Provider.ECB` carries `id="ECB"`, so the local store tags every row
  with `source_registry="ECB"`. Provenance survives the cache.
- The first fetch hit the network. The second time you open
  `cache.duckdb` and ask for the same URN, it's served from DuckDB
  — no network round-trip.
- `(latest)` URNs are the one exception: a `latest` is a pointer, not
  an artefact, so it is never answered from `cache.duckdb`. How long
  one may be reused within a session is the provider's call — see
  [How fresh a `latest` lookup is](#how-fresh-a-latest-lookup-is).

The local-store-as-cache replaces a separate HTTP cache layer for
sdmxlib's read patterns. Provider IDs, materialise tagging, and the
read-before-fetch hook all compose without extra dependencies.

---

## Routing multiple registries by agency

A `FederatedRegistry` dispatches each URN-keyed lookup by the URN's
maintenance agency. Each agency has exactly one source.

```python
import sdmxlib as sl

with sl.LocalRegistry("cache.duckdb", source_registry="local") as local:
    estat = sl.RestRegistry(sl.Provider.ESTAT, store=local)
    sdmx = sl.RestRegistry(sl.Provider.SDMX_GLOBAL, store=local)

    fed = sl.FederatedRegistry(
        routing={"ESTAT": estat, "SDMX": sdmx},
        default=local,  # locally-authored agencies dispatch here
    )

    # Routes by maintenance agency:
    dsd = fed.get(sl.DataStructure.urn_for(agency="ESTAT", id="X", version="1.0"))
    cl = fed.get(sl.Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="2.1"))
    own = fed.get(sl.Codelist.urn_for(agency="MY_ORG", id="CL_X", version="1.0"))
```

`fed` itself satisfies the `sl.RegistryReader` Protocol — it can be a source in
another `FederatedRegistry` if you want layered routing.

### `resolve=` and `partial=` through a federation

Both mean what they mean on `RestRegistry`, and you pass them to `fed.get`
without knowing which source will answer:

```python
own_urn = sl.Codelist.urn_for(agency="MY_ORG", id="CL_X", version="1.0")

with (
    sl.LocalRegistry("cache.duckdb", source_registry="local") as local,
    sl.RestRegistry(sl.Provider.ESTAT, store=local) as estat,
):
    fed = sl.FederatedRegistry(routing={"ESTAT": estat}, default=local)

    fed.get(sl.DataStructure.urn_for(agency="ESTAT", id="CPI"), resolve=True)
    fed.get(own_urn, resolve=True)
```

The second one routes to `local`, whose `get` takes no `resolve=` at all —
so the argument is **translated, not forwarded**. Routing is by agency and the
agency is in the URN, so the federation knows which source will answer before
it asks.

**`resolve=` is dropped for a local source.** Depth is a choice about how many
*requests* to make, and a store has already paid for all of them: it resolves
unconditionally. The artefact you get back already satisfies `resolve=True`,
and `resolve=False` only declined work that was not going to happen.

**`partial=False` is refused instead**, because it has no such reading — a
store holds whatever was written to it, so it can neither give you the
untrimmed codelists nor tell that it has not:

```python
with sl.LocalRegistry("cache.duckdb", source_registry="local") as local:
    fed = sl.FederatedRegistry(routing={}, default=local)
    try:
        fed.get(own_urn, partial=False)
    except TypeError as exc:
        print(exc)
        # partial=False routes to LocalRegistry, which cannot promise it:
        # LocalRegistry.get takes no partial=, and a store holds whatever was
        # written to it. Ask the source that fetches — fed.source_for(urn) —
        # or accept what LocalRegistry has for …
```

Answering that with trimmed codelists would be a wrong answer to an explicit
question. Ask the source that fetches — `fed.source_for(urn)` — or accept what
the store has.

---

## When a source is unreachable

**`None` means the source answered "not here"; an unreachable source raises.**
They are different facts and the caller's response differs — retry a dead
source, fall back for a missing artefact — so reporting them identically makes
the distinction unrecoverable (design §5.8).

```python
import sdmxlib as sl

with sl.RestRegistry(sl.Provider.ESTAT) as estat:
    fed = sl.FederatedRegistry(routing={"ESTAT": estat})

    try:
        result = fed.get(sl.Codelist.urn_for(agency="ESTAT", id="X", version="1.0"))
    except sl.RegistryUnavailable as exc:
        exc.source_id   # which source
        exc.tried       # what was being read
        exc.cause       # the underlying transport error
    else:
        if result is None:
            ...  # genuinely absent, and only that
```

To continue past a dead source in a sweep, write the loop that says so:

```python
with sl.RestRegistry(sl.Provider.ESTAT) as estat:
    fed = sl.FederatedRegistry(routing={"ESTAT": estat})

    for urn in urns:
        try:
            artefact = fed.get(urn)
        except sl.RegistryUnavailable as exc:
            log.warning("skipping %s: %s", urn, exc)
            continue
        if artefact is None:
            ...  # absent
```

Programmer errors (`ValueError`, `TypeError`) propagate unchanged: only
transport failures mean "could not reach". A `404` is the server answering,
so it comes back as `None`; a `500` is the server's own error and is raised.

## How fresh a `latest` lookup is

A pinned URN names an immutable artefact and is cached indefinitely. `latest`
names a *pointer*, and how long it may go un-re-asked is the **provider's**
call, read from `Cache-Control` and `ETag`:

```python
reg = sl.RestRegistry(sl.Provider.OECD)          # says max-age=86400 on structures
reg.get(urn)                                     # one request
reg.get(urn)                                     # served locally, inside the window
```

Providers differ, and the difference is real — measured from recorded
responses:

| provider             | structures                                       | revalidation  |
| -------------------- | ------------------------------------------------ | ------------- |
| ECB, IMF             | `no-cache, no-store, max-age=0, must-revalidate`  | none          |
| OECD                 | `public, max-age=86400`                           | Last-Modified |
| SDMX Global Registry | *(absent)*                                        | ETag          |

Where a validator is offered, re-asking costs a `304` rather than a body.
Where nothing is offered, every ask goes to the provider — the library invents
no window of its own.

**`latest_ttl` overrides a provider that is too stingy**, and only in that
direction:

```python
# ECB sends `no-store` on codelists, and `isFinal="false"` on everything.
# Both look like appliance defaults rather than statements about volatility.
reg = sl.RestRegistry(sl.Provider.ECB, latest_ttl=300)
```

It is a floor, never a ceiling: it cannot shorten a window a provider granted.
To ask for something that cannot go stale, pass a pinned URN — that is not a
pointer at all.

---

## Binding a dataflow to a Parquet table

A `DataflowBinding` is a first-class artefact that says "the
observations for this dataflow live at this table location, with this
column layout, written against this DSD version." It lives in the
local store alongside structural artefacts.

```python
from datetime import UTC, datetime
import sdmxlib as sl

flow_urn = sl.Dataflow.urn_for(agency="STATCAN", id="CENSUS_PROFILE", version="2021")

binding = sl.DataflowBinding(
    dataflow=sl.Ref[sl.Dataflow].unresolved(flow_urn),
    database_role="lake",  # the ATTACH alias of the database owning the table
    table_location="census_2021.observations",
    partition_layout=("GEO_LEVEL",),
    dimension_column_map={"REF_AREA": "REF_AREA", "TIME_PERIOD": "TIME_PERIOD"},
    measure_column_map={"OBS_COUNT": "OBS_COUNT"},
    attribute_column_map={"GEO_NAME": "GEO_NAME"},
    structural_version="STATCAN:DSD_CP(2021)",
    source_registry="local",
    materialized_at=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    bound_at=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
)

with sl.LocalRegistry("lake.duckdb", source_registry="local") as local:
    local.add(binding)
```

Round-trips through `LocalRegistry.add` / `get` like any other artefact:
the binding's URN mirrors the dataflow's identity (one binding per
dataflow per version).

---

## End-to-end: structures from a registry, data from DuckLake

A registry holds artefacts, holds the bindings that are *registrations* over
them, and owns the store their rows live in — so one object answers both
planes. Federating the structural side does not change that: a
`FederatedRegistry` routes structure by agency, and the local registry is where
the bindings and the lake are.

```python
import sdmxlib as sl

# The local store: bindings, cached structures, and the observation lake.
# `LocalRegistry.bundle("/srv/census-2021")` opens a published one the same way.
with sl.LocalRegistry("lake.duckdb", source_registry="local") as local:
    # Federate the *structural* side into it.
    estat = sl.RestRegistry(sl.Provider.ESTAT, store=local)
    sdmx = sl.RestRegistry(sl.Provider.SDMX_GLOBAL, store=local)
    fed = sl.FederatedRegistry(routing={"ESTAT": estat, "SDMX": sdmx}, default=local)

    # Structures come from wherever the routing says…
    dsd = fed.get(sl.DataStructure.urn_for(agency="ESTAT", id="CPI"), resolve=True)

    # …and observations from the registry that has the rows.
    obs = local.obs(
        sl.Dataflow.urn_for(agency="STATCAN", id="CENSUS_PROFILE", version="2021")
    )
    df = obs.filter(obs.col("REF_AREA") == "CA").frame(labels="en")
```

!!! note "One registry answers both planes"

    A registry looks up the binding for a dataflow and delegates to the store
    its rows live in, so no separate catalog object composes the two.

Notes on the moving parts:

- `DuckDBDataStore` shares a connection with `LocalRegistry`. The
  structural and observation sides live in one DuckDB session, so
  cross-database joins (lazy codelists in `sdmx.*` joining
  observations in `lake.*`) work without file-handle juggling.
- Filter clauses use SDMX dimension/measure ids. The binding's column
  maps determine the physical column names — for v1, identity mapping
  (`REF_AREA → REF_AREA`) is the common case.
- `frame(labels="en")` adds a `REF_AREA_label_en` column per enumerated
  component, read from the dataflow's DSD in the registry — the data
  store itself never sees a codelist.

---

## Operational tooling

### Browse all locally-bound dataflows

```python
with sl.LocalRegistry("lake.duckdb") as local:
    for entry in local.browse():
        name = entry.name["en"] if entry.name else "(no name)"
        print(f"{entry.dataflow_urn} — {name}  [source={entry.source_registry}]")
```

`browse()` returns `DataflowEntry` rows: `dataflow_urn`, `binding`,
`name` (resolved from the structural side), and `source_registry`
(provenance). v1 lists only locally-bound dataflows — remote-only
listing is on the follow-up roadmap.

### Detect drift after upstream republishes

```python
with (
    sl.LocalRegistry("lake.duckdb") as local,
    sl.RestRegistry(sl.Provider.ESTAT, store=local) as estat,
):
    fed = sl.FederatedRegistry(routing={"ESTAT": estat}, default=local)
    for entry in local.browse():
        for issue in fed.check_binding_drift(entry.binding):
            print(f"[{issue.kind}] {entry.dataflow_urn}: {issue.description}")
```

`check_binding_drift` returns one `DriftIssue` per detected mismatch
— empty list means clean. Use it for inspection (display drift in a
UI, log it, decide what to do). For fail-loud enforcement (refuse to
proceed if anything has drifted), call `validate_binding(binding)` —
that variant raises `DriftError` carrying the full issue list.

The drift kinds reported in v1:

- `dataflow_missing` — the dataflow disappeared upstream.
- `dsd_unresolvable` — the dataflow exists but its DSD ref can't be
  resolved.
- `structural_version_drift` — the binding's pinned DSD version no
  longer matches the registry's current DSD URN. The data was written
  against an older codelist set; re-bind or re-ingest.

Detailed codelist content diffs (added/removed/renamed codes,
dimension reorder) are tracked as a follow-up.

### Provenance lookup

Every materialised artefact carries `source_registry` in the
`artefact` spine. To audit cache provenance directly:

```python
with sl.LocalRegistry("cache.duckdb") as local:
    rows = local.execute("""
        SELECT artefact_type, agency_id, resource_id, source_registry, fetched_at
        FROM sdmx.artefact
        WHERE source_registry IS NOT NULL
        ORDER BY artefact_type, agency_id, resource_id
    """)
    for row in rows:
        print(row)
```

### Custom SQL against the data plane

`DuckDBDataStore.obs` (the path language over a binding) covers the
common read paths, but
some consumers need SQL those methods don't express — per-value
`printf` formatting, Hive-partition-pruning filters, wide pivots, custom
pagination. Two supported seams make this safe without reaching into
private helpers:

- `store.session()` / `store.execute()` — the escape hatch for running your
  own SQL. Both use the same per-thread handle the DSL runs on, so settings
  and registered relations are shared with it.
- `store.inspect()` — a `SchemaInspector` that reflects the live schema
  (cached `DESCRIBE`) and turns a `DataflowBinding` into a `QueryPlan`, so
  you reuse the store's table-name convention and sidecar join-key
  derivation instead of rebuilding them.

```python
with sl.LocalRegistry("lake.duckdb") as local:
    store = sl.DuckDBDataStore.from_registry(local)
    insp = store.inspect()

    # Reflection — column discovery for your own JOINs.
    cols = insp.columns("census_2021.observations")

    # Planning — resolve a binding's FROM side, then splice your own SQL onto it.
    plan = insp.plan(binding)  # QueryPlan(main, sidecar, join_keys, from_clause)
    sql = (
        f"SELECT REF_AREA, printf('%d', OBS_COUNT) AS value "
        f"FROM {plan.from_clause} WHERE GEO_LEVEL = ?"  # from_clause is sdmxlib-derived
    )
    with store.session() as active:
        rows = active.execute(sql, ["CTRY"]).fetchall()

    # or, for one statement and no handle to hold:
    rows = store.execute(sql, ["CTRY"])
```

`insp.shared_columns(a, b)` derives the join keys when observations and their
series attributes live in two tables.

`store.connection` is still there, but it is the store's *base* connection —
a single object shared by every thread, and the one the library stopped
executing on because `DuckDBPyConnection` is not thread-safe. Use it
for setup and DDL, or to hand to a component that derives its own handle. For
running queries, use `session()` or `execute()`.

`inspect()` returns the same inspector the internal query path uses, so its
`DESCRIBE` cache is shared — you don't pay repeated reflection cost, and you
don't need to memoize it yourself. The cache is invalidated automatically
when `write_observations` rewrites a table.

---

## Patterns

- **One agency, one source.** `FederatedRegistry` v1 routes by
  agency without arbitration. Two registries publishing the same URN
  to the same local store trigger a `RuntimeWarning` (last-write-wins).
- **Local store is the cache.** Materialise-through fills the local
  store; the read-before-fetch hook reads from it. Pinned URNs cache
  forever; `(latest)` URNs are never served from the store, only reused
  in-process for as long as the provider's `Cache-Control` allows.
- **Bindings are immutable references to pinned versions.** When the
  upstream DSD republishes, `check_binding_drift` enumerates the
  mismatches and `validate_binding` raises on them; you decide whether
  to re-ingest or re-bind. Following `(latest)` in a binding is opt-in
  via the `structural_version` field.
- **The data store knows nothing about codelists.** Result
  annotation runs in the registry, using the resolved DSD from the
  structural side. The `DataStore` Protocol stays minimal so non-DuckDB
  backends (Iceberg, Spark) can drop in without re-implementing
  labelling.
