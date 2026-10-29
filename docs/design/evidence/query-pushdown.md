# Query pushdown: evidence

> Evidence record: the SDMX REST capability survey, the recorded ECB response
> sizes behind `references=` and `detail=`, and the materialise-versus-query-
> through measurements, from building the second backend for #306 under %17
> (#312, !308, #317, #318, #335), 14–18 August 2026, with in-place notes added
> 28 October 2026. Not maintained against the code; the design is
> [query-pushdown.md](../query-pushdown.md), and a bare § in this file is a
> section of that document.

Each section below was a numbered section of the design until October 2026.
The design keeps the headings (§10, §11, §12) and states the conclusion each
one still supports.

## Survey: the value of a REST backend (§10)

Before building an executor, a cheaper experiment was run: express the question shapes
#276 inventoried across its 76 hand-written sites, declare SDMX REST's
capability from the URL, and run the planner. No HTTP, no cassettes — the
declaration and the planner are the whole apparatus.

**The declaration is six lines.** `/structure/{resource}/{agency}/{id}/{version}`
gives equality on agency, id and version; the resource is the accessor's own
constraint rather than a predicate. `orders=False`, `slices=False`,
`bounding={agency}`.

*Note, October 2026: `SDMX_REST` in `query/_pushdown.py` now declares the same
three equalities at nine prefixes (`SDMX_REST_PREFIXES`), scans every
maintainable root and every derived root (`_maintainable_roots()` and
`_derived_roots()`), and bounds each of those roots by its own agency. The
survey's verdicts are unchanged.*

Twenty-three shapes, four outcomes:

| outcome | n | meaning |
| --- | --- | --- |
| `PUSHED` | 8 | one request, nothing left over |
| `BOUNDED-LOCAL` | 2 | one request plus a local filter over a bounded set |
| `REWRITE` | 1 | two requests via §9.1, no N+1 |
| `REFUSED` | 12 | no endpoint, or an unbounded scan; raises and names the fix |

**This table is the second reading; the first reported more shapes servable.**
The survey originally reported 6 / 7 / 2 / 5 — 75 % servable — measured
against a *prototype* planner written inside the test. When the rewrite search
moved into `_pushdown.py` the prototype's mistake showed: it checked whether
the **inner** scan of a rewrite was bounded and never the outer one. The real
planner checks every scan, per §5, and eleven of twenty-three survive.

The planner now applies the rule as written. What stopped being servable is
every query with no agency on one of its scans, and those cannot be answered
without fetching a registry. Some of the twelve refusals come from gaps in the
capability model rather than from the query; the subsections below cover
them.

### The corrected planner and the headline query

`dataflows whose DSD uses concept X` needs **two** agency filters, not one:

```python
reg.query(q.dataflow).filter(
    q.dataflow.agency == "ESTAT",       # bounds the dataflow scan
    q.dataflow.dsd.agency == "ESTAT",   # bounds the DSD scan
    q.dataflow.dsd.dimensions.any(q.component.concept.id == "REF_AREA"),
)
```

The earlier write-up said one, having checked only the inner scan. With one,
the rewrite lifts the only predicate into the sub-scan and leaves the outer
scan carrying *nothing* — `GET /dataflow` for every agency, then a local
membership. §5's per-scan bound catches this, though it was drafted against
an unbounded *inner* scan and here the outer one is unbounded. The check now fires on `residual or memberships` rather
than on `residual` alone, because lifting every residual leaves the list
empty and the old condition silently passed.

### Two capability-model gaps, closed

Seven of the twelve refusals are rooted at **item** relations — `q.code`,
`q.component`, `q.concept`. Two things were missing, both now added:

1. **Which roots are scannable** (`Capability.scans`). REST has no endpoint
   for "all codes": items arrive inside their parent. Without this the planner
   read `q.code` as a scan that merely happened to be unbounded and refused it
   with *"add an agency"* — a fix that could not have worked, since there is
   no code endpoint at any agency. A refusal naming an impossible fix sends the
   caller to a dead end.
2. **A per-root bound** (`bounding` keyed by root). One flat set could only
   ever mean "agency on the root", and the bound is not always a filter on the
   root: a codelist is bounded by its own agency, a *code* would be bounded by
   its codelist's.

`root_key` is what splits them, and the split is the resource list itself: a
maintainable's `__constraint__` carries its artefact type, which *is* the
`{resource}` in the URL. Item relations have no constraint and no resource.
The scannable set is derived from the accessors, so a new artefact type gains
an endpoint without anyone remembering to list it.

**This changed the refusal message and left the count at twelve; the
prediction that it would raise the count was wrong.** Serving an item-rooted query needs a third
thing neither field supplies: scan the owner and *explode* its items into
rows. That is a new step kind, not a declaration — and §11's prefetch is the
same machinery seen from the other side, since `GET /codelist/ESTAT/CL_FREQ`
already returns the codes.

What the corpus does show is that these questions have a **REST-native
spelling**, rooted at the owner:

| the question | rooted at the item | rooted at the owner |
| --- | --- | --- |
| the codes of `CL_FREQ` | `q.code` — refused | `q.codelist` filtered by agency + id — one request |
| the dimensions of a DSD | `q.component` — refused | `q.dsd` filtered by agency + id — one request |

Both spellings fetch the same bytes. Only one of them is expressible today,
and whether the other should be is the explode question above rather than a
missing declaration.

### The headline query: dataflows by concept

`dataflows whose DSD uses concept REF_AREA` is #276's headline — the single
most common question asked of an SDMX catalog, and the one with no expression
today. Evaluated naively it is **N+1 requests**: fetch the agency's dataflows,
then resolve each one's DSD. At 800 dataflows that is 801 round trips.

The rewrite makes it **two**, independent of N. No tuning of "fetch and filter
locally" reaches that.

### The headline query needs the DSD's agency

That query is `REFUSED` as written and `REWRITE` only when the DSD's agency is
stated:

```python
reg.query(q.dataflow).filter(
    q.dataflow.agency == "ESTAT",                     # bounds the dataflow scan
    q.dataflow.dsd.agency == "ESTAT",                 # <- required for the rewrite
    q.dataflow.dsd.dimensions.any(q.component.concept.id == "REF_AREA"),
)
```

(With the dataflow's agency alone the query is admitted too, but not
rewritten: it is one bounded scan, `/dataflow/ESTAT?references=descendants`,
with the DSD predicate evaluated locally over what came back — §13's closure.
With the DSD's agency alone it is refused, per the previous subsection.)

The planner cannot supply the DSD's agency itself. **A dataflow's agency does not imply its
DSD's agency** — cross-agency reuse of structures is normal SDMX — so
borrowing the outer agency to bound the inner scan would be a guess, and a
guess that silently returns too few rows when it is wrong. The planner refuses
and names the fix. That has an ergonomic cost: over REST the headline query
needs a filter DuckDB does not.

### What the survey found in the code

Writing the classifier surfaced a defect in `shapes_of` that the unit tests
could not see. It flattened a `Quantified`'s parts into the outer shape set,
mixing two frames of reference: the quantifier's path is relative to the scan
root, its parts' paths to the fan-out. So
`dataflow.dsd.dimensions.any(component.concept.id == "X")` presented
`("concept",)` as if it were a path from the dataflow, and any prefix analysis
over the set concluded the predicate spanned two unrelated relations. Fixed by
nesting the parts under `PredicateShape.inner`, which is also what the
docstring already claimed the design was. The survey found the defect before
the executor existed.

### Verdict

Build it. The two gaps above are closed; they corrected seven misleading
refusals and left the count of servable shapes unchanged.

The rewrite serves few shapes, and the headline query is one of them. The
refusal rule turns an invisible fetch-everything into a stated refusal. The
declaration is small, so the machinery's work is the transformation. The
corrected numbers change the ergonomics: a caller must state an agency on
**every scan a query touches**, which the SDMX REST URL does not make obvious.
`RestRegistry.query`'s docstring says so, and `PushdownError` names the column
to add.

Two things to settle first, answered in §11 and §12: whether `select()` /
`group_by()` are *unavailable* on a REST root rather than residual (§9.4 —
no, they are available), and whether `store` is simply the better answer for
any session issuing more than one query. The REST backend suits the first
query against a live registry, before the caller knows whether they want the
whole catalogue.

*Note, October 2026: a projection across a path (`select(d=q.dataflow.dsd.id)`)
is fetched with the `references=` depth its paths need, since GH-271; until
then it was fetched with `references=none` and answered null (§11).*

## REST response sizes (§11)

Measured against the recorded ECB responses.

### `references=<type>` is a server-side join

`references=` accepts a structure *type*, not only `none` / `descendants`. So
one request returns the dataflows **and** the structures they reference:

| request | bytes | artefacts |
| --- | --- | --- |
| `/dataflow/ECB?detail=full` | 38.6 KB | 102 dataflows |
| `/dataflow/ECB?detail=referencepartial&references=datastructure` | 2.15 MB | 102 dataflows + 81 DSDs |

That is the join, done by the server, in **one** request. §9.1 said REST
cannot join and built the two-scan rewrite around that. REST cannot *filter on
the joined side* — which is what the rewrite does — but it can bring
the joined side back.

### `references=all` on one artefact

The bound in §5 is about which rows come back. A prefetch is about how much
comes back *per row*, and it is unbounded in a way that a scan-level rule does
not see:

```
/codelist/ECB/CL_CURRENCY/latest?references=all   →  29.8 MB, 143,146 codes
```

That is **one artefact**, fully bounded by every rule in §5, returning 30 MB
because `all` followed every edge it could.

### What a stub omits (#318)

`codelist/ECB?detail=allstubs` returns 244
codelists; comparing field-by-field against a full response for the 109 present
in both, exactly two things differ: `codes` and `description`. `id`, `agency`,
`version` and `name` are all present. A third field, `maintainer`, also
differed and is *not* attributed to `detail` — the full response was
`references=all`, which pulled the AgencyScheme and resolved the agency's
name. Attributing it here would have made every query needing an agency name
ask for `full` on the strength of a confounded comparison.

The saving is worth having: 88 KB for the whole catalogue as stubs, against
69 KB for `CL_CURRENCY` alone at full detail.

## Materialise or query through, with numbers (§12)

This question has a measured answer. Measured against the recorded ECB responses on this machine.

**These numbers are the second set.** The first, taken before !307, showed
8.4 ms per artefact and a break-even of about three queries — and the profile
behind them turned out to be measuring a defect: a cascade of ~36 `DELETE`s
run per artefact whatever its type, plus DuckDB's per-parameter pandas probe
riding along on each one (#317). Fixing that moved the figure by 3× and moved
the conclusion with it. The stale version is not preserved here; what follows
measures the fixed writer.

### The cost model

| payload | MB | artefacts | items inside | parse | store | ms/artefact |
| --- | --- | --- | --- | --- | --- | --- |
| `/dataflow/ECB` + its 81 DSDs | 2.2 | 183 | 2,391 | 0.06 s | 0.47 s | 2.6 |
| one DSD, `references=all` | 0.5 | 22 | 2,525 | 0.01 s | 0.06 s | 2.9 |
| `CL_CURRENCY`, `references=all` | 29.8 | 142 | 144,564 | 0.74 s | 1.21 s | 8.5 |

Roughly **2.6 ms per artefact plus a few µs per contained item**. Parsing runs
at ~35 MB/s and never dominates.

The first measurement reported this as *flat per artefact and independent of
size*, which was true only because the per-artefact cost was then large enough
to hide everything else. With it 3× smaller the per-item term is visible:
`CL_CURRENCY` costs 8.5 ms per artefact because 142 artefacts carry 144,564
codes between them, not because those artefacts are individually expensive.
Both terms matter now; payload size in bytes does not drive the cost.

### A bounded query and a materialisation fetch the same bytes

§5 forces the
scan to be bounded by agency, and an agency's structures are exactly the
natural materialisation unit — `GET /dataflow/ECB?references=datastructure` is
both "the request my query needs" and "ECB's catalogue". So the extra cost of
materialising is only the store; the download is already paid.

So the comparison is:

- **query through** — pay one round trip *and a re-parse* on every query.
- **materialise** — pay the store once, then pay nothing again, including in
  later sessions if the DuckDB file persists.

ECB's whole dataflow catalogue now stores in **0.47 s** against a round trip
of ~0.5 s. The break-even is **about one query**.

### The rule of thumb

> **Materialise. Query through only for a single lookup you will not follow
> up.**

- One artefact by agency + id + version, wanted once: query through — 22
  artefacts, 0.06 s, nothing to reuse. Even here the margin is thin.
- Anything else, including the second question about the same agency:
  materialise, per agency. You have already downloaded it.
- A large codelist is not a reason to query through: 143,000 codes cost 1.2 s.
- The real ceiling is **many artefacts**: at 2.6 ms each, 5,000 is ~13 s.
  That argues for materialising per agency rather than a whole registry — not
  for querying through.

The first pass predicted that "at ~1 ms per artefact the rule would collapse
to *materialise, always*". At 2.6 ms the rule is already close to that.

### The two remaining costs

Two costs survived #317, and both have a known cause:

- ~~**Re-add still runs the full cascade**~~ — ~7 ms per artefact against 2.6
  for a fresh write. **Done (#144, #145):** `_delete_artefact_cascade` in
  `storage/writers.py` dispatches on the artefact type, and the per-kind table
  set is derived in a test rather than enumerated twice
  (`test_the_cascade_covers_every_table_each_writer_writes`,
  `tests/unit/test_cascade_dispatch.py`).
- **~3,400 pandas probes remained** (as measured then; not re-measured here) on a fresh write, from ordinary bound
  parameters — ~12 on the spine insert, 2 per extension row, at two probes
  each. Removing them means batching *across* artefacts, which is an `add_all`
  API change rather than a writer fix. Installing pandas also removes them
  and is worth ~17 %; not done, because pandas is a heavy dependency outside
  the baseline deps.
