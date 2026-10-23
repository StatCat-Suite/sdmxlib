# Pushdown: where a query runs

%15 built one query language and one backend. #306's central claim —
**where a query runs is a property of the backend rather than of the
language** — was therefore asserted rather than demonstrated, and it is the
claim that overturned `query-composition.md`'s no-IR decision. This document
designed the second backend and the machinery that decides what it runs.

> **Status: built.** All four steps of §7 have landed, and §13's executor
> with them. Where each piece lives:
>
> | section | what | where |
> | --- | --- | --- |
> | §3 | `Plan`, split from `Query` | `src/sdmxlib/query/_plan.py` |
> | §4 | `Capability`, `PredicateShape`, `DUCKDB`, `SDMX_REST` | `src/sdmxlib/query/_pushdown.py` |
> | §5, §9.2 | `plan_program` → `Program` of `Push` / `Residual`; `is_bounded`; `PushdownError` | `src/sdmxlib/query/_pushdown.py` |
> | §9.1 | the semi-join rewrite (`Membership`), and the predicate rebase it needs | `_pushdown.py`, `_rebase.py` |
> | §11, §13 | the closure — `references=` and `detail=` per scan | `src/sdmxlib/query/_closure.py` |
> | §13 | `Push` → an SDMX REST `Request` | `src/sdmxlib/query/_rest.py` |
> | §13 | fetch, materialise, run the original query | `src/sdmxlib/query/_execute.py`, `RestRegistry.query` |
> | §10 | the survey, pinned per shape | `tests/unit/test_query_rest_survey.py` |
>
> One part is not built: §6's program printout. `Query.explain()` prints the
> SQL and binds, plus one line saying rows are fetched first when the query
> runs over REST; it does not yet list the requests. The same machinery also
> serves observations — `ObsQuery` over REST, with a data capability keyed by
> component role (`data-plane-pushdown.md`).
>
> The text below keeps the design argument as it was made, with notes where
> building it changed the answer.

It differs from the plan-node proposal in `query-language.md` (*Pushdown*)
in one place, argued in §2: **there is no IR to build.** `Query[R]` already
was one, and `Plan` is what extracting it produced.

## 1. The shape, unchanged

**plan → per-backend capability declaration → a program of pushed calls with
local steps between.** Calcite's adapter model, Trino's `applyFilter` /
`applyLimit`, Spark's `SupportsPushDownFilters`, ibis's backend compilers.

```
plan:      Scan(dataflow) · Filter(agency == "ESTAT") · Filter(EXISTS concept) · limit 10

DuckDB     1 GET  all of it, one statement

FMR REST   1 GET  /structure/datastructure/ESTAT?references=children
           2 LOCAL  urns ← rows where concept.id == "REF_AREA"
           3 GET  /structure/dataflow/ESTAT
           4 LOCAL  filter structure_urn ∈ urns; then limit 10
```

The REST program does more than push the agency and do the rest locally. Step 2 turns a path predicate into a set, which step 4 applies as a
membership — the rewrite argued in §9.1. Reading the program top to bottom is
also the argument for why the output is a sequence rather than a
`(pushed, residual)` pair: for DuckDB it always has length one, so the pair
looks sufficient until the REST rewrite needs two calls.

## 2. `Query` is already the IR

`query-language.md` (*Pushdown*) proposed a node set — `Scan`, `Filter`,
`Project`, `Join(edge)`, `Nest`/`Explode`, `Aggregate`, `Sort`, `Limit`.
Every one of those already existed as a field or a declared edge:

| proposed node | where it lives (now on `Plan`) |
| --- | --- |
| `Scan` | `Plan.root` — the accessor, carrying `__table__` and `__constraint__` |
| `Filter` | `Plan.predicates`, a typed tree of `Pred[R]` |
| `Join(edge)` | `query/_edges.py` — declared, with cardinality derived |
| `Sort` | `Plan.order_keys` / `default_order` / `unordered` |
| `Limit` | `Plan.limit_n` / `offset_n` / `cursor` |
| `Project`, `Aggregate`, `Nest` | `Select.items`, `Select.group_keys` |

Measured on the class rather than recalled, when this was written — 8 of
`Query`'s 10 attrs fields were the plan, and the two that were not were
`_conn` and `_resolve`:

```
plan    root, predicates, qualifiers, order_keys,
        default_order, cursor, limit_n, offset_n
WIRING  _conn, _resolve
```

After the extraction `Query` is `plan` plus its wiring — `_conn`,
`_resolve`, `_prepare` (the hook a REST registry uses to fetch before a
terminal runs), `_source_label` and `_source` — and `Plan` has nine fields,
`unordered` (#389, `order_by(None)`) having arrived since.

`_compile.py` was already *the DuckDB backend*: a function from that plan to a
statement. So the architecture the proposal described was two-thirds built, and building a
second structure to sit beside `Query` would mean maintaining two descriptions
of one thing and a translation between them — the duplication this milestone
has spent its length removing (three facet column groups, four DSLs, one
`_hops_available`).

**Three smaller pieces were missing:**

1. `Query` mixes the plan with DuckDB wiring. `_conn` and `_resolve` are how it
   runs, not what it says.
2. There is no way for a backend to declare what it can push.
3. There is no planner to split a plan against a declaration.

### The case for an IR

An IR earns its keep when there are transformations to apply (predicate
pushdown through joins, join reordering) or several frontends to normalise.
There is one frontend, and the classical transformations are the backend's own
business — DuckDB's planner reorders joins, and does it better. So the IR would
be a near-isomorphic copy whose only job is to be a different type.

**One transformation is ours, and it still fits `Plan`.** §9.1's
semi-join rewrite — evaluate a path prefix separately, apply it as a set
membership — is a rewrite we must perform, because no REST server will do it
for us, so it could look like a reason for an IR.

The rewrite consumes a `Plan` and produces *`Plan`s* — a scan of `dsd` filtered by
`components.concept.id`, and a scan of `dataflow` filtered by membership. Both
are expressible in the fields §3 already lists, the second because
`Session.value_set` makes a set an ordinary relation. A transformation that is
closed over the existing type is evidence *for* that type.

The test to re-apply if this changes: a transformation whose output cannot be
written as a `Plan` is the one that would justify a node set. None is known.

If a second frontend ever appears — a parsed query string, say — this decision
should be revisited. Nothing here forecloses it: extracting `Plan` from
`Query` (§3) is the same first step either way.

## 3. Separating the plan from the binding

Built — step 1 of §7, `src/sdmxlib/query/_plan.py`:

```python
@attrs.frozen
class Plan[R: RowKind]:
    root: Cols[R]
    predicates: tuple[Pred[R], ...] = ()
    qualifiers: tuple[WinPred[R], ...] = ()
    order_keys: tuple[OrderKey, ...] = ()
    default_order: tuple[OrderKey, ...] = ()
    unordered: bool = False
    cursor: Cursor[R] | None = None
    limit_n: int | None = None
    offset_n: int = 0
```

`Query` keeps its surface and holds a `Plan` plus its binding. `Select` and
`Grouped` carry the projection half. No caller sees this; it is the seam a
backend needs and the reason `canonical_key` already works on the plan fields
and not on `_conn`.

## 4. What a backend declares

A capability declaration answers one question per plan element: *can you run
this yourself?* It is data, not code, so it can be printed by `explain()` and
asserted in a test.

As built (`_pushdown.Capability`), with the fields §10 and §11 added beside
the original three:

```python
@attrs.frozen
class Capability:
    name: str
    #: True for a backend that evaluates everything (DuckDB). Distinct from
    #: an empty `pushes`, which means the opposite.
    total: bool = False
    #: Predicates this backend evaluates, keyed by the path prefix they are
    #: asked about: `()` is a predicate on the root, `("dsd",)` a predicate
    #: on a scan of `dsd` in its own right. Keying on the prefix rather than
    #: on the whole predicate is what lets a planner discover the §9.1
    #: rewrite — asking "can you scan `dsd` filtered by this?" is a
    #: different question from "can you scan `dataflow` filtered by this?".
    #:
    #: Shapes are structural *and carry their semantics*: a `Match` on
    #: `name` under `unaccented` is a different shape from one under
    #: `casefold`, because a backend that accepts the predicate but folds
    #: differently is wrong rather than slow (§9.3).
    pushes: Mapping[tuple[str, ...], frozenset[PredicateShape]]
    #: Whether ORDER BY / LIMIT / OFFSET survive a push.
    orders: bool = True
    slices: bool = True
    #: Which roots have an endpoint at all (§10).
    scans: frozenset[str] = frozenset()
    #: Per root, the constraints that make a scan *bounded* — see §5, §10.
    bounding: Mapping[str, frozenset[PredicateShape]] = {}
    #: Prefixes the backend can bring back with a row (`references=`, §11).
    prefetch: frozenset[tuple[str, ...]] = frozenset()
```

A prefix is the declared edge *route*, not the attribute spelling:
`q.dataflow.dsd` is `("dataflow", "dsd")`, because `dsd` crosses the dataflow
extension table and back out to the spine. A declaration keyed on `("dsd",)`
would match nothing, silently.

The prefix keying costs nothing for DuckDB, whose declaration is "everything
at every prefix", and it is the difference between the REST backend serving
agency/id/version lookups and serving real path queries. It is also the one
part of this design that is expensive to add later, because it changes what
the planner *iterates over*.

**DuckDB declares everything.** Its planner path is then a no-op, which is what
makes this refactor safe: the emitted SQL must not change, and
`tests/support/sql_corpus.py` is the byte-for-byte evidence. That corpus was
rebuilt for this (!304).

**SDMX REST declares little**, because its URL shape allows little. The
structure endpoint is

```
/structure/{resource}/{agency}/{id}/{version}?detail=…&references=…
```

so the only filters expressible are equality on **resource type, agency, id
and version** — the path segments. There is no name search, no concept filter,
no annotation predicate. `query-language.md` (*Pushdown*) called this
"well-aimed rather than weak", and #276
measured why: most of the spine's 76 hand-written sites are exactly
agency/id/version lookups.

`references=` changes what comes back *with* each artefact and leaves which
artefacts come back alone. The first draft concluded it therefore belongs to
the resolver. §11 shows it is the *only* way a crossed path yields values, so
it is a planner decision about projection.

## 5. The refusal rule

> `filter(name.contains("price"))` cannot push to REST, so the residual means
> fetching every dataflow and filtering locally — the 800-resolutions problem,
> arrived at silently.

The naive rule — *refuse whenever there is a residual* — is too strict: it
would reject `agency == "ESTAT"` plus a local name filter, which is a
perfectly good query over a few hundred rows. The permissive rule — *always
fetch and filter* — downloads every agency's artefacts for a query that wants three.

**The rule is about boundedness.**

> A residual is admissible only over a **bounded** scan. A scan is bounded when
> the pushed constraints are ones a user could reasonably have issued as a
> request in their own right.

For SDMX REST that is decidable from the URL:

| pushed | request | bounded |
| --- | --- | --- |
| type + agency + id + version | one artefact | ✅ |
| type + agency | an agency's codelists | ✅ |
| type alone | *every agency's* codelists | ❌ refuse |

So **a residual requires that `agency` was pushed.** The rule follows from
the wire format and needs no row-count threshold to tune.

### The implemented rule is narrower than the table

Read strictly, the third row refuses `reg.query(q.codelist)` with no filter at
all — a scan of every agency's codelists. The implemented rule does not: it
refuses an unbounded scan **carrying a residual**, and lets a bare unbounded
scan through. The §10 survey is what surfaced the difference.

The code is right and this table is the loose statement. As built,
`plan_program` refuses when a scan carries a residual *or* a §9.1 membership
and `is_bounded` fails; a bare listing (`reg.query(q.dataflow)`) and a query
REST answers whole (equality on agency, id or version alone) need no agency. `query(q.codelist)`
with nothing else asks for every codelist; it returns that, and the caller can
see the cost in the query they wrote.
`query(q.codelist).filter(name.contains("price"))` is a caller asking for
*three* codelists, and the fetch-everything is invisible in the text of it.
The rule exists for the second, and lets the first through.

### The bound is per scan, and the program has several

The rule above counts artefacts within one scan. A program (§9.2) issues
several, so **every scan in the program carries the bound separately** — the
`dsd` scan of §1 step 1 must be bounded on its own terms, and it is, by the
same agency.

Without a per-scan check an unbounded scan re-enters through the program: a
rewrite that bounds the *outer* scan by a set computed from an *unbounded*
inner one has moved the unbounded fetch one step down the program, where it is
harder to see. So the check runs on each scan as it is planned; a
whole-program check misses this case.

The cost unit changes too. Bounded-in-artefacts is not bounded-in-requests: a
program of fixed length is fine, but a rewrite that issued one request *per
row* of a previous step would satisfy every bound above and still be the
800-resolutions problem. So a program's length must be a property of the plan,
never of the data — which the rewrite in §9.1 satisfies, since it emits one
request per path prefix regardless of how many urns step 2 produced.

### Limit and offset do not survive a residual

`limit(10)` with a residual filter cannot be
pushed: fetching 10 rows and *then* filtering yields fewer than 10, silently.
The alternatives are fetch-until-10-survive (unbounded) or fetch-all-then-limit
(bounded only if the scan is).

So `slices` may only be pushed when the residual is empty, and a plan with both
a residual and a slice is admissible only under the §5 bound. This is the same
reasoning `_refuse_after_slice` already applies within one backend, and it
should reuse the argument rather than restate it.

### The refusal must name the fix

Consistent with #233, #305 and the whole of %11: a valid result or an error
naming the problem, never a plausible-looking wrong one. The message should
say which predicate could not be pushed and what would make the query
admissible — almost always "add an agency".

## 6. `explain()` reports the program

`explain()` makes the planner's decision inspectable, as the SQL corpus and
the mutation benchmarks do for theirs. It prints the program, in order, with
each scan's bound beside it, because §5 makes the bound per scan and a single
verdict for the whole query would hide the case that rule catches.

```
Backend  fmr (SDMX REST)
  1 GET    /structure/datastructure/ESTAT?references=children
           bounded — agency pushed
  2 LOCAL  urns ← dsd.components.concept.id == "REF_AREA"
  3 GET    /structure/dataflow/ESTAT
           bounded — agency pushed
  4 LOCAL  structure_urn ∈ urns (2), limit 10
```

A residual step names the predicate it evaluates locally, so the difference
between "pushed" and "rewritten and then applied locally" is legible rather
than inferred. Requests are numbered so a later step can cite the one it
consumes.

**Not built.** `Query.explain()` today prints the compiled SQL and its binds,
and — for a query that fetches first — one line saying so: *"fetches before
running; the SQL below is over materialised rows"*. The `Program` exists
(`plan_program`) and the requests are computable (`_rest.requests_for`), but
neither is printed. This section is the design for when it is.

`explain()` is already total, because #306 made the language strictly lazy —
that was named as the precondition for pushdown and is the reason this is
reachable at all.

## 7. Staging

Each step is separately valuable and separately reviewable. **All four have
landed**; the status is given per step.

1. **Extract `Plan`.** *Built — `_plan.py`.* No behaviour change.
   `sql_corpus` diff must be empty — that is the whole test.
2. **`Capability` + DuckDB's declaration**, which is "everything", and route
   compilation through the planner. *Built — `_pushdown.py`, `DUCKDB`
   (`total=True`).* Again a no-op; again the corpus is the evidence.
3. **The planner and the refusal rule**, tested against a fake backend whose
   declaration is narrow. *Built — `plan_program`, `is_bounded`;
   `tests/unit/test_query_pushdown.py`.* No network involved, and this is where the
   safety-critical logic gets its properties.

   The planner returns a **program** — an ordered sequence of backend calls
   with local steps between — rather than `(pushed_request, residual)`. For
   DuckDB every program has length one, so the pair would look correct
   throughout steps 1–3 and fail at step 4, when the §9.1 rewrite needs two
   calls stitched by `Session.value_set`.
4. **The REST backend**, and the §9.1 rewrite with it. *Built — `_rebase.py`,
   `_closure.py`, `_rest.py`, `_execute.py`, `RestRegistry.query`.* The
   differential runs against `tests/support/fake_rest.py`, a fake service that
   computes `references=` from the SDMX meaning rather than from what the
   closure asked for (`tests/unit/test_query_rest_executor.py`), so a short
   fetch can disagree with a complete store; the live paths are
   cassette-replayed in `tests/integration/`.

**Correction, made while building step 4: the rewrite *is* checkable without
REST.** The paragraph below argued it needed a second executor to disagree
with. That was wrong: the rewrite's **target form is already expressible**. `is_in` over a single-column
sub-query is exactly "match the rows of that other scan by key", so both
formulations can be written today and run by DuckDB:

```python
A = query(dataflow).filter(P)                                        # joined
B = query(dataflow).filter(fk.is_in(query(dsd).filter(rebase(P))))   # rewritten
```

The two must return the same rows, so this is a differential between two
different executions. `_rebase.py` and
`tests/unit/test_query_rebase.py` were built on that, and it holds across nine
predicate kinds. The reasoning below still stands for the *planner* half —
discovering the rewrite and issuing it — but not for the transformation, which
is done and verified.

**The rewrite moved from step 3 to step 4.**
The earlier draft put it in step 3, so that a length-two program was exercised
before REST arrived. That was aimed at one risk — the `(pushed, residual)`
pair surviving — and step 2 already retires it: `Program` is a sequence, and a
push-plus-residual split exercises two steps today.

The rewrite itself needs a second executor, which step 3 lacks. Rebasing a predicate onto a sub-scan root means reconstructing its
columns at a shorter path, and a column is one of six-plus classes with
divergent constructors — `Label` carries a language, `SideLabel` an owner and
a key, `Win` a whole window specification. Get one wrong and the result is a
*wrong query*, not an error. The check that catches that is a second executor
disagreeing, which step 4 brings.

So the rewrite is built beside the backend that needs it, under the
differential gate that can see it. What step 3 owes is that nothing in the refusal rule assumes
one scan — §5's per-scan bound is written and tested as a per-`Push` check for
that reason, and it is trivially satisfied today.

Steps 1–3 need no second backend to be worth having: they make the DuckDB path
declarative and the refusal testable. What they must not do is make the
length-one program look like the general case — §9.2.

## 8. Gates

As built: the refusal is asserted in `test_query_pushdown.py` and per shape in
`test_query_rest_survey.py`; the differential is
`test_a_fetched_store_answers_what_a_complete_one_does` and
`test_a_short_fetch_would_have_been_caught`, against the fake service named
in §7; the corpus is `tests/support/sql_corpus.py`.

- **The refusal is load-bearing.** A query whose residual would run over an
  unbounded scan must raise, and the message must name the predicate and the
  fix.
- **Differential.** The same query against DuckDB and against a REST
  backend (a fake service, as built) returns the same artefacts. This gate
  tests the claim; the others support it.
- **No silent widening.** A property that pushed-plus-residual selects exactly
  what local-only evaluation does. This is the one that would catch a
  capability declared too generously, a failure with no other symptom.
- **The corpus stays byte-identical** through steps 1 and 2.
- **A declared semantics is checked, not trusted.** For every shape a backend
  declares under a fold, the differential property must include inputs that
  *distinguish* that fold — differing case, accents, punctuation. A capability
  declaring `casefold` on a case-sensitive server passes a fixture-shaped test
  and is wrong in production (§9.3); this is the same discrimination
  requirement rule 4 of the oracle notes already imposes on strategies.

## 9. What REST changes for the seam built in step 1

Steps 1–3 are DuckDB-only, so there is a live risk of designing the seam
against one backend and finding the second does not fit. Four places where
REST differs from a narrower DuckDB, and what each implies for the early steps.

### 9.1 A predicate is not atomic

This has the largest consequence. On DuckDB,

```python
q.dataflow.dsd.components.concept.id == "REF_AREA"
```

is one correlated `EXISTS`, and the only question is whether it pushes. REST
cannot filter dataflows by their DSD's concepts at all — but it can answer the
question in **two requests**:

1. fetch the DSDs whose components use `REF_AREA`,
2. filter dataflows by `structure_urn ∈ {those urns}`.

That is a semi-join rewrite, and it is a *transformation* — the thing §2 argued
we do not need, because DuckDB's planner does its own. DuckDB never needs this
one, since it has joins. REST needs it to be useful for anything beyond
agency/id/version lookups.

The machinery already exists: `Session.value_set` registers a set as an Arrow
relation at ~48 µs regardless of size (#271), *(That rate was duckdb with pandas absent; with pandas importable it is ~1-2 us — DuckDB retries `import pandas` per bound value when it is missing (#411). The shape holds either way. See `tests/unit/test_value_set.py`.)* which is exactly the second step
of that rewrite. The planner was the missing piece.

**Implication for step 2** (taken: `pushes` is keyed by prefix, and the
rewrite is `_pushdown._rewrite` producing a `Membership` step).
`Capability.pushes` as first sketched —
"which predicate shapes can you evaluate" — bakes in the atomicity. The
capability must be consulted **per path prefix**, so a planner can ask "can you
scan `dsd` filtered by `components.concept.id`?" separately from "can you scan
`dataflow` filtered by `dsd.components.concept.id`?". That is a small change to
make now and an invasive one to retrofit.

### 9.2 A program issues several backend calls

The §5 bound counts artefacts. A decomposed plan costs **round trips**, and
bounded-in-rows is not bounded-in-requests: the rewrite above is two, and a
naive per-artefact resolution is N.

**Implication for step 3.** The planner should return a *program* — an ordered
sequence of backend calls with local steps between — rather than
`(pushed_request, residual_predicate)`. A pair cannot express the rewrite, and
that shape is the natural one to reach for when only DuckDB exists, because for
DuckDB the program always has length one.

### 9.3 A pushed predicate runs under the server's semantics

`_label_match` and the `Fold` ladder exist because this library pins what
matching *means* — `==` is case-sensitive, `.str.caseless` is not, `unaccented`
and `folded` are label-only (#222, #255). All of that is pinned **locally**,
in SQL we emit.

A predicate pushed to a server is evaluated by rules we do not control. If a
registry matches ids case-insensitively and we push `id == "cl_freq"`, the two
backends disagree — and the differential gate would catch it only if a fixture
happens to differ in case.

**Implication.** A capability declaration must state the *semantics* it
provides, not merely that it accepts the predicate shape. Where a backend's
semantics are unknown or differ, the predicate stays local. This is why §4's
`pushes` is keyed on `PredicateShape` rather than on predicate *type* — the
shape has to include the fold.

### 9.4 A REST scan yields artefacts

DuckDB returns tuples of columns; the structure endpoint returns a
`StructureMessage` — a bag of whole artefacts. So `Select` (projection,
aggregation) has nothing to push: no server computes `count(*)` for us.

The first draft concluded from this that `select()` / `group_by()` are
**unavailable** on a REST root. §11 measured it and that is wrong — artefacts
to rows is an ordinary local projection, and the columns a crossed path needs
arrive in the *same request* via `references=`. Projection is available, it is
computed locally, and what it decides is the shape of the fetch. See §11.

### Unchanged by REST

The §2 argument stands: none of the above wants a second data structure beside
`Query`. `9.1` wants capability lookup keyed by path prefix, `9.2` wants the
planner's return type to be a sequence, `9.3` wants declarations to carry
semantics, `9.4` wants `Select`'s plan kept separable. All four are shapes of
the *planner and its declaration*, and none of them is a node type.

## 10. Survey: the value of a REST backend

Before building an executor, a cheaper experiment was run: express the question shapes
#276 inventoried across its 76 hand-written sites, declare SDMX REST's
capability from the URL, and run the planner. No HTTP, no cassettes — the
declaration and the planner are the whole apparatus.

**The declaration is six lines.** `/structure/{resource}/{agency}/{id}/{version}`
gives equality on agency, id and version; the resource is the accessor's own
constraint rather than a predicate. `orders=False`, `slices=False`,
`bounding={agency}`.

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

## 11. Projection, and REST's `references=` join

§9.4 asserted that `select()` and `group_by()` are probably unavailable over a
REST root, on the reasoning that the endpoint returns artefacts rather than
rows. Measured against the recorded ECB responses, that conclusion is wrong,
and REST has a capability §9 did not account for.

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

### Projection decides the fetch

`select(q.dataflow.id, q.dataflow.dsd.id)` needs a value from across an edge.
It cannot push, since the server computes nothing, and it needs no per-row
resolution: one request with `references=datastructure`
returns everything the projection names, and turning 102 dataflows plus 81
DSDs into rows is ordinary local work. `group_by(q.dataflow.dsd.id)` with a
`count()` is the same: the data is present, the aggregate is ours to compute.

**So `Select` does not need a plan of its own.** What it needs is to contribute
to the request: the set of path prefixes its columns name is exactly the set
of `references=` types the fetch must ask for. Projection drives prefetch.

**§13 widens this.** A *filter* evaluated locally also needs the joined
values, and for a backend that computes nothing, every filter is evaluated
locally. So the fetch must close
over every path the query reaches, not only the ones it projects. Stated as
"projection drives prefetch", this section was true and too narrow.

### The planner's choice

For `filter(q.dataflow.dsd.id == "X")` there are now two admissible programs:

1. **Rewrite** (§9.1): `GET /datastructure/ECB/X` + `GET /dataflow/ECB` — two
   requests, both small, because the inner scan is *narrowed by the filter*.
2. **Prefetch**: `GET /dataflow/ECB?references=datastructure` — one request,
   2.15 MB, then filter locally.

Neither dominates. The rewrite wins when the inner predicate narrows hard —
one DSD instead of 81. Prefetch wins when the query needs the joined values
anyway, which a *projection* always does and a *filter* never does. A first
implementation can pick the rewrite for filters and prefetch for projections
without a cost model, because that split follows from what each needs rather
than from a guess about sizes.

### The prefetch needs its own bound

The bound in §5 is about which rows come back. Prefetch is about how much
comes back *per row*, and it is unbounded in a way that a scan-level rule does
not see:

```
/codelist/ECB/CL_CURRENCY/latest?references=all   →  29.8 MB, 143,146 codes
```

That is **one artefact**, fully bounded by every rule in §5, returning 30 MB
because `all` followed every edge it could. So a capability declares which
prefixes it can prefetch, and the planner never issues `references=all`; it
asks for the types the projection names and nothing else.

### Implication for the staging

Step 4 gains a piece and loses one. It gains prefetch — `Select`'s prefixes
become `references=` types — and the `Select` open question is answered rather
than deferred. It loses nothing structurally: prefetch is a field on
`Capability` and a decision in the planner, not a new node or a second plan.

### `detail=` follows the same rule, with one extra constraint (#318)

`references=` decides which *relations* come back; `detail=` decides how much
of each artefact. Both are derived from the closure and both fail silently
in the same way, so they are computed together — but `detail` has a constraint
`references` does not.

**What a stub omits, measured.** `codelist/ECB?detail=allstubs` returns 244
codelists; comparing field-by-field against a full response for the 109 present
in both, exactly two things differ: `codes` and `description`. `id`, `agency`,
`version` and `name` are all present. A third field, `maintainer`, also
differed and is *not* attributed to `detail` — the full response was
`references=all`, which pulled the AgencyScheme and resolved the agency's
name. Attributing it here would have made every query needing an agency name
ask for `full` on the strength of a confounded comparison.

The saving is worth having: 88 KB for the whole catalogue as stubs, against
69 KB for `CL_CURRENCY` alone at full detail.

**Only a projection may ask for a stub.** A row terminal resolves rows to
artefacts out of the store, and a stub-populated store answers `collect()`
with a codelist carrying zero codes and no error. So `projection=None` means
`full`, and that is the default; a `Select` naming stub-surface columns is the
only thing that can ask for less.

**The rewrite's own reference needs full detail.** The §9.1 membership narrows the
outer scan by `dataflow.dsd_urn`, which a stub does not carry. That column is
not in `step.predicates` — the *planner* produced it — so a check walking only
what the caller wrote reported `allstubs`, every `dsd_urn` came back null, and
the membership matched nothing. The same class of bug as computing
`references=` from a push's own plan, and found the same way: by reading the
URLs the planner emitted.

## 12. Materialise or query through, with numbers

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

## 13. The executor is a fetch planner

One check before building step 4b changed what it is.

### Evaluation is local, so the executor needs no interpreter

§12 concluded: materialise. Follow that through and the REST backend stops
needing a local predicate evaluator, a local sort, a local projection, or an
artefact-to-row layer. It fetches artefacts, writes them into a scratch DuckDB
at 2.6 ms each (#317), and runs **the original query** through the compiler
that already exists.

Measured on ECB's catalogue: 183 artefacts materialise in 0.51 s, and the
query then runs in 1.6 ms.

For a backend that cannot compute, **pushdown is fetch planning**. The program says
which requests to issue; the answer is always computed locally. `Residual` and
`Membership` stop being work the executor performs and become the *reason* a
particular fetch is enough.

### The fetch closes over every path the query touches

A short fetch fails silently. `references=datastructure` returns the DSDs, so
`select(q.dataflow.id, q.dataflow.dsd.id)` works — §11 measured exactly that.
It does **not** return the concept schemes those DSDs reference, so

```python
q.dataflow.dsd.dimensions.any(q.component.concept.id == "REF_AREA")
```

evaluates against an empty `concept` table and returns *nothing*, with no
error. Measured on the recorded responses:

| fetch | artefacts returned | concept ids resolvable |
| --- | --- | --- |
| `/dataflow/ECB?references=datastructure` | 102 dataflows + 81 DSDs | **0** |
| `/dataflow/ECB/EXR?references=descendants` | 1 flow, 1 DSD, 1 scheme, 11 codelists, … | **31** |

§11's rule, "prefetch what the projection names", is too narrow. The rule is:

> The fetch must **close over every relation the query's paths reach**, not
> only the ones it projects. A path that is fetched short evaluates against an
> empty table and answers *nothing*, the failure this design exists to
> prevent.

A query is therefore refused not only when its scan is unbounded, but when the
`references=` depth needed to close it is one the backend will not issue.
`references=all` is never that answer — §11 measured 29.8 MB for one codelist.

### The §9.1 rewrite keeps the closure small

If everything is fetched and evaluated locally, the rewrite might look
pointless. It keeps the closure small:

- Without it, closing `dataflow.dsd.dimensions.concept` over an agency means
  `/dataflow/ECB?references=descendants` — every dataflow dragging its whole
  subtree.
- With it, the inner scan is `/datastructure/ECB?references=children`, which
  closes the same paths over 81 DSDs rather than 102 dataflows-plus-subtrees,
  and the outer scan needs no references at all.

Both return the same answer; here the rewrite picks the cheaper closure. That
reason is separate from avoiding an N+1, and it holds even where the N+1
argument does not.

### What step 4b built

Less than §7 implied. Built as listed:

1. `Push` → a URL. The pushed predicates are equality on agency / id /
   version by construction, so this is assembly, not compilation.
   *`_rest.request_for` / `requests_for`.*
2. A `references=` value per `Push`, derived from the paths the query
   evaluates on that scan's rows — the closure above. *`_closure.py`
   (`references_for`, and `detail_for` for §11's `detail=`).*
3. Fetch, parse, materialise into a scratch registry. *`_execute.fetch_into`;
   the store is `RestRegistry`'s `store`, or an in-memory one it owns.*
4. Run the original `Query` against it. *`RestRegistry.query` returns the
   store's own `Query` with a `prepare` hook that fetches before the
   terminal; fetches are keyed by `canonical_key`, so `filter(a).filter(b)`
   and `filter(b).filter(a)` share one.*

The executor has no local evaluator, row-shaping or second compiler. The differential gate
in §8 stays exactly as written, and is now cheap to satisfy: both sides run
the same SQL, over stores populated differently.

## 14. Open

- ~~**Does `Select` push at all?**~~ **Answered in §11.** It does not push. Projection is *available*, computed
  locally, and what it decides is the request: the prefixes its columns name
  are the `references=` types to ask for. `Select` needs no plan of its own —
  it needs to contribute its prefixes to the fetch. `Capability.prefetch` and
  `can_project` carry that.
- **Federation.** Still open. `FederatedRegistry` routes by agency across
  registries (it has no `query()` of its own), and
  agency is also the bounding constraint of §5 and, per §12, the natural
  materialisation unit. These look like they compose into per-agency
  pushdown, but nothing has been prototyped.
- ~~**Caching.**~~ Built in the narrow form: `RestRegistry` keys each fetch by
  `canonical_key` (and the derived `detail=`), single-flight, so a repeated or
  reordered question fetches once per registry — or once per `store`'s
  lifetime when one is supplied.
- ~~**The writer's 8 ms per artefact**~~ — done in #317 / !307: 8.4 ms to
  2.6 ms, and §12's rule moved with it. The two remaining costs (the re-add
  cascade, and batching across artefacts to retire the last pandas probes) are
  named at the end of §12.
