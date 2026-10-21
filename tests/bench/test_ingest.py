"""End-to-end ingest benchmarks for ``local.Registry.add_all``.

Targets the per-row hot paths called out in issue #139 (`_put_refs`,
`_put_spine`, `_put_localized_text`). Three workload shapes:

- ``ingest_refs`` — one Hierarchy with N code-referencing nodes.
  Exercises ``_put_refs`` (one INSERT per edge in main).
- ``ingest_text`` — M small artefacts (Dataflows) each with a bilingual
  name + description. Exercises ``_put_spine`` + ``_put_localized_text``.
- ``ingest_mixed`` — a realistic blend: a large codelist, a sizable
  hierarchy on top of it, and a moderate dataflow tail.

All workloads run against a fresh in-memory DuckDB. The LocalRegistry is
constructed via ``from_connection`` and ingest is the timed unit; the
fixture build is excluded from the timing region.
"""

from __future__ import annotations

import duckdb
import pytest

from sdmxlib.local.registry import LocalRegistry
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.collections import ItemList
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy
from sdmxlib.model.organisation import Agency
from sdmxlib.model.ref import Ref
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.storage.schema import create_schema

_AGENCY = "BENCH"


def _codelist(n: int) -> Codelist:
    codes = [Code(id=f"C{i:05d}", name={"en": f"Code {i}", "fr": f"Code {i} (fr)"}) for i in range(n)]
    return Codelist(
        id="CL_BENCH",
        maintainer=Agency(id=_AGENCY),
        version="1.0",
        name={"en": "CL_BENCH", "fr": "CL_BENCH (fr)"},
        codes=ItemList(codes),
    )


def _hierarchy(codelist: Codelist, n_nodes: int) -> Hierarchy:
    """Flat hierarchy with *n_nodes* code-referencing nodes.

    Each node carries a Ref into ``codelist`` — one artefact_ref edge per
    node, the loop the issue calls out as the biggest contributor.
    """
    nodes: list[HierarchicalCode] = []
    for i in range(n_nodes):
        code_id = f"C{i:05d}"
        code_urn: SdmxUrn[Code] = SdmxUrn(
            package="codelist",
            artefact_class="Code",
            agency=_AGENCY,
            id=codelist.id,
            version=codelist.version,
            item_id=code_id,
        )
        nodes.append(
            HierarchicalCode(
                id=code_id,
                code=Ref[Code].unresolved(code_urn),
            )
        )
    return Hierarchy(
        id="H_BENCH",
        maintainer=Agency(id=_AGENCY),
        version="1.0",
        name={"en": "H_BENCH", "fr": "H_BENCH (fr)"},
        tree=ItemList(nodes),
    )


def _dataflows(n: int) -> list[Dataflow]:
    # No DSD ref — keeps the workload focused on spine + localized_text.
    return [
        Dataflow(
            id=f"DF_{i:05d}",
            maintainer=Agency(id=_AGENCY),
            version="1.0",
            name={"en": f"Dataflow {i}", "fr": f"Dataflow {i} (fr)"},
            description={"en": f"desc {i}", "fr": f"desc {i} (fr)"},
        )
        for i in range(n)
    ]


def _fresh_registry() -> LocalRegistry:
    conn = duckdb.connect(":memory:")
    create_schema(conn)
    return LocalRegistry.from_connection(conn)


@pytest.mark.parametrize(
    "n_nodes",
    [pytest.param(200, id="200"), pytest.param(2000, id="2k")],
)
def test_ingest_refs(benchmark, n_nodes: int) -> None:
    """Ref-heavy workload: one Hierarchy of N code-referencing nodes."""
    cl = _codelist(n_nodes)
    h = _hierarchy(cl, n_nodes)

    def run() -> None:
        reg = _fresh_registry()
        reg.add_all([cl, h])

    benchmark.pedantic(run, rounds=3, iterations=1)


@pytest.mark.parametrize(
    "n_artefacts",
    [pytest.param(50, id="50"), pytest.param(300, id="300")],
)
def test_ingest_text(benchmark, n_artefacts: int) -> None:
    """Text-heavy workload: many small artefacts with bilingual labels."""
    flows = _dataflows(n_artefacts)

    def run() -> None:
        reg = _fresh_registry()
        reg.add_all(flows)

    benchmark.pedantic(run, rounds=3, iterations=1)


def test_ingest_mixed(benchmark) -> None:
    """Realistic blend — codelist + hierarchy + dataflow tail.

    Scaled to mimic the StatCan profile in miniature: most of the cost
    sits in the hierarchy ref edges and the dataflow text/spine churn,
    not in the codelist bulk insert.
    """
    cl = _codelist(1000)
    h = _hierarchy(cl, 1000)
    flows = _dataflows(200)

    def run() -> None:
        reg = _fresh_registry()
        reg.add_all([cl, h, *flows])

    benchmark.pedantic(run, rounds=3, iterations=1)
