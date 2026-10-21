"""Run a keyed piece of work once, even when several threads ask at once.

`docs/design/caching.md` states the contract every runtime cache here owes:

> Concurrent reads are safe and do not duplicate work.

and requires single-flight rather than a plain mutex, because a mutex only
*serialises* — the second caller still rebuilds after the first releases unless
the cache is re-checked under the lock. It also requires the cache be bounded
and routed through one shared primitive rather than a fresh module global.

`_FIFOCache` in `local/registry.py` is the reference implementation for caching
a *value*. This is its sibling for caching the fact that **work already
happened**: a fetch writes into a store, and what a second caller needs is not
a return value but the knowledge that waiting is enough. So `work`'s result is
discarded — deliberately, since a waiter has no run of its own to take one
from, and handing it the leader's would be a different primitive.

Example:
    flight = SingleFlight(maxsize=256)
    flight.run(key, lambda: fetch_into(store, plan, client))
"""

import threading
from collections.abc import Callable, Hashable

__all__ = ["SingleFlight"]


class SingleFlight:
    """Keyed once-only execution, bounded, with a lock-free hit path.

    Three arrivals for one key produce one call to `work`: the first runs it,
    the other two wait on an `Event` and return when it completes. A fourth
    arrival after completion returns immediately without touching the lock —
    `key in self._done` is a plain dict read, GIL-atomic in CPython, which is
    the same reasoning `_FIFOCache` documents for its reads.

    **Eviction repeats work; it does not corrupt it.** `_done` is bounded and
    evicts FIFO, so a key pushed out is simply run again next time. That is
    only sound because the work this guards is *idempotent* — re-fetching a
    plan re-writes artefacts a store already has. Do not route non-idempotent
    work through it.

    **A failure is not remembered.** If `work` raises, the key is not recorded
    and the exception reaches the caller who ran it; waiters wake, find the key
    absent, and one of them becomes the next leader. So a transient network
    failure does not poison the key for the life of the process, and a
    permanent one surfaces to every caller rather than being swallowed once.
    """

    def __init__(self, maxsize: int) -> None:
        """Bound the completed-key set.

        Args:
            maxsize: How many completed keys to remember. Must be positive —
                zero would defeat the point, since every arrival would re-run
                the work and the single-flight gate would be the only thing
                left.

        Raises:
            ValueError: If `maxsize` is not positive.
        """
        if maxsize <= 0:
            msg = f"maxsize must be positive, got {maxsize}"
            raise ValueError(msg)
        self.maxsize = maxsize
        # dict-as-ordered-set: insertion order is the eviction order, and
        # membership reads stay a plain dict lookup.
        self._done: dict[Hashable, None] = {}
        self._in_flight: dict[Hashable, threading.Event] = {}
        self._lock = threading.Lock()

    def run(self, key: Hashable, work: Callable[[], object]) -> None:
        """Run `work` unless this `key` has already been run, or is running.

        Args:
            key: Identifies the work. Two calls with equal keys share one run.
            work: The side-effecting call. Runs at most once per key per
                eviction lifetime, on whichever thread arrives first. Its
                return value is **discarded** — this remembers that the effect
                happened, not what it produced, because a waiter that arrives
                after completion has no run of its own to take a value from.

        Raises:
            Exception: Whatever `work` raises, on the thread that ran it. The
                key stays unrecorded, so a later caller retries.
        """
        if key in self._done:
            return

        while True:
            with self._lock:
                if key in self._done:
                    return
                waiting = self._in_flight.get(key)
                if waiting is None:
                    event = threading.Event()
                    self._in_flight[key] = event
                    break
            # Wait outside the lock, then re-check: the leader may have failed,
            # in which case this thread takes its turn rather than returning as
            # though the work had been done.
            waiting.wait()

        try:
            work()
        except BaseException:
            with self._lock:
                del self._in_flight[key]
            event.set()
            raise

        with self._lock:
            # Recording and clearing `_in_flight` happen under one lock, and
            # the event is set after. Ordering the set *last* is not what
            # makes this safe — a waiter re-checks under the lock, so it can
            # never observe the key gone from `_in_flight` and absent from
            # `_done`, whichever order the set takes. Moving `event.set()`
            # above this block survives the test suite, and correctly so: it
            # costs a waiter one spin around the loop, not a duplicate run.
            # Kept here because a spin is still waste.
            self._done[key] = None
            while len(self._done) > self.maxsize:
                del self._done[next(iter(self._done))]
            del self._in_flight[key]
        event.set()

    def __contains__(self, key: Hashable) -> bool:
        """Whether `key` has run and not yet been evicted."""
        return key in self._done

    def __len__(self) -> int:
        """How many completed keys are remembered."""
        return len(self._done)

    def clear(self) -> None:
        """Forget every completed key, so the next arrival runs again.

        In-flight work is left alone: it is already running, and its thread
        will record it. That is harmless — the entry is simply remembered
        again, which is what would happen on the next call anyway.
        """
        with self._lock:
            self._done.clear()
