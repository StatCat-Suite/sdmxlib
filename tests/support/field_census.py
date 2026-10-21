"""Round-trip an artefact and report *every* field that changed.

The existing round-trip guard compares hand-written `Check`s: someone decides
which fields matter, writes a lambda per field, and the ones nobody wrote stay
unguarded forever. That is how #345, #346 and #354 all happened — each was a
field no `Check` named, in a format nobody had listed.

This inverts it. Walk the artefact graph and compare *everything*, so a field is
covered by existing and not by being remembered. The list that has to be
maintained by hand is then the list of **known losses**, which is the useful
direction: it shrinks as bugs are fixed, and an entry falling off is a visible
diff rather than a silent one.

Two normalisations, both load-bearing:

* A `Ref` compares by URN. Descending into one raises `UnresolvedRef` for the
  unresolved side, and resolution state is not what a wire format round-trips.
* A `set` / `frozenset` compares unordered. `KeyValue.values` is a frozenset,
  and iteration order is not information the format lost.

Everything else compares as itself, including `None` vs empty, which is the
distinction most of the known losses live in.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from typing import NamedTuple

import attrs

from sdmxlib.model.istring import InternationalString
from sdmxlib.model.ref import Ref


class Difference(NamedTuple):
    """One field that did not survive, as a path plus both sides."""

    path: str
    before: object
    after: object

    def __str__(self) -> str:
        return f"{self.path}: {self.before!r} -> {self.after!r}"


def _normalise(value: object) -> object:
    """A `Ref` as its URN string; everything else unchanged."""
    return f"ref:{value.urn}" if isinstance(value, Ref) else value


def _is_map(value: object) -> bool:
    return isinstance(value, (dict, InternationalString))


def _as_map(value: object) -> dict[object, object]:
    """A mapping side as a plain dict; the `None` of a dropped field as empty."""
    if isinstance(value, InternationalString):
        return dict(value.items())
    return dict(value) if isinstance(value, dict) else {}


def _is_sequence(value: object) -> bool:
    return hasattr(value, "__iter__") and not isinstance(value, str | bytes)


def _elements(value: object) -> list[object]:
    """`value`'s elements, normalised — none at all when `value` is falsy.

    A falsy side is the `None` (or empty collection) of a dropped field, so it
    compares as empty. A truthy side reaching here was judged a sequence by
    `_is_sequence`; anything else is not iterable and says so.
    """
    if not value:
        return []
    if not isinstance(value, Iterable):
        msg = f"{type(value).__name__!r} object is not iterable"
        raise TypeError(msg)
    return [_normalise(x) for x in value]


def _sequence_differences(before: object, after: object, path: str) -> Iterator[Difference]:
    """Compare two collections — unordered for sets, else element-wise."""
    left = _elements(before)
    right = _elements(after)
    if isinstance(before, (set, frozenset)) or isinstance(after, (set, frozenset)):
        if {str(x) for x in left} != {str(x) for x in right}:
            yield Difference(path, sorted(str(x) for x in left), sorted(str(x) for x in right))
        return
    if len(left) != len(right):
        # Reported at the collection, not per element: a writer that dropped
        # the whole list is one loss, not N.
        yield Difference(path, f"<{len(left)} items>", f"<{len(right)} items>")
        return
    for index, (x, y) in enumerate(zip(left, right, strict=True)):
        yield from differences(x, y, f"{path}[{index}]")


def differences(before: object, after: object, path: str = "") -> Iterator[Difference]:
    """Every field where `before` and `after` disagree, depth-first.

    A length mismatch on a collection is reported at the collection rather than
    per element: a writer that dropped the whole list should read as one loss,
    not as N.
    """
    before, after = _normalise(before), _normalise(after)
    before_cls = type(before)
    if attrs.has(before_cls) and attrs.has(type(after)):
        for field in attrs.fields(before_cls):
            if field.name.startswith("_"):
                continue
            yield from differences(
                getattr(before, field.name, None),
                getattr(after, field.name, None),
                f"{path}.{field.name}",
            )
        return
    if before is None and after is None:
        return
    if _is_map(before) or _is_map(after):
        # Compared as a mapping, text included: losing one locale of two is a
        # real loss that element-wise walking would miss.
        #
        # `InternationalString` is named because it is *not* a `dict`, and
        # never was. Until #458 only `dict` was, so a text fell through to the
        # sequence branch below, which iterates its *keys* — a description
        # read from `<com:Name>` compared equal to itself, language for
        # language, and no text value in the model was ever compared.
        #
        # Narrowed rather than suppressed: either side may be the `None` of a
        # dropped field (#347).
        before_map = _as_map(before)
        after_map = _as_map(after)
        if before_map != after_map:
            yield Difference(path, before_map, after_map)
        return
    if _is_sequence(before) or _is_sequence(after):
        yield from _sequence_differences(before, after, path)
        return
    if before != after:
        yield Difference(path, before, after)


def generalise(path: str) -> str:
    """Drop collection indices: ``tree[0].children[1].name`` → ``tree[].children[].name``.

    A synthetic fixture holds one artefact per slot, so `[0]` is stable there.
    A real provider message holds hundreds, and the *same* loss then reports
    under a different index in every file. Registering the shape rather than
    the position is what lets one register describe both.
    """
    return re.sub(r"\[\d+\]", "[]", path)


def census(before: object, after: object, slot: str) -> list[str]:
    """The changed field *paths* under `slot`, for comparison against a baseline.

    Paths only — the values are for a human reading a failure, but a baseline
    keyed on them would churn every time the fixture changed a string.
    """
    return [f"{slot}{d.path}" for d in differences(before, after)]
