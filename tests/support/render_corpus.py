"""Every wire format sdmxlib writes, written from one pinned input each (GH-132).

`sdmxlib.formats.RENDER_VERSION` holds, per media type, the first 16 hex
characters of the sha256 of that format's golden file here. The writers'
output is compared with the golden files (`tests/unit/test_render_version.py`),
so a writer change fails until the golden file is rewritten, and the new
golden file fails until its render version is: run

    uv run python -m tests.support.render_corpus

which rewrites the golden files and prints the `RENDER_VERSION` to paste.

The inputs:

- structures: the canonical corpus (`canonical_corpus.py`, one artefact per
  class). SDMX-ML 3.x and SDMX-JSON have no measure dimension, so their
  data structure is written without it;
- metadata: the canonical metadata set;
- SDMX-JSON 1.0 structure, which sdmxlib writes only for availability: a
  constraint of the shape `build_dataflow_constraint` makes;
- data: three observations of `_DSD`, an enumerated and a free dimension, a
  time dimension, a measure and an enumerated observation attribute, with a
  missing value and non-ASCII text, and the message id and prepared time
  pinned.
"""

import hashlib
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import attrs
import pyarrow as pa

import sdmxlib as sl
from sdmxlib.formats import serialize
from sdmxlib.formats.data_format import DataFormat
from sdmxlib.formats.sdmx_csv.writer import write_sdmx_csv
from sdmxlib.formats.sdmx_json import data_v1, data_v2
from sdmxlib.formats.sdmx_json_1_0.writer import write_contentconstraint
from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.constraint import ConstraintAttachment, CubeRegion, KeyValue, TimeBound, TimeRange
from sdmxlib.model.datastructure import AttachmentLevel, Attribute, DataStructure, Dimension, Measure, TimeDimension
from sdmxlib.model.message import MetadataMessage, StructureMessage
from sdmxlib.model.metadataset import MetadataSet
from sdmxlib.model.ref import Ref
from tests.support.canonical_corpus import corpus
from tests.support.factories import concept_ref
from tests.support.repo import ROOT

#: Where the golden files are.
GOLDEN = ROOT / "tests" / "golden" / "render"

_PREPARED = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

_FREQ = sl.Codelist(
    id="CL_FREQ",
    maintainer="AG",
    version="1.0",
    name={"en": "Frequency", "fr": "Fréquence"},
    codes=[sl.Code(id="A", name={"en": "Annual", "fr": "Annuel"}), sl.Code(id="M", name={"en": "Monthly"})],
)
_STATUS = sl.Codelist(
    id="CL_STATUS",
    maintainer="AG",
    version="1.0",
    name={"en": "Status"},
    codes=[sl.Code(id="A", name={"en": "Normal"}), sl.Code(id="E", name={"en": "Estimated"})],
)
_DSD = DataStructure(
    id="DSD_RENDER",
    maintainer="AG",
    version="1.0",
    name={"en": "Render"},
    dimensions=[
        Dimension(id="FREQ", concept=concept_ref("FREQ"), representation=Ref.to(_FREQ)),
        Dimension(id="GEO", concept=concept_ref("GEO")),
    ],
    time_dimension=TimeDimension(concept=concept_ref("TIME_PERIOD")),
    measures=[Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    attributes=[
        Attribute(
            id="OBS_STATUS",
            concept=concept_ref("OBS_STATUS"),
            representation=Ref.to(_STATUS),
            attachment=AttachmentLevel.OBSERVATION,
        )
    ],
)
_FLOW = sl.Dataflow(id="DF_RENDER", maintainer="AG", version="1.0", name={"en": "Render"}, structure=Ref.to(_DSD))
_ROWS = pa.RecordBatch.from_pydict(
    {
        "FREQ": ["A", "A", "M"],
        "GEO": ["CA", "Québec", "CA"],
        "TIME_PERIOD": ["2020", "2021", "2021-03"],
        "OBS_VALUE": [1.5, None, -2.25],
        "OBS_STATUS": ["A", "E", None],
    }
)
_AVAILABILITY = sl.DataConstraint(
    id="AVAIL",
    maintainer="AG",
    version="1.0",
    name={"en": "Availability"},
    attachment=ConstraintAttachment(dataflows=(Ref.to(_FLOW),)),
    regions=[
        CubeRegion(
            keys=(
                KeyValue(dimension_id="FREQ", values=frozenset({"A", "M"})),
                KeyValue(dimension_id="GEO", values=frozenset({"CA", "Québec"})),
                KeyValue(
                    dimension_id="TIME_PERIOD",
                    time_range=TimeRange(
                        start=TimeBound("2020-01-01T00:00:00Z"), end=TimeBound("2021-03-31T00:00:00Z")
                    ),
                ),
            )
        )
    ],
)


def _structures(*, measure_dimension: bool) -> StructureMessage:
    slots = {f.name for f in attrs.fields(StructureMessage)}
    by_slot: dict[str, list[MaintainableArtefact]] = {}
    for artefact in corpus().values():
        if artefact.msg_field not in slots:
            continue
        written = artefact
        if isinstance(artefact, DataStructure) and not measure_dimension:
            written = attrs.evolve(artefact, measure_dimension=None)
        by_slot.setdefault(artefact.msg_field, []).append(written)
    # One `Any` local, as `fake_rest` and `StructureMessage.containing` do:
    # each slot's element type is read off the message at runtime, and a
    # list of mixed artefacts satisfies no single slot's type.
    slotted: dict[str, Any] = by_slot
    return StructureMessage(**slotted)


def _metadata() -> MetadataMessage:
    (metadata_set,) = (a for a in corpus().values() if isinstance(a, MetadataSet))
    return MetadataMessage(metadata_sets=[metadata_set])


def _json_data(version: str) -> bytes:
    write = data_v2.write_sdmx_json_data if version == "2.0" else data_v1.write_sdmx_json_data
    return b"".join(
        write([_ROWS], _DSD, Ref.to(_FLOW), message_id="RENDER", prepared=_PREPARED, sender_id="SDMXLIB", lang="en")
    )


#: Each format sdmxlib writes, by media type, with the file name of its
#: golden file and how to write it.
WRITERS: dict[str, tuple[str, Callable[[], bytes]]] = {
    "application/vnd.sdmx.structure+xml;version=2.1": (
        "structure-2.1.xml",
        lambda: serialize(_structures(measure_dimension=True), version="2.1", pretty_print=True),
    ),
    "application/vnd.sdmx.structure+xml;version=3.0.0": (
        "structure-3.0.xml",
        lambda: serialize(_structures(measure_dimension=False), version="3.0", pretty_print=True),
    ),
    "application/vnd.sdmx.structure+xml;version=3.1.0": (
        "structure-3.1.xml",
        lambda: serialize(_structures(measure_dimension=False), version="3.1", pretty_print=True),
    ),
    "application/vnd.sdmx.structure+json;version=2.0.0": (
        "structure-2.0.json",
        lambda: serialize(_structures(measure_dimension=False), version="json", pretty_print=True),
    ),
    "application/vnd.sdmx.structure+json;version=1.0.0": (
        "availability-1.0.json",
        lambda: write_contentconstraint(_AVAILABILITY, pretty_print=True),
    ),
    "application/vnd.sdmx.metadata+xml;version=3.0.0": (
        "metadata-3.0.xml",
        lambda: serialize(_metadata(), version="3.0", pretty_print=True),
    ),
    "application/vnd.sdmx.metadata+json;version=2.0.0": (
        "metadata-2.0.json",
        lambda: serialize(_metadata(), version="json", pretty_print=True),
    ),
    DataFormat.SDMX_CSV_2_0_0.value: (
        "data-2.0.0.csv",
        lambda: b"".join(write_sdmx_csv([_ROWS], _DSD, structure_id=_FLOW)),
    ),
    DataFormat.SDMX_JSON_1_0_0.value: ("data-1.0.json", lambda: _json_data("1.0")),
    DataFormat.SDMX_JSON_2_0_0.value: ("data-2.0.json", lambda: _json_data("2.0")),
}


def data_flow() -> list[MaintainableArtefact]:
    """What the golden data files are observations of: `DF_RENDER`, its structure and codelists."""
    return [_FREQ, _STATUS, _DSD, _FLOW]


def written(media_type: str) -> bytes:
    """What sdmxlib writes now in `media_type`, from that format's pinned input."""
    return WRITERS[media_type][1]()


def golden(media_type: str) -> Path:
    """The golden file of `media_type`."""
    return GOLDEN / WRITERS[media_type][0]


def digest(media_type: str) -> str:
    """The render version the golden file of `media_type` gives: its sha256's first 16 hex characters."""
    return hashlib.sha256(golden(media_type).read_bytes()).hexdigest()[:16]


def main() -> None:
    """Rewrite every golden file and print the `RENDER_VERSION` they give."""
    GOLDEN.mkdir(parents=True, exist_ok=True)
    for media_type in WRITERS:
        golden(media_type).write_bytes(written(media_type))
    lines = [f'    "{media_type}": "{digest(media_type)}",' for media_type in WRITERS]
    sys.stdout.write("\n".join(["RENDER_VERSION = {", *lines, "}", ""]))


if __name__ == "__main__":
    main()
