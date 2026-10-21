"""Which fields of an attrs class can reach a `Ref`, computed once per class.

Three places walk the model's attrs graph looking for `Ref`s, each rediscovering
the same structure at runtime, per object:

| walker | what it does with the refs |
| --- | --- |
| `local.registry._collect_refs` | collects unresolved ones to batch-resolve |
| `model._rebind.bind` | binds them to a registry (#492) |
| `model.validation._collect_refs` | records their paths for error messages |

The structure they rediscover is a property of the *class*, not the object, so
rediscovering it per object is pure waste. On the 2021 Census Profile's `CL_GEOGRAPHY`,
`_collect_refs` is **3.29 s of a 6.36 s eager read — 52%** — and for a `Code`
it is guaranteed to find nothing, because `Code` has no field that can hold a
`Ref` and none that recurses anywhere that might (#376).

This module computes that once per class and caches it.

## The assumption, stated rather than buried

`can_reach_ref` reads **declared types**. Taking a declaration as a guarantee
about a runtime value is an assumption, and #376 says so: a field annotated
`str | None` that somehow held a `Ref` would silently stop resolving. Three
things bound it.

**It is conservative where it cannot see.** `object`, `Any`, a bare
`TypeVar`, an unresolved string annotation — anything this module cannot read
is treated as *possibly* holding a `Ref`, so an unreadable annotation costs
speed and never correctness. `_reaches` returns True on everything it does not
understand.

**It is checked against the dynamic walk it replaces.**
`tests/unit/test_refshape_agrees_with_reflection.py` builds real artefacts and
asserts that every field a fresh reflective walk finds interesting is in
`walk_fields`, over every attrs class in `sdmxlib.model`. That is the
differential shape the query oracles use, and the oracle here is the code this
replaces.

**Converters make it near-certain in practice.** The model uses
`field(converter=…)` throughout, so a field's runtime type follows its
declaration by construction rather than by convention. Near-certain is not
checked, which is why the test above exists rather than a comment saying it is
fine.
"""

import functools
import types
import typing
from collections.abc import Iterator
from typing import Final, TypeGuard, Union, get_args, get_origin

import attrs

from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.collections import ArtefactList, ItemList, SdmxItem
from sdmxlib.model.link import Link
from sdmxlib.model.ref import Ref

#: Marks a field as a *derived view*: its contents are reachable through some
#: other field of the same object, so a graph walk that visits both sees every
#: object twice.
#:
#: `Hierarchy._codes` is `_flatten_nodes(self.tree)` — the same node objects,
#: flattened for O(1) lookup — so a `Ref`-hunting walk entered every
#: `HierarchicalCode` twice, once through `tree` and once through the index.
#: The `walked` set made the second visit an immediate return, so this was
#: never a correctness problem; it was 499,138 wasted calls on
#: `H_CP_GEO_FED` (#383).
#:
#: The contract is *reachability*, not privacy: a marked field must contain
#: nothing that an unmarked field does not already reach.
#: `tests/unit/test_derived_fields_are_reachable_anyway.py` checks that for
#: every field carrying the marker, which is what stops this becoming a way to
#: hide an edge.
DERIVED_VIEW: Final = "sdmxlib.derived_view"


def derived_view() -> dict[str, bool]:
    """Field metadata marking a derived view — see `DERIVED_VIEW`."""
    return {DERIVED_VIEW: True}


#: Containers whose *elements* may be attrs objects. A tuple rather than a
#: `|` union because `issubclass` needs bare runtime classes, and writing the
#: union inline made pyrefly ask for type arguments it cannot have there.
_CONTAINERS: Final[tuple[type, ...]] = (ItemList, ArtefactList, list, tuple)

#: Recursion bound for a pathological type expression. Nothing in the model is
#: near it; it exists so a self-referential alias cannot hang a read.
_MAX_DEPTH = 12


def _is_opaque(annotation: object) -> bool:
    """Whether the annotation says nothing useful about what a field may hold.

    `object` and `Any` both pass `isinstance(x, type)` on 3.11+, so without
    this they would be read as ordinary classes, found to be neither a `Ref`
    nor an attrs class, and the field skipped. That is the one way this module
    could be *unsafe* rather than merely slow, so it is checked first.
    """
    return annotation is object or annotation is typing.Any


def _classes_in(annotation: object, depth: int = 0) -> Iterator[type | None]:
    """Every class mentioned in a type expression, or `None` where unreadable.

    `None` is the "I cannot tell" signal, and every caller treats it as
    possibly-a-`Ref`. `object` and `Any` yield it, as does anything whose shape
    this does not model — a string annotation that never got resolved, a bare
    `TypeVar`, a `Callable`'s parameter list — and so does a container with no
    element type.
    """
    if depth > _MAX_DEPTH or _is_opaque(annotation):
        yield None
        return
    if annotation is None or annotation is type(None):
        return
    if annotation is Ellipsis:
        # The `...` in `tuple[X, ...]`. It is a *shape* marker — "homogeneous,
        # any length" — and carries no type, so reading it as an unresolvable
        # annotation and answering `None` made every homogeneous tuple field
        # conservatively walkable. `Metadataflow.targets` is `tuple[str, ...]`
        # and was walked on the strength of the ellipsis alone (#392).
        return
    origin = get_origin(annotation)
    if origin in (Union, types.UnionType):
        for arg in get_args(annotation):
            yield from _classes_in(arg, depth + 1)
        return
    if origin is not None:
        yield from _parameterised(annotation, origin, depth)
        return
    if isinstance(annotation, type):
        yield annotation
        if _is_container(annotation):
            # Bare `ItemList` / `list`. `_reaches` descends only through
            # `attrs.has` mentions, so without this a class whose only route to
            # a `Ref` ran through an unparameterised collection would be
            # skipped and its refs silently left unresolved.
            yield None
        return
    # A string annotation attrs could not resolve, a TypeVar, a Literal, …
    yield None


def _parameterised(annotation: object, origin: object, depth: int) -> Iterator[type | None]:
    """A parameterised generic: the container, then its element types.

    `ItemList[HierarchicalCode]` is the shape that matters here, but
    `list[...]` / `tuple[...]` are treated the same way rather than
    special-cased, so a field that changes container does not silently stop
    being walked.
    """
    container = origin if isinstance(origin, type) else None
    if container is not None:
        yield container
    args = get_args(annotation)
    if not args and _is_container(container):
        # Same argument as the bare case in `_classes_in`.
        yield None
    for arg in args:
        yield from _classes_in(arg, depth + 1)


def _is_ref(cls: type | None) -> bool:
    return cls is not None and isinstance(cls, type) and issubclass(cls, Ref)


@functools.cache
def walk_fields(cls: type) -> tuple[str, ...]:
    """Field names a `Ref`-hunting walk needs to read on *cls*.

    A field is included when its declared type mentions a `Ref`, an attrs
    class, a container that could hold either, or anything unreadable. The
    excluded ones are those that provably hold only scalars, `InternationalString`,
    `Annotations`, enums and the like — for `Code` that is every field, which
    is the point of the exercise.

    Callers still `isinstance`-check what they get back: this narrows *which
    fields to read*, and does not claim to know what a read will return.
    """
    if not attrs.has(cls):
        return ()
    # No `_is_container` arm in the predicate, though there was one until a
    # mutation check showed it decided nothing: `_classes_in` already yields
    # `None` for a container with no element type, and a parameterised one
    # yields its element type, which `attrs.has` catches. It was redundant in
    # both directions while reading as though it were load-bearing.
    return tuple(
        f.name
        for f in attrs.fields(cls)
        if not f.metadata.get(DERIVED_VIEW) and _may_hold_ref(list(_classes_in(f.type)))
    )


def _may_hold_ref(mentioned: list[type | None]) -> bool:
    """Whether a field mentioning these classes is worth a `Ref`-hunting read.

    One exclusion beyond scalars (#422): a plain ``tuple`` or ``list`` of attrs
    objects none of which can reach a `Ref` holds nothing a walk wants. That is
    `links`, a `tuple[Link, ...]` beside nearly every `uri`, which would
    otherwise make every `Code` walkable and every hierarchy node read one
    more field.
    """
    if any(c is None or _is_ref(c) for c in mentioned):
        return True
    held = [c for c in mentioned if c is not None and attrs.has(c)]
    if not held:
        return False
    containers = [c for c in mentioned if c is not None and _is_container(c)]
    plain = bool(containers) and all(c in (tuple, list) for c in containers)
    return not plain or any(can_reach_ref(c) for c in held)


@functools.cache
def link_fields(cls: type) -> tuple[str, ...]:
    """Field names a `Link`-hunting walk needs to read on *cls* (#422).

    `walk_fields` for `Link`: a field whose declared type mentions one, an
    attrs class that can reach one, or anything unreadable. Only SDMX-JSON's
    write checks walk for links, but over every code of a codelist, so the
    shape is read once per class here rather than per object there.
    """
    if not attrs.has(cls):
        return ()
    return tuple(
        f.name
        for f in attrs.fields(cls)
        if not f.metadata.get(DERIVED_VIEW)
        and any(c is None or c is Link or (attrs.has(c) and _reaches_link(c, frozenset())) for c in _classes_in(f.type))
    )


def _reaches_link(cls: type, seen: frozenset[type]) -> bool:
    """`_reaches` for `Link`: whether a walk from *cls* could arrive at one."""
    if cls is Link:
        return True
    if cls in seen or not attrs.has(cls):
        return False
    seen = seen | {cls}
    return any(
        mentioned is None or (attrs.has(mentioned) and _reaches_link(mentioned, seen))
        for f in attrs.fields(cls)
        for mentioned in _classes_in(f.type)
    )


@functools.cache
def walk_plan(cls: type) -> tuple[tuple[str, ...], frozenset[str]]:
    """`walk_fields` split into the fields to walk and the `Ref` containers.

    One cached lookup where a walker would otherwise make two, and the fields
    come back already disjoint from the containers — so the caller's per-field
    loop keeps exactly the branches it had before `ref_container_fields`
    existed. That matters because that loop runs once per object in the graph:
    499,138 times on the 2021 Census Profile's largest hierarchy.

    Returns:
        `(fields, ref_containers)` — the fields to read one by one, and the
        names whose value is a container of `Ref`s to be recorded whole.
    """
    containers = ref_container_fields(cls)
    if not containers:
        return walk_fields(cls), containers
    return tuple(name for name in walk_fields(cls) if name not in containers), containers


@functools.cache
def ref_container_fields(cls: type) -> frozenset[str]:
    """Fields of *cls* that are a container **of `Ref`s**, not of artefacts.

    `walk_fields` says which fields a `Ref`-hunting walk must read;
    this says which of them hold `Ref`s *directly* as elements. The
    difference decides what a walker does per element, and doing it per class
    is what keeps that free.

    Without it a walker must ask `isinstance(item, Ref)` of every element it
    iterates, which on `CL_GEOGRAPHY` is 579,951 questions to which the class
    already knew the answer — measured as the entire cost of #392's fix before
    this existed.

    Four fields qualify today: `MetadataSet.targets`,
    `HierarchicalCodelist.hierarchies` and `.included_codelists`,
    `StructureSet.maps` and `.related_structures`. Every other container in the
    model holds *items* — `ItemList[Code]`, `ItemList[HierarchicalCode]` — which
    a walk descends into rather than resolves.
    """
    if not attrs.has(cls):
        return frozenset()
    return frozenset(
        f.name for f in attrs.fields(cls) if not f.metadata.get(DERIVED_VIEW) and _holds_refs_directly(f.type)
    )


def _holds_refs_directly(annotation: object) -> bool:
    """Whether the annotation is a container whose *elements* are `Ref`s."""
    origin = get_origin(annotation)
    if origin is None or not _is_container(origin if isinstance(origin, type) else None):
        return False
    return any(
        _is_ref(get_origin(arg) or arg if isinstance(get_origin(arg) or arg, type) else None)
        for arg in get_args(annotation)
    )


def _is_container(cls: type | None) -> bool:
    """A container whose *elements* may be attrs objects.

    Kept separate from `attrs.has` because `ItemList` is not an attrs class:
    it is a plain collection, so a field typed `ItemList[HierarchicalCode]`
    mentions both and only the element type is worth descending into.
    """
    if cls is None or not isinstance(cls, type):
        return False
    return issubclass(cls, _CONTAINERS)


@functools.cache
def can_reach_ref(cls: type) -> bool:
    """Whether any walk from *cls* could arrive at a `Ref`.

    False means a `Ref`-hunting walk can return immediately. `Code` is the
    case that motivated this: its fields are `str`, `InternationalString`,
    `Annotations` and `str | None`, none of which is a `Ref` and none of which
    recurses anywhere that could hold one — so the walk was doing one
    `attrs.has`, one `attrs.fields`, six `getattr` and four more probes per
    code, 579,951 times, guaranteed to find nothing.

    Unreadable annotations answer True, so this is a *lower* bound on
    ignorance: it says "definitely not" or "maybe", never "definitely yes".
    """
    return _reaches(cls, frozenset())


def _reaches(cls: type, seen: frozenset[type]) -> bool:
    """`can_reach_ref` with the cycle set threaded through.

    `seen` makes a self-referential class terminate —
    `HierarchicalCode.children` is an `ItemList[HierarchicalCode]`, so a naive
    closure would not.
    """
    if cls in seen or not attrs.has(cls):
        return False
    seen = seen | {cls}
    for f in attrs.fields(cls):
        for mentioned in _classes_in(f.type):
            if mentioned is None or _is_ref(mentioned):
                return True
            if attrs.has(mentioned) and _reaches(mentioned, seen):
                return True
    return False


def _is_item(cls: type) -> TypeGuard[type[SdmxItem]]:
    """Whether *cls* is an SDMX **item** — something with an item URN.

    The signal is the `sdmx_package` / `sdmx_class` pair every item class
    declares (#240, #404), minus the maintainables, which declare the same
    pair for their own scheme-level URN. Reading the class rather than a list
    here is the same choice `walk_fields` makes: a new item-bearing scheme
    then costs a declaration on the item, not an entry in a table somewhere
    else that nobody updates.
    """
    return (
        attrs.has(cls)
        and not issubclass(cls, MaintainableArtefact)
        and getattr(cls, "sdmx_class", None) is not None
        and getattr(cls, "sdmx_package", None) is not None
    )


def _item_classes_in(annotation: object, depth: int = 0) -> Iterator[type[SdmxItem]]:
    """Item classes a field may *hold* — never one it merely references.

    Deliberately **not** `_classes_in`, which is right for `Ref`-hunting and
    wrong here: it reads every class a type expression mentions, so
    `HierarchicalCode.code: Ref[Code] | None` mentions `Code` and the field
    would read as *holding* one. It holds a `Ref`, whose `.id` is a property
    over the URN — so a caller registering `item.id` from it would invent an
    item URN in the referring artefact's own scheme, and a broken reference
    would resolve against the thing that broke it.

    A `Ref` therefore terminates the walk. Everything unreadable yields
    nothing, which is conservative in the direction that keeps
    `validate_references` honest: an item field this cannot see stays
    unregistered, and a reference to it reports as broken rather than
    silently passing.
    """
    if depth > _MAX_DEPTH or _is_opaque(annotation) or annotation is None:
        return
    origin = get_origin(annotation)
    if origin in (Union, types.UnionType):
        for arg in get_args(annotation):
            yield from _item_classes_in(arg, depth + 1)
        return
    if origin is not None:
        container = origin if isinstance(origin, type) else None
        if _is_ref(container) or not _is_container(container):
            return
        for arg in get_args(annotation):
            yield from _item_classes_in(arg, depth + 1)
        return
    if isinstance(annotation, type) and _is_item(annotation):
        yield annotation


#: Field names the maintainable surface itself declares — a scheme's identity
#: and metadata, never its contents. `MaintainableArtefact` annotates
#: `maintainer: Agency`, and `Agency` is an item class, so without this every
#: scheme would read as holding one item: its own maintainer, registered under
#: the scheme's coordinates. It is not there; it lives in an `AgencyScheme`.
#:
#: Read off the mixin rather than hand-listed, so a field added to the
#: maintainable surface is covered by having been added there. The mixin
#: states its fields as properties its attrs subclasses override, so those are
#: what is read; its other properties (`urn`, `is_final`, `agency_id`) name no
#: attrs field, so including them excludes nothing.
_MAINTAINABLE_SURFACE: Final[frozenset[str]] = frozenset(
    name for name, member in vars(MaintainableArtefact).items() if isinstance(member, property)
)


@functools.cache
def _declared_types(cls: type) -> dict[str, object]:
    """Resolved annotations for *cls*, or empty when they cannot be resolved.

    `attrs.fields` hands back the *raw* declaration, which for a
    self-referential field is a string — `Category.categories` is
    `"ItemList[Category]"`, because the class is not bound while its own body
    runs. Those are exactly the fields that nest, so reading them as
    unreadable (which is what `walk_fields` does, safely, by walking them
    anyway) would leave every nested item unregistered and every reference to
    one reported as broken.

    The two sources disagree only there. A maintainable's annotations cannot be
    resolved at all — `MaintainableArtefact` annotates `maintainer: Agency`
    under `TYPE_CHECKING`, so `get_type_hints` raises `NameError` for every
    subclass — and a maintainable has no self-referential item field, so the
    raw declaration is already a type. Hence: hints where they resolve, raw
    declaration otherwise, and `tests/unit/test_validation.py`'s nested
    category and nested metadata-attribute cases are what say the resolving
    half works.
    """
    try:
        return dict(typing.get_type_hints(cls))
    except NameError, TypeError:
        return {}


@functools.cache
def item_fields(cls: type) -> tuple[tuple[str, type[SdmxItem]], ...]:
    """`(field name, item class)` for every field of *cls* that holds items.

    One entry per (field, item class) pair, in declaration order. Both shapes
    the model uses are here and the caller tells them apart by what it reads:
    `DataStructure.dimensions` is an `ItemList[Dimension]`, while
    `time_dimension` and `measure_dimension` are **single** components — a
    detail worth stating because an "accessor column" in a table cannot
    express the second, and SDMX gives the time dimension its own item URN.

    Derived views are skipped, as in `walk_fields`: `CategoryScheme._flat`
    holds the same `Category` objects `categories` reaches, so walking both
    registers every nested item twice and at the wrong depth.

    Items nest — `Category.categories`, `MetadataAttribute.children` — and
    this answers for an item class as readily as for a scheme, so the caller
    recurses with it rather than needing a second shape for the nested case.
    """
    if not attrs.has(cls):
        return ()
    hints = _declared_types(cls)
    return tuple(
        (f.name, item_cls)
        for f in attrs.fields(cls)
        if not f.metadata.get(DERIVED_VIEW) and f.name not in _MAINTAINABLE_SURFACE
        for item_cls in _item_classes_in(hints.get(f.name, f.type))
    )
