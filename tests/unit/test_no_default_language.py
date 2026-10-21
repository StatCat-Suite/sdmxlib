"""No API in `src/` chooses a language for the caller.

Every multilingual call takes `lang` with no default — there is no `"en"`
fallback anywhere. `write_sdmx_json_data` defaulted it to `"en"` in both
SDMX-JSON versions until #509, and nothing noticed, because the rule lived
only in prose. This reads every function in `src/` and fails on a parameter
named for a language that defaults to a string.

`None` is not a language: `Dataset.collect(lang=None)` means "no labels",
which is a different thing from choosing one.
"""

import ast

import pytest

from tests.support.repo import ROOT

pytestmark = pytest.mark.unit

_LANGUAGE_PARAMETERS = frozenset({"lang", "language", "langs", "languages"})


def _defaulted_languages(source: str, name: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        args = node.args
        positional = [*args.posonlyargs, *args.args]
        pairs = [
            *zip(positional[len(positional) - len(args.defaults) :], args.defaults, strict=True),
            *((arg, default) for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True) if default),
        ]
        found.extend(
            f"{name}:{node.lineno} {node.name}({arg.arg}=…)"
            for arg, default in pairs
            if arg.arg in _LANGUAGE_PARAMETERS and isinstance(default, ast.Constant) and isinstance(default.value, str)
        )
    return found


def test_no_function_defaults_a_language() -> None:
    found = [
        hit
        for path in sorted((ROOT / "src").rglob("*.py"))
        for hit in _defaulted_languages(path.read_text(encoding="utf-8"), str(path.relative_to(ROOT)))
    ]
    assert found == []


def test_the_scan_sees_one() -> None:
    """The sabotage check: both parameter kinds, and `None` let through."""
    source = (
        'def a(x, lang="en"): ...\n'
        'def b(*, lang: str = "fr"): ...\n'
        "def c(*, lang: str | None = None): ...\n"
        "def d(lang: str): ...\n"
    )
    assert [hit.split()[1] for hit in _defaulted_languages(source, "s")] == ["a(lang=…)", "b(lang=…)"]
