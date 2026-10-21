# What the mutation runs actually measured

`just mutate` produces a number. This file is what the numbers *mean*, because
twice now a mutation score has been quoted here that measured something other
than what it appeared to.

## The #218 question, answered

> Do property-based tests kill more mutants than example-based ones? Same
> module, same mutants, different suite.

Run with `tools/mutate_compare.py` over `src/sdmxlib/query/_compile.py`
(1,290 mutants), one arm the two oracle-backed property files, the other eight
example files covering the same predicate surface.

Restricted to the **742 mutants both arms execute** — the only fair comparison,
since each suite reaches code the other never runs:

| | killed only by it | killed by both | killed by neither |
| --- | --- | --- | --- |
| properties | **0** | 440 | 196 |
| examples | **106** | | |

Over all 1,290, including what each suite never reaches: properties killed
440, examples 755, and 535 survived both.

**The properties killed a strict subset.** Not "fewer" — a subset: every
mutant they caught, the examples caught too, and the examples caught 106 more.
The 106 cluster in `_constraint_clause`, `_facet_clause` and `_conjoin`.

### What that does and does not license

It does **not** say the property files are redundant, and they are not being
deleted. Three reasons, and the first is the one that matters:

**Mutation kills and found defects are different measurements.** The properties
have caught real bugs no example did — a filter fragment of `":"`, punctuation
that folds to the empty string and matches every non-null label; `#255`, where
one relation folded accents in label search and the other did not. Neither is a
small local fault of the kind mutmut injects. Mutmut perturbs an operator or a
constant; the defects the oracles find are *disagreements about meaning*, which
is a different failure class and the reason `tests/support/oracle.py` is
written as a specification rather than a second implementation.

**They ran at 1× width.** mutmut runs the default Hypothesis profile. The
documented finds came from `just test-properties` at 20×, and CLAUDE.md already
records that both defects fixed in !249's second commit were invisible at
default width. So this measures the properties with most of their search
switched off. Re-running the properties arm under the `deep` profile is the
obvious follow-up and was not done here: it multiplies an already 15-minute run
and pushes more mutants past their CPU budget (see below).

**A property is worth what it *specifies*, not only what it kills.** The
oracles are the executable statement of what a predicate means. That value is
in the reading as much as the running.

### The honest caveats on these numbers

* **Run-to-run variance.** Two runs of the properties arm gave 454 kills / 0
  timeouts and 440 kills / 13 timeouts. Hypothesis tests are slow enough that
  the per-mutant CPU budget sometimes expires, so the properties' kill count is
  a slight *under*-estimate — which, note, cuts in their favour and does not
  change the qualitative result.
* `mutate_only_covered_lines = false` is forced off by the `_duckdb` import
  interaction documented in `pyproject.toml`, so uncovered lines yield
  uninteresting survivors.
* `pytest_add_cli_args = ["-m", "unit and not slow"]` excludes the concurrency
  tests.
* The "examples" arm is eight files against the properties' two. It was not
  balanced for size, because the question is what each suite *can* catch, not
  what it catches per file.

## The scope, and why it is what it is

#304 asked for `code_query.py`, `hierarchy_query.py` and `_query_common.py`
(1,402 statements). #306 deleted all three. The work moved into `_expr.py`,
`_compile.py`, `_joins.py` and `_common.py` — 1,332 statements, so the scope
survived the migration nearly intact under different names, and those four are
now in `only_mutate` (2,300 statements total).

`accessors.py` (533), `query.py` (399) and `_pushdown.py` (221) are held back
deliberately: the first is declarative surface, and the other two deserve their
own decision rather than arriving as a side effect of this one.

## The `_compile.py` baseline, and the first triage installment

Against the **whole** unit suite — the honest baseline, and the one to quote:

| | mutants | killed | survived | timeout | no tests |
| --- | --- | --- | --- | --- | --- |
| before the triage | 1,290 | 1,127 (87.4%) | 163 | 0 | 0 |
| after | 1,290 | 1,147 (88.9%) | 143 | 0 | 0 |

Note what the earlier two-arm comparison could *not* tell you: it reported 196
mutants neither arm killed, but 33 of those die to test files in neither arm.
A survivor set is only meaningful against the suite you actually ship.

### What the first cluster found

`_window` held 23 survivors, the largest group. Triaging them found **a real
defect, not a test gap** — which is the outcome #218 hoped for and did not
promise.

`col.count().over(...)` compiled to `count(*)` for every windowed count. The
counted column was dropped from the SQL **while its bind parameters were still
emitted**, so `description["en"].count().over(...)` died with `Parameter
argument/count mismatch`, and counting any nullable column silently included
its NULLs. The justification in the comment — that the fan-out marker is not a
real column — did not hold: the marker's path is to-many by construction, and
`_flat_reference` refuses that before a window is ever built, so the marker
cannot reach `_window` at all.

It survived because the one test counted `id`, where `count(*)` and
`count(id)` agree.

Four tests and the fix took `_window` from 23 survivors to 5, and 18 of the 20
mutants newly killed across the module were in it. The other three windowed
forms had no test at all: `n_unique().over()` (the `DISTINCT` argument *and*
the rename to `count`, since DuckDB has no `n_unique`), `list().over()` (whose
exclusion from the frame's `ORDER BY` is what keeps it from becoming a running
list), and `lead(offset)` — indistinguishable from plain `lead` at offset 1,
which is all that was tested.

### The five that remain, classified

* **`"XXcountXX"` / `"COUNT"` in the `count` branch** — *equivalent, and the
  branch is now gone.* Once corrected, that branch said exactly what the
  `else` said. Two mutants surviving on the strength of being unable to tell
  a branch from its fall-through is a duplicate branch; deleting it is the fix.
* **`{"n_unique": "COUNT"}`** — *equivalent.* SQL function names are
  case-insensitive, so the rename lands on the same function.
* **`' DESC'` → `' desc'`** — *equivalent*, same reason.
* **`else source[1]` in the sort fallback** — *dead code, not yet removed.*
  `window()` supplies `order_ref` whenever `column.source` is non-empty, and
  an empty source cannot construct a `Win`. Removing the fallback means
  tightening the parameter to non-optional and moving the caller's guard,
  which is a bigger change than this triage should smuggle in.

## Installment 2: the two discriminator clauses, and a broken runner

### The runner had been dead, and nothing said so

Before a single survivor could be read, the run had to work. It did not:

    failed to collect stats. runner returned 1
    1610 mutants   killed 0   survived 0   no-coverage 0

`test_data_format.test_the_cookbook_table_lists_every_member` reads
`docs/cookbook/data.md` through `Path(__file__).parents[2]`. mutmut copies
`src/`, `tests/`, `pyproject.toml` and `uv.lock` into `mutants/` and nothing
else, so that read raised `FileNotFoundError` there and took the **stats
phase** down with it — and mutation testing is not in `just ci`, so every run
since that test landed reported zero of everything and no one saw it.

Exactly the trap `test_count_any.py` documents against #284, one directory
over: *"`tools/` is outside the trees mutmut copies … which is exactly what
#284 did, unnoticed."* Two instances now, both found only when someone next
tried to run mutation. A third is a matter of time; the shape to watch for is
a test reading anything outside `src/` and `tests/`.

The guard is keyed on `docs/` being absent **entirely**, not on the file
missing, so a deleted cookbook page still fails in a real tree rather than
skipping quietly.

### The new baseline

`_compile.py` has grown since installment 1 — 1,290 mutants then, 1,610 now —
so the numbers are not comparable to the table above, only to each other.

| | mutants | killed | survived | segfault | timeout |
| --- | --- | --- | --- | --- | --- |
| before installment 2 | 1,610 | 1,408 (89%) | 174 | 28 | 0 |
| after | 1,610 | 1,433 (90%) | 162 | 28 | 0 |

25 newly killed, **0 resurrected** — the survivor set shrank by exactly the
mutants aimed at, which is the check #324 asks for and the one a "killed 20,
resurrected 1" fix fails.

### What the two clusters found: a declared branch nothing could reach

`_facet_clause` (12 survivors) and `_constraint_clause` (12) are the same
function twice — absent / one value / several — and reading them explains why
no end-to-end test could kill 9 mutants in each:

**Every declared facet and every declared root constraint carries exactly one
value.** All three `_edges.FACETS` are single (`("Dimension",)`,
`("Attribute",)`, `("Measure",)`), and all twenty `__constraint__` attributes
in `accessors.py` are `("artefact_type", ("Something",))`. The multi-value
`IN (...)` branch is unreachable through `q.*`.

Classified as **dead code by declaration, not by design** — the third kind, and
a new one for this file. Both docstrings state the capability (a facet "may
cover several stored kinds even when the domain exposes one name") and a facet
spanning `Dimension` and `TimeDimension` is an obvious future one. So the
branch stays and is now pinned directly, rather than waiting for whoever
declares that facet to discover whether it works.

The remaining three per function are reachable and emit SQL that *runs*, which
is why end-to-end tests pass over them: `len(values) == 1` mutated to `!= 1`
routes a single value through the `IN` branch and emits `IN (?)` — different
text, same rows. Only an assertion on the emitted SQL separates those, so
`tests/unit/test_query_compile_clauses.py` asserts text and params rather than
result sets.

One bonus kill outside the two clusters: `_conjoin__mutmut_5`, from asserting
that an empty clause is *dropped* by `_conjoin` rather than merely falsy.

### The 162 that remain

    20  _Scope._agg_body_from   14  _window            13  compile_page
    11  compile_projection      11  _order_clause      10  _after_sql
     9  _Scope._agg_subquery     6  _Scope.order        5  _Scope.grouped_having
     5  _Scope.compile           5  _check_distinct_order

`_Scope._agg_body_from` is the obvious next installment: the largest cluster,
and aggregate-subquery construction is where a wrong join or a dropped
correlation returns plausible numbers rather than an error.

## Installment 3 (#458): `formats/gaps.py`, and mutants nobody had tried

#458 asked for the readers, the writers and `formats/gaps.py`. The register
went first. It is 289 rows built from small predicate factories, and its
`carried` predicates are what `sl.unrepresentable` and every `dropped` answer
rest on.

### Half the survivors had been tried against one test

The first run against the unit suite reported 268 of 625 mutants surviving.
Every mutant of `_any_node_has`, `_any_selection`, `_any_map_has`, `_url_has`
and five other factories survived. Yet a fresh `pytest` of
`test_field_coverage` under the first `_any_node_has` mutant fails at once,
naming `hierarchies.tree[].version`.

mutmut credits a function to the tests that were running when it was hit. The
factories run while `gaps.py` imports, which happens during collection, before
any test. So every hit was credited to whichever test ran first
(`test_3x_details.py::test_a_url_may_be_given_bare`). Each factory mutant was
then run against that one test about annotation URLs, and it passed.

This is a stock mutmut behaviour, not something the fork workaround
introduced. It applies to any code evaluated at import: a module-level table,
a decorator, a registry built by calling a function. That makes it a
measurement fault for the readers and writers too, which build their dispatch
tables the same way. `tools/mutmut_forksafe.py` now takes collection-time hits
out before the first test starts, and credits each to every test that reaches
its module at run time. The tests are in
`tests/unit/test_mutmut_import_time.py`.

The fix also shrank the timeouts. 117 became 92. All 92 were then re-run
against the six files that test the register, under a two-minute cap:

* 79 fail a test;
* 11 crash `gaps.py` on import;
* 2 (`_follow`, its loop condition) hang for real.

None was a survivor hiding behind a timeout.

| | mutants | killed | timeout | survived | no test |
| --- | --- | --- | --- | --- | --- |
| first-test credit | 625 | 224 | 117 | 268 | 16 |
| module credit | 625 | 413 | 92 | 104 | 16 |
| after this installment | 609 | — | — | 61, all equivalent | 0 |

The last row is the 104 survivors re-run, each in a fresh process, against
`tests/unit/test_gaps_mutant_gaps.py`: 43 killed, 0 resurrected. The 16
untested mutants are gone because their function is gone (below).

### What the 43 were

Real gaps, each now with a test:

* **A walk tried only where it fires first.** The maximal hierarchy versions
  its first top-level node, so `_any_node_has` returned before it ever
  descended. A version only on a child, and a hierarchy with none, were never
  asked about (12 mutants).
* **3.1 keeps availability constraints.** `dropped(msg, "3.1")` returning one
  passed, because the 3.1 round-trip test goes through `without_dropped`, which
  keeps them whatever `dropped` says. `without_dropped` rebuilding a 3.0
  message with them still in it passed for the same kind of reason.
* **Naming a draft is 2.1's limit only.** `urn in out or target != "2.1"`
  mutated to `and` leaves out, of a 3.0 write, anything naming a draft, as
  soon as a `StructureSet` is also left out.
* **A chain that needs a second pass.** Categorisations precede provision
  agreements in message order, so `Categorisation → PA → DF → draft DSD` needs
  `_follow`'s loop to go round again. `changed = True` mutated to `None` passed.
* **A reference to a value list outside the message.** 2.1 cannot name it
  either way. With the value list in the message, `_follow` covered for a
  broken `_names_the_unnameable`.
* **`continue` mutated to `break`** in `_unnameable_in_21`: a component map
  with no value map ended the search.
* **3.1's availability row** (`_states_availability`): Actual *and* fitting,
  and neither alone.
* **Text no test read:** every reason, `describe`, `availability_label`, and
  the unknown-target error. "SDMX-ML None output leaves out" passed.
* **`declared_gaps(type)`** with its filter inverted.
* **`_beyond_2_1_urls`** at exactly one URL and exactly two.

Dead code: `_is_non_default_enum` has had no caller since c797227 (#421)
moved the constraint role to `_states_availability`. Its 16 mutants were "no
test" because nothing could call it. Deleted.

### The 61 that remain are equivalent

| class | count | why it cannot change behaviour |
| --- | --- | --- |
| a `getattr` default dropped or changed | 38 | every row names an attribute its `applies_to` type always has |
| `zip(..., strict=True)` weakened | 9 | both lists are built from the same list, one element each |
| `_collect_refs(artefact, prefix)` | 6 | the path it builds is discarded (`_path`) |
| `dropped`'s early return (`!=` to `==`, a renamed `"2.1"`) | 2 | it only saves the walk: nothing is left out on that path anyway |
| `_left_out`: `_NO_SUCH_CLASS.get(target, {})` without the default | 2 | every target that reaches it is a key; `"storage"` returns early |
| `_follow`: `changed = False` to `None` | 1 | both are falsy |
| `_reach`: `many and isinstance` to `or` | 1 | no non-`[]` step reaches an iterable (checked over every row on the maximal message) |
| `_annotation_at`: `rpartition(".")` to `rpartition("XX.XX")` | 1 | every `_`-suffixed site is top-level (`dimension_descriptor_`, …), so neither finds a dot |
| `_cause`: `in out` to `not in out` | 1 | `owner_classes` returns at most one class today, which its docstring states |

Three of these hold only because of a fact about the table, not the code:

* a row naming a misspelled attribute would make the `getattr` defaults matter;
* a nested `..._`-suffixed annotation site would make `_annotation_at`'s
  `rpartition` matter;
* a second owner class would make `_cause` matter.

The first is the one worth a guard. A typo'd `carried` attribute never fires,
and nothing says so. That is the subject of the row-firing gap below.

### What the register still cannot prove

On the maximal message 262 of 289 rows fire; 27 never do. Each row is checked
in one direction only:

* `test_field_coverage` fails when a loss has no declaring row;
* nothing fails when a row's predicate can never be true.

The 27 are candidates for a missing witness, or for a row that is wrong.

## Installment 4 (#458): the SDMX-ML 2.1 reader, and a census that never compared text

| `sdmx_ml21/reader.py`, unit suite minus 6 DuckLake files | mutants | killed | survived | no test |
| --- | --- | --- | --- | --- |
| baseline | 3,786 | 3,586 | 150 | 50 |
| after this installment | 3,736 | — | 44, all equivalent | 0 |

96% of covered mutants were killed before any work. The 150 survivors were
re-run in fresh processes against the new and tightened tests: 106 killed, 0
resurrected. The 50 no-test mutants were `_read_scheme_map`, which has had no
caller since 4e9651a (#93). It is deleted.

### Two guards that could not fail

**`test_field_coverage` had never compared a text.** Twelve survivors read
an artefact's `<com:Description>` as its `<com:Name>`. `maximal` gives every
artefact a description different from its name, and the census compares every
field, so all twelve should have died there.

The cause is `tests/support/field_census.py`. It compared mappings through an
`isinstance(…, dict)` branch, commented *"an `InternationalString` is a
mapping"*. But `InternationalString` has never been a `dict`, not even when
the census was written (c1c4d67, #354). So a text fell through to the
sequence branch, which iterates its keys. Every name, description and
annotation text in the model compared equal to any other in the same
languages, for every format, since the census existed.

This is the CLAUDE.md case of a guard that never could fail, not one that
stopped. Nothing it asserted was wrong, and its subject was never what the
comment said. The census now compares an `InternationalString` by its
entries. `test_the_census_can_tell_every_kind_of_value_apart` is the
discrimination check it lacked, and it fails against the old branch.
Re-running the suite with the fix found no text defect: the readers were
right, only unguarded.

**`xs:boolean` had four reading rules.** `leveled`'s `"false"` default
survived. Reading why found `xs:boolean` read inline at a dozen sites:

* `leveled`, `isPresentational`, `isSequence`, `isFinal`, `hasFormalLevels`,
  `isRegEx` and a dimension's `optional` compared against `"true"`, so `"1"`
  read false;
* `isInclusive` and a region's `include` compared against `"false"`, so `"0"`
  read true;
* `isExtension`, `isIncluded` and the `_flag` sites got both right.

The schema type's lexical space is `true`, `false`, `1` and `0`. Every read
now goes through `_common.flag` / `optional_flag` / `required_flag`.
`test_xs_boolean.py` scans the readers so a fifth rule fails.

Moving the reads into `flag` would have taken them out of
`test_reader_defaults_census` (#474), which counts `el.get(attr, default)`.
So the census now counts `flag` calls too. Nine reads that had gone through
`_flag` became visible to it for the first time:

* two were wrong. `isRESTDatasource` and `isWebServiceDatasource` are
  `use="required"` in every XSD and were read as false when absent, the
  pattern #474 removed elsewhere. They are `required_flag` now, and a source
  without one is refused;
* two carry no schema default (`removePrefix`, `isRegEx`). The model holds a
  `bool`, and every writer omits a false one, so they are classified with
  that reason.

### What the other survivors were

Each now has a test, in `test_ml21_reader_mutant_gaps.py` unless named:

* **Every maintainable's `@version`, read and defaulted.** `maximal` versions
  everything `1.0`, which is also 2.1's default, so reading the attribute and
  ignoring it agreed. That covers nine artefact kinds, and the three
  organisation schemes, whose fixed version is refused only *because* it is
  read.
* **A stub without its components block.** It is valid 2.1 (`minOccurs="0"`),
  and never tested. A reader initialising `time_dimension` to `""` passed
  everything.
* **A level's malformed coding format** names the attribute it came from.
  There were 27 mutants across seven attributes, and the message had never
  been read.
* **A hierarchical code's version:** `@version` tolerated, `1.0` by default
  (#476). Also a node with no code reference refused, not crashed. And the
  3.0-style text `<Level>` the 2.1 reader accepts.
* **Within-message resolution** of an MSD attribute's codelist and a DSD
  component's. **A second concept role.** **A comment** in a metadata target.
  **A refused attachment constraint, a category scheme map's description, a
  value map with no value type, an absent `maxOccurs`.**
* **The two-report refusal (#439),** whose test matched a fragment, now
  asserts the whole message (`test_nested_paths.py`).

### The 44 that remain are equivalent

| class | count | why |
| --- | --- | --- |
| different only on XSD-invalid input | 23 | a fixed id (`TIME_PERIOD`, `OBS_VALUE`, `REPORTING_YEAR_START_DAY`); a required element (`ConceptIdentity`, `AttributeRelationship`, a measure dimension's `LocalRepresentation`, a map's `Source`/`Target`); a full code reference's `agencyID` and `maintainableParentID`, which come together or not at all |
| on lines the `xs:boolean` fix rewrote | 7 | the mutated code no longer exists |
| the concept index's exact-URN entry | 5 | a fast path: `lookup` falls back to `resolve_item`, which finds the same concept |
| a component list initialised to `None` | 4 | `ItemList(None)` is empty, and `groups` is `None` either way |
| `SdmxUrn.make`'s class argument | 2 | a phantom witness, unused at run time |
| `read_int`'s default under `maxOccurs` | 2 | reached only when the attribute is present |
| a text `<Level>` that is empty | 1 | `""` and `"XXXX"` both name no level |

### What the census still does not reach

`maximal` holds no attachment constraint, availability constraint or category
scheme map. `shapes` covers the first and third. Availability constraints have
their own round-trip tests but no census. The fixture-population check is per
class *present*, so a class absent from the fixture is not demanded at all.

## Installment 5 (#458): the SDMX-ML 2.1 writer, and what only the document holds

| `sdmx_ml21/writer.py`, 16 direct test files, 4 children | mutants | killed | survived | no test |
| --- | --- | --- | --- | --- |
| baseline | 2,578 | 2,376 | 184 | 18 |
| after this installment | — | — | 14 equivalent, plus 3 rewritten away | 0 |

93% of covered mutants were killed before any work.

### The whole unit suite was the wrong scope for a writer

The first run used the reader's selection, the unit suite minus six DuckLake
files. Nearly every round-trip test writes 2.1, so each mutant carried
hundreds of tests and a child ran for two to three minutes. After four hours
it had tried 510 mutants and **248 had timed out**. mutmut's limit comes from
the clean run's timings, and eight children sharing four performance cores
ran slower than that. A timeout is not a verdict, so half the run had
measured nothing. It was stopped.

The run above used 16 files that test the writer directly (916 tests, 22 s).
It took 20 minutes, with no timeouts.

The selection was too narrow in one place. `shapes.message_21`, the fixture
holding what only 2.1 has, is used by tests none of the 16 included. Fifteen
more files re-ran the 202 survivors and no-test mutants in fresh processes:
`test_attribute_census`, `test_element_census`, `test_dsd_21_constructs`,
`test_constraint_attachment` and eleven others. They killed 124, including
all 18 no-test mutants (`_write_category_scheme_map`). **Choose a writer's
selection by its fixtures, not its filenames.**

### A round trip sees what the reader keeps

61 more survivors died to tests in `test_ml21_writer_mutant_gaps.py` and
`test_nested_paths.py`. Nearly all were one gap: the writer writes things the
2.1 reader never reads back, so no round trip could see them going wrong.

* **A component's `position`.** The reader ignores it, so writing the
  measure dimension at `0` and time at `4` passed.
* **A concept role's scheme.** The reader keeps the role's id, so
  `agencyID="sdmx"` passed.
* **The synthetic wrappers' own fields.** A map no set claims, or a hierarchy
  no HCL claims, is written in a marked wrapper that the reader drops (#421).
  The wrapper's version is the map's: a 2.0 map came back 1.0 under a wrapper
  left at the default. Its name, its marker, and the hierarchy wrapper's copy
  of the hierarchy's annotations and description were never asserted either.
* **The synthetic MSD report layer's** marker, `objectType` and
  `IdentifiableReference` text type.
* **The envelope**: the header, the `UTF-8` declaration (lxml omits it unless
  asked), and that output is one line unless pretty-printed.
* **Section order when a neighbour is missing.** `StructureSets` sits between
  `DataStructures` and `Constraints`. Without a DSD, or with a DSD and no
  constraint, two fallback branches place it. Neither had run.

Three gaps were not document-only:

* **A `GeographicCodelist` extending a plain codelist.** 2.1 converts it to a
  plain codelist *before* inheriting, because a geographic one holds only
  feature-set codes. `apply_extensions` on the geographic one raises
  `TypeError`. `maximal`'s extension inherits nothing, so the order was
  untested.
* **The HCL map a write declares.** `hierarchy_containers` was tested by
  calling it directly. Nothing checked that `write_kept` sets it, so a node
  reference naming the wrong container passed.
* **An MSD attribute bounded at exactly 2.** `1` is the default and is left
  out, so writing `!= 2` read back as `1`.

**`test_write_pretty_print` could not fail.** It asserted `b"\n" in` the
pretty output, but the XML declaration ends a line of its own whether or not
the document is pretty-printed. It now compares line counts. Like installment
4's census, it was never a guard, rather than a guard that stopped being one.

### The 17 that remain

| class | count | why |
| --- | --- | --- |
| rewritten away | 3 | `getattr(m, "valid_from", None)` on a type union whose every member has the field; an `isinstance(hc.code, Ref)` the `Ref \| None` annotation already guarantees. Both now plain attribute access. |
| validation refuses the input first | 2 | an MSD with targets but no report structures, and a component map with a text format but no value type. `validate` refuses one without the other before a writer runs. |
| equal-length `zip` | 3 | `strict` on a `zip` of a list and its own comprehension |
| `case _: return None` deleted | 2 | the function falls off its end and returns `None` |
| `_plain`'s guard as `and` | 2 | the cases it now converts (a plain codelist with extensions, a geographic one without) are written the same either way |
| `inner_id` against a container id nothing has | 2 | the hierarchy's container as `None`, a synthetic set treated as declared: these differ only for a member id ending `@None` or `@SS_SYNTHETIC`, which no reader produces |
| a built value map's filter | 1 | `claimed` already holds every built value map, and no set names one (#422) |
| `kind=` the default | 1 | `TargetObject.kind` defaults to `IdentifiableObjectTarget` |
| `_s(None)` | 1 | the f-string makes the same tag, `{ns}None` |

## Installment 6 (#458): the SDMX-ML 3.x reader, and 2.1's shapes it still read

| `sdmx_ml/_v3_reader.py`, 97 files that parse 3.x, 4 children | mutants | killed | survived | timeout | no test |
| --- | --- | --- | --- | --- | --- |
| baseline | 3,905 | 3,520 | 236 | 5 | 144 |
| after this installment | — | — | 50 equivalent, plus 94 rewritten away | 0 | 0 |

94% of covered mutants were killed before any work.

* **The 144 no-test mutants** were three methods with no caller since the
  3.0 and 3.1 readers merged (9850e32, #346): `_read_scheme_map`,
  `_read_id_pair_maps` and `_read_source_target_refs`. They are deleted.
* **The 5 timeouts** remove a required argument from `__init__`. The reader
  is built at import, so the suite cannot even be collected. A fresh process
  exits 4, which is a kill. mutmut 3.7's fork mode booked them as timeouts.

### The reader read 2.1 inside 3.x

70 survivors were in branches that read 2.1's forms inside a 3.x document:

* a `<Ref id=…/>` child where 3.x has text: an attribute's dimensions and
  group, a group's `DimensionReference`, a concept role, a node's `Level`;
* a draft's `@dimensionRef` and `<ObservationValue/>`;
* 2.1's unified `OrganisationSchemes` container.

No 3.x fixture or recorded response uses any of them; every match is a 2.1
cassette. The closed-world check (#435) refuses each form before the reader
runs, so the branches were dead, except one.

**A refused container was still read.** The closed-world check refuses
`<str:OrganisationSchemes>` and records it. The organisation-scheme reader
then looked the container up by name and read its schemes anyway. Under
`strict=False` they came back both in `refused` and as artefacts.

The branches are deleted. `test_v3_reader_mutant_gaps.py` pins each refusal,
so the behaviour no longer rests on the closed-world check's coverage alone.

So is the reader's `get("version", "1.0")` on the three organisation schemes.
3.x prohibits `@version` there, so the read could only ever see the default.
`test_reader_defaults_census` counts 9 assumed versions, not 12.

### A dangling level, in both readers

A hierarchical code naming a level its hierarchy does not define was read
with no level, in 3.x and in 2.1. The reference vanished without a word.
Both readers now go through `_common.node_level`, which refuses the
hierarchy and names the node and the level.

### The rest

Each now has a test:

* **Versions.** `maximal` versions everything `1.0`, which is also the
  reader's assumption for an unversioned 3.x artefact (#474), so reading
  `@version` and ignoring it agreed. That covered nine artefact kinds.
* **Messages.** A dimension out of position names both dimensions; a
  malformed `validFrom`/`validTo` on an item map or a representation mapping
  names the attribute; and so does a malformed `startIndex`/`endIndex`.
* **What a lenient read records.** A refused category scheme map is in
  `refused`, and a registry passed to `read` receives every artefact.
* **Absent blocks.**
  * A usage without a relationship, which FMR emits (#421), is per data set
    with no dimensions.
  * A time dimension without a representation has none.
  * A DSD or MSD stub without components is empty, not crashed.
* **Within-message resolution** of a component's codelist.

### The 50 that remain are equivalent

| class | count | why |
| --- | --- | --- |
| different only on XSD-invalid input | 26 | an empty or missing required element or attribute: a map's `Source`/`Target`, `FrequencyId`, `DatePattern`, a usage's reference, a dataflow `Target`'s URN, a component's `ConceptIdentity`, a dimension's `@id` |
| a component list initialised to `None` | 6 | `ItemList(None)` is empty |
| the concept index's exact-URN entry | 5 | a fast path, as in installment 4 |
| `__init__` | 4 | the `com` prefix no lookup uses; `mes`, which `ReaderHelpers` stores and never reads; `has_is_final=None`, as falsy as `False` |
| the time dimension's `@id` | 3 | fixed to `TIME_PERIOD` by the schema |
| a date map's `zip` | 3 | the schema pairs `Source` and `Target`, so the lengths agree |
| `read_int`'s default under `maxOccurs` | 2 | reached only when the attribute is present |
| an empty concept role | 1 | `""` and `"XXXX"` are neither a URN |

## Installment 7 (#458): the SDMX-ML 3.x writer

| `sdmx_ml/_v3_writer.py`, 96 files that parse 3.x, 4 children, mutmut 3.8 | mutants | killed | survived |
| --- | --- | --- | --- |
| baseline | 3,611 | 3,553 | 58 |
| after this installment | — | — | 7 equivalent, plus 1 rewritten away |

98% of mutants were killed before any work. It is the first installment
measured on mutmut 3.8, after the warm-up fix below. The run before that fix
booked 282 survivors.

**One test file is outside the selection.**
`test_ml3_writer_parity.py::test_every_annotation_site_is_reached` counts
`write_annotations` call sites in the writer's own source. The mutated copy
holds every function a few hundred times over, so it counted 3,768 sites
against 46 markers and failed the stats pass. A test that reads the source of
the module under mutation cannot run against its mutants. The file's other
seven tests went with it.

14 survivors fell to two files the selection had missed, `test_links.py` and
`test_builders_metadata_annotation.py`, which hold the metadata back-link
tests. Re-run in fresh processes, they killed those 14.

36 more fell to `test_v3_writer_mutant_gaps.py`. As with the 2.1 writer,
nearly all of them were what only the document holds:

* **The envelope:** the namespace prefixes, the header, the `UTF-8`
  declaration, and output on one line unless pretty-printed. 3.0 and 3.1
  each.
* **The metadata back-links (#191).** These are `<com:Link rel="metadata">`
  elements: their `rel`/`url`/`urn`, a base URL's trailing slash, and their
  place after the annotations and any links held, in order. Also the refusal
  without a base URL, and that an `AvailabilityConstraint` has no identity to
  link.
* **An MSD attribute bounded at exactly 2,** in 3.0 and 3.1.
* **3.1's availability.** An Actual constraint is written as one only when
  it fits: two regions keep it a data constraint.

| class | count | why |
| --- | --- | --- |
| `getattr(obj, "valid_from", None)`'s default | 4 | every class passed declares the field; the `getattr` is for the static type, as its comment says |
| the class's `pretty_print` default | 2 | `write` and `write_kept` are called only by the 3.0 and 3.1 modules, which always pass it |
| `link_base = ""` | 1 | read only behind `if metadata_links and …`, which a falsy `metadata_links` already fails |
| rewritten away | 1 | `isinstance(hc.code, Ref)`, which `Ref \| None` already guarantees, as in the 2.1 writer |

## Installment 8 (#458): `sdmx_ml/_common.py`, what both SDMX-ML generations share

| `sdmx_ml/_common.py`, 106 files that parse or write SDMX-ML, 4 children, mutmut 3.8 | mutants | killed | survived |
| --- | --- | --- | --- |
| baseline | 4,905 | 4,641 (94.6%) | 264 |
| after this installment | — | — | 64 equivalent, plus 50 rewritten away |

No timeouts: the first run after the warm-up fix below to book none.

**The selection missed files again.** Re-run in fresh processes against the
105 unit files that mention XML, parsing or serialising and were not
selected, 27 more died. Twenty of them were in `ref_el_to_urn`'s `@package` /
`@class` fallback (#240), whose tests are in `test_ref_optional_package.py`.
A file chosen by fixture misses a test that writes its own XML. For a shared
module, start from everything that parses.

123 fell to new tests, in `test_ml_common_mutant_gaps.py` unless named:

* **Refusal messages nobody had read.** These cover a wrong-class reference
  naming its element, on both paths to the check. They also cover a component
  with neither `@id` nor `ConceptIdentity`, and a constraint `role` / `type`
  outside the enumeration, which names the attribute and the constraint. Then
  every malformed-value message: a cascade on a `<Value>` or `<MemberValue>`,
  `ReferencePeriod`'s times, `@validFrom`, a TextFormat bound as integer and
  as number, `@minOccurs`, `@seriesCount`. Also 3.1's availability constraint
  without a `<CubeRegion>`, refused by its position, and 2.1's `<DataSet>`
  without a `<com:DataProvider>`.
* **What an absent attribute means.** A `CubeRegion` without `@include`
  includes. A time bound without `@isInclusive` is inclusive. A 3.x
  `ValueList` without `@version` is `1.0`. A codelist extension without a
  selection has none. A dataflow without `<Structure>` has none, in all three
  versions. Empty `ReleaseCalendar` children, an empty `SimpleDataSource` and
  an empty data-key `<Value>` read as `""`. `maximal` states every one of
  these, so a reader using the wrong default passed.
* **Resolution.** An artefact's maintainer is the agency its scheme describes,
  not a bare `Agency(id)`. A 3.x component's codelist in the message is
  resolved. A 2.1 core representation without `@package` / `@class` is still
  a codelist.
* **An availability constraint's annotations.** No census reads an
  availability constraint (installment 4 said so), and the reader could drop
  them unseen. It now round-trips whole.
* **2.1 metadata constraints** attached to a `Metadataflow` or `MetadataSet`,
  or holding a `MetadataTargetRegion`. Each is refused, as one attached to an
  MSD already was (`test_element_census.py`).
* **The writers.**
  * A `QueryableDataSource`'s `false` flags.
  * A final organisation scheme writes no `isFinal="true"` in 2.1.
  * An unwritable version names its artefact.
  * A facet bound of `1.5` is not rounded, and `1e16` is written without an
    exponent.
  * An `EnumerationFormat` drops `decimals` and `isMultiLingual`.
  * A coding format without a type writes no `textType`.
  * A concept with a text format writes no `EnumerationFormat`.
  * A value in several languages is ordered with the untagged one first.
  * A 2.1 node reference at `2.0` states `maintainableParentVersion`
    (`test_nested_paths.py`). At `1.0`, omitting it reads back the same.

**Rewritten away (50).**

* **The "latest" index keys (16).** The readers indexed every artefact and
  item a second time, under a version-less URN. Only a 2.1 `<URN>` written
  without a version could reach that key, and it then bound to whichever
  version the message held. #476 says an omitted version never means latest,
  so such a reference now stays unresolved, and a test says so.
* **`names_value_list`'s `<Ref>` branch and its 2.1 shortcut (17).** 3.x
  types `<Enumeration>` as URN text, and the closed-world check refuses a
  `<Ref>` child first. A 2.1 `<Enumeration>` has no text, so the text test
  alone answers `False` there.
* **The reader's unused `com`, `str_` and `mes` attributes, and its `mes:`
  prefix (5).**
* **`ref_el_to_urn`'s `@urn` (3).** No version's `<Ref>` has the attribute,
  and the closed-world check refuses one that does.
* **`write_istring`'s third branch (9).** Every caller passes `"Name"` or
  `"Description"`, now a `Literal`.
* **`_write_id_pair_maps`' unused `container_tag` (2).**

| class | count | why |
| --- | --- | --- |
| different only on XSD-invalid input | 40 | a required element (`GeoCell`, a data set's `com:ID`, `DataURL`, a categorisation's `Source`/`Target`, an agreement's two references, 3.x's `DataStructureComponents`, an `ISOConceptReference`'s three children, an extension's `Codelist` URN); a required `@id` on a constraint, and an element's namespace, which every element read has; an empty URN, or an empty or whitespace-only period, parent or `<Dimension>`; a non-boolean `xs:boolean`; `"F"` against `"f"`, which differ only for NaN and infinity |
| `getattr` defaults never reached | 8 | an `ObjectRefType` must state `@class` and `@package`; `None or ""` is `""`; every class written declares `valid_from` / `valid_to`; `is_partial`'s `None` and `False` are both falsy |
| `SdmxUrn.make`'s class argument | 4 | a phantom witness, unused at run time |
| a `"latest"` reference written | 4 | `serialize` refuses a version-less reference before a writer runs |
| `resolve_ref`'s `isinstance` | 2 | `Ref.of(urn, None)` is unresolved; and the URN's class is checked before the lookup, so the index holds the right type |
| equal by construction | 6 | `InternationalString()` is `_EMPTY_ISTRING`; neither `"1.0"` nor a mangled default is a final version; `partial_language`'s `None` is falsy; `pop` of a key the dict always holds; an untagged value sorting as `"XXXX"` still sorts before a lowercase language tag; a time range with no bounds, which `validate()` refuses |

## Installment 9 (#458): `sdmx_json/_structure_ingest.py`, and the second spelling of every text

| `sdmx_json/_structure_ingest.py`, 103 files that mention JSON, 4 children, mutmut 3.8 | mutants | killed | survived |
| --- | --- | --- | --- |
| baseline | 2,681 | 2,535 (94.6%) | 142, plus 4 booked as timeouts |
| after this installment | — | — | 17 equivalent, plus 16 rewritten away |

**The four "timeouts" were not timeouts.** The run was paused with `kill -STOP` partway through, to keep `just ci` from overlapping it. Four children were stopped mid-test and booked as timeouts. In a fresh process all four survive. They delete an organisation scheme map's `uri`, `links`, `valid_from` and `is_partial_language`. A paused run's timeouts must be re-run before they are believed.

**The selection missed nothing this time.** Re-run in fresh processes against the 111 unit files that parse or serialise and were not selected, all 146 survived.

113 fell to `test_json_ingest_mutant_gaps.py`:

- **The singular spelling of every text.** SDMX-JSON carries each text twice: a singular `name` in one language and a `names` map. `ingest_istr` falls back to the singular form when a producer omits the map, which the schema allows. The writer always emits both, so a reader that ignored the singular form read every fixture correctly. About 40 mutants across every artefact kind fell to one test, which strips every map that has its singular beside it.
- **A census over the whole message.** The per-case round-trip guard names its cases, and an organisation scheme map was not one of them. The new test compares every artefact of `maximal` read back from JSON, field by field, filtered through `sl.unrepresentable`. It allows exactly one other difference: a component written without a representation reads back with its concept's, which is `RepresentationMixin`'s rule, not a loss.
- **What an absent field means:**
  - a member value without `cascadeValues`;
  - a cube region without `include`;
  - a time dimension without an id;
  - a component that takes its id from its concept (#477);
  - a DSD without components;
  - a geo-grid stub without `gridDefinition`, which the schema allows "unless the artefact is returned as a stub".
- **Every maintainable reads its own version** (`maximal` versions everything `1.0`), and an organisation scheme at another version is refused only *because* the version is read.
- **The `primaryMeasure` tolerance.** It is not in the 2.0 structure schema, but the schema's own `constructed-sample-full.json` writes it.
- **A queryable data source's `wsdlURL`.** SDMX-JSON 2.0 puts queryable and simple data sources on a data constraint, as SDMX-ML does, so the reader reads them. 2.1 dropped both, and since `"json"` output must validate against 2.0 and 2.1 alike, the writer refuses them. The test builds one by hand for that reason.
- **Refusal messages** for `maxOccurs`, the time dimension, components by kind, positions, cascades and validity dates.

### Rewritten away (16)

- **A misplaced dimension was named `''`.** Positions were checked on the stated id, before a dimension without one took its concept's. They are now checked after the dimensions are built.
- **Three kinds of schema-invalid input were accepted rather than refused.** Each is now an `SdmxConformanceError` refusing the artefact (#435), as the 3.x reader's 2.1 shapes were (installment 6):
  - a concept-role URN that would not parse was skipped, and the role lost;
  - a hierarchy node's code that was not a code's URN only warned, or without `urn:` said nothing, and the node read as placing nothing;
  - a metadata attribute's `maxOccurs` accepted a string numeral such as `"3"`, while a representation's `maxOccurs` refused the same input.

### The 17 that remain are equivalent

| class | count | why |
| --- | --- | --- |
| normalised before construction | 5 | `ItemList(None)` is empty; `groups` passes as `None` when empty; `ingest_annotations` of a falsy value is empty |
| required by the schema | 5 | a sentinel's `value` (both `anyOf` branches require it); a time dimension's representation, which is a format and never an enumeration, so the index and the `isinstance` guard change nothing; `TimeDimension.id` defaults to `TIME_PERIOD` |
| a URN has one `=` | 3 | `split("=", 1)`, `split("=")`, `split("=", 2)` and `rsplit` give the same prefix |
| `zip`'s `strict` | 2 | `strict=None` and an omitted `strict` both mean `False` |
| `resolve_any_urn(None)` | 1 | it returns `None`, as the guard did |
| `levels_by_id.get(None)` | 1 | `None`, as the guard did |

## Installment 10 (#458): `sdmx_json/writer.py`

| `sdmx_json/writer.py`, 217 files that parse or serialise, 4 children, mutmut 3.8 | mutants | killed | survived |
| --- | --- | --- | --- |
| baseline | 163 | 141 (86.5%) | 22 |
| after this installment | — | — | 5 equivalent |

The module is small because it mostly delegates to the DTO layer, which the reader-side runs and the census already exercise. The selection started from every unit file that parses or serialises, as installments 7–9 taught.

All 17 that fell to `test_json_writer_mutant_gaps.py` were, as with both SDMX-ML writers, what only the document holds:

- **The envelope:**
  - `meta.id` and `sender`;
  - `prepared` in UTC, checked against a fixed clock so a non-UTC run is not the only one that would notice. **It had no time zone**, which both SDMX-JSON schemas require ("ISO 8601 … including time zone"), and the metadata writer had the same defect. The structure test pinned the zoneless value at first. pysdmx with its `json` extra, validating, warns on it, and that warning, failing the gate, is what showed it. Both writers now append `Z`;
  - compact output unless pretty-printed, with a two-space indent.
- **The metadata back-links (#191):**
  - `href` and `urn`;
  - one trailing slash trimmed and a leading one kept, including a base ending in `X`, which `rstrip("XX/XX")` would also strip (the 3.x writer's test had the same case);
  - the refusal without a base URL.
- **An unwritable version names its artefact.**

| class | count | why |
| --- | --- | --- |
| `write_kept`'s `pretty_print` default | 1 | `serialize` and `write` always pass it |
| `link_base = ""` | 1 | read only behind `metadata_links and …` |
| the back-link condition as `or` | 1 | `link_base` is set whenever `metadata_links` is truthy, and the `None` branch fails either way |
| a misfit wildcard expanded anyway | 1 | validation refuses the input first: `validate` refuses a codelist whose wildcards cannot be expanded before a writer runs |
| `msgspec.json.format` without `indent` | 1 | its default is 2 |

**SDMX-JSON data sources are refused rightly.** The 2.0 structure schema puts queryable and simple data sources on a data constraint, and the reader reads them. The 2.1 schema dropped both. sdmxlib's `"json"` output must validate against 2.0 and 2.1 alike, so `check_serialisable` refuses them and `gaps.py` declares the loss.

## The runner moved to mutmut 3.8

Installments 1–6 ran on mutmut 3.7, with `tools/mutmut_forksafe.py` patching
its runner so the stats, clean and forced-fail phases ran in a forked child
(#314). mutmut 3.8 does that itself with `process_isolation = "forkserver"`,
and renamed the internals the patch reached into. The tool now requires that
mode and keeps two things of its own:

* **the import-time credit (#458).** 3.8 still credits a collection-time hit
  to the first test;
* **preloading the native extensions.** Without it the stats child exits -6
  (`SIGABRT`) loading pyarrow.

**The first port measured nothing at import, and nobody noticed.** It kept
3.8's default `forkserver_warmup = "collect"`. The fork server collects the
suite once, before any mutant is chosen, and every child inherits the
imported modules. So code that runs at import was never mutated in a child:
the 3.x readers' and writers' module-level instances, `gaps.py`'s table of
`carried` predicates.

Its check measured the credit, and not whether the credited mutants were
ever active. `_any_node_has` was credited to 34 tests, and its mutants still
survived, because none of them ran. The first `_v3_writer.py` run found it:
282 survivors, including `__init__` setting `self._helpers = None`, which a
fresh process kills outright.

The tool now requires `forkserver_warmup = "none"`. A second fault surfaced
with it: a mutant that breaks an import-time table fails collection, and
pytest exits 4. The fork server turns that exception into exit 255, which
3.8 books as a timeout; 3.7 booked it as a kill. `CreditingRunner` books it
as a kill again.

`gaps.py` against `test_gaps_mutant_gaps` and `test_field_coverage`, 622
mutants, 4 children:

| runner | killed | survived | timeout | wall |
| --- | --- | --- | --- | --- |
| warm-up `collect` (the first port) | 317 | 302 | 2 | 79 s |
| warm-up `none` | 490 | 118 | 13 (11 exit 255) | 290 s |
| warm-up `none`, exit 4 a kill | 501 | 118 | 2 | 296 s |

Twelve sampled survivors survive a fresh process too. The two timeouts are
the real infinite loops in `_follow`: `changed = True`, and an inverted
membership test.

Installments 1–6 were measured under 3.7 and were not re-run under 3.8.

## Installment 11 (#324): `_Scope._agg_body_from`, and a `version` aggregate that refused

### A new baseline, and two ways the runner misled

`_compile.py` has grown since installment 2: **1,718** mutants. #324's resume
command mutates against the whole unit suite, and that no longer runs. The
stats child, about 11,300 traced tests in one process, is SIGKILLed for memory
with no message (`ChildProcessError: Child exited with code -9`). The run then
reports `killed 0 survived 0`. Mutated instead against the 116 test files that
exercise the query language, as `mutate_area.py` recommends:

| | mutants | killed | survived | segfault | timeout |
| --- | --- | --- | --- | --- | --- |
| baseline, 2026-10-19 | 1,718 | 1,510 | 155 | 52 | 1 |

The **segfault column is not a kill count** (#504). Re-run in a fresh process
(`MUTANT_UNDER_TEST=… PYTHONPATH=mutants/src pytest -n 6 <the same files>`),
a "segfault" is as likely to be a survivor or an equivalent as a kill. The
child crashing is the fork-safety class (#314, #498), not the mutant. Every
segfault verdict before this one is suspect for the same reason.

Re-checked one by one, against the tests the stats map says cover each
function, the 52 were **18 killed and 34 survived**. So this baseline's true
survivor count is 155 + 34 = **189**. The crashes are not deterministic either:
the verification run below, same command, crashed on 4.

### The 21 in `_agg_body_from`

| mutants | verdict |
| --- | --- |
| 36, 38, 40, 41: the `"in an aggregate"` context of the sort key | **a defect**, below |
| 122, 123: the order of a list of several columns | **gap**: the first column's `.sort()` orders the struct list, and nothing asserted it did |
| 56, 61, 62: `mean` not mapped to `avg` | **gap**: DuckDB accepts `mean()`, so the rows agree, but `avg` is the SQL a second backend reads (§4). Asserted on the emitted SQL, as installment 2 did |
| 66: a plain `min`/`max`'s binds dropped | **gap**: a localised label binds its language through the aggregate |
| 45: the `arg_` form's binds dropped | **dead by declaration**: only `version` sorts on another key, and its key binds nothing. `test_version_is_the_only_column_that_sorts_on_another_key` fails the day one does |
| 124: `_order_clause`'s collected value | **equivalent**: it matters only to `unique()`, and a `unique()` of several columns ordered by anything is refused whatever it is |
| 59, 78, 116: `AVG`, `distinct` | **equivalent**: SQL keywords are case-insensitive |
| 107, 110, 111: `zip(strict=…)` | **equivalent**: `refs` is built from `agg.columns`, so the lengths never differ |
| 11, 133, 134: the closed set's `case _` | **unreachable**: `agg.kind` is a closed set |

### The defect

`version` is the one column that sorts on something other than itself, so its
`min`/`max` are `arg_min`/`arg_max` over `version_sort` (#351). Both callers
handed `_agg_body_from` references resolved in their own scope, but the key was
resolved from the query root. In the grouped form that is the same scope. In
the correlated one it is not: `q.dsd.dataflows.version.max()` read
`dataflows.spine` as crossing the fan-out the aggregate collects over, and
refused it ("cannot use 'dataflows.spine' in an aggregate"). Meanwhile
`.list()`, `.n_unique()`, `.sort().list()` and `max()` of any other column
answered. Each caller now resolves the key as it resolves the values, and the
message whose four mutants were the trail is gone with it.
`test_query_compile_aggregates.py` fails on the old code (it raised) and passes
with dataflows at `1.9` and `1.10`, where text order would pick `1.9`.

### Verified

The same command after the change (1,732 mutants: the fix added lines),
diffing survivors per function against the baseline's 189:

| | killed | survived | segfault | timeout |
| --- | --- | --- | --- | --- |
| after | 1,546 | 180 | 4 | 2 |

- `_agg_body_from`: **21 → 10**. The ten are exactly the equivalents, the
  unreachable `case _` and the `arg_` binds classified above.
- `_grouped_agg_body`: 3 → 5. The two new survivors are mutants of the new
  code: the grouped caller's sort key passes `context`, and the line above it
  references the same column with the same context, which raises first. The
  three before are unchanged and wait for their own installment.
- No other function moved.
- The 4 segfaults were re-run in a fresh process: all 4 killed.
- The 2 timeouts (`compile_page`, `_after_sql`) are genuine infinite loops. A
  mutant that ignores the keyset cursor pages forever, so a paging test catches
  each by never ending. That is detection, but a slow one: a page-count bound in
  that test would fail fast, which is that installment's to add.

## Installment 12 (#324): `_window`, and two dead entries

Of the 15 survivors:

| mutants | verdict |
| --- | --- |
| 14, 15, 27, 28, 29, 30: `n_unique` in the `DISTINCT` set and the name map | **dead code, deleted**. A windowed `n_unique` returns early as two windowed aggregates, so null counts as a value (#326), before `arguments` or `name` is read |
| 70: the binds of that early return | **gap**: a windowed `n_unique` over a label binds its language |
| 35, 36, 37, 38: `mean` not mapped to `avg` | **gap**, as in installment 11: asserted on the emitted SQL |
| 34, 54: `LIST`, `desc` | **equivalent**: case |
| 47, 49: the sort fallback | **dead**, as installment 1 found: `order_ref` is `None` only for a window with no source column, and those are framed aggregates, which never reach the fallback. Removing it is a signature change, not a triage |

`test_query_compile_windows.py` holds the gap tests. Its third test checks the
deletion itself: the French names differ, so their windowed distinct count is
2, not 1.

### Verified

Against installment 11's baseline (189 true survivors), the same command after
the change (1,712 mutants: the deletion removed some): `_window` went **15 → 4**,
the four equivalents and dead mutants above. This run booked 40 crashes and timeouts. Re-run in a
fresh process, 26 of the 39 crashes were kills and 13 survivors, the same 13
as the baseline's. The one timeout is installment 11's paging hang. With those
counted, the true survivors went 189 → 178, and **no function but `_window`
moved**.

## What is not measured yet

Stated rather than left as an absence, per #218's ruling that the triage is the
artifact and the percentage is not:

* **Full-scope wall-clock.** 2,300 statements against the whole unit suite has
  not been timed since the scope changed. The old 923-statement figure (~4.5 h)
  is void — it was dominated by the mis-booked timeouts of #303/#314, both now
  fixed. Do not quote it.
* **Per-module baselines** for `_expr.py`, `_joins.py` and `_common.py`.
  `_compile.py` has two: 588/1,290 against the three projection test files, and
  755/1,290 against the eight predicate files.
* **The rest of `_compile.py`'s triage (#324).** 189 true survivors at
  installment 11's baseline (the segfault verdicts re-checked, #504).
  Installment 11 left 180 and installment 12 178, each verified against that
  baseline alone; the two together are not yet measured. The next clusters are
  `compile_page` (14), `compile_projection` (13), `_after_sql` (12) and
  `_Scope._agg_subquery` (11). The yield so far is a real defect or dead code
  per cluster, and a few missing tests.
* **Every baseline above installment 3 predates the import-time credit.**
  `_compile.py` builds little at import, so its numbers likely stand, but none
  was re-run to check.
* **Modules beyond #458's scope.** Every reader and writer #458 named is
  done (installments 3–10). The SDMX-JSON DTO layer (`_structure_dto.py`,
  `_dto.py`), the metadata readers and writers, and the data readers and
  writers have not been run. For any of them, select by fixture and then
  add every unit file that parses or serialises: installments 7 and 8 each
  found kills outside the fixture selection.
* **Six test files are outside every #458 run.**
  * `test_registry_data_plane`, `test_registry_lifecycle`,
    `test_bundle_relative_data_path`, `test_bench_bundle_resolution`,
    `test_data_store_ducklake_version` and `test_unattached_catalog`.
  * Each loads the DuckLake extension, and a mutmut child that does so is
    killed by SIGKILL.
  * None imports `gaps.py` or a structure reader or writer. One uses the
    *data* writers. A store write can still reach `gaps.py` through
    `LocalRegistry`, so the loss is small, not zero. It would be large for
    `storage/`.
