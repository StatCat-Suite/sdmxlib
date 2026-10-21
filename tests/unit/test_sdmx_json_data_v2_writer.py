"""Unit tests for the streaming SDMX-JSON 2.0.0 data-message writer.

Mirrors the SDMX-CSV writer test shape: build a tiny in-memory DSD +
binding, feed in a single Arrow record batch, drain the generator,
parse the bytes back, and assert on structure. Every test also runs
the emitted bytes through the vendored SDMX-JSON 2.0 JSON-Schema so
conformance regressions surface immediately (parallel to the
SDMX-ML writer's XSD round-trip tests).
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import msgspec
import pyarrow as pa
import pytest

from sdmxlib.formats.sdmx_json.data_v2 import write_sdmx_json_data
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure, Dimension, Measure
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.ref import Ref
from tests.support.factories import concept_ref
from tests.support.json_schema import assert_valid_sdmx_json, compile_json_schema

if TYPE_CHECKING:
    from collections.abc import Iterable

from tests.support.factories import (
    FREQ_CODES_NAMED,
    STATUS_CODES_NAMED,
    data_dsd,
    dsd_binding,
)

pytestmark = pytest.mark.unit


# ── fixtures ─────────────────────────────────────────────────────────────────


def _build_dsd(*, with_attribute: bool = False) -> tuple[DataStructure, Dataflow]:
    """The shared FREQ x REF_AREA data shape, with this module's codes.

    Named codes throughout — the fullest of the three variants.
    """
    return data_dsd(
        freq_codes=FREQ_CODES_NAMED,
        status_codes=STATUS_CODES_NAMED if with_attribute else None,
    )


def _drain(gen: Iterable[bytes]) -> dict[str, Any]:
    """Concatenate generator output, schema-validate, and parse."""
    out = b"".join(gen)
    assert_valid_sdmx_json(out, compile_json_schema("data", version="2.0"))
    return msgspec.json.decode(out, type=dict[str, Any])


# ── tests ────────────────────────────────────────────────────────────────────


def test_single_batch_flat_layout() -> None:
    dsd, flow = _build_dsd()
    binding = dsd_binding(dsd, flow)
    batch = pa.record_batch(
        {
            "FREQ": ["A", "Q", "A"],
            "REF_AREA": ["CA", "US", "US"],
            "OBS_VALUE": [1.0, 2.0, 3.0],
        }
    )
    doc = _drain(
        write_sdmx_json_data(
            [batch],
            dsd,
            binding.dataflow,
            message_id="m1",
            prepared=datetime(2026, 6, 16, tzinfo=UTC),
            sender_id="SENDER",
            lang="en",
        )
    )

    dataset = doc["data"]["dataSets"][0]
    assert dataset["action"] == "Information"
    assert dataset["structure"] == 0
    # Observation keys are colon-joined positions: FREQ (enumerated, seeded
    # from the codelist → A=0, Q=1) x REF_AREA (lazy, CA seen first → 0).
    assert dataset["observations"] == {
        "0:0": [1.0],
        "1:1": [2.0],
        "0:1": [3.0],
    }
    structure = doc["data"]["structures"][0]
    freq_values = [v["id"] for v in structure["dimensions"]["observation"][0]["values"]]
    # Only the codes that actually appear in the batch are emitted — the
    # codelist's third code (M) is suppressed even though FREQ is
    # enumerated. Per spec, the values array can be limited to codes
    # referenced by observations. The pre-bloat fix used to emit M too.
    assert freq_values == ["A", "Q"]
    area_values = [v["id"] for v in structure["dimensions"]["observation"][1]["values"]]
    # Free-text dim — values are appended in first-sight order.
    assert area_values == ["CA", "US"]


def test_multiple_batches_continue_streaming() -> None:
    """The observations map must be valid JSON across batch boundaries —
    separator handling between the last obs of batch N and the first of
    batch N+1 is the tricky bit.
    """
    dsd, flow = _build_dsd()
    binding = dsd_binding(dsd, flow)
    b1 = pa.record_batch({"FREQ": ["A"], "REF_AREA": ["CA"], "OBS_VALUE": [1.0]})
    b2 = pa.record_batch({"FREQ": ["Q", "M"], "REF_AREA": ["US", "FR"], "OBS_VALUE": [2.0, 3.0]})
    doc = _drain(
        write_sdmx_json_data(
            [b1, b2],
            dsd,
            binding.dataflow,
            message_id="m",
            prepared=datetime(2026, 6, 16, tzinfo=UTC),
            sender_id="S",
            lang="en",
        )
    )
    obs = doc["data"]["dataSets"][0]["observations"]
    assert set(obs) == {"0:0", "1:1", "2:2"}


def test_empty_stream_emits_parseable_envelope() -> None:
    """No batches at all — the envelope still has to round-trip through
    a JSON parser, even though it doesn't satisfy schema's
    ``dimValues.minItems: 1`` constraint (which mandates at least one
    value per dimension). Empty SDMX-JSON data messages are a degenerate
    shape; we don't emit synthetic values to placate the schema since
    that would lie to the consumer about what was observed.
    """
    cl_freq = Codelist(
        id="CL_FREQ",
        maintainer="TEST",
        version="1.0",
        name=InternationalString(en="Frequency"),
        codes=[Code(name={"en": "A"}, id="A")],
    )
    cl_area = Codelist(
        id="CL_AREA",
        maintainer="TEST",
        version="1.0",
        name=InternationalString(en="Reference area"),
        codes=[Code(name={"en": "CA"}, id="CA")],
    )
    dsd = DataStructure(
        id="DSD_TEST",
        maintainer="TEST",
        version="1.0",
        name=InternationalString(en="Test DSD"),
        dimensions=[
            Dimension(id="FREQ", representation=Ref[Codelist].to(cl_freq), concept=concept_ref("FREQ")),
            Dimension(id="REF_AREA", representation=Ref[Codelist].to(cl_area), concept=concept_ref("REF_AREA")),
        ],
        measures=[Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )
    flow = Dataflow(
        id="DF_TEST",
        maintainer="TEST",
        version="1.0",
        name=InternationalString(en="Test Dataflow"),
        structure=Ref[DataStructure].to(dsd),
    )
    binding = dsd_binding(dsd, flow)
    payload = b"".join(
        write_sdmx_json_data(
            [],
            dsd,
            binding.dataflow,
            message_id="m",
            prepared=datetime(2026, 6, 16, tzinfo=UTC),
            sender_id="S",
            lang="en",
        )
    )
    doc = msgspec.json.decode(payload)
    assert doc["data"]["dataSets"][0]["observations"] == {}
    # Both dim value arrays are empty — no observations means no codes.
    structure = doc["data"]["structures"][0]
    for dim in structure["dimensions"]["observation"]:
        assert dim["values"] == []


def test_observation_level_attribute_positions() -> None:
    """Enumerated obs-level attributes serialise as integer positions into
    their codelist, appended to the value array after the measure."""
    dsd, flow = _build_dsd(with_attribute=True)
    binding = dsd_binding(dsd, flow)
    batch = pa.record_batch(
        {
            "FREQ": ["A", "A"],
            "REF_AREA": ["CA", "CA"],
            "OBS_VALUE": [1.0, 2.0],
            "OBS_STATUS": ["A", "P"],
        }
    )
    doc = _drain(
        write_sdmx_json_data(
            [batch],
            dsd,
            binding.dataflow,
            message_id="m",
            prepared=datetime(2026, 6, 16, tzinfo=UTC),
            sender_id="S",
            lang="en",
        )
    )
    obs = doc["data"]["dataSets"][0]["observations"]
    # Two obs differ only in OBS_STATUS; the key is the same so the
    # second overwrites the first in JSON-map semantics. The writer
    # emits both — JSON parsers keep the last. This is the
    # documented "flat layout" behaviour.
    assert obs["0:0"] == [2.0, 1]  # last wins; OBS_STATUS=P is position 1
    attrs_block = doc["data"]["structures"][0]["attributes"]["observation"]
    assert attrs_block[0]["id"] == "OBS_STATUS"
    assert [v["id"] for v in attrs_block[0]["values"]] == ["A", "P"]


def test_missing_column_raises_keyerror() -> None:
    dsd, flow = _build_dsd()
    binding = dsd_binding(dsd, flow)
    batch = pa.record_batch({"FREQ": ["A"], "OBS_VALUE": [1.0]})  # missing REF_AREA
    with pytest.raises(KeyError, match="REF_AREA"):
        b"".join(
            write_sdmx_json_data(
                [batch],
                dsd,
                binding.dataflow,
                message_id="m",
                prepared=datetime(2026, 6, 16, tzinfo=UTC),
                sender_id="S",
                lang="en",
            )
        )


def test_multi_measure_rejected() -> None:
    """The initial scope is single-measure (OBS_VALUE). Multi-measure DSDs
    are an SDMX 3.0 feature and require a different value-array layout —
    deliberately raise rather than silently emit a malformed message."""
    dsd, flow = _build_dsd()
    # Add a second measure to trip the guard.
    dsd2 = DataStructure(
        id=dsd.id,
        maintainer=dsd.maintainer,
        version=dsd.version,
        name=dsd.name,
        dimensions=list(dsd.dimensions),
        attributes=list(dsd.attributes),
        measures=[
            Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE")),
            Measure(id="OBS_COUNT", concept=concept_ref("OBS_COUNT")),
        ],
    )
    flow2 = Dataflow(
        id=flow.id,
        maintainer=flow.maintainer,
        version=flow.version,
        name=flow.name,
        structure=Ref[DataStructure].to(dsd2),
    )
    binding = dsd_binding(dsd2, flow2)
    batch = pa.record_batch({"FREQ": ["A"], "REF_AREA": ["CA"], "OBS_VALUE": [1.0], "OBS_COUNT": [1.0]})
    with pytest.raises(ValueError, match="exactly one measure"):
        b"".join(
            write_sdmx_json_data(
                [batch],
                dsd2,
                binding.dataflow,
                message_id="m",
                prepared=datetime(2026, 6, 16, tzinfo=UTC),
                sender_id="S",
                lang="en",
            )
        )


def test_nan_serialises_as_null() -> None:
    """msgspec encodes NaN (and inf) as JSON null, which is what SDMX-JSON
    consumers expect for a missing observation value."""
    dsd, flow = _build_dsd()
    binding = dsd_binding(dsd, flow)
    batch = pa.record_batch({"FREQ": ["A"], "REF_AREA": ["CA"], "OBS_VALUE": [math.nan]})
    doc = _drain(
        write_sdmx_json_data(
            [batch],
            dsd,
            binding.dataflow,
            message_id="m",
            prepared=datetime(2026, 6, 16, tzinfo=UTC),
            sender_id="S",
            lang="en",
        )
    )
    assert doc["data"]["dataSets"][0]["observations"]["0:0"] == [None]
