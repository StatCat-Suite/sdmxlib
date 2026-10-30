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
"""

from collections.abc import Collection, Iterable, Mapping
from datetime import datetime
from typing import Literal

from sdmxlib.inventory._format import (
    INVENTORY_FORMAT,
    Event,
    InventoryError,
    Kind,
    Leaf,
    LeafEntry,
    LeafRef,
    Lifecycle,
    PartialRecord,
    Root,
    State,
    StructureSource,
    Tombstone,
    encode,
    leaf_path,
    sha256_hex,
    validate_leaf,
)
from sdmxlib.storage._manifest import JsonValue, Producer

#: The rule a refused transition breaks.
type Rule = Literal["fast-forward", "if-live", "not-due", "state", "tombstone", "duplicate", "unknown", "kind"]


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
    )


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
        artefacts: Its manifest's artefacts and partials.

    Returns:
        The product's next leaf.

    Raises:
        TransitionError: If the edition is recorded already (``duplicate``) or
            tombstoned (``tombstone``), the product is another kind
            (``kind``), or a live edition is not built on the live one
            (``fast-forward``; an embargoed one is checked at release).
        ValueError: If `now` or `release_at` names no time zone.
    """
    now = _aware(now)
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
) -> tuple[Leaf, tuple[Tombstone, ...]]:
    """Enter a tombstoned edition again as ``superseded``, so a rollback can make it live.

    The writer verifies its manifest and every listed file before it calls
    this (spec §6.4).

    Returns:
        The product's next leaf, and the tombstones the next root keeps.

    Raises:
        TransitionError: If the edition is not tombstoned for this product (``tombstone``).
        ValueError: If `now` names no time zone.
    """
    now = _aware(now)
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
    )
    kept = tuple(t for t in stones if not (t.product_id == leaf.product_id and t.edition_id == edition_id))
    return _with(leaf, [entry]), kept


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
) -> Root:
    """The next Inventory version's root.

    Args:
        previous: The newest version, or None for a catalog's first.
        products: Every product's leaf pointer in the new version.
        event: Who acts, and why.
        producer: The writing library.
        now: The time written, time-zone aware.
        retired: The tombstones; by default `previous`'s.
        embargo: The embargo setting; by default `previous`'s, else False.
        restore_epoch: By default `previous`'s, else 0.
        built_from: The version this one is built from, for a correction
            from an older, known-good one; by default `previous`.
        extensions: By default `previous`'s.

    Returns:
        The root, numbered ``previous.seq + 1``.
    """
    now = _aware(now)
    seq = 1 if previous is None else previous.seq + 1
    return Root(
        format_version=INVENTORY_FORMAT,
        seq=seq,
        previous_inventory=built_from if built_from is not None else (None if previous is None else previous.seq),
        restore_epoch=restore_epoch
        if restore_epoch is not None
        else (0 if previous is None else previous.restore_epoch),
        created_at=now,
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
    )
