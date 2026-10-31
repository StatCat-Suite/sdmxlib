"""Every script under `bench/` and `tools/` takes its arguments through typer (GH-326).

`argparse` returns a `Namespace` pyrefly cannot check, so the option a script
declares and the attribute it reads were two strings nothing tied together:
GH-250 left `bench/tier3/bench_tier3.py` reading an attribute it never
declared, past a green `just ci`. typer builds the options from the function's
parameters, so pyrefly checks every use.

Each script's `--help` runs here, so a script that no longer imports fails
`just ci`. The scripts are found from the tree and their count asserted, so a
new one is checked from the commit that adds it.

These run `bench/` and `tools/` from the checkout through `runpy`: mutmut's
`mutants/` copy holds neither, and a test importing from them would abort a
mutation run (`test_count_any.py`).
"""

import functools
import re
import runpy
import sys

import pytest

from tests.support.repo import ROOT

pytestmark = pytest.mark.unit

#: Scripts whose command line is another program's, with the reason.
EXEMPT = {
    "tools/mutmut_forksafe.py": "hands its command line to mutmut's own click CLI",
}

#: bench scripts run by path, as the justfile runs them: each imports a module
#: beside it as a top-level name.
BY_PATH = {"bench/concurrency/bench_concurrency.py"}

_MAIN_GUARD = re.compile(r"^if __name__ == [\"']__main__[\"']:", re.MULTILINE)


def _scripts() -> list[str]:
    found = [
        path.relative_to(ROOT).as_posix()
        for top in ("bench", "tools")
        for path in sorted((ROOT / top).rglob("*.py"))
        if _MAIN_GUARD.search(path.read_text(encoding="utf-8"))
    ]
    return sorted(found)


def _run_help(script: str, monkeypatch: pytest.MonkeyPatch) -> int:
    """Run `script --help` as the justfile runs it, and return its exit code."""
    path = ROOT / script
    monkeypatch.chdir(ROOT)
    monkeypatch.setattr(sys, "argv", [str(path), "--help"])
    if script.startswith("tools/") or script in BY_PATH:
        # Run by path: the script's own directory comes first on sys.path.
        monkeypatch.syspath_prepend(str(path.parent))
        run = functools.partial(runpy.run_path, str(path), run_name="__main__")
    else:
        monkeypatch.syspath_prepend(str(ROOT))
        module = script.removesuffix(".py").removesuffix("/__main__").replace("/", ".")
        # runpy warns when the module was imported already, as an earlier
        # script's run can import a package's __main__.
        monkeypatch.delitem(sys.modules, f"{module}.__main__", raising=False)
        monkeypatch.delitem(sys.modules, module, raising=False)
        run = functools.partial(runpy.run_module, module, run_name="__main__", alter_sys=True)
    with pytest.raises(SystemExit) as exit_info:
        run()
    code = exit_info.value.code
    return code if isinstance(code, int) else 1


def _not_typer(source: str) -> list[str]:
    """What in `source` reads a command line some other way than typer."""
    problems = [name for name in ("argparse", "sys.argv", "getopt") if name in source]
    if "typer.run(" not in source and "typer.Typer(" not in source:
        problems.append("no typer.run or typer.Typer")
    return problems


def test_the_scripts_are_counted() -> None:
    """20 scripts have a `__main__` guard; a new one changes this number on purpose."""
    scripts = _scripts()
    assert len(scripts) == 20, scripts
    assert set(EXEMPT) <= set(scripts), "an exemption names a script that no longer exists"
    assert set(BY_PATH) <= set(scripts), "BY_PATH names a script that no longer exists"


@pytest.mark.parametrize("script", [s for s in _scripts() if s not in EXEMPT])
def test_script_reads_its_command_line_through_typer(script: str) -> None:
    assert _not_typer((ROOT / script).read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("script", [s for s in _scripts() if s not in EXEMPT])
def test_script_help_runs(script: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """`--help` exits 0 with typer's `Usage:`; argparse prints a lowercase `usage:`."""
    code = _run_help(script, monkeypatch)
    out = capsys.readouterr()
    assert code == 0, out.err
    assert "Usage:" in out.out, out.out


def test_the_source_check_catches_argparse() -> None:
    """The source check reports a script parsed by argparse or `sys.argv`, and passes typer."""
    assert _not_typer("import argparse\nparser = argparse.ArgumentParser()\n") == [
        "argparse",
        "no typer.run or typer.Typer",
    ]
    assert _not_typer("import sys\nmain(sys.argv[1:])\n") == ["sys.argv", "no typer.run or typer.Typer"]
    assert _not_typer("import typer\ntyper.run(main)\n") == []
