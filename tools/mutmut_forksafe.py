"""Run mutmut 3.8+ in fork-server mode, crediting import-time hits (#314, #458).

## Fork safety (#314) is mutmut's now

`mutmut run` forks one child per mutant. Before 3.8 it also ran the suite
**in the parent, in-process**, three times: the stats pass, the clean run and
the forced-fail check. Any of those touching polars left the parent
unforkable. polars' rayon pool comes across a `fork()` without its workers,
and every later child hung at 0% CPU until `RLIMIT_CPU` killed it. mutmut
booked that as a *timeout*, which is what #303's 205 "timeouts" in 706
verdicts were.

The fix was to run those phases in a forked child, and until mutmut 3.8 this
file did it by patching mutmut's runner. 3.8 does the same itself:

* `process_isolation = "forkserver"` in `[tool.mutmut]` keeps pytest out of
  the parent entirely;
* each phase runs in a child of its own;
* a fork server imports and collects the suite once, then forks a grandchild
  per mutant.

Collection imports the test modules without running them, and that was
measured not to poison the fork. `tests/unit/test_mutmut_forksafe.py` keeps
the polars facts this rests on.

## What is left here

**Import-time credit (#458).** mutmut credits a function to the tests that ran
since the last teardown. A hit during collection, such as a factory building a
module-level table (`formats/gaps.py`'s 289 `carried` predicates), therefore
goes to whichever test runs first, and its mutants are tried against that one
test only. `CreditingRunner` takes those hits out when collection finishes and
credits each to every test that reaches its module (`credit_import_time`).
3.8 still does this the old way.

**Preloading the native extensions**, which `_preload` explains.

## Usage

Drop-in for `mutmut`, same arguments:

    uv run python tools/mutmut_forksafe.py run --max-children 8
    uv run python tools/mutmut_forksafe.py results
"""

from typing import TYPE_CHECKING, override

from mutmut import __main__ as mutmut_main
from mutmut.configuration import ForkServerWarmup, ProcessIsolation, config
from mutmut.runners.harness import BadTestExecutionCommandsException, PytestRunner
from mutmut.state import state
from mutmut.workers.isolation import ForkServerRunner, MutantRunner

if TYPE_CHECKING:
    from collections.abc import Iterable


class _ImportTimeHits:
    """A pytest plugin taking the functions hit while the test modules import.

    mutmut credits a function to a test by what was hit *since the last
    teardown*, so a hit during collection is credited to whichever test
    happens to run first. Taken out here, before the first test starts, they
    are credited by `credit_import_time` instead.
    """

    def __init__(self) -> None:
        self.functions: set[str] = set()

    def pytest_collection_finish(self) -> None:
        self.functions |= state()._stats
        state()._stats.clear()


class CreditingRunner(PytestRunner):
    """mutmut's pytest runner, crediting import-time hits on the stats pass.

    The stats pass runs in a forked child, which reports
    `tests_by_mangled_function_name` back to the parent after `run_stats`
    returns. The credit lands before that, so it travels back with the rest.
    """

    def __init__(self) -> None:
        super().__init__()
        self._probe: _ImportTimeHits | None = None

    @override
    def run_stats(self, *, tests: Iterable[str]) -> int:
        probe = self._probe = _ImportTimeHits()
        try:
            code = super().run_stats(tests=tests)
        finally:
            self._probe = None
        if code == 0:
            credit_import_time(state().tests_by_mangled_function_name, probe.functions)
        return code

    @override
    def run_tests(self, *, mutant_name: str | None, tests: Iterable[str]) -> int:
        """A mutant's run, booking a collection that cannot start as the kill it is.

        A mutant that breaks code run at import fails collection, and pytest
        exits 4, which mutmut raises as `BadTestExecutionCommandsException`.
        The fork server turns any exception into exit 255 and books that as a
        timeout (#458). Fork mode booked it as a kill, and a kill is what it
        is. Outside a mutant, the clean and forced-fail runs, exit 4 still
        means a broken command line, and it is left to raise.
        """
        try:
            return super().run_tests(mutant_name=mutant_name, tests=tests)
        except BadTestExecutionCommandsException:
            if not mutant_name:
                raise
            return 1

    @override
    def execute_pytest(self, params: list[str], **kwargs: object) -> int:
        if self._probe is not None:
            plugins = kwargs.pop("plugins", None)
            kwargs["plugins"] = [self._probe, *(plugins if isinstance(plugins, list) else [])]
        return super().execute_pytest(params, **kwargs)


def credit_import_time(tests_by_function: dict[str, set[str]], at_import: set[str]) -> None:
    """Credit each import-time function with every test that reaches its module.

    Code that runs while a module imports can change behaviour only through
    what that module does at run time, so the tests able to see a mutant of it
    are the ones that call into the module. A module no test calls into at run
    time leaves its import-time functions uncredited — reported as having no
    test, which is what is true of them.

    A module first imported *during* a test, by a deferred import, is not
    detected: its import-time hits still go to that test. Every test module
    here imports what it exercises at the top, so collection sees nearly all
    of them.

    Args:
        tests_by_function: mutmut's map from mangled function name to the
            tests that hit it, updated in place.
        at_import: the mangled names hit during collection.
    """
    for function in at_import:
        module = function.rpartition(".")[0]
        reached = {
            test for other, tests in tests_by_function.items() if other.rpartition(".")[0] == module for test in tests
        }
        tests_by_function[function] = set(tests_by_function.get(function, set())) | reached


def mutant_runner(max_workers: int = 1) -> MutantRunner:
    """mutmut's fork-server runner, with `CreditingRunner` as its test runner.

    Fork mode is refused rather than served: it runs the suite in the
    forking parent, which is #314 again. So is any fork-server warm-up: one
    imports the code under test before a mutant is chosen, and every mutant
    of code that runs at import is then never active (#458).

    Args:
        max_workers: Mutants tested at once.

    Returns:
        The runner `mutmut run` drives.

    Raises:
        SystemExit: `[tool.mutmut]` does not set `process_isolation = "forkserver"`
            and `forkserver_warmup = "none"`.
    """
    if config().process_isolation is not ProcessIsolation.FORKSERVER:
        msg = 'set `process_isolation = "forkserver"` in [tool.mutmut]: fork mode is #314'
        raise SystemExit(msg)
    if config().forkserver_warmup is not ForkServerWarmup.NONE:
        msg = 'set `forkserver_warmup = "none"` in [tool.mutmut]: a warm-up imports the code before the mutant is set'
        raise SystemExit(msg)
    return ForkServerRunner(
        max_workers=max_workers,
        test_runner_class=CreditingRunner,
        test_runner_args={},
        debug=config().debug,
        max_restarts=config().max_forkserver_restarts,
    )


def _preload() -> None:
    """Import the native extensions before anything forks.

    Not incidental — without it the fix trades a hang for a crash. mutmut
    generates its mutants through a `multiprocessing.Pool`, and after that pool
    has run, a forked child that *dlopens* pyarrow dies: `Fatal Python error:
    Aborted`, inside `pyarrow/__init__.py`'s extension import.

    mutmut 3.8's fork-server mode meets it too: it is what keeps the parent
    bare. Measured without this, the stats child exits -6 (`SIGABRT`) before
    running a test. mutmut's own `forkserver_warmup` does not help, because
    the fork server is itself forked after the pool.

    Importing is safe in a way *executing* is not: `import polars` leaves the
    fork clean, and one `pl.Series(...).str.contains(...)` does not. That
    asymmetry is what lets the parent hold the imports.

    **Third-party extensions only — never `sdmxlib`.** Importing the library
    here puts the real modules in `sys.modules`, and every child then inherits
    them and tests *unmutated* code from `src/` instead of the copy under
    `mutants/`. It fails loudly, which is the only reason it was noticed:
    mutmut's forced-fail check could no longer force a failure. Silently, it
    would have reported a perfect mutation score measuring nothing.
    """
    import duckdb  # noqa: F401  — dlopened by the child otherwise, see above
    import polars  # noqa: F401
    import pyarrow  # noqa: F401


if __name__ == "__main__":
    _preload()
    mutmut_main.get_mutant_runner = mutant_runner
    mutmut_main.cli()
