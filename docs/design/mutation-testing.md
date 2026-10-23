# Mutation testing: scope, triage, and the equivalent-mutant record

Line coverage says a line ran. Mutation testing says a test would have noticed
if it were wrong. This project runs the second because the first was already
high and told us nothing we could act on — `rest.py` sat at 100 % line coverage
while the empty-`IN ()` defect (#256) lived in it.

`just mutate` runs the configured scope. It is **not** in `just ci`: budget an
hour or more for it — the "about eleven minutes" the #213 pilot cost was
measured against a suite roughly a third of today's size, and the scope has
since grown from six modules to ten. While working, use `just mutate-area` (one
module against one narrow set of tests) or `just mutate-module`; the full run
is for establishing a baseline.

## Scope, and why it is small

`[tool.mutmut]`'s `only_mutate` in `pyproject.toml` restricts mutation to ten
modules, 3,212 statements (`just mutate-scope` prints the split). Mutants run
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

## The three buckets

A surviving mutant is not automatically a gap. Every survivor belongs in
exactly one bucket, and the bucket assignment is the artifact — not the score.
#218 rejected score-driven test writing explicitly, and this file exists so the
same triage is not redone from scratch on the next run.

**Real gap.** The mutant changes behaviour a caller could observe and no test
noticed. Fix by extending an existing property where possible, adding an
example only when the case genuinely is one.

**Equivalent.** The mutant cannot change observable behaviour — most often
message prose, the order of two independent checks, or a branch whose two arms
compute the same thing. These are recorded below so the next run does not
re-triage them.

**Won't fix.** Observable but not worth pinning: behaviour under conditions the
library does not support, or a test that would encode an implementation detail
we intend to keep free. Record the reason, because "not worth it" ages.

## Recording an equivalent

Add a row to the table for the module. Identify the mutant by the name mutmut
prints (`sdmxlib.model.validation.x_validate_references__mutmut_4`), because
that is what `just mutate-show` takes. The name is mutmut's mangling, not a
typo: a module-level function `f` becomes `x_f` (so a private `_f` becomes
`x__f`), and a method becomes `xǁClassǁmethod`. The tables below abbreviate to
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

## `model/collections.py` — triaged #253, run of 2026-08

**179 mutants: 145 killed, 27 survived, 3 timeout, 3 segfault, 1 no-tests.**

The 27 is *exactly* the count the #213 pilot reported. Months of test work landed in between and killed none of them. That
is the calibration #253 asked for on how much four aggregate properties pin —
and the answer is that they pin aggregate claims. Every survivor was in a
*lookup*, a *sort*, a *projection* or a *message*: places where the question is
"which one did you get back", not "is the result a subset".

After #253 the count is **15**: twelve killed, and every survivor below is
recorded as equivalent or won't-fix with its reason.

### Real gaps — closed

Tests in `tests/unit/test_collections_mutant_gaps.py`.

| mutant | what it changed |
| --- | --- |
| `_lookup_ref__mutmut_5` | `ref.id is None` → `is not None` — inverts every wildcard at once |
| `_lookup_ref__mutmut_12` | version `==` → `!=` — returns a real artefact of the wrong version |
| `_lookup_str__mutmut_1` | `and` → `or` on versioned-key detection |
| `_lookup_str__mutmut_32`, `_34` | `_by_id.get(key, [])` default dropped — a miss on a bare id raises `TypeError` instead of returning `None` |
| `_lookup_str__mutmut_19`, `_20` | the malformed-key message replaced by `None` — `KeyError(None)` tells the caller nothing |
| `sort_by__mutmut_5`, `_8` | `reverse=descending` dropped — `descending=True` becomes a silent no-op |
| `to_dict__mutmut_8` | `key(item)` → `key(None)` — every key identical, dict collapses to one entry |
| `ItemList.__hash__mutmut_1` | see *was blocked* below — gone since #290 removed the method |
| `ItemList.__repr____mutmut_13` | dropped the ids from the repr entirely |

### Equivalent — recorded, do not re-triage

| mutant | why no observable behaviour changes |
| --- | --- |
| `_lookup_str__mutmut_27` | `_by_agency_id.get(key, [])` → `.get(key, None)`. This branch falls straight to `return matches[0] if matches else None`, and `None` is falsy exactly as `[]` is. |
| `_lookup_str__mutmut_29` | Same line, default omitted entirely. Same reasoning. |

### Won't fix — recorded, with the reason

| mutant | reason |
| --- | --- |
| `_lookup_str__mutmut_10`, `_11`, `_13` | `split("(", 1)` → `split("(")` / `rsplit` / `split("(", 2)`. These differ only for a key containing **two or more** `(`. No SDMX id can produce one (`A-Za-z0-9_@$-`), so it is reachable only by hand-writing a malformed key, where every spelling ends in an exception. Pinning it would test the shape of a failure nobody can trigger. |
| `_lookup_str__mutmut_40`, `_lookup_ref__mutmut_18` | The separator in `', '.join(qualified)`. The ambiguity messages *are* pinned — two tests assert the colliding artefacts appear — but the punctuation between them is not. Killing these needs an exact-string assertion, which freezes formatting nobody has decided on. |
| eight of the nine `__repr__` mutants | Same rule: `repr` *content* is asserted, `repr` *format* is not. Measured, not assumed — a content assertion (class name plus every id present) killed exactly **one** of the nine. The other eight only move punctuation: the quoting of each id, the separator, the brackets. Killing them needs an exact-string assertion, which is a golden test for debugging output. |

### Was blocked on a defect — fixed (#290)

| mutant | note |
| --- | --- |
| `ItemList.__hash__mutmut_1` | `hash(self._items)` → `hash(None)`. Unkillable in the ordinary way at the time, because **no test could call `hash` on a populated `ItemList`** — `_items` held attrs `@define` items, which were unhashable, so it raised. The class advertised hashability it could not deliver. Filed as **#290** and since fixed: `ItemList.__hash__ = None` (and the same on `ArtefactList`), so the declaration is the refusal and there is no method left to mutate. Since #492 the items are `@frozen(unsafe_hash=False)` — equal by value, still deliberately unhashable, keyed by `urn` — so the collection stays only as hashable as its contents. Pinned by `test_the_collections_are_honestly_unhashable` in `tests/unit/test_collections_mutant_gaps.py`. |

## `model/validation.py` — untriaged

The full-scope run does not finish in a usable time. Observed while it ran:
**1000+ mutants, 129 survivors and 75 timeouts after roughly 25 minutes**, with
739 still unchecked and the timeout count climbing.

That is itself the reportable result #253 anticipated. The timeouts are the
signal: mutating `validation.py` frequently produces a run that does not
terminate promptly, so the cost per mutant is unbounded rather than merely
high. Before triaging this module, decide whether it belongs in the mutation
scope at all — and if it does, it needs its own bounded run
(`just mutate-area` or `just mutate-module` against it, with a mutmut timeout
configured) rather than sharing a scope with modules that complete in minutes.

The 123-survivor figure from #213 is not confirmed or refuted here. It should
not be repeated as fact until a run completes.
