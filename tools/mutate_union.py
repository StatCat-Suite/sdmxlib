"""Union mutation kills across several LIGHT test selections.

`mutate_area` answers "does *this* test file pin *this* code". For a module
exercised by a dozen files — the query compiler is — that under-reports badly,
and the obvious fix of naming every file at once is worse than the disease.

## The measurement that forced this tool

`src/sdmxlib/query/_compile.py`, 1266 mutants:

    1 test file       killed 334   survived 209   timeout   0
    14 test files     killed 471   survived  30   timeout 205   (partial, 706/1266)

Both numbers are wrong, in opposite directions.

The narrow run's 209 survivors are mostly killed by the other thirteen files,
so 62% is not the module's score. But the broad run re-created #303: mutmut
forks each mutant off a parent that has already run the whole selection
in-process, and children forked from a heavy parent are killed by `RLIMIT_CPU`
and booked as **timeouts** rather than hangs. 205 of 706 verdicts were that
artefact, and the run took 16 minutes for work the narrow one did in 60
seconds.

So: run several *light* selections, and union the kills. A mutant is a real
survivor only when **no** group killed it. Each parent stays light, so the
timeout count stays at zero and every verdict means what it says.

## Reading the output

Per-group lines are `mutate_area`'s tally for that group, with the same
caveats — a survivor in group 3 may be killed by group 5. Only the union line
at the end is a claim about the module.

`no_tests` is not a gap on its own: it counts mutants no test *in that group*
covers, which is the normal case when a module is split across many files.

## Usage

    uv run python tools/mutate_union.py src/sdmxlib/query/_compile.py \\
        tests/unit/test_query_compile.py,tests/unit/test_query_trimming.py \\
        tests/unit/test_query_select.py,tests/unit/test_query_group_by.py

Each argument after the module is one comma-separated group. Budget a few
minutes per group. Keep groups to two or three files: that is what keeps the
parent light, and it is the whole point.
"""

import argparse
import json
import pathlib
import shutil
import signal
import subprocess
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import mutate_area as ma


def _restore(pyproject: pathlib.Path, original: str) -> None:
    pyproject.write_text(original, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("module", help="module to mutate, e.g. src/sdmxlib/query/_compile.py")
    parser.add_argument("groups", nargs="+", help="comma-separated test files, one group per argument")
    parser.add_argument("--max-children", default="8")
    args = parser.parse_args()

    groups = [g.split(",") for g in args.groups]
    pyproject = ma.PYPROJECT
    original = pyproject.read_text(encoding="utf-8")

    # A killed run must not leave the mutmut config behind: the next `just
    # check` would then be reading a pyproject nobody edited on purpose. The
    # `finally` covers the normal exit and Ctrl-C; these cover the signals that
    # would otherwise skip it. Learned by killing a 16-minute run and finding
    # `only_mutate` still rewritten.
    def _bail(_signum: int, _frame: types.FrameType | None) -> None:
        _restore(pyproject, original)
        raise SystemExit(130)

    signal.signal(signal.SIGTERM, _bail)
    signal.signal(signal.SIGINT, _bail)

    #: Mutants no group has killed yet — intersected down per group.
    surviving: set[str] | None = None
    totals: list[int] = []
    try:
        for index, tests in enumerate(groups, 1):
            pyproject.write_text(ma.rewrite(original, args.module, tests), encoding="utf-8")
            # `mutants/` caches verdicts keyed on the old config. Without this
            # wipe a later group inherits the previous one's results — and
            # worse, mutmut skips re-running mutants it already has a verdict
            # for, so that group's kills never reach the union. `mutate_area`
            # does the same for the same reason; omitting it cost a 10-minute
            # run that never reported a group.
            shutil.rmtree(ma.ROOT / "mutants", ignore_errors=True)
            subprocess.run(  # noqa: S603
                # Not bare `mutmut run`: the wrapper preloads the native extensions a
                # forked child would otherwise abort loading, and credits import-time
                # hits to the tests that can see them (#314, #458).
                ["uv", "run", "python", "tools/mutmut_forksafe.py", "run", "--max-children", args.max_children],  # noqa: S607
                cwd=ma.ROOT,
                check=False,
                capture_output=True,
            )
            # `mutmut results` lists only the *interesting* verdicts — killed
            # mutants are omitted entirely. So a group's listing is exactly its
            # NOT-killed set, and "killed by this group" is the complement.
            # Parsing it for `killed` yields nothing, which is the bug this
            # replaced: every mutant looked like a survivor.
            results = subprocess.run(
                ["uv", "run", "mutmut", "results"],  # noqa: S607
                cwd=ma.ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            not_killed = {
                stripped.partition(": ")[0]
                for line in results.stdout.splitlines()
                if (stripped := line.strip()).startswith("sdmxlib.")
            }
            # Intersect: a mutant survives the union only if no group killed
            # it. Killed in a later group means absent from that group's
            # listing, which drops it here. "no tests" and "timeout" stay,
            # because neither is a kill.
            surviving = not_killed if surviving is None else surviving & not_killed

            # The tallies come from `export-cicd-stats`, which writes every
            # verdict including the kills `results` hides.
            subprocess.run(  # noqa: S603
                ["uv", "run", "mutmut", "export-cicd-stats"],  # noqa: S607
                cwd=ma.ROOT,
                check=False,
                capture_output=True,
            )
            stats_file = ma.ROOT / "mutants" / "mutmut-cicd-stats.json"
            stats = json.loads(stats_file.read_text(encoding="utf-8")) if stats_file.is_file() else {}
            names = " ".join(pathlib.Path(t).name for t in tests)
            print(
                f"group {index}: {names:56} killed={stats.get('killed', 0):4} "
                f"survived={stats.get('survived', 0):4} timeout={stats.get('timeout', 0):3} "
                f"no_tests={stats.get('no_tests', 0):4}",
                flush=True,
            )
            totals.append(stats.get("killed", 0))
    finally:
        _restore(pyproject, original)

    survivors = sorted(surviving or ())
    print(f"\n  mutants killed by some group : {sum(totals)} (per-group kills, may overlap)")
    print(f"  survived every group         : {len(survivors)}")
    if stats.get("timeout"):
        print("\n  timeouts above are suspect — see #303. If a group reports many,")
        print("  it is too heavy; split it further.")
    if survivors:
        print("\n  real survivors (no group killed these):")
        for name in survivors:
            print(f"    {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
