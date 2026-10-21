"""Stream observations to an SDMX-JSON data message.

Demonstrates Dataset.sink_json, the user-facing entry to the streaming
SDMX-JSON 1.0 / 2.0 data writers. Builds a tiny in-memory DSD +
Dataset, sinks it both ways, and round-trips through the matching
reader to show the encode/decode pair.

Run::

    uv run python examples/sink_sdmx_json.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import msgspec
import polars as pl

from sdmxlib.formats.sdmx_json.data_v1 import read_sdmx_json_data as read_v1
from sdmxlib.formats.sdmx_json.data_v2 import read_sdmx_json_data as read_v2
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.dataset import Dataset
from sdmxlib.model.datastructure import DataStructure, Dimension, Measure
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.ref import Ref


def build_dataset() -> Dataset:
    """Build a tiny in-memory Dataset to sink."""
    cl_freq = Codelist(
        id="CL_FREQ",
        maintainer="DEMO",
        version="1.0",
        name=InternationalString(en="Frequency"),
        codes=[
            Code(id="A", name=InternationalString(en="Annual")),
            Code(id="Q", name=InternationalString(en="Quarterly")),
        ],
    )
    dsd = DataStructure(
        id="DEMO_DSD",
        maintainer="DEMO",
        version="1.0",
        name=InternationalString(en="Demo DSD"),
        dimensions=[
            Dimension(id="FREQ", representation=Ref[Codelist](cl_freq)),
            Dimension(id="REF_AREA"),
        ],
        measures=[Measure(id="OBS_VALUE")],
    )
    flow = Dataflow(
        id="DEMO_FLOW",
        maintainer="DEMO",
        version="1.0",
        name=InternationalString(en="Demo Dataflow"),
        structure=Ref[DataStructure](dsd),
    )
    data = pl.LazyFrame(
        {
            "FREQ": ["A", "Q", "A", "Q"],
            "REF_AREA": ["CA", "US", "FR", "DE"],
            "OBS_VALUE": [1.5, 2.5, 3.5, 4.5],
        }
    )
    return Dataset(structure=dsd, data=data, dataflow=Ref[Dataflow](flow))


def main() -> None:
    ds = build_dataset()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        # ── SDMX-JSON 2.0 (SDMX 3.0 wire format, the default) ────────────
        v2_path = tmp_path / "demo.v2.json"
        ds.sink_json(v2_path)
        v2_payload = v2_path.read_bytes()
        print(f"\n[2.0] wrote {v2_path.name} ({len(v2_payload)} bytes)")
        print("[2.0] envelope keys:", list(msgspec.json.decode(v2_payload)))
        df = read_v2(v2_payload, ds.structure).data.collect()
        print(df)

        # ── SDMX-JSON 1.0 (SDMX 2.1 wire format) ──────────────────────────
        v1_path = tmp_path / "demo.v1.json"
        ds.sink_json(v1_path, version="1.0")
        v1_payload = v1_path.read_bytes()
        print(f"\n[1.0] wrote {v1_path.name} ({len(v1_payload)} bytes)")
        print("[1.0] envelope keys:", list(msgspec.json.decode(v1_payload)))
        df = read_v1(v1_payload, ds.structure).data.collect()
        print(df)


if __name__ == "__main__":
    main()
