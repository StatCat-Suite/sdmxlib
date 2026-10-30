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
from attrs import AttrsInstance, field, frozen

from sdmxlib.bundle._write import Lifecycle, write_manifest
from sdmxlib.local.registry import LocalRegistry
from sdmxlib.model._refshape import item_fields
from sdmxlib.model.annotations import Annotation
from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.collections import ArtefactList, ItemList, SdmxItem
from sdmxlib.model.istring import InternationalString, _normalized, normalize_tag
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.storage._manifest import Manifest, read_manifest

#: The overlay format this build writes and reads (``1.x``).
OVERLAY_FORMAT = "1.2"

#: How an existing annotation and a `set` combine: given every annotation of
#: the entry's type the target already has, and the new one, the annotations
#: of that type it should have. The default replaces them all with the new one.
type Merge = Callable[[list[Annotation], Annotation], list[Annotation]]

#: What a hook's package and name, or an annotation type with a hook, may not
#: hold: the separators of the token's hooks line.
_HOOK_SEPARATORS = frozenset(",=@:\n")


def _plain(value: str) -> str:
    if not value or _HOOK_SEPARATORS & set(value):
        msg = f"{value!r} is empty or holds one of , = @ : or a newline, which the effective token separates on"
        raise ValueError(msg)
    return value


@frozen
class MergeHook:
    """A merge hook and its identity, which the effective token covers.

    A hook that changes what it returns MUST change its `version`: two
    servers applying the same overlays with different hooks serve different
    annotations, and the token is how their caches tell them apart.

    Attributes:
        package: The distribution that provides it.
        name: Its name within the package.
        version: Its version.
        fn: The hook itself.
    """

    package: str = field(converter=_plain)
    name: str = field(converter=_plain)
    version: str = field(converter=_plain)
    fn: Merge

    @property
    def id(self) -> str:
        """``<package>:<name>@<version>``."""
        return f"{self.package}:{self.name}@{self.version}"


class OverlayError(ValueError):
    """An overlay document that is malformed, or in a format this build cannot read."""


class OverlayAnnotation(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """The annotation a `set` or `add` writes. Its type is the entry's."""

    id: str | None = None
    title: str | None = None
    value: str | None = None
    text: dict[str, str] = msgspec.field(default_factory=dict)

    def __post_init__(self) -> None:
        """Refuse a malformed tag in `text`, or two of one tag; msgspec reports it as the document's decode error.

        Raises:
            ValueError: If a key of `text` is not a well-formed language tag,
                or two keys normalise to one tag.
        """
        _normalized(self.text)


class OverlayEntry(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """One change: which targets, what to do, to annotations of which type.

    Attributes:
        selector: ``*``, a class name, ``AGENCY:ID``, or a URN.
        op: ``set`` replaces the type's annotations (through a merge hook),
            ``add`` appends one, ``remove`` drops them all.
        type: The annotation type it acts on.
        annotation: What ``set`` and ``add`` write; absent for ``remove``.
        scope: The audience the entry applies to, or None for every
            audience. Given an ``audience``, `apply_overlays`,
            `effective_token` and `fold_overlays` apply an entry whose scope
            is None or equal to it.
    """

    selector: str
    op: Literal["set", "add", "remove"]
    type: str
    annotation: OverlayAnnotation | None = None
    scope: str | None = None


class OverlayText(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """One language of a name or description, set or removed. Only `fold_overlays` applies it.

    A name is structure: it feeds search and every label map, so a change to
    one is written into a new publication rather than applied as a response
    is served (design §5.4).

    Attributes:
        selector: ``*``, a class name, ``AGENCY:ID``, or a URN, as for an entry.
        op: ``set`` writes `text` as the `lang` text; ``remove`` drops the
            `lang` text. A name keeps at least one language.
        field: ``name`` or ``description``.
        lang: The language tag it acts on. A malformed one raises
            `ValueError` (`normalize_tag`); `fold_overlays` applies it
            normalised.
        text: What ``set`` writes; absent for ``remove``.
        scope: As `OverlayEntry`'s.
    """

    selector: str
    op: Literal["set", "remove"]
    field: Literal["name", "description"]
    lang: str
    text: str | None = None
    scope: str | None = None

    def __post_init__(self) -> None:
        """Refuse a malformed `lang`; msgspec reports it as the document's decode error.

        Raises:
            ValueError: If `lang` is not a well-formed language tag.
        """
        normalize_tag(self.lang)


class Overlay(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True, omit_defaults=True):
    """A versioned overlay document: an id, its annotation entries and its name and description texts, in order.

    ``texts`` is left out of the document, and so of the digest, when it is
    empty: an overlay that changes no name reads and digests as it did in
    format 1.1.
    """

    format_version: str
    id: str
    entries: tuple[OverlayEntry, ...]
    texts: tuple[OverlayText, ...] = ()


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
    for n, text in enumerate(overlay.texts):
        if (text.text is None) != (text.op == "remove"):
            msg = f"{path}: text {n} ({text.op} {text.field} {text.lang}) " + (
                "gives no text" if text.text is None else "removes, so gives no text"
            )
            raise OverlayError(msg)
    return overlay


def _refuse_texts(overlays: Sequence[Overlay]) -> None:
    """Raise for an overlay that changes a name or description: those are folded, never served."""
    for overlay in overlays:
        if overlay.texts:
            msg = (
                f"overlay {overlay.id!r} changes {len(overlay.texts)} name or description text(s); "
                "a name is structure, so it is written into a new publication with fold_overlays, "
                "never applied while serving"
            )
            raise OverlayError(msg)


def generate_overlay_schema() -> bytes:
    """The overlay format's JSON Schema, generated from `Overlay`, as the shipped file holds it."""
    schema = msgspec.json.schema(Overlay)
    return msgspec.json.format(msgspec.json.encode(schema), indent=2) + b"\n"


def overlay_digest(overlay: Overlay) -> str:
    """The sha256 of an overlay's canonical JSON: what `effective_token` folds in."""
    return hashlib.sha256(msgspec.json.encode(overlay, order="sorted")).hexdigest()


def applied(overlay: Overlay, audience: str | None) -> Overlay:
    """`overlay` with only the entries that apply to `audience`: its own and the unscoped ones.

    With no audience every entry applies, `overlay` as it is.
    """
    if audience is None:
        return overlay
    return msgspec.structs.replace(
        overlay,
        entries=tuple(e for e in overlay.entries if e.scope is None or e.scope == audience),
        texts=tuple(t for t in overlay.texts if t.scope is None or t.scope == audience),
    )


def effective_token(
    manifest: Manifest,
    overlays: Sequence[Overlay],
    *,
    merge: Mapping[str, MergeHook] | None = None,
    audience: str | None = None,
) -> str:
    """What a cache keys on when `overlays` are applied to a bundle: its token, the hooks, and theirs.

    The manifest's ``token`` itself when there are no overlays, so a server
    that applies none keys exactly as before. Otherwise the sha256 of the
    lines ``v2``, the token, the hooks, and each overlay's digest as
    applied to `audience`, in apply order. The hooks line is
    ``type=<package>:<name>@<version>`` for each hook, sorted by type and
    joined with ``,``, or ``default`` when there is none. So changing,
    adding, removing or reordering an overlay changes the token; so does a
    different audience that applies different entries, changing a hook, and
    republishing the bundle.

    Args:
        manifest: The bundle's manifest.
        overlays: The overlays applied, in order.
        merge: The hooks applied, by annotation type, as `apply_overlays` takes them.
        audience: As `apply_overlays`.

    Returns:
        The token to key on.
    """
    if not overlays:
        return manifest.token
    _refuse_texts([applied(o, audience) for o in overlays])
    hooks = ",".join(f"{_plain(kind)}={hook.id}" for kind, hook in sorted((merge or {}).items())) or "default"
    digests = [overlay_digest(applied(o, audience)) for o in overlays]
    # ``v2`` names the format of the hashed text, so a server reading
    # another format never takes a token for its own.
    return hashlib.sha256("\n".join(["v2", manifest.token, hooks, *digests]).encode()).hexdigest()


@overload
def apply_overlays(
    target: StructureMessage,
    overlays: Sequence[Overlay],
    *,
    merge: Mapping[str, MergeHook] | None = None,
    audience: str | None = None,
) -> StructureMessage: ...


@overload
def apply_overlays[A: MaintainableArtefact](
    target: Sequence[A],
    overlays: Sequence[Overlay],
    *,
    merge: Mapping[str, MergeHook] | None = None,
    audience: str | None = None,
) -> list[A]: ...


def apply_overlays(
    target: StructureMessage | Sequence[MaintainableArtefact],
    overlays: Sequence[Overlay],
    *,
    merge: Mapping[str, MergeHook] | None = None,
    audience: str | None = None,
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
        audience: Apply only the entries scoped to it and the unscoped
            ones; None applies every entry.

    Returns:
        A `StructureMessage` for a message, a list for a sequence.
    """
    selected = [applied(o, audience) for o in overlays]
    _refuse_texts(selected)
    plan = _Plan(selected, merge or {})
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
    path: str | Path,
    overlays: Sequence[Overlay],
    *,
    merge: Mapping[str, MergeHook] | None = None,
    audience: str | None = None,
    lifecycle: Lifecycle = "live",
) -> Manifest:
    """Publish a new publication of a bundle with `overlays` written into its store.

    The new publication extends the one at `path`: a sibling directory, its
    ``meta.duckdb`` copied from `path` with every artefact an entry changes
    replaced, its lake the same data files, and its manifest's fields kept
    with a new token. The publication at `path` is left as it was. After this
    the overlays are part of the bundle and need not be applied at serve time.
    Loads every artefact, so it is a build-time step.

    A partial keeps the `cut_from` the earlier manifest records, so a
    product bundle's partials can still be checked against the full
    artefacts after a fold.

    Args:
        path: The published publication directory.
        overlays: Applied in order.
        merge: As `apply_overlays`.
        audience: As `apply_overlays`.
        lifecycle: As `write_manifest`: ``final`` for a bundle whose
            observations will not be appended to. The manifest does not
            record it, so a fold states it again.

    Returns:
        The new manifest; its publication is ``<path>/../<token>/``.
    """
    root = Path(path)
    previous = read_manifest(root)
    plan = _Plan([applied(o, audience) for o in overlays], merge or {})
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
        lifecycle=lifecycle,
        cut_from={entry.urn: entry.cut_from for entry in previous.artefacts if entry.cut_from is not None},
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

    def __init__(self, overlays: Sequence[Overlay], merge: Mapping[str, MergeHook]) -> None:
        ranked = [
            (_specificity(entry.selector), n, m, entry)
            for n, overlay in enumerate(overlays)
            for m, entry in enumerate(overlay.entries)
        ]
        self._entries = [entry for *_, entry in sorted(ranked, key=lambda r: r[:3])]
        texts = [
            (_specificity(text.selector), n, m, text)
            for n, overlay in enumerate(overlays)
            for m, text in enumerate(overlay.texts)
        ]
        self._texts = [text for *_, text in sorted(texts, key=lambda r: r[:3])]
        self._merge = merge
        #: Whether any entry could select an item. When none can, an artefact's
        #: items are not walked: a 500,000-code codelist costs 0.28 s to walk.
        self._reaches_items = any(_reaches_items(e.selector) for e in [*self._entries, *self._texts])

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
        changes |= self._named(artefact, str(urn), artefact.sdmx_class, key)
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

    def _named(self, owner: object, urn: str, cls: str, key: str | None) -> dict[str, object]:
        """The ``name`` and ``description`` `owner` should have, by field, where a text selects it.

        Raises:
            OverlayError: If a text would remove a name's last language.
        """
        names = {"*", cls, urn} if key is None else {"*", cls, urn, key}
        selected = [t for t in self._texts if t.selector in names]
        if not selected:
            return {}
        fields: dict[str, dict[str, str]] = {}
        for text in selected:
            current = fields.get(text.field)
            if current is None:
                held: object = getattr(owner, text.field, None)
                current = dict(held.as_dict()) if isinstance(held, InternationalString) else {}
            lang = normalize_tag(text.lang)
            if text.op == "set" and text.text is not None:
                current[lang] = text.text
            else:
                current.pop(lang, None)
            fields[text.field] = current
        if "name" in fields and not fields["name"]:
            msg = f"{urn}: removing the last language of its name; a name keeps at least one"
            raise OverlayError(msg)
        return {field: (value or None) if field == "description" else value for field, value in fields.items()}

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
        hook = self._merge.get(entry.type)
        return kept + (hook.fn(existing, new) if hook is not None else [new])

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
        changes |= self._named(item, prefix + item_path, item_class, None)
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
