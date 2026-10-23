"""Benchmark-suite-wide pytest config.

Marks every test in this directory with ``bench`` so the suite is opt-in
via ``pytest -m bench``.

Also carries the shared fixtures for the **bundle tier** (#379) — the
benchmarks that read a real 2.6 GB SDMX bundle rather than a synthetic
fixture. Those are additionally marked ``bundle`` and deselected from
``just bench``, so ``just bench`` means the same thing on every machine and
``bench/baselines/`` stays comparable across them. ``just bench-bundle`` runs
this tier.
"""

import shutil

import pytest

import sdmxlib as sl
from sdmxlib.bundle import Producer, write_manifest
from tests.bench import _bundle


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        if "tests/bench" in str(item.fspath).replace("\\", "/"):
            item.add_marker(pytest.mark.bench)


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    """Say, on its own line, when the bundle tier did not run.

    A skip in pytest's tail is not a report. The hook this tier replaces
    reported a dead path as three lines of `SKIPPED [3] …` and stayed dead
    for months — CLAUDE.md's *a finding that cannot fail is a finding nobody
    acts on*, in its quietest form.

    **It says "the gate did not check this", not "some benchmarks skipped"**,
    because since #390 these assertions are part of `just ci`. A green gate
    that skipped them is a green gate that checked less than it looks like it
    did, and that is the one thing a skip here must not be able to hide.

    Only fires when bundle tests were actually collected, so a plain
    ``just bench`` (which deselects them) prints nothing.
    """
    reasons: dict[str, int] = {}
    for report in terminalreporter.stats.get("skipped", []):
        longrepr = getattr(report, "longrepr", None)
        # pytest gives a skip as `(path, lineno, "Skipped: <reason>")`. Read
        # the third element rather than splitting the tuple's repr, which
        # leaves the closing quote on the message.
        reason = longrepr[2] if isinstance(longrepr, tuple) and len(longrepr) == 3 else str(longrepr)
        if _bundle.ENV_VAR not in reason:
            continue
        # Counted rather than listed: a whole tier skipping for one reason
        # should print one line, not fifteen.
        reason = reason.removeprefix("Skipped: ")
        reasons[reason] = reasons.get(reason, 0) + 1
    if reasons:
        terminalreporter.write_sep("-", "bundle tier not run — the gate did not check it", yellow=True)
        for reason, count in reasons.items():
            terminalreporter.write_line(f"{count} test(s) skipped: {reason}")
        terminalreporter.write_line(
            "These guard the real-bundle costs (%18, %19). Without them `just ci` cannot "
            "see a regression in an eager read's per-item budget, a pruned sidecar join, "
            "or a declined key order."
        )


@pytest.fixture(scope="session")
def bundle():
    """The bundle directory, or skip the test.

    A *set but unresolvable* ``$SDMXLIB_BENCH_BUNDLE`` raises out of
    ``bundle_path`` rather than skipping here — see `_bundle`'s docstring for
    why those two cases must not read alike.
    """
    path = _bundle.bundle_path()
    if path is None:
        pytest.skip(_bundle.skip_reason())
    return path


@pytest.fixture(scope="session")
def bundle_conn(bundle):
    """A read-only DuckDB connection to the bundle's structural store.

    Session-scoped: opening it is cheap, but DuckDB's lock is exclusive, so
    one connection per test would serialise badly against itself.
    """
    conn = _bundle.connect(bundle)
    try:
        stale = _bundle.schema_skip_reason(conn)
        if stale:
            pytest.skip(stale)
        yield conn
    finally:
        conn.close()


@pytest.fixture(scope="session")
def shapes(bundle_conn):
    """Which artefacts in this bundle have the shapes the benchmarks need."""
    return _bundle.discover(bundle_conn)


@pytest.fixture(scope="session")
def data_plane_bundle(bundle, tmp_path_factory):
    """A **copy** of the bundle, so the observation tier can open it for write.

    `LocalRegistry.bundle()` opens `meta.duckdb` read-write, and DuckDB's file
    lock is exclusive — so pointing it at the real directory would lock the
    thing being measured, which is what `_bundle.connect()` chose read-only to
    avoid. Copying sidesteps that without duplicating `bundle()`'s ATTACH spec
    in the test suite, and it means this tier exercises the public constructor
    #387 fixed rather than a hand-rolled stand-in.

    Cheap where it matters: 2.6 GB clones in ~3 s on APFS, once per session.

    Skips — naming the missing file — when the bundle has no data plane, so a
    structural-only directory still runs every other benchmark.
    """
    missing = _bundle.data_plane_missing(bundle)
    if missing is not None:
        pytest.skip(_bundle.data_plane_skip_reason(bundle, missing))
    # Checked before the copy, and on the source rather than the clone: this
    # fixture opens its copy *read-write*, so a stale schema would be silently
    # dropped and rebuilt — 2.6 GB cloned and then emptied, surfacing three
    # fixtures later as "bundle binds no dataflow" (#145).
    stale = _bundle.schema_skip_reason_for(bundle)
    if stale:
        pytest.skip(stale)
    destination = tmp_path_factory.mktemp("bundle") / bundle.name
    shutil.copytree(bundle, destination)
    # Published on the copy (GH-66): sdmxlib owns `bundle.manifest.json`, and
    # a bundle built before it may carry its builder's own format, which
    # `bundle()` refuses. Republishing the copy runs `write_manifest` at full
    # scale — checksums over every file, counts from the real lake — and
    # leaves the source bundle untouched.
    write_manifest(destination, bundle_id=bundle.name, producer=Producer(name="sdmxlib-bench", version="0"))
    return destination


@pytest.fixture(scope="session")
def data_plane_registry(data_plane_bundle):
    """A registry over the copied bundle, artefacts and observations together."""
    registry = sl.LocalRegistry.bundle(data_plane_bundle)
    try:
        yield registry
    finally:
        registry.close()


@pytest.fixture(scope="session")
def obs_shapes(data_plane_registry):
    """Which dataflow the observation benchmarks read, and its shape."""
    return _bundle.discover_observations(data_plane_registry)
