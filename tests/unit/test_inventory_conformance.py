"""The Inventory protocol's conformance suite, against the file protocol and a Delta prototype (GH-252)."""

import itertools
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import override

import msgspec
import pytest
from msgspec import UnsetType

from sdmxlib.inventory import (
    CATALOG_NAME,
    CatalogDocument,
    EventKind,
    Leaf,
    Root,
    Tombstone,
    change_list,
    decode_leaf,
    decode_root,
    encode,
    root_path,
    sha256_hex,
)
from sdmxlib.inventory.conformance import (
    CHECKS,
    HISTORY_CHECKS,
    Backend,
    HistoryBackend,
    Snapshot,
    run,
    run_history,
)
from tests.support.inventory_backends import DeltaBackend, FileBackend

pytestmark = pytest.mark.unit

_KINDS: dict[str, Callable[[Path], Backend]] = {"file": FileBackend, "delta": DeltaBackend}


@pytest.mark.parametrize("kind", list(_KINDS))
@pytest.mark.parametrize("check", CHECKS, ids=[c.__name__.removeprefix("check_") for c in CHECKS])
def test_the_backend_conforms(kind: str, check: Callable[[Callable[[], Backend]], None], tmp_path: Path) -> None:
    counter = itertools.count()
    check(lambda: _KINDS[kind](tmp_path / f"catalog-{next(counter)}"))


def test_run_runs_every_check(tmp_path: Path) -> None:
    counter = itertools.count()
    assert run(lambda: FileBackend(tmp_path / f"c{next(counter)}")) == [c.__name__ for c in CHECKS]


class _NoConflict(FileBackend):
    """A sabotaged backend whose commits never conflict: the last writer silently wins."""

    @override
    def commit(
        self,
        base: Snapshot | None,
        leaves: Mapping[str, Leaf],
        *,
        retired: Sequence[Tombstone] | None = None,
        embargo: bool | None = None,
        kind: EventKind = "seal",
    ) -> Snapshot:
        return super().commit(self.head(), leaves, retired=retired, embargo=embargo, kind=kind)


class _HeadReader:
    def __init__(self, backend: FileBackend) -> None:
        self._backend = backend

    def refresh(self) -> Snapshot:
        found = self._backend.head()
        assert found is not None
        return found


class _GoesBack(FileBackend):
    """A sabotaged backend whose reader takes whatever is newest on storage, lower or not."""

    @override
    def reader(self) -> _HeadReader:
        return _HeadReader(self)


@pytest.mark.parametrize(
    ("sabotaged", "failing"),
    [
        (_NoConflict, "check_a_stale_commit_is_refused"),
        (_GoesBack, "check_a_reader_finds_newer_versions_and_never_goes_back"),
    ],
)
def test_a_backend_breaking_the_protocol_fails_its_check(
    sabotaged: type[FileBackend], failing: str, tmp_path: Path
) -> None:
    """Discrimination: each check can fail."""
    check = next(c for c in CHECKS if c.__name__ == failing)
    counter = itertools.count()
    with pytest.raises(AssertionError):
        check(lambda: sabotaged(tmp_path / f"c{next(counter)}"))


# ── the history fields (GH-361) ──────────────────────────────────────────────


@pytest.mark.parametrize("check", HISTORY_CHECKS, ids=[c.__name__.removeprefix("check_") for c in HISTORY_CHECKS])
def test_the_reference_writer_meets_the_history_checks(
    check: Callable[[Callable[[], HistoryBackend]], None], tmp_path: Path
) -> None:
    counter = itertools.count()
    check(lambda: FileBackend(tmp_path / f"catalog-{next(counter)}"))


def test_run_history_runs_every_history_check(tmp_path: Path) -> None:
    counter = itertools.count()
    assert run_history(lambda: FileBackend(tmp_path / f"c{next(counter)}")) == [c.__name__ for c in HISTORY_CHECKS]


def _leaves_of(backend: FileBackend, root: Root) -> dict[str, Leaf]:
    return {p: decode_leaf((backend.root / ref.leaf).read_bytes()) for p, ref in root.products.items()}


def _snapshot_of(backend: FileBackend, seq: int) -> Snapshot:
    root = decode_root((backend.root / root_path(seq)).read_bytes())
    return Snapshot(
        seq=seq,
        restore_epoch=root.restore_epoch,
        embargo=root.embargo,
        leaves=_leaves_of(backend, root),
        retired=root.retired,
    )


class _Sabotaged(FileBackend):
    """A writer whose root a subclass corrupts before it is published."""

    def _corrupt(self, root: Root, base: Snapshot | None, built_from: int | None) -> Root:
        del base, built_from
        return root

    @override
    def commit_at(
        self,
        base: Snapshot | None,
        leaves: Mapping[str, Leaf],
        *,
        now: datetime,
        retired: Sequence[Tombstone] | None = None,
        kind: EventKind = "seal",
        built_from: int | None = None,
    ) -> Snapshot:
        root = self._next(base, leaves, retired=retired, embargo=None, kind=kind, now=now, built_from=built_from)
        return self._publish(self._corrupt(root, base, built_from))


class _DiffsAgainstBuiltFrom(_Sabotaged):
    """A writer that diffs a correction against the version it was built from."""

    @override
    def _corrupt(self, root: Root, base: Snapshot | None, built_from: int | None) -> Root:
        if built_from is None or base is None:
            return root
        older = _snapshot_of(self, built_from)
        base_root = decode_root((self.root / root_path(built_from)).read_bytes())
        changes = change_list(
            base_root, older.leaves, products=root.products, leaves=_leaves_of(self, root), retired=root.retired
        )
        return msgspec.structs.replace(root, changes=changes)


class _HashesItsFirstBase(_Sabotaged):
    """A writer that, after losing a race, keeps the hash of the base it first read."""

    @override
    def _corrupt(self, root: Root, base: Snapshot | None, built_from: int | None) -> Root:
        if root.seq != 3:
            return root
        return msgspec.structs.replace(root, previous_root_sha256=sha256_hex((self.root / root_path(1)).read_bytes()))


class _IgnoresTheBaseClock(_Sabotaged):
    """A writer that writes its own clock whatever the base version's ``created_at``."""

    @override
    def _corrupt(self, root: Root, base: Snapshot | None, built_from: int | None) -> Root:
        return msgspec.structs.replace(root, created_at=root.created_at.replace(minute=30, second=0, microsecond=0))


class _DropsTombstoneItems(_Sabotaged):
    """A writer whose change list leaves out the changes to tombstones alone."""

    @override
    def _corrupt(self, root: Root, base: Snapshot | None, built_from: int | None) -> Root:
        changes = root.changes
        if isinstance(changes, UnsetType):
            return root
        kept = tuple(c for c in changes if c.leaf_sha256.from_ != c.leaf_sha256.to)
        return msgspec.structs.replace(root, changes=kept)


class _TakesAnyCatalog(_Sabotaged):
    """A writer that does not read ``catalog.json`` before it commits."""

    @override
    def commit_at(
        self,
        base: Snapshot | None,
        leaves: Mapping[str, Leaf],
        *,
        now: datetime,
        retired: Sequence[Tombstone] | None = None,
        kind: EventKind = "seal",
        built_from: int | None = None,
    ) -> Snapshot:
        catalog = self.root / CATALOG_NAME
        held = catalog.read_bytes()
        catalog.write_bytes(_ONE_POINT_ZERO)
        try:
            return super().commit_at(base, leaves, now=now, retired=retired, kind=kind, built_from=built_from)
        finally:
            catalog.write_bytes(held)


_ONE_POINT_ZERO = encode(CatalogDocument(format_version="1.0", catalog_id="01a18e00-0000-7000-8000-000000000000"))


@pytest.mark.parametrize(
    ("sabotaged", "failing"),
    [
        (_DiffsAgainstBuiltFrom, "check_a_correction_links_and_diffs_against_the_version_before_it"),
        (_HashesItsFirstBase, "check_a_lost_race_rebuilds_its_history_fields"),
        (_IgnoresTheBaseClock, "check_created_at_is_monotone_and_a_fast_base_is_refused"),
        (_DropsTombstoneItems, "check_the_change_list_records_each_kind_of_change"),
        (_TakesAnyCatalog, "check_a_writer_without_stage_2_support_refuses_an_opted_in_catalog"),
    ],
)
def test_a_writer_breaking_a_history_rule_fails_its_check(
    sabotaged: type[FileBackend], failing: str, tmp_path: Path
) -> None:
    """Discrimination: each history check can fail."""
    check = next(c for c in HISTORY_CHECKS if c.__name__ == failing)
    counter = itertools.count()
    with pytest.raises(AssertionError):
        check(lambda: sabotaged(tmp_path / f"c{next(counter)}"))
