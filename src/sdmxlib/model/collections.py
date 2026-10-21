"""ItemList[T] and ArtefactList[T] — SDMX collection types.

- ``ItemList[T]``: child artefacts (codes, dimensions, concepts).
  Lookup by bare id only.
- ``ArtefactList[T]``: maintainable artefacts (codelists, DSDs, etc.).
  Lookup by bare id, agency-qualified key, fully-qualified key,
  SdmxUrn, or SdmxRef.
"""

from collections.abc import Callable, Iterator, Sequence
from typing import Any, ClassVar, Protocol, cast, overload, override, runtime_checkable

from sdmxlib.model.ref import SdmxRef
from sdmxlib.model.urn import SdmxUrn

# ── attrs converter helpers ────────────────────────────────────────────────


def to_itemlist[T: SupportsId](v: ItemList[T] | list[T]) -> ItemList[T]:
    """Attrs converter: wrap a list in ItemList, or pass an ItemList through."""
    return v if isinstance(v, ItemList) else ItemList(v)


def to_itemlist_opt[T: SupportsId](v: ItemList[T] | list[T] | None) -> ItemList[T] | None:
    """Attrs converter: nullable variant of `to_itemlist`."""
    if v is None or isinstance(v, ItemList):
        return v
    return ItemList(v)


# ── Identity protocols ─────────────────────────────────────────────────────


@runtime_checkable
class SupportsId(Protocol):
    """Protocol for any identifiable artefact — the ``ItemList`` element bound.

    A one-property upper bound so ``ItemList[T]`` can look items up by ``id``
    without a runtime ``getattr``/``cast`` dance.

    A read-only property, not a bare attribute: a protocol attribute is
    writable, and no frozen item's ``id`` is (#492).
    """

    @property
    def id(self) -> str:
        """The item's id."""
        ...


@runtime_checkable
class SdmxItem(SupportsId, Protocol):
    """Protocol for an item *inside* a scheme — an id, and its own URN class.

    SDMX gives an item a URN of its own, formed from the scheme's coordinates
    and the item's class: a code in ``CL_FREQ`` is
    ``…codelist.Code=SDMX:CL_FREQ(1.0).A``, so the class names the *item* while
    the id path names the scheme. Both halves of the pair are therefore a
    property of the item class, and `_refshape.item_fields` uses exactly this
    declaration to find which fields of a scheme hold items — a table of
    (scheme, accessor) rows somewhere else would have to be kept in step by
    hand, and `validate_references` shipped for four milestones with one that
    listed a single class (#404).

    The maintainables declare the same pair for their own scheme-level URN, so
    carrying it is not by itself what makes something an item; not being a
    `MaintainableArtefact` is the other half.
    """

    sdmx_package: ClassVar[str]
    sdmx_class: ClassVar[str]


@runtime_checkable
class SupportsUri(Protocol):
    """Protocol for anything carrying SDMX's ``IdentifiableType/@uri``.

    Every identifiable artefact does — maintainables, items and components
    alike — so the format layer takes this rather than a union of forty
    concrete types when all it needs is the optional external link (#238).
    """

    @property
    def uri(self) -> str | None:
        """Read only: a writer reads the link, and a maintainable declares it as a property."""
        ...


@runtime_checkable
class Maintainable(Protocol):
    """Protocol for top-level SDMX artefacts (have agency_id and version)."""

    @property
    def id(self) -> str:
        """Artefact identifier."""
        ...

    @property
    def agency_id(self) -> str:
        """Maintainer agency identifier."""
        ...

    @property
    def version(self) -> str:
        """Semantic version string."""
        ...


# ── ItemList[T] ────────────────────────────────────────────────────────────


class ItemList[T: SupportsId](Sequence[T]):
    """Ordered collection of child artefacts (codes, dimensions, etc.).

    Items have an ``id`` attribute (``T`` is bound to `SupportsId`). Lookup is by
    bare id.

    Example:
        dims["FREQ"]  # by id
        dims.filter(lambda d: d.is_enumerated)   # lambda predicate
        dims.filter(item("is_enumerated"))        # expression predicate
        dims.sort_by(lambda d: d.id)  # sort → new ItemList
        dims.ids()  # ["FREQ", "REF_AREA", ...]
    """

    __slots__ = ("_by_id", "_items")

    def __init__(self, items: list[T] | None = None) -> None:
        self._items: tuple[T, ...] = tuple(items or [])
        self._by_id: dict[str, T] = {item.id: item for item in self._items}

    # ── Sequence ──────────────────────────────────────────────────────────

    @overload
    def __getitem__(self, index: int) -> T: ...

    @overload
    def __getitem__(self, index: slice) -> ItemList[T]: ...

    @overload
    def __getitem__(self, index: str) -> T: ...

    @override
    def __getitem__(self, index: int | str | slice) -> T | ItemList[T]:
        # String lookup is the common case (e.g. dsd.dimensions["FREQ"]);
        # short-circuit it before paying for the int/slice isinstance
        # checks. See issue #118 — Phase 3 bench showed this dispatch
        # added ~35 ns per call atop the dict hit.
        if isinstance(index, str):
            try:
                return self._by_id[index]
            except KeyError:
                msg = f"No item with id {index!r}"
                raise KeyError(msg) from None
        if isinstance(index, int):
            return self._items[index]
        return ItemList(list(self._items[index]))

    @override
    def __len__(self) -> int:
        return len(self._items)

    @override
    def __iter__(self) -> Iterator[T]:
        return iter(self._items)

    def __bool__(self) -> bool:
        return bool(self._items)

    @override
    def __contains__(self, key: object) -> bool:
        if isinstance(key, str):
            return key in self._by_id
        return key in self._items

    # ── Lookup ────────────────────────────────────────────────────────────

    def get(self, id: str) -> T | None:
        """Return item by id, or None if not found."""
        return self._by_id.get(id)

    def ids(self) -> list[str]:
        """List of all item ids in order."""
        return [item.id for item in self._items]

    # ── Filtering ─────────────────────────────────────────────────────────

    def filter(self, predicate: Callable[[T], bool]) -> ItemList[T]:
        """Return new ItemList with items satisfying predicate.

        Accepts a lambda or an item() expression:
            dims.filter(lambda d: d.id == "FREQ")
            dims.filter(item("id") == "FREQ")
            dims.filter(item("is_enumerated"))
            dims.filter(~item("is_enumerated"))
        """
        return ItemList([x for x in self._items if predicate(x)])

    def first_where(self, predicate: Callable[[T], bool]) -> T | None:
        """Return first item satisfying the predicate, or None."""
        for x in self._items:
            if predicate(x):
                return x
        return None

    def partition(self, predicate: Callable[[T], bool]) -> tuple[ItemList[T], ItemList[T]]:
        """Split into (matching, non-matching) ItemLists."""
        yes: list[T] = []
        no: list[T] = []
        for item in self._items:
            (yes if predicate(item) else no).append(item)
        return ItemList(yes), ItemList(no)

    # ── Ordering ──────────────────────────────────────────────────────────

    def sort_by(
        self,
        key: Callable[[T], Any],
        *,
        descending: bool = False,
    ) -> ItemList[T]:
        """Return new ItemList sorted by key function."""
        return ItemList(sorted(self._items, key=key, reverse=descending))

    # ── Conversion ────────────────────────────────────────────────────────

    @overload
    def to_dict(self, key: str = "id") -> dict[str, T]: ...
    @overload
    def to_dict[K](self, key: Callable[[T], K]) -> dict[K, T]: ...
    def to_dict[K](self, key: str | Callable[[T], K] = "id") -> dict[Any, T]:
        """Convert to dict keyed by attribute name or key function.

        A key function recovers its return type: ``to_dict(lambda c: c.id)``
        is ``dict[str, T]``. The attribute-name form keys by that attribute's
        value (typed ``str`` for the common ``id``/``agency_id`` cases).
        """
        if isinstance(key, str):
            attr = key
            return {getattr(item, attr): item for item in self._items}
        return {key(item): item for item in self._items}

    # ── Equality & repr ───────────────────────────────────────────────────

    def __eq__(self, other: object) -> bool:
        if isinstance(other, ItemList):
            return self._items == other._items
        return NotImplemented

    #: Not hashable, and the declaration says so rather than a method failing
    #: at the point of use (#290).
    #:
    #: `__hash__` used to be `hash(self._items)`, which worked for the **empty**
    #: list and raised `TypeError: unhashable type: 'Code'` for every populated
    #: one — every domain item was an attrs `@define` class, so mutable, so
    #: unhashable. The class advertised a capability it could not deliver for
    #: any type it is actually parameterised with.
    #:
    #: Hashing the ids instead would have satisfied the `__eq__`/`__hash__`
    #: contract on paper — equal lists have equal ids — but not in practice:
    #: the items were mutable, so a list used as a dict key would keep its hash
    #: while `__eq__` changed underneath it, and the dict would silently stop
    #: finding its own entry. Since #492 the items are frozen, and still
    #: deliberately unhashable — `@frozen(unsafe_hash=False)`, equal by value,
    #: keyed by `urn` — so the collection is only as hashable as its contents.
    # `__hash__ = None` is the documented Python idiom for "not hashable" —
    # CPython, `dataclasses` and `attrs` all use it — and it is the *only*
    # spelling that makes `isinstance(x, Hashable)` answer False. basedpyright
    # rejected every form of it, even a fully annotated
    # `ClassVar[Callable[..., int] | None]`, and needed a suppression here until
    # it was retired (#425); pyrefly accepts it. The alternative — a `__hash__`
    # that raises — passes the checker and lies at runtime:
    # `isinstance(x, Hashable)` becomes True, so code that feature-detects
    # hashability gets the wrong answer and fails later instead of earlier.
    __hash__: ClassVar[None] = None

    def __repr__(self) -> str:
        ids = [getattr(item, "id", repr(item)) for item in self._items]
        return f"ItemList([{', '.join(repr(i) for i in ids)}])"


# ── ArtefactList[T] ────────────────────────────────────────────────────────


class ArtefactList[T: Maintainable](Sequence[T]):
    """Ordered collection of maintainable SDMX artefacts.

    Supports multiple key formats for lookup:

    - ``"CL_FREQ"`` — bare id (KeyError if ambiguous)
    - ``"SDMX:CL_FREQ"`` — agency-qualified
    - ``"SDMX:CL_FREQ(1.0)"`` — fully qualified
    - ``SdmxUrn[T]`` — typed URN (``version="latest"`` matches any)
    - ``SdmxRef(...)`` — partial identity match

    Example:
        msg.codelists["CL_FREQ"]  # bare id
        msg.codelists["SDMX:CL_FREQ"]  # agency-qualified
        msg.codelists["SDMX:CL_FREQ(1.0)"]  # fully qualified
        msg.codelists[dim.codelist.urn]  # SdmxUrn
        msg.codelists.ids(qualified=True)  # ["SDMX:CL_FREQ", ...]
    """

    __slots__ = ("_by_agency_id", "_by_fqn", "_by_id", "_items")

    def __init__(self, items: list[T] | None = None) -> None:
        self._items: tuple[T, ...] = tuple(items or [])
        self._by_id: dict[str, list[T]] = {}
        self._by_agency_id: dict[str, list[T]] = {}
        self._by_fqn: dict[str, T] = {}
        for item in self._items:
            self._by_id.setdefault(item.id, []).append(item)
            ak = f"{item.agency_id}:{item.id}"
            self._by_agency_id.setdefault(ak, []).append(item)
            self._by_fqn[f"{ak}({item.version})"] = item

    # ── Sequence ──────────────────────────────────────────────────────────

    @overload
    def __getitem__(self, index: int) -> T: ...

    @overload
    def __getitem__(self, index: slice) -> ArtefactList[T]: ...

    @overload
    def __getitem__(self, index: str | SdmxUrn[T] | SdmxRef) -> T: ...

    @override
    def __getitem__(self, index: int | str | SdmxUrn[T] | SdmxRef | slice) -> T | ArtefactList[T]:
        if isinstance(index, int):
            return self._items[index]
        if isinstance(index, slice):
            return ArtefactList(list(self._items[index]))
        result = self._lookup(index)
        if result is None:
            msg = f"No artefact matching {index!r}"
            raise KeyError(msg)
        return result

    @override
    def __len__(self) -> int:
        return len(self._items)

    @override
    def __iter__(self) -> Iterator[T]:
        return iter(self._items)

    def __bool__(self) -> bool:
        return bool(self._items)

    @override
    def __contains__(self, key: object) -> bool:
        if isinstance(key, (str, SdmxUrn, SdmxRef)):
            return self._lookup(cast("str | SdmxUrn[T] | SdmxRef", key)) is not None
        return key in self._items

    # ── Lookup helpers ────────────────────────────────────────────────────

    def _lookup(self, key: str | SdmxUrn[T] | SdmxRef) -> T | None:
        """Core lookup — returns None on miss, raises KeyError on ambiguity."""
        if isinstance(key, SdmxUrn):
            return self._lookup_urn(key)
        if isinstance(key, SdmxRef):
            return self._lookup_ref(key)
        # string key — narrowed by the two isinstance checks above
        return self._lookup_str(key)

    def _lookup_str(self, key: str) -> T | None:
        # "AGENCY:ID(version)"
        if "(" in key and key.endswith(")"):
            q, rest = key.split("(", 1)
            version = rest[:-1]
            if ":" not in q:
                msg = f"Malformed key {key!r}: expected AGENCY:ID(version)"
                raise KeyError(msg)
            fqn = f"{q}({version})"
            return self._by_fqn.get(fqn)
        if ":" in key:
            matches = self._by_agency_id.get(key, [])
        else:
            # bare id — must be unambiguous
            matches = self._by_id.get(key, [])
            if len(matches) > 1:
                qualified = [f"{m.agency_id}:{m.id}({m.version})" for m in matches]
                msg = (
                    f"Ambiguous id {key!r} matches {len(matches)} artefacts: "
                    f"{', '.join(qualified)}. Use a qualified key."
                )
                raise KeyError(msg)

        return matches[0] if matches else None

    def _lookup_urn(self, urn: SdmxUrn[T]) -> T | None:
        ak = f"{urn.agency}:{urn.id}"
        if urn.version not in {"latest", ""}:
            return self._by_fqn.get(f"{ak}({urn.version})")
        matches = self._by_agency_id.get(ak, [])
        return matches[0] if matches else None

    def _lookup_ref(self, ref: SdmxRef) -> T | None:
        matches = [
            item
            for item in self._items
            if (ref.id is None or item.id == ref.id)
            and (ref.agency is None or item.agency_id == ref.agency)
            and (ref.version is None or item.version == ref.version)
        ]
        if len(matches) > 1:
            qualified = [f"{m.agency_id}:{m.id}({m.version})" for m in matches]
            msg = (
                f"Ambiguous SdmxRef {ref!r} matches {len(matches)} artefacts: "
                f"{', '.join(qualified)}. Use a more specific ref."
            )
            raise KeyError(msg)
        return matches[0] if matches else None

    def get(self, key: str | SdmxUrn[T] | SdmxRef) -> T | None:
        """Return artefact by key, or None on miss or ambiguity."""
        try:
            return self._lookup(key)
        except KeyError:
            return None

    def ids(self, *, qualified: bool = False) -> list[str]:
        """List of artefact ids in order."""
        if qualified:
            return [f"{item.agency_id}:{item.id}({item.version})" for item in self._items]
        return [item.id for item in self._items]

    # ── Filtering ─────────────────────────────────────────────────────────

    def filter(self, predicate: Callable[[T], bool]) -> ArtefactList[T]:
        """Return new ArtefactList with items satisfying predicate.

        Accepts a lambda or an item() expression:
            codelists.filter(lambda cl: cl.agency_id == "SDMX")
            codelists.filter(item("agency_id") == "SDMX")
        """
        return ArtefactList([x for x in self._items if predicate(x)])

    def first_where(self, predicate: Callable[[T], bool]) -> T | None:
        """Return first item satisfying the predicate, or None."""
        for x in self._items:
            if predicate(x):
                return x
        return None

    def partition(self, predicate: Callable[[T], bool]) -> tuple[ArtefactList[T], ArtefactList[T]]:
        """Split into (matching, non-matching) ArtefactLists."""
        yes: list[T] = []
        no: list[T] = []
        for item in self._items:
            (yes if predicate(item) else no).append(item)
        return ArtefactList(yes), ArtefactList(no)

    # ── Ordering ──────────────────────────────────────────────────────────

    def sort_by(
        self,
        key: Callable[[T], Any],
        *,
        descending: bool = False,
    ) -> ArtefactList[T]:
        """Return new ArtefactList sorted by key function."""
        return ArtefactList(sorted(self._items, key=key, reverse=descending))

    # ── Conversion ────────────────────────────────────────────────────────

    @overload
    def to_dict(self, key: str = "id") -> dict[str, T]: ...
    @overload
    def to_dict[K](self, key: Callable[[T], K]) -> dict[K, T]: ...
    def to_dict[K](self, key: str | Callable[[T], K] = "id") -> dict[Any, T]:
        """Convert to dict keyed by attribute name or key function.

        A key function recovers its return type: ``to_dict(lambda cl: cl.id)``
        is ``dict[str, T]``. The attribute-name form keys by that attribute's
        value (typed ``str`` for the common ``id``/``agency_id`` cases).
        """
        if isinstance(key, str):
            attr = key
            return {getattr(item, attr): item for item in self._items}
        return {key(item): item for item in self._items}

    # ── Equality & repr ───────────────────────────────────────────────────

    def __eq__(self, other: object) -> bool:
        if isinstance(other, ArtefactList):
            return self._items == other._items
        return NotImplemented

    #: Not hashable, and the declaration says so rather than a method failing
    #: at the point of use (#290).
    #:
    #: `__hash__` used to be `hash(self._items)`, which worked for the **empty**
    #: list and raised `TypeError: unhashable type: 'Code'` for every populated
    #: one — every domain item was an attrs `@define` class, so mutable, so
    #: unhashable. The class advertised a capability it could not deliver for
    #: any type it is actually parameterised with.
    #:
    #: Hashing the ids instead would have satisfied the `__eq__`/`__hash__`
    #: contract on paper — equal lists have equal ids — but not in practice:
    #: the items were mutable, so a list used as a dict key would keep its hash
    #: while `__eq__` changed underneath it, and the dict would silently stop
    #: finding its own entry. Since #492 the items are frozen, and still
    #: deliberately unhashable — `@frozen(unsafe_hash=False)`, equal by value,
    #: keyed by `urn` — so the collection is only as hashable as its contents.
    # `__hash__ = None` is the documented Python idiom for "not hashable" —
    # CPython, `dataclasses` and `attrs` all use it — and it is the *only*
    # spelling that makes `isinstance(x, Hashable)` answer False. basedpyright
    # rejected every form of it, even a fully annotated
    # `ClassVar[Callable[..., int] | None]`, and needed a suppression here until
    # it was retired (#425); pyrefly accepts it. The alternative — a `__hash__`
    # that raises — passes the checker and lies at runtime:
    # `isinstance(x, Hashable)` becomes True, so code that feature-detects
    # hashability gets the wrong answer and fails later instead of earlier.
    __hash__: ClassVar[None] = None

    def __repr__(self) -> str:
        ids = [f"{item.agency_id}:{item.id}({item.version})" for item in self._items]
        return f"ArtefactList([{', '.join(repr(i) for i in ids)}])"
