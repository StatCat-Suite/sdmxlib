"""A 3.x MSD written to SDMX-ML 2.1 with a marked reporting layer (#421).

2.1 requires every MSD to carry a MetadataTarget and a ReportStructure; SDMX
3.0 removed both. A 3.x MSD is written with one target of any object and one
report of every attribute, both annotated, and the 2.1 reader drops them — so
3.x -> 2.1 -> 3.x returns the MSD, and any other 2.1 reader sees a valid one.
"""

import attrs
import pytest
from lxml import etree

from sdmxlib.formats import parse, serialize
from sdmxlib.formats.gaps import unrepresentable
from sdmxlib.formats.sdmx_ml21._synthetic import SYNTHETIC_REPORT_LAYER_TYPE
from sdmxlib.model.collections import ItemList
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.metadatastructure import (
    MetadataAttribute,
    MetadataStructure,
    MetadataTarget,
    ReportStructure,
    TargetObject,
)
from sdmxlib.model.representation import Facet
from sdmxlib.model.validation import ValidationError
from tests.support.factories import concept_ref
from tests.support.roundtrip import as_structure, structure_schema
from tests.support.xsd import assert_valid_sdmx_ml

pytestmark = pytest.mark.unit

_STR = "{http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure}"

_MSD_3X = MetadataStructure(
    id="MSD",
    maintainer="AG",
    name={"en": "msd"},
    attributes=ItemList(
        [
            MetadataAttribute(
                id="CONTACT",
                concept=concept_ref("CONTACT", agency="AG"),
                children=ItemList([MetadataAttribute(id="EMAIL", concept=concept_ref("EMAIL", agency="AG"))]),
            ),
            MetadataAttribute(id="SOURCE", concept=concept_ref("SOURCE", agency="AG")),
        ]
    ),
)


def _written(msd: MetadataStructure) -> bytes:
    written = serialize(StructureMessage(metadata_structures=[msd]), version="2.1")
    assert_valid_sdmx_ml(written, structure_schema("2.1"))
    return written


def test_a_3x_msd_is_written_with_a_marked_layer_and_read_back_without_it() -> None:
    written = _written(_MSD_3X)
    doc = etree.fromstring(written)
    (target,) = doc.iterfind(f".//{_STR}MetadataStructureComponents/{_STR}MetadataTarget")
    (obj,) = target.iter(f"{_STR}IdentifiableObjectTarget")
    assert obj.get("objectType") == "Any"
    (report,) = doc.iter(f"{_STR}ReportStructure")
    assert [el.get("id") for el in report.findall(f"{_STR}MetadataAttribute")] == ["CONTACT", "SOURCE"]
    assert written.count(SYNTHETIC_REPORT_LAYER_TYPE.encode()) == 2
    (back,) = as_structure(parse(written)).metadata_structures
    assert back == _MSD_3X
    assert unrepresentable(_MSD_3X, "2.1") == ()


def test_a_2_1_msd_keeps_its_own_layer() -> None:
    """Nothing is invented for an MSD that has a layer, and nothing of it dropped."""
    msd = attrs.evolve(
        _MSD_3X,
        targets=(
            MetadataTarget(
                id="FLOW",
                target_objects=(
                    TargetObject(
                        id="F",
                        kind="IdentifiableObjectTarget",
                        object_type="Dataflow",
                        representation=Facet(type="IdentifiableReference"),
                    ),
                ),
            ),
        ),
        report_structures=(ReportStructure(id="REPORT", attribute_ids=("CONTACT", "SOURCE"), target_ids=("FLOW",)),),
    )
    written = _written(msd)
    assert SYNTHETIC_REPORT_LAYER_TYPE.encode() not in written
    (back,) = as_structure(parse(written)).metadata_structures
    assert back == msd


@pytest.mark.parametrize(
    ("msd", "message"),
    [
        pytest.param(
            attrs.evolve(_MSD_3X, report_structures=(ReportStructure(id="R", attribute_ids=("SOURCE",)),)),
            "no MetadataTarget",
            id="report-without-target",
        ),
        pytest.param(attrs.evolve(_MSD_3X, attributes=ItemList([])), "has none", id="no-attributes"),
    ],
)
def test_2_1_refuses_an_msd_it_cannot_complete(msd: MetadataStructure, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        serialize(StructureMessage(metadata_structures=[msd]), version="2.1")
