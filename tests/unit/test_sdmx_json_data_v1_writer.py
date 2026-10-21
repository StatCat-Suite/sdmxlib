"""Unit tests for the streaming SDMX-JSON 1.0 data-message writer.

Mirrors `test_sdmx_json_data_v2_writer` — same DSD fixtures, same
batch fixtures, same conformance gate against the vendored 1.0
JSON-Schema. Asserts on the envelope-shape deltas vs 2.0
(``header`` not ``meta``, ``structure`` singular not
``structures[]``, no ``data`` wrapper).
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, override

import msgspec
import pyarrow as pa
import pytest

from sdmxlib.formats.sdmx_json.data_v1 import write_sdmx_json_data
from sdmxlib.model.codelist import Code
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure
from sdmxlib.model.ref import Ref
from tests.support.json_schema import assert_valid_sdmx_json, compile_json_schema

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

from tests.support.factories import (
    FREQ_CODES_NAMED,
    STATUS_CODES_BARE,
    data_dsd,
    dsd_binding,
)

pytestmark = pytest.mark.unit


def _build_dsd(*, with_attribute: bool = False) -> tuple[DataStructure, Dataflow]:
    """The shared FREQ x REF_AREA data shape, with this module's codes.

    Two FREQ codes rather than three, and unnamed status codes. The
    divergence from the v2 module predates #211 and is undocumented.
    """
    return data_dsd(
        freq_codes=FREQ_CODES_NAMED[:2],
        status_codes=STATUS_CODES_BARE if with_attribute else None,
    )


def _drain(gen: Iterable[bytes]) -> dict[str, Any]:
    out = b"".join(gen)
    assert_valid_sdmx_json(out, compile_json_schema("data", version="1.0"))
    return msgspec.json.decode(out, type=dict[str, Any])


def test_envelope_shape_matches_1_0_spec() -> None:
    """1.0 puts ``header`` / ``structure`` / ``dataSets`` at the top
    level — no ``meta`` / ``data`` wrapper, no ``structures`` array.
    """
    dsd, flow = _build_dsd()
    binding = dsd_binding(dsd, flow)
    batch = pa.record_batch({"FREQ": ["A"], "REF_AREA": ["CA"], "OBS_VALUE": [1.0]})
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
    assert set(doc) == {"header", "structure", "dataSets"}
    assert doc["header"]["id"] == "m1"
    assert doc["header"]["sender"]["id"] == "SENDER"
    # `structure` is the singular form — a single object, not an array
    assert isinstance(doc["structure"], dict)
    assert "dimensions" in doc["structure"]


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
            message_id="m",
            prepared=datetime(2026, 6, 16, tzinfo=UTC),
            sender_id="S",
            lang="en",
        )
    )
    assert doc["dataSets"][0]["observations"] == {
        "0:0": [1.0],
        "1:1": [2.0],
        "0:1": [3.0],
    }
    obs_dims = doc["structure"]["dimensions"]["observation"]
    assert [d["id"] for d in obs_dims] == ["FREQ", "REF_AREA"]


def test_multiple_batches_continue_streaming() -> None:
    dsd, flow = _build_dsd()
    binding = dsd_binding(dsd, flow)
    b1 = pa.record_batch({"FREQ": ["A"], "REF_AREA": ["CA"], "OBS_VALUE": [1.0]})
    b2 = pa.record_batch({"FREQ": ["Q"], "REF_AREA": ["US"], "OBS_VALUE": [2.0]})
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
    assert set(doc["dataSets"][0]["observations"]) == {"0:0", "1:1"}


def test_observation_level_attribute_positions() -> None:
    dsd, flow = _build_dsd(with_attribute=True)
    binding = dsd_binding(dsd, flow)
    batch = pa.record_batch(
        {
            "FREQ": ["A"],
            "REF_AREA": ["CA"],
            "OBS_VALUE": [1.0],
            "OBS_STATUS": ["P"],
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
    # Only P is observed in this batch — position is purely arrival-order
    # now (no pre-seed from the codelist enumeration), so OBS_STATUS=P is
    # position 0 rather than the codelist-order position 1.
    assert doc["dataSets"][0]["observations"]["0:0"] == [1.0, 0]
    attrs_block = doc["structure"]["attributes"]["observation"]
    assert attrs_block[0]["id"] == "OBS_STATUS"
    assert [v["id"] for v in attrs_block[0]["values"]] == ["P"]


def test_missing_column_raises_keyerror() -> None:
    dsd, flow = _build_dsd()
    binding = dsd_binding(dsd, flow)
    batch = pa.record_batch({"FREQ": ["A"], "OBS_VALUE": [1.0]})
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


def test_nan_serialises_as_null() -> None:
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
    assert doc["dataSets"][0]["observations"]["0:0"] == [None]


def test_init_does_not_walk_full_codelist() -> None:
    """The writer's per-component label cache MUST be lazy.

    Regression: a previous implementation pre-populated a dict of every code
    in every dimension's resolved codelist at ``ComponentTable.__init__``
    time. For census-scale codelists (~580k codes) this dominated wall time
    on small data responses — a single-row /data query spent multiple
    seconds populating a label cache it would never use more than once.

    This test fakes a codelist whose ``.codes`` iteration raises and asserts
    that writing succeeds anyway; the only acceptable use of the codelist is
    an O(1) ``codes.get(code_id)`` per *observed* code, at ``to_json()``.

    Eager only, and that is how it stayed green through #410: on an eager
    `Codelist` `materialize()` returns ``self`` and iterates nothing, so this
    passed while every lazy codelist was read in full per message. The lazy
    half is `tests/unit/test_sdmx_json_data_lazy_codelists.py`.
    """
    from sdmxlib.formats.sdmx_json._data_common import AnnotationPool, ComponentTable
    from sdmxlib.model.collections import ItemList

    class TripwireItemList(ItemList[Code]):
        @override
        def __iter__(self) -> Iterator[Code]:
            msg = "ComponentTable must not iterate the codelist at init"
            raise AssertionError(msg)

    # Build a real DSD with a real (small) codelist so the rest of the
    # plumbing works, then swap the codelist's ``.codes`` for the tripwire.
    dsd, _flow = _build_dsd()
    freq_dim = dsd.dimensions["FREQ"]
    assert isinstance(freq_dim.representation, Ref)
    cl = freq_dim.codelist
    # Into the very codelist the DSD's reference reaches, so a test-only write
    # into a frozen object (#492): a copy would not be the one reached, and
    # `attrs.evolve` runs the codes converter, which iterates — the tripwire.
    object.__setattr__(cl, "codes", TripwireItemList(list(cl.codes)))

    # Nothing may iterate; to_json() should still emit the right label for an
    # observed code via O(1) get().
    # The pool is required (#366) so no call site can quietly build its own —
    # indices into an unreachable array would still look well-formed. Passing a
    # fresh one here also keeps the tripwire meaningful: registering a
    # *component's* annotations at init is fine, walking the codelist is not.
    table = ComponentTable(["FREQ", "REF_AREA"], dsd, kind="dimension", lang="en", annotations=AnnotationPool())
    pos = table.position("FREQ", "A")
    assert pos == 0
    freq = table.to_json()[0]
    assert freq["values"] == [{"id": "A", "name": "Annual"}]
