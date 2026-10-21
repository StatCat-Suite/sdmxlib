"""An attribute's relationship and usage across SDMX-ML 2.1, 3.x and SDMX-JSON (#421).

Measured across every format pair; three cells did not convert. 2.1's
attachment groups are left out of 3.x and SDMX-JSON, declared, rather than
refusing the message. An unstated usage is optional, as SDMX 3.x's schema
defaults it, rather than a None that 2.1 had to write as Conditional. And an
attribute with no attachment is refused, rather than written by every writer
as attached to the data set.
"""

import attrs
import pytest
from lxml import etree

from sdmxlib.formats import parse, serialize
from sdmxlib.formats.gaps import unrepresentable
from sdmxlib.model.datastructure import (
    AttachmentLevel,
    Attribute,
    AttributeUsage,
    DataStructure,
    Dimension,
    Group,
    Measure,
    TimeDimension,
)
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.representation import Facet
from tests.support.factories import concept_ref
from tests.support.narrow import present
from tests.support.roundtrip import as_structure, structure_schema
from tests.support.xsd import assert_valid_sdmx_ml

pytestmark = pytest.mark.unit

_FORMATS = ["2.1", "3.0", "3.1", "json"]


def _attribute(
    attachment: AttachmentLevel = AttachmentLevel.DATASET,
    related_dimensions: list[str] | None = None,
    attachment_groups: tuple[str, ...] = (),
) -> Attribute:
    return Attribute(
        id="ATT",
        concept=concept_ref("ATT", agency="AG"),
        representation=Facet(type="String"),
        attachment=attachment,
        related_dimensions=related_dimensions,
        attachment_groups=attachment_groups,
    )


def _dsd(attribute: Attribute) -> DataStructure:
    return DataStructure(
        id="DSD",
        maintainer="AG",
        name={"en": "d"},
        dimensions=[
            Dimension(id="FREQ", concept=concept_ref("FREQ", agency="AG"), representation=Facet(type="String")),
        ],
        time_dimension=TimeDimension(
            id="TIME_PERIOD",
            concept=concept_ref("TIME_PERIOD", agency="AG"),
            representation=Facet(type="ObservationalTimePeriod"),
        ),
        measures=[Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE", agency="AG"))],
        groups=[Group(id="G", dimension_ids=["FREQ"])],
        attributes=[attribute],
    )


def _through(dsd: DataStructure, version: str) -> DataStructure:
    written = serialize(StructureMessage(data_structures=[dsd]), version=version)
    if version != "json":
        assert_valid_sdmx_ml(written, structure_schema(version))
    (back,) = as_structure(parse(written)).data_structures
    return back


@pytest.mark.parametrize("version", _FORMATS)
def test_an_unstated_usage_is_optional_everywhere(version: str) -> None:
    attribute = Attribute(id="ATT", usage=None, attachment=AttachmentLevel.DATASET, concept=concept_ref("ATT"))
    assert attribute.usage is AttributeUsage.OPTIONAL
    assert Measure(id="OBS_VALUE", usage=None, concept=concept_ref("OBS_VALUE")).usage is AttributeUsage.OPTIONAL
    dsd = _dsd(_attribute())
    assert _through(dsd, version) == dsd


@pytest.mark.parametrize("version", ["3.0", "3.1", "json"])
def test_attachment_groups_are_left_out_of_3x_declared(version: str) -> None:
    grouped = _attribute(attachment=AttachmentLevel.SERIES, related_dimensions=["FREQ"], attachment_groups=("G",))
    dsd = _dsd(grouped)
    assert [gap.field for gap in unrepresentable(dsd, version)] == ["attributes[].attachment_groups"]
    (back,) = _through(dsd, version).attributes
    assert back == attrs.evolve(grouped, attachment_groups=())


def test_2_1_keeps_attachment_groups() -> None:
    dsd = _dsd(_attribute(attachment=AttachmentLevel.SERIES, related_dimensions=["FREQ"], attachment_groups=("G",)))
    assert _through(dsd, "2.1") == dsd


def test_2_1_declares_a_mandatory_measure_only() -> None:
    dsd = _dsd(_attribute())
    assert unrepresentable(dsd, "2.1") == ()
    mandatory = attrs.evolve(dsd, measures=[attrs.evolve(dsd.measures[0], usage=AttributeUsage.MANDATORY)])
    assert [gap.field for gap in unrepresentable(mandatory, "2.1")] == ["measures[].usage"]


@pytest.mark.parametrize("version", ["2.1", "3.0", "3.1"])
def test_an_omitted_relationship_is_read_as_dataset_level(version: str) -> None:
    """FMR serves attributes without the relationship every schema requires (IMF: 16, and 3,047 usages)."""
    written = serialize(StructureMessage(data_structures=[_dsd(_attribute())]), version=version)
    doc = etree.fromstring(written)
    (relationship,) = doc.iterfind(".//{*}Attribute/{*}AttributeRelationship")
    present(relationship.getparent()).remove(relationship)
    (back,) = as_structure(parse(etree.tostring(doc))).data_structures
    assert back.attributes["ATT"].attachment is AttachmentLevel.DATASET
