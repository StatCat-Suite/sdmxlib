"""A data structure's metadata structure is flat and defines each attribute the data structure uses (GH-299).

SDMX-ML 3.0 and 3.1 document on `DataStructure/Metadata` that "the referenced
metadata set cannot contain nested metadata attributes, as these are not
supported in the data", and a `MetadataAttributeUsage` names one of that
structure's attributes by id. The rule relates two artefacts and no schema
constraint states it, so `validate` warns (#481) where the message holds both.
"""

import warnings

import attrs
import pytest

import sdmxlib as sl
from sdmxlib.model.errors import ImRuleWarning
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.validation import validate
from tests.support import maximal

pytestmark = pytest.mark.unit


def _with_data_msd(change: object) -> StructureMessage:
    """The maximal message with its data structure's metadata structure passed through `change`."""
    assert callable(change)
    msg = maximal.message()
    msds = [change(msd) if msd.id == "MSD_DSD" else msd for msd in msg.metadata_structures]
    return attrs.evolve(msg, metadata_structures=msds)


def _warned(msg: StructureMessage) -> list[tuple[str, str]]:
    with pytest.warns(ImRuleWarning) as caught:
        validate(msg)
    return [
        (issue.path, issue.message)
        for warning in caught
        if isinstance(warning.message, ImRuleWarning)
        for issue in warning.message.issues
    ]


def test_the_maximal_data_structure_validates_quietly() -> None:
    """The discrimination check: its metadata structure is flat and defines both usages."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", ImRuleWarning)
        validate(maximal.message())


def test_a_metadata_structure_that_nests_an_attribute_warns() -> None:
    def nest(msd: sl.MetadataStructure) -> sl.MetadataStructure:
        parent, child = msd.attributes
        return attrs.evolve(msd, attributes=[attrs.evolve(parent, children=[child])])

    assert _warned(_with_data_msd(nest)) == [
        (
            "DataStructure:DSD.metadata",
            (
                "references urn:sdmx:org.sdmx.infomodel.metadatastructure.MetadataStructure=AG:MSD_DSD(1.0), whose "
                "attribute MA nests MA_CHILD; the metadata structure of a data structure has no nested attributes"
            ),
        ),
        (
            "DataStructure:DSD.metadata_attributes[MA_CHILD]",
            (
                "uses MA_CHILD, which urn:sdmx:org.sdmx.infomodel.metadatastructure.MetadataStructure=AG:MSD_DSD(1.0) "
                "does not define at its top level"
            ),
        ),
    ]


def test_a_usage_of_an_attribute_the_metadata_structure_lacks_warns() -> None:
    def drop(msd: sl.MetadataStructure) -> sl.MetadataStructure:
        return attrs.evolve(msd, attributes=[a for a in msd.attributes if a.id != "MA_CHILD"])

    ((path, message),) = _warned(_with_data_msd(drop))
    assert path == "DataStructure:DSD.metadata_attributes[MA_CHILD]"
    assert message.startswith("uses MA_CHILD, which")
