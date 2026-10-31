"""Unit tests for the SDMX-JSON 1.0 / 2.0 data-message readers.

Strategy: round-trip via the writer. The writer is already
conformance-tested against the vendored JSON schemas; if the reader
can recover the same observation rows, the encode/decode pair is
internally consistent. A small assert on the upstream sample
shape catches regressions where the writer's output drifts from the
real-world wire format (since the round-trip alone wouldn't notice).
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

import msgspec
import polars as pl
import pyarrow as pa
import pytest

from sdmxlib.formats.sdmx_json.data_v1 import (
    read_observations as read_v1,
)
from sdmxlib.formats.sdmx_json.data_v1 import (
    write_sdmx_json_data as write_v1,
)
from sdmxlib.formats.sdmx_json.data_v2 import (
    read_observations as read_v2,
)
from sdmxlib.formats.sdmx_json.data_v2 import (
    write_sdmx_json_data as write_v2,
)
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure
from sdmxlib.model.errors import SdmxParseError
from tests.support.factories import (
    FREQ_CODES_BARE,
    STATUS_CODES_BARE,
    data_dsd,
    dsd_binding,
)
from tests.support.repo import ROOT

pytestmark = pytest.mark.unit


def _build_dsd(*, with_attribute: bool = False) -> tuple[DataStructure, Dataflow]:
    """The shared FREQ x REF_AREA data shape, with this module's codes.

    Codes carry no names here, so label lookup falls back to the code
    id — a different path from the two writer modules. See #211.
    """
    return data_dsd(
        freq_codes=FREQ_CODES_BARE,
        status_codes=STATUS_CODES_BARE if with_attribute else None,
    )


def _round_trip_v2(batch: pa.RecordBatch, dsd: DataStructure, flow: Dataflow) -> pl.DataFrame:
    binding = dsd_binding(dsd, flow)
    payload = b"".join(
        write_v2(
            [batch],
            dsd,
            binding.dataflow,
            message_id="m",
            prepared=datetime(2026, 6, 16, tzinfo=UTC),
            sender_id="S",
            lang="en",
        )
    )
    return read_v2(payload)[0]


def _round_trip_v1(batch: pa.RecordBatch, dsd: DataStructure, flow: Dataflow) -> pl.DataFrame:
    binding = dsd_binding(dsd, flow)
    payload = b"".join(
        write_v1(
            [batch],
            dsd,
            binding.dataflow,
            message_id="m",
            prepared=datetime(2026, 6, 16, tzinfo=UTC),
            sender_id="S",
            lang="en",
        )
    )
    return read_v1(payload)[0]


@pytest.mark.parametrize("rt", [_round_trip_v2, _round_trip_v1])
def test_round_trip_basic(rt) -> None:
    """Writer→reader round-trip preserves dim codes, measure values, and row count."""
    dsd, flow = _build_dsd()
    batch = pa.record_batch(
        {
            "FREQ": ["A", "Q", "A"],
            "REF_AREA": ["CA", "US", "US"],
            "OBS_VALUE": [1.0, 2.0, 3.0],
        }
    )
    df = rt(batch, dsd, flow)
    assert df.columns == ["FREQ", "REF_AREA", "OBS_VALUE"]
    assert df.height == 3
    rows = set(
        zip(
            df["FREQ"].to_list(),
            df["REF_AREA"].to_list(),
            df["OBS_VALUE"].to_list(),
            strict=True,
        )
    )
    assert rows == {("A", "CA", 1.0), ("Q", "US", 2.0), ("A", "US", 3.0)}


@pytest.mark.parametrize("rt", [_round_trip_v2, _round_trip_v1])
def test_round_trip_with_attribute(rt) -> None:
    """Enumerated obs-level attributes round-trip back to their code ids
    (not the integer positions used on the wire)."""
    dsd, flow = _build_dsd(with_attribute=True)
    batch = pa.record_batch(
        {
            "FREQ": ["A", "Q"],
            "REF_AREA": ["CA", "US"],
            "OBS_VALUE": [1.0, 2.0],
            "OBS_STATUS": ["A", "P"],
        }
    )
    df = rt(batch, dsd, flow)
    assert df.columns == ["FREQ", "REF_AREA", "OBS_VALUE", "OBS_STATUS"]
    assert set(df["OBS_STATUS"].to_list()) == {"A", "P"}


@pytest.mark.parametrize("rt", [_round_trip_v2, _round_trip_v1])
def test_round_trip_nan_measure(rt) -> None:
    """NaN measures encode as JSON null on the wire and read back as None
    in Polars (which is the SDMX convention for a missing observation)."""
    dsd, flow = _build_dsd()
    batch = pa.record_batch({"FREQ": ["A"], "REF_AREA": ["CA"], "OBS_VALUE": [math.nan]})
    df = rt(batch, dsd, flow)
    assert df["OBS_VALUE"].to_list() == [None]


def test_v2_reader_rejects_v1_envelope() -> None:
    """A 1.0 envelope must not be silently accepted by the 2.0 reader —
    the writer/reader pair would silently produce wrong frames."""
    dsd, flow = _build_dsd()
    binding = dsd_binding(dsd, flow)
    payload = b"".join(
        write_v1(
            [pa.record_batch({"FREQ": ["A"], "REF_AREA": ["CA"], "OBS_VALUE": [1.0]})],
            dsd,
            binding.dataflow,
            message_id="m",
            prepared=datetime(2026, 6, 16, tzinfo=UTC),
            sender_id="S",
            lang="en",
        )
    )
    with pytest.raises(ValueError, match=r"SDMX-JSON 2\.0"):
        read_v2(payload)


def test_v1_reader_rejects_v2_envelope() -> None:
    dsd, flow = _build_dsd()
    binding = dsd_binding(dsd, flow)
    payload = b"".join(
        write_v2(
            [pa.record_batch({"FREQ": ["A"], "REF_AREA": ["CA"], "OBS_VALUE": [1.0]})],
            dsd,
            binding.dataflow,
            message_id="m",
            prepared=datetime(2026, 6, 16, tzinfo=UTC),
            sender_id="S",
            lang="en",
        )
    )
    with pytest.raises(ValueError, match=r"SDMX-JSON 1\.0"):
        read_v1(payload)


def test_v1_reader_handles_upstream_sample() -> None:
    """The upstream SDMX-TWG flat sample must parse — gate against the
    writer's encoding drifting from the real-world wire format. Sample
    has 5 dimensions, multiple obs-level attributes — exercises the
    decoder beyond the synthetic round-trip fixtures."""

    sample = (ROOT / "tests/schemas/sdmx_json/1.0/samples/exr-flat.json").read_bytes()
    df, dataflow = read_v1(sample)
    assert dataflow == "urn:sdmx:org.sdmx.infomodel.datastructure.Dataflow=ECB:EXR(1.0)", "the link a provider writes"
    # The sample is a real ECB exchange-rate snapshot — non-zero rows.
    assert df.height > 0
    # The structures block declares CURRENCY / CURRENCY_DENOM /
    # EXR_TYPE / EXR_SUFFIX / TIME_PERIOD; check that all five made it
    # through as columns.
    assert {"CURRENCY", "CURRENCY_DENOM", "EXR_TYPE", "EXR_SUFFIX", "TIME_PERIOD"} <= set(df.columns)


def test_v2_reader_handles_upstream_sample() -> None:

    sample = (ROOT / "tests/schemas/sdmx_json/2.0/samples/exr-flat.json").read_bytes()
    df, dataflow = read_v2(sample)
    assert dataflow == "urn:sdmx:org.sdmx.infomodel.datastructure.Dataflow=ECB:EXR(1.0)", "the link a provider writes"
    assert df.height > 0
    assert {"CURRENCY", "CURRENCY_DENOM", "EXR_TYPE", "EXR_SUFFIX", "TIME_PERIOD"} <= set(df.columns)


# ── read contract on malformed envelopes (#200) ──────────────────────────────

_V2_OK = (
    b'{"data": {"structures": [{"dimensions": {"observation": '
    b'[{"id":"FREQ","values":[{"id":"A"}]}]}, "attributes": {}}], '
    b'"dataSets": [{"observations": {"0": [1.5]}}]}}'
)
_V1_OK = (
    b'{"structure": {"dimensions": {"observation": '
    b'[{"id":"FREQ","values":[{"id":"A"}]}]}, "attributes": {}}, '
    b'"dataSets": [{"observations": {"0": [1.5]}}]}'
)


@pytest.mark.parametrize(
    ("blob", "match"),
    [
        pytest.param(b'{"structure": null, "dataSets": [{}]}', r"does not look like", id="structure-null"),
        pytest.param(
            b'{"structure": "x", "dataSets": [{}]}', r"Expected `object \| null`", id="structure-not-an-object"
        ),
    ],
)
def test_v1_malformed_structure_raises_value_error(blob: bytes, match: str):
    """The documented contract is ValueError, and a bad ``structure`` must honour it.

    Both of these previously raised ``AttributeError: 'str'/'NoneType' object
    has no attribute 'get'`` — an internal leak that broke the documented
    ``Raises: ValueError``. `msgspec.ValidationError` is a `ValueError`, so
    typed decoding satisfies the contract either way.
    """
    with pytest.raises(ValueError, match=match):
        read_v1(blob)


@pytest.mark.parametrize(
    ("blob", "match"),
    [
        pytest.param(b"{}", r"does not look like", id="empty"),
        pytest.param(b'{"data": null}', r"does not look like", id="data-null"),
        pytest.param(b'{"data": "x"}', r"Expected `object \| null`", id="data-not-an-object"),
        pytest.param(b'{"data": {"structures": [{}]}}', r"does not look like", id="no-datasets"),
        pytest.param(b'{"data": {"dataSets": [{}]}}', r"does not look like", id="no-structures"),
    ],
)
def test_v2_malformed_envelope_raises_value_error(blob: bytes, match: str):
    with pytest.raises(ValueError, match=match):
        read_v2(blob)


def test_value_entry_with_null_id_falls_back_to_name():
    """A ``{"id": null, "name": ...}`` entry must not decode to the string "None".

    The label picker used to test key *presence* (``"id" in entry``) and then
    ``str()`` the value, so an explicit null produced the literal ``"None"`` as
    a dimension value — silent corruption of the decoded frame. It now tests
    for a value, and falls through to ``name``.
    """
    blob = _V2_OK.replace(b'{"id":"A"}', b'{"id":null,"name":"Annual"}')
    assert read_v2(blob)[0]["FREQ"].to_list() == ["Annual"]


def test_value_entry_falls_back_to_literal_value():
    blob = _V2_OK.replace(b'{"id":"A"}', b'{"value":7}')
    assert read_v2(blob)[0]["FREQ"].to_list() == ["7"]


def test_unknown_keys_in_value_entry_are_ignored():
    blob = _V2_OK.replace(b'{"id":"A"}', b'{"id":"A","somethingNew":1}')
    assert read_v2(blob)[0]["FREQ"].to_list() == ["A"]


def test_v1_and_v2_minimal_messages_decode_alike():
    assert read_v1(_V1_OK)[0].to_dicts() == read_v2(_V2_OK)[0].to_dicts()


# ── an attribute slot that is not a position (#348) ──────────────────────────

_V2_BAD_ATTR_SLOT = (
    b'{"data": {"structures": [{"dimensions": {"observation": '
    b'[{"id":"FREQ","values":[{"id":"A"}]}]}, '
    b'"attributes": {"observation": [{"id":"OBS_STATUS","values":[{"id":"A"}]}]}}], '
    b'"dataSets": [{"observations": {"0": [1.5, ["not", "a", "position"]]}}]}}'
)


def test_a_non_positional_attribute_slot_is_a_parse_error_not_a_typeerror() -> None:
    """The slot is an index into the declaration's ``values``. When it is an
    array instead, `int(raw)` used to raise a bare `TypeError` straight out of
    the reader — uncatchable as a parse failure, which is exactly what
    `formats/_coerce` exists to prevent (#247).

    Surfaced by typing the observation arrays `list[object]` instead of
    `list[Any]`: the checker refused `int(raw)` and the narrowing that fixed it
    is what makes the error answerable.
    """
    with pytest.raises(SdmxParseError, match="position into the declaration"):
        read_v2(_V2_BAD_ATTR_SLOT)


def test_the_error_is_still_a_valueerror_for_callers_catching_that() -> None:
    """`SdmxParseError` subclasses `ValueError`, and the readers' documented
    contract is `ValueError` — so tightening the type must not narrow what an
    existing caller catches."""
    with pytest.raises(ValueError, match="position into the declaration"):
        read_v2(_V2_BAD_ATTR_SLOT)


@pytest.mark.parametrize("slot", [b"0", b'"0"', b"0.0"], ids=["int", "numeric-string", "float"])
def test_a_positional_slot_is_accepted_in_every_wire_spelling(slot: bytes) -> None:
    """Providers send positions as JSON numbers; a numeric string is tolerated
    because the pre-narrowing `int(raw)` accepted one and nothing should break
    on the way to a better error."""
    blob = _V2_BAD_ATTR_SLOT.replace(b'["not", "a", "position"]', slot)
    frame = read_v2(blob)[0]
    assert frame["OBS_STATUS"].to_list() == ["A"]


# ── a message of several data sets (GH-264) ──────────────────────────────────


def _structure(dim_id: str, *codes: str) -> dict[str, object]:
    return {"dimensions": {"observation": [{"id": dim_id, "values": [{"id": c} for c in codes]}]}, "attributes": {}}


def _data_set(value: float, **fields: object) -> dict[str, object]:
    return {"observations": {"0": [value]}, **fields}


def _v2(structures: list[dict[str, object]], *data_sets: dict[str, object]) -> bytes:
    return msgspec.json.encode({"data": {"structures": structures, "dataSets": list(data_sets)}})


def _v1(structure: dict[str, object], *data_sets: dict[str, object]) -> bytes:
    return msgspec.json.encode({"structure": structure, "dataSets": list(data_sets)})


def _flow_link(flow_id: str) -> list[dict[str, str]]:
    return [{"rel": "dataflow", "urn": f"urn:sdmx:org.sdmx.infomodel.datastructure.Dataflow=AG:{flow_id}(1.0)"}]


def test_every_data_set_of_a_message_is_read() -> None:
    """Each version used to read `dataSets[0]` and drop the rest unsaid."""
    freq = _structure("FREQ", "A")
    assert read_v2(_v2([freq], _data_set(1.0), _data_set(2.0)))[0]["OBS_VALUE"].to_list() == [1.0, 2.0]
    assert read_v1(_v1(freq, _data_set(1.0), _data_set(2.0)))[0]["OBS_VALUE"].to_list() == [1.0, 2.0]


def test_a_data_set_is_decoded_against_the_structure_it_names() -> None:
    """Position 0 is `A` in the first structure and `Q` in the second, so
    reading `structures[0]` for every data set decodes the wrong code."""
    message = _v2([_structure("FREQ", "A"), _structure("FREQ", "Q")], _data_set(1.0, structure=1))
    assert read_v2(message)[0]["FREQ"].to_list() == ["Q"]


def test_data_sets_naming_different_structures_of_one_shape_read_as_one_frame() -> None:
    message = _v2(
        [_structure("FREQ", "A"), _structure("FREQ", "Q")],
        _data_set(1.0, structure=0),
        _data_set(2.0, structure=1),
    )
    assert read_v2(message)[0].rows() == [("A", 1.0), ("Q", 2.0)]


@pytest.mark.parametrize("action", ["Information", "Append", "Replace"])
def test_a_data_set_that_adds_or_replaces_is_read(action: str) -> None:
    frame = read_v2(_v2([_structure("FREQ", "A")], _data_set(1.0, action=action)))[0]
    assert frame["OBS_VALUE"].to_list() == [1.0]


def test_a_delete_data_set_is_refused() -> None:
    """A Delete data set's rows say what to remove, so reading them as
    observations would report deleted data as data."""
    freq = _structure("FREQ", "A")
    with pytest.raises(SdmxParseError, match=r"dataSets\[1\] is a Delete data set"):
        read_v2(_v2([freq], _data_set(1.0), _data_set(2.0, action="Delete")))
    with pytest.raises(SdmxParseError, match=r"dataSets\[0\] is a Delete data set"):
        read_v1(_v1(freq, _data_set(1.0, action="Delete")))


def test_data_sets_of_different_columns_are_refused() -> None:
    message = _v2(
        [_structure("FREQ", "A"), _structure("REF_AREA", "CA")],
        _data_set(1.0, structure=0),
        _data_set(2.0, structure=1),
    )
    with pytest.raises(
        SdmxParseError, match=r"dataSets\[1\] has columns \['REF_AREA'\] where dataSets\[0\] has \['FREQ'\]"
    ):
        read_v2(message)


def test_data_sets_naming_different_dataflows_are_refused() -> None:
    message = _v2(
        [_structure("FREQ", "A")],
        _data_set(1.0, links=_flow_link("ONE")),
        _data_set(2.0, links=_flow_link("TWO")),
    )
    with pytest.raises(SdmxParseError, match="the data sets name 2 dataflows"):
        read_v2(message)


def test_data_sets_naming_different_data_structures_of_one_shape_are_refused() -> None:
    """Two structure blocks of one shape, each linking its own data structure: different structures."""

    def linked(dsd_id: str) -> dict[str, object]:
        urn = f"urn:sdmx:org.sdmx.infomodel.datastructure.DataStructure=AG:{dsd_id}(1.0)"
        return {**_structure("FREQ", "A"), "links": [{"rel": "datastructure", "urn": urn}]}

    message = _v2([linked("ONE"), linked("TWO")], _data_set(1.0, structure=0), _data_set(2.0, structure=1))
    with pytest.raises(SdmxParseError, match="the data sets name 2 datastructures"):
        read_v2(message)
    same = _v2([linked("ONE"), linked("ONE")], _data_set(1.0, structure=0), _data_set(2.0, structure=1))
    assert read_v2(same)[0]["OBS_VALUE"].to_list() == [1.0, 2.0]


def test_one_dataflow_named_by_some_data_sets_is_the_messages() -> None:
    message = _v2([_structure("FREQ", "A")], _data_set(1.0, links=_flow_link("ONE")), _data_set(2.0))
    assert read_v2(message)[1] == "urn:sdmx:org.sdmx.infomodel.datastructure.Dataflow=AG:ONE(1.0)"


@pytest.mark.parametrize("index", [1, -1])
def test_a_structure_index_outside_the_structures_is_refused(index: int) -> None:
    with pytest.raises(SdmxParseError, match=f"dataSets\\[0\\] names structure {index}, and data.structures holds 1"):
        read_v2(_v2([_structure("FREQ", "A")], _data_set(1.0, structure=index)))
