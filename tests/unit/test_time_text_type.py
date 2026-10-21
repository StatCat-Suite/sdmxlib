"""A time dimension's textType must be a time type (#431).

Every SDMX-ML version types the time dimension's format `TimeTextFormatType`,
whose `textType` is the `TimeDataType` enumeration. The model's
`TimeDimension.representation` is an ordinary `Facet`, so `String` was
accepted and written, schema-invalid in every format: IMF publishes 54 DSDs
declaring it.
"""

import pytest
from lxml import etree

from sdmxlib.formats import serialize
from sdmxlib.model.datastructure import DataStructure, Measure, TimeDimension
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.representation import DataType, Facet
from sdmxlib.model.validation import _TIME_DATA_TYPES, ValidationError
from tests.support import repo
from tests.support.factories import concept_ref

pytestmark = pytest.mark.unit

_XSD = "{http://www.w3.org/2001/XMLSchema}"


def _schema_time_data_types(folder: str) -> frozenset[str]:
    tree = etree.parse(str(repo.ROOT / "tests" / "schemas" / folder / "SDMXCommon.xsd"))
    found = tree.find(f".//{_XSD}simpleType[@name='TimeDataType']")
    assert found is not None, f"TimeDataType is gone from {folder}: has the schema been refreshed?"
    return frozenset(str(e.get("value")) for e in found.iter(f"{_XSD}enumeration"))


@pytest.mark.parametrize("folder", ["sdmx_ml21", "sdmx_ml30", "sdmx_ml31"])
def test_the_rule_is_the_schemas_time_data_type(folder: str) -> None:
    assert _schema_time_data_types(folder) == _TIME_DATA_TYPES


def _dsd(text_type: DataType | str | None) -> DataStructure:
    return DataStructure(
        id="DSD",
        maintainer="AG",
        name={"en": "d"},
        time_dimension=TimeDimension(
            id="TIME_PERIOD", concept=concept_ref("TIME_PERIOD"), representation=Facet(type=text_type)
        ),
        measures=[Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )


def test_a_non_time_text_type_is_invalid() -> None:
    with pytest.raises(ValidationError, match=r"time_dimension\.representation\.type"):
        _dsd(DataType.String)


@pytest.mark.parametrize("text_type", sorted(_TIME_DATA_TYPES))
def test_every_time_type_is_valid(text_type: str) -> None:
    """Discrimination: the rule refuses `String`, not the time types."""
    _dsd(text_type)


def test_no_text_type_is_valid() -> None:
    _dsd(None)


@pytest.mark.parametrize("version", ["2.1", "3.0", "3.1", "json"])
def test_it_is_refused_in_every_format_rather_than_written_invalid(version: str) -> None:
    with pytest.raises(ValidationError, match="must be a time type"):
        serialize(StructureMessage(data_structures=[_dsd(DataType.String)]), version=version)
