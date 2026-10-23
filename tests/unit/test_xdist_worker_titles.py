"""xdist's per-test worker titles stay off on macOS (`tests/conftest.py`).

On Darwin each title update round-trips through LaunchServices, and the unit
tier kept `launchservicesd` at ~1.5 cores. The conftest replaces `setproctitle`
in the namespace the worker actually runs, which is execnet's
`__channelexec__`, not the importable `xdist.remote`. A first version patched
the importable module, passed a test that read that module back, and changed
nothing; so these tests read the running worker's namespace.

They run only in an xdist worker on Darwin, which is how `just test` runs them.
"""

import sys

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(sys.platform != "darwin", reason="the titles are only turned off on Darwin"),
]


def _worker_namespace(config: pytest.Config) -> dict[str, object]:
    """The globals of the code this xdist worker is running."""
    if not hasattr(config, "workerinput"):
        pytest.skip("not in an xdist worker")
    (interactor,) = [p for p in config.pluginmanager.get_plugins() if type(p).__name__ == "WorkerInteractor"]
    namespace: dict[str, object] = type(interactor).__init__.__globals__
    return namespace


def test_the_running_worker_titles_with_the_conftest_no_op(request: pytest.FixtureRequest) -> None:
    namespace = _worker_namespace(request.config)
    assert namespace["__name__"] == "__channelexec__", "xdist no longer runs its worker as execnet channel code"
    title = namespace["setproctitle"]
    assert (getattr(title, "__module__", None), getattr(title, "__qualname__", None)) == ("tests.conftest", "_no_title")


def test_the_running_worker_renames_itself_through_that_name(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If xdist stops calling `setproctitle` by that global name, the no-op no longer reaches it."""
    namespace = _worker_namespace(request.config)
    seen: list[str] = []
    monkeypatch.setitem(namespace, "setproctitle", seen.append)
    worker_title = namespace["worker_title"]
    assert callable(worker_title)
    worker_title("[pytest-xdist running] a test")
    assert seen == ["[pytest-xdist running] a test"]
