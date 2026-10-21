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
part of. A timeout is therefore a result, not a stall: `_forks_cleanly`
reports it as "poisoned" and each case says which it expects.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

pytestmark = pytest.mark.unit

#: A clean child finishes in well under a second, so this is generous. The
#: poisoning case necessarily waits the whole timeout — that is the cost of
#: asserting a hang — so it is kept just long enough to be unambiguous.
_TIMEOUT_S = 10


def _forks_cleanly(parent_does: str) -> bool:
    """Run `parent_does`, then fork a child that uses polars. Did it survive?

    Returns:
        True if the child exited 0. False if it hung or died — the two failure
        modes are not distinguished, because the tool's fix has to avoid both.
    """
    # Dedented *before* substitution: `parent_does` is itself multi-line, and
    # dedenting afterwards finds no common prefix and leaves the first line
    # indented on its own — an IndentationError in the child rather than the
    # measurement asked for.
    template = textwrap.dedent("""
        import os, sys
        import polars as pl

        PARENT_DOES

        sys.stdout.flush()
        pid = os.fork()
        if pid == 0:
            pl.Series("id", ["A", "B"], dtype=pl.Utf8).str.contains("^[A-Z]+$")
            os._exit(0)
        _, status = os.waitpid(pid, 0)
        sys.exit(os.waitstatus_to_exitcode(status))
    """)
    program = template.replace("PARENT_DOES", parent_does)
    try:
        completed = subprocess.run(  # noqa: S603 — same fixed `sys.executable` shape as the three existing sites
            [sys.executable, "-c", program],
            timeout=_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False
    return completed.returncode == 0


def test_importing_polars_leaves_the_fork_clean() -> None:
    """Fact (2): the imports may stay in the parent.

    Without this the tool could not preload the native extensions, and a child
    that dlopens pyarrow after mutmut's mutant-generation `Pool` aborts.
    """
    assert _forks_cleanly("pass"), "importing polars now poisons fork() — tools/mutmut_forksafe.py cannot preload"


def test_one_polars_operation_poisons_the_fork() -> None:
    """Fact (1): and *one row* is enough.

    #314 recorded this as needing "real work first", which sent the diagnosis
    towards `Select.collect()`. It does not: two elements and a regex do it, and
    that is why the real source turned out to be `validate()` on the write
    path rather than anything in the query path.

    If this ever passes, polars has become fork-safe and the tool is dead code.
    """
    poisoned = not _forks_cleanly('pl.Series("id", ["A"], dtype=pl.Utf8).str.contains("^[A-Z]+$")')
    assert poisoned, (
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
    assert _forks_cleanly(delegate), (
        "delegating polars work to a child no longer leaves the parent forkable — "
        "tools/mutmut_forksafe.py's approach is invalid"
    )
