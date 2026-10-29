# Pushing an observation query to SDMX REST

The registry design's landing plan
([evidence/registry-landing.md](evidence/registry-landing.md) §6) made `obs()`
on `RestRegistry` the first step, because it is the one that could still show
the unification is wrong. This document is that step's design. When it was
written none of it existed: `Request.path` was hardcoded `f"structure/{…}"`,
`Capability` / `Push` / the closure were all structural, and nothing mapped a
`Pred[ObsRow]` onto a key.

> **Status: built**, except the streaming rule of §4 (§5 step 6).
> `RestRegistry.obs(flow)`
> returns an `ObsQuery` whose terminals fetch `/data` first and then run the
> original query locally:
>
> ```python
> with sl.RestRegistry(sl.Provider.ECB) as ecb:
>     obs = ecb.obs(sl.Dataflow.urn_for(agency="ECB", id="EXR"))
>     obs.filter(obs.col("CURRENCY").is_in(["USD", "GBP"])).frame()
> ```
>
> | piece | where |
> | --- | --- |
> | role-keyed shapes; `SDMX_REST_DATA_21`, `SDMX_REST_DATA_30` (declarations the tests check; the request is built by `data_request_for`) | `query/_pushdown.py` |
> | `DataRequest`, and the lowering `data_request_for` | `query/_rest.py` |
> | `ComponentRole` (`KEY_DIMENSION`, `TIME_DIMENSION`, `MEASURE`, `ATTRIBUTE`) | `model/datastructure.py` |
> | `obs()`, `_fetch_data_for` | `api/registry.py` (`RestRegistry`) |
> | the fetch: the response to a temporary file, loaded whole into the query store | `SdmxClient.stream_data_at`, `DuckDBDataStore.ingest_csv` |
>
> **§5 step 4, the refusal, is applied (#515).** The data capabilities declare
> the bound (`bounding={DATA_ROOT: frozenset()}` — any pushed constraint
> bounds the scan), and `is_bounded` returns `False` for a plan that pushes
> nothing (`tests/unit/test_data_bounding.py`). `RestRegistry` does not call
> `is_bounded`: it lowers a data plan with `data_request_for` directly and
> states the same rule on the request it built. A residual with nothing
> pushed — no key slot, period or component, the `/data/…/all` fetch — makes
> `_fetch_data_for` call `_refuse_unbounded_fetch`, which raises
> `PushdownError` before the request (`DataRequest.narrows`). Until #515 it fetched the
> whole flow and filtered locally. An unfiltered query is not refused.

Three questions decide it: what the wire can hold, what `Capability` has to
grow, and what bounds a data fetch. §4 records how streaming over REST works.

## 1. What the wire can hold

The structural request surface is six fields and no predicate slot — that is
§1.1 of the [registry design](registry-and-catalog.md), and it is why structural queries fetch and then
evaluate. **The data surface is different, and materially richer.**

The path (`SdmxClient._data_url` in `api/client.py`, written for the
since-retired `reg.data()`):

```
3.0   /data/dataflow/{agency}/{id}/{version}/{key}
2.1   /data/{agency},{id},{version}/{key}
```

and `{key}` is **positional, dot-separated, `+`-joined, empty for wildcard** —
`M.USD+GBP.EUR..A` — so it can express, per *key dimension*, an equality or a
set membership, in DSD dimension order. `_serialise_key` (in
`api/registry.py`, behind `reg.data()`, both retired in #516) built exactly
this from a `_KeySpec`, including the empty-slot wildcard and sorted codes for
stable URLs.

Beyond the key:

| parameter | expresses |
| --- | --- |
| `startPeriod` / `endPeriod` | a **range** on the time dimension |
| `c[<component>]=<values>` (3.0) | membership on **any** component — including attributes and measures, not only key dimensions |
| `updatedAfter` | a range on a vintage the query language does not model |
| `firstNObservations` / `lastNObservations` | a per-series slice, *not* a `LIMIT` |
| `dimensionAtObservation` | the shape of the response, not its content |
| `detail` | which of `dataonly` / `serieskeysonly` / `nodata` / `full` |

`c[…]` is the significant one, and the library already models it — from the
*server* side. `sdmxlib.rest.parse_components` turns a key plus `c[…]` params
into a `ComponentFilter`, and `ObsQuery.filter_components` consumes one. What
did not exist was the emitting direction. *(Built: `DataRequest.params` emits
`c[<id>]=v1+v2` on 3.0.)*

**So the data plane pushes more than the structural plane does.** Structure
pushes equality on identity and nothing else. Data pushes equality and
membership on every key dimension, a range on time, and — on 3.0 — membership
on any component at all. That is a real asymmetry between the two planes and it
should be visible in the capability, not smoothed over.

## 2. What `Capability` has to grow

`Capability` is declared over `PredicateShape` — kind, column, operator, fold —
keyed by path prefix, with `bounding` naming what makes a scan admissible. The
structural declaration is three shapes on identity columns
(`SDMX_REST_MAINTAINABLE`).

A data capability cannot be written the same way, for one reason: **its columns
are not fixed.** `PredicateShape(column="agency_id")` is meaningful because
every maintainable has that column. An observation's columns come from a DSD,
so `column="REF_AREA"` is a fact about one dataflow, not about the backend.

Two ways out, and they are not equivalent:

* **Shapes over column *roles*, not names.** `PredicateShape(kind="Compare",
  column=ComponentRole.KEY_DIMENSION, op="=")`, with the binding resolving a
  physical column to its role. The declaration stays finite and dataflow-independent,
  which is what makes it assertable in a test.
* **A capability parameterised by the DSD.** Built per dataflow at query time.
  Honest, and it gives up the property that a capability is data a test can
  print and compare — which `Capability`'s docstring says is the point of it
  being data rather than code.

**Recommendation: roles.** The four that matter are `KEY_DIMENSION` (equality
and membership, via the key), `TIME_DIMENSION` (range, via
`startPeriod`/`endPeriod`), `COMPONENT` (membership, via `c[…]`, 3.0 only) and
everything else (nothing). A DSD maps physical columns onto those, and the
capability never mentions a dataflow.

*As built* the enum is `ComponentRole` (`model/datastructure.py`), with
`KEY_DIMENSION`, `TIME_DIMENSION`, `MEASURE` and `ATTRIBUTE`; the `COMPONENT`
arm became `ATTRIBUTE` alone, because a filter on a measure is a filter on a
value, and no declaration can vouch for provider support of that. A root
built without a DSD has no roles (`Observations.role_of` returns `None`) and
pushes nothing (§6).

That also makes the 2.1/3.0 difference expressible as two capabilities rather
than as a conditional: 2.1 declares key dimensions and time; 3.0 adds
attributes. `FmrRegistry` and a 2.1-only provider then differ in *data*, which
is the design `Capability` already has.

*As built,* the two capabilities state the rule and the tests check them
(`tests/unit/test_data_capability.py`, `test_data_bounding.py`); no production
code reads them. `data_request_for` (`query/_rest.py`) lowers a plan itself
and branches on the API version: a non-key component goes to `c[…]` only when
the version is 3.0 and the component is an attribute.

## 3. What bounds a data fetch

Registry design §5.2 ([evidence/registry-landing.md](evidence/registry-landing.md))
settled this as **structural with an explicit opt-out** —
refuse unless at least one dimension is constrained, `allow_unbounded=True` to
override — and rejected `/availability` as the mechanism, because
`build_component_availability` returns which values exist, not a row count.

**Amended by registry design §5.12
([evidence/registry-landing.md](evidence/registry-landing.md)): there is no
bare-scan bound.**
The structural plane deliberately allows an unfiltered scan, because the rule
is about the **visibility** of cost rather than its magnitude, and
`obs(flow).frame()` is the visible kind — it says what it asks for.
The data plane states the same rule:

> Nothing is refused for being wide. A query is refused for being *misleadingly
> narrow* — a predicate the backend cannot evaluate, over a scan that names
> nothing to bound it.

So there is no `allow_unbounded=` to build, and the refusal to implement is the
narrow one: a residual over an unbounded data scan. *(Applied by
`RestRegistry.obs` since #515 — see the status note at the top.)* What protects a caller at
scale is the terminal they choose — `stream()` over `frame()`, and an
**unordered** stream, since a sort materialises the whole result before the
first batch leaves. Over REST that holds only for the read out of the query
store, after the fetch has loaded the whole slice into it (§4).

**`bounding` is per root, and an observation root's identity is the binding.**
`root_key(push.plan.root)` keys the requirement, and `Observations` is built
per binding rather than being a singleton — so whatever `root_key` returns for
it must be stable across two `Observations` over the same dataflow, or a
capability lookup misses and `is_bounded` returns `False` for everything.
*(As built, every observation root keys as one constant, `DATA_ROOT`.)*

## 4. Streaming over REST

A REST-backed observation query fetches first and then runs locally.
`_fetch_data_for` writes the `/data` response to a temporary file with
`SdmxClient.stream_data_at`, and `DuckDBDataStore.ingest_csv` loads the whole
file into a table in the query store; the original query then runs there.
`stream()` bounds memory only for the read out of that store. With the default
store, an in-memory DuckDB, the whole slice is in RAM before the first batch;
with a file-backed `store=` it is on disk, after a full download.
`Query.stream`'s docstring scopes its memory figures this way, and `explain()`
says whether a query fetches. There is no wire-level streaming path, for a
fully pushed query or any other.

Reading the response through a file costs about half the peak memory of
reading its body into polars. The two readings weighed, and the 2M-row
measurements, are in
[evidence/data-plane-pushdown.md](evidence/data-plane-pushdown.md).

## 5. What to build, in order

1. **`ComponentRole`-keyed `PredicateShape`s**, and two capabilities —
   `SDMX_REST_DATA_21` and `SDMX_REST_DATA_30`. *Shipped, and they differ by
   more than this predicted: 2.2.2 made the time dimension a key dimension, so
   3.0 also gains membership on time (#337).*
2. **A `DataRequest`** beside `Request`: path from `_data_url`'s two shapes,
   `{key}` from `_serialise_key`, plus period and `c[…]` parameters. Reuse
   rather than reimplement — `_serialise_key` already handles wildcards,
   sorting and the measure-dimension rule. *Shipped as
   `query/_rest.DataRequest`, which carries its own path shapes and builds the
   key with `_rest._dotted_key` over `DataStructure.key_slot_ids(api)` —
   not through `_serialise_key`, which went with `reg.data()` in #516.*
3. **`Pred[ObsRow]` → `DataRequest`**, the lowering: equality and `is_in` on a
   key dimension become key slots; a comparison on the time dimension becomes
   `startPeriod`/`endPeriod`; membership on any other component becomes `c[…]`
   on 3.0 and a residual on 2.1. *Shipped as `data_request_for`; `c[…]` takes
   attributes only, and a strict `<` / `>` on time stays residual because the
   period bounds are inclusive.*
4. **The refusal**, in its narrow form: a residual over a data scan that names
   nothing to bound it. No bare-scan bound, per §3. *Shipped in #515 — see
   the status note.*
5. **`obs()` on `RestRegistry`**, mirroring `query()`:
   `self._query_store().obs(binding)` with `prepare=self._fetch_data_for`,
   the binding synthesised by `DataflowBinding.from_dsd`. *Shipped as
   written (through `DuckDBDataStore.from_registry(self._query_store())`).*
6. **The streaming rule** of §4, with `explain()` saying which path a query
   took. *Not built: every REST data query materialises first (§4).
   `explain()` says whether a query fetches, and `Query.stream`'s docstring
   scopes its memory figures by configuration.*

## 6. What would falsify the unification

Stated up front so it is recognisable if it happens. The design is wrong if:

* **a predicate cannot be lowered without changing its meaning** — for example
  if a server's `c[…]` membership folds case where `is_in` does not, making a
  pushed query and a local query disagree. `PredicateShape` carries the fold
  precisely because a server evaluates by rules we do not control, so the
  answer would be to *not* push that shape rather than to accept the drift;
* **the key's positional order cannot be recovered** for a dataflow whose DSD
  is unavailable — the key is by position, so a wrong order silently queries
  the wrong slice rather than erroring;
* **`stream()` cannot be made bounded in the fully-pushed case**, which would
  mean the two worked examples in the [registry design](registry-and-catalog.md) (`StreamingResponse`, and
  a tool that works against a bundle *and* a provider) are asking for something
  the transport cannot give.

None of the three is known to be true. The first is the one to test earliest,
because it is a semantics question and the other two are engineering.

> **Outcome.** The first did not arise — every shape either lowered exactly or
> stayed residual, and the two under-declarations (`>` on time, any measure
> filter) were deliberate rather than forced. The **second did happen**: #337,
> where the key's positional order differs between REST generations and the
> library had it both ways at once, parsing keys it could not itself emit. It
> was version-dependence rather than impossibility, so the design held. The
> third was not tested, because the wire-level stream was not built: a REST
> query loads its whole slice into the query store, and `stream()` is bounded
> after that only with a file-backed `store=` (§4). The `StreamingResponse`
> facade in the registry design reads a bundle, where the stream is bounded.
