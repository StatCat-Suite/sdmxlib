"""A cube region's value set can exclude its values (#422).

SDMX's `@include` on a `KeyValue` / `Component` (SDMX-JSON `include`) is a
value set's own flag, distinct from the region's: an included region may still
exclude some of one dimension's values. Every reader took `include="false"` as
an inclusion — a constraint saying the opposite of its document — until it was
refused, and now it is held. The censuses prove each format carries it (the
maximal fixture excludes one key and one component); these pin the spelling,
the meaning, and the query column.
"""

import functools
import json
from collections.abc import Callable, Iterator

import pytest
from attrs import evolve
from lxml import etree

import sdmxlib as sl
from sdmxlib.formats.sdmx_json import reader as json_reader
from sdmxlib.formats.sdmx_json import writer as json_writer
from sdmxlib.formats.sdmx_json_1_0.writer import write_contentconstraint
from sdmxlib.formats.sdmx_ml21 import reader as ml21_reader
from sdmxlib.formats.sdmx_ml21 import writer as ml21_writer
from sdmxlib.formats.sdmx_ml30 import reader as ml30_reader
from sdmxlib.formats.sdmx_ml30 import writer as ml30_writer
from sdmxlib.formats.sdmx_ml31 import reader as ml31_reader
from sdmxlib.formats.sdmx_ml31 import writer as ml31_writer
from sdmxlib.model.constraint import (
    ComponentValueSet,
    ConstraintAttachment,
    CubeRegion,
    DataConstraint,
    KeyValue,
    TimeBound,
    TimeRange,
)
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import Ref
from sdmxlib.query.accessors import q
from tests.support.xsd import SchemaVersion, assert_valid_sdmx_ml, compile_structure_schema

pytestmark = pytest.mark.unit

#: `== False` is what the path language means; ruff reads the literal as E712.
EXCLUDED = False

_XML: dict[SchemaVersion, tuple[Callable[[StructureMessage], bytes], Callable[[bytes], StructureMessage]]] = {
    "2.1": (ml21_writer.write, ml21_reader.read),
    "3.0": (ml30_writer.write, ml30_reader.read),
    "3.1": (ml31_writer.write, ml31_reader.read),
}


@functools.cache
def _xsd(version: SchemaVersion) -> etree.XMLSchema:
    return compile_structure_schema(version)


def _constraint() -> DataConstraint:
    flow = Ref[Dataflow].unresolved(Dataflow.urn_for(agency="AG", id="DF", version="1.0"))
    return DataConstraint(
        id="DC",
        maintainer="AG",
        version="1.0",
        name={"en": "DC"},
        attachment=ConstraintAttachment(dataflows=(flow,)),
        regions=[
            CubeRegion(
                include=True,
                keys=(
                    KeyValue(dimension_id="REF_AREA", values=frozenset({"EU", "XX"}), include=False),
                    KeyValue(dimension_id="FREQ", values=frozenset({"A"})),
                ),
                components=(ComponentValueSet(component_id="OBS_STATUS", values=frozenset({"M"}), include=False),),
            )
        ],
    )


# ── what it means ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("key", "allowed"),
    [
        ({"REF_AREA": "DE", "FREQ": "A"}, True),
        ({"REF_AREA": "EU", "FREQ": "A"}, False),
        ({"REF_AREA": "DE", "FREQ": "M"}, False),
    ],
)
def test_an_excluded_value_is_outside_an_included_region(key: dict[str, str], allowed: bool) -> None:
    assert _constraint().allows(key) is allowed


def test_an_excluded_time_range_is_outside() -> None:
    period = KeyValue(
        dimension_id="TIME_PERIOD",
        time_range=TimeRange(start=TimeBound("2020-01-01"), end=TimeBound("2020-12-31")),
        include=False,
    )
    region = CubeRegion(keys=(period,))
    assert not region.allows({"TIME_PERIOD": "2020-06-01"})
    assert region.allows({"TIME_PERIOD": "2021-06-01"})


# ── every format carries it ───────────────────────────────────────────────


@pytest.mark.parametrize("version", sorted(_XML))
def test_sdmx_ml_writes_include_false_and_reads_it_back(version: SchemaVersion) -> None:
    write, read = _XML[version]
    xml = write(StructureMessage(constraints=[_constraint()]))
    assert_valid_sdmx_ml(xml, _xsd(version))
    includes = {el.get("id"): el.get("include") for el in etree.fromstring(xml).iter() if el.get("id") in _IDS}
    assert includes == {"REF_AREA": "false", "FREQ": None, "OBS_STATUS": "false"}, "only the non-default is written"
    assert read(xml).constraints[0].regions == _constraint().regions


_IDS = {"REF_AREA", "FREQ", "OBS_STATUS"}


def test_sdmx_json_writes_include_false_and_reads_it_back() -> None:
    body = json_writer.write(StructureMessage(constraints=[_constraint()]))
    region = json.loads(body)["data"]["dataConstraints"][0]["cubeRegions"][0]
    assert [kv.get("include") for kv in region["keyValues"]] == [False, None]
    assert [c.get("include") for c in region["components"]] == [False]
    assert json_reader.read(body).constraints[0].regions == _constraint().regions


def test_sdmx_json_1_0_refuses_what_it_cannot_say() -> None:
    """1.0's `CubeRegionKeyType` has no `include`: written, it would read as included."""
    with pytest.raises(sl.ValidationError, match=r"regions\[0\]\.keys\[REF_AREA\]\.include"):
        write_contentconstraint(_constraint())
    included = evolve(_constraint(), regions=[CubeRegion(keys=(KeyValue(dimension_id="FREQ", values={"A"}),))])
    assert write_contentconstraint(included)


# ── the store, and the query column ───────────────────────────────────────


@pytest.fixture
def reg() -> Iterator[sl.LocalRegistry]:
    with sl.LocalRegistry(":memory:") as local:
        local.add(_constraint())
        yield local


def test_the_store_keeps_it(reg: sl.LocalRegistry) -> None:
    stored = reg.get(DataConstraint.urn_for(agency="AG", id="DC", version="1.0"))
    assert isinstance(stored, DataConstraint)
    assert stored.regions == _constraint().regions


def test_a_query_tells_an_excluded_value_from_an_allowed_one(reg: sl.LocalRegistry) -> None:
    """`key_values.value == "EU"` alone would find EU listed — as an exclusion."""
    excluded = reg.query(q.key_value).filter(q.key_value.is_included == EXCLUDED)
    assert sorted(excluded.ids()) == ["EU", "M", "XX"]
    allowed = reg.query(q.key_value).filter(q.key_value.is_included != EXCLUDED)
    assert allowed.ids() == ["A"]
