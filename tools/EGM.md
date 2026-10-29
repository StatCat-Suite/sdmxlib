# tools

## Overview

Standalone Python scripts that guard the repository and drive mutation
testing. None is imported by the `sdmxlib` package. Most are gates run through
`justfile` recipes with `--check`; one generates library source from the
vendored XSDs; one is a MkDocs hook; the `mutate_*` scripts wrap mutmut. Each
script's module docstring records why it exists and the measurement behind its
design; read it before changing the script.

## Interface

| Script | Entry | What it does |
| ------ | ----- | ------------ |
| `check_docstrings.py` | `just check-docstrings` (`--check`) | Fails on Sphinx/RST roles and literal blocks, NumPy section headers (a section name followed by a rule), and pydoclint `DOC003` under `src` and `cli/src`. Functions: `rst_markup`, `numpy_sections`, `style_mismatches`, `main`. |
| `check_prose.py` | `just check-prose` (`--check`) | Fails on a Markdown heading ending in `?`, a phrase in `PHRASES`, and a bare `#N` / `GH-N` in user docs (`docs/` outside `docs/design/`). The phrase check also reads the `.py` and `.toml` files and the justfile under `CODE` (`src`, `cli/src`, `tests`, `tools`, `bench`, the root config files). Function: `find`, `main`. |
| `count_any.py` | `just check-any` (`--check`, `--write`) | Counts `Any` used as a type in `src/`, following module-level aliases, and compares the total to `any_baseline.txt`. Functions: `alias_names`, `count_file`, `survey`, `main`. |
| `any_baseline.txt` | read by `count_any.py` | The recorded `Any` count (currently 33). |
| `check_indexes.py` | `just check-indexes` (`--check`) | Fails when a tracked file names a non-public package index, a credential-bearing file (`uv.lock`, `.netrc`, `.pypirc`, `pip.conf`, `pip.ini`) is tracked, or `uv.lock` is not gitignored. Symbols: `Finding`, `PUBLIC_INDEX_HOSTS`, `is_public`, `hosts_in`, `directive_value`, `scan_text`, `tracked_files`, `structural_findings`, `survey`, `main`. |
| `check_return_widening.py` | `uv run python tools/check_return_widening.py [--check]` | Finds functions that declare an sdmxlib class `D` and return a subclass `N` adding public surface; the class pairs in `ALLOWED` and the `file:function` entries in `ALLOWED_FUNCTIONS` are exempt, each with its reason. Symbols: `Finding`, `scan`, `main`. |
| `generate_allowed_children.py` | `just generate-allowed-children` (always rewrites); `uv run python tools/generate_allowed_children.py --check` exits 1 if stale | Resolves the vendored SDMX-ML XSDs in `tests/schemas/` into the per-element allowed-children and allowed-attributes tables. Symbols: `Schema`, `resolve`, `contents`, `table`, `attribute_table`, `render`, `main`. |
| `mkdocs_issue_links.py` | MkDocs hook (`mkdocs.yml`) | Links bare `#N`, `!N`, `%N` to `history/gitlab/` and `GH-N` to GitHub on the docs site. Functions: `link_references`, `on_page_markdown`. |
| `mutate_area.py` | `just mutate-area MODULE TESTS...` | Mutates one module against a narrow test selection, then prints the tally. Functions: `rewrite`, `main`, `report`. |
| `mutate_union.py` | `uv run python tools/mutate_union.py MODULE GROUP...` | Runs several light test groups against one module and unions the kills. |
| `mutate_compare.py` | `uv run python tools/mutate_compare.py MODULE label=files...` | Compares which mutants each labelled suite kills, restricted to mutants both arms reach. |
| `mutate_scope.py` | `just mutate-scope` | Prints `[tool.mutmut] only_mutate` entries with statement counts from coverage's parser. |
| `mutmut_forksafe.py` | `just mutate` | Drop-in `mutmut` wrapper: fork-server mode, native-extension preload, and import-time hit credit. Symbols: `CreditingRunner`, `credit_import_time`, `mutant_runner`. |

## Invariants

- Each gate exits non-zero under `--check` when it has a finding, and only
  reports otherwise. A gate stays able to fail: each has a test in
  `tests/unit/` that feeds it a violation (`test_check_docstrings.py`,
  `test_check_prose.py`, `test_count_any.py`, `test_check_indexes.py`,
  `test_return_widening.py`, `test_closed_world.py`,
  `test_mkdocs_issue_links.py`, `test_mutmut_forksafe.py`).
- `count_any.py --check` fails when the count moves off the baseline in either
  direction. A drop is recorded with `--write`, so the lowered baseline shows
  in the diff; raising it needs a reason in the commit message.
- `check_indexes.py` is an allow-list of public index hosts. It never names a
  private host, because naming it in the gate would leak it.
  `check_indexes.py` and its test are exempt from its own scan.
- `check_prose.py` exempts CLAUDE.md's "Writing style" section and itself,
  since both quote the banned phrases. CHANGELOG.md and `design/archive/` are
  not read.
- `check_docstrings.py` consults only pydoclint's `DOC003`. Every other
  pydoclint code is discarded. It exits 2 when pydoclint fails to run
  (an exit code other than 0 or 1), with or without `--check`.
- `src/sdmxlib/formats/sdmx_ml/_allowed_children.py` is generated output.
  Change the generator or the XSDs, then regenerate; `test_closed_world.py`
  fails while the shipped module differs from `render()`, and is the guard
  `just ci` runs.
- `check_return_widening.py` judges only a single declared sdmxlib class.
  Unions, aliases, generics and `object` are skipped.
- A `mutate_area.py` score answers whether the named tests pin the module.
  It is not a module-wide mutation score. Keep each `mutate_union.py` group to
  two or three test files so the forking parent stays light (#303).

## Side Effects

- `generate_allowed_children.py` without `--check` rewrites
  `src/sdmxlib/formats/sdmx_ml/_allowed_children.py`.
- `count_any.py --write` rewrites `tools/any_baseline.txt`.
- `mutate_area.py`, `mutate_union.py` and `mutate_compare.py` rewrite
  `pyproject.toml`'s `[tool.mutmut]` section for the run, restore it in
  `finally`, and delete the repository's `mutants/` cache before each run
  (`mutate_compare.py` before each arm). `mutate_area.py` backs the original
  up to a temporary directory; `mutate_union.py` and `mutate_compare.py` hold
  it in memory, and `mutate_union.py` also restores it on SIGTERM and
  SIGINT.
- The `mutate_*` scripts and `mutmut_forksafe.py` run mutmut, which writes
  `mutants/` and forks one process per mutant. Runs take minutes to hours.
- `check_return_widening.py` copies `src/sdmxlib` into a temporary directory,
  rewrites its returns there, and runs `.venv/bin/pyrefly` on the copy.
- `check_docstrings.py` runs pydoclint; `check_indexes.py` runs
  `git ls-files` and `git check-ignore`.
- `mkdocs_issue_links.py` rewrites page Markdown in memory during
  `mkdocs build`; it writes no files.

## Verification

- Run: `just check-docstrings`, `just check-prose`, `just check-any`,
  `just check-indexes`
- Run: `uv run pytest tests/unit/test_check_docstrings.py tests/unit/test_check_prose.py tests/unit/test_count_any.py tests/unit/test_check_indexes.py tests/unit/test_return_widening.py tests/unit/test_closed_world.py tests/unit/test_mkdocs_issue_links.py tests/unit/test_mutmut_forksafe.py`
- Full gate: `just ci`
