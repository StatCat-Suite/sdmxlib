"""Tests for FREQ-driven multi-period TIME_PERIOD bounds (issue #149).

A bare TIME_PERIOD id like ``"2021"`` is only a start anchor; the FREQ
dimension's code supplies the duration. ``A5`` means the period spans
2021-2025, not just 2021. These tests cover the duration toolkit and the
end-to-end writer output via both the standard/``A{n}`` convention and an
explicit ``duration`` annotation, plus the mixed-FREQ fallback.
"""

from __future__ import annotations

from datetime import UTC, datetime

import msgspec
import pyarrow as pa
import pytest

from sdmxlib.formats.sdmx_json._data_common import (
    _bounds_from_anchor,
    _convention_duration,
    _Duration,
    _parse_iso_duration,
)
from sdmxlib.formats.sdmx_json.data_v2 import write_sdmx_json_data
from sdmxlib.model.annotations import Annotation, Annotations
from sdmxlib.model.binding import DataflowBinding
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure, Dimension, Measure, TimeDimension
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.ref import Ref
from tests.support.factories import concept_ref

pytestmark = pytest.mark.unit


# ── duration toolkit unit coverage ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("freq_id", "expected"),
    [
        ("A", _Duration(years=1)),
        ("Y", _Duration(years=1)),
        ("S", _Duration(months=6)),
        ("Q", _Duration(months=3)),
        ("M", _Duration(months=1)),
        ("W", _Duration(days=7)),
        ("D", _Duration(days=1)),
        ("H", _Duration(hours=1)),
        ("A2", _Duration(years=2)),
        ("A5", _Duration(years=5)),
        ("A10", _Duration(years=10)),
        ("S2", _Duration(months=12)),
        ("Q2", _Duration(months=6)),
        ("W2", _Duration(days=14)),
    ],
)
def test_convention_duration(freq_id: str, expected: _Duration) -> None:
    assert _convention_duration(freq_id) == expected


@pytest.mark.parametrize("freq_id", ["", "X", "FOO", "A5B", "5"])
def test_convention_duration_unrecognised(freq_id: str) -> None:
    assert _convention_duration(freq_id) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("P5Y", _Duration(years=5)),
        ("P1M", _Duration(months=1)),
        ("PT1H", _Duration(hours=1)),
        ("P2W", _Duration(days=14)),
        ("P1Y6M", _Duration(years=1, months=6)),
        ("P10D", _Duration(days=10)),
    ],
)
def test_parse_iso_duration(text: str, expected: _Duration) -> None:
    assert _parse_iso_duration(text) == expected


@pytest.mark.parametrize("text", ["P", "PT", "", "5Y", "P5X", "garbage"])
def test_parse_iso_duration_invalid(text: str) -> None:
    assert _parse_iso_duration(text) is None


def test_bounds_from_anchor_extends_end() -> None:
    assert _bounds_from_anchor("2021", _Duration(years=5)) == (
        "2021-01-01T00:00:00",
        "2025-12-31T23:59:59",
    )


def test_bounds_from_anchor_none_duration_is_id_shape() -> None:
    assert _bounds_from_anchor("2021", None) == ("2021-01-01T00:00:00", "2021-12-31T23:59:59")


def test_bounds_from_anchor_unrecognised_id() -> None:
    # <start>/<duration> form — the consumer parses it, we emit nothing.
    assert _bounds_from_anchor("2021-01-01/P5Y", _Duration(years=5)) is None


# ── writer integration ────────────────────────────────────────────────────────


def _dsd(freq_codes: list[Code]) -> tuple[DataStructure, Dataflow]:
    cl_freq = Codelist(
        id="CL_FREQ",
        maintainer="TEST",
        version="1.0",
        name=InternationalString(en="Frequency"),
        codes=freq_codes,
    )
    dsd = DataStructure(
        id="DSD_TIME",
        maintainer="TEST",
        version="1.0",
        name=InternationalString(en="DSD with TIME_PERIOD"),
        dimensions=[Dimension(id="FREQ", representation=Ref[Codelist].to(cl_freq), concept=concept_ref("FREQ"))],
        time_dimension=TimeDimension(id="TIME_PERIOD", concept=concept_ref("TIME_PERIOD")),
        measures=[Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )
    flow = Dataflow(
        id="DF_TIME",
        maintainer="TEST",
        version="1.0",
        name=InternationalString(en="Time dataflow"),
        structure=Ref[DataStructure].to(dsd),
    )
    return dsd, flow


def _time_values(dsd: DataStructure, flow: Dataflow, batch: pa.RecordBatch) -> dict[str, dict]:  # type: ignore[type-arg]
    binding = DataflowBinding.from_dsd(dsd, dataflow=Ref[Dataflow].to(flow), schema="data", table="obs")
    doc = msgspec.json.decode(
        b"".join(
            write_sdmx_json_data(
                [batch],
                dsd,
                binding.dataflow,
                message_id="m",
                prepared=datetime(2026, 6, 17, tzinfo=UTC),
                sender_id="S",
                lang="en",
            )
        )
    )
    dims = doc["data"]["structures"][0]["dimensions"]["observation"]
    time_dim = next(d for d in dims if d["id"] == "TIME_PERIOD")
    return {v["id"]: v for v in time_dim["values"]}


def test_multi_year_freq_via_convention() -> None:
    """FREQ=A5 + TIME_PERIOD=2021 covers 2021-2025 by the A{n} convention."""
    dsd, flow = _dsd([Code(name={"en": "A5"}, id="A5")])
    batch = pa.record_batch({"FREQ": ["A5", "A5"], "TIME_PERIOD": ["2021", "2022"], "OBS_VALUE": [1.0, 2.0]})
    values = _time_values(dsd, flow, batch)
    assert values["2021"]["start"] == "2021-01-01T00:00:00"
    assert values["2021"]["end"] == "2025-12-31T23:59:59"
    assert values["2022"]["end"] == "2026-12-31T23:59:59"


def test_duration_annotation_overrides_convention() -> None:
    """An explicit ``duration`` annotation beats the code-id convention."""
    code = Code(
        name={"en": "A"},
        id="A",  # convention says P1Y...
        annotations=Annotations([Annotation(type="duration", text=InternationalString(en="P3Y"))]),  # ...but P3Y wins
    )
    dsd, flow = _dsd([code])
    batch = pa.record_batch({"FREQ": ["A"], "TIME_PERIOD": ["2021"], "OBS_VALUE": [1.0]})
    values = _time_values(dsd, flow, batch)
    assert values["2021"]["end"] == "2023-12-31T23:59:59"


def test_annotation_drives_non_convention_code() -> None:
    """A code id that the convention can't parse still gets bounds from its annotation."""
    code = Code(
        name={"en": "QUINQUENNIAL"},
        id="QUINQUENNIAL",
        annotations=Annotations([Annotation(type="DURATION", text=InternationalString(en="P5Y"))]),
    )
    dsd, flow = _dsd([code])
    batch = pa.record_batch({"FREQ": ["QUINQUENNIAL"], "TIME_PERIOD": ["2021"], "OBS_VALUE": [1.0]})
    values = _time_values(dsd, flow, batch)
    assert values["2021"]["end"] == "2025-12-31T23:59:59"


def test_standard_annual_unchanged() -> None:
    """FREQ=A keeps single-year bounds — the convention matches the id shape."""
    dsd, flow = _dsd([Code(name={"en": "A"}, id="A")])
    batch = pa.record_batch({"FREQ": ["A"], "TIME_PERIOD": ["2021"], "OBS_VALUE": [1.0]})
    values = _time_values(dsd, flow, batch)
    assert values["2021"]["end"] == "2021-12-31T23:59:59"


def test_monthly_freq_matches_id_shape() -> None:
    """Sub-annual FREQ still produces the id-shape bound (P1M = the month)."""
    dsd, flow = _dsd([Code(name={"en": "M"}, id="M")])
    batch = pa.record_batch({"FREQ": ["M"], "TIME_PERIOD": ["2021-07"], "OBS_VALUE": [1.0]})
    values = _time_values(dsd, flow, batch)
    assert values["2021-07"]["start"] == "2021-07-01T00:00:00"
    assert values["2021-07"]["end"] == "2021-07-31T23:59:59"


def test_mixed_freq_collision_falls_back_to_id_shape() -> None:
    """When one TIME_PERIOD id co-occurs with conflicting durations, the
    shared structures-block slot can't disambiguate — fall back to the
    safe single-period (id-shape) bound."""
    dsd, flow = _dsd([Code(name={"en": "A"}, id="A"), Code(name={"en": "A5"}, id="A5")])
    batch = pa.record_batch({"FREQ": ["A", "A5"], "TIME_PERIOD": ["2021", "2021"], "OBS_VALUE": [1.0, 2.0]})
    values = _time_values(dsd, flow, batch)
    assert values["2021"]["end"] == "2021-12-31T23:59:59"


def test_no_freq_dimension_uses_id_shape() -> None:
    """No frequency dimension → today's id-shape behaviour is preserved."""
    dsd = DataStructure(
        id="DSD_NOFREQ",
        maintainer="TEST",
        version="1.0",
        name=InternationalString(en="No FREQ"),
        dimensions=[Dimension(id="REF_AREA", concept=concept_ref("REF_AREA"))],
        time_dimension=TimeDimension(id="TIME_PERIOD", concept=concept_ref("TIME_PERIOD")),
        measures=[Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )
    flow = Dataflow(
        id="DF_NOFREQ",
        maintainer="TEST",
        version="1.0",
        name=InternationalString(en="No FREQ flow"),
        structure=Ref[DataStructure].to(dsd),
    )
    batch = pa.record_batch({"REF_AREA": ["CA"], "TIME_PERIOD": ["2021"], "OBS_VALUE": [1.0]})
    values = _time_values(dsd, flow, batch)
    assert values["2021"]["end"] == "2021-12-31T23:59:59"


def test_start_duration_id_form_emits_no_bounds() -> None:
    """The <start>/<duration> id is left for the consumer to parse, even
    with FREQ context — we never fabricate bounds for it."""
    dsd, flow = _dsd([Code(name={"en": "A5"}, id="A5")])
    batch = pa.record_batch({"FREQ": ["A5"], "TIME_PERIOD": ["2021-01-01/P5Y"], "OBS_VALUE": [1.0]})
    values = _time_values(dsd, flow, batch)
    assert "start" not in values["2021-01-01/P5Y"]
    assert "end" not in values["2021-01-01/P5Y"]
