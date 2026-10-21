"""Tiny timing + JSON-baseline helper for ``bench/``.

Standalone (no ``pytest-benchmark`` dep). Two patterns:

* ``time_serial(name, iterations, fn)`` — call ``fn()`` ``iterations``
  times in one thread, capture per-call timing, return median/p99 in µs.
* ``time_concurrent(name, workers, iter_per_worker, fn)`` —
  ``ThreadPoolExecutor`` releases all workers via a barrier, each runs
  ``fn()`` ``iter_per_worker`` times. Return wall ops/sec and exceptions
  swallowed per worker (so corruption-bug runs don't kill the bench).
"""

from __future__ import annotations

import json
import statistics
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path

BASELINE_PATH = Path(__file__).parent / "baselines.json"


@dataclass
class Result:
    name: str
    iterations: int
    median_us: float | None = None
    p99_us: float | None = None
    ops_per_sec: float | None = None
    workers: int | None = None
    errors: int = 0
    error_samples: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        d = asdict(self)
        # Round timing fields to keep baselines.json readable / diffable.
        for k in ("median_us", "p99_us", "ops_per_sec"):
            if d[k] is not None:
                d[k] = round(d[k], 2)
        return d


def time_serial(name: str, iterations: int, fn: Callable[[], object]) -> Result:
    """Time ``fn`` called ``iterations`` times in this thread."""
    samples: list[float] = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1e6)
    samples.sort()
    return Result(
        name=name,
        iterations=iterations,
        median_us=statistics.median(samples),
        p99_us=samples[int(0.99 * (len(samples) - 1))],
    )


def time_concurrent(
    name: str,
    workers: int,
    iter_per_worker: int,
    fn: Callable[[], object],
) -> Result:
    """Run ``fn`` from ``workers`` threads, ``iter_per_worker`` calls each.

    Workers are released by a single barrier. Exceptions are caught
    per call (so cursor-race corruption doesn't kill the run) and
    counted/sampled.
    """
    barrier = threading.Barrier(workers)
    errors: list[str] = []
    err_lock = threading.Lock()

    def worker() -> int:
        barrier.wait()
        local_errors = 0
        for _ in range(iter_per_worker):
            try:
                fn()
            except Exception as exc:
                local_errors += 1
                if len(errors) < 5:  # cap stored samples
                    with err_lock:
                        if len(errors) < 5:
                            errors.append(f"{type(exc).__name__}: {exc}")
        return local_errors

    t0 = time.perf_counter()
    err_total = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(worker) for _ in range(workers)]
        for fut in as_completed(futures):
            err_total += fut.result()
    elapsed = time.perf_counter() - t0
    total_ops = workers * iter_per_worker
    return Result(
        name=name,
        iterations=total_ops,
        workers=workers,
        ops_per_sec=total_ops / elapsed,
        errors=err_total,
        error_samples=errors,
    )


def load_baselines(path: Path = BASELINE_PATH) -> dict[str, object]:
    """Read ``baselines.json``, or ``{}`` when there is no baseline yet.

    The payload is heterogeneous (``_version`` is a string, ``results`` a
    map of workload dicts), so it comes back as ``object`` values and
    ``report`` narrows what it reads.
    """
    if not path.exists():
        return {}
    decoded: object = json.loads(path.read_text())
    return decoded if isinstance(decoded, dict) else {}


def save_baselines(results: list[Result], version: str, path: Path = BASELINE_PATH) -> None:
    payload: dict[str, object] = {
        "_version": version,
        "_note": (
            "Pinned read-perf baseline. Compared against by "
            "`just bench-concurrency`; overwritten by "
            "`just bench-concurrency-update`. See bench/concurrency/README.md."
        ),
        "results": {r.name: r.to_dict() for r in results},
    }
    path.write_text(json.dumps(payload, indent=2) + "\n")


def _number(value: object) -> float | None:
    """Narrow a decoded JSON value to a number, or ``None`` if it isn't one."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _fmt_delta(current: float | None, baseline: float | None, *, lower_is_better: bool) -> str:
    if current is None or baseline is None or baseline == 0:
        return "—"
    delta_pct = (current - baseline) / baseline * 100
    sign = "+" if delta_pct >= 0 else ""
    regressed = (delta_pct > 0) if lower_is_better else (delta_pct < 0)
    marker = "⚠" if regressed and abs(delta_pct) > 5 else " "
    return f"{marker} {sign}{delta_pct:.1f}%"


def report(results: list[Result], baselines: dict[str, object]) -> None:
    raw_results = baselines.get("results")
    base_results: dict[str, object] = raw_results if isinstance(raw_results, dict) else {}
    base_version = baselines.get("_version", "<none>")
    print(f"\nBaseline pinned to: {base_version}")
    print("-" * 96)
    header = f"{'workload':<40}{'median µs':>12}{'p99 µs':>12}{'ops/sec':>14}{'errors':>10}"
    print(header)
    print("-" * 96)
    for r in results:
        entry = base_results.get(r.name)
        b: dict[str, object] = entry if isinstance(entry, dict) else {}
        med_str = f"{r.median_us:.2f}" if r.median_us is not None else "—"
        p99_str = f"{r.p99_us:.2f}" if r.p99_us is not None else "—"
        ops_str = f"{r.ops_per_sec:.0f}" if r.ops_per_sec is not None else "—"
        err_str = f"{r.errors}" if r.workers else "—"
        print(f"{r.name:<40}{med_str:>12}{p99_str:>12}{ops_str:>14}{err_str:>10}")
        if b:
            med_d = _fmt_delta(r.median_us, _number(b.get("median_us")), lower_is_better=True)
            p99_d = _fmt_delta(r.p99_us, _number(b.get("p99_us")), lower_is_better=True)
            ops_d = _fmt_delta(r.ops_per_sec, _number(b.get("ops_per_sec")), lower_is_better=False)
            print(f"{'  Δ vs baseline':<40}{med_d:>12}{p99_d:>12}{ops_d:>14}")
        if r.error_samples:
            for sample in r.error_samples[:3]:
                print(f"    err: {sample}")
    print("-" * 96)
