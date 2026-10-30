"""`check_edition(expect=...)`: a republish that is to change no content, guarded (GH-183).

Design §9.2 rebuilds every serving bundle at a new store schema and stops if
any content moved. The guard compares the new manifest's fingerprints with
the earlier one's, so each test names exactly the findings one change makes.
"""

from pathlib import Path

import msgspec
import pytest
from sdmxlib_cli import app
from typer.testing import CliRunner

import sdmxlib as sl
from sdmxlib.edition import MANIFEST_NAME, Finding, Producer, check_edition, read_manifest, seal
from tests.support.bundles import FLOW, make_bundle, publish

pytestmark = pytest.mark.unit

_PRODUCER = Producer(name="t", version="1")
_RUNNER = CliRunner()


def _republish(first: Path, *, rows: int = 0, add: tuple[sl.Codelist, ...] = ()) -> Path:
    """The next publication of `first`'s bundle, with `rows` more observations and `add` added, published."""
    with sl.LocalRegistry.build(first.parent, previous=first.name) as builder:
        if rows:
            builder._data_plane()  # ATTACHes the lake
            builder.connection.execute(
                "INSERT INTO lake.d.obs SELECT 'G' || (range % 7), range::DOUBLE FROM range(?)", [rows]
            )
        builder.add_all(list(add))
        publication = builder.edition_dir
    seal(publication, product_id=first.parent.name, producer=_PRODUCER)
    return publication


def _expect_findings(publication: Path, expected: Path) -> set[tuple[str, str]]:
    report = check_edition(publication, at="publish", expect=expected)
    assert "expect" in report.checked
    return {(f.subject, f.detail.split(" ", 1)[0]) for f in report.findings if f.check == "expect"}


@pytest.fixture
def first(tmp_path: Path) -> Path:
    return publish(make_bundle(tmp_path / "b", 10))


def test_a_republish_that_changes_nothing_compares_clean(first: Path) -> None:
    second = _republish(first)
    assert read_manifest(second).edition_id != read_manifest(first).edition_id, "two publications, not one read twice"
    for expected in (first, str(first), read_manifest(first)):
        report = check_edition(second, at="publish", expect=expected)
        assert report.ok, "\n".join(map(str, report.findings))
        assert "expect" in report.checked


def test_rows_added_name_the_binding_and_the_bundle(first: Path) -> None:
    second = _republish(first, rows=5)
    assert _expect_findings(second, first) == {(str(FLOW.urn), "fingerprint"), (MANIFEST_NAME, "content_fingerprint")}


def test_an_added_artefact_names_it_and_the_bundle(first: Path) -> None:
    extra = sl.Codelist(id="CL_EXTRA", maintainer="SDMX", version="1.0", name={"en": "Extra"})
    second = _republish(first, add=(extra,))
    assert _expect_findings(second, first) == {
        (str(extra.urn), "in"),
        (MANIFEST_NAME, "content_fingerprint"),
    }


def test_a_finding_names_both_values(first: Path) -> None:
    second = _republish(first, rows=5)
    one, two = read_manifest(first), read_manifest(second)
    (binding,) = (f for f in check_edition(second, at="publish", expect=first).findings if f.subject == str(FLOW.urn))
    assert binding == Finding(
        "expect",
        str(FLOW.urn),
        f"fingerprint {two.bindings[0].fingerprint}, the expected manifest's is {one.bindings[0].fingerprint}",
    )


def test_manifests_at_different_canonical_forms_are_not_compared(first: Path) -> None:
    second = _republish(first, rows=5)
    older = msgspec.structs.replace(read_manifest(first), canonical_form="0")
    findings = [f for f in check_edition(second, at="publish", expect=older).findings if f.check == "expect"]
    assert [(f.subject, f.detail.startswith("canonical_form 1, the expected manifest's is 0")) for f in findings] == [
        (MANIFEST_NAME, True)
    ]


def test_an_expected_manifest_that_cannot_be_read_is_a_finding(first: Path, tmp_path: Path) -> None:
    findings = check_edition(first, at="open", expect=tmp_path / "nowhere").findings
    assert [(f.check, f.subject) for f in findings] == [("expect", MANIFEST_NAME)]
    assert "the expected manifest cannot be read" in findings[0].detail


def test_the_cli_exits_1_when_content_moved(first: Path) -> None:
    unchanged = _republish(first)
    moved = _republish(unchanged, rows=5)
    clean = _RUNNER.invoke(app, ["edition", "check", str(unchanged), "--at", "receive", "--expect", str(first)])
    assert clean.exit_code == 0, clean.output
    assert "expect" in clean.output.splitlines()[-1]
    found = _RUNNER.invoke(app, ["edition", "check", str(moved), "--at", "receive", "--expect", str(first)])
    assert found.exit_code == 1
    assert f"[expect] {FLOW.urn}: fingerprint" in found.output
