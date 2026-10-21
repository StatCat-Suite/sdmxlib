"""Where two model objects for the same artefact disagree, as field paths.

Used twice: an artefact against itself after a store round trip (what the
DuckDB store loses), and the 2.1 reading of an artefact against its 3.0
reading (what the projection costs, and what each reader disagrees on).

The result is a set of *normalised* paths — `DataStructure.dimensions[].
codelist`, never `dimensions[3]` or `dimensions[FREQ]` — because the summary
counts artefacts per path, and a path that names an index or an id would make
every artefact its own row.

Two rules decide what "the same" means, and both are about not recursing into
the graph:

- A `Ref` is equal when its URN is. Whether one side happened to be resolved
  is the registry's state, not the artefact's.
- Private attrs fields (`_flat`, `_codes`, `_str_cache`) are derived state the
  class maintains itself (CLAUDE.md, the public/private barrier, rule 1), so
  they are skipped; comparing them would compare caches.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TypeIs

import attrs

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
    """Path → example, for every field where `left` and `right` disagree."""
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
        if left.as_dict(copy=False) != right.as_dict(copy=False):
            _walk_istring(left, right, path, out)
        return True
    return False


def _is_attrs_instance(value: object) -> TypeIs[attrs.AttrsInstance]:
    """`attrs.has(type(value))`, narrowing `value` rather than the discarded `type(value)`."""
    return not isinstance(value, type) and attrs.has(type(value))


def _walk_container(left: object, right: object, path: str, out: Differences, seen: set[tuple[int, int]]) -> None:
    if _is_attrs_instance(left):
        for field in attrs.fields(type(left)):
            if not field.name.startswith("_"):
                _walk(getattr(left, field.name), getattr(right, field.name), f"{path}.{field.name}", out, seen)
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
    for key in left.keys() & right.keys():
        _walk(left[key], right[key], f"{path}[]", out, seen)


def _walk_istring(left: InternationalString, right: InternationalString, path: str, out: Differences) -> None:
    a, b = left.as_dict(copy=False), right.as_dict(copy=False)
    if a.keys() != b.keys():
        out.note(f"{path} (languages)", sorted(a), sorted(b))
        return
    lang = next(k for k in a if a[k] != b[k])
    out.note(f"{path} (text)", a[lang], b[lang])
