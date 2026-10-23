"""Annotation overlays: change display annotations without rebuilding a bundle (GH-70).

An `Overlay` is a versioned document of ordered entries. Each picks targets
with a selector, and sets, adds or removes annotations of one type on them.
Annotations only: no ids, codes, structure or data.

- `apply_overlays` applies overlays to model objects and returns changed
  copies, so the result is the same in every wire format. A server applies
  them to what it is about to serve, and keys its caches on `effective_token`.
- `fold_overlays` writes them into a bundle's store and republishes it, which
  settles a change without a rebuild from source.

**Selectors**, most specific last so it wins:

1. ``*``: every artefact and item.
2. A class name: ``Codelist``, ``Code``, ``Dimension``, …
3. ``AGENCY:ID``: a maintainable artefact of any class and version.
4. A URN: one artefact, or one item. A nested item is named by its dotted path
   (``Category=AG:CS(1.0).PARENT.CHILD``), as SDMX names it.

Within one specificity, entries apply in document order, and overlays in the
order given.
"""

import hashlib
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Literal, TypeIs, overload

import attrs
import msgspec
from attrs import AttrsInstance

from sdmxlib.bundle._write import write_manifest
from sdmxlib.local.registry import LocalRegistry
from sdmxlib.model._refshape import item_fields
from sdmxlib.model.annotations import Annotation
from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.collections import ArtefactList, ItemList, SdmxItem
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.storage._manifest import Manifest, read_manifest

#: The overlay format this build writes and reads (``1.x``).
OVERLAY_FORMAT = "1.0"

#: How an existing annotation and a `set` combine: given every annotation of
#: the entry's type the target already has, and the new one, the annotations
#: of that type it should have. The default replaces them all with the new one.
type Merge = Callable[[list[Annotation], Annotation], list[Annotation]]


class OverlayError(ValueError):
    """An overlay document that is malformed, or in a format this build cannot read."""


class OverlayAnnotation(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """The annotation a `set` or `add` writes. Its type is the entry's."""

    id: str | None = None
    title: str | None = None
    value: str | None = None
    text: dict[str, str] = msgspec.field(default_factory=dict)


class OverlayEntry(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """One change: which targets, what to do, to annotations of which type.

    Attributes:
        selector: ``*``, a class name, ``AGENCY:ID``, or a URN.
        op: ``set`` replaces the type's annotations (through a merge hook),
            ``add`` appends one, ``remove`` drops them all.
        type: The annotation type it acts on.
        annotation: What ``set`` and ``add`` write; absent for ``remove``.
        scope: A label carried through and not interpreted: a server may
            choose which entries apply to which audience.
    """

    selector: str
    op: Literal["set", "add", "remove"]
    type: str
    annotation: OverlayAnnotation | None = None
    scope: str | None = None


class Overlay(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """A versioned overlay document: an id, and its entries in order."""

    format_version: str
    id: str
    entries: tuple[OverlayEntry, ...]


def read_overlay(path: str | Path) -> Overlay:
    """An overlay document from a JSON file.

    Args:
        path: The file.

    Returns:
        The overlay.

    Raises:
        OverlayError: If it is malformed, in another major format, or has an
            entry that ``set``s or ``add``s no annotation, or removes one it gives.
    """
    raw = Path(path).read_bytes()
    try:
        overlay = msgspec.json.decode(raw, type=Overlay)
    except msgspec.MsgspecError as exc:
        msg = f"{path} is not an overlay document: {exc}"
        raise OverlayError(msg) from exc
    if overlay.format_version.partition(".")[0] != OVERLAY_FORMAT.partition(".")[0]:
        major = OVERLAY_FORMAT.partition(".")[0]
        msg = f"{path} is overlay format {overlay.format_version}; this sdmxlib reads {major}.x"
        raise OverlayError(msg)
    for n, entry in enumerate(overlay.entries):
        if (entry.annotation is None) != (entry.op == "remove"):
            msg = f"{path}: entry {n} ({entry.op} {entry.type}) " + (
                "gives no annotation" if entry.annotation is None else "removes, so gives no annotation"
            )
            raise OverlayError(msg)
    return overlay


def generate_overlay_schema() -> bytes:
    """The overlay format's JSON Schema, generated from `Overlay`, as the shipped file holds it."""
    schema = msgspec.json.schema(Overlay)
    return msgspec.json.format(msgspec.json.encode(schema), indent=2) + b"\n"


def overlay_digest(overlay: Overlay) -> str:
    """The sha256 of an overlay's canonical JSON: what `effective_token` folds in."""
    return hashlib.sha256(msgspec.json.encode(overlay, order="sorted")).hexdigest()


def effective_token(manifest: Manifest, overlays: Sequence[Overlay]) -> str:
    """What a cache keys on when `overlays` are applied to a bundle: its token, and theirs.

    The manifest's ``token`` itself when there are no overlays, so a server
    that applies none keys exactly as before. Otherwise a sha256 over the token
    and each overlay's digest in apply order, so changing, adding, removing or
    reordering an overlay changes it, and republishing the bundle does too.

    Args:
        manifest: The bundle's manifest.
        overlays: The overlays applied, in order.

    Returns:
        The token to key on.
    """
    if not overlays:
        return manifest.token
    folded = "\n".join([manifest.token, *(overlay_digest(o) for o in overlays)])
    return hashlib.sha256(folded.encode()).hexdigest()


@overload
def apply_overlays(
    target: StructureMessage, overlays: Sequence[Overlay], *, merge: Mapping[str, Merge] | None = None
) -> StructureMessage: ...


@overload
def apply_overlays[A: MaintainableArtefact](
    target: Sequence[A], overlays: Sequence[Overlay], *, merge: Mapping[str, Merge] | None = None
) -> list[A]: ...


def apply_overlays(
    target: StructureMessage | Sequence[MaintainableArtefact],
    overlays: Sequence[Overlay],
    *,
    merge: Mapping[str, Merge] | None = None,
) -> StructureMessage | list[MaintainableArtefact]:
    """`target` with `overlays` applied to its annotations: changed copies, never in place.

    Args:
        target: A `StructureMessage`, or a sequence of artefacts. An
            artefact or item no entry selects is returned as it was.
        overlays: Applied in order.
        merge: Per annotation type, how a ``set`` combines with what is there
            ([GH-19](https://github.com/StatCat-Suite/sdmxlib/issues/19)'s
            typed annotation vocabularies will supply these); replace
            by default.

    Returns:
        A `StructureMessage` for a message, a list for a sequence.
    """
    plan = _Plan(overlays, merge or {})
    if isinstance(target, StructureMessage):
        changes = {
            f.name: [plan.artefact(a) for a in value]
            for f in attrs.fields(StructureMessage)
            if isinstance(value := getattr(target, f.name), ArtefactList) and value
        }
        # `header` and `availability_constraints` named, as `gaps.without_dropped`
        # does, so the artefact lists unpacked need not suit them.
        return attrs.evolve(
            target, header=target.header, availability_constraints=target.availability_constraints, **changes
        )
    return [plan.artefact(a) for a in target]


def fold_overlays(
    path: str | Path, overlays: Sequence[Overlay], *, merge: Mapping[str, Merge] | None = None
) -> Manifest:
    """Publish a new publication of a bundle with `overlays` written into its store.

    The new publication extends the one at `path`: a sibling directory, its
    ``meta.duckdb`` copied from `path` with every artefact an entry changes
    replaced, its lake the same data files, and its manifest's fields kept
    with a new token. The publication at `path` is left as it was. After this
    the overlays are part of the bundle and need not be applied at serve time.
    Loads every artefact, so it is a build-time step.

    Args:
        path: The published publication directory.
        overlays: Applied in order.
        merge: As `apply_overlays`.

    Returns:
        The new manifest; its publication is ``<path>/../<token>/``.
    """
    root = Path(path)
    previous = read_manifest(root)
    plan = _Plan(overlays, merge or {})
    with LocalRegistry.build(root.parent, previous=previous.token) as reg:
        loaded = [a for a in reg.to_registry()._store.values() if isinstance(a, MaintainableArtefact)]
        for artefact in loaded:
            changed = plan.artefact(artefact)
            if changed is not artefact:
                reg.replace(artefact.urn, changed)
        publication = reg.publication
    return write_manifest(
        publication,
        bundle_id=previous.bundle_id,
        producer=previous.producer,
        product=dict(previous.product),
        extensions={k: dict(v) for k, v in previous.extensions.items()},
        provenance=previous.provenance,
    )


def _reaches_items(selector: str) -> bool:
    """Whether `selector` could name an item: ``*``, a class name, or a URN with an item part."""
    if selector.startswith("urn:"):
        return not selector.endswith(")")
    return ":" not in selector


def _specificity(selector: str) -> int:
    if selector == "*":
        return 0
    if selector.startswith("urn:"):
        return 3
    if ":" in selector:
        return 2
    return 1


class _Plan:
    """The entries of a list of overlays, ordered for application, and how to apply them."""

    def __init__(self, overlays: Sequence[Overlay], merge: Mapping[str, Merge]) -> None:
        ranked = [
            (_specificity(entry.selector), n, m, entry)
            for n, overlay in enumerate(overlays)
            for m, entry in enumerate(overlay.entries)
        ]
        self._entries = [entry for *_, entry in sorted(ranked, key=lambda r: r[:3])]
        self._merge = merge
        #: Whether any entry could select an item. When none can, an artefact's
        #: items are not walked: a 500,000-code codelist costs 0.28 s to walk.
        self._reaches_items = any(_reaches_items(entry.selector) for entry in self._entries)

    def artefact[A: MaintainableArtefact](self, artefact: A) -> A:
        """`artefact`, with its own annotations and its items' changed, or itself if nothing is."""
        materialize = getattr(artefact, "materialize", None)
        if callable(materialize):
            artefact = materialize()
        urn: SdmxUrn[object] = artefact.urn
        key = f"{artefact.agency_id}:{artefact.id}"
        changes: dict[str, object] = {}
        annotations = self._annotations(artefact, str(urn), artefact.sdmx_class, key)
        if annotations is not None:
            changes["annotations"] = annotations
        if self._reaches_items:
            changes |= self._items(artefact, urn, path="", top=True)
        return attrs.evolve(artefact, **changes) if changes else artefact

    def _annotations(self, owner: object, urn: str, cls: str, key: str | None) -> list[Annotation] | None:
        """The annotations `owner` should have, or None when no entry selects it."""
        names = {"*", cls, urn} if key is None else {"*", cls, urn, key}
        selected = [e for e in self._entries if e.selector in names]
        if not selected:
            return None
        current = list(getattr(owner, "annotations", ()) or ())
        for entry in selected:
            current = self._apply(entry, current)
        return current

    def _apply(self, entry: OverlayEntry, current: list[Annotation]) -> list[Annotation]:
        kept = [a for a in current if a.type != entry.type]
        if entry.op == "remove" or entry.annotation is None:
            return kept
        new = Annotation(
            id=entry.annotation.id,
            title=entry.annotation.title,
            type=entry.type,
            value=entry.annotation.value,
            text=dict(entry.annotation.text),
        )
        if entry.op == "add":
            return [*current, new]
        existing = [a for a in current if a.type == entry.type]
        return kept + self._merge.get(entry.type, _replace)(existing, new)

    def _items(self, owner: object, scheme: SdmxUrn[object], *, path: str, top: bool) -> dict[str, object]:
        """Changed item fields of `owner`, by field name.

        A container's items nest and take a dotted path. A single item field is
        a component only at the artefact (`DataStructure.time_dimension`); on
        an item it is a sibling pointer (`Agency.parent`), left alone.
        """
        changes: dict[str, object] = {}
        for name, item_cls in item_fields(type(owner)):
            value: object = getattr(owner, name, None)
            if value is None:
                continue
            prefix = _item_prefix(scheme, item_cls)
            if isinstance(value, (ItemList, ArtefactList, list, tuple)):
                items = list(value)
                updated = [self._item(item, prefix, scheme, path) for item in items]
                if any(new is not old for new, old in zip(updated, items, strict=True)):
                    changes[name] = updated
            elif top and isinstance(item_id := getattr(value, "id", None), str):
                updated_single = self._item(value, prefix, scheme, "", own_id=item_id)
                if updated_single is not value:
                    changes[name] = updated_single
        return changes

    def _item(self, item: object, prefix: str, scheme: SdmxUrn[object], path: str, own_id: str | None = None) -> object:
        item_id = own_id or getattr(item, "id", None)
        item_class = getattr(type(item), "sdmx_class", None)
        if not isinstance(item_id, str) or not isinstance(item_class, str) or not _is_attrs(item):
            return item
        item_path = f"{path}.{item_id}" if path else item_id
        changes: dict[str, object] = {}
        annotations = self._annotations(item, prefix + item_path, item_class, None)
        if annotations is not None:
            changes["annotations"] = annotations
        changes |= self._items(item, scheme, path=item_path, top=False)
        return _evolve(item, **changes) if changes else item


#: `attrs.evolve` over a field map built at runtime, which its overloads cannot
#: see: the spelling `_rebind` and `version_rewrite` use for the same walk.
_evolve: Callable[..., object] = attrs.evolve


def _item_prefix(scheme: SdmxUrn[object], item_cls: type[SdmxItem]) -> str:
    """What every item URN of one scheme and item class starts with, as `validation` builds it."""
    base: SdmxUrn[object] = SdmxUrn.make(
        item_cls,
        package=item_cls.sdmx_package,
        artefact_class=item_cls.sdmx_class,
        agency=scheme.agency,
        id=scheme.id,
        version=scheme.version,
    )
    return f"{base}."


def _is_attrs(value: object) -> TypeIs[AttrsInstance]:
    """Narrow `value` itself, which `attrs.has(type(value))` does not (it narrows the type)."""
    return attrs.has(type(value))


def _replace(_existing: list[Annotation], new: Annotation) -> list[Annotation]:
    return [new]
