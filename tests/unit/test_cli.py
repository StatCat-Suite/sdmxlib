"""`sdmxlib bundle check` and `copy`: the `sdmxlib-cli` workspace member (GH-67, GH-123).

The command is a thin wrapper over `check_bundle`, so these pin the wrapping:
what it prints, its exit status, and the flag that skips the closure. The
checks themselves are `test_bundle_check.py`'s.
"""

import tomllib
from pathlib import Path

import pytest
import typer
from sdmxlib_cli import _flow, _source, app
from typer.testing import CliRunner

import sdmxlib as sl
from sdmxlib.bundle import Producer, check_bundle, write_manifest
from tests.support.bundles import make_bundle
from tests.support.repo import ROOT

pytestmark = pytest.mark.unit

_ROOT = ROOT
_RUNNER = CliRunner()


def _published(root: Path) -> Path:
    publication = make_bundle(root, 10)
    write_manifest(publication, bundle_id="b", producer=Producer(name="t", version="1"))
    return publication


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


def test_copy_writes_a_publication_that_checks_clean(tmp_path: Path) -> None:
    source = _published(tmp_path / "src" / "b")
    destination = tmp_path / "dst" / "b" / source.name
    result = _RUNNER.invoke(app, ["bundle", "copy", str(source), str(destination)])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == f"{destination}: b, token {source.name}; 2 files"
    assert check_bundle(destination, references=False).ok


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


def test_the_cli_ships_sdmxlibs_licence_and_links() -> None:
    """One licence, one set of project links: the CLI's copy of `LICENSE` is
    sdmxlib's, since PEP 639 will not let it point outside `cli/`."""
    library = tomllib.loads((_ROOT / "pyproject.toml").read_text())["project"]
    cli = tomllib.loads((_ROOT / "cli" / "pyproject.toml").read_text())["project"]
    assert (library["license"], library["license-files"]) == ("Apache-2.0", ["LICENSE"])
    assert (cli["license"], cli["license-files"]) == (library["license"], library["license-files"])
    assert (_ROOT / "cli" / "LICENSE").read_bytes() == (_ROOT / "LICENSE").read_bytes()
    assert cli["urls"] == library["urls"]


@pytest.mark.parametrize(
    ("text", "urn"),
    [
        ("ECB:EXR(1.0)", "urn:sdmx:org.sdmx.infomodel.datastructure.Dataflow=ECB:EXR(1.0)"),
        ("ECB:EXR", "urn:sdmx:org.sdmx.infomodel.datastructure.Dataflow=ECB:EXR"),
        (
            "urn:sdmx:org.sdmx.infomodel.datastructure.Dataflow=ECB:EXR(1.0)",
            "urn:sdmx:org.sdmx.infomodel.datastructure.Dataflow=ECB:EXR(1.0)",
        ),
    ],
)
def test_a_flow_is_read_short_or_as_a_urn(text: str, urn: str) -> None:
    assert str(_flow(text)) == urn


def test_a_source_is_a_provider_by_name_or_a_url() -> None:
    assert _source("ecb") is sl.Provider.ECB
    assert _source("https://example.test/rest") == "https://example.test/rest"
    with pytest.raises(typer.BadParameter, match="neither a known provider nor a URL"):
        _source("NOPE")
