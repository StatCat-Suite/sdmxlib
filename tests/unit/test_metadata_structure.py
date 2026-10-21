"""Tests for MetadataStructure (MSD) — model + storage round-trip + JSON round-trip.

MetadataSet tests live in test_metadata_set.py / test_sdmx_metadata_messages.py;
the populated payload travels in a different message envelope (GenericMetadata)
and shouldn't share fixtures with the structures tests.
"""

import pytest

from sdmxlib.formats.sdmx_json.reader import read as read_json
from sdmxlib.formats.sdmx_json.writer import write as write_json
from sdmxlib.model.concept import Concept, ConceptScheme
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.metadatastructure import MetadataAttribute, MetadataStructure
from sdmxlib.model.ref import Ref
from sdmxlib.model.representation import DataType, Facet
from sdmxlib.storage.readers import get_artefact
from sdmxlib.storage.writers import put_artefact
from tests.support.factories import concept_ref, memory_cursor

pytestmark = pytest.mark.unit


def _make_concept_scheme() -> ConceptScheme:
    return ConceptScheme(
        id="CS_FOOTNOTES",
        maintainer="STATCAN",
        version="1.0",
        name={"en": "Footnote concepts"},
        concepts=[Concept(id="FOOTNOTE_TEXT", name={"en": "Footnote text"})],
    )


def _make_msd() -> MetadataStructure:
    concept_urn = Concept.urn_for(agency="STATCAN", scheme="CS_FOOTNOTES", id="FOOTNOTE_TEXT")
    return MetadataStructure(
        id="MSD_FOOTNOTES",
        maintainer="STATCAN",
        version="1.0",
        name={"en": "Footnote MSD"},
        attributes=[
            MetadataAttribute(
                id="FOOTNOTE_TEXT",
                concept=concept_urn,
                representation=Facet(type=DataType.String),
                min_occurs=0,
                max_occurs=1,
            )
        ],
    )


# ── Model construction ──────────────────────────────────────────────────


def test_metadata_attribute_defaults() -> None:
    a = MetadataAttribute(id="X")
    assert a.id == "X"
    assert a.concept is None
    assert a.representation is None
    assert a.min_occurs == 0
    assert a.max_occurs == 1
    assert a.is_presentational is False
    assert len(a.children) == 0


def test_metadata_attribute_unbounded() -> None:
    a = MetadataAttribute(id="X", max_occurs=None, concept=concept_ref("X"))
    assert a.max_occurs is None


def test_metadata_attribute_recursive_children() -> None:
    leaf = MetadataAttribute(id="LEAF", concept=concept_ref("LEAF"))
    container = MetadataAttribute(id="QUALITY", is_presentational=True, children=[leaf], concept=concept_ref("QUALITY"))
    assert container.children[0] is leaf
    assert container.children["LEAF"] is leaf


def test_metadata_structure_attributes_addressable_by_id() -> None:
    msd = _make_msd()
    assert msd.attributes["FOOTNOTE_TEXT"].id == "FOOTNOTE_TEXT"


def test_metadata_structure_add_drop() -> None:
    msd = _make_msd()
    msd2 = msd.add(MetadataAttribute(id="NEW", concept=concept_ref("NEW")))
    assert "NEW" in msd2.attributes
    assert "NEW" not in msd.attributes
    msd3 = msd2.drop("NEW")
    assert "NEW" not in msd3.attributes
    with pytest.raises(KeyError):
        msd2.drop("DOES_NOT_EXIST")


def _nested_msd() -> MetadataStructure:
    return MetadataStructure(
        name={"en": "MSD_NESTED"},
        id="MSD_NESTED",
        maintainer="TEST",
        attributes=[
            MetadataAttribute(
                id="CONTACT",
                children=[
                    MetadataAttribute(id="NAME", concept=concept_ref("NAME")),
                    MetadataAttribute(id="EMAIL", concept=concept_ref("EMAIL")),
                    MetadataAttribute(
                        id="ADDRESS",
                        children=[
                            MetadataAttribute(id="STREET", concept=concept_ref("STREET")),
                            MetadataAttribute(id="CITY", concept=concept_ref("CITY")),
                        ],
                        concept=concept_ref("ADDRESS"),
                    ),
                ],
                concept=concept_ref("CONTACT"),
            ),
            MetadataAttribute(id="FOOTNOTE", concept=concept_ref("FOOTNOTE")),
        ],
    )


def test_msd_skos_verbs() -> None:
    msd = _nested_msd()
    assert [a.id for a in msd.roots()] == ["CONTACT", "FOOTNOTE"]
    assert {a.id for a in msd.leaves()} == {"NAME", "EMAIL", "STREET", "CITY", "FOOTNOTE"}
    assert [a.id for a in msd.narrower("CONTACT")] == ["NAME", "EMAIL", "ADDRESS"]
    parent = msd.broader("CONTACT.ADDRESS.STREET")
    assert parent is not None
    assert parent.id == "ADDRESS"
    assert msd.broader("CONTACT") is None
    assert [a.id for a in msd.broader_transitive("CONTACT.ADDRESS.STREET")] == ["CONTACT", "ADDRESS"]
    assert {a.id for a in msd.narrower_transitive("CONTACT")} == {"NAME", "EMAIL", "ADDRESS", "STREET", "CITY"}
    assert msd.depth_of("CONTACT.ADDRESS.STREET") == 2
    assert msd["CONTACT.ADDRESS.STREET"].id == "STREET"


def test_msd_bulk_maps() -> None:
    msd = _nested_msd()
    assert msd.depths() == {
        "CONTACT": 0,
        "FOOTNOTE": 0,
        "CONTACT.NAME": 1,
        "CONTACT.EMAIL": 1,
        "CONTACT.ADDRESS": 1,
        "CONTACT.ADDRESS.STREET": 2,
        "CONTACT.ADDRESS.CITY": 2,
    }
    assert msd.broader_map()["CONTACT.ADDRESS.STREET"] == "CONTACT.ADDRESS"
    assert msd.narrower_map()["CONTACT"] == ("CONTACT.NAME", "CONTACT.EMAIL", "CONTACT.ADDRESS")
    assert msd.ancestors_map()["CONTACT.ADDRESS.STREET"] == ("CONTACT", "CONTACT.ADDRESS")
    assert msd.descendants_map()["CONTACT"] == (
        "CONTACT.NAME",
        "CONTACT.EMAIL",
        "CONTACT.ADDRESS",
        "CONTACT.ADDRESS.STREET",
        "CONTACT.ADDRESS.CITY",
    )
    assert msd.inherit_along_broader({"CONTACT": "contact-info"}) == {
        "CONTACT": "contact-info",
        "CONTACT.NAME": "contact-info",
        "CONTACT.EMAIL": "contact-info",
        "CONTACT.ADDRESS": "contact-info",
        "CONTACT.ADDRESS.STREET": "contact-info",
        "CONTACT.ADDRESS.CITY": "contact-info",
    }


# ── Storage round-trip ──────────────────────────────────────────────────


def test_msd_round_trip_preserves_attribute_metadata() -> None:
    cur = memory_cursor()
    put_artefact(cur, _make_msd())

    urn = "urn:sdmx:org.sdmx.infomodel.metadatastructure.MetadataStructure=STATCAN:MSD_FOOTNOTES(1.0)"
    out = get_artefact(cur, urn)

    assert isinstance(out, MetadataStructure)
    assert out.id == "MSD_FOOTNOTES"
    attr = out.attributes["FOOTNOTE_TEXT"]
    assert attr.min_occurs == 0
    assert attr.max_occurs == 1
    assert isinstance(attr.representation, Facet)
    assert attr.representation.type == "String"
    assert isinstance(attr.concept, Ref)
    assert attr.concept.urn.item_id == "FOOTNOTE_TEXT"


def test_msd_round_trip_preserves_attribute_hierarchy() -> None:
    cur = memory_cursor()
    msd = MetadataStructure(
        name={"en": "MSD_TREE"},
        id="MSD_TREE",
        maintainer="TEST",
        attributes=[
            MetadataAttribute(
                id="QUALITY",
                concept=concept_ref("QUALITY"),
                is_presentational=True,
                children=[
                    MetadataAttribute(
                        id="COVERAGE", concept=concept_ref("COVERAGE"), representation=Facet(type=DataType.String)
                    ),
                    MetadataAttribute(
                        id="ACCURACY",
                        concept=concept_ref("ACCURACY"),
                        is_presentational=True,
                        children=[
                            MetadataAttribute(
                                id="SAMPLING_ERROR",
                                concept=concept_ref("SAMPLING_ERROR"),
                                representation=Facet(type=DataType.Decimal),
                            ),
                        ],
                    ),
                ],
            )
        ],
    )
    put_artefact(cur, msd)
    urn = "urn:sdmx:org.sdmx.infomodel.metadatastructure.MetadataStructure=TEST:MSD_TREE(1.0)"
    out = get_artefact(cur, urn)
    assert isinstance(out, MetadataStructure)
    quality = out.attributes["QUALITY"]
    assert quality.is_presentational
    assert {c.id for c in quality.children} == {"COVERAGE", "ACCURACY"}
    accuracy = quality.children["ACCURACY"]
    assert accuracy.children["SAMPLING_ERROR"].representation == Facet(type="Decimal")


# ── SDMX-JSON Structure round-trip ──────────────────────────────────────


def test_msd_json_round_trip_preserves_structure() -> None:
    msd = _make_msd()
    msg = StructureMessage(metadata_structures=[msd])
    out = read_json(write_json(msg))

    assert len(out.metadata_structures) == 1
    parsed = out.metadata_structures[0]
    assert parsed.id == "MSD_FOOTNOTES"
    attr = parsed.attributes["FOOTNOTE_TEXT"]
    assert isinstance(attr.representation, Facet)
    assert attr.representation.type == "String"


def test_msd_json_round_trip_preserves_hierarchy_and_cardinality() -> None:
    msd = MetadataStructure(
        name={"en": "MSD_TREE"},
        id="MSD_TREE",
        maintainer="TEST",
        attributes=[
            MetadataAttribute(
                id="QUALITY",
                is_presentational=True,
                children=[
                    MetadataAttribute(
                        id="TAG",
                        max_occurs=None,
                        representation=Facet(type=DataType.String),
                        concept=concept_ref("TAG"),
                    ),
                ],
                concept=concept_ref("QUALITY"),
            )
        ],
    )
    out = read_json(write_json(StructureMessage(metadata_structures=[msd])))
    quality = out.metadata_structures[0].attributes["QUALITY"]
    assert quality.is_presentational
    assert quality.children["TAG"].max_occurs is None
