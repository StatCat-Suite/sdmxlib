"""The object-store tier: a bundle read from Azurite, an S3 server and a GCS emulator (GH-180).

Part of `just ci`. A machine with no container runtime skips it and says so
on its own line, as the bundle tier does: a green gate that skipped these
read no remote store, and the summary must not let that look like a pass.
"""

from collections.abc import Iterator

import pytest

from tests.stores import _stores

#: The reason the tier skipped, set once per session.
_SKIPPED: list[str] = []


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    """Say, on its own line, that the object-store tier did not run."""
    if _SKIPPED:
        terminalreporter.write_sep("-", "object-store tier not run — the gate did not check it", yellow=True)
        terminalreporter.write_line(f"skipped: {_SKIPPED[0]}")
        terminalreporter.write_line(
            "These read a bundle's data files from Azurite, an S3 server and a GCS emulator. "
            "Without them `just ci` cannot see LocalRegistry.bundle(remote=...) break on a real store."
        )


@pytest.fixture(scope="session", params=_stores.EMULATORS, ids=lambda e: e.name)
def store(request: pytest.FixtureRequest) -> Iterator[_stores.Running]:
    """One emulator, running for the session, or skip the test."""
    missing = _stores.runtime_missing()
    if missing is not None:
        if not _SKIPPED:
            _SKIPPED.append(missing)
        pytest.skip(f"object-store tier: {missing}")
    running = _stores.start(request.param)
    yield running
    _stores.stop(running)
