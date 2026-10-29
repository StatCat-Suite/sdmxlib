# The Registry API: decisions and landing plan

> Records the decisions behind the unified Registry API and the plan that
> landed it, moved here from [`registry-and-catalog.md`](../registry-and-catalog.md)
> on 2026-10-28 (GH-267). §5.1–§5.11 were decided with the design, merged as
> !353 on 2026-08-18; a clean-room review then reopened §5.5 and §5.7 and added
> §5.12–§5.17, all decided between 2026-08-18 and 2026-08-19 (#336, #338, #339,
> #340). §6 is the landing plan, carried out from August 2026 (!353, !363,
> !362) to October 2026 (#515, #516); every step is done or recorded as
> reversed. Notes in italics were added as steps landed. Section numbers are the
> design document's: §1–§4 there, §5–§6 here. Code references are to the tree
> as each note was written.

## 5. Settled, and what a review reopened

§5.1–5.11 were decided, with the reasoning recorded so a later reader can tell a
decision from an accident. A clean-room review then found that two of them
rested on false readings of the code (5.5, 5.7 — both rewritten below) and that
questions had never been asked at all (5.12–5.17). Those were open when found;
all six are now decided, and each records its resolution in place.

**5.1 `measures()` is a filter, and gets a convenience.** *(Decided; the
`ObsQuery` convenience was **not built**, and `DataQuery.measures()` went with
it in #516. On `ObsQuery` the same thing is a filter on the measure
dimension's column.)* The question was
whether `pivot()`/`measures()` could only be expressed with `Dataset` as the
return type — which would have meant the two data APIs were two for a real
reason. They cannot: `pivot()` is already `Dataset.pivot()` and `DataQuery.pivot()`
is fetch-then-pivot sugar, while `measures(codes)` filters the measure
dimension's key segment (and becomes `measures=` on the wire in 3.0). So
`ObsQuery.measures([...])` would look up the measure dimension from the DSD and
filters it. **This retired the largest risk to the unification.**

**5.2 A remote data fetch is bounded structurally, with an explicit opt-out.**
*Superseded twice — read §5.12 first, then this note. The rule that shipped is
narrower again: SDMX-REST defines 413 and 414, so request size is the server's
call, and the only client-side refusal is for a plan that pushes **nothing**.
There is no `allow_unbounded=`; `Capability.bounding` gained a third state
(declared-and-empty) to say "any pushed constraint bounds this".*
Refuse unless at least one dimension is constrained; `allow_unbounded=True`
overrides. Checkable offline, mirrors `PushdownError` on the structural side,
and makes the expensive thing deliberate. `/availability` was considered and
rejected as the mechanism: `build_component_availability` returns *which values
exist*, not a row count, so sizing needs further calls and provider support
varies. The rule is REST-only — locally a full scan is a table scan — so it is
a `Capability` difference.

Acknowledged crudeness: one constrained dimension on a twelve-dimension flow may
still be enormous. The bar is a floor against *accidents*, not a guarantee.

**5.3 `materialise_to=` becomes `store=`.** It holds bindings, a lake and fetched
artefacts, and in §4.4 it is the product rather than a cache. `store=` is
neutral about direction and says position-in-chain.

**5.4 `sl.Registry` becomes `sl.LocalRegistry`.** Symmetric names make the
absences legible; `sl.Registry` beside `sl.RestRegistry` implied the first was
the general case, which is the confusion removed from `Catalog`.

**5.5 One `get(urn, resolve=…, eager=…)`, and the local default does not
move.** The first draft of this decision made `resolve=False` the default and
would have broken the pattern on CLAUDE.md's front page:
`LocalRegistry.get` today resolves **every** `Ref` before returning, precisely
so `dsd.dimensions["FREQ"].codelist` works, and its docstring says so. (That
spelling lost its parentheses when `codelist` became a property — it is the
resolved codelist, not a `Ref` to unwrap. Three pages kept calling it until
the doc-block runner ran them.) Two axes
were also conflated and are now kept apart:

* **`resolve=`** — reference *depth*. `True` (all — the local default),
  `False` (skeleton, what `RestRegistry.fetch` meant),
  `[sl.Codelist]` (those types only, what `GetQuery.resolve(sl.Codelist)`
  means), with `partial=` alongside for constraint trimming.
* **`eager=`** — whether a codelist comes back as a `LazyCodelist` or a
  materialised `Codelist`. Nothing to do with references; it is about how much
  of *one* artefact is loaded, and it stays.

Defaults differ by backend because the cost does: local
`resolve=True` because resolution is a join away, remote `resolve=False`
because it is another round trip. Both stated on the method rather than
implied.

**As built, the two axes landed on different backends.** Each `get` carries
only the axis it has a choice about:

```python
LocalRegistry.get(target, /, *, expect=None, eager=False)
RestRegistry.get(target, /, *, expect=None, resolve=False, partial=True)
FederatedRegistry.get(target, /, *, expect=None, resolve=False, partial=True)
```

`target` is an `SdmxUrn` or a `Ref`; `expect=` narrows the `object` a parsed
URN answers to a type (#406). A local `get` always resolves — there is no
`resolve=` to turn it off — and only it has a `LazyCodelist` to be eager about.

`GetQuery`, `fetch()` and `resolve()` collapse into this — *(**done**)* — and
`get(type, agency=…, id=…) -> GetQuery` is *also* deleted, though it was the
spelling in every docstring example in `api/registry.py` and in CLAUDE.md.
Those examples moved to `get(Dataflow.urn_for(agency=…, id=…))`.

**5.6 No dataflow resource handle.** Closed rather than deferred. All four
operations are already rule-assigned as flat methods, and a handle would add a
type whose name lies: a dataflow handle is not a `Dataflow`, whereas
`LazyCodelist` genuinely *is* a codelist.

**5.7 The format writers take a DSD *and* a dataflow reference, once the query
projects by component id — which is a prerequisite, not a detail.** The first
draft said the binding was used "only to recover a column order the DSD already
knows". That is false on the code, and the correction matters:

* `write_sdmx_csv` uses the binding's **physical** names to `select()` columns
  out of the Arrow batch, not merely to order them
  (`formats/sdmx_csv/writer.py:93-101`). Drop the binding and the writer cannot
  find its columns.
* The JSON writers need more than a DSD: `dataflow_link(binding)` emits
  `binding.dataflow.urn` into `dataSets[].links`, and `display_names` prefers
  the **dataflow's** name over the DSD's
  (`formats/sdmx_json/_data_common.py:564-571`).

*(Done in §6 steps 3 and 7: an observation result is named by component id,
`write_sdmx_csv(batches, dsd, …)` takes a DSD, and `dataflow_link` takes a
`Ref[Dataflow]`. The file:line references above are to the code of August
2026 and now point elsewhere.)*

So the seam is: **`ObsQuery` aliases every physical column to its component id**,
and then a writer needs only `(dsd, dataflow_ref)` — both of which
`reg.dsd(flow)` and the flow identity already supply. Without the aliasing step
the binding cannot leave the format layer, and that step belongs in §6 before
the writer change.

**5.8 Unreachable raises; absent returns `None`.** *(**Done.**)* A source that
errors raises, naming the source and what was tried; `None` means every source
answered "not here".

**`FailurePolicy` was deleted, not re-scoped.** This section said it *"keeps
its real job — whether a federation continues past a dead source"*, and
building it showed there is no such job: `FederatedRegistry` routes each URN to
exactly one source by agency, so there is no next source to continue to.
`RETURN_NONE`'s only effect was converting an error into a silence — the
cookbook documented it as *"source down or artefact missing — same outcome to
the caller"*, which is the conflation this section forbids, sold as a feature.
A caller who wants to continue past a dead source now writes the `except` that
says so, and it cannot mislabel anything.

Three things the implementation turned up:

* **A bare `RestRegistry` named neither.** A dead provider surfaced as
  `ConnectError: connection refused` — no source, no URL. `RegistryUnavailable`
  now carries `source_id`, `tried` and `cause`, and the client wraps at the
  transport boundary.
* **`TransportError`, not `HTTPError`.** The latter includes
  `HTTPStatusError`, so catching it would relabel a 404 as an unreachable
  source — the same conflation, one layer down.
* **A 404 was an exception, so `None` was nearly unreachable.** SDMX-REST
  assigns 404 the meaning *"no results found"*, making it the commonest way a
  provider says "not here"; it raised `HTTPStatusError`, which left `None`
  reachable only when a provider answered 200 with an empty message. Now a 404
  is `None` and a 500 still raises, because a server that answers at all was
  reached and its error is its own.

**5.9 A cross-source relation filter is refused, naming both sources.**
*(**Done.**)* The error says which two could not see each other and that giving
the registry a `store=` fixes it. Splicing N binds instead would reintroduce the
parameter explosion `InRelation` exists to avoid, and doing it invisibly is what
§1.3's third rule forbids. Auto-`ATTACH` was rejected: the chain must not open
connections the caller did not ask for.

**Measured, it returned a wrong answer without an error.** Two registries whose
codelists disagreed about which codes are named "Ontario": `b` alone answered
`['ON', 'QC']`, and `a.obs(flow).filter(col.is_in(b.query(…)))`
returned `['ON']` — `a`'s answer to `b`'s question, silently, and entirely
plausible-looking. The other registry's SQL *text* was spliced into this
connection, where the same table names resolve to this database's rows.

`sdmxlib.sql.HasSource` is the mechanism: an **optional** companion to
`SqlRelation`, whose docstring already required the subquery's tables be
visible on the consumer's connection and had nothing checking it. Optional
deliberately — making it required would break every existing producer, and a
foreign relation that cannot answer is not necessarily wrong, since Topology B
(the layout named in [#372](https://github.com/StatCat-Suite/sdmxlib/blob/main/history/gitlab/issues/372.md))
puts `sdmx.*` and `statcat.*` on one connection precisely so cross-library
composition is legitimate. So a producer that *can* identify its source is
checked; one that cannot is trusted, which is the state before this existed.

Three things the implementation had to get right, each found by a test rather
than by reading:

* **The token is the base connection, not the query's.** `ObsQuery` runs on a
  per-thread cursor while a structural `Query` holds the base connection, so
  comparing what each query executes on refuses the *same* registry —
  `test_a_relation_valued_filter_crosses_the_planes` failed immediately, which
  is what it is for.
* **The walk covers boolean structure.** A cross-source relation sits under an
  `&` as easily as at the top, and that is precisely the shape a caller reaches
  for when combining registries.
* **`intersect` / `difference` splice the same way** and needed the same
  refusal; there the check is cheaper, because the argument is a `Query` and
  cannot be a producer that legitimately declines to answer.

A label is carried alongside the token because a `DuckDBPyConnection` has no
useful `repr`: without one the message names *"a different database
(DuckDBPyConnection)"* twice, which names two sources without identifying
either.

**5.10 / 5.11 filed as issues, and both were narrower than filed.**

**5.10** proposed `InMemoryRegistry` drop its `Registry` conformance claim
(#333). **Closed as invalid**: the claim is exercised and load-bearing.
`FederatedRegistry` declares `routing: dict[str, RegistryReader]` and is routed
to an `InMemoryRegistry` throughout its tests, and the local-files cookbook
loads into one and calls `get(urn)`. The *concern* — that the read seam reads
as a catch-all — stands, but this member is a registry all the same.

**5.11** said the REST fetch serves only the structure plane (#334). One of the
seven relations it named, not all seven. `msd`, `metadataflow` and
`metadata_provision_agreement` are *definitions*, which SDMX-REST serves from
`/structure` — checked against pysdmx's `StructureType` — so they were already
routed correctly; "metadata" in a resource name says nothing about its plane.
`metadata_attribute`, `reported_attribute` and `metadata_target` are *items*,
refused as roots exactly as `q.code` is, which is behaviour rather than a gap.
That leaves `metadataset`, and it was worse than "no request to issue": it
issued a **wrong** one, and since §5.8 a 404 comes back as "not here", so the
query returned empty and said nothing. `Request.path` now derives the plane
from the resource.

### Decided after review

**5.12 One rule across both planes: visibility, not magnitude.** *(This began
as "an unfiltered structural scan is not refused", filed as a defect. It is not
one — see §1.1 — and correcting it exposed a real question, now decided.)*

The structural plane deliberately **allows** a bare unbounded scan: a caller
who wrote `query(q.codelist)` with no filter asked for every codelist in as
many words. §5.2 had decided the data plane **refuses** a bare
`obs(flow)` unless a dimension is constrained — which contradicts the
precedent, because `obs(flow).frame()` also says what it asks for.

**Resolved toward the precedent, on a real workload.** The magnitude argument —
"every codelist" is thousands of artefacts while "every observation" is tens of
millions of rows — does not survive contact with the workload this library is
built for: 78k-member codelists and billion-row dataflows. Three things follow:

* **The bound never applies to that workload anyway.** `DUCKDB` declares
  `total=True` and `is_bounded` returns immediately, so *every* local query is
  admitted regardless of size. Large data is local data; the refusal machinery
  is entirely about what is a reasonable request against someone else's server.
* **The person with a billion rows is the person who legitimately asks for
  large results.** A rule justified by "that number is too big" would be
  calibrated against the users least likely to need it.
* **On the one path where it would fire — pulling a whole flow over HTTP — the
  caller is already doing something deliberate.** That is a download, not an
  accident, and §2 gives it a method (`download()`).

So: **nothing is refused for being wide. A query is refused only for being
*misleadingly narrow*** — a predicate the backend cannot evaluate, over a scan
that names nothing to bound it. Both planes state that rule, and §5.2's
`allow_unbounded=` has no job and goes.

The terminal a caller chooses protects them at scale, and that belongs in the
docs rather than in a planner: `collect()` resolves
every match into an artefact graph (331x against one statement) where `ids()`,
`urns()` and `frame()` read columns; `frame()` materialises where `stream()`
holds 5 MB flat at any `chunk_rows`; and **an ordered stream is not bounded at
all**, because a sort materialises the whole result before the first batch can
leave — measured at 210 MB on 4M rows and 368 MB on 8M, identical at
`chunk_rows` of 1,000 and 50,000.

**5.13 What is a query's scope — its own fetch, or the store?** *(**Done**;
#340.)* The compiled SQL scanned the whole materialisation store, so an earlier
`get`, `query` or `add` contributed rows. **Decided and built: a query answers
only from rows its own fetch wrote**, and version behaviour follows — a request
that asked `latest` reports what `latest` returned.

Measured before: a provider serving only `CL_LIVE` answered
`['CL_LIVE', 'CL_RETIRED']`, and a store holding two versions answered with
both. What was *not* broken, and what narrowed the fix: a **filtered** query
was already correct, because the predicate re-applies locally over the
materialised rows.

**A membership table, not a watermark.** The obvious
`WHERE fetched_at >= <the fetch's time>` needs a value that does not exist when
the SQL is built — terminals compile first and fetch second, and `to_sql()` is
contractually non-executing with output documented as handable to another
library. `canonical_key(plan)` *is* known at compile time, so
`sdmx.fetch_scope(fetch_key, urn)` needs no reordering and no public signature
change: `Query._fetch_scope` derives the key from the plan, and `fetch_into`
records what it wrote under it.

Three things the build settled that the section had not:

* **Only maintainable roots need it.** `Capability.scans` is
  `_maintainable_roots()`, so every REST-backed query is rooted on
  `sdmx.artefact` keyed by `urn` — the scope is always a set of URNs. The
  guard on table and key is for `ObsQuery`, which carries a `prepare` hook but
  has no urn to scope by.
* **Membership must outlive its fetch.** A second terminal over one query
  skips the fetch, so a scope meaning "rows written just now" would return
  empty. Keyed by the plan, the rows persist for as long as the cache entry.
* **It is replaced, not appended.** A re-fetch under the same key is a fresh
  answer to the same question; keeping the previous one would let a withdrawn
  artefact keep answering, which is the defect being removed.

**5.14 `detail=allstubs` is unreachable.** *(**Wired.**)* `request_for`
computed it only when a projection was supplied, and the only production caller
(`_execute.fetch_into`) supplied none — so #318's derivation existed and was
tested but never ran.

**Only a projection may narrow, and only a trivial one.** The rule was already
written in `request_for`'s comment: `None` means the caller wants *artefacts*,
and a stub has no items. So `Select` — which *is* the projected query — supplies
its columns and every other terminal supplies `None`. `ids()` could in
principle narrow and deliberately does not: it is not a projection, the saving
is small, and a mistake would fail silently.

Two things the wiring needed that the section did not mention, both found by a
test rather than by reading:

* **A projection's *paths* are not in the plan.** `closure(plan)` is derived
  from predicates and order, so a projection crossing to items —
  `select(codes=q.codelist.codes.id.list())` — reached `detail_for` with an
  empty path set and came out `allstubs`. It would have returned empty lists,
  silently: exactly the failure `detail_for` documents, by the one route
  nothing was checking. `_projection_columns` now refuses anything that is not
  a plain column on the root, keeping the saving where it is obviously sound
  and giving up rather than guessing elsewhere.
* **The detail belongs in the fetch key.** The key is `canonical_key(plan)` and
  the projection is not part of the plan, so a `select(id)` fetch marked the
  question done and a later `collect()` skipped its own fetch — resolving
  artefacts whose items never arrived. Keyed on the *derived* detail, so two
  projections that both reduce to `allstubs` still share one fetch, with
  `full` subsuming `allstubs` because everything a stub carries is in a full
  artefact.

**The trade, stated:** a caller mixing a projection with an aggregate over one
plan now pays a round trip that asking `full` throughout would not. Taken
because the payload difference runs the other way and is much larger — a full
scan of a registry's codelists carries every code of every codelist, where the
stub listing carries none. Measured against the recorded cassettes, one *full*
codelist is 75,848 bytes while an `allstubs` listing of every codelist is
96,609.

**5.15 The chain has no concurrency story, and the caching contract forbids
that.** *(Filed as #336; **resolved**.)* `_query_store_owned` and `_fetched`
were mutated without a lock: two threads raced to build two `:memory:` stores,
and two identical queries fetched twice. `_fetched` was an unbounded set, never
invalidated, keyed by `canonical_key`. CLAUDE.md requires a runtime cache to be
single-flight, bounded, and tied to a lifecycle — this was none of the three,
and §4.1 is a FastAPI server.

**The first thing to settle was whether `RestRegistry` is meant to be
thread-safe at all**, because the defect was an *unstated* contract rather than
a violated one: `LocalRegistry` documents its and meets it, this one said
nothing. Settled yes, on §4.1 — a facade is the shape this API is for, and
"nothing" is not an answer a server can build on. The commitment now sits
beside the local one in [`concurrency.md`](../concurrency.md).

Four fixes: a double-checked lock on the store (the loser's store was leaked
*still open*, so a query could read an empty one and correctly report nothing);
`SingleFlight` for the fetch memo, bounded and single-flight; the data-plane
lock widened to span the ingest rather than only the check.

The fourth was **not in the issue**. `_current_catalog` ran
`SELECT current_database()` on the store's base connection — which #300 had
already ruled out — so two threads overwrote each other's row buffer and a
branch commented *"DuckDB always answers this"* raised. The concurrency tests
found it; it had been reachable all along. That is the argument for writing the
tests before calling the fix done: three of the four fixes came from reading
the issue, and the one that produced a user-visible `RuntimeError` did not.

**5.16 `latest` breaks first-hit-wins.** *(Filed as #338; **resolved**. The
freshness window is #339, also built — `latest_ttl=`, §1.3.)* `_local_cache_lookup` already refused to serve a
`latest` URN from cache, because a cached `latest` goes stale. §1.3 stated the
chain rule with no version qualifier and so reintroduced exactly that bug.

**Sharper than filed, once measured:** the inconsistency was not latent in the
design, it was *live in one function*. `_fetch_one` consulted the in-process
`Session` and then `_local_cache_lookup`, and the two had opposite `latest`
policies — the `Session` maintained a whole `_latest` index to serve it, the
local store explicitly refused to. The `Session` is consulted first, so it
always won. A probe where the provider published 2.0 between two calls: one
registry asking `latest` twice made **one** request and got the superseded
artefact.

**The resolution splits a question that had been one.** Retention of an
artefact in hand is governed by SDMX version semantics; freshness of the
`latest` *pointer* is governed by the provider, with an override. Written out
in §1.3. Two of the four options originally on the table — "honour HTTP
headers" and "resolve `latest` to pinned" — turned out to compose rather than
compete, which is why the first framing of the decision was wrong.

**The fix was small.** The resolve-to-pinned half already worked: a `latest`
fetch interns under the pinned URN the payload declares, so the fix was to stop
*reading* the `latest` indices, not to build anything. They then had no readers
and were deleted, along with `_version_key` — a second, cruder version parser
that existed only to pick their winner, and whose two Hypothesis properties now
run against `model/version.py`'s `sort_key`, the one the storage layer bakes.

**Nothing in the suite covered the removed behaviour.** Deleting the `latest`
read path broke no existing test. The guard is `tests/unit/test_rest_latest.py`,
where every test publishes a new version *between two calls* — the only
arrangement that distinguishes a fresh answer from a lucky one.

**5.17 The absence rule has a hole below `bundle()`.** *(**Resolved: there is
no hole.** The premise was wrong on all three counts.)* This read
`obs()`, `write_observations()` and `browse()` as methods needing a
lake, so that on `LocalRegistry("meta.duckdb")` they must *raise* — which §2
says only a type can prevent — and concluded that `BundleRegistry` might have
to be a type after all.

Measured, none of it holds:

* **`write_observations` needs a lake, and says so.** It is on
  `LocalRegistry` (GH-198); on a registry without a lake it refuses with a
  `KeyError` naming `LocalRegistry.build`, rather than DuckDB's catalog error.
* **`browse()` answers on a plain registry**, with an empty list.
* **`obs()` answers on a plain registry** whose binding points at a
  local table — which `test_registry_data_plane.py` has relied on since §6
  step 6a.

What needs the lake is a **binding**, when its `database_role` names one. That
is *data*, and data cannot be checked by a type, so §2's rule was never in
tension: a method that answers for one binding and refuses for another is not
a method that should be absent. `BundleRegistry` does not need to exist for
this reason.

What *was* wrong is the refusal. A bundle's binding read through a plain
registry reached DuckDB as ``Binder Error: Catalog "lake" does not exist!`` —
true, and gives no fix to a caller who does not know that `lake` is what
`bundle()` attaches. It now names the dataflow, the table it expected, the catalogs that
*are* attached, and the constructor. Same shape as §5.8 and §5.9: the defect in
each was a refusal that did not say what to do.

## 6. Landing it

**One merge request.** The surface is never half-migrated, which matches the
no-backcompat rule and avoids a window where two spellings both work.

**Order the work *inside* it so the risky step is first.** Adding
`obs()` to `RestRegistry` is the step that could still invalidate the
design: if a remote fetch cannot be expressed as a query without lying about
what is pushed down, the unification is wrong. Prove it before renaming
anything, so a failure costs one commit rather than the change.

**Step 1 was the least specified thing here, and needed designing before
coding.** *(Designed in [`data-plane-pushdown.md`](../data-plane-pushdown.md) and built: `obs()` on
`RestRegistry` exists.)* When this was written nothing of the data-plane
pushdown existed: `Request.path` was hardcoded `f"structure/{…}"`, `Capability`
/ `Push` / the closure were all structural, and nothing mapped a `Pred[ObsRow]`
onto an SDMX key path or onto `startPeriod` / `endPeriod`. Two implementers
would have diverged immediately — one lowering predicates into a `_KeySpec` and
reusing `_serialise_key`, the other fetching CSV and letting DuckDB filter — and
the two differ in **whether `.stream()` streams at all**, which §4.1's
`StreamingResponse` and §4.2's "works against a bundle *and* a remote provider"
both assume.

Then, in dependency order:

1. **Data-plane pushdown + `obs()` on `RestRegistry`**, planned against
   a cassette: `Pred[ObsRow]` → key path and period parameters, a `/data`
   request shape, the §5.2 bound and its refusal, and a decision on streaming.
2. **`.urn` as a property, `urn_for()` as the builder** — 14 sites in `src/`,
   390 in tests, 31 in docs. `MaintainableArtefact` only; items keep their
   classmethod shape (§5.5's note).
3. **`ObsQuery` projects by component id.** Prerequisite for step 7, and it is
   what lets a physical column name stop escaping into callers.
4. **`get(urn, resolve=…, eager=…)`**; delete `GetQuery`, `fetch`, `resolve`, and
   the `get(type, agency=…, id=…)` overload. Update every docstring example that
   uses it, and CLAUDE.md.
5. **~~Delete the aliases~~ — reversed on evidence (§3.2a).**
   `reported_attributes`, `artefacts` and `metadata_for_target` all earn their
   names — documented, used, and (for `artefacts`) doing what the query
   language cannot. What remains of this step is two naming/placement
   questions that are *not* deletions and want a decision, not a rewrite:
   * **`scan_csv` on `RestRegistry`** — the doc said "move to `sdmxlib.polars`,"
     but the registry variant *auto-resolves the DSD from the DATAFLOW column*,
     which `slpl.scan_csv(source, structure=…)` cannot. So it is not a move; it
     is a convenience that needs a registry. *(Resolved by #516: promoted —
     `scan_csv` and `scan_json` are on `LocalRegistry` and `RestRegistry`, and
     `slpl.scan_csv` is gone.)*
   * **`load(source)`** — parses a local SDMX file into the registry. "Rename as
     an ingest" collides: `Registry.ingest` already exists and takes an
     `InMemoryRegistry`, not a file. Open: `load_file`, `add_file`, or leave.
6. **`FederatedCatalog` deleted.** Split in two, so the deletion happened
   against a registry that already worked:
   * **6a** — `Registry` gains `obs` / `dsd` / `binding_for` /
     `browse` and a `bundle()` constructor. Additive.
   * **6b** — `catalog.py` deleted; `builders/` repointed at a registry;
     `DataflowEntry` moved to `model/binding.py`; tests and cookbooks
     rewritten.

   The clearest evidence the composition had nothing to compose: **every**
   `from_parts(structural=X, bindings=X, data=…)` in the suite passed the same
   registry for all three roles, so the migration was mechanical — drop the
   wrapper, keep `X`. The shared test factory became `return local`.

   One test died rather than migrating: `browse()` refusing a bindings
   registry without `get_all`. That failure mode only existed because the
   three roles *could* be different objects; it is unreachable now, and
   restoring it would mean restoring the composition.

   **The `LocalRegistry` / `store=` renames are deliberately not here.** They
   are pure renames over a surface this step is already reshaping, and folding
   them in would make one diff where a reviewer cannot tell a rename from a
   behaviour change. They want their own step — **6c**, now done: `sl.Registry`
   → `sl.LocalRegistry` (§5.4) and `materialise_to=` → `store=` (§5.3), across
   119 files with no behaviour change and no compatibility alias.

   One thing the rename surfaced: `LocalRegistry` had a private `_store()`
   returning its `DuckDBDataStore`, and `RestRegistry` now has a `_store`
   holding the chain position. Two sibling classes with a `_store` meaning
   different things is precisely the confusion `store=` was chosen to remove,
   so the first became `_data_plane()`.
7. **The writers take `(dsd, dataflow_ref)`**, now that step 3 has aliased the
   columns. Filed as a tidy-up; it is a **regression fix**. Step 3 made an
   observation query project by component id while the writers still read
   their column order off the binding's *physical* names, so any renamed
   binding raised `KeyError: ... missing column(s) ['geo', 'v']` — naming
   columns the batch was correct not to have. No test failed, because every
   writer fixture in the suite used an identity binding. The structure is now
   the single source of both sides, so they cannot disagree; a renamed-binding
   test in `test_registry_data_plane.py` covers the join. `polars.sink_json`
   (now `ObsQuery.sink_json`) stopped synthesising an identity binding it only ever built to hand over a
   column map.
8. **`download()`** with a format argument, `RestRegistry`-only. Landed as
   `sl.DataFormat` (a `StrEnum` of the eleven data media types) plus
   `reg.download(flow, fmt=…, to=…)`. Three properties fell out of building
   it that the sketch had not stated: a download makes **exactly one request**
   (no DSD is needed — there is no key to widen and no columns to interpret,
   which is how it differs most from `obs`);
   the `Content-Type` check has to live here precisely *because* a download
   has no downstream to notice a wrong format later; and the extension check
   deliberately ignores extensions it does not recognise, since refusing
   `.dat` or `.gz` would be guessing at the format.
9. **The chain**: `store=` as a position, unreachable-raises (§5.8), the
   cross-source refusal (§5.9), and the answers to §5.15 and §5.16 — which are
   *prerequisites* for calling the chain a mechanism rather than a description.
   **Done.** `store=` as a position turned out to be largely already true —
   measured: a new registry over an existing store answers from
   it with the remote dead, in one request, which is what "position in the
   chain" means. What was missing was the *provenance* half of §1.3's second
   rule (`fetched_at` written by nothing) and the contradiction between §1.3's
   third rule and §5.8, both settled above.
   Both prerequisites are **done**: §5.15 (#336) gave `RestRegistry` a stated
   thread-safety contract it meets, and §5.16 (#338) gave §1.3 the version
   qualifier it lacked, so the chain now has one `latest` rule instead of two
   contradictory ones. The freshness window for the pointer (#339) was an
   optimisation, not a blocker, and has since landed as `latest_ttl=`.

**§5.12–5.17 were not scheduled here** when this plan was written: four were
questions the design opened, and two (5.12, 5.15) pre-existing defects it made
more visible. All six have since been decided — §5.13 (#340), §5.15 (#336) and
§5.16 (#338) built, §5.14 wired, §5.12 and §5.17 settled — and §5 records each.
