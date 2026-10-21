"""The item-URN grammar: which scheme owns an item, and how to find it.

SDMX gives an item a URN formed from its scheme's coordinates and its *own*
class — ``…codelist.Code=SDMX:CL_FREQ(1.0).A`` names a code inside a codelist —
so a holder of that URN knows everything about the target except the one thing
needed to look it up: the class of the scheme that has it.

Three places now need that step, which is why it is a module rather than a
helper in one of them:

| caller | what it does with the item |
| --- | --- |
| `model.validation` | folds class spellings when checking a reference resolves |
| `formats.*` readers | resolves an intra-message reference to the item object |
| `model.in_memory_registry`, `storage.readers` | answers `get(item_urn)` |

**Everything here is derived, not listed.** `owner_classes` inverts
`_refshape.item_fields` over the maintainable classes, so the answer for a new
item-bearing scheme comes from the `sdmx_package` / `sdmx_class` pair its item
class declares, exactly as #404's expansion does. The alternative — and what
this replaces — is a table of (item class → owning class) rows, which
`storage.writers` carried privately and `validation` carried in another shape:
three copies of one fact, and the copy that mattered listed a single class for
four milestones (#404, #405).

Plain names in a private module rather than underscored names on a public one:
this is a package-internal API with several consumers, the shape CLAUDE.md's
rule 4 describes.
"""

import functools
from collections.abc import Iterator, Mapping
from typing import Final, Protocol, runtime_checkable

import attrs

from sdmxlib.model._refshape import item_fields
from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.collections import ArtefactList, ItemList, SdmxItem
from sdmxlib.model.ref import Ref, SupportsUrn
from sdmxlib.model.urn import SdmxUrn

#: The component classes SDMX spells differently across versions. A URN carries
#: the spelling of whatever produced it — 2.1 names an attribute ``Attribute``
#: and the single measure ``PrimaryMeasure``, 3.0 names them ``DataAttribute``
#: and ``Measure`` — while the model has one class for each and declares one
#: spelling. Both name the same component, so a reference in either spelling
#: must reach it.
#:
#: Groups rather than a one-way map, so the folding cannot go asymmetric:
#: `CLASS_ALIASES` is derived from these, and every member reaches every other.
#: `tests/unit/test_item_urn_classes.py` checks each member against the
#: vendored SDMX vocabularies, so a spelling nothing writes cannot sit here.
CLASS_ALIAS_GROUPS: Final[tuple[tuple[str, ...], ...]] = (
    ("Attribute", "DataAttribute"),
    ("Measure", "PrimaryMeasure"),
)

#: Spelling → the other spellings of the same class, derived from
#: `CLASS_ALIAS_GROUPS`.
CLASS_ALIASES: Final[dict[str, tuple[str, ...]]] = {
    spelling: tuple(other for other in group if other != spelling) for group in CLASS_ALIAS_GROUPS for spelling in group
}


def spellings(artefact_class: str) -> tuple[str, ...]:
    """Every spelling of *artefact_class*, its own first.

    One element for all but the two renamed components, so a caller can loop
    over this without branching on whether an alias exists.
    """
    return (artefact_class, *CLASS_ALIASES.get(artefact_class, ()))


@runtime_checkable
class SupportsItemLookup(Protocol):
    """An artefact that looks one item up by id without materialising the rest.

    `storage.lazy.LazyCodelist` and `LazyHierarchy` are the implementations:
    their subscript is a targeted query, which is the difference between reading
    one code and reading 579,951 of them. No *model* artefact defines
    ``__getitem__`` — items hang off a named field (`Codelist.codes`) — so this
    check separates the two shapes cleanly rather than by naming the classes.
    """

    def __getitem__(self, key: str, /) -> object:
        """The item with this id, or `KeyError`."""
        ...


def _maintainable_classes() -> tuple[type[MaintainableArtefact], ...]:
    """Every `MaintainableArtefact` subclass currently imported, by name.

    Sorted so it is a stable cache key for `_owner_map`. Cached on how many
    subclasses have been defined, so an import that adds one makes the next
    call walk again. A cache that could not see a later import froze a
    partial answer, and its symptom, one item class silently unresolvable, was
    #405. Walking on every call instead cost 4.3 ms of a 16 ms SDMX-ML 2.1 DSD
    write, which asks once per reference (#464). A class deleted at run time
    stays listed until another is defined; only a test deletes one.
    """
    return _subclasses(MaintainableArtefact._subclasses_defined)


@functools.lru_cache(maxsize=1)
def _subclasses(_defined: int) -> tuple[type[MaintainableArtefact], ...]:
    """The walk behind `_maintainable_classes`; `_defined` is only its cache key."""
    found: dict[str, type[MaintainableArtefact]] = {}
    pending = list(MaintainableArtefact.__subclasses__())
    while pending:
        cls = pending.pop()
        if cls.__name__ in found:
            continue
        found[cls.__name__] = cls
        pending.extend(cls.__subclasses__())
    return tuple(found[name] for name in sorted(found))


@functools.cache
def _owner_map(schemes: tuple[type[MaintainableArtefact], ...]) -> dict[str, tuple[type[MaintainableArtefact], ...]]:
    """Item URN class → the scheme classes that hold items of that class.

    The inverse of `item_fields`, which answers the question the other way
    round. Keyed by *every* spelling, so a 3.0 ``DataAttribute`` URN finds
    `DataStructure` through the model's ``Attribute`` declaration.

    Cached on the class tuple rather than on nothing, so importing another
    model module produces a different key and a fresh map instead of a stale
    one.
    """
    found: dict[str, list[type]] = {}
    for scheme in schemes:
        if _refines_identity(scheme, schemes):
            continue
        for _, item_cls in item_fields(scheme):
            for spelling in spellings(item_cls.sdmx_class):
                owners = found.setdefault(spelling, [])
                if scheme not in owners:
                    owners.append(scheme)
    return {spelling: tuple(owners) for spelling, owners in found.items()}


def _refines_identity(scheme: type, schemes: tuple[type, ...]) -> bool:
    """True when `scheme` subclasses another of `schemes` and shares its URN class.

    A `GeographicCodelist` is a `Codelist` down to its URN (#422), so the scheme
    URN an item resolves through is the one `Codelist` already spells. Listing
    it again would only repeat the lookup.
    """
    return any(
        base is not scheme
        and issubclass(scheme, base)
        and getattr(base, "sdmx_class", None) == getattr(scheme, "sdmx_class", None)
        for base in schemes
    )


def owner_classes(item_class: str) -> tuple[type[MaintainableArtefact], ...]:
    """Scheme classes that can hold an item of URN class *item_class*.

    Empty when nothing does, which is the honest answer for a URN naming a
    class this model has no item for — the caller then leaves the reference
    unresolved rather than guessing.

    More than one is possible in principle and does not happen today; the
    caller tries each in turn, so the ambiguity costs a failed dict lookup
    rather than a wrong answer.
    """
    return _owner_map(_maintainable_classes()).get(item_class, ())


def scheme_urn_of(item_urn: SdmxUrn[object], owner: type[MaintainableArtefact]) -> SdmxUrn[object]:
    """The URN of the scheme holding *item_urn*, as *owner* would spell it.

    The package comes from the owner too, not from the item URN: they agree
    everywhere in the model — `Code` and `Codelist` are both ``codelist`` — and
    taking both halves from one place means a disagreement shows up as a missed
    lookup rather than as a URN that names nothing.
    """
    return attrs.evolve(
        item_urn,
        package=owner.sdmx_package,
        artefact_class=owner.sdmx_class,
        item_id=None,
    )


def resolve_item(index: Mapping[str, object], item_urn: SdmxUrn[object]) -> SdmxItem | None:
    """The item *item_urn* names, looked up through an index of schemes.

    **Lazy by design.** The alternative is to index every item as the scheme is
    read, which is what the readers do for `Concept` — two dict entries per
    item, on every parse, against artefacts like the 2021 Census Profile's 579,951-code
    codelist and its 499,138-node hierarchy. This pays per *reference* instead,
    and a message has orders of magnitude fewer references than items.

    Returns None for a URN that is not an item URN, for a scheme the index does
    not hold, and for an item the scheme does not have — all three are the
    caller's existing "unresolved" case.
    """
    if item_urn.item_id is None:
        return None
    for owner_cls in owner_classes(item_urn.artefact_class):
        scheme = index.get(str(scheme_urn_of(item_urn, owner_cls)))
        if scheme is None:
            continue
        found = find_item(scheme, item_urn, owner_cls)
        if found is not None:
            return found
    return None


def find_item(scheme: object, item_urn: SdmxUrn[object], owner_cls: type) -> SdmxItem | None:
    """The item *item_urn* names inside *scheme*, or None.

    *owner_cls* supplies the shape to read — which fields hold items, and of
    what class — while *scheme* supplies the values. They are the same class
    for a model artefact; they differ for a lazy facade, which duck-types the
    real class's read surface without being it.

    A nested item is found by its dotted path — the item part SDMX gives its
    URN (``Category=AG:CS(1.0).ECON.GDP``) — and a bare id names a root. A leaf
    id is *not* searched for below the roots: it may repeat in another branch,
    so it does not name one item (#439). SDMX's `IDType` excludes ``.``, so a
    dot in an item id is always nesting and never part of a name.

    The declared fields are read first and the facade subscript second, not the
    other way round: `LazyHierarchy` holds two kinds of item and its subscript
    answers with one of them, so a `Level` URN reaches `levels` through the
    field path and never has to be told apart from a node afterwards.

    Returns an `SdmxItem` rather than `object` — narrowed by a runtime check, so
    the caller gets a type that says "an item" rather than a suppression that
    says nothing.
    """
    item_id = item_urn.item_id
    if item_id is None:
        return None
    wanted = spellings(item_urn.artefact_class)
    found = _descend(scheme, owner_cls, item_id.split("."), wanted)
    if found is None and isinstance(scheme, SupportsItemLookup):
        found = _by_subscript(scheme, item_id, wanted)
    return found if isinstance(found, SdmxItem) else None


def _by_subscript(scheme: SupportsItemLookup, item_id: str, wanted: tuple[str, ...]) -> object | None:
    """One item out of a lazy facade, whose items are a query rather than a list.

    The class is checked on the way out because the subscript does not take one:
    ``LazyHierarchy['L1']`` answers with a node, and a `Level` URN asking for
    ``L1`` must not be handed one. A wrong class is a miss, never a wrong
    answer.
    """
    try:
        found = scheme[item_id]
    except KeyError, IndexError:
        return None
    return found if getattr(type(found), "sdmx_class", None) in wanted else None


def _matching_fields(owner_cls: type, wanted: tuple[str, ...]) -> Iterator[tuple[str, type[SdmxItem]]]:
    """The item fields of *owner_cls* whose class is one of *wanted*."""
    for name, item_cls in item_fields(owner_cls):
        if item_cls.sdmx_class in wanted:
            yield name, item_cls


def _by_id(value: object, item_id: str) -> object | None:
    """One item out of whatever an item field holds.

    `ItemList.get` is the O(1) path and covers nearly every field; a plain
    `list`/`tuple` is scanned, and a single-valued field
    (`DataStructure.time_dimension`) is compared directly.
    """
    if isinstance(value, (ItemList, ArtefactList)):
        return value.get(item_id)
    if isinstance(value, (list, tuple)):
        return next((item for item in value if getattr(item, "id", None) == item_id), None)
    return value if getattr(value, "id", None) == item_id else None


def _descend(owner: object, owner_cls: type, segments: list[str], wanted: tuple[str, ...]) -> object | None:
    """Follow a dotted item path, one O(1) lookup per segment."""
    for name, item_cls in _matching_fields(owner_cls, wanted):
        item = _by_id(getattr(owner, name, None), segments[0])
        if item is None:
            continue
        if len(segments) == 1:
            return item
        found = _descend(item, item_cls, segments[1:], wanted)
        if found is not None:
            return found
    return None


def item_urn_of[T: SdmxItem](scheme: SupportsUrn, item_cls: type[T], item_id: str) -> SdmxUrn[T]:
    """The URN of *item_id* inside *scheme*, as SDMX spells it.

    The inverse of `scheme_urn_of`, and the constructive half of what #404 and
    #405 made the library able to *read*. Agency, id and version come off the
    scheme rather than from the caller, which is the whole point: a URN built by
    hand can name a parent that does not exist — six keyword arguments, three of
    them restating the scheme — and `validate_references` will now correctly
    report that as broken (#407).
    """
    return SdmxUrn.make(
        item_cls,
        package=item_cls.sdmx_package,
        artefact_class=item_cls.sdmx_class,
        agency=scheme.agency_id,
        id=scheme.id,
        version=scheme.version,
        item_id=item_id,
    )


def locate_item(scheme: object) -> Iterator[tuple[str, type[SdmxItem]]]:
    """The (field name, item class) pairs `ref_to` searches, in order."""
    yield from item_fields(type(scheme))


def item_ref[T: SdmxItem](scheme: SupportsUrn, item_id: str, item_cls: type[T]) -> Ref[T]:
    """A resolved `Ref` to one item of *scheme*, found by path.

    *item_id* is the item part of the URN: a root's id, or a nested item's
    dotted path, which the URN carries unchanged.

    Raises:
        KeyError: If no item of that class in *scheme* has this path. The
            message names the scheme, so a caller who passed the right id to
            the wrong artefact is told which artefact answered.
    """
    found = _descend(scheme, type(scheme), item_id.split("."), spellings(item_cls.sdmx_class))
    if found is None and isinstance(scheme, SupportsItemLookup):
        # A lazy facade keeps its items behind a query, so the declared-field
        # walk finds nothing; its subscript is the targeted lookup. Same order
        # as `find_item`, and the reason `LazyCodelist.ref_to` can exist at all
        # rather than being refused until you materialise (#407).
        found = _by_subscript(scheme, item_id, spellings(item_cls.sdmx_class))
    # `isinstance` rather than a cast: it narrows `_descend`'s `object | None` to
    # `T` with a runtime check, and covers "not found" in the same branch.
    if not isinstance(found, item_cls):
        msg = (
            f"no {item_cls.sdmx_class} with id {item_id!r} in "
            f"{type(scheme).__name__} {scheme.agency_id}:{scheme.id}({scheme.version})"
        )
        raise KeyError(msg)
    return Ref[T].of(item_urn_of(scheme, item_cls, item_id), found)


def any_item_ref(scheme: SupportsUrn, item_id: str) -> Ref[object]:
    """A resolved `Ref` to one item of *scheme*, whichever kind holds the id.

    For a `DataStructure`, whose `dimensions`, `time_dimension`,
    `measure_dimension`, `measures`, `attributes` and `groups` are six item
    lists under one maintainable. Dispatching is unambiguous rather than
    convenient: `storage.schema` declares
    ``PRIMARY KEY (dsd_urn, component_id)`` on `dsd_component`, so this library
    already treats a component id as unique across the whole DSD, whichever
    list it sits in (#407).

    `Ref[object]` because the class varies and every field that consumes one of
    these — `HierarchyAssociation.linked_object`, `Categorisation.target` — is
    declared `Ref[object]`. The URN carries the real class, so nothing is lost
    at runtime; a caller who needs it statically can `isinstance` the unwrapped
    item.

    Raises:
        KeyError: If no item of *scheme* has this id, naming the fields
            searched so the message says where it looked.
    """
    segments = item_id.split(".")
    for name, item_cls in locate_item(scheme):
        found = _by_id(getattr(scheme, name, None), segments[0])
        if found is not None and len(segments) > 1:
            found = _descend(found, item_cls, segments[1:], spellings(item_cls.sdmx_class))
        # `isinstance` rather than `is not None`: it narrows to the item class,
        # which is what `Ref.of` needs to type the pair it is handed.
        if isinstance(found, item_cls):
            return Ref[object].of(item_urn_of(scheme, item_cls, item_id), found)
    searched = ", ".join(name for name, _ in locate_item(scheme)) or "no item fields"
    msg = (
        f"no item with id {item_id!r} in {type(scheme).__name__} "
        f"{scheme.agency_id}:{scheme.id}({scheme.version}) — searched {searched}"
    )
    raise KeyError(msg)
