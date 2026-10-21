"""An artefact, with every `Ref` in it bound to a registry (#492).

`Registry.add` used to resolve the references inside the caller's artefact by
swapping canonical `Ref`s into its fields. A `Ref` is now frozen and finds its
target through the registry that bound it, so `add` stores a copy whose
references are bound to its `Resolver` and leaves the caller's object as it
was built.

The copy is as shallow as it can be: a field that holds no `Ref` is shared,
not copied, and an object none of whose references change is returned
itself. `_refshape` already knows which fields of which class can hold a
reference, so a 579,951-code codelist whose codes hold none is not walked
code by code.
"""

from collections.abc import Callable
from typing import TypeIs

import attrs

from sdmxlib.model._binding import Resolver
from sdmxlib.model._refshape import can_reach_ref, walk_fields
from sdmxlib.model.collections import ItemList, SupportsId
from sdmxlib.model.ref import Ref


def bind[T](obj: T, resolver: Resolver) -> T:
    """*obj*, or a copy of it whose every `Ref` *resolver* binds.

    Args:
        obj: An artefact, or anything that holds references.
        resolver: The registry's map the references will look their targets up in.

    Returns:
        *obj* itself if every reference in it is already bound to *resolver*.
    """
    rebound = _bind(obj, resolver)
    # The walk rebuilds each object as its own class, so this is a check, not a
    # narrowing the walk could get wrong silently.
    cls: type[T] = type(obj)
    if not isinstance(rebound, cls):
        msg = f"rebinding a {type(obj).__name__} built a {type(rebound).__name__}"
        raise TypeError(msg)
    return rebound


#: `attrs.evolve` over a field map built at runtime, which its overloads cannot
#: see — the spelling `version_rewrite` uses for the same walk.
_evolve: Callable[..., object] = attrs.evolve


def _bind(value: object, resolver: Resolver) -> object:
    if isinstance(value, Ref):
        if value._resolver is resolver:
            return value
        return Ref(urn=value.urn, _target=value._target, _resolver=resolver)
    if isinstance(value, (ItemList, list, tuple)):
        return _bind_sequence(value, resolver)
    if not _is_attrs_instance(value):
        return value
    cls = type(value)
    if not can_reach_ref(cls):
        return value
    changes: dict[str, object] = {}
    for name in walk_fields(cls):
        held: object = getattr(value, name, None)
        new = _bind(held, resolver)
        if new is not held:
            changes[_init_name(cls, name)] = new
    return _evolve(value, **changes) if changes else value


def _bind_sequence(value: ItemList[SupportsId] | list[object] | tuple[object, ...], resolver: Resolver) -> object:
    """An `ItemList`, list or tuple, rebuilt as its own type only if an element changed."""
    items = list(value)
    changed = [_bind(item, resolver) for item in items]
    if all(new is old for new, old in zip(changed, items, strict=True)):
        return value
    if isinstance(value, ItemList):
        return type(value)([item for item in changed if isinstance(item, SupportsId)])
    return type(value)(changed)


def _is_attrs_instance(value: object) -> TypeIs[attrs.AttrsInstance]:
    # On the value, not `attrs.has(type(value))`, whose guard lands on the
    # discarded `type(value)`. A class is not an instance.
    return not isinstance(value, type) and attrs.has(type(value))


def _init_name(cls: type, name: str) -> str:
    """The keyword `__init__` takes for field *name*: its alias, for a private field."""
    field = attrs.fields_dict(cls)[name]
    return field.alias or name
