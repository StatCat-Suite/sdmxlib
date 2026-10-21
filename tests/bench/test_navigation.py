"""Phase 3: the elegance tax.

Quantifies the cost of the navigational API
(``dsd.dimensions["FREQ"].codelist.codes``) against the equivalent
hand-built dict access. The point is not to push users away from the
elegant style — it's to know what we're paying for it so the design
keeps the tax cheap.

All benchmarks use a fully-resolved synthetic DSD held in an
``InMemoryRegistry`` so every Ref is interned and ``.codelist`` /
``.codes`` walks resolve at C speed (no I/O).
"""

import pytest

from sdmxlib.model.in_memory_registry import InMemoryRegistry
from tests.bench.fixtures import make_message

_SHAPES = [
    pytest.param((5, 50), id="5x50"),
    pytest.param((10, 200), id="10x200"),
]


@pytest.fixture(scope="session", params=_SHAPES)
def resolved_dsd(request: pytest.FixtureRequest):
    """A DSD whose dimension Refs are fully resolved, plus hand-built
    parallel indexes for the direct-access baseline."""
    n_dims, codes_per_dim = request.param
    msg = make_message(n_dataflows=1, n_dims=n_dims, codes_per_dim=codes_per_dim)
    reg = InMemoryRegistry()
    reg.add_all(msg.all_artefacts())
    # The registry's copy: `add` binds a copy and leaves the caller's as it was
    # built (#492), so the message's own DSD resolves nothing.
    dsd = reg.get(msg.data_structures[0].urn)
    assert dsd is not None
    # Hand-built indexes — what a perf-optimised user would build once.
    dim_by_id = {d.id: d for d in dsd.dimensions}
    cl_by_dim_id = {d.id: d.codelist for d in dsd.dimensions}
    code_ids_by_dim_id = {d_id: [c.id for c in cl.codes] for d_id, cl in cl_by_dim_id.items()}
    return dsd, dim_by_id, cl_by_dim_id, code_ids_by_dim_id


# ── single-step lookups ──────────────────────────────────────────────────────


def test_nav_dim_lookup(benchmark, resolved_dsd) -> None:
    """ItemList[id] vs dict[id] — single dimension lookup."""
    dsd, _, _, _ = resolved_dsd

    def run() -> None:
        _ = dsd.dimensions["D00"]

    benchmark(run)


def test_direct_dim_lookup(benchmark, resolved_dsd) -> None:
    _, dim_by_id, _, _ = resolved_dsd

    def run() -> None:
        _ = dim_by_id["D00"]

    benchmark(run)


def test_nav_codelist_resolve(benchmark, resolved_dsd) -> None:
    """dim.codelist — Ref unwrap + isinstance + property."""
    dsd, _, _, _ = resolved_dsd
    dim = dsd.dimensions["D00"]

    def run() -> None:
        _ = dim.codelist

    benchmark(run)


def test_direct_codelist_resolve(benchmark, resolved_dsd) -> None:
    _, _, cl_by_dim_id, _ = resolved_dsd

    def run() -> None:
        _ = cl_by_dim_id["D00"]

    benchmark(run)


# ── full traversals ──────────────────────────────────────────────────────────


def test_nav_walk_all_codes(benchmark, resolved_dsd) -> None:
    """``{dim.id: [c.id for c in dim.codes]}`` over every dimension."""
    dsd, _, _, _ = resolved_dsd

    def run() -> dict[str, list[str]]:
        return {dim.id: [c.id for c in dim.codes] for dim in dsd.dimensions}

    benchmark(run)


def test_direct_walk_all_codes(benchmark, resolved_dsd) -> None:
    """Same shape, pre-built dict — no Ref calls, no ItemList walk."""
    _, _, _, code_ids_by_dim_id = resolved_dsd

    def run() -> dict[str, list[str]]:
        return {d_id: list(ids) for d_id, ids in code_ids_by_dim_id.items()}

    benchmark(run)


# ── high-level API ───────────────────────────────────────────────────────────


def test_dsd_all_codelists(benchmark, resolved_dsd) -> None:
    """``dsd.all_codelists`` — property that walks every component."""
    dsd, _, _, _ = resolved_dsd
    benchmark(lambda: dsd.all_codelists)


def test_dsd_code_map(benchmark, resolved_dsd) -> None:
    """``dsd.code_map(lang="en")`` — the canonical aggregate."""
    dsd, _, _, _ = resolved_dsd
    benchmark(dsd.code_map, lang="en")


# ── repeated access (caching probe) ──────────────────────────────────────────


def test_nav_repeated_dim_lookup(benchmark, resolved_dsd) -> None:
    """100× ItemList[id] — surfaces any per-call overhead amortised."""
    dsd, _, _, _ = resolved_dsd

    def run() -> None:
        for _ in range(100):
            _ = dsd.dimensions["D00"]

    benchmark(run)


def test_direct_repeated_dim_lookup(benchmark, resolved_dsd) -> None:
    _, dim_by_id, _, _ = resolved_dsd

    def run() -> None:
        for _ in range(100):
            _ = dim_by_id["D00"]

    benchmark(run)
