"""Opening a store on another schema version never empties it unasked (GH-57).

Migration is drop and rebuild. sdmxlib 0.68 to 0.70 ran it on any store whose
``schema_version`` differed, so a bundle built by 0.67 lost every artefact the
moment a newer sdmxlib opened it, even to read, and the older sdmxlib that
built it then read it empty too. Only a read-only connection was refused, and
`LocalRegistry.bundle` opens read-write.

Each test stamps a real store with a version other than this build's, so the
guard decides on the same `_meta` row the old stores carry.
"""

from pathlib import Path

import duckdb
import pytest

import sdmxlib as sl
from sdmxlib.storage.schema import SCHEMA_VERSION, ReadOnlySchemaError, get_schema_version

pytestmark = pytest.mark.unit

_FREQ = sl.Codelist(
    id="CL_FREQ", maintainer="EX", version="1.0", name={"en": "Frequency"}, codes=[sl.Code(id="A", name={"en": "A"})]
)


def _store(path: Path, *, stamped: str, populated: bool = True) -> Path:
    """A store at `path` holding `_FREQ` (or nothing), with its schema version set to `stamped`."""
    with sl.LocalRegistry(path) as reg:
        if populated:
            reg.add(_FREQ)
    with duckdb.connect(str(path)) as conn:
        conn.execute("UPDATE sdmx._meta SET value = ? WHERE key = 'schema_version'", [stamped])
    return path


def _artefacts(path: Path) -> int:
    """Rows in `sdmx.artefact`, read with plain DuckDB, so no sdmxlib code path is involved."""
    with duckdb.connect(str(path), read_only=True) as conn:
        row = conn.execute("SELECT count(*) FROM sdmx.artefact").fetchone()
    assert row is not None
    return int(row[0])


@pytest.mark.parametrize("stamped", ["2.19", "9.99"], ids=["older", "newer"])
def test_a_populated_store_on_another_schema_is_refused_and_kept(tmp_path: Path, stamped: str) -> None:
    """Older is #57 itself. Newer is the same harm the other way: an older
    sdmxlib would have emptied a store a newer one wrote."""
    path = _store(tmp_path / "store.duckdb", stamped=stamped)
    with pytest.raises(sl.StoreSchemaError, match=rf"schema {stamped}.*schema {SCHEMA_VERSION}.*rebuild=True"):
        sl.LocalRegistry(path).open()
    assert _artefacts(path) == 1


def test_a_bundle_on_another_schema_is_refused_and_kept(tmp_path: Path) -> None:
    """The reported case, a bundle's store opened on another schema, here by a builder resuming it."""
    publication = tmp_path / "bundle" / "token"
    publication.mkdir(parents=True)
    _store(publication / "meta.duckdb", stamped="2.19")
    with pytest.raises(sl.StoreSchemaError):
        sl.LocalRegistry.build(publication.parent, resume=publication.name)
    assert _artefacts(publication / "meta.duckdb") == 1


def test_rebuild_asks_for_the_drop(tmp_path: Path) -> None:
    """A cache that can be fetched again opts in, and gets today's behaviour."""
    path = _store(tmp_path / "cache.duckdb", stamped="2.19")
    with sl.LocalRegistry(path, rebuild=True) as reg:
        assert len(reg) == 0
        assert get_schema_version(reg.connection) == SCHEMA_VERSION


def test_an_empty_store_on_another_schema_is_migrated_unasked(tmp_path: Path) -> None:
    """Nothing to lose, so nothing to refuse."""
    path = _store(tmp_path / "empty.duckdb", stamped="2.19", populated=False)
    with sl.LocalRegistry(path) as reg:
        assert get_schema_version(reg.connection) == SCHEMA_VERSION


def test_a_store_on_this_schema_opens_as_before(tmp_path: Path) -> None:
    path = _store(tmp_path / "current.duckdb", stamped=SCHEMA_VERSION)
    with sl.LocalRegistry(path) as reg:
        assert reg.get(_FREQ.urn) is not None


def test_a_read_only_connection_is_refused_the_same_way(tmp_path: Path) -> None:
    """Populated and read-only: the populated store is the reason, so the
    error says so, and catching `StoreSchemaError` catches both kinds."""
    path = _store(tmp_path / "store.duckdb", stamped="2.19")
    with duckdb.connect(str(path), read_only=True) as conn, pytest.raises(sl.StoreSchemaError, match="holds artefacts"):
        sl.LocalRegistry.from_connection(conn)
    assert issubclass(ReadOnlySchemaError, sl.StoreSchemaError)


def test_a_read_only_connection_cannot_rebuild(tmp_path: Path) -> None:
    path = _store(tmp_path / "store.duckdb", stamped="2.19")
    with duckdb.connect(str(path), read_only=True) as conn, pytest.raises(ReadOnlySchemaError, match="read-write"):
        sl.LocalRegistry.from_connection(conn, rebuild=True)
    assert _artefacts(path) == 1
