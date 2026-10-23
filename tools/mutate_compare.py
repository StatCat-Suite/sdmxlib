"""Which suite kills which mutants — the #218 comparison, run rather than argued.

#218 asked whether property-based tests kill more mutants than example-based
ones, over the same module and the same mutant set. #304 carried it forward and
it stayed unanswerable while a heavy test selection deadlocked (#314).

**The answer worth having is not two percentages.** "Properties killed 61%,
examples killed 58%" invites picking a winner, and both suites are kept
whatever it says. What decides anything is the asymmetry:

* mutants **only** the properties kill — behaviour no hand-written case pins,
* mutants **only** the examples kill — behaviour generation never reaches,
* mutants **neither** kills — the actual gap, and the thing to triage.

A large symmetric-difference in both directions is the case for keeping both
suites. A near-empty one in either direction is a real finding about the other.

## Two things that make the number mean something

**Verdicts are read from `mutmut results --all`.** The bare listing omits
kills, so a killed set can only be inferred as its complement — and across two
arms that inference cannot tell "both killed it" from "neither arm listed it".
The first version of this tool did exactly that and printed `killed by every
arm 0` beside an arm that had killed 454.

**The comparison is restricted to mutants both arms reach.** `no tests` is not
a verdict about the code; it says an arm never executed that line. Counting it
against a suite measures coverage rather than strength, and the two arms here
differ enormously — the properties drive predicate compilation and never touch
projection or paging at all. Both totals are printed, and the restricted one
is the answer.

## Usage

    uv run python tools/mutate_compare.py src/sdmxlib/query/_compile.py \\
        properties=tests/unit/test_properties_query_code.py,tests/unit/test_properties_query_hcode.py \\
        examples=tests/unit/test_query_compile.py,tests/unit/test_query_boolean.py

Each arm is `label=comma,separated,test,files`. Two or more arms.
"""

import argparse
import json
import pathlib
import shutil
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import mutate_area as ma


def _run_arm(module: str, tests: list[str], max_children: str) -> dict[str, str]:
    """Mutate `module` against `tests`; return every mutant's verdict by name.

    `mutmut results --all` is what makes the comparison exact. Without `--all`
    the listing omits kills, so a killed set can only be *inferred* as the
    complement of a listing — and with two arms that inference cannot
    distinguish "both killed it" from "neither arm reported it", which is how
    the first version of this tool printed `killed by every arm  0` next to an
    arm that had killed 454.
    """
    original = ma.PYPROJECT.read_text(encoding="utf-8")
    ma.PYPROJECT.write_text(ma.rewrite(original, module, tests), encoding="utf-8")
    try:
        # A stale `mutants/` is keyed on the previous arm's config, and mutmut
        # skips mutants it already has a verdict for — the previous arm's
        # kills would be reported as this one's.
        shutil.rmtree(ma.ROOT / "mutants", ignore_errors=True)
        subprocess.run(  # noqa: S603
            ["uv", "run", "python", "tools/mutmut_forksafe.py", "run", "--max-children", max_children],  # noqa: S607
            cwd=ma.ROOT,
            check=False,
            capture_output=True,
        )
        listing = subprocess.run(
            # `--all` is declared as an option with a default, not a flag, so
            # it needs a value — `--all` alone is a click parse error and the
            # listing comes back empty.
            ["uv", "run", "mutmut", "results", "--all", "true"],  # noqa: S607
            cwd=ma.ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
    finally:
        ma.PYPROJECT.write_text(original, encoding="utf-8")

    verdicts: dict[str, str] = {}
    for line in listing.stdout.splitlines():
        name, sep, status = line.strip().partition(": ")
        if sep and name.startswith("sdmxlib."):
            verdicts[name] = status
    return verdicts


def _tally(verdicts: dict[str, str]) -> str:
    counts: dict[str, int] = {}
    for status in verdicts.values():
        counts[status] = counts.get(status, 0) + 1
    return "  ".join(f"{status}={count}" for status, count in sorted(counts.items()))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("module", help="source file to mutate")
    parser.add_argument("arms", nargs="+", help="label=test1,test2 — two or more")
    parser.add_argument("--max-children", default="8")
    args = parser.parse_args()

    if not (ma.ROOT / args.module).is_file():
        print(f"no such module: {args.module}", file=sys.stderr)
        return 2
    arms: list[tuple[str, list[str]]] = []
    for arm in args.arms:
        label, _, joined = arm.partition("=")
        if not joined:
            print(f"arm must be label=tests: {arm}", file=sys.stderr)
            return 2
        tests = joined.split(",")
        for test in tests:
            if not (ma.ROOT / test).exists():
                print(f"no such test path: {test}", file=sys.stderr)
                return 2
        arms.append((label, tests))
    if len(arms) < 2:
        print("need at least two arms to compare", file=sys.stderr)
        return 2

    print(f"comparing suites over {args.module}\n")
    verdicts_by_arm: dict[str, dict[str, str]] = {}
    for label, tests in arms:
        verdicts = _run_arm(args.module, tests, args.max_children)
        verdicts_by_arm[label] = verdicts
        print(f"  {label:12} {' '.join(pathlib.Path(t).name for t in tests)}")
        print(f"  {'':12} {len(verdicts)} mutants: {_tally(verdicts)}\n", flush=True)

    labels = [label for label, _ in arms]
    killed = {label: {n for n, s in v.items() if s == "killed"} for label, v in verdicts_by_arm.items()}
    # "no tests" is not a verdict about the code — it says this arm never
    # reached the mutant. Comparing suites over mutants one of them cannot
    # see measures coverage, not strength, so the honest comparison is the
    # intersection of what both arms reached.
    covered = {label: {n for n, s in v.items() if s != "no tests"} for label, v in verdicts_by_arm.items()}
    common = set.intersection(*covered.values())

    print(f"  over all {len(set().union(*(set(v) for v in verdicts_by_arm.values())))} mutants:")
    for label in labels:
        others: set[str] = set().union(*(killed[other] for other in labels if other != label))
        print(f"    killed only by {label:12} {len(killed[label] - others):5}")
    print(f"    killed by every arm       {len(set.intersection(*killed.values())):5}")
    print(
        f"    killed by no arm          {len(set().union(*(set(v) for v in verdicts_by_arm.values())) - set().union(*killed.values())):5}"
    )

    print(f"\n  over the {len(common)} mutants *both* arms reach — the fair comparison:")
    for label in labels:
        others = set().union(*(killed[other] for other in labels if other != label))
        only = (killed[label] & common) - others
        print(f"    killed only by {label:12} {len(only):5}")
    print(f"    killed by every arm       {len(set.intersection(*killed.values()) & common):5}")
    survived_everywhere = common - set().union(*killed.values())
    print(f"    killed by no arm          {len(survived_everywhere):5}   <- the gap to triage")

    for label in labels:
        others = set().union(*(killed[other] for other in labels if other != label))
        only = sorted((killed[label] & common) - others)
        if only:
            print(f"\n  only {label} kills these ({len(only)}):")
            for name in only[:40]:
                print(f"    {name}")
            if len(only) > 40:
                print(f"    ... and {len(only) - 40} more")

    report = ma.ROOT / "mutants" / "mutate-compare.json"
    report.parent.mkdir(exist_ok=True)
    report.write_text(json.dumps(verdicts_by_arm, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\n  per-mutant verdicts written to {report.relative_to(ma.ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
