"""`sl.diff`: where two model objects for the same artefact disagree, as field paths (GH-401).

The result maps each *normalised* path to its first example,
``"before → after"``. A path names fields, never an index or an id —
``DataStructure.dimensions[].codelist``, not ``dimensions[3]`` or
``dimensions[FREQ]`` — so the same disagreement in many items is one entry,
and paths from many artefacts can be counted.

What "the same" means:

- **A `Ref` is equal when its URN is**, and the walk does not follow it:
  whether one side was resolved is a registry's state, not the artefact's.
- **Private attrs fields are skipped** (``_flat``, ``_codes``,
  ``_str_cache``): derived state the class maintains itself.
- **An `ItemList` is compared by id**: items only one side holds are
  reported as ``path[] (only left)`` / ``(only right)``, and items both hold
  are walked.
- **A sequence is compared in order**, reported as ``path (length)`` when the
  lengths differ, except a field marked `unordered` (an attribute's related
  dimensions), compared as a set and reported as ``path (set)``.
- **An `InternationalString`** differs by its languages
  (``path (languages)``) or by a text (``path (text)``).
- **Values of different types** are reported as ``path (type)``.

Used to check a migration (the 2.1 source against the 3.0 result, the result
against what a registry then serves), the store's round trip, and the
interoperability oracles.
"""

from collections.abc import Iterable, Mapping, Sequence
from typing import TypeIs

import attrs

from sdmxlib.model._refshape import UNORDERED
from sdmxlib.model.collections import ItemList, SupportsId
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.ref import Ref
from sdmxlib.model.urn import SdmxUrn

#: How long an example value may be in the full report before it is cut.
_EXAMPLE = 120


def _short(value: object) -> str:
    text = repr(value)
    return text if len(text) <= _EXAMPLE else text[: _EXAMPLE - 1] + "…"


class Differences:
    """Paths where two objects disagree, each with its first example."""

    def __init__(self) -> None:
        self.paths: dict[str, str] = {}

    def note(self, path: str, left: object, right: object) -> None:
        """Record `path`, keeping the first example seen for it."""
        self.paths.setdefault(path, f"{_short(left)} → {_short(right)}")


def diff(left: object, right: object) -> dict[str, str]:
    """Every field where *left* and *right* disagree, as normalised path → first example.

    Args:
        left: A model object, typically one artefact as read from one source.
        right: The object to compare it with, of the same type.

    Returns:
        Path → ``"left → right"`` for each disagreement; empty when they
        agree. The module docstring states what counts as agreeing.

    Example:
        sl.diff(codelist_21, codelist_30)   # {"Codelist.codes[].name (text)": "'a' → 'b'"}
    """
    out = Differences()
    _walk(left, right, type(left).__name__, out, set())
    return out.paths


def _walk(left: object, right: object, path: str, out: Differences, seen: set[tuple[int, int]]) -> None:
    if left is right:
        return
    if type(left) is not type(right):
        out.note(f"{path} (type)", type(left).__name__, type(right).__name__)
        return
    if _identity(left, right, path, out):
        return
    pair = (id(left), id(right))
    if pair in seen:
        return
    seen.add(pair)
    _walk_container(left, right, path, out, seen)


def _identity(left: object, right: object, path: str, out: Differences) -> bool:
    """Compare a value that is equal by identity rather than by structure.

    Returns:
        Whether `left` was one of those, so the walk stops here.
    """
    if isinstance(left, Ref) and isinstance(right, Ref):
        if str(left.urn) != str(right.urn):
            out.note(path, str(left.urn), str(right.urn))
        return True
    if isinstance(left, SdmxUrn):
        if str(left) != str(right):
            out.note(path, str(left), str(right))
        return True
    if isinstance(left, InternationalString) and isinstance(right, InternationalString):
        if left._backing() != right._backing():
            _walk_istring(left, right, path, out)
        return True
    return False


def _is_attrs_instance(value: object) -> TypeIs[attrs.AttrsInstance]:
    """`attrs.has(type(value))`, narrowing `value` rather than the discarded `type(value)`."""
    return not isinstance(value, type) and attrs.has(type(value))


def _walk_container(left: object, right: object, path: str, out: Differences, seen: set[tuple[int, int]]) -> None:
    if _is_attrs_instance(left):
        _walk_fields(left, right, path, out, seen)
    elif isinstance(left, ItemList) and isinstance(right, ItemList):
        _walk_keyed(_by_id(left), _by_id(right), path, out, seen)
    elif isinstance(left, Mapping) and isinstance(right, Mapping):
        _walk_keyed(dict(left.items()), dict(right.items()), path, out, seen)
    elif isinstance(left, Sequence) and isinstance(right, Sequence) and not isinstance(left, str | bytes):
        if len(left) != len(right):
            out.note(f"{path} (length)", len(left), len(right))
            return
        for a, b in zip(left, right, strict=True):
            _walk(a, b, f"{path}[]", out, seen)
    elif left != right:
        out.note(path, left, right)


def _walk_fields(
    left: attrs.AttrsInstance, right: object, path: str, out: Differences, seen: set[tuple[int, int]]
) -> None:
    """Each public field of an attrs object; a field marked `unordered` compared as a set."""
    for field in attrs.fields(type(left)):
        if field.name.startswith("_"):
            continue
        a, b, where = getattr(left, field.name), getattr(right, field.name), f"{path}.{field.name}"
        if not field.metadata.get(UNORDERED):
            _walk(a, b, where, out, seen)
        elif _members(a) != _members(b):
            out.note(f"{where} (set)", sorted(map(str, _members(a))), sorted(map(str, _members(b))))


def _members(value: object) -> frozenset[object]:
    """The members of an unordered field's value: empty for None."""
    if value is None:
        return frozenset()
    if isinstance(value, Iterable) and not isinstance(value, str | bytes):
        return frozenset(value)
    return frozenset({value})


def _by_id(items: ItemList[SupportsId]) -> dict[object, object]:
    return dict(zip(items.ids(), items, strict=True))


def _walk_keyed(
    left: dict[object, object], right: dict[object, object], path: str, out: Differences, seen: set[tuple[int, int]]
) -> None:
    only_left = left.keys() - right.keys()
    only_right = right.keys() - left.keys()
    if only_left:
        out.note(f"{path}[] (only left)", sorted(map(str, only_left))[:3], len(only_left))
    if only_right:
        out.note(f"{path}[] (only right)", sorted(map(str, only_right))[:3], len(only_right))
    # In the left side's order, not a set's: the first example kept for a path
    # must not depend on the hash seed.
    for key in [key for key in left if key in right]:
        _walk(left[key], right[key], f"{path}[]", out, seen)


def _walk_istring(left: InternationalString, right: InternationalString, path: str, out: Differences) -> None:
    a, b = left._backing(), right._backing()
    if a.keys() != b.keys():
        out.note(f"{path} (languages)", sorted(a), sorted(b))
        return
    lang = next(k for k in a if a[k] != b[k])
    out.note(f"{path} (text)", a[lang], b[lang])
