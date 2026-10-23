# sdmxlib — SDMX for Python

## What this library does

sdmxlib fetches, models, stores and queries SDMX: structural metadata
(DataStructures, Codelists, Dataflows, etc.), reference metadata and
observation data, from SDMX REST APIs, SDMX files or a local DuckDB store.
Users navigate a typed graph of artefacts
through plain attribute access. Observations are queried through `obs()` and
come back as Polars frames.

## SDMX glossary

- **DSD** (DataStructureDefinition): defines the shape of a dataset —
  dimensions, attributes, measures
- **Dimension**: component that identifies an observation. Usually enumerated by
  a Codelist.
- **Codelist**: finite set of valid codes (e.g. CL_FREQ: A=Annual, Q=Quarterly)
- **Concept**: semantic meaning of a component (e.g. "Frequency")
- **Dataflow**: published data product backed by a DSD
- **ProvisionAgreement**: binds a data provider to a dataflow
- **MSD** (MetadataStructureDefinition): defines the shape of a reference-
  metadata payload — a tree of MetadataAttribute slots
- **MetadataAttribute**: one slot in an MSD (id, concept, representation,
  min/max occurs, optional children for hierarchy)
- **Metadataflow**: published reference-metadata product backed by an MSD
  (mirrors Dataflow → DSD)
- **MetadataSet**: a populated reference-metadata payload — maintainable,
  references a Metadataflow (or a metadata provision agreement), carries `targets: list[Ref]` (the
  identifiable artefacts the reported attributes describe) and a single
  attribute tree that applies to all targets. SDMX 3.0 dropped the v2.1
  `MetadataReport` layer; we follow the standard.
- **ReportedAttribute**: one populated attribute within a MetadataSet (id +
  scalar/InternationalString value + optional recursive children). Repetition
  uses sibling attributes with the same id, never a values list.
- **MetadataMessage**: wire-format envelope carrying populated MetadataSets
  (`<mes:GenericMetadata>` in SDMX-ML 3.0; metadata-schema envelope in SDMX-JSON
  2.0). Sibling to `StructureMessage`.
- **URN**: globally unique identifier for any SDMX artefact
- **Ref[T]**: reference to another artefact. Frozen; always carries URN, and
  finds its target on access through the registry that bound it (#492). ref()
  to unwrap.

## Architecture rules

- Baseline runtime deps: `attrs`, `httpx`, `lxml`, `duckdb`, `polars`,
  `pyarrow`, `msgspec`, `typing-extensions` (PEP 728 closed TypedDicts, until
  `typing` has them in 3.15). All required — none are optional extras. Any module may
  import them. `msgspec` is used everywhere we touch JSON (SDMX-JSON
  reader/writer/metadata/data): typed `msgspec.Struct` DTOs encoded via the
  shared `_dto.ENCODER`, typed `msgspec.convert` / `msgspec.json.decode` on
  read. orjson was removed in milestone #8; neither orjson nor stdlib `json`
  should appear in `src/sdmxlib/formats/sdmx_json/`.
- All domain objects in `sdmxlib.model` are attrs `@frozen` (#492); an
  artefact or item is `@frozen(unsafe_hash=False)` — equal by value, not
  hashable (key by `urn`). A change is an `attrs.evolve` copy; derived state
  set in `__attrs_post_init__` goes through `object.__setattr__`. Domain types stay pure data + behaviour over that data
  — no XML/JSON awareness, no HTTP, no I/O side effects.
- `Ref[T]` for every outgoing URN edge. None = "doesn't exist."
- Registry presents the remote graph as local attribute access.
- Format layer in `sdmxlib/formats/` owns all XML/JSON parsing and serialisation
  — model imports nothing from `formats/`.
- `sdmxlib/polars.py` is the user-facing Polars bridge (DataFrame construction,
  label maps, etc.); the structural query language (`sdmxlib/query/`) uses
  duckdb internally for execution and returns polars frames from `select()`.

## Caching & optimization passes (read before adding a cache)

Before adding or extending **any** cache during a performance pass, consult
`docs/design/caching.md` — the caching decision framework. It exists so we stop
accreting ad-hoc caches that paper over query/layout problems. Non-negotiable
summary:

- **Measure before you cache.** The trigger is a profile showing the
  _computation_ is expensive and reused — not a load test showing a slow
  _symptom_. If you can't name the slow line, you don't cache (see the issue
  #155 case study: a "58 s label map" that measured 8 ms — the real cost was LLM
  round-trip time, mis-attributed).
- **Decision hierarchy, in order:** (1) profile it, and stop if it is fast.
  (2) If physical layout at build time fixes it, fix that. (3) If the input is
  immutable within a bundle or artefact version, **bake it** into the bundle
  (like `code.depth` / `code.ancestor_ids`) instead of caching at runtime.
  (4) Only if the cost is genuinely per-process, add a runtime cache.
- **Runtime caches** must be single-flight (not just a lock), bounded,
  invalidation tied to a lifecycle, placed at the _source_ of the cost, and
  routed through the shared primitive — not a fresh module global. Contract:
  _concurrent reads are safe and do not duplicate work._
- Guard the fix with a test asserting the **count** of expensive calls (SQL
  round-trips / builds), not wall-clock.

## Common patterns

```python
import sdmxlib as sl

with sl.RestRegistry(sl.Provider.SDMX_GLOBAL) as reg:
    dsd = reg.get(sl.DataStructure.urn_for(agency="ESTAT", id="CPI"), resolve=True)

    # Navigate — pure attribute access
    dsd.dimensions                     # ItemList[Dimension]
    dsd.dimensions["FREQ"]             # Dimension
    dsd.dimensions["FREQ"].representation    # Ref[Codelist | ValueList]
    dsd.dimensions["FREQ"].representation()  # the Codelist (unwrap)
    dsd.dimensions["FREQ"].codelist          # the Codelist — the Ref, followed
    dsd.dimensions["FREQ"].codes             # ItemList[Code] — builds them (#398)
    dsd.dimensions["FREQ"].code_ids()        # list[str] — the cheap path


    # Aggregate
    dsd.code_map(lang="en")                # dict[str, dict[str, str]]
    dsd.all_codelists                  # dict[str, Codelist]
    dsd.column_labels(lang="en")            # dict[str, str]

    # Filter
    dsd.dimensions.filter(lambda d: d.is_enumerated)   # lambda
    dsd.dimensions.filter(sl.item("is_enumerated"))    # expression
    dsd.dimensions.partition(lambda d: d.is_enumerated)

    # Pattern match
    match dim.representation:
        case sl.Ref(): ...           # enumerated — dim.representation() → Codelist | ValueList
        case sl.Facet(type=dtype): ...  # free text — dtype is DataType | str | None

    # Query — the path language over REST (#312)
    for df in reg.query(sl.q.dataflow).filter(sl.q.dataflow.agency == "ESTAT").collect():
        print(df.id, df.structure.dimensions.ids())
```

## SDMX Model Implementation Comparisons

Two key python implementations of the SDMX information model are:

- [sdmx1](https://github.com/khaeru/sdmx)
- [pysdmx](https://github.com/bis-med-it/pysdmx)

Use these to check our model, and when possible make sure we follow the data
representation they use if we are unsure.

## Issue references

Issues live on GitHub (StatCat-Suite/sdmxlib). The project moved from GitLab
in October 2026, and the tree cites the GitLab tracker about 4,350 times as a
bare `#N` — `#492`, `#75`. Those keep meaning GitLab
(gitlab.com/pinax-suite/sdmxlib, an archive now) and are not rewritten.

**Write a GitHub issue or PR as `GH-N`**, never a bare `#N`: GitHub numbers
from 1, so a bare number would collide with the GitLab one the tree already
means. GitHub links `GH-N` by itself. The 31 issues still open at the move were
re-created on GitHub, each carrying a "Moved from GitLab #N" line.

## Commits

Conventional commits, `type(scope): subject`, with the issue as `(GH-N)` at
the end. git-cliff builds `CHANGELOG.md` from the subjects (`cliff.toml`): the
type picks the section an entry lands in, and git-cliff drops a subject it
cannot parse from the release notes.

- `feat` / `model` → Features, `fix` → Bug Fixes, `docs`, `perf`, `refactor`,
  `test`, and `chore` → Miscellaneous.
- `!` after the type or scope (`fix!:`, `feat(query)!:`) is a breaking change:
  it heads the entry under Breaking Changes, bumps the minor while we are
  pre-1.0, and makes `release-3-pr` require a `docs/upgrading.md` change since
  the last tag (`just release-check-upgrading`).
- `ci`, `chore(release)` and merge commits are skipped.

## Related repos

The two other StatCat-Suite libraries build on this one, so a breaking change
here lands there. Each has its own maintainers and agents: from here they are
read-only, and a change they need is an issue on their tracker
(`StatCat-Suite/<repo>#N`).

- `../statcatlib` — the catalog bundle format. Publishes through
  `sdmxlib.bundle` (`write_manifest(extensions=...)`, wrapping
  `check_bundle`), injects a `SqlRelation` into the path language, and reads
  `storage.schema.SCHEMA_VERSION`.
- `../sdmxlib-rest` — an SDMX-REST server over a `LocalRegistry`: the model
  artefacts, `DataflowBinding`, `obs()` and `ObsQuery`.

Both pin sdmxlib by range in their `pyproject.toml`. Before a breaking release,
`git grep` the name in both checkouts and write the move in
`docs/upgrading.md`.

## Key files

| File                              | Purpose                                                                                                                                                                |
| --------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `model/ref.py`                    | Ref[T], SdmxRef, UnresolvedRef                                                                                                                                         |
| `model/_resolvers.py`             | What a `Ref` looks its target up in (#492): `MapResolver` over a registry's store or a document's index (`document_binding`); readers bind as they build (`_binding.py`, which also holds the `Resolver` protocol), `add` binds a copy (`_rebind.py`) |
| `model/urn.py`                    | SdmxUrn[T] — typed artefact identity                                                                                                                                   |
| `model/collections.py`            | ItemList[T], ArtefactList[T]                                                                                                                                           |
| `model/expr.py`                   | item(), ItemExpr — filter expressions                                                                                                                                  |
| `model/registry.py`               | `RegistryReader` / `SupportsGet` / `SupportsGetAll` / `SupportsAddAll` protocols — the uniform read seam |
| `model/in_memory_registry.py`     | InMemoryRegistry — URN-keyed store; `add` keeps a copy whose references are bound to it (#492) |
| `model/binding.py`                | DataflowBinding + DriftIssue + DriftError                                                                                                                              |
| `query/_plan.py`                  | `Plan[R]` — what a query *says*, split from how it runs (pushdown §3)                                                                                   |
| `query/_closure.py`               | What a fetch must contain for a locally-evaluated query — crossings, not hops; fetch short and the answer is silently empty |
| `query/_rest.py`                  | `Push` → an SDMX REST `Request`; `references=` comes from the closure, covering the *local* work that follows the scan |
| `query/_execute.py`               | Fetch what a plan needs, materialise it, then run the *original* query — no local evaluator (§13)                        |
| `query/_pushdown.py`              | `Capability` + `Program` — what a backend can evaluate, and the ordered work a `Plan` becomes for it                                                     |
| `query/_rebase.py`                | Re-roots a predicate onto a shorter path — the §9.1 rewrite's transformation, differentially checked against the joined form |
| `query/_edges.py`                 | Declared edge graph (#306) — one `Edge` per storage FK, read both directions; cardinality *derived* from key constraints, guarded in `tests/unit/test_query_edges.py` |
| `query/accessors.py`              | `sl.q` — lowercase singletons per relation; paths carry the *root's* row kind                                                                            |
| `query/raw.py`                    | `reg.sql()` — the raw-SQL escape hatch; `$name` rewritten to `?`, checked against DuckDB's own parse                                                    |
| `query/schema.py`                 | `sl.schema` — qualified names for raw SQL, reflected from the DDL so a stale column is an `AttributeError`                                              |
| `query/_joins.py`                 | Paths → hops → a join tree with shared-prefix dedup                                                                                                     |
| `query/_compile.py`               | Predicates → SQL; a to-many hop becomes a correlated `EXISTS`, never a join                                                                              |
| `query/query.py`                  | `Query[R]` — a `Plan` plus its binding; strictly lazy, refining; `reg.query(sl.q.dsd)` is the entry                                                                                 |
| `query/_common.py`                | The refinement laws (`narrow_limit`, `advance_window`) and `canonical_key` — all that survived `_query_common`'s 2400 lines                             |
| `model/_tree.py`                  | Nested items keyed by **path** (#439) — `PathIndex` + `PathNavigation`, shared by `CategoryScheme`, `Hierarchy`, `MetadataStructure`; a leaf id may repeat across branches, so `scheme["A.B"]` / `by_id(leaf)` |
| `model/version.py`                | `sort_key` (ordering, #311) + `compliance` / `unwritable` (#319) — the 2.1 and 3.0 version grammars are *not* nested, neither way; a refusal is a `ValidationError` |
| `model/_rules.py`                 | What a value alone decides — id, agency id, version, name, facet bound — as attrs field validators, so a bad one is refused as it is set, not at `validate()` (#492); tests build one inside `tests/support/unchecked.py` |
| `model/_invariants.py`            | What an artefact as a whole must be, checked as it is built (#492): `validation.py` registers each class's rules, `__attrs_post_init__` calls `enforce`; `validate()` keeps only what a message can know |
| `model/istring.py`                | InternationalString                                                                                                                                                    |
| `model/datastructure.py`          | DSD, Dimension, Attribute, Measure                                                                                                                                     |
| `model/valuelist.py`              | ValueList, ValueItem — SDMX 3.x's enumeration that is not a codelist; a representation is `Ref[Codelist \| ValueList]`, and 2.1 leaves a value list, and what names it, out (#422) |
| `model/link.py`                   | Link — SDMX 3.x's `<com:Link>`, a `links` tuple beside every `uri`; its `urn` is text (`anyURI`), not a `Ref`; SDMX-ML 2.1 drops links, a declared gap (#422) |
| `model/metadatastructure.py`      | MSD, MetadataAttribute                                                                                                                                                 |
| `model/metadataflow.py`           | Metadataflow                                                                                                                                                           |
| `model/metadataset.py`            | MetadataSet, ReportedAttribute, ReportedAttributeValue alias                                                                                                           |
| `model/message.py`                | StructureMessage, MetadataMessage                                                                                                                                      |
| `model/header.py`                 | A message's header (#480): `Header` / `MetadataHeader`, `Party`, `Sender`; SDMX-ML in `formats/sdmx_ml/_header.py`, SDMX-JSON `meta` in `formats/sdmx_json/_header.py`; none writes fixed placeholders and reads back as none |
| `formats/sdmx_ml30/metadata.py`   | `<mes:GenericMetadata>` reader+writer                                                                                                                                  |
| `formats/sdmx_json/metadata.py`   | SDMX-JSON 2.0 metadata-schema reader+writer                                                                                                                            |
| `formats/sdmx_json_1_0/writer.py` | SDMX-JSON 1.0 ContentConstraint writer (`/availability` downgrade)                                                                                                     |
| `builders/contentconstraint.py`   | `build_dataflow_constraint` (flow-wide) + `build_component_availability` (per-component slice, exact/available) — synthesise availability DataConstraint from the lake |
| `api/session.py`                  | Session — identity map + construction                                                                                                                                  |
| `api/registry.py`                 | RestRegistry — user-facing HTTP entry; `get(urn, resolve=, partial=)`, `query()` (the path language over REST, #312), `obs()`, `download()`; `store=` keeps what it fetches |
| `api/federated.py`                | FederatedRegistry — agency-routed dispatch across Registry sources                                                                                                     |
| `api/policy.py`                   | `RegistryUnavailable` — a source that could not be reached (federation and REST) |
| `data_store.py`                   | DataStore Protocol — the data plane's *write* side; reading is `obs()` (#329) |
| `local/data_store.py`             | DuckDBDataStore — DuckDB/DuckLake-backed DataStore, shares a `LocalRegistry`'s connection; `obs(binding)`, `write_observations`, `inspect()` |
| `local/sql.py`                    | SchemaInspector + QueryPlan — custom-SQL seam (schema reflection + binding→query planning, cached DESCRIBE)                                                            |
| `api/query.py`                    | `DataQuery` — the `reg.data(...)` builder that resolves to a `Dataset` |
| `formats/gaps.py`                 | What each wire format cannot carry — `sl.unrepresentable(artefact, "3.0")`; the round-trip guard proves each row |
| `formats/sdmx_ml21/`              | lxml-based XML reader/writer (2.1)                                                                                                                                     |
| `formats/sdmx_ml30/`              | lxml-based XML reader/writer (3.0); structure envelope. Sibling `metadata.py` handles `<mes:GenericMetadata>`.                                                         |
| `polars.py`                       | Polars bridge                                                                                                                                                          |
| `_time.py`                        | Naive-UTC convention for every TIMESTAMP — a tz-aware value in a zoneless column shifts per reader's machine (#321); the model holds every datetime tagged UTC via `as_utc`, so a parsed artefact equals its stored self (#440) |
| `storage/schema.py`               | DuckDB DDL (sdmx schema namespace)                                                                                                                                     |
| `storage/writers.py`              | Write domain objects to DuckDB                                                                                                                                         |
| `storage/readers.py`              | Reconstruct objects from DuckDB                                                                                                                                        |
| `storage/_checked.py`             | A store read checks what it builds as `add` does (#492): `get` raises, a bulk read records it in `LocalRegistry.refused` and reads on |
| `storage/lazy.py`                 | `LazyCodelist` / `LazyHierarchy` — store-backed facades whose walks return a `Query` |
| `local/registry.py`               | `LocalRegistry` — the DuckDB store: `add`/`add_all`, `get` (lazy codelists and hierarchies), `query`, `sql`, `obs`, `refused`, `bundle` |
| `query/observations.py`           | `ObsQuery` — the path language over a dataflow's observations; `obs.col(id)` names a component |
| `query/_expr.py`                  | Columns, predicates, aggregates and windows — what `sl.q` paths build; `where=` on an aggregate |
| `storage/resolve.py`              | Batch resolvers — key rows to domain objects for the path language (`cl.codes` → `Code`s) |
| `model/representation.py`         | `Facet`, `DataType`, `Representation` (`Ref[Codelist | ValueList] | Facet | None`) |
| `model/errors.py`                 | `ValidationError`, `SdmxParseError`/`SdmxConformanceError`, `Refusal`, `ReadGap`, `LossyReadError`, `SdmxWarning`/`ImRuleWarning` (#481) |
| `_singleflight.py`                | `SingleFlight` — the shared primitive a runtime cache goes through (see Caching) |
| `session.py`                      | One DuckDB session per thread, per store (#300): `cursor_for`, session hooks — not `api/session.py`'s identity map |
| `sql.py`                          | `SqlRelation` / `HasSource` — what splices into `is_in` and where it reads |
| `api/providers.py`                | `sl.Provider` — pre-configured endpoints, each with its REST API version |
| `formats/sdmx_ml31/`              | lxml-based XML reader/writer (3.1), bound onto the 3.0 implementation |
| `formats/sdmx_csv/`               | SDMX-CSV data reader/writer |

## Testing

```bash
just check           # lint + typecheck (inner dev loop)
just test            # unit tests
just test-integration  # cassette-replayed end-to-end tests (no network)
just ci              # lint + typecheck + unit + docs + integration
                     #   + the bundle tier's assertions (full gate)
just test-properties # property-based tests at 20x examples (Hypothesis `deep`)
just mutate          # mutation over the configured scope (hours — see justfile)
just mutate-module X # mutation over one module/function (minutes)
just mutate-scope    # what only_mutate covers, and how big
```

`just ci` includes `bench-bundle-assertions` — the real-bundle tests that
*assert a property*, without the eight that only report a number. It costs
~2 min on a machine holding the 2.6 GB bundle and skips in ~0 s on one that is
not, **saying so on its own line**. It is in the gate because #386 renamed
`LocalRegistry`'s `resolve_cache_size` to `resolve_cache_items` past a green
`just check` and 6,284 green unit tests, and only this tier failed, with 19
errors. The measurements stay opt-in as
`just bench-bundle`.

**`just ci` is the gate, and it runs locally.** Hosted CI runs no tests — only
`.github/workflows/docs.yml`, which publishes the docs to GitHub Pages. So
nothing merges on a green hosted run; it merges on a green `just ci`.

Running the gate locally is a hosting decision, and test design ignores
compute cost: Hypothesis explores at full width (the `ci` profile differs from
`dev` only by `derandomize`, for reproducibility), mutation testing runs
whatever scope is useful, and the suite grows as needed. If hosted compute is
bought later, restore the quality jobs from git history and keep all of the
above.

Coverage: use `--cov=sdmxlib`, not `--cov=sdmxlib.model.<module>`. The
module-scoped form fails to collect with the same `_duckdb` shadowing error
documented against `mutate_only_covered_lines` in `pyproject.toml`
(`No module named '_duckdb._sqltypes'`).

### Test style

- **Plain functions, not classes.** Use `def test_foo()` directly. Only use a
  class when tests genuinely share state (fixtures, setup/teardown, inherited
  behaviour).
- **`pytestmark` at module level** marks every test in a file without
  repetition:
  ```python
  pytestmark = pytest.mark.unit
  ```
- **`pytest.raises` blocks need no assignment.** `unused-call-result` is off
  in `pyrefly.toml`, so write:
  ```python
  with pytest.raises(ValueError):
      SomeClass.parse("bad input")
  ```
- **Generic Refs in tests** must be called on the parameterised class when
  the URN does not carry `T`, so the checker can bind it:
  ```python
  ref = Ref[object].unresolved(urn)   # ✓ T is bound
  ref = Ref.unresolved(urn)           # ✗ T is Unknown
  ```

### A guard has to stay able to fail

Write a guard so it can fail, and watch it fail before trusting it ("Ruff
warnings fail the gate too" below, and `tests/bench/test_bundle_reads.py`).

This section covers a guard that **was** correct, **was** watched to fail, and
later lost the ability: its assertion stayed the same while its *subject*
moved. #403 found four in one session, each by a different mechanism, and
three surfaced only because unrelated work forced a re-run.

**1. Assert the exact quantity when it is knowable.** `sites == len(_MARKERS)`
fails when its subject moves — it caught a guard reading a file that had become
a 49-line stub, at `0 == 30`. `sites >= 20` would not have. Use a bound only
when the true value legitimately varies — a discovery floor, a per-item budget
— and say so in the message. `test_cascade_dispatch`'s `<= 10` and
`test_field_converters`' `>= 30` both carry that reason next to them; a bare
`assert len(x) > 1` does not, which is why four of them were tightened in #403.

**2. A comparison needs a discrimination check.** If a test asserts two things
agree, assert separately that they are *capable* of disagreeing. `sdmx_ml30`
and `sdmx_ml31` were compared function by function for a year; once both became
41-line bindings onto a shared class the comparison was trivially satisfied and
said nothing. It now also asserts each shared class really carries the
implementation.

**3. A guard that starts at zero needs a sabotage test.** A guard that finds
nothing prints the same output whether the tree is clean or the guard is
broken. `tools/check_docstrings.py` parsed pydoclint's `stdout`, which
is empty — it reported a clean tree on a file pydoclint had just flagged, and
only a deliberate sabotage found it.

**4. Name the subject, not a proxy for it.** `len(again) > 20` stood in for
"the cascade ran". #144 made the cascade dispatch on type, the honest count
fell to 9, and the proxy failed — correctly, but by luck: a number above 20
would have kept it green while it measured something that no longer existed.

No tool checks these four rules: whether an assertion can still fail is
undecidable. Mutation testing comes closest; it is #324's subject and stays
out of `just ci` for its run time. Apply the rules in review.

### Differential properties and the oracles (the query language)

`query/_compile.py` and `query/_joins.py` turn a path expression into SQL and run
it in DuckDB. They are tested **differentially**: a pure-Python oracle evaluates
the same predicates directly over the artefact, and the property compares the
two. That is far stronger than asserting an expected result set, because the
expectation is derived independently rather than read off the implementation.

| File | Models |
| ---- | ------ |
| `tests/support/oracle.py` | code predicates, over `Code` objects |
| `tests/support/hierarchy_oracle.py` | placement predicates, over `Hierarchy` nodes |
| `tests/unit/test_properties_query_code.py` | the properties, over `q.code` |
| `tests/unit/test_properties_query_hcode.py` | the properties, over `q.hcode` |

Both oracles predate the compiler they now check. They were written as
*specifications* and share no code with any compiler, so the #306 migration
deleted the old DSLs and kept their coverage. The five recursive predicates
did not port: the path language has none by design, and
`test_the_recursive_predicates_are_absent_by_design` records that so the gap
does not read as missing coverage.

Four rules keep the oracles useful; each came from a defect.

**1. An oracle shares no code with the module it tests, and the two oracles
share no code with each other.** They re-derive depth and ancestry by walking
`parent_id` / the tree, never reading the `depth` / `ancestor_ids` columns the
storage writer bakes — so every differential property is *also* checking the
bake against a fresh traversal, the invariant `docs/design/caching.md` implies
but nothing else enforces. The two oracles restate rather than import their
lookalike parts, because two oracles that share a helper can be wrong the
same way, and differential testing cannot catch that.

**2. An oracle states what the library does, not what it should do.** Where the
behaviour is surprising, model it and cite the issue arguing about it. Both
oracles fold case for `contains`/`starts_with`/`ends_with` while `==` stays
case-sensitive (#222); both treat annotation predicates as existence-scoped
because the compiler reaches them through an inner JOIN. An oracle that smooths
over a surprise hides it; one that encodes it with an issue reference keeps the
question open.

**3. Every predicate kind gets a property, and the count is asserted.** Both
files carry a `test_every_predicate_kind_is_covered` that enumerates
`Predicate` subclasses off the module and compares against a hand-listed
covered set. A new predicate added without a property fails there rather than
going quietly untested.

**4. A strategy that leaves a field unset makes every predicate over it
vacuous.** `forest_hierarchies` populates name/level/code/annotations for this
reason, and draws annotation types from a two-element vocabulary so a generated
filter can actually hit a row — types drawn from `sdmx_ids` would never
collide, and the property would spend every example confirming that the empty
set equals the empty set. `codelist_with_hierarchy` goes further and makes node
ids *differ* from the code ids they reference, so a compiler assuming
`node_id == code_id` fails rather than passes. When adding a strategy for a
query property, measure that it discriminates before trusting the green.

**Limits of differential testing.** The four rules above make the oracles
trustworthy against each other. Both can still be wrong about something they
*both* believe. `is_in("CA")` silently meant
`is_in(["C", "A"])` and shipped in #261: the strategies only generate
well-formed input, so no example reached a bare string; both checkers correctly
allowed it, because `str` really does satisfy `Iterable[object]`; and had an
example reached it, `oracle.in_set("CA")` returned `['C', 'A']` too — the
property would have passed. Rule 1 reduces this risk, but the two shared
*Python's iteration semantics*, which rule 1 does not cover.

The mitigation is a third implementation outside the shared assumptions. For
these DSLs that means polars, SQLAlchemy, or the database itself — polars
rejects `is_in("CA")`, and the disagreement was obvious the moment the two APIs
were placed side by side. **When a DSL decision has a counterpart in polars or
SQLAlchemy, check it.** Several times now that comparison has changed the
answer, and once it found a defect nothing in the suite could have.

The standing guard is `tests/unit/test_query_misuse.py`: wrong calls must fail
with a message naming the fix, and a static inventory fails when a *new*
collection-typed parameter appears un-reviewed. Argument-shape errors belong
there, not in Hypothesis — the type is fixed at the call site, so a generator
would just be a parametrised list of bad literals.

**Run `just test-properties` when you touch the query language.** It is not in
`just ci` — 20× examples over these two files alone is ~7 minutes — and the
default width does not find everything. Both defects fixed in !249's second
commit were invisible at default width and fell out at 20×, including one found
via a filter fragment of `":"`, a needle made only of punctuation, which folds
to the empty string and matches every non-null label. No hand-written example
covered it.

**When a property fails, triage before editing either side:** *SQL bug* /
*oracle bug* / *underspecified DSL*. All three have happened. #222 (LIKE
metacharacters) and the `.any(depth=n)` bound that was parsed and then dropped
were SQL bugs. The two fixed in !249 were oracle bugs. #255 — one relation
folded accents and punctuation in label search and the other did not — is the
third kind, and exposed a question the DSL had never answered.

### SDMX-ML XSD validation

The official SDMX-ML 2.1 and 3.0 XSDs are vendored under `tests/schemas/`
(test-only — excluded from the wheel) and the writer round-trip tests validate
emitted XML against them via `tests/support/xsd.py`:

```python
from tests.support.xsd import assert_valid_sdmx_ml, compile_structure_schema

@functools.cache
def _schema() -> etree.XMLSchema:
    return compile_structure_schema("2.1")  # or "3.0"

def _roundtrip(name: str) -> tuple[StructureMessage, StructureMessage]:
    orig = _load(name)
    written = write(orig)
    assert_valid_sdmx_ml(written, _schema())   # catches conformance bugs
    return orig, parse(written)
```

XSD validation catches a class of writer bugs that pure round-trip can't: the
writer emits non-conformant XML that our own reader tolerates. Input fixtures
are validated too via `test_input_fixture_is_xsd_valid` so latent fixture bugs
don't mask writer bugs. See `tests/schemas/README.md` for the refresh procedure
when SDMX-TWG publishes a new schema tag.

## Dependencies

- Core: attrs (model), httpx (api), polars + pyarrow (data plane)
- Formats: lxml (XML), msgspec (SDMX-JSON)
- Storage: duckdb (local registry, DuckLake catalogs)

## SQL style

- **Quote style follows the prevailing Python SQL convention** (SQLAlchemy,
  psycopg, asyncpg, Django ORM):
  - Single-line SQL → regular double-quoted `"..."` / `f"..."`.
  - Multi-line SQL → triple-quoted `"""..."""` / `f"""..."""`.
  - The split is on line count, not on length. A 120-character one-liner stays
    in `"..."`; a 30-character two-liner uses `"""..."""`.
- Always use parameterized queries (`$name` style preferred) for values.
- Never use f-strings or concatenation to interpolate values into SQL.
  Identifier-only interpolation (table/view/alias names that came from a
  validated source like `_sql_identifier()`) is allowed.
- For dynamic WHERE clause composition, build a list of clause strings and join
  them; interpolate clause structure with f-strings but parameterize all values.

## Writing style

These rules cover all prose written for this repo: comments, docstrings,
documentation, CHANGELOG and `docs/upgrading.md` entries, commit messages,
issues, PRs and milestones. The section is shared across the StatCat-Suite
repos (GH-58); change it in all three or none.

Write in plain language. State claims directly, in the order the reader
needs them. Use subject-verb-object sentences, concrete nouns, and the
actual number, name, or mechanism instead of an allusion to it.

Follow these rules from Strunk & White:

- Put statements in positive form.
- Use definite, specific, concrete language.
- Omit needless words.

Avoid these constructions everywhere (sentences, headings, titles, lists):

- Contrast by negation: "not X, but Y", "it's not about X, it's about Y",
  "X isn't the problem; Y is". State Y. Mention X only if someone
  actually holds that view.
- Hook clauses that read like half a question: "what X doesn't tell you",
  "the part nobody mentions", "the evidence Y lacks", "why this matters".
  State the fact the clause points at.
- Colon reveals: "The real issue: ...", "Here's the thing: ...",
  "The short version: ...". Drop the setup and write the sentence.
- Punchline structure: ending a sentence or list on a twist, a dramatic
  final item, or a short fragment for emphasis.
- Rhetorical questions that the text then answers.
- Framing work or ideas by what is missing when you can name what is
  present or proposed.

Titles (milestones, issues, PRs, headings):

- Name the deliverable or scope as a plain noun phrase or imperative.
  A reader should know from the title alone what gets merged or decided.
- Use a colon only to separate a component name from the work item
  (e.g. "Query DSL: add DuckDB backend"), never to introduce a hook.

Examples:

```text
Bad:  Conformance evidence: what the green checks do not prove
Good: Conformance: add negative and edge-case test fixtures

Bad:  Query DSL: a second backend, and the evidence the first one lacks
Good: Query DSL: add second backend; add validation tests for SQL backend

Bad:  The green checks tell you less than you'd think.
Good: The green checks only cover valid inputs; there are no tests
      for malformed DSDs or constraint violations.

Bad:  The problem isn't performance — it's correctness.
Good: The cache returns stale codelists after a version bump.

Bad:  Here's what the benchmark doesn't show: cold-start latency.
Good: The benchmark measures warm queries only; cold-start latency
      is untested.
```

Before finishing, reread the output. Rewrite any sentence that delays its
point, contrasts with a claim nobody made, or alludes to a fact instead of
stating it.

## String style (non-SQL)

- For multi-line error messages, warnings, and other human-readable prose: use
  **implicit string concatenation** inside parentheses:
  ```python
  msg = (
      f"ComponentFilter has {n} ids but {m} dimensions — "
      "must be parallel arrays"
  )
  ```
- Do **not** use line-continuation backslashes inside `"""..."""` to fake
  single-line semantics — the `f"""\\\nfoo \\\nbar\\\n"""` pattern is banned.
  Either use the implicit-concat form above or accept the newlines.
- Triple-quoted is reserved for SQL and intentionally multi-line text (CSV/XML
  test fixtures, DDL).

## Docstring style

**Pure Google style. No RST/Sphinx markup, no NumPy sections.** No
`` :class:`X` ``, no `:param x:` field lists, no `Example::` literal blocks, no
underlined section headers. `Args:` / `Returns:` / `Raises:` with a colon, and
prose otherwise.

`just check-docstrings` enforces it — **`ruff.toml`'s `convention = "google"`
does not.** That setting checks a docstring's *structure* (summary line, blank
lines, punctuation) and never reads its markup or rejects a NumPy section. In
the sibling statcatlib project (then pinax) the same line sat declared and unenforced while 98 Sphinx roles and 13
NumPy-sectioned docstrings accumulated under it (#374).

Three arms, all at zero here: a grep for roles and literal blocks, a match for
a NumPy section *name followed by a rule*, and pydoclint's `DOC003` for a
docstring that is not Google-shaped without either — a `:param x:` field list
carries no role and no underline, so only the third arm sees it.

The second arm keys on the section name, never a bare rule, because three
module docstrings head **prose** with an RST rule (`formats/sdmx_json/_dto.py`,
`builders/metadata_annotation.py` twice) and are perfectly good Google
docstrings. `tools/check_docstrings.py` carries the reasoning;
`tests/unit/test_check_docstrings.py` asserts each arm catches its own
violation and that the prose headings stay legal.

## Protected files & suppressions

Two things are **never** changed as part of a normal coding loop. Both require
an explicit ask, and both are also enforced by `.claude/settings.json` — but the
rule stands on its own, agent or human.

**1. Checker configuration is protected.** `pyrefly.toml` and `ruff.toml` are
not edited without asking first. A checker gate exists so that it
doesn't move when the code makes it inconvenient. Loosening a rule to make a run
go green is the failure mode these files exist to prevent. Ask, explain what the
rule is catching and why it should change, and let the maintainer decide.

**2. Suppression comments require approval.** Adding `# noqa`,
`# type: ignore[...]`, `# pyright: ignore[...]`, `# pyrefly: ignore[...]`, or
ruff's block forms (`# ruff: disable[...]` / `# ruff: enable[...]` /
`# ruff: ignore[...]` / `# ruff: file-ignore[...]`, stabilised in ruff
0.15/0.16) needs the maintainer's approval. Default to fixing the underlying
defect. A suppression is only justified once the finding is _demonstrated_ to be
a tool bug — ideally with a minimal repro — and the maintainer has agreed. The
block forms deserve extra scepticism: they silence a _range_, so they age worse
than a line comment and hide findings nobody ever reads again.

**Write suppressions as `# pyrefly: ignore[kind]`**, not the generic
`# type: ignore[...]` — the bracket in the generic form is decorative, so it is
a blanket line suppression (see "Hygiene" below). Dead suppressions fail the
gate in both spellings: `unused-ignore` and `unused-type-ignore` are on in
`pyrefly.toml`. While two checkers ran, the shared form was also
*unattributable* — neither could tell a dead comment from one carrying the
other's error — which is why `# pyright: ignore[...]` existed here until
pyrefly became the only checker (#425).

**Checker versions carry upper bounds.** `uv.lock` is gitignored (published
library), so every fresh `uv sync` resolves the dev group anew — an unbounded
floor means a checker release lands in `just ci` with no commit of ours in
between. With ruff on `select = ["ALL"]` this has happened: 0.16.0 stabilised
12 preview rules. Bump `ruff` / `pyrefly` deliberately, in their own PR, with
the new findings fixed in the same change.

In the pyrefly 1.2 upgrade, all five full-preset
findings looked like tooling limitations and all five were real defects (an
`SdmxUrn[Any]` masking a covariance violation, a `hasattr` duck-test neither
checker could narrow, two `attrs.has` TypeGuards applied to discarded
`type(obj)` call expressions, and an invariant `Ref[A|B]` returned as
`Ref[A] | Ref[B]`). Every one was fixed in code. The single genuine tool bug —
`implicit-any-lambda` — was repro'd in isolation first, then turned off in
config with the repro recorded in a comment. Turning a rule off needs that
standard of evidence.

## Typing conventions (Any / object / generics)

Target Python 3.14+. **pyrefly is the only type checker** (#425) — `just
typecheck`, folded into `just check`, configured in `pyrefly.toml`. Until then
basedpyright was the source of truth and pyrefly a second opinion over `src/`;
they disagreed on enough of the typing spec (phantom-parameter variance,
overload resolution on an `Any` argument — #399) that every fix had to satisfy
both and suppressions were written twice.

**pyrefly covers `src`, all of `tests/`, and `bench`.** `tests/` was outside
every checker until #347 brought it under basedpyright, which is what let 59
dead suppressions accumulate there; that cost ~1,500 fixes and found real
defects each pass — a `serving()` signature narrower than what it forwarded to,
four `str` URNs the bundle tier caught, a metadata provision agreement pointing
at an MSD, a `FakeRelation` that never satisfied the protocol it claimed.
Moving it to pyrefly (#425) found 3,291 more, mostly Optional member access,
now narrowed with `tests/support/narrow.py:present()`. Two rules are
subtracted for `tests/**` (a `[[sub-config]]` in `pyrefly.toml`, measured and
annotated); everything else is on.

**Both linters run "everything on, then subtract, with a written reason."** ruff
at `select = ["ALL"]` + an annotated `ignore` list; pyrefly at
`preset = "all"` + an annotated `[errors]` deny-list. With that shape, a _new_
rule in the next release arrives as a visible finding. The subtractions are
measured: each deny-list entry carries the count that justified it.

pyrefly was previously scoped to `implicit-any` alone via `preset = "off"`,
because ≤1.1.x mismodeled attrs `field(converter=…)`. 1.2.0 fixed that. Going
from there to `preset = "all"` cost ~60 code fixes and no suppressions: 31
missing `@override`, 11 `Any` leaking out of a declared return type, 17
redundant `int()`/`bool()`/`str()` conversions (three of which were sanitising
casts standing in for bind parameters in SQL — now parameterised), a deprecated
`@contextmanager` return annotation, and an attribute declared inside
`__attrs_post_init__`. `implicit-any-lambda`, off as a 1.2.0 false positive
with no code-level fix, came back on with 1.4, which fixed it.

**pyrefly 1.4** added implicit-`Any` rules (`unknown-attribute-access`,
`unknown-return-type`) and `open-unpacking`, `uninitialized-instance-variable`
and `method-assign`. src/ and bench/ went to zero with one new suppression — a
pyrefly gap, `__attrs_post_init__` not counted as an initializer, repro beside
it. Three fixes changed the code's shape. Unpacked TypedDicts are closed (PEP 728, hence
`typing-extensions`); mixins state their fields as properties the attrs
subclasses override, not bare annotations (a protocol base made `isinstance`
misses 5x slower); and the query accessors' late edges are `_Late`
descriptors, so a declaration is its own binding. The new rules' tests/
findings are subtracted in `pyrefly.toml`, marked TEMPORARY with their counts,
and come off one rule at a time.

Keep `just lint` and `just typecheck` at 0 findings. Prefer fixing the code: of
the findings each strictness step has turned up so far, essentially all were
genuine defects.

**An annotation's names are imported for real.** On 3.14 an annotation is
evaluated when something asks (PEP 649), and `inspect.signature` asks: a name
imported only under `if TYPE_CHECKING:` raises `NameError` there, which broke
`inspect.signature(sl.RestRegistry)` and every Jupyter tooltip on it. `from
__future__ import annotations` is not the way out — it makes each annotation
a string `typing.get_type_hints` cannot resolve against a PEP 695 parameter.
So neither appears, but for a genuine import cycle, which says why beside it
(`model/base.py`'s `Agency`, and `local/data_store.py`'s `from __future__`
for `LocalRegistry`). `tests/unit/test_runtime_annotations.py`
evaluates every public signature.

**`object` by default, `Any` almost never.** `object` is inert — the checker
forces you to narrow before use. `Any` is a _viral hole_ — it propagates
unchecked through every operation it touches. Reach for `Any` only at genuine
gradual-typing boundaries (untyped third-party returns, raw deserialized
payloads) and **narrow immediately**.

- **The litmus:** use `object` unless the value flows into a position that needs
  the _erased type back out_. A bare param / `dict[str, Any]` value touches
  everything downstream → prefer `object`. A phantom type-arg on an
  otherwise-concrete type touches nothing until recovered.
- **`SdmxUrn[T]` specifically:** `SdmxUrn`'s `T` is a phantom, so `SdmxUrn[Any]`
  and `SdmxUrn[object]` give _identical_ body-checking. Use
  **`SdmxUrn[object]`** for pure-consumer params (URN only stringified / used as
  a str key — `replace`, `drop`, lookups). A URN parsed from text is
  `SdmxUrn[object]` too — `SdmxUrn.parse`, `parse_item` and `artefact_urn`
  (#399): it can name anything, so `get(SdmxUrn.parse(text))` answers
  `object | None` and the caller narrows. When the caller *knows* the type,
  recover it on the class rather than through `Any`:
  `Concept.parse_urn(text) -> SdmxUrn[Concept]`, so
  `Ref.unresolved(Concept.parse_urn(text))` is a `Ref[Concept]` — the readers'
  pattern. It was `parse() -> SdmxUrn[Any]` until #399, and that `Any` made
  `get(parse(text))` a confident wrong `LazyCodelist` under basedpyright and an
  unchecked `Any` under pyrefly. A class-as-witness classmethod (`ParsesUrn`)
  rather than a `kind: type[T]` parameter, because a witness the body never
  reads is an unused argument to ruff.
- **`dict[str, object]` over `dict[str, Any]`** for heterogeneous maps you'll
  narrow; localize the one unsafe `object -> T` step to the accessor (e.g. a
  `get[T]` return with an approved `# pyrefly: ignore[...]`). Prefer a **`TypedDict`**
  (with `ReadOnly` / `Required` / `NotRequired`) for known-shape payloads.

**Recover types, don't erase them.**

- PEP 695 generics (`def f[T](...)`, `class C[T: Bound]`) to thread the type
  through instead of dropping to `Any`.
- `type[T]` _witness_ params to recover a type at runtime (`SdmxUrn.make[U]`).
- **Protocols** (structural typing) for duck-typed params instead of `Any`
  (`SupportsUrn`, `Maintainable`). Bound a generic to a Protocol
  (`ItemList[T: SupportsId]`) rather than `getattr`/`cast`-ing inside.

**Narrow, don't cast.** Prefer `isinstance` / `TypeIs` (PEP 742) / `TypeGuard`.
`cast()` is an unchecked assertion — a lie the checker believes; last resort,
kept one line from a runtime guard. Verify with `assert_type` / `reveal_type` in
probes.

**Typed core, untyped shell.** The `Any` boundary is the format/storage layer
(msgspec untyped decode, lxml, duckdb rows): parse → validate → hand typed
domain objects inward. `Any` must not leak into `sdmxlib.model`. The model layer
stays pure and fully typed.

That boundary is **linted**: `ANN401` (no `Any`-annotated parameters) is on
**everywhere in `src/`**, with no per-directory exemptions. It formerly
exempted `formats/**`, `storage/**` and `local/**` on the reasoning that `Any`
is legitimate at the msgspec/lxml/duckdb boundary. #202/#203/#204 typed every
one of those parameters instead, mostly with Protocols that already existed (`SupportsUrn`, `SupportsId`,
`MaintainableArtefact`). Lifting the exemption then produced a single finding,
so a directory-wide ignore was covering exactly one justified site. `src/` now
carries **zero** `Any`-annotated parameters or locals, and an `Any` parameter
anywhere is a finding. The usual fixes, in order of preference: `object` plus an
`isinstance` narrowing; a `TypeIs` helper when the runtime probe doesn't narrow
on its own (`attrs.has(type(val))` is the classic — the TypeGuard lands on the
discarded `type(val)`, not on `val`); a generic that threads the element type
through (`_to_artefact_list[T]`); or, when the dynamic step is genuinely
unavoidable, one annotated local (`kwargs: dict[str, Any]`,
`cur: X | None = getattr(...)`) so the `Any` dies on that line instead of riding
out through the signature. Two places legitimately return `Any`, each with a
scoped `# noqa: ANN401` saying why: `Ref.__getattr__`, which proxies an
arbitrary attribute, and `storage.readers.get_artefact`, which dispatches on a
runtime artefact-type column so no static type describes its result
(`Registry.get` is the typed entry point built over it).

**`ANN401` and generic arguments.** It reads the annotation, not the type
inside it — so `def f(x: Any)` is a finding and
`def f(x: dict[str, Any])` is silent. An audit after %15 found **112 of 171**
occurrences were the second form, meaning the paragraph above was true of about
four sites and read as though it were true of all of them. No linter in the
stack has a rule for the generic-argument case, so `just check-any` carries a
**ratchet** instead: `tools/count_any.py` counts real uses (comments,
docstrings and imports excluded) and fails if the total moves off
`tools/any_baseline.txt` in either direction. Rising means a new one slipped
in; *falling* also fails, so a drop is recorded with `--write` and shows as a
lowered baseline in the diff. %16 walks the remaining count down by category.

**Ruff warnings fail the gate too** (`just lint`). Ruff exits 0 on a
`warning:` line, so two of them — a literal `# noqa` appearing inside a doc
comment, which ruff reads as a malformed directive — survived three milestones
of work. They survived because they were noisy and were filtered out of check
output as routine; a finding that does not fail the gate gets ignored. Revisit if ruff grows a native warnings-as-errors flag; 0.16 has
none.

Two more `Any` sources are clean and regression-guarded by pyrefly's `no-any-return-explicit`: a bare `msgspec.json.decode(b)` returns
`Any` (pass `type=...`), and `getattr(obj, name, None)` returns `Any` (annotate
the receiving local).

**Hygiene.** `@override` (PEP 698) on overrides — enforced by pyrefly's
`missing-override-decorator`; `Self` for fluent APIs; and **no `# type: ignore`
in any form** — always `# pyrefly: ignore[kind]`, so a _different_ error at
that line still surfaces.

`# type: ignore[code]` suppresses the whole line: no mypy runs here, so the
bracket is decorative. That is how one `# type: ignore[arg-type]` in
`api/client.py` came to be hiding 17 distinct errors (#196). `src/` now contains
**zero** of them (#195); `tests/` still carries 23, each live — a dead one fails
`unused-type-ignore`. Converting them is a suppression change and goes through
approval like any other.

## The public / private barrier (`_name`)

No linter enforces this — ruff's SLF001 measures something else (see rule 5) —
so this section is the only statement of it. It is written down because the code was
already broadly consistent while the _reason_ was unstated, which made every new
class a fresh judgement call.

The numbers behind it, measured in #207: the behavioural
layer is 77 % private attributes, the attrs domain model 1 %. Neither figure
alone is meaningful — the first counts only classes with a hand-written
`__init__`, which here are almost all infrastructure (connections, locks,
caches, facades, builders) where private state is correct; the second counts a
representation of the SDMX specification, where the spec sets which fields are
public.

**1. Domain-_artefact_ fields are public.** The attrs classes representing SDMX
artefacts take their surface from the spec — `Codelist.id` is public because
SDMX says a codelist has an id. Their
private fields are _derived_ state the class maintains and invalidates itself:
`_flat`, `_parent_of`, `_codes`, `_str_cache`.

This keys on the **class, not the directory** — an audit found two correct
exceptions living under `model/`: `Ref._resolved` is resolution state, and
`rollup.AncestorMap` is a behavioural class holding a DuckDB connection.
Rule 2 governs those, as it does everything under `query/`. A domain artefact
follows rule 1 and machinery follows rule 2, wherever it lives. A new private field on an _artefact_ should be a
cache or an index, or it probably belongs on a different class.

**2. In behavioural classes, `_name` means invariant-bearing.** Use it when you
can name what breaks if a caller assigns it: a connection's lifecycle, a lock's
discipline, a cache's coherence, a fluent builder's immutability, a `Ref`'s
canonicalisation. "It's internal" is not a reason on its own — a stashed
constructor argument a caller might reasonably want to read is a candidate for
rule 3 instead. If you cannot finish the sentence "assigning this from outside
would break …", do not underscore it.

**3. If callers need the value, expose a property and keep the field private.**
`storage/lazy.py` is the pattern: eleven `_id` / `_name` / `_version` fields
behind eleven documented public properties. The underscore then marks storage
and the property carries the contract — which is also why an attribute counted
as "private" is often not the same thing as "not publicly reachable".

**4. Package-internal APIs get a plain name in a private module.** Python has no
`friend`, and `_name` conflates "not for other classes" with "not for other
packages". The ecosystem answer is a private _module_ holding plainly-named
functions — pydantic's `_internal/` mixes `collect_model_fields` (package API)
with `_check_protected_namespaces` (module-internal). Prefer that over either a
public method (which advertises the call on every instance) or a scatter of line
suppressions. Note `__all__` cannot help here: it governs module-level names, so
a method on an exported class is reachable regardless.

**5. SLF001 is off, deliberately.** It cannot see package boundaries, so
intra-package access reads as a violation at every line. Every comparable
project does that access routinely and none runs the rule — sqlalchemy ~3800
times, polars ~1019, rich ~163, pydantic ~142. Of our 40 suppressions, 28 were a
ruff type-inference gap on `cls.__new__(cls)` locals (fixed in !198 with a
`Self` annotation, not a suppression) and the remaining 12 were inspected
individually: **zero were defects**. That measured 0 % true-positive rate is the
written reason the `select = ["ALL"]` policy requires for switching a rule off.

Module-level privacy is deliberately **not** part of this rule. Three
conventions are live among top projects — httpx (every module `_`-prefixed),
pydantic/fastapi (one `_internal/` package), sqlalchemy/starlette (none) — so
there is nothing to converge on, and it would not affect SLF001 either way:
httpx still has cross-object private access inside its own private modules.
sdmxlib sits with sqlalchemy/starlette; leave it there.
