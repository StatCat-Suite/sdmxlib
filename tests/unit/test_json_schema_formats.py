"""The SDMX-JSON schema checks check formats, not only structure (#496).

`jsonschema` passes any `format` it has no library for: without
`rfc3339-validator`, `"format": "date-time"` accepted a timestamp with no zone.
None was installed, so our output's dates and date-times were never checked,
and several writers emitted ones every SDMX-JSON schema refuses. The format
libraries are now in the dev group (`jsonschema[format-nongpl]`); the first
tests here fail if one goes missing, rather than the schema checks quietly
passing again.

The rest is what checking formats found, and what was done about each: map
validity dates written as midnight UTC, a zoneless period refused, `prepared`
taken as an aware `datetime`, and the one declared deviation, a time dimension
value's bounds, which stay zoneless for .Stat.
"""

import json
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import msgspec
import pyarrow as pa
import pytest

import sdmxlib as sl
from sdmxlib.formats.sdmx_json import data_v1, data_v2
from sdmxlib.formats.sdmx_json import writer as json_writer
from sdmxlib.formats.sdmx_json_1_0.writer import write_contentconstraint
from sdmxlib.model.constraint import (
    ConstraintAttachment,
    ConstraintType,
    CubeRegion,
    DataConstraint,
    KeyValue,
    TimeBound,
    TimeRange,
)
from sdmxlib.model.datastructure import TimeDimension
from sdmxlib.model.validation import SdmxConformanceError, ValidationError
from tests.support import maximal
from tests.support.factories import concept_ref
from tests.support.json_schema import (
    SchemaKind,
    SchemaVersion,
    assert_valid_sdmx_json,
    compile_json_schema,
    is_declared_time_bound,
)

pytestmark = pytest.mark.unit

_SCHEMAS = Path(__file__).parents[1] / "schemas" / "sdmx_json"


def _formats() -> set[str]:
    found: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            fmt = node.get("format")
            if isinstance(fmt, str):
                found.add(fmt)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for path in _SCHEMAS.glob("*/*.json"):
        walk(json.loads(path.read_text()))
    return found


#: Per format the schemas use: a value it admits, and one it refuses.
_SAMPLES: dict[str, tuple[str, str]] = {
    "date-time": ("2020-01-01T00:00:00Z", "2020-01-01T00:00:00"),
    "date": ("2020-01-01", "2020-13-01"),
    "email": ("a@example.org", "no-at-sign"),
    "uri": ("https://example.org/x", "not a uri"),
    "uri-reference": ("/x", "a b"),
}

_COMPILED: list[tuple[SchemaKind, SchemaVersion]] = [
    ("structure", "2.0"),
    ("structure", "2.1"),
    ("metadata", "2.0"),
    ("data", "2.0"),
]


def test_the_schemas_use_the_formats_this_checks() -> None:
    """Exact: a schema refresh that adds a format must add a sample for it here."""
    assert _formats() == set(_SAMPLES)


@pytest.mark.parametrize(("kind", "version"), _COMPILED)
@pytest.mark.parametrize("fmt", sorted(_SAMPLES))
def test_every_format_a_schema_uses_is_checked(kind: SchemaKind, version: SchemaVersion, fmt: str) -> None:
    """Through the compiled validator itself, so a dropped `format_checker` fails here too.

    The admitted value is the discrimination check: a validator refusing
    everything would refuse the malformed one as well.
    """
    good, bad = _SAMPLES[fmt]
    checks = compile_json_schema(kind, version).evolve(schema={"format": fmt})
    assert checks.is_valid(good)
    assert not checks.is_valid(bad), f"no library checks {fmt!r}; install jsonschema[format-nongpl]"


# ── what the formats found, and what was done about each ──────────────────


def _maximal_doc() -> dict[str, Any]:
    return msgspec.json.decode(json_writer.write(maximal.message()), type=dict[str, Any])


def _item_map(doc: dict[str, Any]) -> dict[str, Any]:
    item_map: dict[str, Any] = doc["data"]["conceptSchemeMaps"][0]["itemMaps"][0]
    return item_map


def test_a_map_validity_date_is_written_as_midnight_utc() -> None:
    """SDMX-ML 3.0 has `xs:date`, SDMX-JSON `date-time`: the date's start, in UTC."""
    item_map = _item_map(_maximal_doc())
    assert (item_map["validFrom"], item_map["validTo"]) == ("2020-01-01T00:00:00Z", "2030-12-31T00:00:00Z")


@pytest.mark.parametrize("text", ["2020-01-01T00:00:00Z", "2020-01-01T00:00:00+00:00"])
def test_midnight_utc_reads_back_as_its_date(text: str) -> None:
    doc = _maximal_doc()
    _item_map(doc)["validFrom"] = text
    read = sl.parse(msgspec.json.encode(doc))
    assert isinstance(read, sl.StructureMessage)
    (csm,) = read.concept_scheme_maps
    assert csm.maps[0].valid_from == date(2020, 1, 1)


@pytest.mark.parametrize(
    "text",
    [
        "2020-01-01T12:00:00Z",  # a time of day the model cannot hold
        "2020-01-01T00:00:00+02:00",  # midnight somewhere else is not this date's start
        "2020-01-01T00:00:00",  # no zone
        "2020-01-01",  # SDMX-ML's form, not SDMX-JSON's
    ],
)
def test_any_other_instant_is_refused_rather_than_truncated(text: str) -> None:
    doc = _maximal_doc()
    _item_map(doc)["validFrom"] = text
    with pytest.raises(SdmxConformanceError, match=r"validFrom.*is not midnight UTC, and the model holds a date"):
        sl.parse(msgspec.json.encode(doc))


def _constraint(period: str) -> DataConstraint:
    flow = sl.Dataflow(id="DF", maintainer="AG", version="1.0", name={"en": "df"})
    return DataConstraint(
        id="CC",
        maintainer="AG",
        version="1.0",
        name={"en": "cc"},
        constraint_type=ConstraintType.ALLOWED,
        attachment=ConstraintAttachment(dataflows=(sl.Ref[sl.Dataflow].to(flow),)),
        regions=(
            CubeRegion(keys=(KeyValue(dimension_id="TIME_PERIOD", time_range=TimeRange(end=TimeBound(period))),)),
        ),
    )


def test_a_period_with_no_zone_is_refused_by_name() -> None:
    """Valid `xs:dateTime` in SDMX-ML, and nothing SDMX-JSON can write without inventing a zone."""
    with pytest.raises(ValidationError) as caught:
        json_writer.write(sl.StructureMessage(constraints=[_constraint("2025-06-30T00:00:00")]))
    assert [issue.path for issue in caught.value.issues] == [
        "DataConstraint:CC.regions[0].keys[TIME_PERIOD].time_range.end.period"
    ]


@pytest.mark.parametrize("period", ["2025-06-30T00:00:00Z", "2025-06-30T00:00:00-05:00", "2025-06-30", "2025-Q2"])
def test_a_period_json_can_write_is_written_as_given(period: str) -> None:
    out = json_writer.write(sl.StructureMessage(constraints=[_constraint(period)]))
    assert_valid_sdmx_json(out, compile_json_schema("structure", "2.0"))
    (key,) = msgspec.json.decode(out, type=dict[str, Any])["data"]["dataConstraints"][0]["cubeRegions"][0]["keyValues"]
    assert key["timeRange"] == {"beforePeriod": {"period": period}}


def test_the_1_0_constraint_writer_refuses_a_period_with_no_zone() -> None:
    with pytest.raises(ValidationError) as caught:
        write_contentconstraint(_constraint("2025-06-30T00:00:00"))
    assert [issue.path for issue in caught.value.issues] == [
        "DataConstraint:CC.regions[0].keys[TIME_PERIOD].time_range.end.period"
    ]


# ── `prepared` ────────────────────────────────────────────────────────────

_NAIVE = datetime.fromisoformat("2026-06-16T00:00:00")


def _time_dsd() -> tuple[sl.DataStructure, sl.Dataflow]:
    dsd = sl.DataStructure(
        id="DSD_TIME",
        maintainer="TEST",
        version="1.0",
        name={"en": "time"},
        dimensions=[sl.Dimension(id="FREQ", concept=concept_ref("FREQ"))],
        time_dimension=TimeDimension(id="TIME_PERIOD", concept=concept_ref("TIME_PERIOD")),
        measures=[sl.Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )
    flow = sl.Dataflow(
        id="DF_TIME", maintainer="TEST", version="1.0", name={"en": "time"}, structure=sl.Ref[sl.DataStructure].to(dsd)
    )
    return dsd, flow


def _data(write: Callable[..., Iterable[bytes]], prepared: datetime) -> dict[str, Any]:
    dsd, flow = _time_dsd()
    batch = pa.record_batch({"FREQ": ["A", "A"], "TIME_PERIOD": ["2021", "2022-Q1"], "OBS_VALUE": [1.0, 2.0]})
    out = write([batch], dsd, sl.Ref[sl.Dataflow].to(flow), message_id="m", prepared=prepared, sender_id="S", lang="en")
    return msgspec.json.decode(b"".join(out), type=dict[str, Any])


@pytest.mark.parametrize("write", [data_v1.write_sdmx_json_data, data_v2.write_sdmx_json_data], ids=["1.0", "2.0"])
def test_a_naive_prepared_is_refused(write: Callable[..., Iterable[bytes]]) -> None:
    with pytest.raises(ValueError, match=r"^prepared must be timezone-aware, got naive '2026-06-16T00:00:00'"):
        _data(write, _NAIVE)


def test_the_1_0_constraint_writer_refuses_a_naive_prepared() -> None:
    with pytest.raises(ValueError, match=r"^prepared must be timezone-aware"):
        write_contentconstraint(_constraint("2025"), prepared=_NAIVE)


def test_an_aware_prepared_is_written_in_its_own_zone() -> None:
    zone = timezone(timedelta(hours=-5))
    meta = _data(data_v2.write_sdmx_json_data, datetime(2026, 6, 16, 9, 30, tzinfo=zone))["meta"]
    assert meta["prepared"] == "2026-06-16T09:30:00-05:00"


# ── the declared deviation: a time dimension value's bounds ───────────────


def test_the_time_bounds_are_the_data_message_s_only_violation() -> None:
    """Exact, and not empty: the excusal is live, and excuses these and nothing else."""
    doc = _data(data_v2.write_sdmx_json_data, datetime(2026, 6, 16, tzinfo=UTC))
    errors = list(compile_json_schema("data", "2.0").iter_errors(doc))
    assert sorted("/".join(map(str, e.absolute_path)) for e in errors) == sorted(
        f"data/structures/0/dimensions/observation/1/values/{i}/{bound}" for i in (0, 1) for bound in ("start", "end")
    )
    assert all(is_declared_time_bound(e) for e in errors)
    assert_valid_sdmx_json(doc, compile_json_schema("data", "2.0"))


@pytest.mark.parametrize("bound", ["not a time", "2021-13-01T00:00:00"])
def test_a_malformed_time_bound_is_not_excused(bound: str) -> None:
    """The sabotage half: the excusal is for a missing zone, not for any value at all."""
    doc = _data(data_v2.write_sdmx_json_data, datetime(2026, 6, 16, tzinfo=UTC))
    doc["data"]["structures"][0]["dimensions"]["observation"][1]["values"][0]["start"] = bound
    with pytest.raises(AssertionError, match=r"values/0/start"):
        assert_valid_sdmx_json(doc, compile_json_schema("data", "2.0"))


def test_a_zoneless_date_time_elsewhere_is_not_excused() -> None:
    doc = _data(data_v2.write_sdmx_json_data, datetime(2026, 6, 16, tzinfo=UTC))
    doc["meta"]["prepared"] = "2026-06-16T00:00:00"
    with pytest.raises(AssertionError, match=r"/meta/prepared"):
        assert_valid_sdmx_json(doc, compile_json_schema("data", "2.0"))
