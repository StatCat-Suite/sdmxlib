"""A hierarchical codelist keeps its edges through the data writers — #365.

`parent` is a first-class property of a dimension value in the SDMX-JSON data
schema for **1.0, 2.0 and 2.1**, and the writers never emitted it. The resolved
`Code` was already in hand — the name was read off it on the line above — so a
codelist with 878 parent edges arrived at the client as a flat list of 1000.

That is user-visible rather than cosmetic. The .Stat Data Explorer indents
table row headers with

    groupBy(v => v.parent && has(v.parent, indexed) ? v.parent : '#ROOT')

(`rules2/src/hierarchiseDimensionWithNativeHierarchy2.js`), so with no `parent`
every value lands at `#ROOT` and the table renders one flat list even when the
codelist is a real tree.

Both writers are covered because both go through `_data_common`, and a test
naming only one of them would let the other regress silently — which is the
shape of the bug this file exists for: the sibling *structure* writer
(`structure_values.serialize_code`) has always emitted `parent`, so the two
halves of the same format held the same `Code` and disagreed about it.
"""

from __future__ import annotations

import functools
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pyarrow as pa
import pytest

from sdmxlib.formats.sdmx_json.data_v1 import write_sdmx_json_data as write_v1
from sdmxlib.formats.sdmx_json.data_v2 import write_sdmx_json_data as write_v2
from sdmxlib.model.codelist import Code
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.ref import Ref
from tests.support.factories import data_dsd
from tests.support.json_schema import SchemaVersion, assert_valid_sdmx_json, compile_json_schema

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from tests.support.json_schema import Validator

pytestmark = pytest.mark.unit

#: ``TOTAL`` is a root; ``A`` and ``Q`` hang off it. Deliberately *not* the
#: shared `FREQ_CODES_NAMED`, which is flat — a flat fixture would make every
#: assertion here vacuously true.
HIERARCHICAL_FREQ = [
    Code(id="TOTAL", name={"en": "All frequencies"}),
    Code(id="A", name={"en": "Annual"}, parent_id="TOTAL"),
    Code(id="Q", name={"en": "Quarterly"}, parent_id="TOTAL"),
]

#: Every code observed, so `values[]` carries the whole tree. The unobserved
#: case is its own test below.
_ALL_THREE = {
    "FREQ": ["A", "Q", "TOTAL"],
    "REF_AREA": ["CA", "CA", "CA"],
    "TIME_PERIOD": ["2020", "2020-Q1", "2020"],
    "OBS_VALUE": [1.0, 2.0, 3.0],
}

_WRITERS = [pytest.param(write_v1, "1.0", id="1.0"), pytest.param(write_v2, "2.0", id="2.0")]


@functools.cache
def _validator(version: SchemaVersion) -> Validator:
    return compile_json_schema("data", version=version)


def _emit(writer: Callable[..., Iterator[bytes]], rows: dict[str, list[Any]]) -> bytes:
    dsd, flow = data_dsd(freq_codes=HIERARCHICAL_FREQ)
    batch = pa.RecordBatch.from_pydict(rows)
    return b"".join(
        writer(
            [batch],
            dsd,
            Ref[Dataflow].to(flow),
            message_id="m",
            prepared=datetime(2020, 1, 1, tzinfo=UTC),
            sender_id="s",
            lang="en",
        )
    )


def _freq_values(raw: bytes) -> list[dict[str, Any]]:
    """The FREQ dimension's ``values[]``, from either envelope shape.

    1.0 says ``structure`` singular at the top level, 2.0 nests
    ``structures[]`` under ``data`` — the envelope deltas the two writer
    modules exist to cover, and irrelevant to what is asserted here.
    """
    doc = json.loads(raw)
    data = doc.get("data", doc)
    struct = data["structures"][0] if "structures" in data else data["structure"]
    for dim in struct["dimensions"]["observation"]:
        if dim["id"] == "FREQ":
            values: list[dict[str, Any]] = dim["values"]
            return values
    pytest.fail("FREQ is not among the observation dimensions")


@pytest.mark.parametrize(("writer", "version"), _WRITERS)
def test_a_child_code_carries_its_parent(writer: Callable[..., Iterator[bytes]], version: SchemaVersion) -> None:
    """The edge that was dropped. Without it the Explorer renders a flat table."""
    values = {v["id"]: v for v in _freq_values(_emit(writer, _ALL_THREE))}
    assert values["A"]["parent"] == "TOTAL"
    assert values["Q"]["parent"] == "TOTAL"


@pytest.mark.parametrize(("writer", "version"), _WRITERS)
def test_a_root_code_omits_parent_entirely(writer: Callable[..., Iterator[bytes]], version: SchemaVersion) -> None:
    """Absent, not ``null``.

    The schema types `parent` as a string, so an explicit null is invalid — and
    the sibling structure writer omits it the same way.
    """
    values = {v["id"]: v for v in _freq_values(_emit(writer, _ALL_THREE))}
    assert "parent" not in values["TOTAL"]


@pytest.mark.parametrize(("writer", "version"), _WRITERS)
def test_the_emitted_message_is_schema_valid(writer: Callable[..., Iterator[bytes]], version: SchemaVersion) -> None:
    """`parent` is in the published schema, so adding it must not cost validity."""
    assert_valid_sdmx_json(_emit(writer, _ALL_THREE), _validator(version))


@pytest.mark.parametrize(("writer", "version"), _WRITERS)
def test_an_unobserved_parent_still_emits_the_edge(
    writer: Callable[..., Iterator[bytes]], version: SchemaVersion
) -> None:
    """A dangling `parent` is correct, not a defect to filter out.

    The writers emit only *observed* values, so a parent with no observations
    in this slice is absent from ``values[]``. Emitting the edge anyway is what
    the reference .Stat NSI does — its own published messages carry dangling
    parents — and the Explorer handles it by treating the child as a root
    (``has(v.parent, indexed)`` is its guard, so the result is a forest rather
    than a flat list).

    Filtering the edge out here instead would be strictly worse: it discards
    information the consumer might resolve from the codelist, to no benefit.
    """
    rows = {**_ALL_THREE}
    rows = {k: [v for k2, v in zip(_ALL_THREE["FREQ"], vals, strict=True) if k2 != "TOTAL"] for k, vals in rows.items()}
    values = {v["id"]: v for v in _freq_values(_emit(writer, rows))}
    assert "TOTAL" not in values, "fixture no longer exercises the unobserved-parent case"
    assert values["A"]["parent"] == "TOTAL"


@pytest.mark.parametrize(("writer", "version"), _WRITERS)
def test_a_flat_codelist_adds_no_parent_key(writer: Callable[..., Iterator[bytes]], version: SchemaVersion) -> None:
    """The common case must not grow a key. Guards against emitting ``parent: None``."""
    dsd, flow = data_dsd()  # the shared flat FREQ codes
    batch = pa.RecordBatch.from_pydict({"FREQ": ["A"], "REF_AREA": ["CA"], "TIME_PERIOD": ["2020"], "OBS_VALUE": [1.0]})
    raw = b"".join(
        writer(
            [batch],
            dsd,
            Ref[Dataflow].to(flow),
            message_id="m",
            prepared=datetime(2020, 1, 1, tzinfo=UTC),
            sender_id="s",
            lang="en",
        )
    )
    assert b'"parent"' not in raw
