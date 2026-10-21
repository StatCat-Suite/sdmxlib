"""The SDMX-JSON data writers over a DSD read from a `LocalRegistry` (#410).

Such a DSD's components resolve to `LazyCodelist`s. Since #398 the writer had
called `materialize()` on each one per message — every code of every
enumerated component, however few the message observed. With a 200k-code
geography and 12 small codelists that was 1.4 s to write one observation,
against 3 ms for the same DSD with eager codelists.

`test_init_does_not_walk_full_codelist` in the 1.0 writer's tests was meant to
forbid exactly this and stayed green throughout: it only ever built an *eager*
DSD, where `materialize()` returns ``self`` and walks nothing. Its subject
moved and it did not. These tests reach the lazy path the way a user does —
through a registry — and assert the property directly: one lookup per
component, over the observed ids, and no materialisation.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pyarrow as pa
import pytest

import sdmxlib as sl
from sdmxlib.formats.sdmx_json.data_v1 import write_sdmx_json_data as write_v1
from sdmxlib.formats.sdmx_json.data_v2 import write_sdmx_json_data as write_v2
from sdmxlib.model.annotations import Annotation, Annotations
from sdmxlib.model.datastructure import TimeDimension
from sdmxlib.storage import resolve
from sdmxlib.storage.lazy import LazyCodelist
from tests.support.factories import concept_ref

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterator, Sequence

pytestmark = pytest.mark.unit

_WRITERS = [pytest.param(write_v1, id="1.0"), pytest.param(write_v2, id="2.0")]

#: Many more codes than any message observes, so a writer that reads the
#: whole codelist is distinguishable from one that reads what it needs.
_GEO_SIZE = 200

_FREQ = sl.Codelist(
    id="CL_FREQ",
    maintainer="AG",
    version="1.0",
    name={"en": "Frequency"},
    codes=[
        sl.Code(id="TOTAL", name={"en": "All"}),
        sl.Code(
            id="A",
            name={"en": "Annual"},
            parent_id="TOTAL",
            annotations=Annotations([Annotation(type="ORDER", text={"en": "1"})]),
        ),
        # Not a convention code: its period length comes only from the
        # annotation, which the writer now reads off the observed codes.
        sl.Code(
            id="CENSUS",
            name={"en": "Census cycle"},
            annotations=Annotations([Annotation(type="duration", text={"en": "P5Y"})]),
        ),
        sl.Code(id="Q", name={"en": "Quarterly"}),
    ],
)
_GEO = sl.Codelist(
    id="CL_GEO",
    maintainer="AG",
    version="1.0",
    name={"en": "Geography"},
    codes=[sl.Code(id=f"G{i}", name={"en": f"Place {i}"}) for i in range(_GEO_SIZE)],
)
_STATUS = sl.Codelist(
    id="CL_STATUS",
    maintainer="AG",
    version="1.0",
    name={"en": "Status"},
    codes=[sl.Code(id="F", name={"en": "Final"}), sl.Code(id="P", name={"en": "Provisional"})],
)
_DSD = sl.DataStructure(
    id="DSD",
    maintainer="AG",
    version="1.0",
    name={"en": "d"},
    dimensions=[
        sl.Dimension(id="FREQ", concept=concept_ref("FREQ"), representation=sl.Ref[sl.Codelist].to(_FREQ)),
        sl.Dimension(id="GEO", concept=concept_ref("GEO"), representation=sl.Ref[sl.Codelist].to(_GEO)),
    ],
    time_dimension=TimeDimension(id="TIME_PERIOD", concept=concept_ref("TIME_PERIOD")),
    attributes=[
        sl.Attribute(
            attachment=sl.AttachmentLevel.DATASET,
            id="OBS_STATUS",
            concept=concept_ref("OBS_STATUS"),
            representation=sl.Ref[sl.Codelist].to(_STATUS),
        )
    ],
    measures=[sl.Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
)
_FLOW = sl.Dataflow(
    id="DF", maintainer="AG", version="1.0", name={"en": "f"}, structure=sl.Ref[sl.DataStructure].to(_DSD)
)

#: ``XX`` is outside CL_GEO: it must still be emitted, labelled by its id.
_ROWS: dict[str, list[Any]] = {
    "FREQ": ["A", "CENSUS", "A"],
    "GEO": ["G3", "G7", "XX"],
    "TIME_PERIOD": ["2020", "2021", "2020"],
    "OBS_STATUS": ["F", None, "F"],
    "OBS_VALUE": [1.0, 2.0, 3.0],
}

#: What each enumerated component observed — the only ids a lookup may ask for.
_OBSERVED = {"FREQ": {"A", "CENSUS"}, "GEO": {"G3", "G7", "XX"}, "OBS_STATUS": {"F"}}


@pytest.fixture
def lazy_dsd() -> Iterator[sl.DataStructure]:
    with sl.LocalRegistry(":memory:") as reg:
        reg.add_all([_FREQ, _GEO, _STATUS, _DSD, _FLOW])
        dsd = reg.get(_DSD.urn)
        assert isinstance(dsd, sl.DataStructure)
        for comp in (*dsd.dimensions, *dsd.attributes):
            assert isinstance(comp.resolved_codelist, LazyCodelist), "the premise of this whole file"
        yield dsd


def _emit(writer: Callable[..., Iterator[bytes]], dsd: sl.DataStructure) -> dict[str, Any]:
    raw = b"".join(
        writer(
            [pa.RecordBatch.from_pydict(_ROWS)],
            dsd,
            sl.Ref[sl.Dataflow].to(_FLOW),
            message_id="m",
            prepared=datetime(2020, 1, 1, tzinfo=UTC),
            sender_id="s",
            lang="en",
        )
    )
    doc: dict[str, Any] = json.loads(raw)
    return doc


def _structure(doc: dict[str, Any]) -> dict[str, Any]:
    data = doc.get("data", doc)
    struct: dict[str, Any] = data["structures"][0] if "structures" in data else data["structure"]
    return struct


def _values(doc: dict[str, Any], kind: str, component_id: str) -> dict[str, dict[str, Any]]:
    for comp in _structure(doc)[kind]["observation"]:
        if comp["id"] == component_id:
            return {v["id"]: v for v in comp["values"]}
    raise KeyError(component_id)


@pytest.mark.parametrize("writer", _WRITERS)
def test_lazy_and_eager_dsds_write_the_same_message(
    writer: Callable[..., Iterator[bytes]], lazy_dsd: sl.DataStructure
) -> None:
    lazy = _emit(writer, lazy_dsd)
    assert lazy == _emit(writer, _DSD)

    # The comparison above is only worth something if the message carries what
    # the codelists contribute — labels, `parent`, annotations, and a period
    # stretched by a FREQ code's `duration` annotation. Without these, two
    # writers that both ignored the codelists would agree.
    freq = _values(lazy, "dimensions", "FREQ")
    assert freq["A"]["name"] == "Annual"
    assert freq["A"]["parent"] == "TOTAL"
    assert freq["A"]["annotations"] == [0]
    assert _structure(lazy)["annotations"][0]["type"] == "ORDER"
    geo = _values(lazy, "dimensions", "GEO")
    assert geo["G7"]["name"] == "Place 7"
    assert geo["XX"] == {"id": "XX", "name": "XX"}
    assert _values(lazy, "dimensions", "TIME_PERIOD")["2021"]["end"] == "2025-12-31T23:59:59"
    assert _values(lazy, "attributes", "OBS_STATUS")["F"]["name"] == "Final"


@pytest.mark.parametrize("writer", _WRITERS)
def test_no_codelist_is_materialised(
    writer: Callable[..., Iterator[bytes]], lazy_dsd: sl.DataStructure, monkeypatch: pytest.MonkeyPatch
) -> None:
    def tripwire(self: LazyCodelist) -> sl.Codelist:
        msg = f"the writer materialised {self.id}"
        raise AssertionError(msg)

    monkeypatch.setattr(LazyCodelist, "materialize", tripwire)
    _emit(writer, lazy_dsd)


@pytest.mark.parametrize("writer", _WRITERS)
def test_one_lookup_per_component_kind_over_the_observed_ids(
    writer: Callable[..., Iterator[bytes]], lazy_dsd: sl.DataStructure, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The count, not the clock: one batched lookup for the dimensions, one for the attributes (#441).

    The writer keeps one component table per kind, and each resolves its own
    observed codes in one call. Exact on both axes. More calls means the
    per-component lookups crept back, which cost a census DSD 54 statements
    and ~79 ms a message. An id outside `_OBSERVED` means the lookup is
    reading codes the message does not carry.
    """
    asked: list[list[tuple[str, set[str]]]] = []
    original = LazyCodelist.codes_for_each

    def spy(
        cls: type[LazyCodelist], requests: Sequence[tuple[LazyCodelist, Collection[str]]]
    ) -> list[dict[str, sl.Code]]:
        asked.append([(codelist.id, set(ids)) for codelist, ids in requests])
        return original(requests)

    monkeypatch.setattr(LazyCodelist, "codes_for_each", classmethod(spy))
    _emit(writer, lazy_dsd)

    by_codelist = {"CL_FREQ": "FREQ", "CL_GEO": "GEO", "CL_STATUS": "OBS_STATUS"}
    dimensions, attributes = (sorted((by_codelist[cl], ids) for cl, ids in call) for call in asked)
    assert dimensions == [("FREQ", _OBSERVED["FREQ"]), ("GEO", _OBSERVED["GEO"])]
    assert attributes == [("OBS_STATUS", _OBSERVED["OBS_STATUS"])]


@pytest.mark.parametrize("writer", _WRITERS)
def test_a_message_reads_codes_and_annotations_once(
    writer: Callable[..., Iterator[bytes]], lazy_dsd: sl.DataStructure, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One code read and one annotation read per component kind, however many components (#441).

    Two kinds, dimensions and attributes, so two of each per message. Asked per
    component, this DSD's three enumerated components made three of each, and
    a census DSD's 18 made 18.
    """
    reads = {"codes_by_ids": 0, "annotations_for_urns": 0}

    def counting(name: str) -> Callable[..., object]:
        original: Callable[..., object] = getattr(resolve, name)

        def count(*args: object, **kwargs: object) -> object:
            reads[name] += 1
            return original(*args, **kwargs)

        return count

    for name in reads:
        monkeypatch.setattr(resolve, name, counting(name))
    _emit(writer, lazy_dsd)
    assert reads == {"codes_by_ids": 2, "annotations_for_urns": 2}
