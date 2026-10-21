"""Print what `[tool.mutmut] only_mutate` covers, and how big each entry is.

Run via `just mutate-scope`. It exists because the scope is the one number that
decides whether a mutation run is minutes or hours, and it was previously
invisible — #252 found the issue's own statement counts had drifted by ~50%
after the shared compiler moved into `_query_common.py`, and nobody noticed
because reading the scope meant opening pyproject.toml and counting by hand.

Statements are counted with coverage's own parser, so the number matches what
coverage reports rather than a hand-rolled AST count.
"""

import pathlib
import sys
import tomllib

from coverage.parser import PythonParser


def statements(path: pathlib.Path) -> int:
    """Executable statements in *path*, as coverage counts them."""
    parser = PythonParser(filename=str(path))
    parser.parse_source()
    return len(parser.statements)


def main() -> int:
    root = pathlib.Path(__file__).resolve().parent.parent
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    entries: list[str] = config["tool"]["mutmut"]["only_mutate"]

    print("only_mutate:")
    total = 0
    for entry in entries:
        path = root / entry
        if not path.is_file():
            print(f"  {entry:<44}   MISSING")
            continue
        count = statements(path)
        total += count
        print(f"  {entry:<44} {count:>5} statements")
    print(f"  {'TOTAL':<44} {total:>5}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
