"""A read of every product fetches its leaves on a pool (GH-375; sharded catalog spec §6.5, §7.3)."""

import threading
from collections import Counter
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import override

import pytest

from sdmxlib.inventory import (
    CURRENT_NAME,
    DEFAULT_LEAF_THREADS,
    FullInventory,
    InventoryError,
    Leaf,
    LiveEdition,
    LiveInventory,
    Root,
    local_fetch,
    record,
    root_path,
)
from sdmxlib.inventory import _reader as reader_module
from tests.support.inventory import NOW, new_catalog, write_version

pytestmark = pytest.mark.unit

_A = "01a18e00-0000-7000-8000-000000000001"
_B = "01a18e00-0000-7000-8000-000000000002"
#: Long enough for a loaded machine, and short enough that a regression fails instead of hanging.
_TIMEOUT_S = 30.0
_LEAF_DIR = "inventory/leaves/"

type _Reader = FullInventory | LiveInventory
type _View = type[FullInventory | LiveInventory]

_VIEWS: tuple[_View, ...] = (FullInventory, LiveInventory)


def _sealed(product: str, *, edition: str = _A, base: Leaf | None = None, previous: str | None = None) -> Leaf:
    return record(
        base,
        product_id=product,
        kind="data",
        lifecycle="revisable",
        edition_id=edition,
        previous=previous,
        manifest_sha256="0" * 64,
        schema_version="2.52",
        embargo=False,
        now=NOW,
    )


def _catalog(root: Path, count: int) -> Root:
    new_catalog(root)
    return write_version(root, {f"p{n:03}": _sealed(f"p{n:03}") for n in range(count)})


def _leaf_file(root: Path, version: Root, product: str) -> Path:
    return root / version.products[product].leaf


def _read_all(reader: _Reader) -> Mapping[str, Leaf] | Mapping[str, LiveEdition]:
    return reader.leaves() if isinstance(reader, FullInventory) else reader.editions()


def _new(view: _View, fetch: Callable[[str], bytes | None], leaf_threads: int) -> _Reader:
    if view is FullInventory:
        return FullInventory(fetch, leaf_threads=leaf_threads)
    return LiveInventory(fetch, schema_version="2.52", leaf_threads=leaf_threads)


class _Recording:
    """A `Fetch` over a directory that records each GET's path and thread. `list.append` is atomic."""

    def __init__(self, root: Path) -> None:
        self._fetch = local_fetch(root)
        self.paths: list[str] = []
        self.threads: list[threading.Thread] = []

    def __call__(self, path: str) -> bytes | None:
        self.paths.append(path)
        self.threads.append(threading.current_thread())
        return self._fetch(path)

    def leaves(self) -> list[str]:
        """The leaf paths fetched, in the order the calls started."""
        return [path for path in self.paths if path.startswith(_LEAF_DIR)]


def _expected_error(view: _View, root: Path) -> str:
    """The message a sequential read of `root` raises, compared by the tests below against a pool's."""
    with pytest.raises(InventoryError) as caught:
        _read_all(_new(view, local_fetch(root), 1))
    return str(caught.value)


# -- the GETs ----------------------------------------------------------------


@pytest.mark.parametrize("view", _VIEWS)
def test_a_cold_read_makes_one_get_per_leaf(tmp_path: Path, view: _View) -> None:
    version = _catalog(tmp_path, 12)
    pooled, sequential = _Recording(tmp_path), _Recording(tmp_path)

    _read_all(_new(view, pooled, 4))
    _read_all(_new(view, sequential, 1))

    expected = Counter(
        [CURRENT_NAME, root_path(version.seq), root_path(version.seq + 1)]
        + [ref.leaf for ref in version.products.values()]
    )
    assert Counter(pooled.paths) == expected
    assert Counter(sequential.paths) == expected


@pytest.mark.parametrize("view", _VIEWS)
def test_only_the_leaf_gets_leave_the_calling_thread(tmp_path: Path, view: _View) -> None:
    _catalog(tmp_path, 12)
    pooled, sequential = _Recording(tmp_path), _Recording(tmp_path)
    caller = threading.current_thread()

    _read_all(_new(view, pooled, 4))
    _read_all(_new(view, sequential, 1))

    for path, thread in zip(pooled.paths, pooled.threads, strict=True):
        assert (thread is caller) == (not path.startswith(_LEAF_DIR)), path
    assert set(sequential.threads) == {caller}


def test_two_products_on_one_leaf_make_one_get(tmp_path: Path) -> None:
    new_catalog(tmp_path)
    shared = _sealed("a")
    version = write_version(tmp_path, {"a": shared, "b": shared, "c": _sealed("c")})
    fetch = _Recording(tmp_path)

    leaves = FullInventory(fetch, leaf_threads=4).leaves()

    assert version.products["a"] == version.products["b"]
    assert list(leaves) == ["a", "b", "c"]
    assert Counter(fetch.leaves()) == Counter([version.products["a"].leaf, version.products["c"].leaf])


# -- the overlap -------------------------------------------------------------


def _overlap(
    root: Path, parties: int, *, timeout: float = _TIMEOUT_S
) -> tuple[Callable[[str], bytes | None], Callable[[], int]]:
    """A `Fetch` whose leaf GETs each wait until `parties` of them are in flight, and the most ever in flight."""
    fetch = local_fetch(root)
    barrier = threading.Barrier(parties, timeout=timeout)
    lock = threading.Lock()
    in_flight = [0, 0]

    def overlapping(path: str) -> bytes | None:
        if not path.startswith(_LEAF_DIR):
            return fetch(path)
        with lock:
            in_flight[0] += 1
            in_flight[1] = max(in_flight)
        barrier.wait()
        raw = fetch(path)
        with lock:
            in_flight[0] -= 1
        return raw

    return overlapping, lambda: in_flight[1]


@pytest.mark.parametrize("view", _VIEWS)
def test_leaf_reads_overlap_up_to_the_pool_size(tmp_path: Path, view: _View) -> None:
    _catalog(tmp_path, 8)
    fetch, most = _overlap(tmp_path, 4)

    assert len(_read_all(_new(view, fetch, 4))) == 8
    assert most() == 4


def test_the_default_pool_size_overlaps_that_many_reads(tmp_path: Path) -> None:
    assert DEFAULT_LEAF_THREADS == 64, "spec §6.5 and §7.3 state this default"
    _catalog(tmp_path, DEFAULT_LEAF_THREADS)
    fetch, most = _overlap(tmp_path, DEFAULT_LEAF_THREADS)

    assert len(FullInventory(fetch).leaves()) == DEFAULT_LEAF_THREADS
    assert most() == DEFAULT_LEAF_THREADS


def test_a_sequential_read_fails_the_overlap_barrier(tmp_path: Path) -> None:
    """The discrimination check for the overlap tests: without a pool, the first leaf GET waits alone."""
    _catalog(tmp_path, 4)
    fetch, most = _overlap(tmp_path, 2, timeout=0.05)

    with pytest.raises(threading.BrokenBarrierError):
        FullInventory(fetch, leaf_threads=1).leaves()

    assert most() == 1


# -- the results -------------------------------------------------------------


@pytest.mark.parametrize("view", _VIEWS)
def test_the_mappings_equal_a_sequential_read_in_the_same_order(tmp_path: Path, view: _View) -> None:
    new_catalog(tmp_path)
    live = {f"p{n:02}": _sealed(f"p{n:02}") for n in range(9)}
    embargoed = record(
        None,
        product_id="p04",
        kind="data",
        lifecycle="revisable",
        edition_id=_A,
        previous=None,
        manifest_sha256="0" * 64,
        schema_version="2.52",
        embargo=True,
        now=NOW,
    )
    write_version(tmp_path, {**live, "p04": embargoed})

    pooled = _read_all(_new(view, local_fetch(tmp_path), 4))
    sequential = _read_all(_new(view, local_fetch(tmp_path), 1))

    assert pooled == sequential
    assert list(pooled) == list(sequential)
    # `LiveInventory` drops the product with no live edition and `FullInventory` keeps it,
    # so the two views can differ and each mapping is the one its view promises.
    expected = sorted(live) if view is FullInventory else sorted(set(live) - {"p04"})
    assert list(pooled) == expected


def test_live_products_read_through_the_pool(tmp_path: Path) -> None:
    _catalog(tmp_path, 8)
    fetch, most = _overlap(tmp_path, 4)

    assert LiveInventory(fetch, schema_version="2.52", leaf_threads=4).products() == [f"p{n:03}" for n in range(8)]
    assert most() == 4


# -- the errors --------------------------------------------------------------


def _missing(root: Path, version: Root, product: str) -> None:
    _leaf_file(root, version, product).unlink()


def _wrong_hash(root: Path, version: Root, product: str) -> None:
    _leaf_file(root, version, product).write_bytes(b"{}")


@pytest.mark.parametrize("view", _VIEWS)
@pytest.mark.parametrize("fault", [_missing, _wrong_hash])
def test_a_bad_leaf_raises_a_sequential_reads_error_and_leaves_no_thread(
    tmp_path: Path, view: _View, fault: Callable[[Path, Root, str], None]
) -> None:
    version = _catalog(tmp_path, 12)
    fault(tmp_path, version, "p005")
    expected = _expected_error(view, tmp_path)
    fetch = _Recording(tmp_path)

    with pytest.raises(InventoryError) as caught:
        _read_all(_new(view, fetch, 4))

    assert str(caught.value) == expected
    assert version.products["p005"].leaf in expected
    workers = set(fetch.threads) - {threading.current_thread()}
    assert workers, "the read went through a pool"
    assert not [thread for thread in workers if thread.is_alive()]


@pytest.mark.parametrize("view", _VIEWS)
def test_the_first_bad_leaf_in_sorted_order_raises_even_when_a_later_one_fails_first(
    tmp_path: Path, view: _View
) -> None:
    version = _catalog(tmp_path, 4)
    first, later = version.products["p001"].leaf, version.products["p002"].leaf
    _missing(tmp_path, version, "p001")
    _wrong_hash(tmp_path, version, "p002")
    expected = _expected_error(view, tmp_path)
    fetch = local_fetch(tmp_path)
    later_done = threading.Event()

    def later_first(path: str) -> bytes | None:
        if path == first:
            assert later_done.wait(_TIMEOUT_S)
        raw = fetch(path)
        if path == later:
            later_done.set()
        return raw

    with pytest.raises(InventoryError) as caught:
        _read_all(_new(view, later_first, 4))

    assert str(caught.value) == expected
    assert first in expected
    assert later_done.is_set(), "the later leaf was read before the first one returned"


def test_an_exception_from_fetch_reaches_the_caller_unchanged(tmp_path: Path) -> None:
    version = _catalog(tmp_path, 4)
    fetch = local_fetch(tmp_path)
    refused = OSError("the store refused")

    def refusing(path: str) -> bytes | None:
        if path == version.products["p001"].leaf:
            raise refused
        return fetch(path)

    with pytest.raises(OSError, match="the store refused") as caught:
        FullInventory(refusing, leaf_threads=4).leaves()

    assert caught.value is refused


def test_a_failed_read_holds_the_leaves_before_the_bad_one(tmp_path: Path) -> None:
    version = _catalog(tmp_path, 6)
    target = _leaf_file(tmp_path, version, "p003")
    good = target.read_bytes()
    target.unlink()
    fetch = _Recording(tmp_path)
    reader = FullInventory(fetch, leaf_threads=4)

    with pytest.raises(InventoryError):
        reader.leaves()
    target.write_bytes(good)
    fetch.paths.clear()
    assert list(reader.leaves()) == [f"p{n:03}" for n in range(6)]

    assert Counter(fetch.leaves()) == Counter(version.products[f"p{n:03}"].leaf for n in range(3, 6))


def test_a_failure_cancels_the_fetches_not_yet_started(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The first leaf is missing and every other GET blocks until the reader shuts its pool down."""
    version = _catalog(tmp_path, 40)
    threads = 2
    _missing(tmp_path, version, "p000")
    bad = version.products["p000"].leaf
    fetch = _Recording(tmp_path)
    shut_down = threading.Event()

    def blocking(path: str) -> bytes | None:
        raw = fetch(path)
        if path.startswith(_LEAF_DIR) and path != bad:
            assert shut_down.wait(_TIMEOUT_S)
        return raw

    class _Announcing(ThreadPoolExecutor):
        """Cancels first, then releases the GETs in flight, so none can start after the failure."""

        @override
        def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
            super().shutdown(wait=False, cancel_futures=cancel_futures)
            shut_down.set()
            super().shutdown(wait=wait)

    monkeypatch.setattr(reader_module, "ThreadPoolExecutor", _Announcing)

    with pytest.raises(InventoryError, match="is missing"):
        FullInventory(blocking, leaf_threads=threads).leaves()

    # A bound, since the workers may or may not have taken a second leaf by the time of the failure.
    assert bad in fetch.leaves()
    assert len(fetch.leaves()) <= 1 + threads, "at most the bad leaf and one GET per worker"


# -- what a reader holds -----------------------------------------------------


@pytest.mark.parametrize("view", _VIEWS)
def test_a_second_read_fetches_no_leaf(tmp_path: Path, view: _View) -> None:
    _catalog(tmp_path, 6)
    fetch = _Recording(tmp_path)
    reader = _new(view, fetch, 4)

    first = _read_all(reader)
    assert len(fetch.leaves()) == 6
    fetch.paths.clear()

    assert _read_all(reader) == first
    assert fetch.leaves() == []


def test_a_leaf_read_earlier_is_not_fetched_again(tmp_path: Path) -> None:
    version = _catalog(tmp_path, 5)
    fetch = _Recording(tmp_path)
    reader = FullInventory(fetch, leaf_threads=4)

    reader.leaf("p002")
    assert fetch.leaves() == [version.products["p002"].leaf]
    fetch.paths.clear()
    reader.leaves()

    assert Counter(fetch.leaves()) == Counter(version.products[f"p{n:03}"].leaf for n in (0, 1, 3, 4))


@pytest.mark.parametrize("view", _VIEWS)
@pytest.mark.parametrize("changed", [1, 3])
def test_a_refresh_then_a_read_fetches_only_the_changed_leaves(tmp_path: Path, view: _View, changed: int) -> None:
    version = _catalog(tmp_path, 6)
    fetch = _Recording(tmp_path)
    reader = _new(view, fetch, 4)
    _read_all(reader)
    products = [f"p{n:03}" for n in range(changed)]
    held = FullInventory(local_fetch(tmp_path))
    newer = write_version(
        tmp_path,
        {p: _sealed(p, edition=_B, base=held.leaf(p), previous=_A) for p in products},
    )
    fetch.paths.clear()
    fetch.threads.clear()

    reader.refresh()
    _read_all(reader)

    assert Counter(fetch.leaves()) == Counter(newer.products[p].leaf for p in products)
    assert Counter(fetch.leaves()) != Counter(version.products[p].leaf for p in products)
    caller = threading.current_thread()
    leaf_threads = [t for path, t in zip(fetch.paths, fetch.threads, strict=True) if path.startswith(_LEAF_DIR)]
    assert all((thread is caller) == (changed == 1) for thread in leaf_threads), "one leaf starts no pool"


def test_one_product_reads_in_the_calling_thread(tmp_path: Path) -> None:
    version = _catalog(tmp_path, 4)
    fetch = _Recording(tmp_path)

    FullInventory(fetch).leaf("p001")
    LiveInventory(fetch, schema_version="2.52").head("p002")

    assert sorted(fetch.leaves()) == sorted([version.products["p001"].leaf, version.products["p002"].leaf])
    assert set(fetch.threads) == {threading.current_thread()}


# -- the argument ------------------------------------------------------------


@pytest.mark.parametrize("view", _VIEWS)
@pytest.mark.parametrize("threads", [0, -1])
def test_a_pool_size_below_one_is_refused(tmp_path: Path, view: _View, threads: int) -> None:
    with pytest.raises(ValueError, match=f"leaf_threads must be at least 1, got {threads}"):
        _new(view, local_fetch(tmp_path), threads)
