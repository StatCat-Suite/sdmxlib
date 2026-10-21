"""Tests for the SDMX-JSON 1.0 ContentConstraint serialiser."""

from datetime import UTC, datetime

import attrs
import msgspec
import pytest

from sdmxlib.formats.sdmx_json_1_0.writer import write_contentconstraint
from sdmxlib.model.constraint import (
    ComponentValueSet,
    ConstraintAttachment,
    ConstraintType,
    CubeRegion,
    DataConstraint,
    KeyValue,
    TimeBound,
    TimeRange,
)
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.ref import Ref
from tests.support.json_schema import assert_valid_sdmx_json, compile_json_schema

pytestmark = pytest.mark.unit

_FLOW_URN = Dataflow.urn_for(agency="ECB", id="EXR", version="1.0")


def _constraint() -> DataConstraint:
    region = CubeRegion(
        include=True,
        keys=(
            # Deliberately unsorted: dim id order + value order are the
            # serialiser's job, not the model's.
            KeyValue(dimension_id="FREQ", values=frozenset({"M", "A", "Q"})),
            KeyValue(dimension_id="CURRENCY", values=frozenset({"USD", "GBP", "CHF"})),
        ),
    )
    return DataConstraint(
        id="EXR",
        maintainer="ECB",
        version="1.0",
        name=InternationalString(en="Availability for EXR"),
        constraint_type=ConstraintType.ACTUAL,
        attachment=ConstraintAttachment(dataflows=(Ref[Dataflow].unresolved(_FLOW_URN),)),
        regions=(region,),
    )


def test_envelope_shape():
    doc = msgspec.json.decode(write_contentconstraint(_constraint()))
    assert set(doc) == {"meta", "data", "errors"}
    assert doc["meta"]["id"] == "SDMXLIB"
    assert doc["meta"]["sender"] == {"id": "SDMXLIB"}
    assert "contentConstraints" in doc["data"]
    assert len(doc["data"]["contentConstraints"]) == 1


def test_content_constraint_fields():
    cc = msgspec.json.decode(write_contentconstraint(_constraint()))["data"]["contentConstraints"][0]
    assert cc["id"] == "EXR"
    assert cc["agencyID"] == "ECB"
    assert cc["version"] == "1.0"
    assert cc["name"] == "Availability for EXR"
    assert cc["names"] == {"en": "Availability for EXR"}
    # 1.0 carries the Allowed/Actual role inline as ``type``.
    assert cc["type"] == "Actual"
    assert cc["constraintAttachment"] == {"dataflows": [str(_FLOW_URN)]}


def test_cube_region_uses_isincluded_and_sorted_keyvalues():
    cc = msgspec.json.decode(write_contentconstraint(_constraint()))["data"]["contentConstraints"][0]
    regions = cc["cubeRegions"]
    assert len(regions) == 1
    region = regions[0]
    # 1.0 inclusion flag is ``isIncluded`` (2.x uses ``include``).
    assert region["isIncluded"] is True
    # Key values sorted by dimension id, values sorted lexicographically.
    assert region["keyValues"] == [
        {"id": "CURRENCY", "values": ["CHF", "GBP", "USD"]},
        {"id": "FREQ", "values": ["A", "M", "Q"]},
    ]


def test_deterministic_bytes_across_calls():
    assert write_contentconstraint(_constraint()) == write_contentconstraint(_constraint())


def test_no_prepared_by_default_but_emitted_when_passed():
    without = msgspec.json.decode(write_contentconstraint(_constraint()))
    assert "prepared" not in without["meta"]

    prepared = datetime(2026, 6, 17, tzinfo=UTC)
    with_ts = msgspec.json.decode(write_contentconstraint(_constraint(), prepared=prepared))
    assert with_ts["meta"]["prepared"] == prepared.isoformat()


def test_message_and_sender_id_overridable():
    doc = msgspec.json.decode(write_contentconstraint(_constraint(), message_id="MID", sender_id="SID"))
    assert doc["meta"]["id"] == "MID"
    assert doc["meta"]["sender"] == {"id": "SID"}


def test_pretty_print_indents():
    assert b"\n" in write_contentconstraint(_constraint(), pretty_print=True)


# ── against the official 1.0 structure schema (#433) ─────────────────────────


def _with_selections() -> DataConstraint:
    region = CubeRegion(
        keys=(
            KeyValue(dimension_id="FREQ", values=frozenset({"A"})),
            KeyValue(
                dimension_id="TIME_PERIOD",
                time_range=TimeRange(start=TimeBound("2000-01-01"), end=TimeBound("2010-12-31", inclusive=False)),
            ),
            KeyValue(dimension_id="EMPTY"),
        ),
        components=(ComponentValueSet(component_id="UNIT_MULT", values=frozenset({"6", "0"})),),
    )
    return attrs.evolve(_constraint(), regions=(region,))


def test_the_1_0_schema_can_reject_a_document() -> None:
    """Discrimination: the 1.0 schema is loose, so check it can fail at all."""
    doc = msgspec.json.decode(write_contentconstraint(_constraint(), prepared=datetime(2026, 1, 1, tzinfo=UTC)))
    assert_valid_sdmx_json(msgspec.json.encode(doc), compile_json_schema("structure", "1.0"))
    doc["data"]["contentConstraints"][0]["cubeRegions"][0]["isIncluded"] = "yes"
    with pytest.raises(AssertionError):
        assert_valid_sdmx_json(msgspec.json.encode(doc), compile_json_schema("structure", "1.0"))


def test_time_ranges_attributes_and_empty_keys_are_written_valid_1_0() -> None:
    # `prepared` is required by the 1.0 schema and omitted by default for
    # byte-identical caches (see `write_contentconstraint`), so pass one.
    written = write_contentconstraint(_with_selections(), prepared=datetime(2026, 1, 1, tzinfo=UTC))
    assert_valid_sdmx_json(written, compile_json_schema("structure", "1.0"))
    region = msgspec.json.decode(written)["data"]["contentConstraints"][0]["cubeRegions"][0]
    assert region["keyValues"] == [
        {"id": "EMPTY", "values": []},
        {"id": "FREQ", "values": ["A"]},
        {
            "id": "TIME_PERIOD",
            "timeRange": {
                "startPeriod": {"period": "2000-01-01"},
                "endPeriod": {"period": "2010-12-31", "isInclusive": False},
            },
        },
    ]
    # 1.0's name for what 2.x calls `components`.
    assert region["attributes"] == [{"id": "UNIT_MULT", "values": ["0", "6"]}]
