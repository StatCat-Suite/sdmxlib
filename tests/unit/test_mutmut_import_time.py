"""Mutants of code that runs at import are tried against the tests that can see them (#458).

mutmut credits a function to the tests that were running when it was hit. A
factory called while a module imports — `formats/gaps.py` builds its 289-row
table from them — is hit during collection, before any test, so stock mutmut
credits it to whichever test runs first. Every mutant of `_any_node_has` was
then run against one test about annotation URLs, and all 28 survived, while a
fresh run of `test_field_coverage` kills the first of them outright.

`tools/mutmut_forksafe.py` takes collection-time hits out before that first
test and credits each to every test reaching its module at run time. mutmut
3.8 still credits the old way.
"""

import importlib.util
import sys
from pathlib import Path

import pytest
from mutmut.configuration import ForkServerWarmup, ProcessIsolation
from mutmut.runners.harness import BadTestExecutionCommandsException
from mutmut.state import state
from mutmut.workers.isolation import ForkServerRunner

from tests.support.narrow import present
from tests.support.repo import ROOT

pytestmark = pytest.mark.unit

_TOOL = ROOT / "tools" / "mutmut_forksafe.py"
_spec = importlib.util.spec_from_file_location("mutmut_forksafe", _TOOL)
assert _spec is not None
assert _spec.loader is not None
forksafe = importlib.util.module_from_spec(_spec)
sys.modules["mutmut_forksafe"] = forksafe
_spec.loader.exec_module(forksafe)

_GAPS = "sdmxlib.formats.gaps"
_FACTORY = f"{_GAPS}.x__any_node_has"


def test_an_import_time_function_is_credited_with_every_test_reaching_its_module() -> None:
    tests = {
        f"{_GAPS}.x_unrepresentable": {"t_field_coverage", "t_output_gaps"},
        f"{_GAPS}.x__reach": {"t_reach"},
        _FACTORY: set(),
    }
    forksafe.credit_import_time(tests, {_FACTORY})
    assert tests[_FACTORY] == {"t_field_coverage", "t_output_gaps", "t_reach"}


def test_another_module_s_tests_are_not_credited() -> None:
    tests = {
        f"{_GAPS}.x_unrepresentable": {"t_gaps"},
        "sdmxlib.formats.gaps_extra.x_other": {"t_other"},
        "sdmxlib.formats.x_shallower": {"t_shallower"},
        _FACTORY: set(),
    }
    forksafe.credit_import_time(tests, {_FACTORY})
    assert tests[_FACTORY] == {"t_gaps"}


def test_a_function_hit_at_import_and_at_run_time_keeps_its_own_tests() -> None:
    tests = {f"{_GAPS}.x_unrepresentable": {"t_gaps"}, _FACTORY: {"t_direct"}}
    forksafe.credit_import_time(tests, {_FACTORY})
    assert tests[_FACTORY] == {"t_gaps", "t_direct"}


def test_a_module_no_test_reaches_at_run_time_leaves_it_uncredited() -> None:
    """Reported by mutmut as having no test — which is what is true of it."""
    tests: dict[str, set[str]] = {"sdmxlib.model.codelist.x_validate": {"t_codelist"}}
    forksafe.credit_import_time(tests, {_FACTORY})
    assert tests[_FACTORY] == set()


def test_the_probe_takes_collection_time_hits_away_from_the_first_test() -> None:
    probe = forksafe._ImportTimeHits()
    stats = state()._stats
    saved = set(stats)
    stats.clear()
    stats.add(_FACTORY)
    try:
        probe.pytest_collection_finish()
        assert probe.functions == {_FACTORY}
        assert stats == set(), "left in place, the hit is credited to whichever test runs first"
    finally:
        stats.clear()
        stats.update(saved)


def _record_plugins(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    seen: list[object] = []

    def fake_main(_args: list[str], plugins: list[object]) -> int:
        seen.extend(plugins)
        return 0

    monkeypatch.setattr(pytest, "main", fake_main)
    return seen


def test_the_stats_run_carries_the_probe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Without it the credit above has nothing to work from and changes nothing."""
    seen = _record_plugins(monkeypatch)
    runner = forksafe.CreditingRunner()
    monkeypatch.chdir(tmp_path)
    (tmp_path / "mutants").mkdir()  # mutmut runs the suite from inside it
    assert runner.run_stats(tests=["tests/unit/test_x.py"]) == 0
    assert [type(plugin).__name__ for plugin in seen] == ["_ImportTimeHits", "StatsCollector"]


def test_only_the_stats_run_carries_the_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """A mutant's own run credits nothing, so a probe there would only cost."""
    seen = _record_plugins(monkeypatch)
    assert forksafe.CreditingRunner().execute_pytest(["-q"], plugins=["mutmut's own"]) == 0
    assert seen == ["mutmut's own"]


def test_the_stats_run_credits_what_collection_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    """End to end through `run_stats`: hit at collection, credited to the module's tests."""
    runner = forksafe.CreditingRunner()
    tests = state().tests_by_mangled_function_name
    saved = {k: set(v) for k, v in tests.items()}

    def fake_stats(self: object, *, tests: object) -> int:
        del self, tests
        probe = present(runner._probe)
        state()._stats.add(_FACTORY)
        probe.pytest_collection_finish()
        state().tests_by_mangled_function_name[f"{_GAPS}.x_unrepresentable"].add("t_gaps")
        return 0

    monkeypatch.setattr(forksafe.PytestRunner, "run_stats", fake_stats)
    try:
        assert runner.run_stats(tests=[]) == 0
        assert tests[_FACTORY] == {"t_gaps"}
    finally:
        tests.clear()
        tests.update(saved)


def test_fork_mode_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fork mode runs the suite in the forking parent, which is #314."""
    monkeypatch.setattr(forksafe, "config", lambda: _Isolation(ProcessIsolation.FORK))
    with pytest.raises(SystemExit, match="forkserver"):
        forksafe.mutant_runner(4)


@pytest.mark.parametrize("warmup", [ForkServerWarmup.COLLECT, ForkServerWarmup.IMPORT])
def test_a_fork_server_warm_up_is_refused(monkeypatch: pytest.MonkeyPatch, warmup: ForkServerWarmup) -> None:
    """One imports the code before a mutant is set: import-time mutants never run (#458)."""
    monkeypatch.setattr(forksafe, "config", lambda: _Isolation(ProcessIsolation.FORKSERVER, warmup))
    with pytest.raises(SystemExit, match="forkserver_warmup"):
        forksafe.mutant_runner(4)


def test_fork_server_mode_runs_the_crediting_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(forksafe, "config", lambda: _Isolation(ProcessIsolation.FORKSERVER))
    runner = forksafe.mutant_runner(4)
    assert isinstance(runner, ForkServerRunner)
    assert (runner.test_runner_class, runner.max_workers) == (forksafe.CreditingRunner, 4)


class _Isolation:
    def __init__(self, isolation: ProcessIsolation, warmup: ForkServerWarmup = ForkServerWarmup.NONE) -> None:
        self.process_isolation = isolation
        self.forkserver_warmup = warmup
        self.debug = False
        self.max_forkserver_restarts = 3


def _broken_import(_self: object, *, mutant_name: str | None, tests: object) -> int:
    del mutant_name, tests
    raise BadTestExecutionCommandsException(["-q"])


def test_a_mutant_that_breaks_collection_is_killed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fork server books any exception as exit 255, a timeout; it is a kill (#458)."""
    monkeypatch.setattr(forksafe.PytestRunner, "run_tests", _broken_import)
    assert forksafe.CreditingRunner().run_tests(mutant_name="m.x_f__mutmut_1", tests=[]) == 1


def test_a_clean_run_that_cannot_collect_still_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a mutant, exit 4 is a broken command line, not a kill."""
    monkeypatch.setattr(forksafe.PytestRunner, "run_tests", _broken_import)
    with pytest.raises(BadTestExecutionCommandsException):
        forksafe.CreditingRunner().run_tests(mutant_name=None, tests=[])
