# Mutation testing: scope, triage, and the equivalent-mutant record

Line coverage says a line ran. Mutation testing says a test would have noticed
if it were wrong. This project runs the second because the first was already
high and told us nothing we could act on — `rest.py` sat at 100 % line coverage
while the empty-`IN ()` defect (#256) lived in it.

`just mutate` runs the configured scope. It is **not** in `just ci`: budget
hours for it — the "about eleven minutes" the #213 pilot cost was
measured against a suite roughly a third of today's size, and the scope has
since grown from six modules to ten. While working, use `just mutate-area` (one
module against one narrow set of tests) or `just mutate-module`; the full run
is for establishing a baseline.

## Scope, and why it is small

`[tool.mutmut]`'s `only_mutate` in `pyproject.toml` restricts mutation to ten
modules, 3,383 statements on 2026-10-28 (`just mutate-scope`, which runs
`tools/mutate_scope.py`, prints the split). Mutants run
2–4× the statement count, so a whole-`src/` run is a five-figure mutant count
that would never finish.

- **Six model modules** — `urn.py`, `istring.py`, `expr.py`,
  `representation.py`, `collections.py`, `validation.py` — the original pilot
  scope. They were chosen as the sharpest available question rather than the
  easiest: they are pure, fast, and already claimed high coverage — `urn.py`
  and `representation.py` were at 100 %, which makes "is a fully covered
  module actually pinned?" answerable.
- **Four query-compiler modules** (#304) — `query/_expr.py`, `_compile.py`,
  `_joins.py`, `_common.py`. #304 named `code_query.py`, `hierarchy_query.py`
  and `_query_common.py`; #306 deleted all three, and these four are where the
  work moved. `accessors.py`, `query.py` and `_pushdown.py` are deliberately
  left out, pending their own decision (the reasoning is beside `only_mutate`).

## The runner

`just mutate` runs `tools/mutmut_forksafe.py run --max-children 8`, a
drop-in for `mutmut` on mutmut 3.8 or later. `[tool.mutmut]` sets
`process_isolation = "forkserver"`, which keeps pytest out of the forking
parent (#314), and `forkserver_warmup = "none"`, so nothing is imported
before a mutant is chosen and code that runs at import is mutated too
(#458). The wrapper refuses to run without the fork server. It also
preloads the native extensions and credits import-time hits to every test
that reaches the module (`CreditingRunner`, #458), where mutmut alone
credits them to whichever test ran first.

`just mutate-area MODULE TESTS...` (`tools/mutate_area.py`) narrows
`only_mutate` and the test selection to one module and one set of test
files, runs the same wrapper, and restores the config. Its score answers
"does this test file pin this code"; it is not a module-wide score.
`just mutate-module PREFIX` runs one module or function from the configured
scope.

## The three buckets

A surviving mutant is not automatically a gap. Every survivor belongs in
exactly one bucket, and the bucket assignment is the artifact — not the score.
#218 rejected score-driven test writing explicitly, and the record in
[evidence/mutation-runs.md](evidence/mutation-runs.md) exists so the same
triage is not redone from scratch on the next run.

**Real gap.** The mutant changes behaviour a caller could observe and no test
noticed. Fix by extending an existing property where possible, adding an
example only when the case genuinely is one.

**Equivalent.** The mutant cannot change observable behaviour — most often
message prose, the order of two independent checks, or a branch whose two arms
compute the same thing. These are recorded beside the run that found them, so
the next run does not re-triage them.

**Won't fix.** Observable but not worth pinning: behaviour under conditions the
library does not support, or a test that would encode an implementation detail
we intend to keep free. Record the reason, because "not worth it" ages.

## Recording an equivalent

Add a row to the module's table in
[evidence/mutation-runs.md](evidence/mutation-runs.md); the `collections.py`
record is
[there](evidence/mutation-runs.md#modelcollectionspy-triaged-253-run-of-2026-08).
Identify the mutant by the name mutmut
prints (`sdmxlib.model.validation.x_validate_references__mutmut_4`), because
that is what `just mutate-show` takes. The name is mutmut's mangling, not a
typo: a module-level function `f` becomes `x_f` (so a private `_f` becomes
`x__f`), and a method becomes `xǁClassǁmethod`. The tables abbreviate to
the function and mutant number. Give the reason in terms of *why no
observable behaviour changes* — "it only changes the message text" is a reason;
"looks fine" is not.

Mutant names are positional and **will shift** when the module is edited. The
reason column is what survives; treat a name that no longer resolves as an
instruction to re-triage that one, not to delete the row.

## Probing a single test by hand, and the stale-bytecode trap

Between full runs, the cheap way to check that a test *discriminates* is to
reintroduce the defect it claims to catch, run that test, and restore. Several
guards in the suite were verified this way rather than assumed.

**Clear `__pycache__` after restoring.** Python invalidates a `.pyc` on
`(mtime, size)`. A probe that writes a broken file and restores it milliseconds
later usually produces a file of the *same size* at the *same mtime* to the
filesystem's resolution — so the broken bytecode stays cached while the `.py`
on disk is correct. The symptom: `inspect.getsource` shows the right
code, `git diff` is clean, and the behaviour is the old one.

That happened while verifying the #268 join-splice test. It presented as a
parameter-ordering bug in `code_query._where` (a module #306 has since
deleted), and about fifteen minutes went
into a defect that did not exist. The tell is a probe whose *restore* step
fails: every earlier probe in this suite ended with "restored -> PASSED", which
is what rules the hazard out.

    find src -name __pycache__ -type d -exec rm -rf {} +

## Keeping the gate runnable

mutmut copies `src/`, `tests/`, `pyproject.toml` and `uv.lock` into `mutants/`
and runs the suite there. **A test that imports from any other repo directory
fails at collection and aborts the entire run** — not one mutant, all of them.

That happened: #284 added `from tools.count_any import …` and silently broke
`just mutate` until #253 tried to use it. Nothing caught it, because the
expensive gate is not in `just ci` and the cheap signal did not exist.

It does now:
`test_count_any.test_no_test_imports_a_repo_tree_mutmut_does_not_copy` scans
`tests/` for imports of repo directories outside the copied set. It costs
milliseconds and runs in `just ci`.

## Where the run records live

Each run's counts, triage and equivalent / won't-fix tables are in
[evidence/mutation-runs.md](evidence/mutation-runs.md): the #218 property
comparison, installments 1–12 (#324, #458), the `model/collections.py` triage
(#253) and the untriaged `model/validation.py` run.
