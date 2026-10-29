"""`sdmxlib bundle check`, `copy` and `files`: the `sdmxlib-cli` workspace member (GH-67, GH-123, GH-182).

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
from sdmxlib.bundle import MANIFEST_NAME, Producer, bundle_files, check_bundle, read_manifest, write_manifest
from tests.support.bundles import make_bundle
from tests.support.repo import ROOT

pytestmark = pytest.mark.unit

_ROOT = ROOT
_RUNNER = CliRunner()


def _published(root: Path, *, closed: bool = True) -> Path:
    publication = make_bundle(root, 10, closed=closed)
    write_manifest(publication, bundle_id="b", producer=Producer(name="t", version="1"))
    return publication


def test_a_clean_bundle_exits_0_and_says_what_it_checked(tmp_path: Path) -> None:
    result = _RUNNER.invoke(app, ["bundle", "check", str(_published(tmp_path / "b")), "--at", "receive"])
    assert result.exit_code == 0, result.output
    assert result.output.strip().endswith("0 findings; checked manifest, files, lake, bindings, references")


def test_findings_are_printed_and_exit_1(tmp_path: Path) -> None:
    """Without its concept scheme the bundle's DSD names two concepts it does not hold."""
    result = _RUNNER.invoke(app, ["bundle", "check", str(_published(tmp_path / "b", closed=False)), "--at", "publish"])
    assert result.exit_code == 1
    lines = result.output.strip().splitlines()
    assert sum(line.startswith("[references] ") for line in lines) == 2
    checked = "manifest, files, lake, bindings, references, fingerprints, availability"
    assert lines[-1].endswith(f"2 findings; checked {checked}")


def test_the_profile_is_required(tmp_path: Path) -> None:
    result = _RUNNER.invoke(app, ["bundle", "check", str(_published(tmp_path / "b"))])
    assert result.exit_code == 2
    assert "--at" in result.output


def test_copy_writes_a_publication_that_checks_clean(tmp_path: Path) -> None:
    source = _published(tmp_path / "src" / "b")
    destination = tmp_path / "dst" / "b" / source.name
    result = _RUNNER.invoke(app, ["bundle", "copy", str(source), str(destination)])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == f"{destination}: b, token {source.name}; 3 files"
    assert check_bundle(destination, at="publish").ok


def test_copy_takes_the_tiers_to_copy(tmp_path: Path) -> None:
    """A table this size has a data file, tier 3, which `--tiers 1,2` leaves out."""
    source = make_bundle(tmp_path / "src" / "b", 200_000)
    write_manifest(source, bundle_id="b", producer=Producer(name="t", version="1"))
    assert any((tmp_path / "src" / "b" / "data").rglob("*.parquet"))
    destination = tmp_path / "dst" / "b" / source.name
    result = _RUNNER.invoke(app, ["bundle", "copy", str(source), str(destination), "--tiers", "1, 2"])
    assert result.exit_code == 0, result.output
    assert sorted(p.name for p in destination.iterdir()) == ["bundle.manifest.json", "lake.ducklake", "meta.duckdb"]
    assert not (tmp_path / "dst" / "b" / "data").exists()


def test_files_prints_the_listing_one_path_per_line(tmp_path: Path) -> None:
    source = make_bundle(tmp_path / "b", 200_000)
    write_manifest(source, bundle_id="b", producer=Producer(name="t", version="1"))
    result = _RUNNER.invoke(app, ["bundle", "files", str(source), "--tiers", "1,2"])
    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == bundle_files(read_manifest(source), tiers=(1, 2))
    everything = _RUNNER.invoke(app, ["bundle", "files", str(source)]).output.splitlines()
    assert any(line.startswith("data/") for line in everything)
    assert not any(line.startswith("data/") for line in result.output.splitlines())


def test_files_manifest_prints_the_manifest_path_alone(tmp_path: Path) -> None:
    source = _published(tmp_path / "b")
    result = _RUNNER.invoke(app, ["bundle", "files", str(source), "--manifest"])
    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [f"{source.name}/{MANIFEST_NAME}"]


def test_files_reads_a_manifest_staged_outside_its_publication(tmp_path: Path) -> None:
    source = _published(tmp_path / "b")
    staged = tmp_path / "stage" / MANIFEST_NAME
    staged.parent.mkdir()
    staged.write_bytes((source / MANIFEST_NAME).read_bytes())
    for given in (staged, staged.parent):
        result = _RUNNER.invoke(app, ["bundle", "files", str(given)])
        assert result.exit_code == 0, result.output
        assert result.output.splitlines() == bundle_files(read_manifest(source))


def test_files_refuses_a_tier_that_is_not_one(tmp_path: Path) -> None:
    result = _RUNNER.invoke(app, ["bundle", "files", str(_published(tmp_path / "b")), "--tiers", "0"])
    assert result.exit_code == 2
    assert "'0' is not a tier; give 1, 2 or 3" in result.output


def test_copy_refuses_a_tier_that_is_not_one(tmp_path: Path) -> None:
    source = _published(tmp_path / "src" / "b")
    result = _RUNNER.invoke(app, ["bundle", "copy", str(source), str(tmp_path / "dst"), "--tiers", "1,4"])
    assert result.exit_code == 2
    assert "'4' is not a tier; give 1, 2 or 3" in result.output
    assert not (tmp_path / "dst").exists()


def test_an_unpublished_bundle_is_one_manifest_finding(tmp_path: Path) -> None:
    result = _RUNNER.invoke(app, ["bundle", "check", str(make_bundle(tmp_path / "b", 10)), "--at", "open"])
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


@pytest.mark.parametrize(
    "text",
    [
        "urn:junk",
        "EC B:X",
        "ECB:X(bad v)",
        "ECB:X(1.0)junk",
        "urn:sdmx:org.sdmx.infomodel.datastructure.Dataflow=ECB:X(bad v)",
        ":X",
        "ECB:",
    ],
)
def test_a_malformed_flow_is_a_usage_error_naming_the_forms(text: str) -> None:
    """GH-265: the library's parse errors and an invalid version become
    typer's `BadParameter`, so the CLI prints usage rather than a traceback."""
    with pytest.raises(typer.BadParameter, match=r"is not AGENCY:ID\(VERSION\), AGENCY:ID or a dataflow URN"):
        _flow(text)


def test_a_source_is_a_provider_by_name_or_a_url() -> None:
    assert _source("ecb") is sl.Provider.ECB
    assert _source("https://example.test/rest") == "https://example.test/rest"
    with pytest.raises(typer.BadParameter, match="neither a known provider nor a URL"):
        _source("NOPE")
