# The Registry API

`Registry` and `Catalog` are one concept. This is the unified surface, the three
ideas that decide its shape, and what is still open.

How it was reached — the old split's real seam, what sdmx1 and pysdmx do, why
the spec's word is *registry*, and the options weighed and dropped — is in the
commits preceding this file. It is not repeated here.

## 1. Three governing ideas

### 1.1 Retrieval versus computation

The path language is **not** a SQL compiler with a REST mode bolted on. A path
is IM traversal and compiles two ways: `_closure.py` turns it into
`references=`, `_joins.py` turns the same path into a join tree.

What cannot compile to REST is a **predicate**, because there is nowhere on the
wire to put one. The entire structure-request surface is

```python
resource, agency, resource_id, version, references, detail
```

so the only expressible predicate is equality on identity (`_equalities`), and
`_execute.py` states the consequence flatly: *the backend computes nothing.*

The split is a property of the **plan**, not of the entry point, and the planner
already derives it:

| | names | REST | SQL | client-side evaluator? |
| --- | --- | --- | --- | --- |
| `get(urn)` | one identity | one GET, or an identity-map hit | point select by key | no |
| `query(path)`, unfiltered | a neighbourhood | `all/all/latest?detail=full` — the **widest** request the surface can make | SELECT + JOIN | no |
| `query(path).filter(agency==…)` | the same, narrowed by identity | `ECB/all/latest` — the equality is lifted into the **path** | SELECT + JOIN | no |
| `query(path).filter(<anything else>)` | narrowed by a predicate | the same GET as the row above | SELECT + JOIN + WHERE | **yes** |

Measured:

```
unfiltered                        [('all', 'all', 'full', 'none')]
filter(q.dataflow.agency=='ECB')  [('ECB', 'all', 'full', 'none')]
```

So the split is **not** filtered-versus-unfiltered. Identity equalities push
into the path (`_equalities`); everything else runs after the fetch. An
unfiltered query is the *most* expensive call, not the cheapest.

**And it is allowed, deliberately.** `is_bounded` is consulted only when a push
carries a residual or a membership, which reads like an oversight and is not —
[`query-pushdown.md`](query-pushdown.md) §5 argues it under the heading *"The
implemented rule is narrower than the table"*. The rule is about the **visibility** of cost, not its
magnitude: `query(q.codelist)` is a caller asking for every codelist in as many
words, and the cost is legible in the query they wrote, while
`query(q.codelist).filter(name.contains("price"))` is a caller asking for three
and the fetch-everything is invisible in the text. A refusal catching the first
would refuse a query nobody is confused about.

The remaining asymmetry is narrow: **the SQL is identical in both backends.**
Locally the rows are already in DuckDB and the `WHERE` goes into the statement;
remotely the same `WHERE` needs rows in front of it, so they are materialised
first (`_query_store()` — `store=`, else an owned `:memory:`). Same compiler,
same statement, different work to get rows where the statement can see them.

**A REST query answers only from rows its own fetch wrote.** The store may
hold artefacts landed by an earlier `get`, `query` or `add`, and with a
persistent `store=` it usually does. `RestRegistry` records each URN a fetch
writes in `sdmx.fetch_scope` under the plan's `canonical_key`, replacing what
that key held before, and the compiler adds `urn IN (SELECT urn FROM
fetch_scope WHERE fetch_key = ?)` to the root (`_scope_clause` in
`query/_compile.py`). A local query has no fetch and no scope clause. The
decision is §5.13 in
[evidence/registry-landing.md](evidence/registry-landing.md).

**One evaluator, by choice.** A Python walker over the fetched graph is
buildable — `tests/support/oracle.py` is one — but it covers code and hierarchy
predicates and not joins, EXISTS, windows or aggregates, so promoting it means
writing the compiler twice. Two evaluators mean two sets of semantics for case
folding, nulls, LIKE metacharacters and set membership to keep in agreement.
Revisit only if the DuckDB round-trip shows
up in a profile.

### 1.2 What splits, and what does not

SDMX REST splits `/structure`, `/data`, `/metadata`. `query()` covers the first
and third, `obs()` the second. That split follows the IM more closely than
SDMX REST's does.

**Structure and reference metadata cannot be separated, because paths cross
between them.** The edge graph wires metadata relations onto the same `artefact`
spine:

```python
Edge("spine", "metadata_set", "urn", "artefact", "urn")
Edge("msd", "metadataflow", "msd_urn", "artefact", "urn")
```

A path may start at `q.metadata_set` and end at a codelist. Two entry points
split by plane would be unenforceable (which owns a crossing path?),
behaviourally identical (same compiler, same schema, same joins), and would have
to reject legitimate traversals to stay honest. The three-way split is a
**transport** fact and belongs in `_rest.py`.

**Data stays separate because a DSD fixes its schema.** Every
`sl.q.X` relation has a shape the *IM* fixes — `q.code` is always id, name,
parent_id. An observation relation's shape is fixed by a **DSD**: CPI's
observations carry FREQ/REF_AREA/TIME_PERIOD, EXR's carry CURRENCY. Different
arity, different names, one per dataflow. There is no singleton
`sl.q.observation` because there is no single observation relation.

That is the IM's own distinction: a DataStructure *defines the shape of a
dataset*, while a Codelist needs no such definition.

| | schema fixed by | nameable as a singleton | entry |
| --- | --- | --- | --- |
| structural + reference-metadata relations | the IM | yes | `query(root)` |
| observations | a DSD | no — constructed per binding | `obs(flow)` |

Naming the surface after `/structure` and `/metadata` would import a transport
boundary into the model layer, which is the move this document removes from
`RestRegistry` and `FederatedCatalog`.

**Metadata on data does not cross the line.** Three mechanisms attach
description, and only one reaches a row:

* **DSD attributes with an attachment level** (DATASET / SERIES / OBSERVATION)
  are *data columns* — `OBS_STATUS`, a series-level `GEO_NAME` — declared by the
  DSD, stored in the observation table or its sidecar (#332), reached as
  `obs.col(…)`. Already on the DSD-schema side.
* **Reference metadata** cannot reach a row: a `MetadataSet`'s targets are
  artefacts by URN, and SDMX 3.0 collapsed v2.1's `MetadataReport` layer with
  its key-descriptor targets.
* **Annotations** likewise, on artefacts and items.

**The two vocabularies never mix in one path, and compose freely in one query.**
A typed path (fixed at import) and a runtime column (fixed by a binding) meet
through `InRelation` — `column IN (<subquery>)` — not through a shared
traversal:

```python
obs.filter(obs.col("REF_AREA").is_in(
    reg.query(sl.q.code)
       .filter(sl.q.code.codelist.id == "CL_GEO",
               sl.q.code.name["en"].str.contains("Ontario"))
       .select(sl.q.code.id)
))
```

### 1.3 Provenance is a source chain

An ordered list of sources, each serving some of `{artefact, rows, bytes}`;
first hit wins; caches earlier in the chain fill on the way back.

```
LocalRegistry("metadata.duckdb")          [Session, DuckDB]
LocalRegistry.edition("/srv/…")        [Session, DuckDB + lake]
RestRegistry(FMR)                     [Session, Rest]
RestRegistry(FMR, store=b)   [Session, Bundle(b), Rest]
FederatedRegistry(routing={...})      [Session, Routed({ESTAT: …, ECB: …})]
```

So `store` is a *position* in the chain rather than a parameter, and
`FederatedRegistry` is *a source that dispatches by agency* rather than a
parallel mechanism.

Three rules keep it predictable.

1. **The order is explicit and never inferred.**
2. **Every result records which source answered, and when.**
   `source_registry` was threaded everywhere; `fetched_at`, declared beside it
   in the DDL, was written by nothing — always NULL in every path, a schema
   promising provenance it did not keep, and unnoticed because nothing read it.
   The two now travel together: an artefact from a source records both, one
   added locally records neither, since a timestamp without a source implies a
   fetch that never happened. (`structural_version` was named here as also
   inconsistent. It is not — it is a required field on `DataflowBinding`,
   written and read on every path. The claim was stale.)
3. **A routing miss is an error naming what was tried, not `None`.** Said
   without the qualifier this contradicts §5.8
   ([evidence/registry-landing.md](evidence/registry-landing.md)), which
   reserves `None` for "every source answered *not here*" — and §5.8 is the
   later, settled rule.
   Both hold once the two kinds of miss are separated: **no source is routed**
   for the URN's agency is a `KeyError`, because the caller asked a question
   the chain cannot even attempt; **routed and absent** is `None`; **routed and
   unreachable** is `RegistryUnavailable`.

**First-hit-wins is qualified by version, because a `latest` URN is not an
identity.** This paragraph had no version qualifier, which is what §5.16 / #338
([evidence/registry-landing.md](evidence/registry-landing.md)) reported: two layers of the chain held opposite `latest` policies, and the
first one consulted always won. The rule, now stated:

* **A pinned URN names an immutable artefact.** `CL_X(1.0)` cannot come to mean
  different content — that is what versioning is for — so it caches at every
  position in the chain, indefinitely. **HTTP cache headers get no say here.**
  RFC 9111 constrains caches interposed between a client and an origin; an
  application holding an artefact under an identity the standard forbids
  reusing is not one. Measured, this matters: ECB and IMF send
  `no-cache, no-store, max-age=0, must-revalidate` on *codelists* — 94 of the
  173 structure responses in our cassettes — with no ETag and no
  `Last-Modified`, so obedience would mean re-fetching immutable artefacts
  forever with not even a cheap revalidation. Their `isFinal="false"` on all
  2420 artefacts recorded suggests both are appliance defaults rather than
  statements about volatility.
* **`latest` names a mutable pointer and is never a cache key.** It is asked of
  the authority; what comes back is interned under the *pinned* URN it resolved
  to, which costs no extra round-trip because the payload carries its own
  version. So `get(CL_X(latest))` re-asks, and every subsequent
  `get(CL_X(1.0))` is free.
* **How long the pointer may go un-re-asked is the provider's call, with a
  caller override.** This is where `Cache-Control` legitimately applies, and
  where providers actually differ meaningfully — OECD publishes
  `public, max-age=86400` on structures, the SDMX Global Registry sends ETags
  and no max-age. The override exists because ECB's `no-store` is boilerplate
  and obeying it literally optimises for a statement nobody meant to make.
  **Built** — #339, as `RestRegistry(…, latest_ttl=seconds)`: a *floor* over
  what the provider's `Cache-Control` allows, never a ceiling. The default, 0,
  obeys the provider exactly; wanting *less* than the provider allows is spelled
  with a pinned URN.
* **A provider directive does not veto an explicit `store`.** The
  `Session` is an implicit cache the user never asked for, and is exactly what
  those directives are written for; `store` is the user taking a copy
  by name. A `no-store` veto would stop an ETL pipeline from keeping anything it
fetched.

Note the policies were **inverted relative to consent** before this: the cache
the user never asked for served `latest` forever, and the one they asked for by
name refused `latest` entirely.

The chain is the mechanism **inside** a registry, not a user-facing type. §2's
static absences depend on the type, and a user-assembled chain would forfeit
them.

## 2. Decisions

**One family, three types.** `RestRegistry`, `LocalRegistry`,
`FederatedRegistry`. `FederatedCatalog` goes; its three constructor arguments
were a structural reader, artefacts the registry already holds, and a store the
registry already owns.

**Types, not a factory.** A new type when a method appears or disappears; a
constructor when only arguments differ. So `FmrRegistry` stays a subclass of
`RestRegistry`. The reason is that `sql()`, `add()` and `download()` must be
*absent*, not raising, and only a type can do that. The same rule made
`EditionRegistry` a type (GH-122): a published bundle opens read-only, so its
`add()` and `sql()` disappear, and `LocalRegistry.edition(path)` returns a
`EditionRegistry`, which `LocalRegistry` extends with the writes. Until then a
bundle differed from a store only in wiring, and `bundle(path)` returned a
`LocalRegistry`. (`write_observations()` was on this list. It is a
`LocalRegistry` method, the way a builder writes a publication's rows, and
refuses on a registry with no lake: GH-198, §5.17 in
[evidence/registry-landing.md](evidence/registry-landing.md).)

**`RestRegistry` contains a `LocalRegistry`, and does not inherit one.** It
already does — `_query_store()` — and inheriting would drag in `add()` and
`sql()`, undoing those absences.

**One compiler.** Compilation does not vary by backend; *admission* does.
`Capability` / `Program` / `Push` decide how much of a plan a backend absorbs
and `_rest.request_for` renders the rest as a fetch. REST is a fetch, not a
dialect — a second SQL engine would be a dialect, and that is what `Capability`
anticipates.

**`get` is the chain's read, not a query entry.** The query language runs over
one materialised store; `get` walks sources that may be a dict, an HTTP service
or an identity map, none of which can evaluate a predicate. So
`reg.query(urn).collect()` in its place is feasible and wrong: `get[T](SdmxUrn[T])`
recovers `T` while `collect()` returns `list[object]` (checked); a lazy-query
seam would require every source to carry a compiler; `get` can answer from the
identity map with no backend touch; and it states one-or-none where a list must
be unwrapped. Gremlin unifies (`g.V(id)` is a constrained start step) and can,
having no identity map to shortcut; SQLAlchemy has one and keeps `Session.get`
separate for the same reason we do. Point lookup *does* fall out of the
traversal one level down, where it belongs: `Query.get(item_id)`, `q["FREQ"]`.

**Identity in, this registry's structure out.** `obs()` takes a
`Dataflow`, a `Ref[Dataflow]` or an `SdmxUrn[Dataflow]`, uses it *only for
identity*, and resolves the DSD from this registry — which removes the
cross-registry hazard rather than legislating against it, and leaves one
semantics rather than two. A `dsd=` override for structure this registry has
not got was proposed here and **not built**: `obs(flow)` takes the
flow and nothing else. A `RestRegistry` fetches the DSD from its own provider;
a `LocalRegistry` needs it `add`ed first.

**`observations`, not `data`.** Both data-plane methods live under
`/data/dataflow/…` — one serves it as rows, one as bytes — so `data` names the
resource, not either method. `observations` names the *relation*, which is the
query language's convention everywhere else, and is the more precise spec term.
*(Since shortened to `obs`, the spec term's transparent abbreviation — as
`hcode` is `HierarchicalCode`'s — matching `ObsQuery` / `ObsRow` and the `obs`
every example already binds. The argument against `data` is unchanged.)*

**`download` is `RestRegistry`-only.** A remote download is byte-faithful
passthrough of what the provider sent. The local equivalent is serialisation
through our own writer — a different operation, and already composition:
`write_sdmx_csv(obs.stream(), dsd, …)`.

**No eager query.** *(**Done**, #516, further than planned.)* `DataQuery.fetch()`
went, and so did `Dataset`: it was to survive as the format layer's I/O
payload, but once `ObsQuery` gained `structure`, typed sinks, `validate()` and
`pivot(on=)`, the readers had a better answer — a file read through a registry
is an `ObsQuery` over a view (`reg.scan_csv` / `reg.scan_json`), and the
writers take `obs.stream()`'s batches.

**`.urn` becomes a property; the builder is renamed.** *(**Done.**)* `flow.urn`
was a bound method, and `ref.py`'s own docstring offered
`Ref.unresolved(cl_freq.urn)`, which raised. `Dataflow.urn_for(agency=…, id=…)`
builds one; `flow.urn` is a property that reads one and returns
`SdmxUrn[Self]`, better typed than `artefact_urn`'s `SdmxUrn[object]`
(`SdmxUrn[Any]` until #399).

Counted rather than estimated: **435 call sites, of which 14 are in `src/`** —
390 in tests, 31 in docs. The mechanical bulk is the suite, not the library.

**It does not generalise below maintainables, and the docstring must say so.**
`Concept.urn` takes `scheme=` as well as `agency`/`id`, and a `Code` instance
cannot derive its own URN at all (`hasattr(Code("A"), "urn")` is `False`) —
items are identified within their scheme, not independently. So `.urn` is a
property on `MaintainableArtefact` and the item builders keep their classmethod
shape.

**`download` takes a format.** *(**Done** — `download(flow, fmt=…)`, §6 step
8 in [evidence/registry-landing.md](evidence/registry-landing.md).)* `Accept` was hardcoded to `text/csv`, so a download's path extension was
decorative and SDMX-ML data could not be asked for at all. Format is explicit, never inferred from the extension — though a
contradiction between them should be refused — and the response's
`Content-Type` is checked against what was asked.

**Corrected while building it: an enum, where the design had a
`(media type, version)` pair.**
The pair does not survive the vocabulary. SDMX-ML 2.1 carries data under
**four** media types — generic and structure-specific, each plain or
time-series — so `("xml", "2.1")` names no single format; and
`application/vnd.sdmx.data+xml` did not exist before 3.0, so the pair cannot be
normalised into one either. `("csv", "3.0")` names nothing at all. A `DataFormat`
enum makes exactly the eleven real formats expressible and nothing else, which
is what pysdmx concluded too — our member list is compared against theirs in
`tests/unit/test_data_format.py` rather than against itself.

## 3. The surface, audited

The test for arbitrariness: can every entry's presence **and absence** be
assigned by a stated rule? The core entries can (§3.1). A tail cannot, and is
listed separately rather than smoothed into the first table (§3.2).

The tables below list every public method and property of the four types
in sdmxlib 0.77.0. The `DataQuery` methods an earlier audit could not place
went with `DataQuery` in #516: a slice and a period are `obs.filter(...)`
predicates pushed into the key, `fetch_to()` is a `store=` plus
`obs.sink_parquet`, and `measure_dim=` became `obs.pivot(on=)`.

### 3.1 The core

As built — checked with `hasattr` against `EditionRegistry` (B), `LocalRegistry`
(L), `RestRegistry` (R) and `FederatedRegistry` (F). `LocalRegistry` extends
`EditionRegistry`, so every B row is also an L row. Where the build departed
from the design, the rule column says so.

| entry | takes | returns | B | L | R | F | rule |
| --- | --- | --- | :-: | :-: | :-: | :-: | --- |
| `get(urn)` | identity | `T \| None` | Y | Y | Y | Y | chain read — identity to value |
| `download(flow, fmt=…, to=…)` | identity | bytes \| `Path` | | | Y | | chain read — identity to value |
| `query(root)` | a relation | `Query[R]` | Y | Y | Y | | query entry — names a fixed-schema relation |
| `obs(flow)` | identity | `ObsQuery` | Y | Y | Y | | query entry — constructs a DSD-schema relation |
| `scan_csv(source)` / `scan_json(source)` | a file | `ObsQuery` | | Y | Y | | query entry — a file's observations, read with the structure this registry holds or fetches |
| `dsd(flow)` | identity | `DataStructure \| None` | Y | Y | | | derived structural fact — needs a store; on R the DSD is a `get` |
| `binding_for(flow)` | identity | `DataflowBinding \| None` | Y | Y | | | derived structural fact; R synthesises its binding inside `obs()` |
| `locate(binding)` | a binding | `DataflowBinding` | Y | Y | | | derived — the binding with its `database_role` naming the attached catalog |
| `availability(flow)` | identity | the stored availability | Y | Y | | | derived — what data the flow holds, stored when the bundle was published |
| `check_binding_drift` / `validate_binding` | a binding | issues | Y | Y | Y | Y | derived structural fact — reads artefacts only |
| `refused` | — | refusals | Y | Y | Y | | what a read refused to build, with why (#492) |
| `browse()` | — | entries | Y | Y | | | enumerate bindings — "which flows have rows" |
| `add` / `add_all` / `drop` / `replace` / `ingest` | artefacts | — | | Y | | | write — needs a database of record |
| `write_observations` | rows | — | | Y | | | write — into this registry's lake; refuses without one (GH-198) |
| `sql(text, **params)` | SQL | `RawSql` | | Y | | | escape hatch — needs a database of record |
| `execute(sql, params)` | one statement | rows | Y | Y | | | a statement on this thread's handle |
| `bundle(path)` | a directory | `EditionRegistry` | Y | Y | | | construction — opens a published bundle, read-only (GH-122) |
| `build(path)` | a directory | `LocalRegistry` | | Y | | | construction — starts a publication (GH-123) |
| `from_connection(conn)` | a DuckDB connection | the registry | Y | Y | | | construction |
| `manifest` / `data_path` / `lake_alias` | — | bundle facts | Y | Y | | | the manifest `bundle()` read, where the lake reads its files, the lake's catalog name |
| `publication` | — | `Path` | | Y | | | the publication directory `build` created |
| `open` / `closed` / `connection` / `uri` | — | — | Y | Y | | | lifecycle of a database of record |
| `close` | — | — | Y | Y | Y | | lifecycle |
| `transaction` | — | — | | Y | | | lifecycle — a write transaction |
| `session` / `open_sessions` / `on_connect` | — | — | Y | Y | | | per-thread DuckDB sessions (#300) |
| `routing` / `default` / `source_for(urn)` | — | sources | | | | Y | the agency routing a federation dispatches by |
| `__contains__` | identity | `bool` | Y | Y | Y | Y | chain read |
| `__len__` | — | `int` | Y | Y | | | count of stored artefacts |

### 3.2 The tail, and its disposition

| entry | on | what it really is | disposition |
| --- | --- | --- | --- |
| `fetch(urn)` | R | `get` at another resolution depth | **folded** into `get(urn, resolve=…)` |
| `resolve(target)` | R | `get` at another resolution depth | **folded**, same |
| `reported_attributes()` | B L | `query(q.reported_attribute)` | **keep** — see §3.2a |
| `artefacts(type)` | B L | Python type → discriminator, with validation | **keep** — see §3.2a |
| `metadata_for_target(urn)` | B L | `query(q.metadata_set)` + a filter | **keep** — see §3.2a |
| `metadata_report(set_urn)` | B L | MSD-typed projection into `MetadataSection` | keep |
| `applicable_hierarchies(…)` | B L | a precedence rule over two partitions | keep |
| `unique_hierarchy(…)` | B L | a cardinality assertion with a domain error | keep |
| `codelist(urn)` | B L | returns `LazyCodelist` — a proxy | keep — a proxy may take the type's name |
| `code_map` / `dsd_code_map` | B L | mirrors of `dsd.code_map()` | keep — lazy/eager parity |
| `get_all(type)` | B L | enumerate by type | keep — `SupportsGetAll` is Protocol |
| `ref(urn)` | B L | a `Ref` bound to the registry (#492) | keep — binding, not retrieval |
| `scan_csv(source)` | L R | a file's observations, with the structure resolved from the file's own `STRUCTURE` / `DATAFLOW` column | **promoted** to the core (§3.1), beside `scan_json` (#516) |
| `load(source)` | R | parse a message into the registry | keep — the rename to an ingest collided with `ingest`, and was left open |
| `registry` / `to_registry()` | R / B L | hands out an `InMemoryRegistry` | keep — §5.10 ([evidence/registry-landing.md](evidence/registry-landing.md)) |
| `clear_resolve_cache` / `resolve_cache_info` | B L | cache introspection | keep — ops surface |

### 3.2a The three "delete" rows, overturned in implementation

The audit above marked three L-methods **delete** on the rule "row selection
wearing a name." Building step 5 of the landing plan (§6,
[evidence/registry-landing.md](evidence/registry-landing.md)) turned up evidence
that overturned all three, and the correction is recorded here rather than hidden in a revert:

* **`artefacts(type)` keeps** because it is *not* row selection. It maps a
  Python class to the wire discriminator (`sl.Dataflow` → `"Dataflow"`) and
  raises a naming `TypeError` for an unknown class. `query(q.artefact).filter(
  q.artefact.type == "Dataflow")` forces the caller to hardcode the wire
  string; the method is the thing the query language *cannot* do ergonomically.
  A test — `test_reg_artefacts_still_narrows_by_a_runtime_type` — already
  pinned this, from a prior milestone that reached the same conclusion.

* **`reported_attributes()` keeps** because the evidence is overwhelmingly that
  it is a *documented, curated convenience*, not cruft: ~40 call sites, a whole
  `reference_metadata.md` cookbook section ("every QUALITY statement anywhere —
  use `reg.reported_attributes()`"), a surface listing in `query-language.md`,
  and an explicit conceptual pairing with `metadata_report` (cross-catalogue
  vs per-set). That is the same category as `codelist()`, `code_map()` and
  `get_all()` — all kept. The rule cannot tell a curated convenience from a
  duplicate; usage can.

* **`metadata_for_target()` keeps** by consistency with its sibling above, and
  because it is the SDMX by-target primitive returning a `list[MetadataSet]`
  with a `structure_urn` filter. The query replacement *was* verified
  equivalent (target lookup, structure filter, `order_by(urn)` all map) — so
  the deletion is *possible*, but "possible to inline" is not the bar the other
  kept conveniences were held to.

The general lesson, folded back into the rule: **a named convenience over a
common query earns its name when it is documented and used, exactly as a proxy
like `codelist()` does.** "Row selection wearing a name" catches an *undocumented*
duplicate, not a curated entry point.

### 3.3 The rule the contested four settle

> **A method stays if it encodes a rule the IM states and the query language
> cannot. It goes if it is row selection wearing a name.**

* **`metadata_for_target` stays** — §3.2a reverses the earlier "goes." The
  replacement below is real and verified equivalent, so a caller who *wants*
  the query form has it; the method is kept as the by-target convenience beside
  its `reported_attributes` sibling rather than deleted for being inlinable:

  ```python
  reg.query(sl.q.metadata_set).filter(
      sl.q.metadata_set.targets.artefact.urn == str(target_urn),
      sl.q.metadata_set.structure.urn == str(structure_urn),   # when given
  ).order_by(sl.q.metadata_set.urn).collect()
  ```

* **`metadata_report` stays.** It joins the **MSD** to give every value its
  declared `text_type` and `is_presentational`, and projects into
  `MetadataSection` ordered by `(path, sibling_index)`.
* **`applicable_hierarchies` stays.** Scoped HierarchyAssociations before
  unscoped is an SDMX precedence rule; the language orders by columns, not
  policy.
* **`unique_hierarchy` stays.** It raises `AmbiguousHierarchyError` naming the
  dimension, the dataflow and the competing associations. `one()` would say
  "expected at most one row", which is true and names none of them.

### 3.4 Construction

```python
sl.LocalRegistry("metadata.duckdb")
sl.LocalRegistry.edition("/srv/census-2021")
sl.RestRegistry(sl.Provider.ECB)
sl.RestRegistry(FMR, store=bundle)
sl.FederatedRegistry(routing={"ESTAT": a, "ECB": b}, default=c)
```

## 4. Worked examples

From the kinds of consumer that exist: a FastAPI SDMX REST facade, an LLM
agent built on sdmxlib, a bundle builder (the write side), and analyst
notebook use.

### 4.1 REST facade — `/data/{flow}/{key}` as streamed SDMX-CSV

```python
reg = sl.LocalRegistry.edition(settings.bundle_dir)     # startup, once

@app.get("/data/dataflow/{agency}/{flow_id}/{version}/{key}")
def data(agency: str, flow_id: str, version: str, key: str, request: Request):
    flow = sl.Dataflow.urn_for(agency=agency, id=flow_id, version=version)
    dsd = reg.dsd(flow)
    cf = sl.parse_components(key, parse_c_params(request.query_params), dsd.key_slot_ids("3.0"))

    return StreamingResponse(
        write_sdmx_csv(
            reg.obs(flow).filter_components(cf).stream(),
            dsd,
            structure_id=f"{agency}:{flow_id}({version})",
        ),
        media_type="application/vnd.sdmx.data+csv;version=2.0.0",
    )
```

One object for structure and data. The writer takes the DSD, which fixes the
column order, and reads each column by component id, the name an observation
result carries (§5.7, [evidence/registry-landing.md](evidence/registry-landing.md)).

### 4.2 Agent tool — filtered observations with a per-group top-N

```python
def query_observations(characteristic_ids, ref_areas=None, partition_by=None, partition_limit=None):
    obs = reg.obs(CENSUS_FLOW)
    q = obs.filter(obs.col("CHARACTERISTIC").is_in(characteristic_ids))
    if ref_areas:
        q = q.filter(obs.col("REF_AREA").is_in(ref_areas))
    if partition_by:
        q = q.qualify(
            obs.col("OBS_VALUE").rank().over(obs.col(partition_by)) <= partition_limit
        )
    return q.frame(labels="en")
```

Conditional refinement from whatever arguments arrived, nothing executing until
`frame()`. This function should work against a bundle *and* a remote provider
with only the construction differing — which is why `obs()` must not be
transport-shaped.

### 4.3 Analyst — explore a public registry, then pull data

```python
with sl.RestRegistry(sl.Provider.ECB) as reg:
    flows = (reg.query(sl.q.dataflow)
                .filter(sl.q.dataflow.agency == "ECB")
                .filter(sl.q.dataflow.name["en"].str.contains("exchange"))
                .collect())

    obs = reg.obs(flows[0])
    df = (obs.filter(obs.col("CURRENCY").is_in(["USD", "GBP"]))
             .filter(obs.col("FREQ") == "M")
             .frame(labels="en"))
```

No catalog, no binding in sight — the binding is synthesised from the DSD and
the fetch planned from the dimension predicates. A filter the data key cannot
carry, with nothing else narrowing the fetch, is refused rather than fetching
the whole flow ([`data-plane-pushdown.md`](data-plane-pushdown.md)).

### 4.4 Builder — fetch structures remotely, write rows locally

```python
bundle = sl.LocalRegistry.build(out_dir)  # a publication: a store and a lake
with sl.RestRegistry(FMR, store=bundle) as fmr:
    dsd = fmr.get(
        sl.DataStructure.urn_for(agency="STATCAN", id="CENSUS_DSD", version="2021"),
        resolve=True,
    )
    # resolved into `bundle` on the way past — the chain filled its cache

bundle.write_observations(schema="data", table="obs", source=frame, sorted_by=("GEO", "TIME_PERIOD"))
bundle.add(sl.DataflowBinding.from_dsd(dsd, dataflow=flow_ref, schema="data", table="obs"))
```

"Fetch remotely, land locally" is a chain whose cache is the artefact being
built. Confirms the type split too: `add` is absent on
`RestRegistry`, and `write_observations` is on `LocalRegistry`, whose
publication has a lake. `LocalRegistry.edition(path)` *opens* a published bundle, read-only;
its builder writes it through `LocalRegistry.build(path)`.

### 4.5 Browse, which stays separate

```python
for entry in reg.browse():
    print(entry.dataflow_urn, entry.name, entry.binding.materialized_at)
```

`browse()` enumerates bindings — "which dataflows have rows" — and a REST
provider has no cheap way to answer that. So it is a `LocalRegistry` method, and
the remote analogue (`query(sl.q.dataflow)`) answers a different question.

## 5. Decisions after review

The decisions below hold in the code. Each was argued, and several were
corrected by measurement, in §5.1–§5.17 of
[evidence/registry-landing.md](evidence/registry-landing.md), which keeps the
numbering this document cites.

* **No bare-scan bound, on either plane** (§5.2, §5.12). Nothing is refused for
  being wide. A query is refused for a residual over a scan that pushes
  nothing to bound it.
* **Names** (§5.3, §5.4): `store=`, `LocalRegistry`.
* **`get`** (§5.5): a local `get` always resolves and takes `eager=`; a remote
  or federated `get` takes `resolve=` and `partial=`. There is no dataflow
  handle (§5.6).
* **Writers take a DSD** (§5.7), because an observation result is named by
  component id.
* **Misses** (§5.8): an unreachable source raises `RegistryUnavailable`; an
  absent artefact, including a 404, is `None`.
* **A relation-valued filter across two sources is refused**, naming both
  (§5.9, `sdmxlib.sql.HasSource`).
* **A REST query's scope is its own fetch** (§5.13, §1.1); a plain-column
  projection fetches `detail=allstubs` (§5.14).
* **`RestRegistry` is thread-safe** (§5.15, [`concurrency.md`](concurrency.md)),
  and **`latest` is never a cache key** (§5.16, §1.3).
* **A lake is a binding's need** (§5.17): `obs()` and `browse()` answer on any
  `LocalRegistry`, and a binding naming an unattached catalog is refused with
  the fix.

## 6. Landing it

The unified surface landed between August and October 2026, in the order the
plan set: data-plane pushdown and `obs()` on `RestRegistry` first, then
`.urn` as a property, component-id projection, one `get`, the deletion of
`FederatedCatalog`, the writers, `download(fmt=…)` and the chain. Every step
is done or recorded as reversed; the plan and what each step turned up are in
[evidence/registry-landing.md](evidence/registry-landing.md) §6.
