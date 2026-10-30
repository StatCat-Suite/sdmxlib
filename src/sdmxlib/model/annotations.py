"""Annotation and Annotations — SDMX annotation support."""

from collections.abc import Iterable, Iterator, Sequence
from typing import ClassVar, overload, override

from attrs import field, frozen
from attrs.converters import optional

from sdmxlib.model.istring import InternationalString, normalize_tag
from sdmxlib.model.istring import to_istring as _to_istring


@frozen
class AnnotationURL:
    """One external resource an annotation points at, optionally in one language.

    SDMX 3.x's ``AnnotationURLType``: a URI with an optional ``xml:lang``
    naming the language of the resource, absent when it is not localised.
    SDMX-JSON spells it a ``link`` with ``href`` and ``hreflang``, and gives
    that link a ``urn`` and a ``type`` besides, which SDMX-ML's
    ``AnnotationURLType`` has no place for: an SDMX-ML write drops them
    (declared in `formats/gaps.py`).

    Attributes:
        url: The resource's URL.
        lang: The language of the resource, None when it is not localised;
            normalised by `normalize_tag`, so a malformed one raises
            `ValueError`.
        urn: The SDMX registry URN of what the link points at — SDMX-JSON's
            alone.
        type: The kind of resource, such as ``"PDF"`` — SDMX-JSON's alone.
    """

    url: str
    lang: str | None = field(default=None, converter=optional(normalize_tag))
    urn: str | None = None
    type: str | None = None


def _to_urls(v: str | AnnotationURL | Iterable[str | AnnotationURL] | None) -> tuple[AnnotationURL, ...]:
    """Attrs converter: one URL or several, each a bare string or an `AnnotationURL`."""
    if v is None:
        return ()
    if isinstance(v, str | AnnotationURL):
        v = (v,)
    return tuple(AnnotationURL(u) if isinstance(u, str) else u for u in v)


@frozen(unsafe_hash=False)
class Annotation:
    """A single SDMX annotation attached to an artefact or item."""

    id: str | None = None
    title: str | None = None
    type: str | None = None
    #: Every ``AnnotationURL``, in order (#422). SDMX 3.x allows several, each
    #: optionally localised; SDMX-ML 2.1 allows one, unlocalised, so writing
    #: 2.1 keeps the first and drops the rest (declared in `formats/gaps.py`).
    urls: tuple[AnnotationURL, ...] = field(factory=tuple, converter=_to_urls)
    text: InternationalString = field(factory=InternationalString, converter=_to_istring)
    #: SDMX ``AnnotationValue`` — the **non-localised** body, as distinct from
    #: `text`, which is a language map. Added in SDMX-ML 3.0; SDMX-ML 2.1 has no
    #: such element, so writing 2.1 drops it (declared in `formats/gaps.py`).
    #:
    #: This is the right home for a machine-read payload — an ordinal, a flag, a
    #: numeric weight — where a language map is meaningless. `.Stat` puts its
    #: ``ORDER`` number in `text` instead, which is why #369 has to read from
    #: there: a convention predating the field, not a reason to avoid it.
    value: str | None = None


class Annotations(Sequence[Annotation]):
    """Immutable collection of Annotation with type-based querying.

    Example:
        "DEPRECATED" in annotations  # checks annotation type
        annotations.by_type("CONTACT")  # list[Annotation]
        annotations.first("CONTACT")  # Annotation | None
        annotations.types  # list[str]
    """

    __slots__ = ("_items",)

    #: No annotations, shared. Assigned below the class body, since it is an
    #: instance of the class being defined.
    #:
    #: Safe to share because the backing store is a `tuple` behind
    #: `__slots__`: every "mutation" here (`__getitem__` with a slice) already
    #: returns a new `Annotations`. `tests/unit/test_istring_empty_is_shared.py`
    #: holds that contract alongside the istring one.
    #:
    #: The readers reached for `Annotations()` as a *default argument* —
    #: `annotations_by_owner.get(urn, Annotations())` — which Python evaluates
    #: eagerly, so it was built and discarded once per item at eight sites
    #: (#376, #377).
    EMPTY: ClassVar[Annotations]

    def __init__(self, items: list[Annotation] | None = None) -> None:
        self._items: tuple[Annotation, ...] = tuple(items or [])

    # ── Sequence ──────────────────────────────────────────────────────────

    @overload
    def __getitem__(self, index: int) -> Annotation: ...

    @overload
    def __getitem__(self, index: slice) -> Annotations: ...

    @override
    def __getitem__(self, index: int | slice) -> Annotation | Annotations:
        if isinstance(index, slice):
            return Annotations(list(self._items[index]))
        return self._items[index]

    @override
    def __len__(self) -> int:
        return len(self._items)

    @override
    def __iter__(self) -> Iterator[Annotation]:
        return iter(self._items)

    def __bool__(self) -> bool:
        return bool(self._items)

    # ── Type-based querying ───────────────────────────────────────────────

    @override
    def __contains__(self, key: object) -> bool:
        """True if any annotation has type == key."""
        if isinstance(key, str):
            return any(a.type == key for a in self._items)
        return super().__contains__(key)

    def by_type(self, annotation_type: str) -> list[Annotation]:
        """All annotations with the given type."""
        return [a for a in self._items if a.type == annotation_type]

    def first(self, annotation_type: str) -> Annotation | None:
        """First annotation with the given type, or None."""
        for a in self._items:
            if a.type == annotation_type:
                return a
        return None

    @property
    def types(self) -> list[str]:
        """All type values present (deduplicated, order preserved)."""
        seen: set[str] = set()
        result: list[str] = []
        for a in self._items:
            if a.type and a.type not in seen:
                seen.add(a.type)
                result.append(a.type)
        return result

    # ── Equality & repr ───────────────────────────────────────────────────

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Annotations):
            return self._items == other._items
        return NotImplemented

    __hash__ = None  # pyrefly: ignore[implicit-any-attribute]  # mutable container, explicitly unhashable

    def __repr__(self) -> str:
        return f"Annotations([{', '.join(repr(a) for a in self._items)}])"


#: See `Annotations.EMPTY`. Bound after the class body because it is an
#: instance of it; the class attribute is the public spelling.
EMPTY_ANNOTATIONS = Annotations()
Annotations.EMPTY = EMPTY_ANNOTATIONS
