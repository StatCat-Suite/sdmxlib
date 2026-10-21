"""Concurrent-read corruption reproducers for issue #75.

Two failure modes to capture:

1. **Exceptions from clobbered row buffers** — ``IndexError`` /
   ``ValueError`` raised inside ``readers.py`` because one thread's
   ``execute()`` overwrites the cursor's row buffer before another
   thread finishes consuming it.
2. **Silent identity corruption** — ``get(urn_A)`` returns an artefact
   populated from rows belonging to ``urn_B``. This is the data-integrity
   claim in #75 and the real reason to ship the fix; (1) only makes the
   bug *visible*.

Both tests use a small ``LocalRegistry(":memory:")`` populated with a mix of
artefact types and a ``ThreadPoolExecutor`` to race reads. An inner
``REPEAT`` loop amplifies the race window so failures on ``main`` are
deterministic; once the fix lands the tests should pass under repeat
without flakes.
"""

from __future__ import annotations

import random
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import duckdb
import pytest

from sdmxlib.local import LocalRegistry
from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.concept import Concept, ConceptScheme
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure
from sdmxlib.model.errors import ValidationError
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.urn import SdmxUrn

# `serial`: `just test` runs these alone, after the parallel phase; see there.
pytestmark = [pytest.mark.unit, pytest.mark.slow, pytest.mark.serial]

_NAME = InternationalString(en="Test")


def _populate(reg: LocalRegistry) -> list[tuple[SdmxUrn[MaintainableArtefact], str]]:
    """Add a mix of artefacts. Returns ``[(urn, expected_id), ...]``."""
    entries: list[tuple[SdmxUrn[MaintainableArtefact], str]] = []

    for i in range(4):
        cl = Codelist(
            id=f"CL_{i}",
            maintainer="SDMX",
            version="1.0",
            name=_NAME,
            codes=[Code(id=f"C{i}_{j}", name=InternationalString(en=f"Code {i}.{j}")) for j in range(3)],
        )
        reg.add(cl)
        entries.append((Codelist.urn_for(agency="SDMX", id=f"CL_{i}", version="1.0"), f"CL_{i}"))

    for i in range(3):
        cs = ConceptScheme(
            id=f"CS_{i}",
            maintainer="SDMX",
            version="1.0",
            name=_NAME,
            concepts=[Concept(id="FREQ", name=_NAME)],
        )
        reg.add(cs)
        entries.append((ConceptScheme.urn_for(agency="SDMX", id=f"CS_{i}", version="1.0"), f"CS_{i}"))

    for i in range(3):
        dsd = DataStructure(id=f"DSD_{i}", maintainer="SDMX", version="1.0", name=_NAME)
        reg.add(dsd)
        entries.append((DataStructure.urn_for(agency="SDMX", id=f"DSD_{i}", version="1.0"), f"DSD_{i}"))

    for i in range(3):
        df = Dataflow(id=f"DF_{i}", maintainer="SDMX", version="1.0", name=_NAME)
        reg.add(df)
        entries.append((Dataflow.urn_for(agency="SDMX", id=f"DF_{i}", version="1.0"), f"DF_{i}"))

    return entries


REPEAT_MIXED = 200
REPEAT_CACHE_HIT = 400
"""Inner repeats per worker — amplifies race window so failures on ``main``
are deterministic rather than flaky. Combined with the ``threading.Barrier``
that releases all workers simultaneously, this reliably surfaces the cursor
race within a single test invocation.

Measured by putting the race back (one cursor shared by every thread), 20 or
30 runs each. The mixed-URN reproducer caught it 30/30 at 200 and 20/20 at
800, and missed one in 20 at 50 and one in 10 at 10: 200 keeps the margin at a
quarter of the cost. `test_the_mixed_reproducer_still_catches_a_shared_cursor`
keeps proving it. The cache-hit one is weaker per iteration — 22/30 at 100,
16/30 at 50 — so it keeps 400 (10/10)."""

WORKERS_MIXED = 16
WORKERS_CACHE_HIT = 32


def _race_mixed_reads(reg: LocalRegistry) -> None:
    """Race `WORKERS_MIXED` threads of eager `get` over mixed URNs; raise on any corruption."""
    entries = _populate(reg)
    rng = random.Random(0xC0FFEE)
    barrier = threading.Barrier(WORKERS_MIXED)

    def worker(seed: int) -> list[tuple[str, str]]:
        local_rng = random.Random(seed)
        results: list[tuple[str, str]] = []
        barrier.wait()
        for _ in range(REPEAT_MIXED):
            urn, expected_id = local_rng.choice(entries)
            # eager=True forces resolution of any Refs the artefact
            # carries, which adds extra SQL round-trips per get — that
            # widens the race window and makes failures more reliable
            # than a bare lookup of a leaf artefact.
            obj = reg.get(urn, eager=True)
            assert obj is not None, f"missing artefact for {urn!r}"
            results.append((expected_id, obj.id))
        return results

    seeds = [rng.randrange(2**31) for _ in range(WORKERS_MIXED)]
    with ThreadPoolExecutor(max_workers=WORKERS_MIXED) as pool:
        futures = [pool.submit(worker, s) for s in seeds]
        for fut in as_completed(futures):
            # Re-raise any worker exception (IndexError/ValueError from
            # clobbered buffers) with full traceback.
            pairs = fut.result()
            for expected_id, actual_id in pairs:
                assert actual_id == expected_id, (
                    f"identity corruption: expected {expected_id!r}, "
                    f"got {actual_id!r} — get() returned an artefact "
                    f"belonging to a different URN"
                )


def test_concurrent_get_identity_mixed_urns():
    """Concurrent ``get`` across mixed URNs must not return cross-wired artefacts.

    On ``main`` this fails with either:
      - ``IndexError: tuple index out of range`` (readers.py — clobbered
        row buffer yielded a row with the wrong column count), or
      - an identity mismatch: ``reg.get(urn_for_CL_1).id == "DF_2"``
        because the shared cursor returned a Dataflow row while a
        Codelist read was in flight.

    With the fix, all ``WORKERS_MIXED × REPEAT`` reads return the
    artefact whose URN was actually requested.
    """
    with LocalRegistry(":memory:") as reg:
        _race_mixed_reads(reg)


def test_the_mixed_reproducer_still_catches_a_shared_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard above, watched failing: #75's bug put back, it must notice.

    A reproducer passes on fixed code whether or not it can still see the bug,
    so `REPEAT_MIXED` could be cut until it saw nothing and stay green. Three
    attempts, because a race is a probability: at the measured rate one
    attempt misses well under one time in 30, and three in a row do not.
    """
    shared: list[duckdb.DuckDBPyConnection] = []

    def one_cursor_for_every_thread(self: LocalRegistry) -> duckdb.DuckDBPyConnection:
        if not shared:
            cur = self._require_open().cursor()
            cur.execute("SET search_path = 'sdmx'")
            shared.append(cur)
        return shared[0]

    monkeypatch.setattr(LocalRegistry, "_read_cursor", one_cursor_for_every_thread)
    for _ in range(3):
        shared.clear()
        with LocalRegistry(":memory:") as reg:
            try:
                _race_mixed_reads(reg)
            # A garbled row now often meets a field validator before anything
            # else, and is refused as the model builds it (#492).
            except AssertionError, IndexError, ValueError, TypeError, ValidationError:
                return
    pytest.fail("three races over one shared cursor read back clean: the reproducer no longer reproduces")


def test_concurrent_get_cache_hit_invariant():
    """Cache-hit hot loop: 32 threads × ``REPEAT`` reads of the *same* URN.

    Canary for the FIFO-cache change in #78 — after that lands, cache
    reads are supposed to be lock-free, and any racing in the cache
    accounting would surface here as either an exception or a wrong
    identity.

    Today on ``main`` this exercises the shared cursor under maximum
    contention (every thread reads the same artefact, so the shared
    state is hit on every iteration).
    """
    with LocalRegistry(":memory:") as reg:
        entries = _populate(reg)
        # Pick a deterministic target (a Dataflow — eager=True will also
        # exercise Ref resolution against the structure cache) and warm
        # the cache.
        target_urn, target_id = next(e for e in entries if e[1].startswith("DF_"))
        warmup = reg.get(target_urn, eager=True)
        assert warmup is not None
        assert warmup.id == target_id

        barrier = threading.Barrier(WORKERS_CACHE_HIT)

        def worker() -> None:
            barrier.wait()
            for _ in range(REPEAT_CACHE_HIT):
                obj = reg.get(target_urn, eager=True)
                assert obj is not None
                assert obj.id == target_id, f"cache-hit identity corruption: expected {target_id!r}, got {obj.id!r}"

        with ThreadPoolExecutor(max_workers=WORKERS_CACHE_HIT) as pool:
            futures = [pool.submit(worker) for _ in range(WORKERS_CACHE_HIT)]
            for fut in as_completed(futures):
                fut.result()
