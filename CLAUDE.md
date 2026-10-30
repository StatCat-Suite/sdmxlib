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

- Baseline runtime deps: `attrs`, `httpx2`, `lxml`, `duckdb`, `polars`,
  `pyarrow`, `msgspec`, `google-crc32c` (the manifest's CRC32C, GH-181),
  `typing-extensions` (PEP 728 closed TypedDicts, until `typing` has them in
  3.15). All required — none are optional extras. Any module may
  import them. `msgspec` is used everywhere we touch JSON (SDMX-JSON
  reader/writer/metadata/data): typed `msgspec.Struct` DTOs encoded via the
  shared `_dto.ENCODER`, typed `msgspec.convert` / `msgspec.json.decode` on
  read. Neither orjson nor stdlib `json` appears in
  `src/sdmxlib/formats/sdmx_json/`.
- All domain objects in `sdmxlib.model` are attrs `@frozen` (#492); an
  artefact or item is `@frozen(unsafe_hash=False)` — equal by value, not
  hashable (key by `urn`). A change is an `attrs.evolve` copy; derived state
  set in `__attrs_post_init__` goes through `object.__setattr__`. Domain types
  stay pure data + behaviour over that data — no XML/JSON awareness, no HTTP,
  no I/O side effects. `model/rollup.py`'s `AncestorMap`, which queries
  DuckDB, is the one behavioural class in `model/`.
- `Ref[T]` for every outgoing URN edge. None = "doesn't exist."
- Registry presents the remote graph as local attribute access.
- Format layer in `sdmxlib/formats/` owns the SDMX wire formats' parsing and
  serialisation — model imports nothing from `formats/`. Bundle and manifest
  JSON is read and written in `storage/` and `edition/`.
- `sdmxlib/polars.py` is the user-facing Polars bridge (DataFrame construction,
  label maps, etc.); the structural query language (`sdmxlib/query/`) uses
  duckdb internally for execution and returns polars frames from `select()`.

## Caching & optimization passes (read before adding a cache)

Before adding or extending **any** cache during a performance pass, consult
`docs/design/caching.md` — the caching decision framework, and the list of the
runtime caches that exist today. It exists so we stop accreting ad-hoc caches
that paper over query/layout problems. Non-negotiable summary:

- **Measure before you cache.** The trigger is a profile showing the
  _computation_ is expensive and reused — not a load test showing a slow
  _symptom_. If you can't name the slow line, you don't cache (the #155 case
  study in `caching.md`: a "58 s label map" that measured 8 ms).
- **Decision hierarchy, in order:** (1) profile it, and stop if it is fast.
  (2) If physical layout at build time fixes it, fix that. (3) If the input is
  immutable within an edition or artefact version, **bake it** into the edition
  (like `code.depth` / `code.ancestor_ids`) instead of caching at runtime.
  (4) Only if the cost is genuinely per-process, add a runtime cache.
- **A new runtime cache goes through `SingleFlight`** (`_singleflight.py`):
  single-flight (not just a lock), bounded, invalidation tied to a lifecycle,
  placed at the _source_ of the cost — never a fresh module global. Contract:
  _concurrent reads are safe and do not duplicate work._ Several existing
  caches predate the primitive; `caching.md` lists them.
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
bare `#N` (an issue), `!N` (a merge request) or `%N` (a milestone). Those keep
their GitLab numbers and are not rewritten. **Read them in
`history/gitlab/`**: `#492` is `history/gitlab/issues/492.md`, `!600` is
`merge_requests/600.md`, `%15` is `milestones/15.md`, exported with their
comments so no one needs GitLab (GH-85). Text that described StatCan-internal
systems was withheld from the export; its README says how much.

**Write a GitHub issue or PR as `GH-N`**, never a bare `#N`: GitHub numbers
from 1, so a bare number would collide with the GitLab one the tree already
means. GitHub links `GH-N` by itself. The 31 issues still open at the move were
re-created on GitHub, each carrying a "Moved from GitLab #N" line.

**Living text states current behaviour** (GH-85). Comments, docstrings and
the docs say what the code does now; history belongs in git, the CHANGELOG
and the issue. Keep a reference only where the reader needs the issue: an
open limitation or follow-up, a measurement or decision recorded there, or a
test pinning a regression. In user docs (`docs/` outside `docs/design/`) and
in public docstrings, which render in the API reference, either link the
issue in full or leave it out: a GitHub issue as
`[GH-19](https://github.com/StatCat-Suite/sdmxlib/issues/19)`, a GitLab one
through the export, as
`[#422](https://github.com/StatCat-Suite/sdmxlib/blob/main/history/gitlab/issues/422.md)`. The CHANGELOG and released history are left as written.

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

Nothing is signed with Claude: commit messages carry no `Co-Authored-By`
trailer, and PR descriptions, issues and comments carry no "Generated with
Claude Code" footer. The README's note discloses AI use once for the project
(a StatCat-Suite convention, GH-156).

## Related repos

The two other StatCat-Suite libraries build on this one, so a breaking change
here lands there. Each has its own maintainers and agents: from here they are
read-only, and a change they need is an issue on their tracker
(`StatCat-Suite/<repo>#N`).

- `../statcatlib` — the catalog of products and its Inventory writer. Seals through
  `sdmxlib.edition` (`seal(extensions=...)`, wrapping
  `check_edition`), injects a `SqlRelation` into the path language, and reads
  `storage.schema.SCHEMA_VERSION`.
- `../sdmxlib-rest` — an SDMX-REST server over a `LocalRegistry`: the model
  artefacts, `DataflowBinding`, `obs()` and `ObsQuery`.

Both pin sdmxlib by range in their `pyproject.toml`. Before a breaking release,
`git grep` the name in both checkouts and write the move in
`docs/upgrading.md`.

## Codebase map

Each code directory carries an `EGM.md` contract: what it offers, the
invariants it keeps, its side effects, and how to verify it. The root
[`EGM.md`](EGM.md) is the index; `src/sdmxlib/EGM.md` maps the package and
links each subpackage's contract. Read the contract of the directory you are
changing before you change it, and update it in the same change when the
interface, an invariant or a side effect moves.

Where to start:

- `src/sdmxlib/__init__.py` — the public API (`sl.*`); a new public name is
  exported here (`tests/unit/test_public_surface.py`).
- `model/` — the frozen SDMX information model, `SdmxUrn`, `Ref[T]`.
- `formats/` — every SDMX wire format; `formats/gaps.py` is what each cannot
  carry.
- `storage/` and `local/` — the DuckDB store, `EditionRegistry` /
  `LocalRegistry`; `edition/` seals and checks an edition (`docs/edition.md`).
- `query/` — the `sl.q` path language and `ObsQuery`.
- `api/` — `RestRegistry`, `FederatedRegistry`, `Provider`.

Design documents are in `docs/design/`: current designs at the top level,
dated measurement and decision records in `docs/design/evidence/`, and EGM
reviews and drift reports in `docs/design/egm/`. Superseded designs and the
exported GitLab tracker are in `history/`.

EGM artifact directory: docs/design/egm

## Testing

```bash
just check           # lint + typecheck + docstrings, prose, Any, indexes, workflows (inner dev loop)
just test            # unit tests
just test-integration  # cassette-replayed end-to-end tests (no network)
just ci-fast         # lint + typecheck + unit + docs + integration + docs build
just ci              # ci-fast + audit + the bundle and object-store tiers (full gate)
just test-properties # property-based tests at 20x examples (Hypothesis `deep`)
just mutate          # mutation over the configured scope (hours — see justfile)
just mutate-module X # mutation over one module/function (minutes)
just mutate-scope    # what only_mutate covers, and how big
```

`just ci` includes `bench-bundle-assertions` — the real-bundle tests that
*assert a property*, without the ones that only report a number and without
the `recompute` ones, which redo publishing's work (`just bench-bundle` runs
those). It reads the sealed 2021 Census Profile edition
StatCat-Suite/cpt-builder builds (`census-2021-cpt`, 2.0 GB) in place,
read-only, from the edition directory `$SDMXLIB_BENCH_BUNDLE` names. It costs ~2 min
on a machine holding that edition and skips in ~0 s on one that is not,
**saying so on its own line**. DuckDB in this tier runs on
`SDMXLIB_BENCH_THREADS` threads (default 4). It is in the gate because it is
the only tier that exercises `LocalRegistry`'s public API against a real
edition: #386 renamed a constructor parameter past a green `just check` and the
whole unit tier, and only this tier failed.

**`just ci` is the gate, and it runs locally.** Hosted CI runs no tests.
`main` requires the hosted `check` status, up to date with `main`, and no
other hosted job blocks a merge:

- `.github/workflows/check.yml` runs `just check` on every pull request and
  push to `main`, resolving the dependencies fresh since `uv.lock` is
  gitignored (GH-236).
- `.github/workflows/docs.yml` publishes the docs to GitHub Pages.
- `.github/workflows/release.yml` publishes a release to PyPI when
  `just release-4-merge` publishes its GitHub release (GH-238). It builds from
  the tag, waits for the maintainer to approve the `pypi` environment, uploads
  with trusted publishing and attestations, and attaches the files and their
  Sigstore signatures to the release. No PyPI token is stored.
- `.github/workflows/fuzz.yml` fuzzes the readers with Atheris, weekly and
  on pull requests that change a reader: `sl.parse`'s (GH-241), and the data
  readers behind `scan_json` and `scan_csv` (GH-247). The
  targets are in `tests/fuzz/targets.py`; a crash input it finds is saved
  under `tests/fuzz/crashes/<target>/` with its fix, and
  `tests/unit/test_fuzz_crashes.py` replays it. Atheris installs on Linux
  x86_64 only, so fuzzing itself never runs locally.
- The security checks report to the Security tab (GH-220): `zizmor.yml` lints
  the workflows, `scorecard.yml` scores the repository, and GitHub runs
  CodeQL, secret scanning and Dependabot from the repository settings.

So a merge waits on a green `just ci`, which also runs `just audit` (pip-audit
over the local lock) and, through `just check`, `just lint-workflows`
(zizmor, offline).

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
warnings fail the gate too" below, and `tests/bench/test_bundle_reads.py`). A
guard that was correct and watched to fail can still lose the ability later:
its assertion stays the same while its *subject* moves (#403 found four, each
by a different mechanism).

**1. Assert the exact quantity when it is knowable.** `sites == len(_MARKERS)`
fails when its subject moves; `sites >= 20` does not, and kept a guard green
while the file it read became a 49-line stub. Use a bound only when the true
value legitimately varies — a discovery floor, a per-item budget — and say so
in the message, as `test_cascade_dispatch`'s `<= 10` and
`test_field_converters`' `>= 30` do. A bare `assert len(x) > 1` is a defect.

**2. A comparison needs a discrimination check.** If a test asserts two things
agree, assert separately that they are *capable* of disagreeing. Once
`sdmx_ml30` and `sdmx_ml31` became bindings onto one shared class, comparing
them function by function was trivially satisfied; the test also asserts each
shared class really carries the implementation.

**3. A guard that starts at zero needs a sabotage test.** A guard that finds
nothing prints the same output whether the tree is clean or the guard is
broken. Feed it a violation and assert it reports one, as
`tests/unit/test_check_docstrings.py` does for each arm of
`tools/check_docstrings.py`.

**4. Name the subject, not a proxy for it.** Assert the thing itself ("the
cascade ran") rather than a number that happened to accompany it
(`len(again) > 20`): when the implementation changes, a proxy can stay green
while measuring something that no longer exists.

No tool checks these four rules: whether an assertion can still fail is
undecidable. Mutation testing comes closest (`docs/design/mutation-testing.md`)
and stays out of `just ci` for its run time. Apply the rules in review.

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

The oracles are *specifications*: they share no code with any compiler. The
path language has no recursive predicates by design, and
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

**Limits of differential testing.** The four rules make the oracles
trustworthy against each other. Both can still be wrong about something they
*both* believe: `is_in("CA")` silently meant `is_in(["C", "A"])` (#261),
because the strategies generate only well-formed input, `str` satisfies
`Iterable[object]` for both checkers, and the oracle iterated the string the
same way. The two shared *Python's iteration semantics*, which rule 1 does not
cover.

The mitigation is a third implementation outside the shared assumptions. For
these DSLs that means polars, SQLAlchemy, or the database itself — polars
rejects `is_in("CA")`. **When a DSL decision has a counterpart in polars or
SQLAlchemy, check it.**

The standing guard is `tests/unit/test_query_misuse.py`: wrong calls must fail
with a message naming the fix, and a static inventory fails when a *new*
collection-typed parameter appears un-reviewed. Argument-shape errors belong
there, not in Hypothesis — the type is fixed at the call site, so a generator
would just be a parametrised list of bad literals.

**Run `just test-properties` when you touch the query language.** It is not in
`just ci` — 20× examples over these two files alone is ~7 minutes — and the
default width does not find everything: inputs such as a filter fragment made
only of punctuation (`":"`, which folds to the empty string and matches every
non-null label) fall out only at 20×.

**When a property fails, triage before editing either side:** *SQL bug* /
*oracle bug* / *underspecified DSL*. All three have happened: #222 (LIKE
metacharacters) was an SQL bug, the two fixed in !249 were oracle bugs, and
#255 (one relation folded accents in label search and the other did not)
exposed a question the DSL had never answered.

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

- Core: attrs (model), httpx2 (api), polars + pyarrow (data plane)
- Formats: lxml (XML), msgspec (SDMX-JSON)
- Storage: duckdb (local registry, DuckLake catalogs)

Install instructions (README, docs, CLI and error messages) say `uv add <pkg>`
first and `pip install <pkg>` second, as the fallback, and quote extras:
`uv add 'pkg[extra]'`. The CLI is a tool: `uv tool install sdmxlib-cli`
first, `pip install sdmxlib-cli` second.

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
lines, punctuation) and never reads its markup or rejects a NumPy section.

Three arms, all at zero here: a grep for roles and literal blocks, a match for
a NumPy section *name followed by a rule*, and pydoclint's `DOC003` for a
docstring that is not Google-shaped without either — a `:param x:` field list
carries no role and no underline, so only the third arm sees it.

The second arm keys on the section name, never a bare rule, because some
module docstrings head **prose** with an RST rule (`formats/sdmx_json/_dto.py`,
`builders/metadata_annotation.py`) and are good Google docstrings.
`tools/check_docstrings.py` carries the reasoning;
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
`# ruff: ignore[...]` / `# ruff: file-ignore[...]`) needs the maintainer's
approval. Default to fixing the underlying defect. A suppression is only
justified once the finding is _demonstrated_ to be a tool bug — with a minimal
repro, recorded beside it — and the maintainer has agreed. The block forms
deserve extra scepticism: they silence a _range_, so they age worse than a
line comment and hide findings nobody ever reads again. Turning a rule off in
config needs the same standard of evidence. Findings that look like tooling
limitations have nearly always been real defects.

**Write suppressions as `# pyrefly: ignore[kind]`**, not the generic
`# type: ignore[...]` — the bracket in the generic form is decorative, so it is
a blanket line suppression (see "Hygiene" below). Dead suppressions fail the
gate in both spellings: `unused-ignore` and `unused-type-ignore` are on in
`pyrefly.toml`.

**Checker versions carry upper bounds.** `uv.lock` is gitignored (published
library), so every fresh `uv sync` resolves the dev group anew — an unbounded
floor means a checker release lands in `just ci` with no commit of ours in
between (ruff 0.16.0 stabilised 12 preview rules that way). Bump `ruff` /
`pyrefly` deliberately, in their own PR, with the new findings fixed in the
same change.

## Typing conventions (Any / object / generics)

Target Python 3.14+. **pyrefly is the only type checker** (#425) — `just
typecheck`, folded into `just check`, configured in `pyrefly.toml`.

**pyrefly covers `src`, all of `tests/`, and `bench`.** In `tests/`, narrow an
Optional with `tests/support/narrow.py:present()`. Two rules are subtracted for
`tests/**` (a `[[sub-config]]` in `pyrefly.toml`, measured and annotated), and
the rules pyrefly 1.4 added are subtracted for `tests/` too, marked TEMPORARY
with their counts, to come off one rule at a time; everything else is on.

**Both linters run "everything on, then subtract, with a written reason."** ruff
at `select = ["ALL"]` + an annotated `ignore` list; pyrefly at
`preset = "all"` + an annotated `[errors]` deny-list. With that shape, a _new_
rule in the next release arrives as a visible finding. The subtractions are
measured: each deny-list entry carries the count that justified it.

Keep `just lint` and `just typecheck` at 0 findings. Prefer fixing the code:
the findings each strictness step has turned up have essentially all been
genuine defects. The shapes those fixes took are the house style: closed
TypedDicts for unpacked keyword arguments (PEP 728, hence
`typing-extensions`); mixins state their fields as properties the attrs
subclasses override, not bare annotations; and the query accessors' late
edges are `_Late` descriptors, so a declaration is its own binding.

**An annotation's names are imported for real.** On 3.14 an annotation is
evaluated when something asks (PEP 649), and `inspect.signature` asks: a name
imported only under `if TYPE_CHECKING:` raises `NameError` there, which breaks
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
  pattern (`docs/design/phantom-generics.md`). A class-as-witness classmethod
  (`ParsesUrn`) rather than a `kind: type[T]` parameter, because a witness the
  body never reads is an unused argument to ruff.
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
**everywhere in `src/`**, with no per-directory exemptions, and `src/` carries
**zero** `Any`-annotated parameters or locals. The usual fixes, in order of
preference: `object` plus an `isinstance` narrowing; a `TypeIs` helper when the
runtime probe doesn't narrow on its own (`attrs.has(type(val))` is the classic
— the TypeGuard lands on the discarded `type(val)`, not on `val`); a generic
that threads the element type through (`_to_artefact_list[T]`); or, when the
dynamic step is genuinely unavoidable, one annotated local
(`kwargs: dict[str, Any]`, `cur: X | None = getattr(...)`) so the `Any` dies on
that line instead of riding out through the signature. Two places legitimately
return `Any`, each with a scoped `# noqa: ANN401` saying why: `Ref.__getattr__`,
which proxies an arbitrary attribute, and `storage.readers.get_artefact`, which
dispatches on a runtime artefact-type column so no static type describes its
result (`Registry.get` is the typed entry point built over it).

**`ANN401` and generic arguments.** It reads the annotation, not the type
inside it — so `def f(x: Any)` is a finding and `def f(x: dict[str, Any])` is
silent. No linter in the stack has a rule for the generic-argument case, so
`just check-any` carries a **ratchet** instead: `tools/count_any.py` counts
real uses (comments, docstrings and imports excluded) and fails if the total
moves off `tools/any_baseline.txt` in either direction. Rising means a new one
slipped in; *falling* also fails, so a drop is recorded with `--write` and
shows as a lowered baseline in the diff.

**Ruff warnings fail the gate too** (`just lint`). Ruff exits 0 on a
`warning:` line (a literal `# noqa` inside a doc comment, which it reads as a
malformed directive, is one), and a finding that does not fail the gate gets
ignored. Revisit if ruff grows a native warnings-as-errors flag.

Two more `Any` sources are regression-guarded by pyrefly's
`no-any-return-explicit`: a bare `msgspec.json.decode(b)` returns `Any` (pass
`type=...`), and `getattr(obj, name, None)` returns `Any` (annotate the
receiving local).

**Hygiene.** `@override` (PEP 698) on overrides — enforced by pyrefly's
`missing-override-decorator`; `Self` for fluent APIs; and **no `# type: ignore`
in any form** — always `# pyrefly: ignore[kind]`, so a _different_ error at
that line still surfaces. `# type: ignore[code]` suppresses the whole line: no
mypy runs here, so the bracket is decorative, and one such comment has hidden
17 distinct errors (#196). `src/` contains none; `tests/` still carries 23,
each live — a dead one fails `unused-type-ignore`. Converting them is a
suppression change and goes through approval like any other.

## The public / private barrier (`_name`)

No linter enforces this — ruff's SLF001 measures something else (see rule 5) —
so this section is the only statement of it.

**1. Domain-_artefact_ fields are public.** The attrs classes representing SDMX
artefacts take their surface from the spec — `Codelist.id` is public because
SDMX says a codelist has an id. Their private fields are _derived_ state the
class maintains and invalidates itself: `_flat`, `_parent_of`, `_codes`,
`_str_cache`.

This keys on the **class, not the directory**: under `model/`, `Ref._resolved`
is resolution state, and `rollup.AncestorMap` is a behavioural class holding a
DuckDB connection. Rule 2 governs those, as it does everything under `query/`.
A domain artefact follows rule 1 and machinery follows rule 2, wherever it
lives. A new private field on an _artefact_ should be a cache or an index, or
it probably belongs on a different class.

**2. In behavioural classes, `_name` means invariant-bearing.** Use it when you
can name what breaks if a caller assigns it: a connection's lifecycle, a lock's
discipline, a cache's coherence, a fluent builder's immutability, a `Ref`'s
canonicalisation. "It's internal" is not a reason on its own — a stashed
constructor argument a caller might reasonably want to read is a candidate for
rule 3 instead. If you cannot finish the sentence "assigning this from outside
would break …", do not underscore it.

**3. If callers need the value, expose a property and keep the field private.**
`storage/lazy.py` is the pattern: its `_id` / `_name` / `_version` fields sit
behind documented public properties. The underscore then marks storage and the
property carries the contract — which is also why an attribute counted as
"private" is often not the same thing as "not publicly reachable".

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
project does that access routinely and none runs the rule. Of the suppressions
it once needed here, none marked a defect (#207): that measured 0 %
true-positive rate is the written reason the `select = ["ALL"]` policy
requires for switching a rule off.

Module-level privacy is deliberately **not** part of this rule. Top projects
follow three conventions — httpx (every module `_`-prefixed), pydantic/fastapi
(one `_internal/` package), sqlalchemy/starlette (none) — so there is nothing
to converge on. sdmxlib sits with sqlalchemy/starlette; leave it there.
