"""What mutation found the SDMX-ML 3.x writer's tests could not see (#458).

The triage of the rest — equivalent, and why — is in
`docs/design/mutation-evidence.md`.

As with the 2.1 writer (installment 5), most of these are what only the
document holds: the envelope, which no reader returns, and the metadata
back-links' placement and text. Those are asserted on the document.
"""

from __future__ import annotations

import attrs
import pytest
from lxml import etree

import sdmxlib as sl
from sdmxlib.formats.gaps import without_dropped
from sdmxlib.formats.sdmx_ml30 import writer as ml30_writer
from sdmxlib.formats.sdmx_ml31 import writer as ml31_writer
from sdmxlib.model.constraint import ConstraintType
from sdmxlib.model.message import StructureMessage
from tests.support import maximal, shapes
from tests.support.narrow import present
from tests.support.roundtrip import as_structure

pytestmark = pytest.mark.unit

_WRITERS = {"3.0": ml30_writer, "3.1": ml31_writer}
_NS = {
    "3.0": "http://www.sdmx.org/resources/sdmxml/schemas/v3_0/",
    "3.1": "http://www.sdmx.org/resources/sdmxml/schemas/v3_1/",
}


def _message(version: str) -> StructureMessage:
    return without_dropped(shapes.message_31() if version == "3.1" else shapes.message(), version)


# ── the document around the structures ───────────────────────────────────


@pytest.mark.parametrize("version", sorted(_WRITERS))
def test_the_document_declares_itself_utf_8(version: str) -> None:
    """lxml leaves the declaration out of a UTF-8 document unless asked."""
    written = _WRITERS[version].write(_message(version))
    assert written.startswith(b"<?xml version='1.0' encoding='UTF-8'?>\n<mes:Structure ")


@pytest.mark.parametrize("version", sorted(_WRITERS))
def test_a_written_document_is_one_line_unless_pretty_printed(version: str) -> None:
    """The declaration ends a line of its own either way, so ``\\n in`` cannot tell."""
    writer, msg = _WRITERS[version], _message(version)
    plain, pretty = writer.write(msg), writer.write(msg, pretty_print=True)
    assert plain.count(b"\n") == 1
    assert pretty.count(b"\n") > 20, "a floor: the fixture grows"
    # `serialize` calls this one, always saying which; its own default agrees.
    assert writer.write_kept(msg) == plain


@pytest.mark.parametrize("version", sorted(_WRITERS))
def test_the_envelope_is_the_one_sdmxlib_writes(version: str) -> None:
    root = etree.fromstring(_WRITERS[version].write(_message(version)))
    base = _NS[version]
    assert root.nsmap == {"mes": f"{base}message", "str": f"{base}structure", "com": f"{base}common"}
    header = present(root.find(f"{{{base}message}}Header"))
    assert [(etree.QName(el).localname, el.text, dict(el.attrib)) for el in header] == [
        ("ID", "SDMX_MSG", {}),
        ("Test", "false", {}),
        ("Prepared", "2000-01-01T00:00:00", {}),
        ("Sender", None, {"id": "sdmxlib"}),
    ]


# ── the metadata back-links ───────────────────────────────────────────────


def _set_urn(set_id: str) -> str:
    return f"urn:sdmx:org.sdmx.infomodel.metadatastructure.MetadataSet=AG:{set_id}(1.0)"


def _linked(flow: sl.Dataflow, base_url: str, *sets: str) -> etree._Element:
    out = ml30_writer.write(
        StructureMessage(dataflows=[flow]),
        metadata_links={str(flow.urn): [_set_urn(s) for s in sets]},
        base_url=base_url,
    )
    (el,) = etree.fromstring(out).iter("{*}Dataflow")
    return el


def _flow(*, annotations: list[sl.Annotation] | None = None, links: tuple[sl.Link, ...] = ()) -> sl.Dataflow:
    return sl.Dataflow(
        id="DF", maintainer="AG", version="1.0", name={"en": "df"}, annotations=annotations or [], links=links
    )


@pytest.mark.parametrize("base", ["https://h/sdmx/v2/", "https://h/sdmx/v2", "https://h/APIX/"])
def test_a_back_link_names_the_report_by_url_and_urn(base: str) -> None:
    """FMR's shape: `rel`, `url` and `urn`, from a base with its one trailing slash trimmed."""
    el = _linked(_flow(), base, "MS1")
    (link,) = el.iter("{*}Link")
    assert dict(link.attrib) == {
        "rel": "metadata",
        "url": f"{base.rstrip('/')}/metadata/metadataset/AG/MS1/1.0",
        "urn": _set_urn("MS1"),
    }


def test_back_links_go_after_the_annotations_and_held_links_in_order() -> None:
    """`MaintainableBaseType`: Annotations, then Links, then Name."""
    held = sl.Link(rel="alternate", url="https://example.org/df")
    flow = _flow(annotations=[sl.Annotation(id="A", title="a")], links=(held,))
    el = _linked(flow, "https://h", "MS1", "MS2")
    shape = [(etree.QName(child).localname, child.get("rel"), child.get("urn")) for child in el][:5]
    assert shape == [
        ("Annotations", None, None),
        ("Link", "alternate", None),
        ("Link", "metadata", _set_urn("MS1")),
        ("Link", "metadata", _set_urn("MS2")),
        ("Name", None, None),
    ]


def test_back_links_on_an_unannotated_artefact_come_first() -> None:
    el = _linked(_flow(), "https://h", "MS1", "MS2")
    assert [etree.QName(child).localname for child in el][:3] == ["Link", "Link", "Name"]


def test_back_links_without_a_base_are_refused() -> None:
    flow = _flow()
    with pytest.raises(ValueError, match=r"^base_url is required when metadata_links is provided$"):
        ml30_writer.write(StructureMessage(dataflows=[flow]), metadata_links={str(flow.urn): [_set_urn("MS1")]})


def test_back_links_skip_an_availability_constraint() -> None:
    """It has no identity to be linked by; asking for its URN is a crash (#421)."""
    msg = _message("3.1")
    assert msg.availability_constraints, "the case lost its subject"
    flow = msg.dataflows[0]
    out = ml31_writer.write(msg, metadata_links={str(flow.urn): [_set_urn("MS1")]}, base_url="https://h")
    (el,) = (d for d in etree.fromstring(out).iter("{*}Dataflow") if d.get("id") == flow.id)
    assert [link.get("urn") for link in el.iter("{*}Link") if link.get("rel") == "metadata"] == [_set_urn("MS1")]


# ── a metadata structure ──────────────────────────────────────────────────


@pytest.mark.parametrize("version", sorted(_WRITERS))
def test_a_metadata_attributes_occurrence_bounds_of_two_are_written(version: str) -> None:
    """``1`` is the default and left out; anything else is stated."""
    full = maximal.message()
    msd = full.metadata_structures[0]
    first, *rest = msd.attributes
    bounded = attrs.evolve(msd, attributes=[attrs.evolve(first, min_occurs=2, max_occurs=2), *rest])
    msg = StructureMessage(concept_schemes=full.concept_schemes, metadata_structures=[bounded])
    (back,) = as_structure(sl.parse(_WRITERS[version].write(msg))).metadata_structures
    assert (back.attributes[first.id].min_occurs, back.attributes[first.id].max_occurs) == (2, 2)


# ── 3.1's availability constraint ─────────────────────────────────────────


def test_an_actual_constraint_that_misfits_stays_a_data_constraint() -> None:
    """Actual is not enough: two regions have no availability form (#421)."""
    full = _message("3.1")
    actual = attrs.evolve(full.constraints[0], constraint_type=ConstraintType.ACTUAL)
    assert actual.availability_misfits() == [], "the case lost its subject"
    split = attrs.evolve(actual, regions=[*actual.regions, *actual.regions])
    assert split.availability_misfits(), "the case lost its subject"
    for dc, as_availability in ((actual, True), (split, False)):
        msg = attrs.evolve(full, constraints=sl.ArtefactList([dc]), availability_constraints=sl.ArtefactList([]))
        back = as_structure(sl.parse(ml31_writer.write(msg)))
        assert (len(back.availability_constraints), len(back.constraints)) == ((1, 0) if as_availability else (0, 1))
