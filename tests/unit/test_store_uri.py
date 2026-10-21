"""A store's constructor takes a path, and refuses a connection by name (#505).

`LocalRegistry(conn)` and `DuckDBDataStore(conn)` passed a `DuckDBPyConnection`
through to `duckdb.connect` as its own `repr`, which opened a new database
file named ``<_duckdb.DuckDBPyConnection object at 0x…>`` in the working
directory. Everything the caller added went there, and nothing said so; the
local registry tutorial used the form, and its doc test passed.
"""

from collections.abc import Callable
from pathlib import Path

import duckdb
import pytest

import sdmxlib as sl

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("build", "fix"),
    [
        pytest.param(sl.LocalRegistry, "LocalRegistry.from_connection(conn)", id="registry"),
        pytest.param(sl.DuckDBDataStore, "from_registry(sl.LocalRegistry.from_connection(conn))", id="data-store"),
    ],
)
def test_a_connection_is_refused_naming_the_way_to_use_it(
    build: Callable[[object], object], fix: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(TypeError, match="not a DuckDB connection") as caught:
        build(duckdb.connect())
    assert fix in str(caught.value)
    assert list(tmp_path.iterdir()) == [], "no stray database file"


@pytest.mark.parametrize("build", [sl.LocalRegistry, sl.DuckDBDataStore], ids=["registry", "data-store"])
def test_anything_else_that_is_not_a_path_is_refused(build: Callable[[object], object]) -> None:
    with pytest.raises(TypeError, match=r"takes a path \(str or Path\), got int"):
        build(42)


@pytest.mark.parametrize("spell", [str, Path], ids=["str", "Path"])
def test_a_path_is_taken_either_way(spell: Callable[[str], str | Path], tmp_path: Path) -> None:
    """The discrimination check: what the refusal must not catch."""
    with sl.LocalRegistry(spell(str(tmp_path / "store.duckdb"))) as registry:
        assert registry.uri == tmp_path / "store.duckdb"
    assert (tmp_path / "store.duckdb").exists()
