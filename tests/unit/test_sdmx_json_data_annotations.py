"""Annotations reach the data message, pooled and referenced by index — #366.

The SDMX-JSON data writers emitted no annotations at all: not on a value, not
on a component. That is a whole channel missing rather than one field, and it
is how the wire format carries everything a consumer needs beyond id and name —
`.Stat`'s `ORDER`, `NOT_DISPLAYED`, `LAYOUT_ROW` and the rest all travel this
way. Without it a consumer's value ordering falls back to first-seen order.

The format does not repeat an annotation object on every value that carries it:
the structure block holds one array of objects and everything else references
it by integer index. So the interesting properties here are not "is the field
present" but **referential**: one pool per structure block, shared by the
dimension and attribute tables, with every emitted index resolving inside it.
An index is meaningless without knowing which array it indexes, and there is
only one array.

No filtering by annotation type, deliberately. Every SDMX-ML version documents
`AnnotationType` as *"not enumerated, as these can be specified by the user or
creator of the annotations"* — there is no taxonomy, so an allow-list would
hard-code one publisher's private vocabulary into a general library and be
wrong for the next.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import attrs
import pyarrow as pa
import pytest

from sdmxlib.formats.sdmx_json.data_v1 import write_sdmx_json_data as write_v1
from sdmxlib.formats.sdmx_json.data_v2 import write_sdmx_json_data as write_v2
from sdmxlib.model.annotations import Annotation, Annotations
from sdmxlib.model.codelist import Code
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.ref import Ref
from tests.support.factories import data_dsd
from tests.support.json_schema import SchemaVersion, assert_valid_sdmx_json, compile_json_schema

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from sdmxlib.model.datastructure import DataStructure

pytestmark = pytest.mark.unit

_WRITERS = [pytest.param(write_v1, "1.0", id="1.0"), pytest.param(write_v2, "2.0", id="2.0")]

#: ``NOT_DISPLAYED`` is deliberately carried by *two* codes so interning has
#: something to collapse — without a repeat, a pool that never dedupes would
#: pass every assertion here.
_SHARED = Annotation(type="NOT_DISPLAYED")

FREQ_CODES = [
    Code(id="TOTAL", name={"en": "All"}, annotations=Annotations([Annotation(type="ORDER", text={"en": "1"})])),
    Code(
        id="A",
        name={"en": "Annual"},
        parent_id="TOTAL",
        annotations=Annotations([Annotation(type="ORDER", text={"en": "2"}), _SHARED]),
    ),
    Code(id="Q", name={"en": "Quarterly"}, parent_id="TOTAL", annotations=Annotations([_SHARED])),
]

_ROWS = {
    "FREQ": ["A", "Q", "TOTAL"],
    "REF_AREA": ["CA", "CA", "CA"],
    "TIME_PERIOD": ["2020", "2020-Q1", "2020"],
    "OBS_VALUE": [1.0, 2.0, 3.0],
}


def _emit(writer: Callable[..., Iterator[bytes]], dsd: DataStructure, flow: Dataflow, rows: dict[str, Any]) -> bytes:
    return b"".join(
        writer(
            [pa.RecordBatch.from_pydict(rows)],
            dsd,
            Ref[Dataflow].to(flow),
            message_id="m",
            prepared=datetime(2020, 1, 1, tzinfo=UTC),
            sender_id="s",
            lang="en",
        )
    )


def _structure(raw: bytes) -> dict[str, Any]:
    """The structure block, from either envelope shape (1.0 vs 2.0)."""
    doc = json.loads(raw)
    data = doc.get("data", doc)
    struct: dict[str, Any] = data["structures"][0] if "structures" in data else data["structure"]
    return struct


@pytest.mark.parametrize(("writer", "version"), _WRITERS)
def test_a_value_references_its_annotations_by_index(
    writer: Callable[..., Iterator[bytes]], version: SchemaVersion
) -> None:
    """The channel that was missing entirely."""
    dsd, flow = data_dsd(freq_codes=FREQ_CODES)
    struct = _structure(_emit(writer, dsd, flow, _ROWS))
    pool = struct["annotations"]
    values = {v["id"]: v for d in struct["dimensions"]["observation"] if d["id"] == "FREQ" for v in d["values"]}
    resolved = {vid: [pool[i]["type"] for i in v["annotations"]] for vid, v in values.items()}
    assert resolved == {"TOTAL": ["ORDER"], "A": ["ORDER", "NOT_DISPLAYED"], "Q": ["NOT_DISPLAYED"]}


@pytest.mark.parametrize(("writer", "version"), _WRITERS)
def test_every_emitted_index_resolves_in_the_pool(
    writer: Callable[..., Iterator[bytes]], version: SchemaVersion
) -> None:
    """The referential invariant, over *every* index the message emits.

    A dangling index is the failure mode this design invites — two tables
    numbering into one array — and it is invisible to a test that only checks
    one component.
    """
    dsd, flow = data_dsd(
        freq_codes=FREQ_CODES, status_codes=[Code(name={"en": "A"}, id="A", annotations=Annotations([_SHARED]))]
    )
    rows = {**_ROWS, "OBS_STATUS": ["A", "A", "A"]}
    struct = _structure(_emit(writer, dsd, flow, rows))
    size = len(struct["annotations"])
    emitted: list[int] = []
    for level in ("dimensions", "attributes"):
        for comp in struct[level]["observation"]:
            emitted.extend(comp.get("annotations", []))
            for value in comp.get("values", []):
                emitted.extend(value.get("annotations", []))
    assert emitted, "fixture emitted no indices, so this proves nothing"
    assert all(0 <= i < size for i in emitted), f"index outside pool of {size}: {emitted}"


@pytest.mark.parametrize(("writer", "version"), _WRITERS)
def test_dimensions_and_attributes_share_one_pool(
    writer: Callable[..., Iterator[bytes]], version: SchemaVersion
) -> None:
    """An attribute value's annotation lands in the same array a dimension's does.

    The two `ComponentTable`s are built separately; if each kept its own pool
    the indices would still *look* valid and would silently point at the wrong
    annotation. Here the attribute code shares `NOT_DISPLAYED` with two FREQ
    codes, so a correct writer gives all three the same index.
    """
    dsd, flow = data_dsd(
        freq_codes=FREQ_CODES, status_codes=[Code(name={"en": "A"}, id="A", annotations=Annotations([_SHARED]))]
    )
    rows = {**_ROWS, "OBS_STATUS": ["A", "A", "A"]}
    struct = _structure(_emit(writer, dsd, flow, rows))
    pool = struct["annotations"]
    freq_q = next(
        v for d in struct["dimensions"]["observation"] if d["id"] == "FREQ" for v in d["values"] if v["id"] == "Q"
    )
    status = next(v for a in struct["attributes"]["observation"] for v in a.get("values", []) if v.get("id") == "A")
    assert freq_q["annotations"] == status["annotations"]
    assert pool[freq_q["annotations"][0]]["type"] == "NOT_DISPLAYED"
    assert sum(1 for a in pool if a.get("type") == "NOT_DISPLAYED") == 1, "the shared annotation was pooled twice"


@pytest.mark.parametrize(("writer", "version"), _WRITERS)
def test_a_component_carries_its_own_annotations(
    writer: Callable[..., Iterator[bytes]], version: SchemaVersion
) -> None:
    """`dimensions[].annotations` is a distinct schema slot from the values'."""
    dsd, flow = data_dsd()
    layout = Annotations([Annotation(type="LAYOUT_ROW", text={"en": "1"})])
    dsd = attrs.evolve(
        dsd, dimensions=[attrs.evolve(d, annotations=layout) if d.id == "FREQ" else d for d in dsd.dimensions]
    )
    rows = {"FREQ": ["A"], "REF_AREA": ["CA"], "TIME_PERIOD": ["2020"], "OBS_VALUE": [1.0]}
    struct = _structure(_emit(writer, dsd, flow, rows))
    freq = next(d for d in struct["dimensions"]["observation"] if d["id"] == "FREQ")
    ref_area = next(d for d in struct["dimensions"]["observation"] if d["id"] == "REF_AREA")
    assert struct["annotations"][freq["annotations"][0]]["type"] == "LAYOUT_ROW"
    assert "annotations" not in ref_area, "a component with none must not grow the key"


@pytest.mark.parametrize(("writer", "version"), _WRITERS)
def test_an_unannotated_message_grows_no_keys(writer: Callable[..., Iterator[bytes]], version: SchemaVersion) -> None:
    """The common case pays nothing — no empty pool, no empty index arrays."""
    dsd, flow = data_dsd()
    rows = {"FREQ": ["A"], "REF_AREA": ["CA"], "TIME_PERIOD": ["2020"], "OBS_VALUE": [1.0]}
    assert b'"annotations"' not in _emit(writer, dsd, flow, rows)


@pytest.mark.parametrize(("writer", "version"), _WRITERS)
def test_the_annotated_message_is_schema_valid(writer: Callable[..., Iterator[bytes]], version: SchemaVersion) -> None:
    """Objects in the pool, integers everywhere else — the schema is strict about which."""
    dsd, flow = data_dsd(
        freq_codes=FREQ_CODES, status_codes=[Code(name={"en": "A"}, id="A", annotations=Annotations([_SHARED]))]
    )
    rows = {**_ROWS, "OBS_STATUS": ["A", "A", "A"]}
    assert_valid_sdmx_json(_emit(writer, dsd, flow, rows), compile_json_schema("data", version=version))


def test_the_writer_filters_no_annotation_type() -> None:
    """No allow-list: `AnnotationType` has no taxonomy in the SDMX IM.

    Every SDMX-ML version documents it as "not enumerated, as these can be
    specified by the user or creator of the annotations", so `ORDER` and
    friends are a `.Stat` convention rather than SDMX vocabulary. A type this
    library has never heard of has to survive, or the next publisher's
    annotations are silently dropped.
    """
    invented = Annotation(type="A_TYPE_NO_ONE_HAS_DEFINED", text={"en": "x"})
    codes = [Code(id="A", name={"en": "Annual"}, annotations=Annotations([invented]))]
    dsd, flow = data_dsd(freq_codes=codes)
    rows = {"FREQ": ["A"], "REF_AREA": ["CA"], "TIME_PERIOD": ["2020"], "OBS_VALUE": [1.0]}
    struct = _structure(_emit(write_v2, dsd, flow, rows))
    assert [a["type"] for a in struct["annotations"]] == ["A_TYPE_NO_ONE_HAS_DEFINED"]


def _dataset(raw: bytes) -> dict[str, Any]:
    doc = json.loads(raw)
    data = doc.get("data", doc)
    dataset: dict[str, Any] = data["dataSets"][0]
    return dataset


@pytest.mark.parametrize(("writer", "version"), _WRITERS)
def test_the_dataflow_annotations_are_referenced_by_the_dataset(
    writer: Callable[..., Iterator[bytes]], version: SchemaVersion
) -> None:
    """The message wraps a dataflow, so the dataflow's annotations travel with it.

    .Stat's Data Explorer reads `NOT_DISPLAYED` — the attribute values a table
    should hide — from this pool, and the NSI web service fills it from the
    dataflow with the dataSet pointing at them.
    """
    dsd, flow = data_dsd()
    flow = attrs.evolve(
        flow,
        annotations=Annotations(
            [Annotation(type="NOT_DISPLAYED", title="OBS_STATUS=A"), Annotation(type="DEFAULT", title="FREQ=A")]
        ),
    )
    rows = {"FREQ": ["A"], "REF_AREA": ["CA"], "TIME_PERIOD": ["2020"], "OBS_VALUE": [1.0]}
    raw = _emit(writer, dsd, flow, rows)
    pool = _structure(raw)["annotations"]
    referenced = [(pool[i]["type"], pool[i]["title"]) for i in _dataset(raw)["annotations"]]
    assert referenced == [("NOT_DISPLAYED", "OBS_STATUS=A"), ("DEFAULT", "FREQ=A")]
    assert_valid_sdmx_json(raw, compile_json_schema("data", version=version))


@pytest.mark.parametrize(("writer", "version"), _WRITERS)
def test_a_dataflow_annotation_shared_with_a_value_is_pooled_once(
    writer: Callable[..., Iterator[bytes]], version: SchemaVersion
) -> None:
    """The dataflow's annotations are registered first, then the values intern against them.

    Registering first is what lets the dataSet head — written before any
    observation — carry final indices; a value carrying the same annotation must
    reuse that index, not push a second copy that shifts nothing but wastes bytes.
    """
    dsd, flow = data_dsd(freq_codes=FREQ_CODES)
    flow = attrs.evolve(flow, annotations=Annotations([_SHARED]))
    raw = _emit(writer, dsd, flow, _ROWS)
    struct = _structure(raw)
    freq_q = next(
        v for d in struct["dimensions"]["observation"] if d["id"] == "FREQ" for v in d["values"] if v["id"] == "Q"
    )
    assert _dataset(raw)["annotations"] == [0] == freq_q["annotations"]
    assert sum(1 for a in struct["annotations"] if a.get("type") == "NOT_DISPLAYED") == 1


@pytest.mark.parametrize(("writer", "version"), _WRITERS)
def test_an_unresolved_dataflow_contributes_no_annotations(
    writer: Callable[..., Iterator[bytes]], version: SchemaVersion
) -> None:
    """Only a resolved ref has annotations to give; the dataSet grows no key."""
    dsd, flow = data_dsd()
    flow = attrs.evolve(flow, annotations=Annotations([Annotation(type="NOT_DISPLAYED", title="OBS_STATUS=A")]))
    rows = {"FREQ": ["A"], "REF_AREA": ["CA"], "TIME_PERIOD": ["2020"], "OBS_VALUE": [1.0]}
    raw = b"".join(
        writer(
            [pa.RecordBatch.from_pydict(rows)],
            dsd,
            Ref[Dataflow].unresolved(flow.urn),
            message_id="m",
            prepared=datetime(2020, 1, 1, tzinfo=UTC),
            sender_id="s",
            lang="en",
        )
    )
    assert b'"annotations"' not in raw
