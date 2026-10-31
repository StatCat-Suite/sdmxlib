"""A partial item scheme against its full scheme, as a registry adds one over the other (GH-298).

SDMX 3.1 Section 2, §3.5.3.1 p. 37: a partial ItemScheme "cannot contain Items
that are not present in the full ItemScheme and the content of any one Item
(e.g., names and descriptions) cannot deviate from the content in the full
ItemScheme". The two share a URN, so one message cannot hold both; a registry
meets them when it adds one over the other. It keeps the full scheme, which
holds every item the partial does, and warns (#481) for each item the partial
adds or changes. Every field of an item is compared but the items nested under
it, which a partial leaves out by design.
"""

import warnings
from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, contextmanager

import attrs
import pytest

import sdmxlib as sl
from sdmxlib.model.errors import ImRuleWarning

pytestmark = pytest.mark.unit

_FULL = sl.Codelist(
    id="CL",
    maintainer="AG",
    version="1.0",
    name={"en": "codes"},
    codes=[
        sl.Code(id="A", name={"en": "A"}),
        sl.Code(id="B", name={"en": "B"}, parent_id="A", annotations=[sl.Annotation(type="T", text={"en": "x"})]),
        sl.Code(id="C", name={"en": "C"}),
    ],
)
_PARTIAL = _FULL.partial(["B"])

_CATEGORIES = sl.CategoryScheme(
    id="CAT",
    maintainer="AG",
    version="1.0",
    name={"en": "categories"},
    categories=[
        sl.Category(id="ECON", name={"en": "economy"}, categories=[sl.Category(id="GDP", name={"en": "GDP"})]),
        sl.Category(id="SOC", name={"en": "society"}),
    ],
)


@contextmanager
def _in_memory() -> Generator[sl.InMemoryRegistry | sl.LocalRegistry]:
    yield sl.InMemoryRegistry()


@contextmanager
def _local() -> Generator[sl.InMemoryRegistry | sl.LocalRegistry]:
    with sl.LocalRegistry(":memory:") as registry:
        yield registry


type _Opens = Callable[[], AbstractContextManager[sl.InMemoryRegistry | sl.LocalRegistry]]

_REGISTRIES: list[_Opens] = [_in_memory, _local]
_IDS = ["in-memory", "local"]


def _added(registry: sl.InMemoryRegistry | sl.LocalRegistry, *artefacts: sl.Codelist | sl.CategoryScheme) -> list[str]:
    """Add each in turn; the issues every warning names, as ``path: message``."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        for artefact in artefacts:
            registry.add(artefact)
    return [
        f"{issue.path}: {issue.message}"
        for warning in caught
        if isinstance(warning.message, ImRuleWarning)
        for issue in warning.message.issues
    ]


def test_a_partial_with_an_item_the_full_scheme_lacks_warns() -> None:
    partial = attrs.evolve(_PARTIAL, codes=[*_PARTIAL.codes, sl.Code(id="Z", name={"en": "Z"})])
    assert _added(sl.InMemoryRegistry(), _FULL, partial) == ["Codelist:CL[Z]: is not an item of the full scheme"]


@pytest.mark.parametrize("registry", _REGISTRIES, ids=_IDS)
def test_a_partial_added_over_its_full_scheme_keeps_the_full_scheme_quietly(registry: _Opens) -> None:
    """The discrimination check: `_PARTIAL` is the full scheme's own cut, so nothing warns."""
    with registry() as reg:
        assert _added(reg, _FULL, _PARTIAL) == []
        held = reg.get(_FULL.urn)
        assert held is not None
        assert (held.is_partial, list(held.code_ids())) == (False, ["A", "B", "C"])


@pytest.mark.parametrize("registry", _REGISTRIES, ids=_IDS)
def test_a_full_scheme_added_over_its_partial_replaces_it_and_checks_it(registry: _Opens) -> None:
    renamed = attrs.evolve(
        _PARTIAL, codes=[attrs.evolve(c, name={"en": "renamed"}) if c.id == "B" else c for c in _PARTIAL.codes]
    )
    with registry() as reg:
        assert _added(reg, renamed, _FULL) == ["Codelist:CL[B]: differs from the full scheme's item in name"]
        held = reg.get(_FULL.urn)
        assert held is not None
        assert (held.is_partial, list(held.code_ids())) == (False, ["A", "B", "C"])


@pytest.mark.parametrize("registry", _REGISTRIES, ids=_IDS)
def test_every_field_of_an_item_is_compared(registry: _Opens) -> None:
    changed = attrs.evolve(
        _PARTIAL,
        codes=[
            attrs.evolve(c, annotations=[], parent_id=None, description={"en": "d"}) if c.id == "B" else c
            for c in _PARTIAL.codes
        ],
    )
    with registry() as reg:
        assert _added(reg, _FULL, changed) == [
            "Codelist:CL[B]: differs from the full scheme's item in description, parent_id, annotations"
        ]


@pytest.mark.parametrize("registry", _REGISTRIES, ids=_IDS)
def test_a_nested_item_is_compared_by_where_it_is_nested(registry: _Opens) -> None:
    moved = attrs.evolve(
        _CATEGORIES,
        is_partial=True,
        categories=[
            sl.Category(id="SOC", name={"en": "society"}, categories=[sl.Category(id="GDP", name={"en": "GDP"})])
        ],
    )
    with registry() as reg:
        assert _added(reg, _CATEGORIES, moved) == [
            "CategoryScheme:CAT[GDP]: differs from the full scheme's item in parent"
        ]


def test_a_batch_holding_both_keeps_the_full_scheme_in_either_order() -> None:
    for batch in ([_FULL, _PARTIAL], [_PARTIAL, _FULL]):
        with sl.LocalRegistry(":memory:") as reg:
            reg.add_all(batch)
            held = reg.get(_FULL.urn)
            assert held is not None
            assert list(held.code_ids()) == ["A", "B", "C"]
        memory = sl.InMemoryRegistry()
        memory.add_all(batch)
        held = memory.get(_FULL.urn)
        assert held is not None
        assert list(held.code_ids()) == ["A", "B", "C"]


def test_replace_stores_a_partial_over_its_full_scheme() -> None:
    with sl.LocalRegistry(":memory:") as reg:
        reg.add(_FULL)
        reg.replace(_FULL.urn, _PARTIAL)
        held = reg.get(_FULL.urn)
        assert held is not None
        assert list(held.code_ids()) == ["A", "B"]
