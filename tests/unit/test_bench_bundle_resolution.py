"""How the bundle benchmarks find a bundle — and refuse to skip quietly (#379).

These are unit tests, not benchmarks, and they run in `just ci` on purpose.
The tier they guard cannot run here (it needs a 2.0 GB published bundle
nobody has in CI), but its *resolution* is exactly the part that failed silently before:
`test_data_writers._census_geo_path` fell back to a workspace that no longer
existed and reported it as `SKIPPED [3]` for months.

So the rule "set but unresolvable is an error, unset is a skip" is asserted
where it will actually be exercised. Without this file the tier's own skip
logic would only ever be checked by a machine that has the bundle — that is,
never on the path where it matters.
"""

import pytest

from tests.bench import _bundle

pytestmark = pytest.mark.unit


def test_a_declared_bundle_that_is_not_a_directory_raises(monkeypatch, tmp_path):
    """Setting the variable is a claim to have the bundle; a bad path is a lie.

    This is the exact failure the tier exists to prevent, so it raises rather
    than skipping — see the module docstring.
    """
    monkeypatch.setenv(_bundle.ENV_VAR, str(tmp_path / "nope"))
    with pytest.raises(_bundle.BundlePathError, match="not a directory"):
        _bundle.bundle_path()


def test_a_declared_directory_without_the_store_raises(monkeypatch, tmp_path):
    """A directory that is not a bundle fails differently from one that is absent.

    Two distinct messages, because "wrong path" and "no bundle" were
    indistinguishable in the hook this replaces, and that is why nobody noticed.
    """
    monkeypatch.setenv(_bundle.ENV_VAR, str(tmp_path))
    with pytest.raises(_bundle.BundlePathError, match=f"no {_bundle.REQUIRED}"):
        _bundle.bundle_path()


def test_a_declared_bundle_resolves(monkeypatch, tmp_path):
    (tmp_path / _bundle.REQUIRED).touch()
    monkeypatch.setenv(_bundle.ENV_VAR, str(tmp_path))
    assert _bundle.bundle_path() == tmp_path


def test_no_variable_is_a_skip_not_an_error(monkeypatch, tmp_path):
    """Not having the bundle is the ordinary case and must never fail a run."""
    monkeypatch.delenv(_bundle.ENV_VAR, raising=False)
    monkeypatch.chdir(tmp_path)
    assert _bundle.bundle_path() is None


def test_there_is_no_default_location(monkeypatch, tmp_path):
    """Only the variable names a bundle — a bundle beside the checkout is not found.

    A baked-in fallback names one machine's layout, and the one this replaced
    was a stale path that skipped for months. So a directory that *would* have
    matched a sibling default — a real bundle, one level up — must not resolve.
    The `.env` route (`set dotenv-load` in the justfile) is how a machine with
    the bundle sets the variable without exporting it.
    """
    monkeypatch.delenv(_bundle.ENV_VAR, raising=False)
    checkout = tmp_path / "sdmxlib"
    checkout.mkdir()
    sibling = tmp_path / "bundle"
    sibling.mkdir()
    (sibling / _bundle.REQUIRED).touch()
    monkeypatch.chdir(checkout)
    assert _bundle.bundle_path() is None
    assert not hasattr(_bundle, "SIBLING"), "a default location crept back in"


def test_the_skip_reason_names_the_variable_and_how_to_set_it():
    """A skip line nobody can act on is the failure mode, so say the one route and where to put it."""
    reason = _bundle.skip_reason()
    assert f"${_bundle.ENV_VAR} is unset" in reason
    assert ".env" in reason
    assert _bundle.REQUIRED in reason


def test_counting_restores_the_original_constructor():
    """`counting` patches a model class; a leak would corrupt every later test."""

    class Thing:
        def __init__(self, x: int) -> None:
            self.x = x

    original = Thing.__init__
    with _bundle.counting(Thing) as built:
        Thing(1)
        Thing(2)
    assert built[0] == 2
    assert Thing.__init__ is original


def test_counting_restores_on_exception():
    class Thing:
        def __init__(self) -> None: ...

    original = Thing.__init__
    with pytest.raises(RuntimeError), _bundle.counting(Thing):
        raise RuntimeError
    assert Thing.__init__ is original


def test_a_bundle_is_a_publication_whose_lake_is_optional():
    """The tier reads a published bundle, and its structural half reads no observations.

    The manifest is what makes a directory a published bundle; the store is
    what the structural benchmarks read. Requiring `lake.ducklake` of every
    bundle would refuse a structural-only one for benchmarks that never touch
    the data plane, so only the observation tier asks for it.
    """
    assert _bundle.REQUIRED == "bundle.manifest.json"
    assert _bundle.STORE == "meta.duckdb"
    assert "lake.ducklake" in _bundle.DATA_PLANE_FILES
    assert "lake.ducklake" not in (_bundle.REQUIRED, _bundle.STORE)
