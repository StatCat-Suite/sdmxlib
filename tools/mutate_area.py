"""Mutate one module against one narrow set of tests, then restore the config.

Run via `just mutate-area`. This is the *default* way to use mutation testing
here; `just mutate` over the whole configured scope is a baselining tool that
takes hours.

## Why narrow, and why it is a correctness fix rather than only a speed one

mutmut runs a "stats pass" — the whole selected test suite, in-process — to
learn which tests cover which function, then `fork()`s one child per mutant off
that parent. With `pytest_add_cli_args_test_selection = ["tests/unit"]` the
parent has executed all ~3,900 tests before the first fork, and children forked
from it are pathological. Measured on `_validate_hierarchy`, same mutant, same
53 covering tests:

    forked from a LIGHT parent    exit 0, 3.0 s CPU        (correctly: survived)
    forked from a HEAVY parent    never completed in 8 min

Under mutmut those children are killed by `RLIMIT_CPU` (exit -24, SIGXCPU) and
booked as **timeouts**. That is #303: 283 `validation.py` mutants recorded as
timeouts that are not hangs at all — running the identical workload in a fresh
process takes 3 s and reports the mutant as survived.

Narrowing the test selection keeps the parent light. The same function, mutated
against `tests/unit/test_hierarchy.py` alone:

    16 mutants, 0 timeouts, 7 s wall      (was 14/14 timeouts, 93 s)

## What a narrow score means, and does not

It answers "does *this* test file pin *this* code" — which is the question worth
asking while working. It is **not** a module-wide mutation score: mutants whose
covering tests lie outside the selection are simply not run, and survivors here
may well be killed by tests elsewhere. Do not quote a number from this as the
module's score; use `just mutate` for that, with the caveats in the justfile.
"""

import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Annotated

import typer

ROOT = pathlib.Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"


def rewrite(text: str, module: str, tests: list[str]) -> str:
    """Point `only_mutate` at *module* and the test selection at *tests*."""

    def as_toml(items: list[str]) -> str:
        return "[\n" + "".join(f'    "{i}",\n' for i in items) + "]"

    out, n = re.subn(r"only_mutate = \[[^\]]*\]", f"only_mutate = {as_toml([module])}", text, count=1)
    if not n:
        msg = "could not find `only_mutate` in [tool.mutmut]"
        raise SystemExit(msg)
    out, n = re.subn(
        r"pytest_add_cli_args_test_selection = \[[^\]]*\]",
        f"pytest_add_cli_args_test_selection = {as_toml(tests)}",
        out,
        count=1,
    )
    if not n:
        msg = "could not find `pytest_add_cli_args_test_selection` in [tool.mutmut]"
        raise SystemExit(msg)
    return out


def main(
    module: Annotated[pathlib.Path, typer.Argument(help="Source file to mutate, e.g. src/sdmxlib/model/codelist.py.")],
    tests: Annotated[list[str], typer.Argument(help="Test files the stats pass may run.")],
    *,
    max_children: Annotated[int, typer.Option(help="Forked mutant runners at once.")] = 8,
) -> None:
    """Mutate one module against the tests named, and print the tally."""
    if not (ROOT / module).is_file():
        msg = f"no such module: {module}"
        raise typer.BadParameter(msg)
    for test in tests:
        if not (ROOT / test).exists():
            msg = f"no such test path: {test}"
            raise typer.BadParameter(msg)

    original = PYPROJECT.read_text(encoding="utf-8")
    # Kept outside the tree so an interrupted run cannot leave a stray file that
    # looks like a config, and restored in `finally` so ^C restores it too.
    backup = pathlib.Path(tempfile.mkdtemp()) / "pyproject.toml"
    backup.write_text(original, encoding="utf-8")

    print(f"mutating {module}")
    print(f"  against {' '.join(tests)}")
    try:
        PYPROJECT.write_text(rewrite(original, str(module), tests), encoding="utf-8")
        # `mutants/` caches results keyed on the old config; a stale cache here
        # would report the previous area's verdicts as if they were this one's.
        shutil.rmtree(ROOT / "mutants", ignore_errors=True)
        subprocess.run(  # noqa: S603
            # Not bare `mutmut run`: the wrapper preloads the native extensions a
            # forked child would otherwise abort loading, and credits import-time
            # hits to the tests that can see them (#314, #458).
            ["uv", "run", "python", "tools/mutmut_forksafe.py", "run", "--max-children", str(max_children)],  # noqa: S607
            cwd=ROOT,
            check=False,
        )
        # The tally comes from `export-cicd-stats`, which writes a JSON file
        # with every verdict. `mutmut results` lists only the *interesting*
        # ones — killed mutants are omitted entirely — so counting its lines
        # makes a healthy area look like a total failure (`survived=82` with no
        # mention of the 189 killed alongside them).
        stats = subprocess.run(
            ["uv", "run", "mutmut", "export-cicd-stats"],  # noqa: S607
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        if stats.returncode:
            print(f"export-cicd-stats failed: {stats.stderr.strip()[:200]}", file=sys.stderr)
    finally:
        PYPROJECT.write_text(backup.read_text(encoding="utf-8"), encoding="utf-8")
        shutil.rmtree(backup.parent, ignore_errors=True)

    report(ROOT / "mutants" / "mutmut-cicd-stats.json")


def report(stats_file: pathlib.Path) -> None:
    """Print the tally, and the caveats that stop it being read as a grade."""
    if not stats_file.is_file():
        print("\n  no stats written — did any mutant run?")
        return
    s = json.loads(stats_file.read_text(encoding="utf-8"))
    killed, survived = s.get("killed", 0), s.get("survived", 0)
    covered = killed + survived
    print(
        f"\n  {s.get('total', 0)} mutants   killed {killed}   survived {survived}   no-coverage {s.get('no_tests', 0)}"
    )
    if covered:
        print(f"  {killed / covered:.0%} of covered mutants killed by this test selection")
    if s.get("timeout"):
        print(
            f"\n  {s['timeout']} timeouts — suspect. See #303: under a broad test\n"
            "  selection these are RLIMIT_CPU kills of healthy children, not hangs.\n"
            "  Narrow the selection before believing them."
        )
    if s.get("no_tests"):
        print(f"\n  {s['no_tests']} mutants had no covering test *in this selection* — not a gap on their own.")
    print("\n  survivors: just mutate-results     one diff: just mutate-show <name>")


if __name__ == "__main__":
    typer.run(main)
