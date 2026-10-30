# Building an SDMX REST Facade

Recipes for putting an SDMX REST API in front of a `LocalRegistry` —
parsing inbound URL slices into typed filters, streaming results, and
serialising bulk SDMX-CSV output.

Throughout, `reg` is an open registry holding the structures and the
bindings, and `flow_urn` names the dataflow the request is for:

```python
reg = sl.LocalRegistry.edition("edition")  # /srv/products/census/<edition_id> in a deployment
flow_urn = sl.Dataflow.parse_rest_ref("ESTAT,STS_INPR_M,1.0")  # AGENCY,ID,VERSION
```

This page is for publishers of SDMX data. If you're fetching from a public
registry, see [Observation Data](data.md).

The kit is four small pieces, designed to compose:

| Piece | Purpose |
| ----- | ------- |
| `sl.parse_components` | URL key + `c[…]=…` query params → `ComponentFilter` |
| `LocalRegistry.obs` | Dataflow → typed query; `.stream()` gives Arrow record batches |
| `sdmxlib.formats.sdmx_csv.write_sdmx_csv` | Record batches → SDMX-CSV bytes |
| `Dataflow.parse_rest_ref`, `StructureMessage.containing` | URL ref + artefact dispatch |

---

## Parsing an SDMX REST key into a `ComponentFilter`

The SDMX REST 2.0 URL `/data/{flow}/{key}?c[<id>]=<vals>&…` identifies
a slice with two filter mechanisms:

- A *dotted key* over the DSD's key dimensions (`CA+US.M.2021`,
  `..2021`, `all`).
- The 2.0 `c[<id>]=<vals>` query params which constrain *any* component
  — most importantly attributes like `c[GEO_LEVEL]=FED`.

`sl.parse_components` is the canonical parser. The HTTP layer parses
the URL into the two halves; sdmxlib normalises their meaning:

```python
import sdmxlib as sl

dsd = reg.dsd(flow_urn)

cf = sl.parse_components(
    "CA+US.M.2021",  # dotted-key path segment
    {"GEO_LEVEL": ["FED"]},  # parsed from c[…]=…
    dsd.key_slot_ids("3.0"),  # 3.0 gives the time dimension a segment; 2.1 does not
)

cf.dimensions  # [['CA', 'US'], ['M'], ['2021']]
cf.attributes  # {'GEO_LEVEL': ['FED']}
```

Wildcard semantics:

| URL fragment | Meaning |
| ------------ | ------- |
| `all` or `""` | every observation |
| `..2021` | wildcard on first two dims, `TIME_PERIOD = 2021` |
| `CA+US.M.` | OR within a dim, wildcard on the last |
| `c[GEO_LEVEL]=FED+PROV` | OR within an attribute |

A mismatched segment count raises `ValueError` with the DSD's key
dimensions in the message — fail-fast for malformed URLs.

---

## Filtering on attributes with `c[…]=…`

Real workloads filter on attributes that aren't in the key. The
canonical example is StatCan Census Profile's `GEO_LEVEL` — derivable
from `REF_AREA`, so it can't be a key dimension, but every bulk
download filters on it ("give me all federal electoral districts").

Without `c[…]=…` you would hand-roll attribute WHERE clauses in raw SQL,
or expand the attribute filter into a 50KB OR-list on `REF_AREA`.

`ComponentFilter.attributes` is translated against
`binding.attribute_column_map` exactly the same way `dimensions` is
translated against `binding.dimension_column_map`. When an attribute
lives on `binding.series_attributes_location` (the JOINed sidecar
table), filtering still works — same plumbing.

---

## Filtering by hierarchy: lazy SQL relations

A common pattern: "give me all dissemination areas in Ontario." That's
a hierarchy traversal — `Codelist.narrower_transitive("ON_CODE")` —
which can resolve to ~50k REF_AREA codes. Materialising those into a
Python list and binding them as 50k WHERE-IN parameters works on
DuckDB, but binding is slow and other backends cap bind parameters.

`ComponentFilter` filter values accept either an eager `list[str]`
*or* a `SqlRelation` — a lazy single-column subquery. `LazyCodelist`'s
hierarchy methods (`narrower_transitive`, `roots`, etc.) return
a projection, which satisfies `SqlRelation`. The SQL builder splices
the subquery inline:

```python
import sdmxlib as sl

lazy_cl = reg.get(geo_codelist_urn)  # a LazyCodelist
ontario_areas = lazy_cl.narrower_transitive("ON_CODE").select(sl.q.code.id)  # lazy, not a list

cf = sl.parse_components(
    "all",
    {"REF_AREA": ontario_areas},  # relation, not list
    dsd.key_slot_ids("3.0"),
)

df = reg.obs(flow_urn).filter_components(cf).frame()
```

The emitted SQL becomes:

```sql
WHERE REF_AREA IN (SELECT c.code_id FROM sdmx.code c WHERE c.codelist_urn = ? AND ...)
```

— one fragment, a handful of bind parameters, regardless of how many
codes the traversal resolves to.

**Mixing forms** is fine: one dimension can take a relation while
another takes an eager list. The only restriction is that you can't
combine a dotted-key segment for a key dimension with a relation for
the same dimension — relations don't merge with eager lists. Use one
path or the other.

**Connection scoping**: the relation's subquery executes on the data
store's connection. For `DuckDBDataStore` (which shares a connection
with `LocalRegistry`), codelist relations compose automatically because
both sides see the same `sdmx.code` table.

---

## Running a streaming query

`stream()` is `frame()`'s bounded-memory sibling: the same query, yielding
`pyarrow.RecordBatch` instead of materialising a DataFrame. Peak RSS is flat —
5 MB against `frame()`'s 441 MB on 4M rows, and it does not move at 8M.

```python
batches = reg.obs(flow_urn).filter_components(cf).stream(chunk_rows=10_000)
for batch in batches:
    process(batch)  # one chunk at a time
```

It is the same query object either way, so every filter, projection and
aggregate above applies.

**`chunk_rows` sets the batch size** — 1,000 and 50,000 measure identically.
Size it for the response chunk you want to write.

**A sort decides peak memory.** A sort materialises the
whole result before the first batch can leave, so an *ordered* stream is not
bounded at all — 210 MB on 4M rows, 368 MB on 8M, whatever `chunk_rows` says.
A facade streaming a big slice must not add an `ORDER BY`; `stream()` drops the
default key tiebreak for exactly this reason. See
[Observation Data](data.md#streaming-a-large-query-result).

---

## End-to-end: SDMX-CSV bulk download

The natural sink for `stream()` is the SDMX-CSV 2.0 writer.
`write_sdmx_csv` consumes record batches, prepends the
`STRUCTURE,STRUCTURE_ID,ACTION` envelope columns, and emits UTF-8 bytes
chunk-by-chunk — header on the first chunk, data on every chunk
including the first.

```python
from sdmxlib.formats.sdmx_csv import write_sdmx_csv


def download(key, query_components):
    cf = sl.parse_components(key, query_components, dsd.key_slot_ids("3.0"))

    chunks = write_sdmx_csv(
        reg.obs(flow_urn).filter_components(cf).stream(),
        dsd,
        structure_id="ESTAT:STS_INPR_M(1.0)",
    )

    for chunk in chunks:
        yield chunk  # FastAPI/Starlette StreamingResponse


list(download("all", {}))
```

The writer uses Polars' Rust CSV serialiser, so it isn't the
bottleneck — bulk SDMX-CSV downloads are network-bound, not
writer-bound.

---

## Mapping URL refs to typed URNs

SDMX REST URLs identify maintainable artefacts as `AGENCY,ID,VERSION`.
`parse_rest_ref` parses that form, symmetric with `urn_for(...)`:

```python
flow_urn = sl.Dataflow.parse_rest_ref("ESTAT,STS_INPR_M,1.0")
codelist_urn = sl.Codelist.parse_rest_ref("SDMX,CL_FREQ,1.0")
```

A `/structure/{type}/{agency}/{id}/{version}` handler resolves an
artefact and needs to wrap it in a `StructureMessage` with the right
slot. Skip the isinstance chain:

```python
artefact = reg.get(codelist_urn)
message = sl.StructureMessage.containing(artefact)
# — equivalent to StructureMessage(dataflows=[artefact]) etc., dispatched
# by artefact type via the msg_field ClassVar.
```

---

## Putting it together

A FastAPI handler for `GET /data/{flow}` becomes:

```python
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
import sdmxlib as sl
from sdmxlib.formats.sdmx_csv import write_sdmx_csv

app = FastAPI()


def parse_c_params(params) -> dict[str, list[str]]:
    """`c[GEO_LEVEL]=FED+PROV` → `{"GEO_LEVEL": ["FED", "PROV"]}`.

    Yours to write: the framework owns URL-decoding and this is the shape
    `parse_components` wants.
    """
    return {
        name[2:-1]: value.split("+")
        for name, value in params.items()
        if name.startswith("c[") and name.endswith("]")
    }


@app.get("/data/{flow}/{key:path}")
def get_data(
    flow: str,
    key: str,
    request: Request,
):
    flow_urn = sl.Dataflow.parse_rest_ref(flow)

    dsd = reg.dsd(flow_urn)

    # Pull c[<id>]=<vals> out of the query string. The web framework owns
    # URL-decoding; sdmxlib normalises the meaning.
    component_filters = parse_c_params(request.query_params)
    cf = sl.parse_components(key, component_filters, dsd.key_slot_ids("3.0"))

    return StreamingResponse(
        write_sdmx_csv(
            reg.obs(flow_urn).filter_components(cf).stream(),
            dsd,
            structure_id=f"{flow_urn.agency}:{flow_urn.id}({flow_urn.version})",
        ),
        media_type="application/vnd.sdmx.data+csv;version=2.0.0",
    )
```

Exercised without a server, which is also how you would test yours:

```python
from fastapi.testclient import TestClient

response = TestClient(app).get(
    "/data/ESTAT,STS_INPR_M,1.0/all", params={"c[GEO_LEVEL]": "FED"}
)
assert response.status_code == 200
assert response.text.startswith("STRUCTURE,STRUCTURE_ID,ACTION,")
```

Every translation lives in sdmxlib; the facade is plumbing.
