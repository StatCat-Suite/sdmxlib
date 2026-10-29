"""Tests for the streaming SDMX-CSV writer."""

from __future__ import annotations

import csv
import io
from typing import cast

import pyarrow as pa
import pytest

from sdmxlib.formats.sdmx_csv import write_sdmx_csv
from sdmxlib.formats.sdmx_csv.writer import CsvAction
from sdmxlib.local import LocalRegistry
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.collections import ItemList
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import AttachmentLevel, Attribute, DataStructure, Dimension, Measure
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.ref import Ref
from sdmxlib.model.representation import Facet
from tests.support.factories import concept_ref

pytestmark = pytest.mark.unit


def _make_dsd(*, with_attrs: bool = False) -> DataStructure:
    freq_cl = Codelist(
        id="CL_FREQ",
        maintainer="TEST",
        name=InternationalString(en="Frequency"),
        codes=ItemList(
            [
                Code(id="A", name=InternationalString(en="Annual")),
                Code(id="Q", name=InternationalString(en="Quarterly")),
                Code(id="M", name=InternationalString(en="Monthly")),
            ]
        ),
    )
    return DataStructure(
        id="DSD_TEST",
        maintainer="TEST",
        name=InternationalString(en="Test DSD"),
        dimensions=ItemList(
            [
                Dimension(
                    id="FREQ",
                    representation=Ref[Codelist].of(
                        Codelist.urn_for(agency="TEST", id="CL_FREQ"),
                        freq_cl,
                    ),
                    concept=concept_ref("FREQ"),
                ),
                Dimension(id="REF_AREA", representation=Facet(type="String"), concept=concept_ref("REF_AREA")),
            ]
        ),
        measures=ItemList([Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))]),
        attributes=ItemList(
            [Attribute(attachment=AttachmentLevel.DATASET, id="GEO_NAME", concept=concept_ref("GEO_NAME"))]
            if with_attrs
            else []
        ),
    )


def _make_batch(rows: list[dict[str, object]]) -> pa.RecordBatch:
    """Build a single record batch from a list of row-dicts."""
    if not rows:
        return pa.RecordBatch.from_pylist([])
    return pa.RecordBatch.from_pylist(rows)


def _read_csv_bytes(chunks: list[bytes]) -> tuple[list[str], list[list[str]]]:
    text = b"".join(chunks).decode("utf-8")
    reader = csv.reader(io.StringIO(text))
    header = next(reader)
    rows = list(reader)
    return header, rows


def test_writer_emits_header_then_rows():
    dsd = _make_dsd()
    batch = _make_batch(
        [
            {"FREQ": "A", "REF_AREA": "CA", "OBS_VALUE": 100.0},
            {"FREQ": "A", "REF_AREA": "US", "OBS_VALUE": 200.0},
        ]
    )
    chunks = list(
        write_sdmx_csv([batch], dsd, structure_id="TEST:DF_TEST(1.0)"),
    )
    header, rows = _read_csv_bytes(chunks)
    assert header == ["STRUCTURE", "STRUCTURE_ID", "ACTION", "FREQ", "REF_AREA", "OBS_VALUE"]
    assert rows == [
        ["dataflow", "TEST:DF_TEST(1.0)", "I", "A", "CA", "100.0"],
        ["dataflow", "TEST:DF_TEST(1.0)", "I", "A", "US", "200.0"],
    ]


def test_writer_header_only_on_first_chunk():
    dsd = _make_dsd()
    b1 = _make_batch([{"FREQ": "A", "REF_AREA": "CA", "OBS_VALUE": 1.0}])
    b2 = _make_batch([{"FREQ": "A", "REF_AREA": "US", "OBS_VALUE": 2.0}])
    chunks = list(
        write_sdmx_csv([b1, b2], dsd, structure_id="TEST:DF_TEST(1.0)"),
    )
    assert len(chunks) == 2
    # First chunk has header line; second does not.
    assert chunks[0].startswith(b"STRUCTURE,")
    assert not chunks[1].startswith(b"STRUCTURE,")


def test_writer_empty_stream_emits_header_only():
    dsd = _make_dsd()
    chunks = list(write_sdmx_csv([], dsd, structure_id="TEST:DF_TEST(1.0)"))
    header, rows = _read_csv_bytes(chunks)
    assert header == ["STRUCTURE", "STRUCTURE_ID", "ACTION", "FREQ", "REF_AREA", "OBS_VALUE"]
    assert rows == []


def test_writer_includes_attribute_columns():
    dsd = _make_dsd(with_attrs=True)
    batch = _make_batch(
        [
            {"FREQ": "A", "REF_AREA": "CA", "OBS_VALUE": 1.0, "GEO_NAME": "Canada"},
        ]
    )
    chunks = list(
        write_sdmx_csv([batch], dsd, structure_id="TEST:DF_TEST(1.0)"),
    )
    header, rows = _read_csv_bytes(chunks)
    assert header[-1] == "GEO_NAME"
    assert rows[0][-1] == "Canada"


def test_writer_action_and_structure_overrides():
    dsd = _make_dsd()
    batch = _make_batch([{"FREQ": "A", "REF_AREA": "CA", "OBS_VALUE": 1.0}])
    chunks = list(
        write_sdmx_csv(
            [batch],
            dsd,
            structure_id="TEST:DSD_TEST(1.0)",
            structure="datastructure",
            action="R",
        ),
    )
    _, rows = _read_csv_bytes(chunks)
    assert rows[0][:3] == ["datastructure", "TEST:DSD_TEST(1.0)", "R"]


@pytest.mark.parametrize("action", ["I", "A", "R", "D"])
def test_each_of_sdmx_csv_2_0s_actions_is_written(action: CsvAction) -> None:
    batch = _make_batch([{"FREQ": "A", "REF_AREA": "CA", "OBS_VALUE": 1.0}])
    _, rows = _read_csv_bytes(list(write_sdmx_csv([batch], _make_dsd(), structure_id="TEST:DF(1.0)", action=action)))
    assert rows[0][2] == action


@pytest.mark.parametrize("action", ["M", "X", "Information", ""])
def test_an_action_sdmx_csv_2_0_lacks_is_refused(action: str) -> None:
    """GH-284: any string was written, so `"M"` (2.1's Merge) or a typo made a
    2.0 file no conformant reader accepts. The `cast` stands in for an
    untyped caller."""
    batch = _make_batch([{"FREQ": "A", "REF_AREA": "CA", "OBS_VALUE": 1.0}])
    with pytest.raises(ValueError, match=r"ACTION must be one of I, A, R, D"):
        list(write_sdmx_csv([batch], _make_dsd(), structure_id="TEST:DF(1.0)", action=cast("CsvAction", action)))


def test_writer_renders_none_as_empty_string():
    dsd = _make_dsd(with_attrs=True)
    batch = _make_batch(
        [
            {"FREQ": "A", "REF_AREA": "CA", "OBS_VALUE": 1.0, "GEO_NAME": None},
        ]
    )
    chunks = list(
        write_sdmx_csv([batch], dsd, structure_id="TEST:DF_TEST(1.0)"),
    )
    _, rows = _read_csv_bytes(chunks)
    assert rows[0][-1] == ""


def test_writer_raises_on_missing_column():
    dsd = _make_dsd()
    batch = _make_batch([{"FREQ": "A", "REF_AREA": "CA"}])  # no OBS_VALUE
    with pytest.raises(KeyError, match="OBS_VALUE"):
        list(write_sdmx_csv([batch], dsd, structure_id="TEST:DF_TEST(1.0)"))


# ── Round-trip via existing reader ──────────────────────────────────────


def test_writer_round_trips_through_reader():
    dsd = _make_dsd()
    batch = _make_batch(
        [
            {"FREQ": "A", "REF_AREA": "CA", "OBS_VALUE": 100.0},
            {"FREQ": "Q", "REF_AREA": "US", "OBS_VALUE": 200.0},
        ]
    )
    csv_bytes = b"".join(
        write_sdmx_csv([batch], dsd, structure_id="TEST:DF_TEST(1.0)"),
    )
    freq = dsd.dimensions["FREQ"].representation
    assert isinstance(freq, Ref)
    flow = Dataflow(id="DF_TEST", maintainer="TEST", name=InternationalString(en="f"), structure=Ref.to(dsd))
    with LocalRegistry(":memory:") as registry:
        registry.add_all([freq(), dsd, flow])
        obs = registry.scan_csv(csv_bytes)
        df = obs.frame()
        # Envelope columns are not components, so not columns.
        assert set(df.columns) == {"FREQ", "REF_AREA", "OBS_VALUE"}
        assert df.height == 2
        assert df["REF_AREA"].to_list() == ["CA", "US"]
        # The registry resolved the dataflow the STRUCTURE_ID column names.
        assert obs.structure is not None
        assert obs.structure.id == "DSD_TEST"
