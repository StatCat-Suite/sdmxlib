"""Fail when a function declares a class but returns a subclass that offers more.

## Why this exists

`ObsQuery.col()` declared `Col` and returned `Untyped`; `Agg.__gt__` declared
`Pred` and returned `AggCompare`. Both are legal — a subclass is assignable to
its base, so no checker objects — and both hid working features: `.str`'s
`contains`/`starts_with`/`ends_with` and the comparisons (#413), and `having()`
accepting an aggregate comparison (#414). A consumer read the declaration,
concluded the feature was missing, and worked around it.

Neither checker can flag it, because nothing about it is an error: a
declaration wider than the value is the normal way to keep a concrete type
private. What makes it a defect is the *difference*: the returned class adds a
public method, operator or base class the declared one lacks, so the caller is
denied something that is really there.

## What it does

Copies `src/sdmxlib` to a temporary directory and rewrites each `return X` in
an annotated function to `return reveal_type(X)`, in place so lines do not
move. pyrefly then reports the type of every returned value; the copy is its
own import root, so the types are the package's real ones. A
finding is a function whose declared return is one sdmxlib class `D` and whose
returned value is a different sdmxlib class `N`, a subclass of `D`, that adds
public surface or bases.

Unions, aliases, generics and `object` are not judged — the audit that wrote
this found them to be deliberate or false positives (a `T` accessor, an
overload implementation, a type alias the comparison cannot expand).

## Usage

    uv run python tools/check_return_widening.py          # report
    uv run python tools/check_return_widening.py --check  # exit 1 on a finding
"""

import ast
import importlib
import inspect
import json
import pkgutil
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import attrs

import sdmxlib

if TYPE_CHECKING:
    from collections.abc import Iterator

ROOT = Path(__file__).resolve().parent.parent

#: Pairs where the returned class adds only compiler data, never something a
#: caller uses. `Computed` is a mixin carrying `op` / `operands` — the
#: derivation a computed column renders from — and nothing outside `_compile`
#: reads them; `Computed` itself is checked only by `isinstance` there.
ALLOWED: dict[tuple[str, str], str] = {
    ("Num", "NumExpr"): "Computed's op/operands are compiler data",
    ("Str", "StrExpr"): "Computed's op/operands are compiler data",
}

#: Functions allowed one specific widening, keyed `file:function`, each with why
#: no caller loses anything. Per function rather than per class pair, because
#: the same pair elsewhere is exactly the defect: `(Pred, AggCompare)` was #414.
ALLOWED_FUNCTIONS: dict[str, str] = {
    "sdmxlib/model/rollup.py:ancestors": (
        "declares the public SqlRelation rather than the private class behind it (#513); what the class "
        "adds is `rollup`, the AncestorMap the caller called this on"
    ),
    "sdmxlib/query/_rebase.py:_rebased": (
        "rebuilds whichever predicate node it is given, so an AggCompare in is one out; its only "
        "caller passes the result to filter(), which takes Pred — nothing it hides is wanted"
    ),
}

#: Dunders that are API: an operator a caller writes. Other dunders are not.
_OPERATORS = frozenset(
    {
        "__lt__", "__le__", "__gt__", "__ge__", "__add__", "__radd__", "__sub__", "__rsub__",
        "__mul__", "__rmul__", "__truediv__", "__rtruediv__", "__neg__", "__invert__",
        "__and__", "__or__", "__getitem__", "__iter__", "__len__", "__contains__",
    }
)  # fmt: skip


class Finding(NamedTuple):
    """One function whose declaration hides what its returned value offers."""

    file: str
    line: int
    function: str
    declared: str
    returned: str
    adds: tuple[str, ...]

    def __str__(self) -> str:
        return (
            f"{self.file}:{self.line} {self.function} declares {self.declared} but returns "
            f"{self.returned}, which adds {', '.join(self.adds)}"
        )


def _sdmxlib_classes() -> dict[str, list[type]]:
    found: dict[str, list[type]] = {}
    for info in pkgutil.walk_packages(sdmxlib.__path__, "sdmxlib."):
        module = importlib.import_module(info.name)
        for name, obj in vars(module).items():
            if inspect.isclass(obj) and obj.__module__ == module.__name__:
                found.setdefault(name, []).append(obj)
    return found


def _surface(cls: type) -> set[str]:
    fields = {f.name for f in attrs.fields(cls)} if attrs.has(cls) else set()
    return {n for n in dir(cls) if n not in fields and (not n.startswith("_") or n in _OPERATORS)}


def _adds(declared: type, returned: type) -> tuple[str, ...]:
    surface = sorted(_surface(returned) - _surface(declared))
    bases = sorted(
        f"base {b.__name__}"
        for b in returned.__mro__
        if b is not returned and b not in declared.__mro__ and b.__module__.startswith("sdmxlib.")
    )
    return (*surface, *bases)


def _returns(node: ast.AST, declared: str | None, scope: tuple[str, ...]) -> Iterator[tuple[ast.expr, str, str]]:
    """Each returned expression of an annotated, non-overload function: (value, function, declared)."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.ClassDef):
            yield from _returns(child, None, (*scope, child.name))
        elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
            overload = any(
                (isinstance(d, ast.Name) and d.id == "overload")
                or (isinstance(d, ast.Attribute) and d.attr == "overload")
                for d in child.decorator_list
            )
            annotation = ast.unparse(child.returns) if child.returns is not None and not overload else None
            yield from _returns(child, annotation, (*scope, child.name))
        elif isinstance(child, ast.Lambda):
            continue
        elif isinstance(child, ast.Return) and declared is not None and child.value is not None:
            yield child.value, ".".join(scope), declared
        else:
            yield from _returns(child, declared, scope)


def _rewrite(copy: Path) -> dict[tuple[str, int], tuple[str, str]]:
    """Wrap each annotated function's returns in `reveal_type`; map (file, line) to (function, declared)."""
    sites: dict[tuple[str, int], tuple[str, str]] = {}
    for path in sorted(copy.rglob("*.py")):
        rel = str(path.relative_to(copy.parent))
        text = path.read_text()
        edits: list[tuple[int, int, int, int]] = []
        for value, function, declared in _returns(ast.parse(text), None, ()):
            edits.append((value.lineno, value.col_offset, value.end_lineno or value.lineno, value.end_col_offset or 0))
            sites[(rel, value.lineno)] = (function, declared)
        lines = text.splitlines(keepends=True)
        # From the end, so earlier offsets stay valid; in place, so lines do not move.
        for line, col, end_line, end_col in sorted(edits, reverse=True):
            end = lines[end_line - 1]
            lines[end_line - 1] = end[:end_col] + ")" + end[end_col:]
            start = lines[line - 1]
            lines[line - 1] = start[:col] + "reveal_type(" + start[col:]
        path.write_text("".join(lines))
    return sites


def _single_head(type_text: str) -> str | None:
    """The class name of a non-union, non-quoted type, e.g. `Col` from `Col[ObsRow]`."""
    text = type_text.strip().strip("'\"")
    if "|" in text.split("[", 1)[0] or (("|" in text) and text.count("[") == 0):
        return None
    head = text.split("[", 1)[0].split(".")[-1].strip()
    return head if head.isidentifier() else None


def scan(package: Path) -> list[Finding]:
    """Findings for the package directory `package` (a copy of `src/sdmxlib`, or the real one)."""
    classes = _sdmxlib_classes()
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "sdmxlib"
        shutil.copytree(package, copy, ignore=shutil.ignore_patterns("__pycache__"))
        sites = _rewrite(copy)
        # The copy is checked under its own config: an import root of `tmp` so
        # `sdmxlib` resolves to the copy (not `src/`), and the project's
        # interpreter so third-party imports resolve as they do in the gate.
        (Path(tmp) / "pyrefly.toml").write_text(
            'search-path = ["."]\n'
            'project-includes = ["sdmxlib"]\n'
            'python-version = "3.13"\n'
            f'python-interpreter-path = "{ROOT / ".venv" / "bin" / "python"}"\n'
        )
        proc = subprocess.run(
            [str(ROOT / ".venv" / "bin" / "pyrefly"), "check", "--output-format", "json"],
            capture_output=True,
            text=True,
            check=False,
            cwd=tmp,
        )
        try:
            diagnostics = json.loads(proc.stdout)["errors"]
        except (json.JSONDecodeError, KeyError) as exc:
            msg = f"pyrefly produced no JSON:\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
            raise RuntimeError(msg) from exc

        findings: set[Finding] = set()
        for diag in diagnostics:
            if diag.get("name") != "reveal-type":
                continue
            rel = diag["path"]
            site = sites.get((rel, diag["line"]))
            if site is None:
                continue
            function, declared = site
            returned = diag["description"].removeprefix("revealed type:").strip()
            want, got = _single_head(declared), _single_head(returned)
            if want is None or got is None or want == got or (want, got) in ALLOWED:
                continue
            if f"{rel}:{function}" in ALLOWED_FUNCTIONS:
                continue
            for d in classes.get(want, []):
                for n in classes.get(got, []):
                    # A Protocol is matched structurally, which pyrefly has
                    # already checked; `issubclass` refuses one with a data
                    # member (`SqlRelation.columns`), so it is not asked.
                    related = getattr(d, "_is_protocol", False) or issubclass(n, d)
                    if related and (adds := _adds(d, n)):
                        findings.add(Finding(rel, diag["line"], function, declared, returned, adds))
        return sorted(findings)


def main() -> int:
    """Report the findings over `src/sdmxlib`; with `--check`, exit 1 if there are any."""
    findings = scan(ROOT / "src" / "sdmxlib")
    for finding in findings:
        sys.stdout.write(f"{finding}\n")
    if "--check" in sys.argv[1:] and findings:
        sys.stderr.write(
            f"{len(findings)} return declaration(s) hide what the value offers — declare the class "
            f"really returned, or add the pair to ALLOWED with the reason it is compiler detail.\n"
        )
        return 1
    if not findings:
        sys.stdout.write("return declarations: none hides what its value offers\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
