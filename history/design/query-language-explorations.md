# The query language: rejected explorations

> Superseded: rejected branches from the query-language design pass for
> #306/#280, August 2026. The code follows rooted paths through declared edges;
> the design is [query-language.md](../../docs/design/query-language.md).

Companion to [`query-language.md`](../../docs/design/query-language.md) (the
design), [`evidence/query-language.md`](../../docs/design/evidence/query-language.md)
(what was run), [`query-language-examples.md`](../../docs/design/query-language-examples.md)
(the surface, construct by construct) and
[`query-language-cookbook.md`](../../docs/design/query-language-cookbook.md)
(the surface applied).

This file holds branches that were prototyped and **rejected**. They are kept
rather than deleted because each one's failure is the reason a live choice is
right, and re-deriving them costs more than storing them. Nothing here is
proposed. If a section reads as a good idea, read its verdict.

## Bare and contextual paths

### Bare, un-rooted paths in `filter()`

This section answers from scratch, without appeal to the choice already made,
why a bare, un-rooted path cannot be the spelling, fully typed.

**Ambiguity is not the blocker.** Re-measured with a *minimal* edge set — only
real FK columns, no convenience shortcuts, which is what an implementation
would actually declare:

```
minimal FK-only model: 13 edges, 31 reachable pairs, 26 unique (84%)
still ambiguous: component->codelist, component->code, dataflow->codelist,
                 dsd->codelist, dsd->code
```

All five trace to one root cause — `component.codelist` (own representation)
versus `component.concept.codelist` (the concept's default). And the example
above resolves *uniquely*: `dataflow -> concept` is `dsd.components.concept`,
one path. Earlier drafts overstated this; some of the ambiguity measured
before was self-inflicted by declaring shortcut edges.

**Blocker 1: reachability is not statically expressible.** "Fully typed" means
two different things here. Column-level typing — `.id` exists, `== "CL_GEO"`
yields a predicate — is trivially available for a bare path. Reachability
typing — *a route from `Dataflow` to `Concept` exists* — is not.

**In form B the path is the proof.**

```
q.dataflow.dsd.components.any(...)   ok   every dot is a declared edge, checked
q.dataflow.concept                   error: Cannot access attribute "concept"
                                            for class "DataflowCols"
q.dataflow.dsd.components.concept    error: Cannot access attribute "concept"
                                            for class "Many"
```

Writing the path *constructs a machine-checked witness* that the route exists.
The verbosity is the proof term. A bare
reference offers no proof, so there is nothing to check. Validating one
statically would require the type system to decide "does a path exist from
`DataflowRow` to `ConceptRow` in a graph declared at runtime" — a constraint
solver, not Python's type system. The best available is a **build-time** error
when `.filter()` is called, not a check-time one.

**Blocker 2: cardinality, which is independent of typing.** The example's path
crosses `dsd.components`, a to-many. So the filter must mean "*has some*
component whose concept's codelist is CL_GEO" — an `EXISTS` — and nothing in
the bare spelling says so. The third error above is the design refusing exactly
that: `Many` cannot be dotted through, which forces `.any()` and puts the
cardinality at the call site. This blocker survives even where the path is
unique.

**A coherent bare design does exist.** It is buildable, and the objection to it
lies outside its individual costs:

- resolve unique paths; error listing candidates when ambiguous
- to-many hops compile to `EXISTS`, preserving row count
- projection through a to-many refused, or forced through `list()`

That is Django's `__` lookups with `EXISTS` substituted for `INNER JOIN` —
Django without the duplicate-row footgun (`query-language.md` §3.3), and it
would work.

**Form B rules it out for a different reason: a shorter unchecked spelling
next to a longer checked one means the checked one stops being used.** Callers drift to the brief form, and the static
guarantee becomes theoretical while remaining documented. That objection holds
even if every path in the schema were unique, and it is why this is a
either/or rather than a "support both, prefer B" — the same reasoning that
demoted form A, arrived at independently.

What would change the answer: evidence that the explicit form is burdensome in
practice. That is not known yet, and it is a question for consumers rather than
for this document.

## A parent-level `.any()`

### `.any()` on the parent, without the edge name

`sl.q.dsd.any(sl.q.concept.id == "REF_AREA")` is a real middle point: it keeps
**cardinality** explicit (strictly better than Django, whose implicit `INNER
JOIN` is the duplicate-row footgun) and gives up only the **path**. It types
without trouble — `def any[C](self, p: Pred[C]) -> Pred[R]` — but that is the
problem rather than the recommendation: being generic over every row kind, it
also accepts `q.dsd.any(q.metadataflow.id == "MDF")`, which is unreachable.
Both checkers pass it, because nothing in it names a route to check.

The concrete failure, run against a fixture where concept `REF_AREA` defaults
to `CL_GEO`, `CPI` uses it as a dimension but **overrides** the codelist to
`CL_AREA`, and `TRADE` uses it as an attribute keeping `CL_GEO`:

```
sl.q.dsd.any(sl.q.codelist.id == "CL_GEO")
  components.codelist          (component's own representation)  -> ['TRADE']
  components.concept.codelist  (concept's default)               -> ['CPI', 'TRADE']
```

Two real FK routes — `dsd_component.codelist_urn` and
`concept.repr_codelist_urn` — two different questions, two different answers.
**No modelling choice removes this**, because representation override is an
SDMX feature rather than an artefact of our schema.

And note the failure profile: the answers differ *only* on `CPI`, the structure
that overrides. Drop the override from the fixture and both return `['TRADE']`.
So it is invisible in test data where nobody overrides and wrong in production
where they do.

A second case is **contingent rather than inherent**, and an earlier draft of
this section overstated it by not saying so:

```
sl.q.dsd.any(sl.q.concept.id == "REF_AREA")
  dimensions.concept  -> ['CPI']
  attributes.concept  -> ['TRADE']
  components.concept  -> ['CPI', 'TRADE']
```

Three answers only if `dimensions` / `attributes` / `measures` are declared as
separate edges. With a single `components` edge this resolves uniquely. Three
is the likely choice — `DataStructure` already carries three `ItemList`s, and
the survey's largest bucket (21 sites) is role-specific — but it is a decision,
not a fact.

**So the edge name is the only place the role and the route can be
stated.** Where the parent has several to-many edges, the explicit
form is also the shorter one:

```python
sl.q.dsd.dimensions.any(sl.q.component.concept.id == "REF_AREA")   # says it
sl.q.dsd.any(...)                                                   # cannot say it
sl.q.dsd.components.any(                                            # the workaround
    (sl.q.component.role == "dimension") & (sl.q.component.concept.id == "REF_AREA")
)
```

### The two cases mechanism 5 fails

Two failures, and they fail for different reasons. Recorded because "it cannot
be typed" is the wrong summary — the predicate is perfectly well typed.

**The predicate is not the problem.** `q.codelist.id == "CL_GEO"` is a
`Pred[CodelistRow]` and knows exactly which relation it is about. What is
missing is a **term**, not type information: `dsd.any(<pred about a codelist>)`
never writes down *which route* connects the DSD to that codelist, so there is
nothing for a checker to check. A predicate is a proof *about a codelist row*;
it is not a proof that *a codelist row is reachable from a DSD by a particular
route*. Only the first was supplied.

That is a language-design fact rather than a Python limitation. No type system
checks a route the source does not contain. And it is exactly what mechanism 1
supplies: `dsd.dimensions` **is** the missing term, after which `any()` takes a
`Pred[ComponentRow]` precisely and nothing needs inferring.

**The fallback check is also unavailable.** Suppose we give up on *which route*
and check only *reachable at all*. That is encodable — a hand-maintained
transitive closure per root:

```python
type DsdReachable = ComponentRow | ConceptRow | CodelistRow | CodeRow
def any_tight(self, p: Pred[DsdReachable]) -> Pred[R]: ...
```

and it works — `q.dsd.any_tight(q.metadataflow.id == "MDF")` is rejected. So
the root check is genuinely recoverable, and an earlier draft was wrong to mark
it simply unavailable.

Two things kill it anyway.

*The closure is hand-maintained, and large.* Over the minimal FK edge model:

```
dsd 5   dataflow 7   provision 7   component 5
concept 3   conceptscheme 3   codelist 1   code 1
                              32 union members across 8 roots
```

One added edge changes several closures at once, and a stale entry left after
an edge is removed fails *silently in the accepting direction*.

*And it requires `Pred` to be covariant, which predicate conjunction forbids.*
Measured:

```
Pred with only  negate(self) -> Pred[R]                -> 0 errors  (covariant; union works)
Pred with       __and__(self, other: Pred[R]) -> Pred  -> 2 errors  (invariant; union dead)
  "Type parameter R@Pred is invariant, but CodelistRow is not the same as DsdReachable"
```

`other: Pred[R]` is a contravariant position, so `Pred` becomes invariant and
`Pred[CodelistRow]` stops being assignable to `Pred[DsdReachable]`. And AND-ing
predicates is not optional — it is the one composition law
`query-composition.md` found unanimous across SQLAlchemy, Django and ibis.

| what you would check | why it fails |
| --- | --- |
| *which route* — the real question | the term is not in the source; nothing to check |
| *reachable at all* — the fallback | needs covariance; predicate conjunction forbids it |

This also explains why mechanism 1 is unaffected: naming the edge means
`any()` takes one exact row kind rather than a union, so invariance is fine.

What *is* right about the objection: the friction is real, and it is the child
accessor (`sl.q.component`) appearing inside a fan-out rooted elsewhere. The
fix that removes it without touching path resolution is to let `Many` expose
the child's columns directly — `sl.q.dsd.dimensions.concept.id` — which keeps
the edge name and drops the repetition. That is dot-through, which the design
adopted (`query-language.md` §2.9 and §3.4).

## The `Fan[R]` marker

### The fan marker does not nest

The idea was to re-expose the child accessor at a marker row kind `Fan[R]`
meaning "a child row behind a to-many from R", so the marker forces exactly one
interpretation per position — `Pred[Fan[R]]` in a filter is `EXISTS`,
`Col[Fan[R]]` in a projection is `list(...)`. It type-checks cleanly at one
level, and fails at two:

```
q.dsd.dimensions.codelist.codes.id == "CA"       # "DSDs with a dimension whose
                                                 #  codelist has code CA"
error: Pred[Fan[Fan[DsdRow]]] cannot be assigned to
       Pred[DsdRow] | Pred[Fan[DsdRow]]
```

Nested fan-out is an ordinary query, and the marker cannot be made idempotent —
that needs `Fan[Fan[R]] == Fan[R]`, a type-level function Python does not have.

## Strict path with a bare inner predicate

The reasoning in this section was adopted, and is now the design's rule:
`query-language.md` §2.9, "Every edge is named, and `.any()` is the
quantifier". What stays here is the branch it led to and the design did not
build, a postfix universal.

### A postfix `.every()` modifier

Dot-through drops one thing `.any()` carried: the quantifier. It looked
recoverable as a modifier rather than a wrapper:

```python
sl.q.dsd.dimensions.codelist.id == "CL_GEO"             # exists — the default
(sl.q.dsd.dimensions.codelist.id == "CL_GEO").every()   # for all
```

`Pred[R].every() -> Pred[R]` types trivially. It needs two build-time checks,
both decidable from the recorded path: refuse when the path crosses no to-many
(nothing to quantify), and refuse when it crosses two, since "for all
dimensions, for all codes" and "for all dimensions, some code" are different
and the modifier cannot say which. Not prototyped.

If that held, dot-through would subsume `.any()` in filter position, and
`Many`'s verbs would be needed only where the fan-out itself is refined
(`dimensions.filter(...).count()`) and for `list()` in projections. The design
kept `.any(*preds)` for predicates that must land on one child and built the
universal as `FanOut.every(*preds)`, which composes across nested to-many
edges with neither refusal (`query-language.md` §3.4, §3.5 and §14).

## Superseded: the grain claim

### To-many in a projection

`.any()` covers to-many in a *filter*. It does not cover to-many in a
*projection*, and the question that opened #280 is exactly that: "every
dataflow × dimension × codelist as one frame" *wants* row multiplication.

The usual answer is to root the query at the grain you want — SQLAlchemy's
`select(Component)`, Django's `Component.objects` — so only to-one edges are
traversed. That works for "components of ESTAT structures". It **fails** here,
because a DSD may back many dataflows, so `component -> dataflow` is to-many
upward too. The grain is the pair `(dataflow, component)`, which no single base
relation expresses.

So a many-to-many product still needs an explicit join producing a wider row.
Neither path traversal nor `.any()` provides it, and that remains unsolved.

**Both halves of that paragraph were later overturned, in different ways.**
The nested result sidesteps the product entirely (`query-language.md` §6, "A
fan-out nests by default"). And rooting at the child retracts the middle claim
(`query-language.md` §2.4, "The root fixes the grain"): rooting at `component` and traversing `component -> flow` **does**
express the `(dataflow, component)` grain, because that upward edge is to-one
per row and it is the flat shape `structure_frame` already returns. The
sentence "which no single base relation expresses" was reasoning about
*downward* edges only and did not notice the upward reading. Left in place
because the correction records where the reasoning went wrong.
