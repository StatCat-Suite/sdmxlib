"""SDMX-ML 3.0 round-trip tests for MetadataStructure (MSD) and Metadataflow.

MetadataSet is intentionally NOT covered here — it belongs in a
GenericMetadata message, not a Structure message. See issue #44.
"""

import pytest

from sdmxlib.formats.sdmx_ml30.reader import read as read_xml
from sdmxlib.formats.sdmx_ml30.writer import write as write_xml
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.metadataflow import Metadataflow
from sdmxlib.model.metadatastructure import MetadataAttribute, MetadataStructure
from sdmxlib.model.ref import Ref
from sdmxlib.model.representation import DataType, Facet
from tests.support.factories import concept_ref

pytestmark = pytest.mark.unit


# ── Fixtures ─────────────────────────────────────────────────────────────


def _make_msd() -> MetadataStructure:
    return MetadataStructure(
        id="MSD_FOOTNOTES",
        maintainer="STATCAN",
        version="1.0",
        name={"en": "Footnote MSD"},
        description={"en": "Defines footnote text slots"},
        attributes=[
            MetadataAttribute(
                id="FOOTNOTE_TEXT",
                representation=Facet(type=DataType.String, max_length=4000),
                min_occurs=0,
                max_occurs=1,
                concept=concept_ref("FOOTNOTE_TEXT"),
            )
        ],
    )


# ── MetadataStructure round-trips ───────────────────────────────────────


def test_msd_xml_round_trip_preserves_attribute_metadata() -> None:
    msd = _make_msd()
    msg = StructureMessage(metadata_structures=[msd])

    out = read_xml(write_xml(msg))

    assert len(out.metadata_structures) == 1
    parsed = out.metadata_structures[0]
    assert parsed.id == "MSD_FOOTNOTES"
    assert parsed.agency_id == "STATCAN"
    assert parsed.name["en"] == "Footnote MSD"
    assert parsed.description["en"] == "Defines footnote text slots"

    attr = parsed.attributes["FOOTNOTE_TEXT"]
    assert attr.min_occurs == 0
    assert attr.max_occurs == 1
    assert isinstance(attr.representation, Facet)
    assert attr.representation.type == "String"
    assert attr.representation.max_length == 4000


def test_msd_xml_round_trip_preserves_hierarchy_and_unbounded() -> None:
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
                        id="COVERAGE",
                        representation=Facet(type=DataType.String),
                        concept=concept_ref("COVERAGE"),
                    ),
                    MetadataAttribute(
                        id="TAG",
                        max_occurs=None,  # unbounded
                        representation=Facet(type=DataType.String),
                        concept=concept_ref("TAG"),
                    ),
                ],
                concept=concept_ref("QUALITY"),
            )
        ],
    )

    out = read_xml(write_xml(StructureMessage(metadata_structures=[msd])))
    parsed = out.metadata_structures[0]
    quality = parsed.attributes["QUALITY"]
    assert quality.is_presentational is True
    assert {c.id for c in quality.children} == {"COVERAGE", "TAG"}
    assert quality.children["TAG"].max_occurs is None  # round-trips "unbounded"


def test_msd_xml_emits_unbounded_token() -> None:
    """maxOccurs=None should serialise to the literal token "unbounded"."""
    msd = MetadataStructure(
        name={"en": "MSD_U"},
        id="MSD_U",
        maintainer="TEST",
        attributes=[MetadataAttribute(id="X", max_occurs=None, concept=concept_ref("X"))],
    )
    xml = write_xml(StructureMessage(metadata_structures=[msd]))
    assert b'maxOccurs="unbounded"' in xml


# ── Metadataflow round-trips ────────────────────────────────────────────


def test_metadataflow_xml_round_trip() -> None:
    msd = _make_msd()
    mdf = Metadataflow(
        id="MDF_FOOTNOTES",
        maintainer="STATCAN",
        version="1.0",
        name={"en": "Footnote flow"},
        structure=msd,
    )
    msg = StructureMessage(metadata_structures=[msd], metadataflows=[mdf])

    out = read_xml(write_xml(msg))

    assert len(out.metadataflows) == 1
    parsed = out.metadataflows[0]
    assert parsed.id == "MDF_FOOTNOTES"
    assert parsed.name["en"] == "Footnote flow"
    assert isinstance(parsed.structure, Ref)
    assert parsed.structure.urn.id == "MSD_FOOTNOTES"
    # Cross-resolved within the same message via the parser's index
    assert parsed.structure.resolved
    assert parsed.structure() is out.metadata_structures[0]


def test_metadataflow_xml_round_trip_with_unresolved_msd() -> None:
    """Metadataflow whose MSD is not in the same message must round-trip
    as an unresolved Ref carrying the URN."""
    msd_urn = MetadataStructure.urn_for(agency="STATCAN", id="EXTERNAL_MSD", version="1.0")
    mdf = Metadataflow(name={"en": "MDF"}, id="MDF", maintainer="STATCAN", structure=msd_urn)
    out = read_xml(write_xml(StructureMessage(metadataflows=[mdf])))

    parsed = out.metadataflows[0]
    assert isinstance(parsed.structure, Ref)
    assert parsed.structure.urn.id == "EXTERNAL_MSD"
    assert parsed.structure.urn.artefact_class == "MetadataStructure"
    assert not parsed.structure.resolved


# ── Coexistence with structures-side artefacts ──────────────────────────


def test_msd_and_dsd_in_same_message() -> None:
    """A message with both a DSD and an MSD round-trips both."""
    from sdmxlib.model.datastructure import DataStructure, Dimension

    msd = _make_msd()
    dsd = DataStructure(
        id="DSD_TEST",
        maintainer="TEST",
        name={"en": "Test DSD"},
        dimensions=[Dimension(id="FREQ", concept=concept_ref("FREQ"))],
    )
    out = read_xml(write_xml(StructureMessage(data_structures=[dsd], metadata_structures=[msd])))

    assert len(out.data_structures) == 1
    assert len(out.metadata_structures) == 1
    assert out.data_structures[0].id == "DSD_TEST"
    assert out.metadata_structures[0].id == "MSD_FOOTNOTES"
