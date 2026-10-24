"""Many bundles in one DuckDB engine (GH-133).

Each publication attaches under a prefix of its own, its handles read its own
``sdmx`` catalog, and its binding's ``lake`` role reads its own lake, so a
query on one bundle answers from that bundle and no other. Each bundle here
holds a different number of rows, so a query answered from another bundle is
caught by its count.
"""

import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import duckdb
import pytest

import sdmxlib as sl
from sdmxlib.local.engine import BundleEngine
from tests.support.bundles import FLOW, make_bundle, publish

pytestmark = pytest.mark.unit

_MANY = 200


def _published(root: Path, count: int) -> list[Path]:
    """`count` published bundles under `root`, the i-th with i + 1 observations."""
    with ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(lambda i: publish(make_bundle(root / f"b{i}", i + 1)), range(count)))


@pytest.fixture(scope="module")
def few(tmp_path_factory: pytest.TempPathFactory) -> list[Path]:
    """Six published bundles, the i-th with i + 1 observations."""
    return _published(tmp_path_factory.mktemp("few"), 6)


def test_two_hundred_bundles_in_one_engine_each_answer_from_their_own(tmp_path: Path) -> None:
    many = _published(tmp_path, _MANY)
    engine = BundleEngine()
    registries = [sl.LocalRegistry.bundle(path, engine=engine) for path in many]
    try:
        assert len(engine.attached) == _MANY
        assert [r.obs(FLOW.urn).count() for r in registries] == [i + 1 for i in range(_MANY)]
        assert [r.execute("SELECT count(*) FROM sdmx.artefact")[0][0] for r in registries] == [4] * _MANY
    finally:
        for registry in registries:
            registry.close()
    assert engine.attached == ()
    engine.close()


def test_concurrent_reads_on_two_bundles_stay_apart(few: list[Path]) -> None:
    """Each thread reads through its own handles, each pointed at its own bundle."""
    engine = BundleEngine()
    small, large = (sl.LocalRegistry.bundle(few[i], engine=engine) for i in (0, 5))
    wrong: list[str] = []

    def read(registry: sl.BundleRegistry, expected: int) -> None:
        for _ in range(25):
            count = registry.obs(FLOW.urn).count()
            if count != expected:
                wrong.append(f"{count} != {expected}")

    threads = [threading.Thread(target=read, args=(r, n)) for r, n in ((small, 1), (large, 6)) * 4]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    small.close()
    large.close()
    engine.close()
    assert wrong == []


def test_one_publication_opened_twice_shares_its_attachment(few: list[Path]) -> None:
    engine = BundleEngine()
    first = sl.LocalRegistry.bundle(few[3], engine=engine)
    second = sl.LocalRegistry.bundle(few[3], engine=engine)
    assert len(engine.attached) == 1
    assert first.obs(FLOW.urn).count() == second.obs(FLOW.urn).count() == 4
    second.close()
    assert len(engine.attached) == 1, "the first still holds it"
    assert first.obs(FLOW.urn).count() == 4
    first.close()
    assert engine.attached == ()
    engine.close()


def test_the_stored_binding_keeps_its_role_and_reads_through_the_bundles_lake(few: list[Path]) -> None:
    engine = BundleEngine()
    with sl.LocalRegistry.bundle(few[0], engine=engine) as registry:
        (stored,) = registry.get_all(sl.DataflowBinding)
        located = registry.binding_for(FLOW.urn)
        assert stored.database_role == "lake"
        assert located is not None
        assert located.database_role == registry.lake_alias == f"{engine.attached[0]}__lake"
        assert registry.locate(stored) == located
        (entry,) = registry.browse()
        assert entry.binding == located, "browse names the bundle's lake too"
    engine.close()


def test_a_bundle_without_an_engine_gets_one_of_its_own(few: list[Path]) -> None:
    registry = sl.LocalRegistry.bundle(few[1])
    own = registry._engine
    assert own is not None
    assert len(own.attached) == 1
    assert registry.obs(FLOW.urn).count() == 2
    registry.close()
    with pytest.raises(duckdb.ConnectionException):
        own.connection.execute("SELECT 1")


def test_the_engine_carries_its_limits() -> None:
    engine = BundleEngine(memory_limit="1GB", threads=2)
    settings = "SELECT current_setting('memory_limit'), current_setting('threads')"
    assert engine.connection.execute(settings).fetchone() == ("953.6 MiB", 2), "1GB as DuckDB reports it"
    engine.close()


def test_a_memory_limit_that_is_not_a_size_is_refused() -> None:
    with pytest.raises(ValueError, match="is not a size"):
        BundleEngine(memory_limit="lots")


def test_a_thread_pool_below_one_is_refused() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        BundleEngine(threads=0)


def test_another_publication_under_a_taken_prefix_is_refused(tmp_path: Path, few: list[Path]) -> None:
    """A copy of a publication elsewhere has the same bundle id and token, so the same prefix."""
    copy = tmp_path / few[5].parent.name / few[5].name
    copy.parent.mkdir()
    shutil.copytree(few[5], copy)
    engine = BundleEngine()
    with sl.LocalRegistry.bundle(few[5], engine=engine), pytest.raises(ValueError, match="is attached from"):
        sl.LocalRegistry.bundle(copy, engine=engine)
    engine.close()


def test_two_publications_of_one_bundle_answer_apart(tmp_path: Path) -> None:
    """A release serves the old publication and the new one side by side: one
    bundle id, two tokens, so the token is what keeps their catalogs apart."""
    first = publish(make_bundle(tmp_path / "b", 10))
    with sl.LocalRegistry.build(first.parent, previous=first.name) as builder:
        builder._data_plane()
        builder.connection.execute("INSERT INTO lake.d.obs SELECT 'G9', 1.0 FROM range(5)")
        second = builder.publication
    publish(second)
    engine = BundleEngine()
    with sl.LocalRegistry.bundle(first, engine=engine) as old, sl.LocalRegistry.bundle(second, engine=engine) as new:
        assert len(engine.attached) == 2
        assert (old.obs(FLOW.urn).count(), new.obs(FLOW.urn).count()) == (10, 15)
    engine.close()


def test_every_handle_a_query_derives_reads_its_own_bundle(few: list[Path]) -> None:
    """A query takes a handle off its store's read cursor, and a stream takes one
    more off that. Each runs `USE` for its bundle, so each reads that bundle,
    not the engine's default catalog, which holds no ``sdmx`` schema."""
    engine = BundleEngine()
    small, large = (sl.LocalRegistry.bundle(few[i], engine=engine) for i in (0, 5))
    try:
        for registry, rows in ((small, 1), (large, 6)):
            assert sum(batch.num_rows for batch in registry.obs(FLOW.urn).stream(chunk_rows=2)) == rows
            assert registry.query(sl.q.dataflow).frame().height == 1
            flows = registry.query(sl.q.dataflow).select(sl.q.dataflow.id)
            assert sum(batch.num_rows for batch in flows.stream()) == 1
            obs = registry.obs(FLOW.urn)
            assert obs.filter(obs.col("GEO").is_in(registry.query(sl.q.dataflow).select(sl.q.dataflow.id))).count() == 0
    finally:
        small.close()
        large.close()
        engine.close()
