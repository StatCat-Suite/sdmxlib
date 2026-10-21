"""A 2.1 metadataflow given the targets SDMX 3.x requires (#421).

2.1 declares what metadata may describe in the metadata structure, as
MetadataTargets; 3.x moved it to the metadataflow and requires at least one.
A flow read from 2.1 has none, so the 3.x and SDMX-JSON writers derive them
from its structure's IdentifiableObjectTargets, one wildcard URN per class.
A flow they cannot be derived for is refused, as a flow without targets was
before.
"""

import attrs
import pytest

from sdmxlib.formats import parse, serialize
from sdmxlib.formats.gaps import unrepresentable
from sdmxlib.model.collections import ItemList
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.metadataflow import Metadataflow
from sdmxlib.model.metadatastructure import (
    MetadataAttribute,
    MetadataStructure,
    MetadataTarget,
    ReportStructure,
    TargetObject,
)
from sdmxlib.model.ref import Ref
from sdmxlib.model.representation import Facet
from sdmxlib.model.validation import ValidationError
from tests.support.factories import concept_ref
from tests.support.json_schema import assert_valid_sdmx_json, compile_json_schema
from tests.support.roundtrip import as_structure, structure_schema
from tests.support.xsd import assert_valid_sdmx_ml

pytestmark = pytest.mark.unit

_REFERENCE = Facet(type="IdentifiableReference")


def _msd(*objects: TargetObject) -> MetadataStructure:
    return MetadataStructure(
        id="MSD",
        maintainer="AG",
        name={"en": "msd"},
        attributes=ItemList([MetadataAttribute(id="SOURCE", concept=concept_ref("SOURCE", agency="AG"))]),
        targets=(MetadataTarget(id="TARGET", target_objects=objects),),
        report_structures=(ReportStructure(id="REPORT", attribute_ids=("SOURCE",), target_ids=("TARGET",)),),
    )


def _object(object_type: str) -> TargetObject:
    return TargetObject(id=object_type.upper(), object_type=object_type, representation=_REFERENCE)


_MSD = _msd(
    _object("Dataflow"),
    _object("Code"),
    _object("Dataflow"),
    TargetObject(id="DATA_SET", kind="DataSetTarget", representation=Facet(type="DataSetReference")),
)
_MDF = Metadataflow(id="MDF", maintainer="AG", name={"en": "mdf"}, structure=_MSD.urn)
_DERIVED = (
    "urn:sdmx:org.sdmx.infomodel.datastructure.Dataflow=*:*(*)",
    "urn:sdmx:org.sdmx.infomodel.codelist.Code=*:*(*).*",
)


@pytest.mark.parametrize("version", ["3.0", "3.1", "json"])
def test_a_flow_without_targets_is_written_with_its_structures(version: str) -> None:
    written = serialize(StructureMessage(metadata_structures=[_MSD], metadataflows=[_MDF]), version=version)
    if version == "json":
        for schema in ("2.0", "2.1"):
            assert_valid_sdmx_json(written, compile_json_schema("structure", schema))
    else:
        assert_valid_sdmx_ml(written, structure_schema(version))
    (back,) = as_structure(parse(written)).metadataflows
    assert back.targets == _DERIVED
    assert [gap.field for gap in unrepresentable(_MDF, version)] == ["targets"]


def test_a_resolved_structure_needs_no_message_copy() -> None:
    mdf = attrs.evolve(_MDF, structure=Ref.to(_MSD))
    assert mdf.with_derived_targets().targets == _DERIVED
    assert mdf.target_misfits() == ()


def test_a_flow_with_targets_keeps_them() -> None:
    mdf = attrs.evolve(_MDF, targets=("urn:sdmx:org.sdmx.infomodel.datastructure.Dataflow=AG:DF(1.0)",))
    assert mdf.with_derived_targets([_MSD]) is mdf
    assert unrepresentable(mdf, "3.0") == ()


def test_2_1_reads_a_flow_without_targets_and_3x_derives_them() -> None:
    """The case this exists for: 2.1 in, 3.0 out."""
    msg = StructureMessage(metadata_structures=[_MSD], metadataflows=[_MDF])
    from_21 = as_structure(parse(serialize(msg, version="2.1")))
    assert from_21.metadataflows[0].targets == ()
    (back,) = as_structure(parse(serialize(from_21, version="3.0"))).metadataflows
    assert back.targets == _DERIVED


@pytest.mark.parametrize(
    ("msds", "message"),
    [
        pytest.param([], "neither resolved nor in the message", id="structure-missing"),
        pytest.param([_msd(_object("Any"))], "type 'Any'", id="any-object"),
        pytest.param([_msd(_object("ContentConstraint"))], "type 'ContentConstraint'", id="no-3x-class"),
        pytest.param(
            [_msd(TargetObject(id="DATA_SET", kind="DataSetTarget", representation=Facet(type="DataSetReference")))],
            "declares no IdentifiableObjectTarget",
            id="no-identifiable-target",
        ),
    ],
)
@pytest.mark.parametrize("version", ["3.0", "3.1", "json"])
def test_a_flow_whose_targets_cannot_be_derived_is_refused(
    msds: list[MetadataStructure], message: str, version: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        serialize(StructureMessage(metadata_structures=msds, metadataflows=[_MDF]), version=version)
    with pytest.raises(ValueError, match=message):
        _MDF.with_derived_targets(msds)


def test_the_misfits_name_what_is_missing() -> None:
    assert _MDF.target_misfits() == (f"its metadata structure {_MSD.urn} is neither resolved nor in the message",)
    assert attrs.evolve(_MDF, structure=None).target_misfits() == ("it names no metadata structure",)
