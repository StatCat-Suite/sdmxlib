"""`SingleFlight` — the primitive, tested for the properties it claims.

`docs/design/caching.md` asks a runtime cache for three things: single-flight
(not just a lock), a bound, and a stated invalidation. Each is asserted here as
a **count of calls to the expensive work**, per the same page's checklist —
never as wall-clock, which would make these tests flaky on a loaded machine.

The concurrency tests use a barrier rather than a sleep, so "arrived together"
is a fact rather than a hope: every thread blocks until all of them have
reached the call, and the barrier itself times out rather than hanging the
suite if the arrangement is wrong.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import pytest

from sdmxlib._singleflight import SingleFlight

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.unit

_TIMEOUT = 10.0


def _await(predicate: Callable[[], bool], message: str) -> None:
    """Poll until `predicate` holds, or fail — never hang.

    Written the moment a mutant proved the point: an unbounded poll turns a
    broken implementation into a suite that never finishes, which reads as
    infrastructure trouble rather than as the defect it is.
    """
    tick = threading.Event()
    for _ in range(int(_TIMEOUT / 0.001)):
        if predicate():
            return
        tick.wait(0.001)
    raise AssertionError(message)


class _Counter:
    """Work that records how often it actually ran, and can be made to block."""

    def __init__(self, *, gate: threading.Event | None = None) -> None:
        self.runs = 0
        self._lock = threading.Lock()
        self._gate = gate

    def __call__(self) -> None:
        with self._lock:
            self.runs += 1
        if self._gate is not None:
            assert self._gate.wait(_TIMEOUT), "the gate was never released"


# ── once per key ──────────────────────────────────────────────────────────


def test_a_repeated_key_runs_once() -> None:
    flight, work = SingleFlight(maxsize=8), _Counter()
    for _ in range(5):
        flight.run("k", work)
    assert work.runs == 1


def test_distinct_keys_each_run() -> None:
    """Or the cache would be answering the wrong question, silently."""
    flight, work = SingleFlight(maxsize=8), _Counter()
    for key in ("a", "b", "c"):
        flight.run(key, work)
    assert work.runs == 3


def test_membership_and_length_report_what_is_remembered() -> None:
    flight = SingleFlight(maxsize=8)
    flight.run("a", _Counter())
    assert "a" in flight
    assert "b" not in flight
    assert len(flight) == 1


# ── single-flight, not just a lock ────────────────────────────────────────


def test_concurrent_arrivals_for_one_key_produce_one_run() -> None:
    """The property a plain mutex does *not* give you.

    A mutex would serialise these three followers and let each run the work in
    turn once the leader released — three fetches, one at a time, which is
    slower than no cache at all.

    The arrangement makes the fast path unavailable: the leader is held inside
    `work` by a closed gate, so the key cannot be in `_done` when a follower
    calls `run`. A follower therefore either duplicates the work (caught by
    the count) or waits. The gate opens only once all three followers have
    reached the call.
    """
    gate = threading.Event()
    flight, work = SingleFlight(maxsize=8), _Counter(gate=gate)
    leading = threading.Event()
    arrived = threading.Semaphore(0)

    def _lead() -> None:
        leading.set()
        flight.run("k", work)

    def _follow() -> None:
        arrived.release()
        flight.run("k", work)

    with ThreadPoolExecutor(max_workers=4) as pool:
        leader = pool.submit(_lead)
        assert leading.wait(_TIMEOUT)
        # The leader is inside `work` and blocked; `_in_flight` says so.
        # Bounded, so an implementation that never registers the key fails
        # here rather than hanging the suite — which is what the naive
        # check-run-record version does, and how this test kills it.
        _await(lambda: "k" in flight._in_flight, "the leader never claimed the key")
        followers = [pool.submit(_follow) for _ in range(3)]
        for _ in range(3):
            assert arrived.acquire(timeout=_TIMEOUT)
        gate.set()
        for future in (leader, *followers):
            future.result(timeout=_TIMEOUT)

    assert work.runs == 1


def test_concurrent_arrivals_for_different_keys_do_not_block_each_other() -> None:
    """Single-flight is per key. A single global lock would pass the test
    above and deadlock here, because each thread's work waits for a gate that
    only opens after every thread has started."""
    started = threading.Barrier(3, timeout=_TIMEOUT)
    flight = SingleFlight(maxsize=8)

    def _work() -> None:
        started.wait()

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(flight.run, key, _work) for key in ("a", "b", "c")]
        for future in futures:
            future.result(timeout=_TIMEOUT)

    assert len(flight) == 3


# ── failure is not remembered ─────────────────────────────────────────────


def test_a_failure_reaches_the_caller_and_is_retried() -> None:
    """A cached failure would be the worst outcome: one transient network
    error would make a key permanently unanswerable for the life of the
    process, with no way to ask again."""
    flight = SingleFlight(maxsize=8)
    attempts = 0

    def _flaky() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            msg = "network"
            raise RuntimeError(msg)

    with pytest.raises(RuntimeError, match="network"):
        flight.run("k", _flaky)
    assert "k" not in flight, "a failed run must not be recorded"

    flight.run("k", _flaky)
    assert attempts == 2
    assert "k" in flight


def test_waiters_retry_after_the_leader_fails() -> None:
    """The subtle half: a follower blocked on the leader's event must not
    return as though the work had succeeded. It wakes, finds the key absent,
    and takes its own turn.

    Who leads is *arranged*, not raced: the follower is only submitted once
    the leader has claimed the key, so the failing run is provably the one
    holding the gate.
    """
    flight = SingleFlight(maxsize=8)
    release = threading.Event()
    arrived = threading.Semaphore(0)
    attempts = 0
    lock = threading.Lock()

    def _work() -> None:
        nonlocal attempts
        with lock:
            attempts += 1
            mine = attempts
        if mine == 1:
            arrived.release()
            assert release.wait(_TIMEOUT)
            msg = "leader fails"
            raise RuntimeError(msg)

    def _lead() -> None:
        with pytest.raises(RuntimeError, match="leader fails"):
            flight.run("k", _work)

    with ThreadPoolExecutor(max_workers=2) as pool:
        leader = pool.submit(_lead)
        assert arrived.acquire(timeout=_TIMEOUT), "the leader never entered the work"
        follower = pool.submit(flight.run, "k", _work)
        release.set()
        leader.result(timeout=_TIMEOUT)
        follower.result(timeout=_TIMEOUT)

    assert attempts == 2, "the follower ran rather than inheriting a failure"
    assert "k" in flight, "and its success is what gets remembered"


# ── bounded ───────────────────────────────────────────────────────────────


def test_the_oldest_key_is_evicted_past_the_bound() -> None:
    flight, work = SingleFlight(maxsize=2), _Counter()
    for key in ("a", "b", "c"):
        flight.run(key, work)

    assert len(flight) == 2
    assert "a" not in flight, "FIFO: the first inserted goes first"
    assert "b" in flight
    assert "c" in flight


def test_an_evicted_key_runs_again() -> None:
    """Which is why the work must be idempotent — stated on the class, and
    the reason eviction is safe here at all."""
    flight, work = SingleFlight(maxsize=1), _Counter()
    flight.run("a", work)
    flight.run("b", work)
    flight.run("a", work)
    assert work.runs == 3


def test_the_bound_holds_under_concurrent_arrivals() -> None:
    """Eviction runs under the lock; without it the dict would be mutated
    while another thread iterated it for the oldest key."""
    flight, work = SingleFlight(maxsize=4), _Counter()
    with ThreadPoolExecutor(max_workers=8) as pool:
        for future in [pool.submit(flight.run, f"k{i}", work) for i in range(64)]:
            future.result(timeout=_TIMEOUT)
    assert work.runs == 64
    assert len(flight) == 4


def test_a_non_positive_bound_is_refused() -> None:
    """`maxsize=0` would leave the gate but no memory — every arrival reruns.
    That is a config that looks like a cache and is not one."""
    with pytest.raises(ValueError, match="maxsize must be positive"):
        SingleFlight(maxsize=0)
    with pytest.raises(ValueError, match="maxsize must be positive"):
        SingleFlight(maxsize=-1)


# ── invalidation ──────────────────────────────────────────────────────────


def test_clear_makes_the_next_arrival_run_again() -> None:
    flight, work = SingleFlight(maxsize=8), _Counter()
    flight.run("k", work)
    flight.clear()
    flight.run("k", work)
    assert work.runs == 2
    assert len(flight) == 1
