"""A time dimension always has a format, so every writer can write it (#507).

Every SDMX format requires one: SDMX-ML's `TimeDimensionType` has a mandatory
`LocalRepresentation`, and SDMX-JSON 2.0 a mandatory
`localRepresentation.format`. Each gives its type a default,
`ObservationalTimePeriod`. The model held `None` for "none stated", and every
writer turned that into a document its own schema rejected — SDMX-JSON's
reader then refusing what its writer had just written.

The model now holds the default as a value, and a document that omits the
format reads as it, with the assumption recorded (`test_read_assumptions.py`,
`test_json_read_assumptions.py`).
"""

import pytest

import sdmxlib as sl
from sdmxlib.formats import parse, serialize
from tests.support.json_schema import assert_valid_sdmx_json, compile_json_schema
from tests.support.roundtrip import as_structure
from tests.support.xsd import SchemaVersion, assert_valid_sdmx_ml, compile_structure_schema

pytestmark = pytest.mark.unit

_CONCEPTS = sl.ConceptScheme(
    id="CS",
    maintainer="AG",
    name={"en": "Concepts"},
    concepts=[sl.Concept(id=c, name={"en": c}) for c in ("GEO", "TIME_PERIOD", "OBS_VALUE")],
)


def _message() -> sl.StructureMessage:
    dsd = sl.DataStructure(
        id="DSD",
        maintainer="AG",
        name={"en": "d"},
        dimensions=[sl.Dimension(id="GEO", concept=_CONCEPTS.ref_to("GEO"))],
        # No format stated: the case every writer got wrong.
        time_dimension=sl.TimeDimension(concept=_CONCEPTS.ref_to("TIME_PERIOD")),
        measures=[sl.Measure(id="OBS_VALUE", concept=_CONCEPTS.ref_to("OBS_VALUE"))],
    )
    return sl.StructureMessage(concept_schemes=[_CONCEPTS], data_structures=[dsd])


def test_no_format_stated_is_the_default_type() -> None:
    td = sl.TimeDimension(concept=_CONCEPTS.ref_to("TIME_PERIOD"))
    assert td.representation == sl.Facet(type=sl.DataType.ObservationalTimePeriod)


@pytest.mark.parametrize("version", ["2.1", "3.0", "3.1"])
def test_sdmx_ml_writes_it_valid_and_reads_it_back(version: SchemaVersion) -> None:
    written = serialize(_message(), version=version)
    assert_valid_sdmx_ml(written, compile_structure_schema(version))
    assert as_structure(parse(written)).data_structures == _message().data_structures


def test_sdmx_json_writes_it_valid_and_reads_it_back() -> None:
    """The reported case: `to_json` wrote what `parse` then refused."""
    written = serialize(_message(), version="json")
    assert_valid_sdmx_json(written, compile_json_schema("structure", "2.0"))
    back = as_structure(parse(written))
    assert (back.refused, back.data_structures) == ((), _message().data_structures)
