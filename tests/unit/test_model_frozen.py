"""Every model class is frozen, and an artefact compares by value but does not hash (#492).

Artefacts and items were `@define`: mutable, with nothing recording why. The
mutation they allowed was a `Ref` being filled in and a reader appending
children; a `Ref` now finds its target through its registry and the readers
build a tree from its leaves, so nothing is left that needs it. Frozen, an
artefact a registry hands out cannot be changed under another holder, and a
rule `__init__` checks stays true.

Unhashable, as the mutable classes were and `ItemList` is: hashing a 579,951
-code codelist would walk every code, and a URN is the key a caller wants.
"""

import importlib
import pkgutil

import attrs
import pytest

import sdmxlib.model
from sdmxlib.model.base import MaintainableArtefact

pytestmark = pytest.mark.unit


def _model_classes() -> list[type]:
    found: list[type] = []
    for info in pkgutil.walk_packages(sdmxlib.model.__path__, "sdmxlib.model."):
        module = importlib.import_module(info.name)
        found.extend(
            value
            for value in vars(module).values()
            if isinstance(value, type) and attrs.has(value) and value.__module__ == info.name
        )
    return found


def _is_frozen(cls: type) -> bool:
    # attrs installs this one function as `__setattr__` on every frozen class.
    return getattr(cls.__setattr__, "__name__", "") == "_frozen_setattrs"


def test_the_sweep_sees_every_model_class():
    """Exact, so a class added or lost moves it: the sweeps below say nothing about a class they miss."""
    assert len(_model_classes()) == 110


def test_every_model_class_is_frozen():
    assert [cls.__qualname__ for cls in _model_classes() if not _is_frozen(cls)] == []


def test_the_frozen_check_can_fail():
    @attrs.define
    class Mutable:
        x: int

    @attrs.frozen
    class Frozen:
        x: int

    assert not _is_frozen(Mutable)
    assert _is_frozen(Frozen)


def test_every_artefact_is_unhashable():
    artefacts = [cls for cls in _model_classes() if issubclass(cls, MaintainableArtefact)]
    assert len(artefacts) == 30
    assert [cls.__qualname__ for cls in artefacts if cls.__hash__ is not None] == []
