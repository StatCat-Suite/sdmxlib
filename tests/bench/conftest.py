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

import duckdb
import pytest

import sdmxlib as sl
from sdmxlib.bundle import Producer, write_manifest
from tests.bench import _bundle

#: What the bundle fixtures did that the summary should say: a 6.5-minute
#: republish is worth a line, and so is a run that reused one.
_NOTES: list[str] = []


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
    for note in _NOTES:
        terminalreporter.write_line(note)
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
    """A **copy** of the bundle, republished as a publication, which `LocalRegistry.bundle()` opens read-only.

    The copy is what `write_manifest` writes into: sdmxlib owns
    `bundle.manifest.json`, and the real bundle may carry its builder's older
    one, which `bundle()` refuses. Republishing a copy leaves the source
    untouched. Opening it through `bundle()` means this tier exercises the
    public constructor #387 fixed rather than a hand-rolled stand-in.

    The real bundle keeps the single-directory layout (``meta.duckdb``,
    ``lake.ducklake``, ``parquet/``); the copy is laid out as a publication
    (GH-123): `LocalRegistry.build` mints the token and the directory, the
    real store and catalog replace the empty ones, the token is stamped into
    the copied store as `build` stamps it, and ``parquet/`` becomes the
    bundle's ``data/``. Returns the publication directory.

    **Cached across sessions** (GH-127). Publishing computes every fingerprint,
    about 6.5 minutes on the census bundle, so the published copy is kept under
    ``$SDMXLIB_BENCH_CACHE`` (``~/.cache/sdmxlib/bench`` by default), keyed on
    the source bundle and on everything publishing depends on: the store
    schema, the manifest format, the canonical form, DuckDB's version and the
    source of the publishing modules. A change to any of them republishes, and
    the summary says which happened.

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
    key = _bundle.publication_key(bundle)
    cached = _bundle.cached_publication(key)
    if cached is not None:
        _NOTES.append(f"bundle tier: reused the census publication cached under {cached.parent.parent}")
        return cached
    _NOTES.append(f"bundle tier: republished the census bundle with fingerprints (key {key[:12]})")
    return _bundle.cache_publication(key, lambda root: _republish(bundle, root))


def _republish(bundle, root):
    """`bundle`, in the single-directory layout, as a publication under `root`, published."""
    with sl.LocalRegistry.build(root) as started:
        publication = started.publication
    for name in ("meta.duckdb", "lake.ducklake"):
        shutil.copyfile(bundle / name, publication / name)
    shutil.copytree(bundle / "parquet", root / "data", dirs_exist_ok=True)
    with duckdb.connect(str(publication / "meta.duckdb")) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO sdmx._meta (key, value) VALUES ('publication_token', ?)", [publication.name]
        )
    # Published on the copy (GH-66): sdmxlib owns `bundle.manifest.json`, and
    # a bundle built before it may carry its builder's own format, which
    # `bundle()` refuses. Republishing the copy runs `write_manifest` at full
    # scale — checksums over every file, counts from the real lake, and every
    # fingerprint (about 6.5 minutes) — and leaves the source bundle untouched.
    write_manifest(publication, bundle_id=bundle.name, producer=Producer(name="sdmxlib-bench", version="0"))
    return publication


@pytest.fixture(scope="session")
def data_plane_registry(data_plane_bundle):
    """A registry over the copied bundle, artefacts and observations together.

    In an engine of `_bundle.threads()` threads, so the tier leaves the
    machine usable.
    """
    engine = sl.BundleEngine(threads=_bundle.threads())
    registry = sl.LocalRegistry.bundle(data_plane_bundle, engine=engine)
    try:
        yield registry
    finally:
        registry.close()
        engine.close()


@pytest.fixture(scope="session")
def obs_shapes(data_plane_registry):
    """Which dataflow the observation benchmarks read, and its shape."""
    return _bundle.discover_observations(data_plane_registry)
