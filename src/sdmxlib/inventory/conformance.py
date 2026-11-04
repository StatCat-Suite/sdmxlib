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

A writer that writes the history fields of Inventory 1.2 (stage 1)
also provides a `HistoryBackend` and runs `run_history`, the checks in
`HISTORY_CHECKS`. The cases of spec §6.9 for the catalog lock, expiry,
restores and ``verify-catalog`` come with stage 2.
"""

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Protocol

import msgspec
from msgspec import UnsetType

from sdmxlib.inventory._format import (
    CATALOG_NAME,
    HISTORY_FORMAT,
    HISTORY_NAME,
    OPTED_IN_CATALOG_FORMAT,
    CatalogHistory,
    ChangeItem,
    EventKind,
    History,
    InventoryError,
    Leaf,
    Root,
    Tombstone,
    decode_catalog,
    decode_leaf,
    decode_root,
    encode,
    root_path,
    sha256_hex,
)
from sdmxlib.inventory._rules import (
    TransitionError,
    change_list,
    head,
    record,
    reinstate,
    release,
    release_due,
    repairable,
    retire,
    rollback,
)

#: The schema every check's editions are written at.
SCHEMA_VERSION = "2.52"

_T0 = datetime(2026, 10, 9, 12, 30, tzinfo=UTC)
_MINUTE = timedelta(minutes=1)


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


# ── the history fields (Inventory 1.2, stage 1; GH-361) ─────────────────────


class HistoryBackend(Backend, Protocol):
    """A `Backend` whose writer writes the history fields (spec §6.2, §6.6), over the file protocol.

    Attributes:
        stage_2: Whether the writer declares stage-2 support: it takes the
            catalog lock and checks ``expired_through`` (spec §6.1, §6.9).
    """

    stage_2: bool

    def fetch(self, path: str) -> bytes | None:
        """The bytes at `path` under the catalog root, or None."""
        ...

    def put(self, path: str, data: bytes) -> None:
        """Replace the object at `path` under the catalog root, as another process would."""
        ...

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
        """`Backend.commit` with the writer's clock reading `now`, built from version `built_from` if given.

        Raises:
            ConflictError: If `base` is not the newest version.
            InventoryError: If the writer refuses the catalog or the base version.
        """
        ...


def _root(backend: HistoryBackend, seq: int) -> tuple[Root, bytes]:
    raw = backend.fetch(root_path(seq))
    if raw is None:
        msg = f"{root_path(seq)} is missing"
        raise _fail(msg)
    root = decode_root(raw)
    if not root.has_history:
        msg = f"Inventory version {seq} lacks the history fields"
        raise _fail(msg)
    return root, raw


def _changes(root: Root) -> tuple[ChangeItem, ...]:
    return () if isinstance(root.changes, UnsetType) else root.changes


def _expect_chain(backend: HistoryBackend, seq: int) -> Root:
    """Root `seq`, after checking it links root ``seq - 1``'s stored bytes and diffs against its leaves."""
    root, _ = _root(backend, seq)
    base, raw = _root(backend, seq - 1)
    if root.previous_root_sha256 != sha256_hex(raw):
        msg = f"Inventory version {seq} does not link the stored bytes of version {seq - 1}"
        raise _fail(msg)

    def leaves(of: Root) -> dict[str, Leaf]:
        found = {}
        for product, ref in of.products.items():
            data = backend.fetch(ref.leaf)
            if data is None:
                msg = f"{ref.leaf} is missing"
                raise _fail(msg)
            found[product] = decode_leaf(data)
        return found

    expected = change_list(base, leaves(base), products=root.products, leaves=leaves(root), retired=root.retired)
    if _changes(root) != expected:
        msg = f"Inventory version {seq}'s change list is not the one against version {seq - 1}"
        raise _fail(msg)
    return root


def check_a_correction_links_and_diffs_against_the_version_before_it(new: Callable[[], HistoryBackend]) -> None:
    """A correction at 4 built from 1 links root 3 and diffs against it (spec §6.2)."""
    backend = new()
    first = backend.commit_at(None, {"p": _sealed(None, "p", _edition(1), None)}, now=_T0)
    second = backend.commit_at(
        first, {"p": _sealed(first.leaves["p"], "p", _edition(2), _edition(1), at=1)}, now=_T0 + _MINUTE
    )
    third = backend.commit_at(second, {"q": _sealed(None, "q", _edition(3), None)}, now=_T0 + 2 * _MINUTE)
    backend.commit_at(third, {"p": first.leaves["p"]}, now=_T0 + 3 * _MINUTE, kind="correct", built_from=1)
    root = _expect_chain(backend, 4)
    if root.previous_inventory != 1:
        msg = f"the correction says it is built from {root.previous_inventory}, not 1"
        raise _fail(msg)
    third_root, _ = _root(backend, 3)
    changed = _changes(root)
    if [c.product_id for c in changed] != ["p"] or changed[0].leaf_sha256.from_ != third_root.products["p"].leaf_sha256:
        msg = "the correction's change list does not start from version 3's leaf of p"
        raise _fail(msg)
    marks = {e.edition_id: (e.from_, e.to) for e in changed[0].entries}
    if marks != {_edition(1): ("superseded", "live"), _edition(2): ("live", None)}:
        msg = f"the correction's entry items are {marks}"
        raise _fail(msg)


def check_a_lost_race_rebuilds_its_history_fields(new: Callable[[], HistoryBackend]) -> None:
    """The loser of a race computes its change list and hash again from the new head (spec §6.6)."""
    backend = new()
    base = backend.commit_at(None, {"a": _sealed(None, "a", _edition(1), None)}, now=_T0)
    backend.commit_at(base, {"b": _sealed(None, "b", _edition(2), None)}, now=_T0 + _MINUTE)
    try:
        backend.commit_at(base, {"c": _sealed(None, "c", _edition(3), None)}, now=_T0 + _MINUTE)
    except ConflictError:
        retried = _present(backend.head(), "after the race")
        backend.commit_at(retried, {"c": _sealed(None, "c", _edition(3), None)}, now=_T0 + _MINUTE)
    else:
        msg = "a commit from a stale base landed"
        raise _fail(msg)
    root = _expect_chain(backend, 3)
    if [c.product_id for c in _changes(root)] != ["c"]:
        msg = f"the retried version lists changes to {[c.product_id for c in _changes(root)]}, not only c"
        raise _fail(msg)


def check_created_at_is_monotone_and_a_fast_base_is_refused(new: Callable[[], HistoryBackend]) -> None:
    """``created_at`` steps past the base's, across an epoch change, and a base 5 minutes ahead is refused."""
    backend = new()
    ahead = _T0 + 4 * _MINUTE
    first = backend.commit_at(None, {"a": _sealed(None, "a", _edition(1), None)}, now=ahead)
    try:
        backend.commit_at(first, {"b": _sealed(None, "b", _edition(2), None)}, now=_T0 - 2 * _MINUTE)
    except ConflictError as exc:
        msg = "a base 6 minutes ahead raised a conflict, not a refusal"
        raise _fail(msg) from exc
    except InventoryError as exc:
        if ahead.isoformat()[:19] not in str(exc):
            msg = f"the refusal does not name the base version's time: {exc}"
            raise _fail(msg) from exc
    else:
        msg = "a base version 6 minutes ahead of the writer's clock was accepted"
        raise _fail(msg)
    backend.commit_at(first, {"b": _sealed(None, "b", _edition(2), None)}, now=_T0)
    second = _expect_chain(backend, 2)
    if second.created_at != ahead + timedelta(milliseconds=1):
        msg = f"version 2 was created at {second.created_at.isoformat()}, not one millisecond after version 1"
        raise _fail(msg)
    backend.restore_with_new_epoch(2)
    restored = _expect_chain(backend, 3)
    if restored.restore_epoch == second.restore_epoch or restored.created_at <= second.created_at:
        msg = "the first version of the new epoch is not created after the version it follows"
        raise _fail(msg)


type _Marks = dict[str, tuple[str | None, str | None]]


def check_the_change_list_records_each_kind_of_change(new: Callable[[], HistoryBackend]) -> None:
    """An entry that keeps its state, a release, a retire, a reinstate and a dropped tombstone (spec §6.3)."""
    backend = new()
    one, two = _edition(1), _edition(2)
    leaf = _sealed(_sealed(None, "p", one, None), "p", two, one, embargo=True, at=1)
    later = _T0 + timedelta(days=1)
    rescheduled = msgspec.structs.replace(
        leaf,
        editions=tuple(
            msgspec.structs.replace(e, release_at=later) if e.edition_id == two else e for e in leaf.editions
        ),
    )
    released = release(rescheduled, two, now=later, explicit=True)
    kept, stones = retire(released, [one], now=later)
    back, remaining = reinstate(
        kept,
        stones,
        edition_id=one,
        previous=None,
        manifest_sha256="0" * 64,
        schema_version=SCHEMA_VERSION,
        now=later,
    )
    again, stones_again = retire(back, [one], now=later)
    # Each: the product's next leaf (None: unchanged), the tombstones, the kind, the expected marks.
    steps: list[tuple[Leaf | None, tuple[Tombstone, ...] | None, EventKind, _Marks]] = [
        (rescheduled, None, "embargo-change", {two: ("embargoed", "embargoed")}),
        (released, None, "release", {one: ("live", "superseded"), two: ("embargoed", "live")}),
        (kept, stones, "retire", {one: ("superseded", "tombstone")}),
        (back, remaining, "reinstate", {one: ("tombstone", "superseded")}),
        (again, stones_again, "retire", {one: ("superseded", "tombstone")}),
        (None, (), "correct", {one: ("tombstone", None)}),
    ]
    snapshot = backend.commit_at(None, {"p": leaf}, now=_T0)
    for n, (next_leaf, retired, kind, expected) in enumerate(steps, start=1):
        leaves = {} if next_leaf is None else {"p": next_leaf}
        snapshot = backend.commit_at(snapshot, leaves, now=_T0 + n * _MINUTE, retired=retired, kind=kind)
        changed = _changes(_expect_chain(backend, snapshot.seq))
        items = [e for c in changed for e in c.entries]
        marks: _Marks = {e.edition_id: (e.from_, e.to) for e in items}
        if marks != expected:
            msg = f"the {kind} version's entry items are {marks}, not {expected}"
            raise _fail(msg)
        for item in items:
            if isinstance(item.entry, UnsetType) == (item.to not in {None, "tombstone"}):
                msg = f"the {kind} version's item for {item.edition_id} carries its entry wrongly"
                raise _fail(msg)
        hashes = changed[0].leaf_sha256
        if (hashes.from_ == hashes.to) != (next_leaf is None):
            msg = f"the {kind} version's leaf hashes are {hashes.from_} and {hashes.to}"
            raise _fail(msg)


def check_a_writer_without_stage_2_support_refuses_an_opted_in_catalog(new: Callable[[], HistoryBackend]) -> None:
    """A writer that takes no catalog lock refuses ``catalog.json`` 2.x (spec §6.1)."""
    backend = new()
    if backend.stage_2:
        return
    base = backend.commit_at(None, {"a": _sealed(None, "a", _edition(1), None)}, now=_T0)
    raw = backend.fetch(CATALOG_NAME)
    if raw is None:
        msg = f"the backend wrote no {CATALOG_NAME}"
        raise _fail(msg)
    catalog = decode_catalog(raw)
    opted_in = msgspec.structs.replace(
        catalog, format_version=OPTED_IN_CATALOG_FORMAT, history=CatalogHistory(since_seq=1)
    )
    history = History(
        format_version=HISTORY_FORMAT,
        catalog_id=catalog.catalog_id,
        restore_epoch=catalog.restore_epoch,
        expired_through=0,
        newest_segment=None,
        newest_segment_sha256=None,
        cleaned_through=None,
    )
    backend.put(HISTORY_NAME, encode(history))
    backend.put(CATALOG_NAME, encode(opted_in))
    try:
        backend.commit_at(base, {"b": _sealed(None, "b", _edition(2), None)}, now=_T0 + _MINUTE)
    except InventoryError:
        return
    msg = "a writer without stage-2 support wrote to an opted-in catalog"
    raise _fail(msg)


#: Every history check, in the order `run_history` calls them. The cases of spec §6.9
#: for expiry, restores and ``verify-catalog`` arrive with stage 2.
HISTORY_CHECKS = (
    check_a_correction_links_and_diffs_against_the_version_before_it,
    check_a_lost_race_rebuilds_its_history_fields,
    check_created_at_is_monotone_and_a_fast_base_is_refused,
    check_the_change_list_records_each_kind_of_change,
    check_a_writer_without_stage_2_support_refuses_an_opted_in_catalog,
)


def run_history(new: Callable[[], HistoryBackend]) -> list[str]:
    """Run every history check against fresh backends from `new`.

    Returns:
        The names of the checks run, all passed.

    Raises:
        AssertionError: From the first check that fails, naming it.
    """
    ran: list[str] = []
    for check in HISTORY_CHECKS:
        try:
            check(new)
        except AssertionError as exc:
            msg = f"{check.__name__}: {exc}"
            raise AssertionError(msg) from exc
        ran.append(check.__name__)
    return ran
