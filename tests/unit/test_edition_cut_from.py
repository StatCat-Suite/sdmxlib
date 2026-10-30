"""A partial's `partial_of`, recorded by `seal` and checked against its full artefact (GH-224).

A product bundle vendors a partial codelist; a structures bundle holds the
full one. The partial's manifest entry records the full one's fingerprint, so a
catalog can tell which full artefact it was cut from without its codes, and
`check_edition(structures=...)` confirms the cut against that artefact.
"""

import json
from pathlib import Path

import pytest

import sdmxlib as sl
from sdmxlib.edition import MANIFEST_NAME, ManifestError, Producer, Report, check_edition, read_manifest, seal
from tests.support.bundles import make_bundle

pytestmark = pytest.mark.unit

_PRODUCER = Producer(name="test-builder", version="1.0")

FULL = sl.Codelist(
    id="CL_X",
    maintainer="T",
    version="1.0",
    name={"en": "x"},
    codes=[
        sl.Code(id="A", name={"en": "A"}),
        sl.Code(id="A1", name={"en": "A1"}, parent_id="A"),
        sl.Code(id="A2", name={"en": "A2"}, parent_id="A"),
        sl.Code(id="B", name={"en": "B"}),
    ],
)
URN = str(FULL.urn)


def _structures(root: Path, full: sl.Codelist = FULL) -> tuple[Path, str]:
    """A published publication holding `full`, and the fingerprint its manifest records for it."""
    publication = make_bundle(root, 10, extra=(full,))
    manifest = seal(publication, product_id="structures", producer=_PRODUCER)
    (entry,) = (a for a in manifest.artefacts if a.urn == URN)
    return publication, entry.fingerprint


def _product(root: Path, partial: sl.Codelist, partial_of: dict[str, str] | None) -> Path:
    publication = make_bundle(root, 10, extra=(partial,))
    seal(publication, product_id="product", producer=_PRODUCER, partial_of=partial_of)
    return publication


def _partials(report: Report) -> list[tuple[str, str]]:
    return [(f.subject, f.detail) for f in report.findings if f.check == "partials"]


def test_write_manifest_records_cut_from_on_the_partial_alone(tmp_path: Path) -> None:
    _, fingerprint = _structures(tmp_path / "s")
    product = _product(tmp_path / "p", FULL.partial(["A1"]), {URN: fingerprint})

    manifest = read_manifest(product)
    assert manifest.format_version == "3.0"
    entries = {a.urn: a for a in manifest.artefacts}
    assert entries[URN].partial_of == fingerprint
    assert {a.partial_of for urn, a in entries.items() if urn != URN} == {None}
    written = json.loads((product / MANIFEST_NAME).read_text())["artefacts"]
    assert ["partial_of" in a for a in written] == [a["urn"] == URN for a in written], "absent for a full artefact"


def test_cut_from_naming_no_partial_is_refused(tmp_path: Path) -> None:
    publication = make_bundle(tmp_path / "p", 10, extra=(FULL,))
    with pytest.raises(ManifestError, match=r"partial_of names .*CL_X\(1\.0\), which the store holds as no partial"):
        seal(publication, product_id="p", producer=_PRODUCER, partial_of={URN: "0" * 64})
    assert not (publication / MANIFEST_NAME).exists()


def test_a_partial_cut_from_its_full_artefact_checks_clean(tmp_path: Path) -> None:
    structures, fingerprint = _structures(tmp_path / "s")
    product = _product(tmp_path / "p", FULL.partial(["A1", "B"]), {URN: fingerprint})

    report = check_edition(product, at="publish", structures=structures)

    assert report.ok, "\n".join(map(str, report.findings))
    assert "partials" in report.checked


def test_a_code_the_full_artefact_lacks_is_named(tmp_path: Path) -> None:
    structures, fingerprint = _structures(tmp_path / "s")
    extra = sl.Codelist(
        id="CL_X",
        maintainer="T",
        version="1.0",
        name={"en": "x"},
        is_partial=True,
        codes=[sl.Code(id="A", name={"en": "A"}), sl.Code(id="C", name={"en": "C"})],
    )
    product = _product(tmp_path / "p", extra, {URN: fingerprint})

    report = check_edition(product, at="publish", structures=structures)

    assert _partials(report) == [(URN, "C is not in the full artefact")]


def test_a_code_under_another_parent_is_named(tmp_path: Path) -> None:
    structures, fingerprint = _structures(tmp_path / "s")
    moved = sl.Codelist(
        id="CL_X",
        maintainer="T",
        version="1.0",
        name={"en": "x"},
        is_partial=True,
        codes=[sl.Code(id="A1", name={"en": "A1"}, parent_id="B"), sl.Code(id="B", name={"en": "B"})],
    )
    product = _product(tmp_path / "p", moved, {URN: fingerprint})

    report = check_edition(product, at="publish", structures=structures)

    assert _partials(report) == [(URN, "A1 is under B here and under A in the full artefact")]


def test_a_partial_cut_from_another_version_of_the_full_artefact_is_named(tmp_path: Path) -> None:
    """The full codelist was edited at the same URN after the cut: the fingerprints differ."""
    _, before = _structures(tmp_path / "old")
    edited = sl.Codelist(id="CL_X", maintainer="T", version="1.0", name={"en": "x, edited"}, codes=list(FULL.codes))
    structures, after = _structures(tmp_path / "new", edited)
    assert before != after
    product = _product(tmp_path / "p", FULL.partial(["A1"]), {URN: before})

    report = check_edition(product, at="publish", structures=structures)

    assert _partials(report) == [(URN, f"cut from {before}, and the full artefact in {structures} is {after}")]


def test_a_structures_publication_without_the_artefact_is_named(tmp_path: Path) -> None:
    _, fingerprint = _structures(tmp_path / "s")
    empty = make_bundle(tmp_path / "empty", 10)
    seal(empty, product_id="empty", producer=_PRODUCER)
    product = _product(tmp_path / "p", FULL.partial(["A1"]), {URN: fingerprint})

    report = check_edition(product, at="publish", structures=empty)

    assert _partials(report) == [(URN, f"{empty} holds no full artefact at this URN")]


def test_without_structures_the_partials_check_does_not_run(tmp_path: Path) -> None:
    _, fingerprint = _structures(tmp_path / "s")
    product = _product(tmp_path / "p", FULL.partial(["A1"]), {URN: fingerprint})

    assert "partials" not in check_edition(product, at="publish").checked


def test_structures_is_refused_at_open(tmp_path: Path) -> None:
    structures, fingerprint = _structures(tmp_path / "s")
    product = _product(tmp_path / "p", FULL.partial(["A1"]), {URN: fingerprint})

    with pytest.raises(ValueError, match="at='open' reads no artefact"):
        check_edition(product, at="open", structures=structures)


def test_a_2_x_manifest_is_refused_naming_the_rebuild(tmp_path: Path) -> None:
    """Manifest 3.0 renamed keys and files with no migrator: a 2.x one is refused, naming the re-ingest."""
    _, fingerprint = _structures(tmp_path / "s")
    product = _product(tmp_path / "p", FULL.partial(["A1"]), {URN: fingerprint})
    path = product / MANIFEST_NAME
    raw = json.loads(path.read_text())
    raw["format_version"] = "2.1"
    path.write_text(json.dumps(raw))

    with pytest.raises(ManifestError, match=r"manifest format 2\.1; this sdmxlib reads 3\.x.*re-ingesting"):
        read_manifest(product)
