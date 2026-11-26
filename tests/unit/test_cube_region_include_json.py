"""SDMX-JSON 2.0 writes a cube region's and a key value's ``include`` only when false (GH-412).

Absent is true, the schema's default, and writers state nothing at its
default (#503). The comment on `CubeRegionJson.include` said the opposite;
this pins the written shape so the comment and the writer cannot drift apart
again. A consumer mapping 2.0 to 1.0's ``isIncluded`` applies the default.
"""

import json

import pytest

import sdmxlib as sl
from sdmxlib.formats.sdmx_json import writer as json_writer

pytestmark = pytest.mark.unit


def _constraint(*, region_include: bool, key_include: bool) -> sl.StructureMessage:
    region = sl.CubeRegion(
        include=region_include, keys=[sl.KeyValue(dimension_id="REF_AREA", values=["CA"], include=key_include)]
    )
    constraint = sl.DataConstraint(
        id="CC", maintainer="AG", version="1.0.0", name={"en": "c"}, constraint_type="Actual", regions=[region]
    )
    return sl.StructureMessage(constraints=[constraint])


def _written_region(msg: sl.StructureMessage) -> dict[str, object]:
    document: dict[str, dict[str, list[dict[str, list[dict[str, object]]]]]] = json.loads(json_writer.write(msg))
    (constraint,) = document["data"]["dataConstraints"]
    (region,) = constraint["cubeRegions"]
    return region


def _key_values(region: dict[str, object]) -> list[dict[str, object]]:
    found = region["keyValues"]
    assert isinstance(found, list)
    return [entry for entry in found if isinstance(entry, dict)]


def test_a_cube_region_writes_include_only_when_false() -> None:
    assert "include" not in _written_region(_constraint(region_include=True, key_include=True))
    assert _written_region(_constraint(region_include=False, key_include=True))["include"] is False


def test_a_key_value_writes_include_only_when_false() -> None:
    (inclusive,) = _key_values(_written_region(_constraint(region_include=True, key_include=True)))
    (exclusive,) = _key_values(_written_region(_constraint(region_include=True, key_include=False)))
    assert "include" not in inclusive
    assert exclusive["include"] is False


@pytest.mark.parametrize("include", [True, False])
def test_the_flag_reads_back(include: bool) -> None:
    read = sl.parse(json_writer.write(_constraint(region_include=include, key_include=True)))
    assert isinstance(read, sl.StructureMessage)
    (constraint,) = read.constraints
    (region,) = constraint.regions
    assert region.include is include
