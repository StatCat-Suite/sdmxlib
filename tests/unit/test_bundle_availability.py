"""Availability stored at publish, and coded columns holding only codes (GH-194).

`write_manifest` scans each binding once: the values each dimension holds
become the stored SDMX 3.1 `AvailabilityConstraint`, and a coded column's
value that is not one of its codes refuses the publication. The census bundle
stored "no value" as ``""`` in three coded attributes, which is the case these
tests write.
"""

from pathlib import Path

import duckdb
import pytest

import sdmxlib as sl
from sdmxlib.bundle import MANIFEST_NAME, Manifest, ManifestError, Producer, check_bundle, write_manifest
from sdmxlib.model.binding import DataflowBinding
from sdmxlib.model.ref import Ref
from tests.support.narrow import present

pytestmark = pytest.mark.unit

_PRODUCER = Producer(name="t", version="1")

CL_GEO = sl.Codelist(
    id="CL_GEO",
    maintainer="T",
    version="1.0",
    name={"en": "Geo"},
    codes=[sl.Code(id=f"G{i}", name={"en": f"G{i}"}) for i in range(8)],
)
CL_NOTE = sl.Codelist(
    id="CL_NOTE", maintainer="T", version="1.0", name={"en": "Note"}, codes=[sl.Code(id="N1", name={"en": "N1"})]
)
CONCEPTS = sl.ConceptScheme(
    id="CS",
    maintainer="T",
    version="1.0",
    name={"en": "C"},
    concepts=[sl.Concept(id=c, name={"en": c}) for c in ("GEO", "NOTE", "OBS_VALUE")],
)


def _concept(id_: str) -> Ref[sl.Concept]:
    return Ref[sl.Concept].unresolved(
        sl.Concept.parse_urn(f"{CONCEPTS.urn}.{id_}".replace("ConceptScheme=", "Concept="))
    )


DSD = sl.DataStructure(
    id="DSD",
    maintainer="T",
    version="1.0",
    name={"en": "DSD"},
    dimensions=[sl.Dimension(id="GEO", concept=_concept("GEO"), representation=Ref.to(CL_GEO))],
    attributes=[
        sl.Attribute(
            id="NOTE",
            attachment=sl.AttachmentLevel.OBSERVATION,
            concept=_concept("NOTE"),
            representation=Ref.to(CL_NOTE),
        )
    ],
    measures=[sl.Measure(id="OBS_VALUE", concept=_concept("OBS_VALUE"))],
)
FLOW = sl.Dataflow(id="F", maintainer="T", version="1.0", name={"en": "F", "fr": "F"}, structure=Ref.to(DSD))


def _publication(root: Path, *, note: str) -> Path:
    """A publication of 70 rows, GEO G0..G6, whose NOTE is ``N1`` on even rows and the SQL `note` on odd ones."""
    root.mkdir(parents=True)
    with sl.LocalRegistry.build(root) as reg:
        reg._data_plane()
        reg.connection.execute("CREATE SCHEMA lake.d")
        reg.connection.execute(
            f"CREATE TABLE lake.d.obs AS SELECT 'G' || (range % 7) AS GEO, "
            f"CASE WHEN range % 2 = 0 THEN 'N1' ELSE {note} END AS NOTE, range::DOUBLE AS OBS_VALUE FROM range(70)"
        )
        reg.add_all([CL_GEO, CL_NOTE, CONCEPTS, DSD, FLOW])
        reg.add(DataflowBinding.from_dsd(DSD, dataflow=Ref.to(FLOW), schema="d", table="obs", database_role="lake"))
        return reg.publication


def _publish(publication: Path, *, availability: bool = True) -> Manifest:
    return write_manifest(publication, bundle_id="b", producer=_PRODUCER, availability=availability)


def test_publishing_stores_the_unfiltered_availability(tmp_path: Path) -> None:
    publication = _publication(tmp_path / "b", note="NULL")
    _publish(publication)
    with sl.LocalRegistry.bundle(publication) as reg:
        stored = present(reg.availability(FLOW.urn))
    assert stored.obs_count == 70
    assert {k.dimension_id: sorted(v.value for v in k.values) for k in stored.region.keys} == {
        "GEO": [f"G{i}" for i in range(7)]
    }
    assert [str(f.urn) for f in stored.attachment.dataflows] == [str(FLOW.urn)]


def test_a_null_in_a_coded_column_is_an_absent_value(tmp_path: Path) -> None:
    """``NULL`` is how a value is absent: it is neither a finding nor a stored value."""
    publication = _publication(tmp_path / "b", note="NULL")
    _publish(publication)
    assert check_bundle(publication, at="publish").ok


def test_an_empty_string_in_a_coded_column_refuses_the_publication(tmp_path: Path) -> None:
    publication = _publication(tmp_path / "b", note="''")
    with pytest.raises(ManifestError) as refused:
        _publish(publication)
    message = str(refused.value)
    assert "1 value(s) in coded columns are not codes" in message
    assert f"NOTE holds '' in 35 rows, not a code of {CL_NOTE.urn}" in message
    assert not (publication / MANIFEST_NAME).exists(), "refused before anything is published"


def test_a_value_outside_the_codelist_is_refused_with_its_row_count(tmp_path: Path) -> None:
    publication = _publication(tmp_path / "b", note="'N9'")
    with pytest.raises(ManifestError, match=r"NOTE holds 'N9' in 35 rows"):
        _publish(publication)


def test_availability_false_stores_none_and_still_checks_codes(tmp_path: Path) -> None:
    clean = _publication(tmp_path / "clean", note="NULL")
    _publish(clean, availability=False)
    with sl.LocalRegistry.bundle(clean) as reg:
        assert reg.availability(FLOW.urn) is None
    refused = _publication(tmp_path / "refused", note="''")
    with pytest.raises(ManifestError, match="not codes"):
        _publish(refused, availability=False)


def test_the_publish_check_compares_the_stored_availability_with_the_data(tmp_path: Path) -> None:
    publication = _publication(tmp_path / "b", note="NULL")
    _publish(publication)
    report = check_bundle(publication, at="publish")
    assert report.ok, "\n".join(map(str, report.findings))
    assert "availability" in report.checked
    with duckdb.connect(str(publication / "meta.duckdb")) as conn:
        conn.execute("DELETE FROM sdmx.availability_value WHERE value = 'G3'")
        conn.execute("UPDATE sdmx.availability SET obs_count = 69")
    findings = [f for f in check_bundle(publication, at="publish").findings if f.check == "availability"]
    assert sorted(f.detail for f in findings) == [
        "stores 6 values of GEO, the data holds 7; 0 stored and absent, 1 held and not stored",
        "stores 69 observations, the data holds 70",
    ]


def test_the_availability_check_runs_only_at_publish(tmp_path: Path) -> None:
    """It rescans every row; receive and open check sizes and digests, not content."""
    publication = _publication(tmp_path / "b", note="NULL")
    _publish(publication)
    assert "availability" not in check_bundle(publication, at="receive").checked
    assert "availability" not in check_bundle(publication, at="open").checked


def test_a_publication_extending_another_holds_only_its_own_availability(tmp_path: Path) -> None:
    """`build(previous=)` copies the store, availability included; `availability=False` stores none (GH-203)."""
    first = _publication(tmp_path / "b", note="NULL")
    _publish(first)
    with sl.LocalRegistry.build(first.parent, previous=first.name) as builder:
        second = builder.publication
    _publish(second, availability=False)
    with sl.LocalRegistry.bundle(second) as reg:
        assert reg.availability(FLOW.urn) is None
    with sl.LocalRegistry.bundle(first) as reg:
        assert reg.availability(FLOW.urn) is not None, "the first publication keeps its own"
