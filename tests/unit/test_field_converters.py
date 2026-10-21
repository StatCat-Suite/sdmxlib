"""Every immutable-collection field converts what it is given (#371).

    >>> dp = sl.DataProvider(id="P", contacts=[sl.Contact(name={"en": "c"})])
    >>> type(dp.contacts).__name__
    'list'          # declared: tuple[Contact, ...]

Thirty fields were in that shape: `tuple[X, ...]` with no converter, so the
value was stored exactly as passed and the annotation was simply false.
`isinstance(dp.contacts, tuple)` answered `False`, and `dp.contacts + (x,)`
raised on an object whose type promised otherwise.

It stayed invisible because `src` populates these from readers that already
build tuples, and `tests/` was unchecked until #347. It surfaced the moment
`tests/support` was type-checked, from fixture code passing the list literal
that reads more naturally.

The immutability the annotation implies was also absent, which is the half that
bites without ever looking like a type error: a stored list can be mutated by
whoever passed it in, after construction, including through a `@frozen`
container — `@frozen` is shallow.

## Why this is a walk rather than thirty assertions

The fix is one converter per field, and the failure mode is *forgetting one* on
a field added later. Thirty hand-written assertions would all pass while the
thirty-first field goes unconverted, so the guard enumerates the model instead
and asserts a property of every field it finds. A new artefact is covered the
day it is written.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from typing import TYPE_CHECKING

import attrs
import pytest

import sdmxlib as sl
import sdmxlib.model

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit

#: Declared types whose whole point is that the stored value is not the one
#: passed in. `list[...]` is deliberately absent: a field declaring a list and
#: storing the caller's list is telling the truth.
IMMUTABLE_PREFIXES = ("tuple[", "frozenset[", "set[")


def _model_classes() -> Iterator[type]:
    seen: set[int] = set()
    for module_info in pkgutil.walk_packages(sdmxlib.model.__path__, "sdmxlib.model."):
        module = importlib.import_module(module_info.name)
        for _name, obj in inspect.getmembers(module, inspect.isclass):
            if attrs.has(obj) and obj.__module__.startswith("sdmxlib.") and id(obj) not in seen:
                seen.add(id(obj))
                yield obj


def _immutable_fields() -> list[tuple[str, str, object]]:
    """(qualified name, declared type, converter) for every such field."""
    rows: list[tuple[str, str, object]] = []
    for cls in _model_classes():
        for field in attrs.fields(cls):
            declared = str(field.type)
            if declared.startswith(IMMUTABLE_PREFIXES):
                rows.append((f"{cls.__name__}.{field.name}", declared, field.converter))
    return sorted(rows)


def test_the_survey_finds_the_fields_it_is_meant_to() -> None:
    """A walk that finds nothing asserts nothing.

    The count is not pinned — new artefacts arrive — but the floor is, because
    the failure this file guards against is the walk silently stopping at zero
    after an import or naming change.
    """
    rows = _immutable_fields()
    assert len(rows) >= 30, f"only {len(rows)} immutable-collection fields found; the walk has gone blind"
    names = {name for name, _, _ in rows}
    assert "DataProvider.contacts" in names, "the field the issue opens with"
    assert "KeyValue.values" in names, "and the frozenset one the tuple survey missed"


def test_every_immutable_collection_field_has_a_converter() -> None:
    """The property, over the whole model.

    Without one, the annotation is a claim about a value the class never
    touched.
    """
    missing = [(name, declared) for name, declared, converter in _immutable_fields() if converter is None]
    assert not missing, (
        f"{len(missing)} field(s) declare an immutable collection and store what they are given:\n"
        + "\n".join(f"  {name}: {declared}" for name, declared in missing)
    )


# ── the behaviour the converters buy, at the boundary ─────────────────────


def test_a_list_passed_in_becomes_a_tuple() -> None:
    """The issue's own reproduction, as an assertion."""
    provider = sl.DataProvider(id="P", name={"en": "p"}, contacts=[sl.Contact(name={"en": "c"})])
    assert isinstance(provider.contacts, tuple)
    # Concatenation with a tuple is the operation the annotation promised and
    # a list could not do — it raised `TypeError: can only concatenate list`.
    extended = (*provider.contacts, sl.Contact(name={"en": "d"}))
    assert len(extended) == 2


def test_mutating_the_caller_s_list_afterwards_cannot_reach_the_artefact() -> None:
    """The half that never looks like a type error.

    `@frozen` is shallow, so a stored list stays writable through the reference
    the caller kept. Converting is what actually makes the field immutable.
    """
    contacts = [sl.Contact(name={"en": "first"})]
    provider = sl.DataProvider(id="P", name={"en": "p"}, contacts=contacts)
    contacts.append(sl.Contact(name={"en": "smuggled"}))
    assert len(provider.contacts) == 1


def test_a_nested_pair_field_converts_all_the_way_down() -> None:
    """`DataKey.values` is the model's only nested declaration.

    A plain `converter=tuple` fixes the outer sequence and leaves the pairs as
    lists, so the annotation stays false one level down — which is why this one
    field has its own converter rather than the builtin.
    """
    key = sl.DataKey(values=[["FREQ", "A"], ["GEO", "CA"]])
    assert isinstance(key.values, tuple)
    assert all(isinstance(pair, tuple) for pair in key.values), key.values
    assert key.values == (("FREQ", "A"), ("GEO", "CA"))


def test_a_frozenset_field_converts_too() -> None:
    """`KeyValue.values` declares a frozenset and had no converter either.

    Not in the issue's list of 30, which surveyed `tuple[` — same defect, one
    type over, found by widening the walk. Its elements are `RegionValue`s
    since #422, and a bare string still converts to one.
    """
    key_value = sl.KeyValue(dimension_id="FREQ", values=["A", "Q", "A"])
    assert isinstance(key_value.values, frozenset)
    assert key_value.values == {sl.RegionValue("A"), sl.RegionValue("Q")}
    assert key_value.codes() == {"A", "Q"}


def test_an_existing_tuple_survives_unchanged() -> None:
    """The converters must be idempotent — readers already build tuples."""
    pairs = (("FREQ", "A"),)
    assert sl.DataKey(values=pairs).values == pairs
    contacts = (sl.Contact(name={"en": "c"}),)
    assert sl.Agency(id="AG", contacts=contacts).contacts == contacts
