"""How many SQL statements one `LocalRegistry.get()` costs, per artefact kind (#426).

In DuckDB a statement has a fixed cost of roughly 0.4 ms however little it
returns, so a structural read is priced in statements. #426 measured a
service's per-request `obs()` at 13-19 ms and found it was 16 point
lookups, two of them repeated on every artefact: the spine row was read once
for its type and again by the reader, and name and description were two
queries. Those are gone; this pins the counts so they do not drift back.

Exact counts, not bounds (CLAUDE.md, "assert the exact quantity when it is
knowable"): a refactor that adds a statement to every `get()` is the
regression, and `<= n` would wave it through. Each count is measured with the
resolver cache warm, so it is the steady state a service sees per request.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import duckdb
import pytest

import sdmxlib as sl
from sdmxlib.model.binding import DataflowBinding
from sdmxlib.model.datastructure import DataStructure, Dimension, Measure
from sdmxlib.model.ref import Ref
from tests.support.factories import dsd_binding

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sdmxlib.model.urn import SdmxUrn

pytestmark = pytest.mark.unit

_CL = sl.Codelist(
    id="CL_X", maintainer="AG", version="1.0", name={"en": "cl"}, codes=[sl.Code(id="A", name={"en": "a"})]
)
_CS = sl.ConceptScheme(
    id="CS",
    maintainer="AG",
    version="1.0",
    name={"en": "cs"},
    concepts=[sl.Concept(id="D", name={"en": "d"}), sl.Concept(id="OBS_VALUE", name={"en": "obs"})],
)
_DSD = DataStructure(
    id="DSD_X",
    maintainer="AG",
    version="1.0",
    name={"en": "dsd"},
    dimensions=[Dimension(id="D", concept=_CS.ref_to("D"), representation=Ref[sl.Codelist].of(_CL.urn, _CL))],
    measures=[Measure(id="OBS_VALUE", concept=_CS.ref_to("OBS_VALUE"))],
)
_FLOW = sl.Dataflow(id="DF_X", maintainer="AG", version="1.0", name={"en": "flow"}, structure=_DSD)
#: Annotated, one annotation with a URL: its URLs cost a statement of their own,
#: and only an artefact with annotations pays it.
_ANNOTATED = sl.Dataflow(
    id="DF_ANN",
    maintainer="AG",
    version="1.0",
    name={"en": "flow"},
    structure=_DSD,
    annotations=sl.Annotations([sl.Annotation(type="NOTE", urls=(sl.AnnotationURL("https://example.org/note"),))]),
)

#: Statements per `get()`, cache warm. Each artefact pays two for its header —
#: the spine row and its name+description — then what its kind needs.
_EXPECTED: dict[str, tuple[SdmxUrn[object], int]] = {
    # header 2 + annotations + the dataflow row
    "dataflow": (_FLOW.urn, 4),
    # and its annotations' URLs, read only because it has annotations
    "annotated dataflow": (_ANNOTATED.urn, 5),
    # header 2 + annotation subtree + components + groups + its SDMX 3.0
    # additions, the MSD and its metadata attribute usages, in one (#422)
    "data structure": (_DSD.urn, 6),
    # spine + its own row: a binding has no name or annotations
    "binding": (DataflowBinding.urn_for(agency="AG", id="DF_X", version="1.0"), 2),
    # a lazy codelist is its header and nothing else until asked
    "lazy codelist": (_CL.urn, 2),
}


@pytest.fixture(scope="module")
def reg() -> Iterator[sl.LocalRegistry]:
    with sl.LocalRegistry(":memory:") as registry:
        registry.add_all([_CL, _CS, _DSD, _FLOW, _ANNOTATED])
        registry.add(dsd_binding(_DSD, _FLOW))
        for urn, _ in _EXPECTED.values():
            _ = registry.get(urn)
        yield registry


def _statements(reg: sl.LocalRegistry, urn: SdmxUrn[object]) -> list[str]:
    seen: list[str] = []
    real = duckdb.DuckDBPyConnection.execute

    def counting(
        self: duckdb.DuckDBPyConnection, query: duckdb.Statement | str, parameters: object = None
    ) -> duckdb.DuckDBPyConnection:
        seen.append(" ".join(str(query).split()))
        return real(self, query, parameters)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(duckdb.DuckDBPyConnection, "execute", counting)
        assert reg.get(urn) is not None
    return seen


#: A read of the spine table, `artefact` itself: not `artefact_text` or the like,
#: and whatever the statement's shape (it opens with `WITH` since its link parts
#: stopped correlating).
_SPINE = re.compile(r"\bFROM artefact\b(?!_)")


@pytest.mark.parametrize("kind", list(_EXPECTED))
def test_a_get_costs_exactly_its_statements(reg: sl.LocalRegistry, kind: str) -> None:
    urn, expected = _EXPECTED[kind]
    seen = _statements(reg, urn)
    assert len(seen) == expected, f"get({kind}) issued {len(seen)} statements, expected {expected}:\n" + "\n".join(seen)


def test_the_spine_is_read_once_per_get(reg: sl.LocalRegistry) -> None:
    """The specific regression #426 removed: the type lookup, then the same row again."""
    for kind, (urn, _) in _EXPECTED.items():
        spine = [s for s in _statements(reg, urn) if _SPINE.search(s)]
        assert len(spine) == 1, f"get({kind}) read the spine {len(spine)} times: {spine}"


def test_an_annotations_urls_come_back() -> None:
    """The discrimination check for the count above: the second statement is what carries the URLs."""
    with sl.LocalRegistry(":memory:") as registry:
        registry.add_all([_CL, _CS, _DSD, _ANNOTATED])
        flow = registry.get(_ANNOTATED.urn)
    assert flow is not None
    assert flow.annotations == _ANNOTATED.annotations
