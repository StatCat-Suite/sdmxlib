"""The cost of a codelist's whole-artefact rules, on a large codelist.

They run as the codelist is built (#492), so every construction pays them —
a read, an `attrs.evolve`, a store's eager read. These benchmarks lock that in:

- ``rules_100k_codes`` records the absolute cost of a 100k-code codelist's
  rules. A regression to per-code Python work shows up as a large slowdown.
- ``rules_overhead_vs_serialize_100k`` asserts that the rules cost less than
  serialising the same codelist: invisible relative to the work around them.

Run with ``just bench`` or ``uv run pytest tests/bench -m bench``.
"""

from __future__ import annotations

import time

import pytest

from sdmxlib.formats import serialize
from sdmxlib.model._invariants import issues_of
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.message import StructureMessage

_LARGE_N = 100_000


def _big_codelist(n: int = _LARGE_N) -> Codelist:
    return Codelist(
        id="CL_BIG",
        maintainer="TEST",
        version="1.0",
        # Named, because a name is required on every NameableArtefact and
        # this benchmark measures the cost of the rules, not of 100,001
        # issues about the fixture (#269).
        name={"en": "Big codelist"},
        codes=[Code(id=f"C{i:06d}", name={"en": f"Code {i}"}) for i in range(n)],
    )


def test_rules_100k_codes(benchmark) -> None:
    cl = _big_codelist()
    benchmark(issues_of, cl)


def test_rules_overhead_vs_serialize_100k() -> None:
    """The rules must cost less than serialising what they were run on.

    Asserts a relative ratio rather than an absolute time so the test isn't
    sensitive to host speed. Vectorised polars path on 100k ids takes
    <100ms; SDMX-ML 3.0 serialisation of the same codelist takes seconds.
    """
    cl = _big_codelist()
    msg = StructureMessage(codelists=[cl])

    # Warm caches.
    serialize(msg, version="3.0")

    iters = 3

    t0 = time.perf_counter()
    for _ in range(iters):
        issues_of(cl)
    t_validate = (time.perf_counter() - t0) / iters

    t0 = time.perf_counter()
    for _ in range(iters):
        serialize(msg, version="3.0")
    t_serialize = (time.perf_counter() - t0) / iters

    ratio = t_validate / t_serialize
    # Loose ceiling — the vectorised path is dramatically cheaper than
    # serialization. A Python-loop regression would push this over 1.0.
    assert ratio < 0.25, (
        f"validation overhead too high: {t_validate * 1000:.1f}ms vs "
        f"serialize {t_serialize * 1000:.1f}ms (ratio {ratio:.2f}) — "
        "the polars-vectorised path in _check_item_ids is likely broken"
    )


@pytest.mark.parametrize("n", [100, 10_000, 100_000])
def test_rules_scale_linearly(benchmark, n: int) -> None:
    """Sanity: the rules' time scales linearly, not quadratically, in n."""
    cl = _big_codelist(n)
    benchmark(issues_of, cl)
