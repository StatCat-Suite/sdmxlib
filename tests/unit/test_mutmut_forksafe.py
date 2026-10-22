"""The premise `tools/mutmut_forksafe.py` rests on, asserted (#314).

Since mutmut 3.8 the workaround is mutmut's own `process_isolation =
"forkserver"`, which the tool requires; it is only needed while three facts
about polars hold, and all three are somebody else's code:

1. executing *any* polars operation in a process makes a later `fork()` fatal
   to a child that also uses polars,
2. merely *importing* polars does not, and
3. a child's own use does not poison the parent, so work can be delegated.

(1) and (2) are why the fix is possible at all — a parent can hold the imports
and delegate the execution. (3) is the mechanism. If polars ever becomes
fork-safe, (1) starts failing here and the whole tool can be deleted; that is
the outcome this file exists to notice.

**Every case runs in a fresh subprocess.** By the time this module executes,
the suite has long since used polars, so forking from *this* process is the
hazard being described — a test that forked directly would hang the run it is
part of. A hang is therefore a result, not a stall: the probe times its own
forked child, kills it if it hangs, and reports "hung"; each case says which
outcome it expects.

**The probe cleans up after itself** (GH-34). It used to time the whole
subprocess and kill only that on timeout, so the poisoning case's hung child
was re-parented to init and lived on: one orphan per suite run, 485 of them on
one machine. Timing from the fork also keeps Python's start-up and the polars
import out of the measurement, which under load had taken long enough to fail
the clean case once.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
from typing import Literal, NamedTuple

import pytest

pytestmark = pytest.mark.unit

#: How long a forked child may run before it counts as hung. A clean child
#: finishes in well under a second, so this is generous; the poisoning case
#: necessarily waits all of it, the cost of asserting a hang.
_HANG_S = 10

#: A backstop for the probe as a whole, start-up included. Reaching it is not a
#: fork result: the machine was too loaded to measure, and the test says so.
_PROBE_S = 120

#: The probe's exit status when it had to kill a hung child.
_HUNG = 124

_POISON = 'pl.Series("id", ["A"], dtype=pl.Utf8).str.contains("^[A-Z]+$")'


class Probe(NamedTuple):
    """What became of the forked child, and its pid (None if it never forked)."""

    outcome: Literal["clean", "died", "hung"]
    child: int | None


def _probe(parent_does: str) -> Probe:
    """Run `parent_does`, then fork a child that uses polars. Did it survive?

    Returns:
        "clean" if the child exited 0, "hung" if it was still running after
        `_HANG_S` and was killed, "died" otherwise.
    """
    # Dedented *before* substitution: `parent_does` is itself multi-line, and
    # dedenting afterwards finds no common prefix and leaves the first line
    # indented on its own — an IndentationError in the child rather than the
    # measurement asked for.
    template = textwrap.dedent("""
        import os, signal, sys, time
        import polars as pl

        PARENT_DOES

        sys.stdout.flush()
        pid = os.fork()
        if pid == 0:
            os.close(1)  # the test reads stdout to EOF; a child holding it would hide its own leak
            pl.Series("id", ["A", "B"], dtype=pl.Utf8).str.contains("^[A-Z]+$")
            os._exit(0)
        print(pid, flush=True)
        deadline = time.monotonic() + HANG_S
        while time.monotonic() < deadline:
            done, status = os.waitpid(pid, os.WNOHANG)
            if done:
                sys.exit(os.waitstatus_to_exitcode(status))
            time.sleep(0.05)
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
        sys.exit(HUNG)
    """)
    program = template.replace("PARENT_DOES", parent_does).replace("HANG_S", str(_HANG_S)).replace("HUNG", str(_HUNG))
    # Its own session, so the backstop can kill the probe and anything it forked.
    probe = subprocess.Popen(  # noqa: S603 — same fixed `sys.executable` shape as the three existing sites
        [sys.executable, "-c", program],
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        out, _ = probe.communicate(timeout=_PROBE_S)
    except subprocess.TimeoutExpired:
        os.killpg(probe.pid, signal.SIGKILL)
        probe.communicate()
        pytest.fail(f"the probe took over {_PROBE_S}s: the machine was too loaded to measure, not a fork result")
    lines = out.split()
    child = int(lines[-1]) if lines else None
    if probe.returncode == 0:
        return Probe("clean", child)
    return Probe("hung" if probe.returncode == _HUNG else "died", child)


@pytest.fixture(scope="module")
def poisoned() -> Probe:
    """The poisoning case, run once: it waits out `_HANG_S`, and two tests read it."""
    return _probe(_POISON)


def test_importing_polars_leaves_the_fork_clean() -> None:
    """Fact (2): the imports may stay in the parent.

    Without this the tool could not preload the native extensions, and a child
    that dlopens pyarrow after mutmut's mutant-generation `Pool` aborts.
    """
    probe = _probe("pass")
    assert probe.outcome == "clean", (
        f"the child {probe.outcome} after importing polars — tools/mutmut_forksafe.py cannot preload"
    )


def test_one_polars_operation_poisons_the_fork(poisoned: Probe) -> None:
    """Fact (1): and *one row* is enough.

    #314 recorded this as needing "real work first", which sent the diagnosis
    towards `Select.collect()`. It does not: two elements and a regex do it, and
    that is why the real source turned out to be `validate()` on the write
    path rather than anything in the query path.

    If this ever passes, polars has become fork-safe and the tool is dead code.
    """
    assert poisoned.outcome != "clean", (
        "a polars operation no longer poisons fork() — re-check whether "
        "tools/mutmut_forksafe.py is still needed, and delete it if not"
    )


def test_delegating_the_work_to_a_child_keeps_the_parent_forkable() -> None:
    """Fact (3): the mechanism the whole tool is built on.

    The parent runs mutmut's three test phases in a forked child, then forks
    again per mutant. That only works because rayon initialises in the child's
    own memory and leaves the parent as it was.
    """
    delegate = textwrap.dedent("""
        pid = os.fork()
        if pid == 0:
            pl.Series("id", [str(i) for i in range(500)], dtype=pl.Utf8).str.contains("^[0-9]+$")
            os._exit(0)
        os.waitpid(pid, 0)
    """).strip()
    probe = _probe(delegate)
    assert probe.outcome == "clean", (
        f"the child {probe.outcome}: delegating polars work to a child no longer leaves the "
        "parent forkable — tools/mutmut_forksafe.py's approach is invalid"
    )


def test_a_hung_child_does_not_outlive_its_probe(poisoned: Probe) -> None:
    """GH-34: the poisoning case's child must be gone once the probe returns.

    Before, a timeout killed only the probe, and the hung child it had forked
    was re-parented to init: one orphan per suite run, in every worktree.
    """
    assert poisoned.child is not None, f"the probe never forked (the child {poisoned.outcome})"
    with pytest.raises(ProcessLookupError):
        os.kill(poisoned.child, 0)
