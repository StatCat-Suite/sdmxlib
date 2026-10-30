"""The canonical text of a model object, which an artefact fingerprint hashes (GH-126).

Two artefacts with equal content give byte-equal text; any change to a field
the model holds changes it. That needs a form defined field by field rather
than borrowed from a wire format: SDMX-JSON cannot carry 28 of the model's
fields and writes the minimal spelling, and msgspec cannot encode the attrs
model at all.

The form, `CANONICAL_FORM` ``"1"``: compact JSON in UTF-8, with every
object's keys sorted, built as follows.

- An attrs object is an object holding ``"$class"``, its Python class name,
  and every field set through ``__init__``, ``null`` included. Fields the
  class derives itself (``init=False``) are left out.
- ``maintainer`` is the agency's id: it refers to an Agency item, whose own
  content belongs to its scheme's text.
- A `Ref` is its URN, never followed.
- An `InternationalString` is an object of language tag to text.
- A list, tuple, `ItemList` or `Annotations` is an array in its order; a set
  or frozenset is an array sorted by each element's canonical text.
- An enum is its member's name; a ``datetime`` is UTC, ``YYYY-MM-DDTHH:MM:SS.ffffffZ``;
  a ``date`` is ``YYYY-MM-DD``.
- A float is msgspec's shortest round-trip form, and a non-finite one is
  refused: JSON has no spelling for it.

Any other value is refused naming its type and field, so a new kind of field
fails here rather than encoding as something ambiguous. A change to any rule
above is a new `CANONICAL_FORM`; fingerprints made at different forms are
never compared.
"""

import enum
import math
from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import TypeIs

import attrs
import msgspec

from sdmxlib.model.annotations import Annotations
from sdmxlib.model.collections import ItemList
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.organisation import Agency
from sdmxlib.model.ref import Ref

#: The version of the rules above. Recorded beside every fingerprint made from them.
CANONICAL_FORM = "1"

_ENCODER = msgspec.json.Encoder(order="sorted")

#: A canonical value, as msgspec encodes it.
type _Plain = bool | int | float | str | list[_Plain] | dict[str, _Plain] | None


def canonical_json(obj: object) -> bytes:
    """The canonical text of a model object: an artefact, an item, a component.

    Args:
        obj: An attrs instance from `sdmxlib.model`. A store-backed
            `LazyCodelist` is not one and is refused; read the codelist with
            ``eager=True``.

    Returns:
        Compact, key-sorted UTF-8 JSON, as the module docstring defines it.

    Raises:
        TypeError: If a field holds a value the form does not define.
        ValueError: If a field holds a non-finite float or a naive datetime.
    """
    return _ENCODER.encode(_plain(obj, type(obj).__name__))


def _plain(value: object, path: str) -> _Plain:
    """`value` as plain JSON values; `path` names it in an error."""
    match value:
        # Before the attrs case: `Ref` is an attrs class, and its fields hold
        # the resolved target, which the form never follows.
        case Ref():
            return str(value.urn)
        case InternationalString():
            return dict(value._backing())
        case Annotations() | ItemList() | list() | tuple():
            return [_plain(item, f"{path}[{i}]") for i, item in enumerate(value)]
        case set() | frozenset():
            return sorted((_plain(item, f"{path}{{}}") for item in value), key=_ENCODER.encode)
        case Mapping():
            return _mapping(value, path)
        case _ if _is_attrs_instance(value):
            return _object(value, path)
        case _:
            return _scalar(value, path)


def _scalar(value: object, path: str) -> _Plain:
    """A value that is not a container: a JSON scalar, or a model value with one spelling."""
    match value:
        # Before `str()`: a `StrEnum` member is a `str`, and is spelled by name too.
        case enum.Enum():
            return value.name
        case None | bool() | int() | str():
            return value
        case float():
            if not math.isfinite(value):
                msg = f"{path} is {value}, which canonical JSON cannot spell"
                raise ValueError(msg)
            return value
        case datetime():
            if value.tzinfo is None:
                msg = f"{path} is a naive datetime; the model holds every datetime in UTC"
                raise ValueError(msg)
            return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        case date():
            return value.isoformat()
        case _:
            msg = f"{path} holds a {type(value).__name__}, which the canonical form does not define"
            raise TypeError(msg)


def _is_attrs_instance(value: object) -> TypeIs[attrs.AttrsInstance]:
    """An attrs instance, narrowed on the value (``attrs.has`` takes a class)."""
    return not isinstance(value, type) and attrs.has(type(value))


def _mapping(value: Mapping[object, object], path: str) -> dict[str, _Plain]:
    plain: dict[str, _Plain] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            msg = f"{path} has a {type(key).__name__} key; canonical JSON keys are strings"
            raise TypeError(msg)
        plain[key] = _plain(item, f"{path}[{key!r}]")
    return plain


def _object(value: attrs.AttrsInstance, path: str) -> dict[str, _Plain]:
    cls = type(value)
    plain: dict[str, _Plain] = {"$class": cls.__name__}
    for field in attrs.fields(cls):
        if not field.init:
            continue
        item: object = getattr(value, field.name)
        if field.name == "maintainer" and isinstance(item, Agency):
            plain[field.name] = item.id
        else:
            plain[field.name] = _plain(item, f"{path}.{field.name}")
    return plain
