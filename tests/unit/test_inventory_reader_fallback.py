"""The reader's fallback when ``CURRENT`` fails (GH-361; sharded catalog spec §6.6)."""

from collections.abc import Callable
from pathlib import Path

import msgspec
import pytest

from sdmxlib.inventory import (
    CATALOG_NAME,
    CURRENT_NAME,
    HISTORY_FORMAT,
    HISTORY_NAME,
    OPTED_IN_CATALOG_FORMAT,
    CatalogHistory,
    Event,
    FullInventory,
    History,
    InventoryError,
    Leaf,
    decode_catalog,
    decode_root,
    encode,
    local_fetch,
    next_root,
    record,
    root_path,
)
from tests.support.inventory import NOW, PRODUCER, new_catalog, set_current, write_version

pytestmark = pytest.mark.unit

_A = "01a18e00-0000-7000-8000-000000000001"


def _sealed(leaf: Leaf | None, edition: str, previous: str | None, product: str = "p") -> Leaf:
    return record(
        leaf,
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


def _versions(root: Path, count: int, *, restore_epoch: int = 0) -> Path:
    new_catalog(root, restore_epoch=restore_epoch)
    for n in range(count):
        write_version(root, {f"p{n}": _sealed(None, _A, None, f"p{n}")}, restore_epoch=restore_epoch)
    return root


def _opt_in(root: Path, *, expired_through: int = 0, restore_epoch: int = 0, history: bool = True) -> None:
    catalog = decode_catalog((root / CATALOG_NAME).read_bytes())
    opted = msgspec.structs.replace(
        catalog, format_version=OPTED_IN_CATALOG_FORMAT, history=CatalogHistory(since_seq=1)
    )
    (root / CATALOG_NAME).write_bytes(encode(opted))
    if history:
        document = History(
            format_version=HISTORY_FORMAT,
            catalog_id=catalog.catalog_id,
            restore_epoch=restore_epoch,
            expired_through=expired_through,
            newest_segment=None,
            newest_segment_sha256=None,
            cleaned_through=None,
        )
        (root / HISTORY_NAME).write_bytes(encode(document))


def _expire(root: Path, through: int) -> None:
    for seq in range(1, through + 1):
        (root / root_path(seq)).unlink()


def _recording(root: Path) -> tuple[Callable[[str], bytes | None], list[str]]:
    seen: list[str] = []
    fetch = local_fetch(root)

    def recorded(path: str) -> bytes | None:
        seen.append(path)
        return fetch(path)

    return recorded, seen


# ── the reader fallback (spec §6.6) ──────────────────────────────────────────


def test_with_current_verifying_the_reader_reads_only_current_and_roots(tmp_path: Path) -> None:
    _versions(tmp_path, 2)
    fetch, seen = _recording(tmp_path)
    assert FullInventory(fetch).refresh().seq == 2
    assert seen == [CURRENT_NAME, root_path(2), root_path(3)]


def test_with_current_missing_on_a_1x_catalog_the_epoch_comes_from_catalog_json(tmp_path: Path) -> None:
    _versions(tmp_path, 2, restore_epoch=1)
    (tmp_path / CURRENT_NAME).unlink()
    fetch, seen = _recording(tmp_path)
    reader = FullInventory(fetch).refresh()
    assert (reader.seq, reader.restore_epoch) == (2, 1)
    assert CATALOG_NAME in seen
    assert HISTORY_NAME not in seen


def test_with_current_missing_on_a_2x_catalog_the_epoch_comes_from_history(tmp_path: Path) -> None:
    _versions(tmp_path, 2)
    write_version(tmp_path, {}, kind="restore", restore_epoch=1)
    _opt_in(tmp_path, restore_epoch=1)
    (tmp_path / CURRENT_NAME).unlink()
    reader = FullInventory(local_fetch(tmp_path)).refresh()
    assert (reader.seq, reader.restore_epoch) == (3, 1)
    assert decode_catalog((tmp_path / CATALOG_NAME).read_bytes()).restore_epoch == 0


def test_a_2x_catalog_without_history_fails_closed(tmp_path: Path) -> None:
    _versions(tmp_path, 2)
    _opt_in(tmp_path, history=False)
    (tmp_path / CURRENT_NAME).unlink()
    with pytest.raises(InventoryError, match=f"has no {HISTORY_NAME}"):
        FullInventory(local_fetch(tmp_path)).refresh()


def test_the_probe_starts_above_expired_through(tmp_path: Path) -> None:
    _versions(tmp_path, 5)
    _opt_in(tmp_path, expired_through=3)
    _expire(tmp_path, 3)
    (tmp_path / CURRENT_NAME).unlink()
    fetch, seen = _recording(tmp_path)
    assert FullInventory(fetch).refresh().seq == 5
    assert [p for p in seen if p.startswith("inventory/0")] == [root_path(4), root_path(5), root_path(6)]


def test_the_walk_back_stops_at_the_bound(tmp_path: Path) -> None:
    _versions(tmp_path, 6)
    _opt_in(tmp_path, expired_through=3)
    _expire(tmp_path, 3)
    (tmp_path / root_path(5)).unlink()
    (tmp_path / root_path(6)).unlink()
    set_current(tmp_path, 6, "f" * 64)
    fetch, seen = _recording(tmp_path)
    assert FullInventory(fetch).refresh().seq == 4
    assert root_path(3) not in seen


def test_a_held_root_at_or_below_expired_through_is_dropped(tmp_path: Path) -> None:
    _versions(tmp_path, 2)
    reader = FullInventory(local_fetch(tmp_path)).refresh()
    other = FullInventory(local_fetch(tmp_path)).refresh()
    write_version(tmp_path, {})
    write_version(tmp_path, {})
    _opt_in(tmp_path, expired_through=3)
    _expire(tmp_path, 3)
    (tmp_path / CURRENT_NAME).unlink()
    assert reader.refresh().seq == 4
    (tmp_path / root_path(4)).unlink()
    with pytest.raises(InventoryError, match="no valid Inventory version"):
        other.refresh()
    with pytest.raises(InventoryError, match="no valid Inventory version"):
        _ = other.seq


def test_a_held_root_above_the_bound_is_kept_when_nothing_newer_is_found(tmp_path: Path) -> None:
    _versions(tmp_path, 3)
    reader = FullInventory(local_fetch(tmp_path)).refresh()
    (tmp_path / root_path(3)).unlink()
    (tmp_path / CURRENT_NAME).unlink()
    assert reader.refresh().seq == 3


def test_no_root_above_the_bound_and_none_held_fails_closed(tmp_path: Path) -> None:
    _versions(tmp_path, 3)
    _opt_in(tmp_path, expired_through=3)
    _expire(tmp_path, 3)
    (tmp_path / CURRENT_NAME).unlink()
    with pytest.raises(InventoryError, match="no valid Inventory version"):
        FullInventory(local_fetch(tmp_path)).refresh()


def test_a_malformed_current_falls_back(tmp_path: Path) -> None:
    _versions(tmp_path, 2)
    (tmp_path / CURRENT_NAME).write_bytes(b"{not json")
    reader = FullInventory(local_fetch(tmp_path)).refresh()
    assert reader.seq == 2
    assert any(CURRENT_NAME in s for s in reader.skipped)


def test_a_restore_crash_after_history_fails_closed_until_the_new_epoch_has_a_root(tmp_path: Path) -> None:
    """Restore step 2 wrote the new epoch to HISTORY; no root of epoch 1 exists yet (spec §6.9)."""
    _versions(tmp_path, 3)
    _opt_in(tmp_path, restore_epoch=1)
    (tmp_path / root_path(3)).unlink()
    set_current(tmp_path, 3, "f" * 64)
    with pytest.raises(InventoryError, match="no valid Inventory version"):
        FullInventory(local_fetch(tmp_path)).refresh()
    raw = (tmp_path / root_path(2)).read_bytes()
    base = decode_root(raw)
    restore = Event(actor="tests", reason="restore", kind="restore")
    after = next_root(
        base,
        products=base.products,
        event=restore,
        producer=PRODUCER,
        now=NOW,
        restore_epoch=1,
        previous_bytes=raw,
        changes=(),
    )
    (tmp_path / root_path(3)).write_bytes(encode(after))
    assert (FullInventory(local_fetch(tmp_path)).refresh().seq, after.restore_epoch) == (3, 1)
