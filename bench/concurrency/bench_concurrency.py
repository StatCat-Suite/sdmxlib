"""Read-path benchmarks for the concurrency refactor (#75–#79).

Run via ``just bench-concurrency`` (compares to ``baselines.json``) or
``just bench-concurrency-update`` (overwrites the baseline).
"""

from __future__ import annotations

import itertools
from importlib.metadata import version
from typing import Annotated

import typer

# ``_harness`` resolves because running a script puts its own directory on
# ``sys.path`` — this file is only ever invoked as
# ``python bench/concurrency/bench_concurrency.py`` (see the justfile).
from _harness import (
    Result,
    load_baselines,
    report,
    save_baselines,
    time_concurrent,
    time_serial,
)

from sdmxlib.local import LocalRegistry
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.concept import Concept, ConceptScheme
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.urn import SdmxUrn

_NAME = InternationalString(en="Test")

# Raised when a URN we just wrote reads back as None — the shape a cursor
# race takes. Hoisted so the raise sites stay one line (EM101/TRY003).
_MISSING_URN = "get returned None for known URN"

# Workload sizes — kept modest so the whole suite runs in under a minute
# on a laptop. Bigger numbers reduce variance but slow iteration.
N_CL = 4  # codelists, each with N_CL_CODES codes
N_CL_CODES = 50
N_CS = 3
N_DSD = 3
N_DF = 3

ITERS_SERIAL = 5000  # single-thread workloads
ITERS_CODE_MAP = 1000  # code_map is heavier per call

WORKERS_LIST = (1, 4, 8, 16)
CACHE_HIT_WORKERS = 32
ITERS_PER_CONCURRENT_WORKER = 500


def _check_not_none(result: object) -> None:
    """Bench-time correctness probe — surface the dominant bug-induced None."""
    if result is None:
        raise RuntimeError(_MISSING_URN)


def _populate(reg: LocalRegistry) -> dict[str, SdmxUrn[object]]:
    """Add a representative mix of artefacts. Returns ``{key: urn}``."""
    # ``SdmxUrn[object]``, not ``[Any]``: the phantom type is never
    # recovered here — the bench only calls ``get`` and checks for None —
    # so an ``Any`` would just ride out through every result below.
    urns: dict[str, SdmxUrn[object]] = {}

    for i in range(N_CL):
        cl = Codelist(
            id=f"CL_{i}",
            maintainer="SDMX",
            version="1.0",
            name=_NAME,
            codes=[Code(id=f"C{i}_{j:03d}", name=InternationalString(en=f"Code {i}.{j}")) for j in range(N_CL_CODES)],
        )
        reg.add(cl)
        urns[f"cl_{i}"] = Codelist.urn_for(agency="SDMX", id=f"CL_{i}", version="1.0")

    for i in range(N_CS):
        cs = ConceptScheme(
            id=f"CS_{i}",
            maintainer="SDMX",
            version="1.0",
            name=_NAME,
            concepts=[Concept(id="FREQ", name=_NAME)],
        )
        reg.add(cs)
        urns[f"cs_{i}"] = ConceptScheme.urn_for(agency="SDMX", id=f"CS_{i}", version="1.0")

    for i in range(N_DSD):
        dsd = DataStructure(id=f"DSD_{i}", maintainer="SDMX", version="1.0", name=_NAME)
        reg.add(dsd)
        urns[f"dsd_{i}"] = DataStructure.urn_for(agency="SDMX", id=f"DSD_{i}", version="1.0")

    for i in range(N_DF):
        df = Dataflow(id=f"DF_{i}", maintainer="SDMX", version="1.0", name=_NAME)
        reg.add(df)
        urns[f"df_{i}"] = Dataflow.urn_for(agency="SDMX", id=f"DF_{i}", version="1.0")

    return urns


def run_benches() -> list[Result]:
    """Execute the full bench suite against an in-memory LocalRegistry."""
    results: list[Result] = []

    with LocalRegistry(":memory:") as reg:
        urns = _populate(reg)
        df_urn = urns["df_0"]
        cl_urn = urns["cl_0"]

        # ── Single-threaded ──────────────────────────────────────────────
        # Cold cache: clear between bursts so each measured call is a real
        # DB read. (The bench harness records per-call timings, so the
        # first call vs later calls difference shows up in p99 anyway —
        # but we make the intent explicit by clearing first.)
        reg.clear_resolve_cache()
        results.append(
            time_serial(
                "single_get_dataflow_cold",
                ITERS_SERIAL,
                lambda: reg.get(df_urn),
            )
        )

        # Warm cache — the get() above warmed everything.
        results.append(
            time_serial(
                "single_get_dataflow_warm",
                ITERS_SERIAL,
                lambda: reg.get(df_urn),
            )
        )

        results.append(
            time_serial(
                "single_get_codelist_eager",
                ITERS_SERIAL,
                lambda: reg.get(cl_urn, eager=True),
            )
        )

        results.append(
            time_serial(
                "single_code_map",
                ITERS_CODE_MAP,
                lambda: reg.code_map(cl_urn, lang="en"),
            )
        )

        # ── Concurrent — mixed URNs ──────────────────────────────────────
        urn_pool = list(urns.values())
        # Cheap deterministic rotation through the pool. ``next`` on a
        # ``count`` is a single C-level step, so it avoids both the
        # per-call random overhead and a lock that would inflate the bench.
        turns = itertools.count()

        def mixed_get() -> None:
            result = reg.get(urn_pool[next(turns) % len(urn_pool)], eager=True)
            # Cursor race surfaces as silent None; count it as an error
            # so the bench reports it. Identity corruption (wrong artefact
            # for the URN) goes uncounted — that's covered by the
            # reproducer tests, not the bench.
            if result is None:
                raise RuntimeError(_MISSING_URN)

        results.extend(
            time_concurrent(
                f"concurrent_get_mixed_{w}w",
                workers=w,
                iter_per_worker=ITERS_PER_CONCURRENT_WORKER,
                fn=mixed_get,
            )
            for w in WORKERS_LIST
        )

        # ── Cache-hit hot loop ───────────────────────────────────────────
        results.append(
            time_concurrent(
                f"concurrent_cache_hit_{CACHE_HIT_WORKERS}w",
                workers=CACHE_HIT_WORKERS,
                iter_per_worker=ITERS_PER_CONCURRENT_WORKER,
                fn=lambda: _check_not_none(reg.get(df_urn, eager=True)),
            )
        )

    return results


def main(*, update: Annotated[bool, typer.Option(help="Overwrite baselines.json with these results.")] = False) -> None:
    """Run read-path benches."""
    print(f"sdmxlib version: {version('sdmxlib')}")
    results = run_benches()

    if update:
        save_baselines(results, version=version("sdmxlib"))
        print(f"\nBaseline written ({len(results)} workloads).")
        report(results, baselines={})
    else:
        baselines = load_baselines()
        report(results, baselines)


if __name__ == "__main__":
    typer.run(main)
