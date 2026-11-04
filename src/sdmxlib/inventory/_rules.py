"""The Inventory's state transitions, as pure functions a writer calls before it publishes a version (GH-251).

Each takes the current leaf of one product and returns the leaf the next
version holds, or raises `TransitionError` naming the rule. Nothing here reads or
writes storage: statcatlib's writer fetches the current version, applies
these, writes the new leaves and root by exclusive create, and on losing that
race re-reads the head and calls them again (spec §6.6).

The rules are the sharded catalog spec's §6.4:

- **The head** of a product is its ``live`` entry at a ``schema_version``.
- **Fast-forward**: an edition becomes ``live`` only when its ``previous`` is
  the live edition at its schema. A product with no live edition at that
  schema takes an edition built on nothing, or on its live edition at another
  schema, which is how a schema rollout's first edition is built (§9.2).
- **Rollback is conditional**: it names the live edition it expects
  (``if_live``) and is refused when another has gone live since.
- **Retirement is a tombstone**, and a tombstoned edition is never recorded
  again except by `reinstate`.

A writer that writes the history fields (§6.2, §6.6) builds them from the
base version, root ``seq - 1``, and its leaves: `change_list` diffs the two
versions, `next_root` hashes the base's stored bytes, and `next_created_at`
keeps ``created_at`` monotone and refuses a base from a fast clock.
"""

from collections.abc import Collection, Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Literal

from msgspec import UNSET, UnsetType

from sdmxlib.inventory._format import (
    INVENTORY_FORMAT,
    ChangeItem,
    EntryItem,
    EntryRecord,
    Event,
    InventoryError,
    Kind,
    Leaf,
    LeafEntry,
    LeafHashes,
    LeafRef,
    Lifecycle,
    Marker,
    PartialRecord,
    Root,
    State,
    StructureSource,
    Tombstone,
    check_event_kind,
    decode_root,
    encode,
    leaf_path,
    sha256_hex,
    validate_leaf,
    validate_root,
)
from sdmxlib.storage._manifest import JsonValue, Producer

type Rule = Literal["fast-forward", "if-live", "not-due", "state", "tombstone", "duplicate", "unknown", "kind"]
"""The rule a refused transition breaks: `TransitionError.rule`.

- ``fast-forward``: the edition is not built on the live edition at its
  schema, or on the same base as the edition it supersedes.
- ``if-live``: the live edition is not the one a rollback names.
- ``not-due``: an embargoed edition's ``release_at`` has not passed.
- ``state``: the entry is not in a state the operation moves from.
- ``tombstone``: the edition is retired, or `reinstate` names one that is not.
- ``duplicate``: the edition is recorded already.
- ``unknown``: the product records no such edition.
- ``kind``: the product's kind differs from the one recorded.
"""


class TransitionError(InventoryError):
    """A transition the rules refuse.

    Attributes:
        rule: Which rule it breaks.
    """

    def __init__(self, message: str, *, rule: Rule) -> None:
        """Say why, and name the rule."""
        self.rule: Rule = rule
        super().__init__(message)


def _aware(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        msg = f"{now!r} names no time zone; the Inventory records UTC instants, from the storage server's clock"
        raise ValueError(msg)
    return now


def head(leaf: Leaf | None, schema_version: str) -> LeafEntry | None:
    """The product's live entry at `schema_version`, or None when it has none."""
    if leaf is None:
        return None
    return next((e for e in leaf.editions if e.state == "live" and e.schema_version == schema_version), None)


def _entry(leaf: Leaf, edition_id: str) -> LeafEntry:
    found = next((e for e in leaf.editions if e.edition_id == edition_id), None)
    if found is None:
        msg = f"{leaf.product_id} records no edition {edition_id}"
        raise TransitionError(msg, rule="unknown")
    return found


def _with(leaf: Leaf, changed: Iterable[LeafEntry]) -> Leaf:
    """`leaf` with each of `changed` replacing the entry of its id, or added."""
    entries = {e.edition_id: e for e in leaf.editions}
    entries |= {e.edition_id: e for e in changed}
    result = Leaf(
        format_version=INVENTORY_FORMAT,
        product_id=leaf.product_id,
        kind=leaf.kind,
        lifecycle=leaf.lifecycle,
        editions=tuple(entries[k] for k in sorted(entries)),
    )
    validate_leaf(result)
    return result


def _moved(entry: LeafEntry, state: State, now: datetime) -> LeafEntry:
    return LeafEntry(
        edition_id=entry.edition_id,
        previous=entry.previous,
        state=state,
        manifest_sha256=entry.manifest_sha256,
        schema_version=entry.schema_version,
        state_changed_at=now,
        release_at=None,
        structure_source=entry.structure_source,
        artefacts=entry.artefacts,
        dataflows=entry.dataflows,
        pull_bytes=entry.pull_bytes,
    )


def _routing(
    dataflows: Iterable[str] | None, pull_bytes: int | None
) -> tuple[tuple[str, ...] | UnsetType, int | UnsetType]:
    """The routing fields a new entry carries: both, sorted and unique, or neither (spec §6.3)."""
    if dataflows is None and pull_bytes is None:
        return UNSET, UNSET
    if dataflows is None or pull_bytes is None:
        msg = "dataflows and pull_bytes are the routing fields; pass both or neither (spec §6.3)"
        raise ValueError(msg)
    return tuple(sorted(set(dataflows))), pull_bytes


def _fast_forward(leaf: Leaf, edition_id: str, previous: str | None, schema_version: str) -> LeafEntry | None:
    """The live entry `edition_id` replaces at its schema, or None; refused when it is not built on it."""
    live = head(leaf, schema_version)
    if live is not None:
        if previous != live.edition_id:
            msg = (
                f"{leaf.product_id}: {edition_id} is built on {previous}, but the live edition at schema "
                f"{schema_version} is {live.edition_id}. Discard it, then build from {live.edition_id}."
            )
            raise TransitionError(msg, rule="fast-forward")
        return live
    others = {e.edition_id for e in leaf.editions if e.state == "live"}
    if previous is not None and previous not in others:
        base = next((e for e in leaf.editions if e.edition_id == previous), None)
        state = "not recorded" if base is None else base.state
        msg = (
            f"{leaf.product_id}: {edition_id} is built on {previous}, which is {state}; with no live edition at "
            f"schema {schema_version} it must be built on nothing or on a live edition at another schema"
        )
        raise TransitionError(msg, rule="fast-forward")
    return None


def record(
    leaf: Leaf | None,
    *,
    product_id: str,
    kind: Kind,
    lifecycle: Lifecycle,
    edition_id: str,
    previous: str | None,
    manifest_sha256: str,
    schema_version: str,
    embargo: bool,
    now: datetime,
    release_at: datetime | None = None,
    retired: Collection[str] = (),
    structure_source: StructureSource | None = None,
    artefacts: Mapping[str, PartialRecord] | None = None,
    dataflows: Iterable[str] | None = None,
    pull_bytes: int | None = None,
) -> Leaf:
    """Record a sealed edition: ``live`` when embargo is off, ``embargoed`` when it is on.

    A live one supersedes the live edition it was built on, in the same leaf.

    Args:
        leaf: The product's current leaf, or None for its first edition.
        product_id: The product.
        kind: The product's kind; a recorded product keeps its kind.
        lifecycle: The product's lifecycle, which this version records.
        edition_id: The edition, as its manifest names it.
        previous: Its manifest's ``previous``.
        manifest_sha256: The SHA-256 of its ``manifest.json``.
        schema_version: Its manifest's ``schema_version``.
        embargo: The root's ``embargo``.
        now: The storage server's time, time-zone aware.
        release_at: For an embargoed edition, when it may be released.
        retired: The edition ids the root tombstones for this product.
        structure_source: Where its structures came from.
        artefacts: Its manifest's artefact records (`routing_fields`); a
            writer passes them for every structure edition.
        dataflows: A routing field: its manifest's binding URNs, which the
            entry records sorted and without duplicates.
        pull_bytes: A routing field: the size of its manifest's required
            tier. Pass both routing fields or neither.

    Returns:
        The product's next leaf.

    Raises:
        TransitionError: If the edition is recorded already (``duplicate``) or
            tombstoned (``tombstone``), the product is another kind
            (``kind``), or a live edition is not built on the live one
            (``fast-forward``; an embargoed one is checked at release).
        ValueError: If `now` or `release_at` names no time zone, or one
            routing field is passed without the other.
        InventoryError: If `pull_bytes` is negative.
    """
    now = _aware(now)
    routed, pulled = _routing(dataflows, pull_bytes)
    if release_at is not None:
        _aware(release_at)
    current = leaf or Leaf(
        format_version=INVENTORY_FORMAT, product_id=product_id, kind=kind, lifecycle=lifecycle, editions=()
    )
    if current.kind != kind:
        msg = f"{product_id} is a {current.kind} product; {edition_id} would record it as {kind}"
        raise TransitionError(msg, rule="kind")
    if any(e.edition_id == edition_id for e in current.editions):
        msg = f"{product_id} records {edition_id} already"
        raise TransitionError(msg, rule="duplicate")
    if edition_id in retired:
        msg = f"{product_id}'s {edition_id} is tombstoned; only reinstate records it again"
        raise TransitionError(msg, rule="tombstone")
    entry = LeafEntry(
        edition_id=edition_id,
        previous=previous,
        state="embargoed" if embargo else "live",
        manifest_sha256=manifest_sha256,
        schema_version=schema_version,
        state_changed_at=now,
        release_at=release_at if embargo else None,
        structure_source=structure_source,
        artefacts=dict(artefacts) if artefacts is not None else None,
        dataflows=routed,
        pull_bytes=pulled,
    )
    changed = [entry]
    if not embargo:
        replaced = _fast_forward(current, edition_id, previous, schema_version)
        if replaced is not None:
            changed.append(_moved(replaced, "superseded", now))
    relabelled = Leaf(
        format_version=INVENTORY_FORMAT,
        product_id=product_id,
        kind=kind,
        lifecycle=lifecycle,
        editions=current.editions,
    )
    return _with(relabelled, changed)


def release(leaf: Leaf, edition_id: str, *, now: datetime, explicit: bool = False) -> Leaf:
    """Make an embargoed edition live, superseding the live one at its schema.

    Args:
        leaf: The product's current leaf.
        edition_id: The embargoed edition.
        now: The storage server's time, time-zone aware.
        explicit: Release now whatever its ``release_at``, for a manual
            release; otherwise it must be due.

    Returns:
        The product's next leaf.

    Raises:
        TransitionError: If the edition is not recorded (``unknown``) or not
            embargoed (``state``), is not due (``not-due``), or is not built
            on the live edition (``fast-forward``).
        ValueError: If `now` names no time zone.
    """
    now = _aware(now)
    entry = _entry(leaf, edition_id)
    if entry.state != "embargoed":
        msg = f"{leaf.product_id}'s {edition_id} is {entry.state}, not embargoed"
        raise TransitionError(msg, rule="state")
    if not explicit and (entry.release_at is None or entry.release_at > now):
        when = "no release time" if entry.release_at is None else f"release_at {entry.release_at.isoformat()}"
        msg = f"{leaf.product_id}'s {edition_id} has {when}; it is not due at {now.isoformat()}"
        raise TransitionError(msg, rule="not-due")
    replaced = _fast_forward(leaf, edition_id, entry.previous, entry.schema_version)
    changed = [_moved(entry, "live", now)]
    if replaced is not None:
        changed.append(_moved(replaced, "superseded", now))
    return _with(leaf, changed)


def due(leaf: Leaf, now: datetime) -> list[str]:
    """The embargoed editions whose ``release_at`` has come at `now`, in id order."""
    now = _aware(now)
    return [
        e.edition_id
        for e in leaf.editions
        if e.state == "embargoed" and e.release_at is not None and e.release_at <= now
    ]


def release_due(leaves: Mapping[str, Leaf], now: datetime) -> tuple[dict[str, Leaf], dict[str, str]]:
    """Release every due edition it can, product by product, and report the rest.

    A refusal in one product never stops another's release (spec §6.4).

    Args:
        leaves: The current leaves, by product.
        now: The storage server's time, time-zone aware.

    Returns:
        The changed leaves by product, and each refused edition's reason by
        ``<product_id>/<edition_id>``.
    """
    changed: dict[str, Leaf] = {}
    refused: dict[str, str] = {}
    for product_id, leaf in sorted(leaves.items()):
        current = leaf
        for edition_id in due(leaf, now):
            try:
                current = release(current, edition_id, now=now)
            except TransitionError as exc:
                refused[f"{product_id}/{edition_id}"] = str(exc)
        if current is not leaf:
            changed[product_id] = current
    return changed, refused


def cancel(leaf: Leaf, edition_id: str, *, now: datetime) -> Leaf:
    """Withdraw an embargoed edition before its release.

    Raises:
        TransitionError: If it is not recorded (``unknown``) or not embargoed (``state``).
        ValueError: If `now` names no time zone.
    """
    now = _aware(now)
    entry = _entry(leaf, edition_id)
    if entry.state != "embargoed":
        msg = f"{leaf.product_id}'s {edition_id} is {entry.state}; only an embargoed edition is cancelled"
        raise TransitionError(msg, rule="state")
    return _with(leaf, [_moved(entry, "withdrawn", now)])


def supersede_pending(leaf: Leaf, edition_id: str, *, by: str, now: datetime) -> Leaf:
    """Withdraw an embargoed edition in favour of another embargoed one built on the same live edition.

    A correction made while an edition is under embargo: `by` is recorded
    first, embargoed, and this withdraws the edition it corrects.

    Raises:
        TransitionError: If either is not recorded (``unknown``), either is not
            embargoed (``state``), or they are built on different editions
            (``fast-forward``).
        ValueError: If `now` names no time zone.
    """
    now = _aware(now)
    old, new = _entry(leaf, edition_id), _entry(leaf, by)
    for entry in (old, new):
        if entry.state != "embargoed":
            msg = f"{leaf.product_id}'s {entry.edition_id} is {entry.state}, not embargoed"
            raise TransitionError(msg, rule="state")
    if old.previous != new.previous or old.schema_version != new.schema_version:
        msg = (
            f"{leaf.product_id}: {by} is built on {new.previous} at schema {new.schema_version}, "
            f"and {edition_id} on {old.previous} at {old.schema_version}"
        )
        raise TransitionError(msg, rule="fast-forward")
    return _with(leaf, [_moved(old, "withdrawn", now)])


def rollback(leaf: Leaf, *, schema_version: str, if_live: str, to: str | None = None, now: datetime) -> Leaf:
    """Withdraw the live edition and make a superseded one live again.

    Args:
        leaf: The product's current leaf.
        schema_version: The schema whose head moves.
        if_live: The edition the caller expects to be live; by convention
            the one live when the decision was made.
        to: The superseded edition to make live; by default the most
            recent superseded one at this schema.
        now: The storage server's time, time-zone aware.

    Returns:
        The product's next leaf.

    Raises:
        TransitionError: If the live edition is not `if_live` (``if-live``), or
            `to` is not a superseded edition at this schema (``state``,
            ``unknown``).
        ValueError: If `now` names no time zone.
    """
    now = _aware(now)
    live = head(leaf, schema_version)
    if live is None or live.edition_id != if_live:
        found = "none" if live is None else live.edition_id
        msg = (
            f"{leaf.product_id}: rollback expects {if_live} live at schema {schema_version}, "
            f"and the live one is {found}"
        )
        raise TransitionError(msg, rule="if-live")
    candidates = [e for e in leaf.editions if e.state == "superseded" and e.schema_version == schema_version]
    if to is None:
        if not candidates:
            msg = f"{leaf.product_id} has no superseded edition at schema {schema_version} to roll back to"
            raise TransitionError(msg, rule="state")
        target = candidates[-1]
    else:
        target = _entry(leaf, to)
        if target not in candidates:
            msg = (
                f"{leaf.product_id}'s {to} is {target.state} at schema {target.schema_version}; "
                f"rollback needs a superseded one at {schema_version}"
            )
            raise TransitionError(msg, rule="state")
    return _with(leaf, [_moved(live, "withdrawn", now), _moved(target, "live", now)])


def retire(leaf: Leaf, edition_ids: Iterable[str], *, now: datetime) -> tuple[Leaf, tuple[Tombstone, ...]]:
    """Drop superseded or withdrawn editions from the leaf, returning their tombstones.

    Raises:
        TransitionError: If one is not recorded (``unknown``), or is live or embargoed (``state``).
        ValueError: If `now` names no time zone.
    """
    now = _aware(now)
    gone = []
    for edition_id in edition_ids:
        entry = _entry(leaf, edition_id)
        if entry.state not in {"superseded", "withdrawn"}:
            msg = f"{leaf.product_id}'s {edition_id} is {entry.state}; only a superseded or withdrawn edition retires"
            raise TransitionError(msg, rule="state")
        gone.append(edition_id)
    kept = tuple(e for e in leaf.editions if e.edition_id not in gone)
    stones = tuple(Tombstone(product_id=leaf.product_id, edition_id=e, retired_at=now) for e in sorted(gone))
    return Leaf(
        format_version=INVENTORY_FORMAT,
        product_id=leaf.product_id,
        kind=leaf.kind,
        lifecycle=leaf.lifecycle,
        editions=kept,
    ), stones


def reinstate(
    leaf: Leaf,
    retired: Iterable[Tombstone],
    *,
    edition_id: str,
    previous: str | None,
    manifest_sha256: str,
    schema_version: str,
    now: datetime,
    artefacts: Mapping[str, PartialRecord] | None = None,
    dataflows: Iterable[str] | None = None,
    pull_bytes: int | None = None,
) -> tuple[Leaf, tuple[Tombstone, ...]]:
    """Enter a tombstoned edition again as ``superseded``, so a rollback can make it live.

    The writer verifies its manifest and every listed file before it calls
    this (spec §6.4).

    Args:
        leaf: The product's current leaf.
        retired: The current root's tombstones.
        edition_id: The tombstoned edition.
        previous: Its manifest's ``previous``.
        manifest_sha256: The SHA-256 of its ``manifest.json``.
        schema_version: Its manifest's ``schema_version``.
        now: The storage server's time, time-zone aware.
        artefacts: Its manifest's artefact records, so the reinstated entry
            keeps them (spec §6.3).
        dataflows: A routing field, as `record` takes it.
        pull_bytes: A routing field, as `record` takes it. Pass both routing
            fields or neither.

    Returns:
        The product's next leaf, and the tombstones the next root keeps.

    Raises:
        TransitionError: If the edition is not tombstoned for this product (``tombstone``).
        ValueError: If `now` names no time zone, or one routing field is
            passed without the other.
        InventoryError: If `pull_bytes` is negative.
    """
    now = _aware(now)
    routed, pulled = _routing(dataflows, pull_bytes)
    stones = tuple(retired)
    if not any(t.product_id == leaf.product_id and t.edition_id == edition_id for t in stones):
        msg = f"{leaf.product_id}'s {edition_id} is not tombstoned; reinstate restores only a retired edition"
        raise TransitionError(msg, rule="tombstone")
    entry = LeafEntry(
        edition_id=edition_id,
        previous=previous,
        state="superseded",
        manifest_sha256=manifest_sha256,
        schema_version=schema_version,
        state_changed_at=now,
        artefacts=dict(artefacts) if artefacts is not None else None,
        dataflows=routed,
        pull_bytes=pulled,
    )
    kept = tuple(t for t in stones if not (t.product_id == leaf.product_id and t.edition_id == edition_id))
    return _with(leaf, [entry]), kept


#: How far ahead of a writer's clock a base version's ``created_at`` may be (spec §6.6).
MAX_BASE_AHEAD = timedelta(minutes=5)

#: The least step from a base version's ``created_at`` to the next version's.
CREATED_AT_STEP = timedelta(milliseconds=1)


def next_created_at(base: Root | None, now: datetime) -> datetime:
    """The ``created_at`` of the version after `base`, written at the writer's time `now` (spec §6.6).

    It is at least the base version's ``created_at`` plus one millisecond,
    across a ``restore_epoch`` change too, so a writer whose clock reads
    earlier writes that instant.

    Raises:
        InventoryError: If the base version's ``created_at`` is more than
            `MAX_BASE_AHEAD` ahead of `now`, naming its time, so one fast
            clock cannot pin ``created_at`` for every later writer.
        ValueError: If `now` names no time zone.
    """
    now = _aware(now)
    if base is None:
        return now
    if base.created_at > now + MAX_BASE_AHEAD:
        msg = (
            f"Inventory version {base.seq} was created at {base.created_at.isoformat()}, more than "
            f"{MAX_BASE_AHEAD} ahead of this writer's clock ({now.isoformat()}); fix the clock that wrote it "
            "or this one"
        )
        raise InventoryError(msg)
    return max(now, base.created_at + CREATED_AT_STEP)


def entry_record(entry: LeafEntry) -> EntryRecord:
    """A change item's copy of `entry`: its seven required fields (spec §6.3)."""
    return EntryRecord(
        edition_id=entry.edition_id,
        previous=entry.previous,
        state=entry.state,
        manifest_sha256=entry.manifest_sha256,
        schema_version=entry.schema_version,
        state_changed_at=entry.state_changed_at,
        release_at=entry.release_at,
    )


def _leaf_for(leaves: Mapping[str, Leaf], product_id: str, ref: LeafRef | None, which: str) -> Leaf | None:
    if ref is None:
        return None
    leaf = leaves.get(product_id)
    if leaf is None:
        msg = f"{product_id}'s leaf in the {which} version is needed for the change list and was not given"
        raise InventoryError(msg)
    if leaf_ref(leaf)[1] != ref:
        msg = f"{product_id}'s leaf given for the {which} version does not hash to {ref.leaf_sha256}"
        raise InventoryError(msg)
    return leaf


def _marks(leaf: Leaf | None, stones: Mapping[str, Tombstone]) -> dict[str, tuple[Marker, LeafEntry | Tombstone]]:
    marks: dict[str, tuple[Marker, LeafEntry | Tombstone]] = {}
    for entry in leaf.editions if leaf is not None else ():
        marks[entry.edition_id] = (entry.state, entry)
    for edition_id, stone in stones.items():
        marks[edition_id] = ("tombstone", stone)
    return marks


def change_list(
    base: Root | None,
    base_leaves: Mapping[str, Leaf],
    *,
    products: Mapping[str, LeafRef],
    leaves: Mapping[str, Leaf],
    retired: Iterable[Tombstone],
) -> tuple[ChangeItem, ...]:
    """The change list of the version after `base`: one item per product whose leaf or tombstones differ (spec §6.3).

    It reads the base version, root ``seq - 1``, and no other, whatever
    version the new one was built from: a correction built from an older
    version still diffs against ``seq - 1`` (spec §6.2).

    Args:
        base: The base version, root ``seq - 1``, or None for version 1.
        base_leaves: The base version's leaves by product: at least each
            product whose leaf or tombstones change.
        products: Every product's leaf pointer in the new version.
        leaves: The new version's leaves by product: at least each product
            whose leaf or tombstones change.
        retired: The new version's tombstones.

    Returns:
        The items, sorted by ``product_id``, each listing its editions sorted
        by ``edition_id``.

    Raises:
        InventoryError: If a leaf the list needs is missing from `base_leaves`
            or `leaves`, or does not hash to the pointer its version holds.
    """
    before = base.products if base is not None else {}
    stones_before: dict[str, dict[str, Tombstone]] = {}
    for stone in base.retired if base is not None else ():
        stones_before.setdefault(stone.product_id, {})[stone.edition_id] = stone
    stones_after: dict[str, dict[str, Tombstone]] = {}
    for stone in retired:
        stones_after.setdefault(stone.product_id, {})[stone.edition_id] = stone
    items: list[ChangeItem] = []
    for product_id in sorted({*before, *products, *stones_before, *stones_after}):
        old_ref, new_ref = before.get(product_id), products.get(product_id)
        old_stones, new_stones = stones_before.get(product_id, {}), stones_after.get(product_id, {})
        if old_ref == new_ref and old_stones == new_stones:
            continue
        old_leaf = _leaf_for(base_leaves, product_id, old_ref, "base")
        new_leaf = old_leaf if old_ref == new_ref else _leaf_for(leaves, product_id, new_ref, "new")
        old_marks, new_marks = _marks(old_leaf, old_stones), _marks(new_leaf, new_stones)
        entries: list[EntryItem] = []
        for edition_id in sorted({*old_marks, *new_marks}):
            old, new = old_marks.get(edition_id), new_marks.get(edition_id)
            if old == new:
                continue
            to = None if new is None else new[0]
            record_of = new[1] if new is not None and isinstance(new[1], LeafEntry) else None
            entries.append(
                EntryItem(
                    edition_id=edition_id,
                    from_=None if old is None else old[0],
                    to=to,
                    entry=entry_record(record_of) if record_of is not None else UNSET,
                )
            )
        items.append(
            ChangeItem(
                product_id=product_id,
                leaf_sha256=LeafHashes(
                    from_=None if old_ref is None else old_ref.leaf_sha256,
                    to=None if new_ref is None else new_ref.leaf_sha256,
                ),
                entries=tuple(entries),
            )
        )
    return tuple(items)


def leaf_ref(leaf: Leaf) -> tuple[bytes, LeafRef]:
    """The bytes a writer stores for `leaf`, and the root's pointer to them."""
    data = encode(leaf)
    sha = sha256_hex(data)
    return data, LeafRef(leaf=leaf_path(sha), leaf_sha256=sha)


def next_root(
    previous: Root | None,
    *,
    products: Mapping[str, LeafRef],
    event: Event,
    producer: Producer,
    now: datetime,
    retired: Iterable[Tombstone] | None = None,
    embargo: bool | None = None,
    restore_epoch: int | None = None,
    built_from: int | None = None,
    extensions: Mapping[str, Mapping[str, JsonValue]] | None = None,
    previous_bytes: bytes | UnsetType | None = UNSET,
    changes: Sequence[ChangeItem] | UnsetType = UNSET,
) -> Root:
    """The next Inventory version's root.

    A writer that writes the history fields passes `previous_bytes` and
    `changes` together (spec §6.2): the root then carries the SHA-256 of
    `previous_bytes` and `changes`, and its ``created_at`` follows
    `next_created_at`. Without them the root carries neither field and
    ``created_at`` is `now`.

    Args:
        previous: The newest version, root ``seq - 1``, or None for a
            catalog's first. It is the base version of the history fields.
        products: Every product's leaf pointer in the new version.
        event: Who acts, and why.
        producer: The writing library.
        now: The writer's time, time-zone aware.
        retired: The tombstones; by default `previous`'s.
        embargo: The embargo setting; by default `previous`'s, else False.
        restore_epoch: By default `previous`'s, else 0.
        built_from: The version this one is built from, for a correction
            from an older, known-good one; by default `previous`. The
            history fields still follow `previous`.
        extensions: By default `previous`'s.
        previous_bytes: The stored bytes of `previous`, or None for a
            catalog's first version.
        changes: The change list against `previous` (`change_list`).

    Returns:
        The root, numbered ``previous.seq + 1``.

    Raises:
        InventoryError: If ``event.kind`` is not an `EventKind`; if only one
            of `previous_bytes` and `changes` is given, or `previous_bytes`
            are not `previous`'s; if the change list breaks a rule
            (`validate_root`); or if `previous` was created more than
            `MAX_BASE_AHEAD` ahead of `now`.
        ValueError: If `now` names no time zone.
    """
    now = _aware(now)
    seq = 1 if previous is None else previous.seq + 1
    check_event_kind(event, seq)
    history = not isinstance(previous_bytes, UnsetType)
    if history == isinstance(changes, UnsetType):
        msg = f"Inventory version {seq}: previous_bytes and changes are the history fields, given both or neither"
        raise InventoryError(msg)
    previous_root_sha256: str | UnsetType | None = UNSET
    if isinstance(previous_bytes, bytes):
        if previous is None or decode_root(previous_bytes) != previous:
            msg = f"Inventory version {seq}: the previous_bytes given are not version {seq - 1}'s root"
            raise InventoryError(msg)
        previous_root_sha256 = sha256_hex(previous_bytes)
    elif previous_bytes is None:
        if previous is not None:
            msg = f"Inventory version {seq}: previous_bytes is None, and version {seq - 1} exists"
            raise InventoryError(msg)
        previous_root_sha256 = None
    root = Root(
        format_version=INVENTORY_FORMAT,
        seq=seq,
        previous_inventory=built_from if built_from is not None else (None if previous is None else previous.seq),
        restore_epoch=restore_epoch
        if restore_epoch is not None
        else (0 if previous is None else previous.restore_epoch),
        created_at=next_created_at(previous, now) if history else now,
        producer=producer,
        embargo=embargo if embargo is not None else (previous.embargo if previous is not None else False),
        products=dict(sorted(products.items())),
        retired=tuple(
            sorted(
                retired if retired is not None else (previous.retired if previous is not None else ()),
                key=lambda t: (t.product_id, t.edition_id),
            )
        ),
        event=event,
        extensions={
            k: dict(v) for k, v in (extensions or (previous.extensions if previous is not None else {})).items()
        },
        previous_root_sha256=previous_root_sha256,
        changes=UNSET if isinstance(changes, UnsetType) else tuple(changes),
    )
    validate_root(root)
    return root


def repairable(
    journal: Iterable[tuple[str, str]], leaves: Mapping[str, Leaf], retired: Iterable[Tombstone]
) -> list[tuple[str, str]]:
    """The journalled seals a writer may record: those the Inventory neither names nor tombstones.

    The narrow repair of spec §6.7. A seal writes a journal entry before its
    manifest and the writer removes it once the edition is recorded, so an
    entry left behind is an edition this writer sealed and may never have
    recorded. One the newest version names, in any state, was recorded; one it
    tombstones was retired. Neither is recorded again, so a withdrawn edition
    is never re-released by repair.

    Args:
        journal: ``(product_id, edition_id)`` for each journal entry left behind.
        leaves: The newest version's leaves, by product.
        retired: The newest version's tombstones.

    Returns:
        The entries to record, in the order given.
    """
    named = {(p, e.edition_id) for p, leaf in leaves.items() for e in leaf.editions}
    stones = {(t.product_id, t.edition_id) for t in retired}
    return [entry for entry in journal if entry not in named and entry not in stones]
