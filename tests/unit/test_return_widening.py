"""Tests for `tools/check_return_widening.py` — declarations that hide what is returned.

`ObsQuery.col()` declared `Col` and returned `Untyped` (#413); `Agg`'s
comparisons declared `Pred` and returned `AggCompare` (#414). Both legal, both
hid working features from every checker and reader. The audit that followed
found two more instances (`text_type`) and judged the rest deliberate; this
keeps it from coming back.

Three checks, per CLAUDE.md's "a guard has to stay able to fail":

- the tree is clean;
- **sabotage** — both original defects, put back into a copy, are flagged, and
  nothing else is, so the guard is known to see the shape it exists for;
- each per-function allowance still names a function that exists, so an
  allowance cannot outlive its subject.

The scan runs pyrefly over a copy of the package, twice here.
"""

from __future__ import annotations

import ast
import importlib.util
import shutil
from pathlib import Path

import pytest

from tests.support.repo import ROOT

pytestmark = pytest.mark.unit

_TOOL = ROOT / "tools" / "check_return_widening.py"
_spec = importlib.util.spec_from_file_location("check_return_widening", _TOOL)
assert _spec is not None
assert _spec.loader is not None
check_return_widening = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_return_widening)

PACKAGE = ROOT / "src" / "sdmxlib"


def test_no_return_declaration_hides_what_its_value_offers() -> None:
    findings = check_return_widening.scan(PACKAGE)
    assert findings == [], "\n".join(str(f) for f in findings)


def _put_back(path: Path, fixed: str, broken: str) -> None:
    text = path.read_text()
    assert text.count(fixed) == 1, f"the sabotage target moved: {fixed!r} in {path.name}"
    path.write_text(text.replace(fixed, broken))


def test_both_original_defects_are_flagged_and_nothing_else(tmp_path: Path) -> None:
    copy = tmp_path / "sdmxlib"
    shutil.copytree(PACKAGE, copy, ignore=shutil.ignore_patterns("__pycache__"))
    _put_back(
        copy / "query" / "observations.py",
        "    def col(self, component_id: str) -> Untyped[ObsRow]:",
        "    def col(self, component_id: str) -> Col[ObsRow]:",
    )
    _put_back(
        copy / "query" / "_expr.py",
        '    def __gt__(self, other: object) -> AggCompare[R]:\n        return _agg_compared(self, ">", other)',
        '    def __gt__(self, other: object) -> Pred[R]:\n        return _agg_compared(self, ">", other)',
    )
    flagged = {(f.file, f.function) for f in check_return_widening.scan(copy)}
    assert flagged == {
        ("sdmxlib/query/observations.py", "ObsQuery.col"),
        ("sdmxlib/query/_expr.py", "Agg.__gt__"),
    }


@pytest.mark.parametrize("key", sorted(check_return_widening.ALLOWED_FUNCTIONS))
def test_each_allowance_still_names_a_function(key: str) -> None:
    rel, function = key.split(":")
    tree = ast.parse((ROOT / "src" / rel).read_text())
    names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)}
    assert function.split(".")[-1] in names, f"{key} is allowed but no longer exists — drop the allowance"
