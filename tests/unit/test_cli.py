"""`sdmxlib bundle check`: the `sdmxlib-cli` workspace member (GH-67).

The command is a thin wrapper over `check_bundle`, so these pin the wrapping:
what it prints, its exit status, and the flag that skips the closure. The
checks themselves are `test_bundle_check.py`'s.
"""

import tomllib
from pathlib import Path

import pytest
from sdmxlib_cli import app
from typer.testing import CliRunner

from sdmxlib.bundle import Producer, write_manifest
from tests.support.bundles import make_bundle
from tests.support.repo import ROOT

pytestmark = pytest.mark.unit

_ROOT = ROOT
_RUNNER = CliRunner()


def _published(root: Path) -> Path:
    write_manifest(make_bundle(root, 10), bundle_id="b", producer=Producer(name="t", version="1"))
    return root


def test_a_clean_bundle_exits_0_and_says_what_it_checked(tmp_path: Path) -> None:
    result = _RUNNER.invoke(app, ["bundle", "check", str(_published(tmp_path / "b")), "--no-references"])
    assert result.exit_code == 0, result.output
    assert result.output.strip().endswith("0 findings; checked manifest, files, lake, bindings")


def test_findings_are_printed_and_exit_1(tmp_path: Path) -> None:
    """The default bundle's DSD names a concept scheme it does not hold, so the
    closure, on by default, finds both concepts."""
    result = _RUNNER.invoke(app, ["bundle", "check", str(_published(tmp_path / "b"))])
    assert result.exit_code == 1
    lines = result.output.strip().splitlines()
    assert sum(line.startswith("[references] ") for line in lines) == 2
    assert lines[-1].endswith("2 findings; checked manifest, files, lake, bindings, references")


def test_an_unpublished_bundle_is_one_manifest_finding(tmp_path: Path) -> None:
    result = _RUNNER.invoke(app, ["bundle", "check", str(make_bundle(tmp_path / "b", 10))])
    assert result.exit_code == 1
    assert result.output.startswith("[manifest] bundle.manifest.json: ")


def test_the_cli_is_locked_to_sdmxlibs_version() -> None:
    """Released together: the CLI's version, and its pin on sdmxlib, are
    sdmxlib's version. The release steps bump all three."""
    library = tomllib.loads((_ROOT / "pyproject.toml").read_text())["project"]
    cli = tomllib.loads((_ROOT / "cli" / "pyproject.toml").read_text())["project"]
    assert cli["version"] == library["version"]
    assert f"sdmxlib=={library['version']}" in cli["dependencies"]
