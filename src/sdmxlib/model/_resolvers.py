"""A `Resolver` over a URN → artefact map, the kind a registry or a document holds (#492).

`InMemoryRegistry` resolves through its store; a structure reader through the
index of the document it is reading, so a reference finds an artefact the
document defines later as well as earlier. A refused artefact is never built
(#492), so never in the index, and a reference to it finds nothing.
"""

from collections.abc import MutableMapping
from contextlib import AbstractContextManager, nullcontext

from sdmxlib.model._binding import binding, current
from sdmxlib.model._items import resolve_item
from sdmxlib.model.urn import SdmxUrn


class MapResolver[V]:
    """What a `Ref` bound to a URN → artefact map finds: the artefact, or an item inside one.

    An item is found inside the scheme its URN names, as `resolve_item` finds
    it, and remembered until the map changes — a component's concept is
    unwrapped far more often than a scheme is added.
    """

    __slots__ = ("_items", "_map")

    def __init__(self, mapping: MutableMapping[str, V]) -> None:
        self._map = mapping
        self._items: dict[str, object] = {}

    def lookup(self, urn: SdmxUrn[object], /) -> object | None:
        """What *urn* names in the map, or an item inside what it holds."""
        key = str(urn)
        held = self._map.get(key)
        if held is not None or urn.item_id is None:
            return held
        found = self._items.get(key)
        if found is None:
            found = resolve_item(self._map, urn)
            if found is not None:
                self._items[key] = found
        return found

    def forget_items(self) -> None:
        """Call when the map changes: an item found before may be in a newer scheme now."""
        self._items.clear()


def document_binding(index: MutableMapping[str, object]) -> AbstractContextManager[None]:
    """Bind a reader's references to its document *index* — unless a binding is already in force.

    A caller reading straight into a registry (`parse(registry=…)`, a
    `RestRegistry` fetch) binds to that registry first, so its `add` finds
    every reference already bound and copies nothing.
    """
    return nullcontext() if current() is not None else binding(MapResolver(index))
