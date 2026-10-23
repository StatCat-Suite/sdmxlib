# Working with a Local Registry

A guide to using `LocalRegistry` for persistent, low-memory access to
SDMX structural metadata. Covers populating a local store from a remote
endpoint, querying without network access, lazy codelist access, and
keeping the store up to date.

---

## Before You Start

```python
import sdmxlib as sl
```

`LocalRegistry` stores artefacts in a DuckDB file. The `duckdb` package
is a required dependency of sdmxlib.

---

## 1. What a Local Registry is for

When you work with large SDMX registries -- hundreds of codelists, some
with tens of thousands of codes -- loading everything into memory every
time is slow and wasteful. A `LocalRegistry` solves this:

- **Persistent** -- artefacts survive process restarts, no re-fetching
- **Low memory** -- codelist codes stay in DuckDB until you need them
- **Fast startup** -- opening a `.duckdb` file takes milliseconds, not seconds
- **Mutable** -- add, drop, or replace artefacts as versions evolve

---

## 2. Creating and Populating a Local Store

The simplest path is to fetch from a remote registry and dump everything
into a local store:

```python
import sdmxlib as sl

with sl.RestRegistry(sl.Provider.BIS) as remote:
    dsd = remote.get(sl.DataStructure.urn_for(agency="BIS", id="WS_CBPOL"), resolve=True)

    # The remote registry has accumulated all fetched artefacts
    with sl.LocalRegistry("bis_cbpol.duckdb") as local:
        local.ingest(remote.registry)
        print(local)
```

```
LocalRegistry(Codelist=7, ConceptScheme=1, DataStructure=1)
```

`remote.registry` is the `InMemoryRegistry` every artefact the remote session
fetched accumulates in; `ingest()` copies all of it in one transaction.

You can also add artefacts one at a time, including ones you built yourself:

```python
cl = sl.Codelist(
    id="CL_FREQ",
    maintainer="MY_ORG",
    version="1.0",
    name={"en": "Frequency"},
    codes=[
        sl.Code(id="A", name={"en": "Annual"}),
        sl.Code(id="Q", name={"en": "Quarterly"}),
    ],
)

with sl.LocalRegistry("my_store.duckdb") as local:
    local.add(cl)
    local.add(my_dsd)
    local.add(my_dataflow)
```

`add()` replaces whatever the store already holds under the same URN, and
`add_all([...])` adds a batch in one transaction, in any order.

The `.duckdb` file is a standard DuckDB database: portable, and it survives
process restarts.

```python
# Session 1: populate
with sl.LocalRegistry("store.duckdb") as local:
    local.add(my_codelist)

# Session 2: read back
with sl.LocalRegistry("store.duckdb") as local:
    cl = local.get(codelist_urn)
    print(cl.id)  # CL_FREQ
```

---

## 3. Querying the Local Store

Once populated, use the local store without network access. The API
mirrors `InMemoryRegistry`, and `get()` takes a URN (or a `Ref`) — never a
string:

```python
with sl.LocalRegistry("bis_cbpol.duckdb") as local:
    urn = sl.DataStructure.urn_for(agency="BIS", id="WS_CBPOL", version="1.0")
    dsd = local.get(urn)

    print(dsd.id)
    for dim in dsd.dimensions:
        print(f"  {dim.id}")
```

```
WS_CBPOL
  FREQ
  REF_AREA
  ...
```

### Traversal syntax works out of the box

When you `get()` a DSD (or any artefact with Refs), the same read loads
what it references from DuckDB and binds each `Ref` to it; a `Ref` finds its
target when you access it. So standard traversal syntax works exactly as it
does with `RestRegistry`:

```python
with sl.LocalRegistry("bis_cbpol.duckdb") as local:
    urn = sl.DataStructure.urn_for(agency="BIS", id="WS_CBPOL", version="1.0")
    dsd = local.get(urn)

    # Navigate dimensions → codelists (resolved to LazyCodelist)
    freq_cl = dsd.dimensions["FREQ"].codelist
    print(type(freq_cl).__name__)  # LazyCodelist
    print(freq_cl.codes["M"].name["en"])  # "Monthly"

    # Aggregate helpers work too
    cm = dsd.code_map(lang="en")  # {dim_id: {code_id: label}}
    labels = dsd.column_labels(lang="en")  # {dim_id: concept label}
    all_cls = dsd.all_codelists  # {dim_id: LazyCodelist}
```

Codelist Refs resolve to `LazyCodelist` objects, so codes stay in DuckDB
until you actually access them.

<!-- prettier-ignore -->
!!! tip "Missing targets"
    A `Ref` to an artefact the store does not hold stays unresolved. Reading
    the artefact that holds it is not an error, but `ref.resolved` is `False`
    and calling `ref()` raises `UnresolvedRef`.

### When a stored artefact breaks a rule

A store read checks each artefact it builds the way `add` does. `get()` raises
on an artefact a rule refuses (an `sl.SdmxParseError`, listing why). Every other read — `get_all()`, a query's
`collect()`, resolving a reference — leaves it out, records it in
`local.refused` with the reason, and reads on; a reference to it stays
unresolved. `add`, `drop` and `replace` clear the list, since what they
changed may be what was refused.

```python
with sl.LocalRegistry("bis_cbpol.duckdb") as local:
    local.get_all(sl.Codelist)
    print(local.refused)  # ()
```

### Browse by type

```python
with sl.LocalRegistry("bis_cbpol.duckdb") as local:
    for cl in local.get_all(sl.Codelist):
        print(f"{cl.id}: {len(cl)} codes")
```

### Check existence

```python
urn = sl.Codelist.urn_for(agency="BIS", id="CL_FREQ", version="1.0")

with sl.LocalRegistry("bis_cbpol.duckdb") as local:
    print(urn in local)  # True
    print(len(local))  # total artefact count
```

---

## 4. Lazy Codelists

This is the key performance feature. When you `get()` a codelist from a
`LocalRegistry`, you receive a `LazyCodelist` -- a lightweight facade that
fetches codes from DuckDB on demand rather than loading them all into
memory.

```python
with sl.LocalRegistry("bis_cbpol.duckdb") as local:
    urn = sl.Codelist.urn_for(agency="BIS", id="CL_BIS_IF_REF_AREA", version="1.0")
    cl = local.get(urn)

    print(type(cl).__name__)  # LazyCodelist
    print(len(cl))  # count without loading codes

    # Single code lookup -- one SQL query
    code = cl.codes["US"]
    print(f"{code.id}: {code.name['en']}")
```

```
LazyCodelist
44
US: United States
```

### What you can do with a LazyCodelist

`cl.codes` is a **query**, not a list: it describes SQL that has not run.
Nothing executes until you ask for something, which is what lets a filter be
pushed into the database rather than applied after fetching everything. It
does not iterate, and `len()` on it raises rather than running silently — ask
for ids, a count, or the artefacts.

All of it reads through the registry's connection, so it belongs **inside the
block** — a `LazyCodelist` outliving its registry raises rather than answering
from a copy it never made:

```python
with sl.LocalRegistry("bis_cbpol.duckdb") as local:
    cl = local.get(sl.Codelist.urn_for(agency="BIS", id="CL_BIS_IF_REF_AREA", version="1.0"))

    # Materialise the rows
    for code in cl.codes.collect():
        print(code.id)

    # Ask the database instead of counting in Python
    print(cl.codes.count())                       # one COUNT(*), no objects built
    print(cl.codes.filter(sl.q.code.id == "US").exists())  # True

    # Check membership on the codelist itself
    print("US" in cl)  # True

    # List all ids
    print(cl.codes.ids())  # ["AE", "AR", "AU", ...]

    # Hierarchical access — SKOS-aligned, returns a lazy `Query`
    roots = cl.roots().collect()  # list[Code]
    children = cl.narrower("WORLD").collect()
    ancestors = cl.broader_transitive("FRA")  # lazy Query → ancestors of FRA

    # Filter (materialises matching codes only)
    filtered = cl.codes.filter(sl.q.code.id.str.starts_with("C")).collect()

    # Bulk projections — one SQL each, dict[str, ...] returned
    depths = cl.depths()  # {code_id: depth}
    parents = cl.broader_map()  # {code_id: parent_id | None}
    ancestors_map = cl.ancestors_map()  # {code_id: tuple of ancestor ids, root-first}
```

### Getting a real Codelist

When you need a standard `Codelist` object (e.g. to pass to a function
that expects one), use `materialize()` or pass `eager=True`:

```python
with sl.LocalRegistry("bis_cbpol.duckdb") as local:
    urn = sl.Codelist.urn_for(agency="BIS", id="CL_BIS_IF_REF_AREA", version="1.0")
    cl = local.get(urn)

    # Option 1: materialize from lazy
    real_cl = cl.materialize()
    print(type(real_cl).__name__)  # Codelist

    # Option 2: eager get
    real_cl = local.get(urn, eager=True)
    print(type(real_cl).__name__)  # Codelist
```

!!! warning "Keep eager reads of large artefacts off the request path"

    It builds a Python object per item and resolves each one's references, so
    it costs what the object graph weighs. Measured on a real bundle:

    | artefact | items | eager `get()` | retained |
    | --- | --- | --- | --- |
    | a 28-node hierarchy | 28 | 0.11 s | 5 MB |
    | `H_CP_GEO_ADMINISTRATIVE` | 63,404 | 0.93 s | 135 MB |
    | `CL_GEOGRAPHY` | 579,951 | 2.00 s | 404 MB |
    | `H_CP_GEO_FED` | 499,138 | 7.25 s | 913 MB |

    A hierarchy node costs about **4x** a code, because it pays for its own
    construction *and* for the `Code` its `Ref` resolves to.

    The lazy artefact answers most questions without any of that, on those
    same two giants:

    | | |
    | --- | --- |
    | `local.get(urn)` — lazy | 0.7–1.4 ms |
    | `len(codelist)` / `hierarchy.nodes.count()` | 18 ms |
    | `hierarchy.nodes.ids()` | 97 ms |
    | `local.code_map(urn, lang="en")` | 140 ms |
    | `codelist.codes.frame()` | 147 ms |

    So reach for `eager=True` or `materialize()` when you genuinely need the
    object graph — passing a real `Codelist` to something that expects one,
    serialising it to SDMX-ML. For counting, listing, labelling or analysing,
    stay lazy: it is the same data two to four orders of magnitude cheaper.
    And when you need full `Code` objects — annotations, `parent_id` — for
    *some* codes, `codelist.codes_for(ids)` builds exactly those in one batched
    read, eager or lazy alike; it is what the SDMX-JSON data writer uses to
    label the codes a message observed (#410).

### Running without pandas

sdmxlib does not depend on pandas, and neither does DuckDB. But when pandas is
**not installed**, DuckDB's Python client retries `import pandas` twice for
every value it binds as a query parameter — it checks whether the value is
`pandas.NA` — and never remembers that the import failed (#411). Every
parameterised read pays it: ~200 failed imports to read one dataflow, and
binding gets 40-60x slower per value, so a long `is_in` list feels it most.
Each failed import walks `sys.path`, so a long path makes it worse.

An *application* that knows pandas is absent can stop the retries, once, at
startup and before the first query:

```python
import importlib.util
import sys


def stop_pandas_probes() -> None:
    """Make `import pandas` fail at once instead of walking `sys.path` again."""
    if importlib.util.find_spec("pandas") is None:
        sys.modules["pandas"] = None
```

That tells every library in the process something slightly different from the
truth, and some read it wrongly: polars then reports pandas as available, and
`to_pandas()` in polars and pyarrow fails with
`AttributeError: 'NoneType' object has no attribute '__version__'` rather than
`No module named 'pandas'`. `import pandas` itself still raises
`ModuleNotFoundError`. If nothing in your process converts to pandas the trade
is usually worth it — binding 5,000 values drops from ~400 ms to ~35 ms — and
that is why sdmxlib leaves it to you rather than doing it on import.

---

## 5. Direct SQL Code Maps

The biggest performance win is `code_map()` -- it returns
`{code_id: label}` for a codelist using a single SQL query, without
creating any `Code` objects:

```python
with sl.LocalRegistry("bis_cbpol.duckdb") as local:
    urn = sl.Codelist.urn_for(agency="BIS", id="CL_BIS_IF_REF_AREA", version="1.0")

    # On the LazyCodelist
    cl = local.get(urn)
    cm = cl.code_map(lang="en")
    print(cm["US"])  # "United States"

    # Or directly on the registry
    cm = local.code_map(urn, lang="en")
    print(cm["US"])  # "United States"
```

For a full DSD, `dsd_code_map()` builds the mapping across all enumerated
components in one call:

```python
with sl.LocalRegistry("bis_cbpol.duckdb") as local:
    dsd_urn = sl.DataStructure.urn_for(agency="BIS", id="WS_CBPOL", version="1.0")
    cm = local.dsd_code_map(dsd_urn, lang="en")

    for comp_id, codes in cm.items():
        print(f"{comp_id}: {len(codes)} codes")
```

---

## 6. Updating the Store

### Replacing a version

When a new version of a codelist is published:

```python
with sl.LocalRegistry("my_store.duckdb") as local:
    old_urn = sl.Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")
    local.replace(old_urn, new_codelist_v2)
```

### Dropping an artefact

```python
with sl.LocalRegistry("my_store.duckdb") as local:
    urn = sl.Codelist.urn_for(agency="SDMX", id="CL_OBSOLETE", version="1.0")
    removed = local.drop(urn)
    print(f"Removed: {removed}")  # True if it existed
```

Dropping a codelist automatically removes all its codes from the
database.

### Refreshing from a remote registry

```python
with sl.RestRegistry(sl.Provider.BIS) as remote:
    dsd = remote.get(sl.DataStructure.urn_for(agency="BIS", id="WS_CBPOL"), resolve=True)

    with sl.LocalRegistry("bis_cbpol.duckdb") as local:
        local.ingest(remote.registry)  # upserts all artefacts
```

`ingest()` upserts: an artefact already stored under the same URN is
replaced with the fresh one.

---

## 7. Resolver Cache

`LocalRegistry` keeps a bounded cache of resolved `Ref` targets, shared
across `get()` / `get_all()` / `to_registry()`, so workloads that iterate
many artefacts sharing a heavy parent (every MetadataSet of a Metadataflow
referencing the same MSD; every dimension of a DSD referencing the same
ConceptScheme) only pay for that parent once. Eviction is first-in,
first-out rather than least-recently-used: tracking recency would mutate
the cache on every hit and put every reader behind its lock, and the
lock-free read path is worth more than the small difference in hit rate.

```python
# Default: 1,000,000 *items* — see below for what an item weighs.
local = sl.LocalRegistry("store.duckdb")

# Tune for big web services, or disable entirely:
local = sl.LocalRegistry("store.duckdb", resolve_cache_items=4_000_000)
local = sl.LocalRegistry("store.duckdb", resolve_cache_items=0)  # off
```

The cache is automatically cleared on every `add` / `drop` / `replace`
so reads never see stale data after a mutation. For explicit
invalidation hooks (e.g. when an upstream registry mutates without
going through your local mirror) call `clear_resolve_cache()`:

```python
local.clear_resolve_cache()
```

`resolve_cache_info()` exposes counters for ops dashboards and
integration tests:

```python
info = local.resolve_cache_info()
# {"entries": 42, "weight": 812_004, "max_items": 1_000_000,
#  "hits": 9123, "misses": 50, "refused": 0}
```

**The bound counts items.** An entry is a whole artefact, and artefacts
differ in size by four orders of magnitude — a codelist of 579,951 codes
retains ~400 MB, an empty dataflow ~0.08 MB. A bound of "256 entries" could
therefore hold 4.4 GB while reporting 2% full (#386).

Measured per item: **~0.7 KB per code and ~2.1 KB per hierarchy node**, so the
1,000,000-item default is roughly 0.7–2.0 GB. Artefacts holding no items weigh a floor of 64, which is what
they actually retain, and is what stops a flood of small ones being
unbounded.

An artefact heavier than the whole bound is **refused** rather than
cached and immediately evicted — otherwise one huge read would empty the
cache on its way out. `refused` counts them: rising `refused` with a poor
hit rate means the working set does not fit, which is a different fix
from a cold cache.

Pass `0` to disable for workloads where every request resolves unique
URNs (a cache would only hurt). Raise it for long-running read-heavy services
with a stable working set — and past your largest artefact, or that artefact
is `refused` and never cached at all.

**Single-call correctness:** the resolver uses a per-call overlay over
the shared cache, so `resolve_cache_items=0` and tight bounds never
produce unresolved `Ref`s mid-walk — eviction can never affect a
resolution that's already in flight.

---

## 8. Escape Hatch: Converting to an In-Memory Registry

If you need the full in-memory artefact graph, every reference bound to it
(e.g. for passing to code that expects an `InMemoryRegistry`), use
`to_registry()`:

```python
with sl.LocalRegistry("bis_cbpol.duckdb") as local:
    reg = local.to_registry()

    # reg is an InMemoryRegistry holding every artefact, each Ref bound to it
    print(len(reg))
```

This loads everything into memory -- use it when you genuinely need the
full graph, not as a default.

---

## 9. Shared Connections

`LocalRegistry.from_connection()` wraps a `duckdb.DuckDBPyConnection` you
already have, for shared-database workflows (e.g. sharing a DuckDB file with
statcatlib). The constructor takes a path, not a connection:

```python
import duckdb

conn = duckdb.connect("shared.duckdb")
with sl.LocalRegistry.from_connection(conn) as local:
    local.add(my_codelist)
# conn is still open, and holds what was added
print(conn.execute("SELECT count(*) FROM sdmx.artefact").fetchone())  # (1,)
```

A wrapped connection is not closed on exit -- the caller retains ownership.

---

## 10. Multilingual Support

All languages are stored automatically using DuckDB `MAP(VARCHAR, VARCHAR)`
columns -- no upfront language configuration needed. Any language present
in the data is queryable:

```python
with sl.LocalRegistry("store.duckdb") as local:
    local.add(bilingual_codelist)
    cm_en = local.code_map(urn, lang="en")
    cm_fr = local.code_map(urn, lang="fr")
```
