"""How much of this machine the sweep may use, decided when it starts.

The sweep is single-threaded per document and documents are independent, so
it can run several at once — on a machine that has the room. "Has the room"
is what this module measures: cores that are *idle now* (not cores that
exist) and memory that is *available now* (not memory that is installed),
read from the OS each run, so a laptop already compiling something, or a CI
container with a cgroup limit, gets fewer workers than an idle workstation.

Two limits, and the run takes the smaller:

- **CPU**: idle cores, minus one left for whoever is using the machine.
- **Memory**: a budget of `_MEMORY_SHARE` of available memory, spent per job
  by `estimate`. A job is only started when its estimate fits beside the
  ones running, so one huge document runs alone rather than beside five
  others and swapping.

Standard library only, since psutil is not a dependency: `vm_stat` on macOS,
`/proc/meminfo` and the cgroup files on Linux. Anywhere else the probe gives
up and the sweep runs one document at a time, which is what it did before.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from attrs import frozen

#: Share of currently-available memory the sweep may plan to use. The rest is
#: slack for estimates that run low and for whatever else the machine is doing.
_MEMORY_SHARE = 0.6

#: Resident memory per byte of document parsed at once, and the fixed cost of
#: a worker. Measured per task (`peak_mb` in the full report), not guessed:
#:
#: - IMF codelist chunks, 100-124 MB each: 2.2-2.6 GB (~19x over the base)
#: - IMF codelists, 685 MB whole, one process: 8.0 GB (~11.7x)
#: - OECD codelists 2.1 + 3.0 pair, 153 MB: 1,723 MB (~9.8x over the base)
#: - BIS codelist pair, 8.6 MB: 367 MB, of which ~220 MB is the base
#:
#: Code-dense chunks cost the most per byte, and an underestimate is the
#: expensive direction — it lets too many jobs start — so the factor sits
#: above the highest ratio seen.
_BYTES_FACTOR = 20
_WORKER_BASE = 300 * 1024 * 1024

_GIB = 1024**3


@frozen
class Capacity:
    """What a sweep run may use.

    Attributes:
        cores: Cores this process may run on.
        load: One-minute load average, or None where the OS has none.
        workers: Documents to run at once.
        available: Memory available now, in bytes, or None if unknown.
        budget: Bytes of job estimates that may be in flight at once.
        source: How memory was measured, for the printed plan.
    """

    cores: int
    load: float | None
    workers: int
    available: int | None
    budget: int | None
    source: str

    def describe(self) -> str:
        """One line for the start of a run."""
        load = f", load {self.load:.1f}" if self.load is not None else ""
        if self.available is None or self.budget is None:
            memory = f"memory unknown ({self.source})"
        else:
            memory = f"{self.available / _GIB:.1f} GB available ({self.source}), budget {self.budget / _GIB:.1f} GB"
        return f"{self.cores} cores{load}; {memory} → {self.workers} worker(s)"


def estimate(parsed_at_once: int) -> int:
    """Peak resident bytes for analysing `parsed_at_once` bytes of SDMX-ML.

    The caller says how much is parsed at once — the whole document, or one
    chunk of a split one — since that, not the file size, is what sets the peak.
    """
    return _WORKER_BASE + _BYTES_FACTOR * parsed_at_once


def measure(jobs: int | None = None) -> Capacity:
    """This machine's capacity now, or a fixed worker count if `jobs` is given."""
    cores = os.process_cpu_count() or 1
    load = _load()
    available, source = _available_memory()
    budget = int(available * _MEMORY_SHARE) if available is not None else None
    if jobs is not None:
        workers = max(1, jobs)
    elif available is None:
        workers = 1  # nothing to plan memory against: do what the sweep always did
    else:
        idle = cores - (load or 0.0)
        workers = max(1, min(cores - 1, int(idle)))
    return Capacity(cores=cores, load=load, workers=workers, available=available, budget=budget, source=source)


def _load() -> float | None:
    try:
        return os.getloadavg()[0]
    except AttributeError, OSError:
        return None


def _available_memory() -> tuple[int | None, str]:
    linux = _linux_available()
    if linux is not None:
        return linux
    mac = _macos_available()
    if mac is not None:
        return mac, "vm_stat"
    return None, "no probe for this OS"


def _linux_available() -> tuple[int, str] | None:
    meminfo = Path("/proc/meminfo")
    if not meminfo.exists():
        return None
    match = re.search(r"^MemAvailable:\s+(\d+) kB", meminfo.read_text(), re.MULTILINE)
    if match is None:
        return None
    available = int(match.group(1)) * 1024
    # A container sees the host's meminfo; its real ceiling is the cgroup's.
    limit, current = Path("/sys/fs/cgroup/memory.max"), Path("/sys/fs/cgroup/memory.current")
    if limit.exists() and current.exists() and (text := limit.read_text().strip()) != "max":
        return min(available, int(text) - int(current.read_text())), "/proc/meminfo + cgroup"
    return available, "/proc/meminfo"


def _macos_available() -> int | None:
    """Free + inactive + speculative + purgeable pages: what macOS can hand out without swapping."""
    try:
        out = subprocess.run(["/usr/bin/vm_stat"], capture_output=True, text=True, check=True, timeout=5).stdout
    except OSError, subprocess.SubprocessError:
        return None
    page = re.search(r"page size of (\d+) bytes", out)
    if page is None:
        return None
    pages = 0
    for name in ("free", "inactive", "speculative", "purgeable"):
        found = re.search(rf"^Pages {name}:\s+(\d+)\.", out, re.MULTILINE)
        pages += int(found.group(1)) if found else 0
    return pages * int(page.group(1))
