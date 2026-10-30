"""A conformance suite for implementations of the Inventory protocol.

The Inventory is a small transaction log of its own: exclusively created,
sequence-numbered versions, a hint pointer, a probing reader, a seal journal
and tombstones (sharded catalog spec §6). This suite states its guarantees as
checks any implementation runs: statcatlib's writer over local disk or blob
storage, sdmxlib's reference writer, and a Delta-backed prototype that proves
the spec's escape route (§6.6).

An implementation provides a `Backend`, and `run` calls every check with a
factory that returns a fresh, empty one:

    from sdmxlib.inventory.conformance import run

    def test_my_inventory_conforms(tmp_path):
        run(lambda: MyBackend(tmp_path / str(uuid.uuid4())))

Each check raises `AssertionError` naming what failed.
"""

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Protocol

import msgspec

from sdmxlib.inventory._format import EventKind, Leaf, Tombstone
from sdmxlib.inventory._rules import (
    TransitionError,
    head,
    record,
    release,
    release_due,
    repairable,
    retire,
    rollback,
)

#: The schema every check's editions are written at.
SCHEMA_VERSION = "2.52"

_T0 = datetime(2026, 10, 9, 12, 30, tzinfo=UTC)


class Snapshot(msgspec.Struct, frozen=True, kw_only=True):
    """One Inventory version as an implementation holds it.

    Attributes:
        seq: The version's sequence number.
        restore_epoch: Its restore epoch.
        embargo: Its embargo setting.
        leaves: Each product's leaf.
        retired: Its tombstones.
    """

    seq: int
    restore_epoch: int
    embargo: bool
    leaves: dict[str, Leaf]
    retired: tuple[Tombstone, ...] = ()


class ConflictError(Exception):
    """A commit lost to another writer: its base is no longer the newest version."""


class Reader(Protocol):
    """A long-lived reader, holding the version it last read."""

    def refresh(self) -> Snapshot:
        """Read the newest version the protocol accepts, and hold it."""
        ...


class Backend(Protocol):
    """What an implementation of the Inventory provides to the suite."""

    def head(self) -> Snapshot | None:
        """The newest version, as a fresh reader sees it, or None before the first."""
        ...

    def commit(
        self,
        base: Snapshot | None,
        leaves: Mapping[str, Leaf],
        *,
        retired: Sequence[Tombstone] | None = None,
        embargo: bool | None = None,
        kind: EventKind = "seal",
    ) -> Snapshot:
        """Write the version after `base`, with `leaves` replacing those products' leaves.

        Raises:
            ConflictError: If `base` is not the newest version.
        """
        ...

    def commit_interrupted(self, base: Snapshot | None, leaves: Mapping[str, Leaf]) -> None:
        """Start the version after `base` and stop as a writer killed before its last byte would."""
        ...

    def reader(self) -> Reader:
        """A new long-lived reader."""
        ...

    def hint_behind(self) -> None:
        """Make the hint name a version older than the newest, as a writer that died before updating it leaves it."""
        ...

    def restore_to(self, seq: int) -> None:
        """Lose every version after `seq`, as restoring an older copy of storage does, keeping the epoch."""
        ...

    def restore_with_new_epoch(self, seq: int) -> None:
        """Lose every version after `seq`, then bump the restore epoch, as a recorded failover does."""
        ...


def _fail(message: str) -> AssertionError:
    return AssertionError(message)


def _present(snapshot: Snapshot | None, what: str) -> Snapshot:
    if snapshot is None:
        msg = f"{what}: the backend has no Inventory version"
        raise _fail(msg)
    return snapshot


def _edition(n: int) -> str:
    return f"01a18e00-0000-7000-8000-{n:012d}"


def _sealed(
    leaf: Leaf | None, product: str, edition: str, previous: str | None, *, embargo: bool = False, at: int = 0
) -> Leaf:
    now = _T0 + timedelta(minutes=at)
    return record(
        leaf,
        product_id=product,
        kind="data",
        lifecycle="revisable",
        edition_id=edition,
        previous=previous,
        manifest_sha256="0" * 64,
        schema_version=SCHEMA_VERSION,
        embargo=embargo,
        now=now,
        release_at=now if embargo else None,
    )


def _live(snapshot: Snapshot | None, product: str) -> str | None:
    if snapshot is None or product not in snapshot.leaves:
        return None
    entry = head(snapshot.leaves[product], SCHEMA_VERSION)
    return None if entry is None else entry.edition_id


def _seal_with_retry(backend: Backend, product: str, edition: str, base: Snapshot | None) -> Snapshot:
    """A writer's loop: on a conflict, re-read the head and re-evaluate against it."""
    for _ in range(5):
        current = base
        leaf = None if current is None else current.leaves.get(product)
        previous = _live(current, product)
        try:
            return backend.commit(current, {product: _sealed(leaf, product, edition, previous)})
        except ConflictError:
            base = backend.head()
    msg = f"{product}: five conflicts in a row"
    raise AssertionError(msg)


def check_concurrent_writers_on_different_products_both_land(new: Callable[[], Backend]) -> None:
    """Two writers start from one version; the loser retries; both products end live."""
    backend = new()
    base = backend.commit(None, {"a": _sealed(None, "a", _edition(1), None)})
    first = _seal_with_retry(backend, "b", _edition(2), base)
    second = _seal_with_retry(backend, "c", _edition(3), base)
    newest = _present(backend.head(), "after two writers")
    if not newest.seq == second.seq == first.seq + 1:
        msg = f"versions {first.seq} and {second.seq}, head {newest.seq}: the loser did not land after the winner"
        raise _fail(msg)
    found = {p: _live(newest, p) for p in ("a", "b", "c")}
    if found != {"a": _edition(1), "b": _edition(2), "c": _edition(3)}:
        msg = f"live editions {found}: a writer's change was lost"
        raise _fail(msg)


def check_a_stale_commit_is_refused(new: Callable[[], Backend]) -> None:
    """A commit whose base is no longer the newest version raises `ConflictError`."""
    backend = new()
    base = backend.commit(None, {"a": _sealed(None, "a", _edition(1), None)})
    backend.commit(base, {"b": _sealed(None, "b", _edition(2), None)})
    try:
        backend.commit(base, {"c": _sealed(None, "c", _edition(3), None)})
    except ConflictError:
        return
    msg = "a commit from a stale base landed"
    raise _fail(msg)


def check_an_interrupted_commit_leaves_the_head_readable_and_writable(new: Callable[[], Backend]) -> None:
    """A writer killed between create and its last byte leaves the previous head, and the next write lands."""
    backend = new()
    base = backend.commit(None, {"a": _sealed(None, "a", _edition(1), None)})
    backend.commit_interrupted(base, {"b": _sealed(None, "b", _edition(2), None)})
    held = _present(backend.head(), "after an interrupted commit")
    if held.seq != base.seq or "b" in held.leaves:
        msg = f"the head moved to version {held.seq} after an interrupted commit"
        raise _fail(msg)
    after = backend.commit(held, {"b": _sealed(None, "b", _edition(2), None)})
    if after.seq != base.seq + 1:
        msg = f"the next writer wrote version {after.seq}, not {base.seq + 1}"
        raise _fail(msg)


def check_a_reader_finds_newer_versions_and_never_goes_back(new: Callable[[], Backend]) -> None:
    """A reader probes past a lagging hint, and holding N never accepts N - 1."""
    backend = new()
    newest = _three_versions(backend)
    backend.hint_behind()
    reader = backend.reader()
    if (seq := reader.refresh().seq) != newest:
        msg = f"a reader read version {seq} where the newest is {newest}"
        raise _fail(msg)
    backend.restore_to(newest - 1)
    if (seq := reader.refresh().seq) != newest:
        msg = f"a reader holding version {newest} accepted {seq}"
        raise _fail(msg)


def check_a_restore_epoch_change_resets_readers(new: Callable[[], Backend]) -> None:
    """After a recorded restore, a reader holding a higher seq follows the new epoch."""
    backend = new()
    newest = _three_versions(backend)
    reader = backend.reader()
    held = reader.refresh()
    backend.restore_with_new_epoch(newest - 1)
    after = reader.refresh()
    if after.restore_epoch <= held.restore_epoch or "p3" in after.leaves:
        msg = "a reader kept a version the restore lost, at the old epoch"
        raise _fail(msg)


def check_fast_forward_and_conditional_rollback(new: Callable[[], Backend]) -> None:
    """A non-fast-forward release is refused, and a rollback retried after a competing release is refused."""
    backend = new()
    leaf = _sealed(_sealed(None, "p", _edition(1), None), "p", _edition(2), _edition(1), at=1)
    base = backend.commit(None, {"p": leaf})
    stale = _sealed(base.leaves["p"], "p", _edition(3), _edition(1), embargo=True, at=2)
    _refused(lambda: release(stale, _edition(3), now=_T0 + timedelta(minutes=3)), "fast-forward")
    decided = _present(backend.head(), "before the rollback")
    planned = rollback(decided.leaves["p"], schema_version=SCHEMA_VERSION, if_live=_edition(2), now=_T0)
    competing = _sealed(decided.leaves["p"], "p", _edition(4), _edition(2), at=4)
    backend.commit(decided, {"p": competing})
    try:
        backend.commit(decided, {"p": planned})
    except ConflictError:
        pass
    else:
        msg = "a rollback from a stale base landed"
        raise _fail(msg)
    retried = _present(backend.head(), "after the competing release")
    _refused(
        lambda: rollback(retried.leaves["p"], schema_version=SCHEMA_VERSION, if_live=_edition(2), now=_T0), "if-live"
    )


def check_a_withdrawn_edition_is_never_released_again(new: Callable[[], Backend]) -> None:
    """Rolled back, then retired, an edition left in the seal journal is not recorded or released again."""
    backend = new()
    leaf = _sealed(_sealed(None, "p", _edition(1), None), "p", _edition(2), _edition(1), at=1)
    base = backend.commit(None, {"p": leaf})
    back = rollback(base.leaves["p"], schema_version=SCHEMA_VERSION, if_live=_edition(2), now=_T0)
    withdrawn = backend.commit(base, {"p": back}, kind="rollback")
    kept, stones = retire(withdrawn.leaves["p"], [_edition(2)], now=_T0)
    retired = backend.commit(withdrawn, {"p": kept}, retired=stones, kind="retire")
    journal = [("p", _edition(2))]
    if repairable(journal, withdrawn.leaves, withdrawn.retired) or repairable(journal, retired.leaves, retired.retired):
        msg = "the repair would record a withdrawn or retired edition"
        raise _fail(msg)
    changed, _ = release_due(withdrawn.leaves, _T0 + timedelta(days=1))
    if changed:
        msg = "release_due released a withdrawn edition"
        raise _fail(msg)
    if (live := _live(backend.head(), "p")) != _edition(1):
        msg = f"{live} is live after the rollback, not {_edition(1)}"
        raise _fail(msg)


def _three_versions(backend: Backend) -> int:
    """Commit three versions, adding products p1 to p3; return the newest's seq."""
    snapshot = None
    for n in range(1, 4):
        snapshot = backend.commit(snapshot, {f"p{n}": _sealed(None, f"p{n}", _edition(n), None)})
    return _present(snapshot, "after three commits").seq


def _refused(transition: Callable[[], object], rule: str) -> None:
    try:
        transition()
    except TransitionError as exc:
        if exc.rule != rule:
            msg = f"refused by {exc.rule}, not {rule}"
            raise _fail(msg) from exc
        return
    msg = f"a transition the {rule} rule refuses went through"
    raise _fail(msg)


#: Every check, in the order `run` calls them.
CHECKS = (
    check_concurrent_writers_on_different_products_both_land,
    check_a_stale_commit_is_refused,
    check_an_interrupted_commit_leaves_the_head_readable_and_writable,
    check_a_reader_finds_newer_versions_and_never_goes_back,
    check_a_restore_epoch_change_resets_readers,
    check_fast_forward_and_conditional_rollback,
    check_a_withdrawn_edition_is_never_released_again,
)


def run(new: Callable[[], Backend]) -> list[str]:
    """Run every check against fresh backends from `new`.

    Returns:
        The names of the checks run, all passed.

    Raises:
        AssertionError: From the first check that fails, naming it.
    """
    ran: list[str] = []
    for check in CHECKS:
        try:
            check(new)
        except AssertionError as exc:
            msg = f"{check.__name__}: {exc}"
            raise AssertionError(msg) from exc
        ran.append(check.__name__)
    return ran
