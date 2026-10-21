"""The three mapping artefacts #395 added storage kinds for.

`RepresentationMap` was the only map that persisted; `ConceptSchemeMap`,
`CategorySchemeMap` and `StructureMap` were refused by `Registry.add` while
every wire format read and wrote them happily. That asymmetry is what this
closes — see #394 for why "not stored" had become "cannot read the document".

Two storage decisions are worth knowing before reading the assertions, both
taken from precedent already in `schema.py`:

* **One parent table per kind**, not a shared one with a discriminator, for
  the reason `metadata_provider` gives: they are separate artefacts, and an
  edge resolving to "a map" resolves to nothing in particular.
* **One `item_map` table for both scheme maps**, because `ConceptSchemeMap.maps`
  and `CategorySchemeMap.maps` are both `tuple[ItemMap, ...]` — the same class
  — the way `organisation_contact` serves `Agency` and `DataProvider`.
  `StructureMap.maps` is `ComponentMap`, a different class, so it gets its own.

`test_the_refusal_table_matches_the_dispatch` is the guard that would have
caught the two stale `_NOT_PERSISTED` entries this pass found: `StructureSet`
and `HierarchicalCodelist` became storable in #395's first step and stayed
listed as refused, which nothing noticed because the dispatch finds a writer
first and never consults the table.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

import sdmxlib as sl
from sdmxlib.model.mapping import (
    CategorySchemeMap,
    ComponentMap,
    ConceptSchemeMap,
    ItemMap,
    StructureMap,
)
from sdmxlib.model.ref import artefact_urn
from sdmxlib.query.accessors import q
from sdmxlib.storage._kinds import CLASS_TO_NAME
from sdmxlib.storage.writers import _NOT_PERSISTED, _WRITER_DISPATCH
from tests.support.narrow import present

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit

#: The three, as one type. Every assertion below reads `id` / `name` / `maps`
#: / `source` / `target`, which all three carry.
type AnyMap = ConceptSchemeMap | CategorySchemeMap | StructureMap


def _endpoints() -> tuple[sl.ConceptScheme, sl.CategoryScheme, sl.DataStructure]:
    return (
        sl.ConceptScheme(id="CS", maintainer="AG", version="1.0", name={"en": "Concepts"}),
        sl.CategoryScheme(id="CATS", maintainer="AG", version="1.0", name={"en": "Categories"}),
        sl.DataStructure(id="DSD", maintainer="AG", version="1.0", name={"en": "Structure"}),
    )


def _maps() -> tuple[ConceptSchemeMap, CategorySchemeMap, StructureMap]:
    """One of each, with *two* pairs so order is observable."""
    concepts, categories, dsd = _endpoints()
    return (
        ConceptSchemeMap(
            id="CSM",
            maintainer="AG",
            version="1.0",
            name={"en": "Concept map"},
            description={"en": "how one scheme's concepts read as another's"},
            source=sl.Ref.to(concepts),
            target=sl.Ref.to(concepts),
            maps=(ItemMap(source_id="ZULU", target_id="Z"), ItemMap(source_id="ALPHA", target_id="A")),
        ),
        CategorySchemeMap(
            id="CATM",
            maintainer="AG",
            version="1.0",
            name={"en": "Category map"},
            source=sl.Ref.to(categories),
            target=sl.Ref.to(categories),
            maps=(ItemMap(source_id="B", target_id="B2"), ItemMap(source_id="A", target_id="A2")),
        ),
        StructureMap(
            id="SM",
            maintainer="AG",
            version="1.0",
            name={"en": "Structure map"},
            source=sl.Ref.to(dsd),
            target=sl.Ref.to(dsd),
            maps=(
                ComponentMap(sources=("FREQ",), targets=("FREQUENCY",)),
                ComponentMap(sources=("A",), targets=("B",)),
            ),
        ),
    )


def _read_back(reg: sl.LocalRegistry, original: AnyMap) -> AnyMap:
    """The stored artefact, narrowed to the three this file is about.

    `get(SdmxUrn.parse(...))` answers `object | None`: a parsed URN names no
    particular type (#399). The narrowing is what these tests already assert.
    """
    back = reg.get(sl.SdmxUrn.parse(str(artefact_urn(original))))
    assert back is not None, f"{type(original).__name__} did not come back"
    assert isinstance(back, ConceptSchemeMap | CategorySchemeMap | StructureMap)
    return back


@pytest.fixture
def stored() -> Iterator[sl.LocalRegistry]:
    with sl.LocalRegistry(":memory:") as reg:
        reg.add_all([*_endpoints(), *_maps()])
        yield reg


# ── round trip ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("original", _maps(), ids=lambda m: type(m).__name__)
def test_each_map_round_trips(stored: sl.LocalRegistry, original: AnyMap) -> None:
    """Write, read, compare — the shape the existing kinds are held to."""
    back = _read_back(stored, original)
    assert type(back) is type(original)
    assert back.id == original.id
    assert back.name == original.name
    assert back.description == original.description
    assert back.version == original.version


@pytest.mark.parametrize("original", _maps(), ids=lambda m: type(m).__name__)
def test_the_pairs_survive_in_order(stored: sl.LocalRegistry, original: AnyMap) -> None:
    """`maps` is a tuple, so its order is part of the artefact.

    The fixtures are deliberately *not* sorted — `ZULU` before `ALPHA` — so a
    reader that lost the order and re-sorted would fail here rather than
    coincide with the input.
    """
    back = _read_back(stored, original)
    assert back.maps == original.maps


@pytest.mark.parametrize("original", _maps(), ids=lambda m: type(m).__name__)
def test_the_endpoints_survive(stored: sl.LocalRegistry, original: AnyMap) -> None:
    back = _read_back(stored, original)
    assert back.source is not None
    assert back.target is not None
    assert str(back.source.urn) == str(present(original.source).urn)
    assert str(back.target.urn) == str(present(original.target).urn)


# ── the query surface ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("root", "expected"),
    [(q.concept_scheme_map, ["CSM"]), (q.category_scheme_map, ["CATM"]), (q.structure_map, ["SM"])],
    ids=["concept_scheme_map", "category_scheme_map", "structure_map"],
)
def test_each_map_has_a_queryable_root(stored: sl.LocalRegistry, root: Any, expected: list[str]) -> None:
    assert stored.query(root).ids() == expected


def test_the_endpoint_hop_reaches_the_scheme(stored: sl.LocalRegistry) -> None:
    """`source` / `target` are named plainly, unlike `representation_map`'s
    role-plus-class pair: those two endpoints are both Codelist and needed
    telling apart, while these name one class each."""
    rows = stored.query(q.concept_scheme_map).select(q.concept_scheme_map.source.id).collect()
    assert rows.to_series().to_list() == ["CS"]


def test_the_shared_item_map_table_does_not_mix_the_two_kinds(stored: sl.LocalRegistry) -> None:
    """One table holds both scheme maps' pairs, so the discrimination has to
    come from the root. If it did not, each map would see the other's rows."""
    concept_pairs = stored.query(q.concept_scheme_map).select(n=q.concept_scheme_map.item_maps.count()).collect()
    category_pairs = stored.query(q.category_scheme_map).select(n=q.category_scheme_map.item_maps.count()).collect()
    assert concept_pairs.to_dicts() == [{"n": 2}]
    assert category_pairs.to_dicts() == [{"n": 2}]
    assert stored.query(q.item_map).count() == 4, "and the table itself holds both"


def test_component_maps_are_reachable_from_their_structure_map(stored: sl.LocalRegistry) -> None:
    rows = stored.query(q.structure_map).select(n=q.structure_map.component_maps.count()).collect()
    assert rows.to_dicts() == [{"n": 2}]


# ── the refusal table cannot drift from the dispatch ──────────────────────


def test_the_refusal_table_matches_the_dispatch() -> None:
    """Every `_NOT_PERSISTED` entry names a type that really is refused.

    Two entries had gone stale — `StructureSet` and `HierarchicalCodelist`
    became storable in #395's first step and stayed listed as refused. Nothing
    noticed because `put_artefact` finds a writer first and never reaches the
    table, so the wrong reason was only ever one dispatch change away from
    being printed.
    """
    storable = {cls.__name__ for cls in _WRITER_DISPATCH} | set(CLASS_TO_NAME.values())
    stale = sorted(set(_NOT_PERSISTED) & storable)
    assert not stale, (
        f"{stale} are listed as not persisted but this registry stores them — "
        f"remove the entries, or the refusal will name a reason that is no longer true"
    )


def test_the_three_maps_are_no_longer_refused() -> None:
    """The other direction, named rather than inferred from the count."""
    for name in ("ConceptSchemeMap", "CategorySchemeMap", "StructureMap"):
        assert name not in _NOT_PERSISTED, f"{name} is stored now (#395)"


def test_attachment_constraint_is_still_refused_by_name() -> None:
    """The one deliberately left out, and why.

    3.0 folded `AttachmentConstraint` into `DataConstraint`, so storing it is
    a modelling decision — its own kind, or a `DataConstraint` with a role —
    rather than a table. Until that is decided it refuses *by name*, which is
    the acceptance criterion #395 states for anything still deferred.
    """
    assert "AttachmentConstraint" in _NOT_PERSISTED
    assert "DataConstraint" in _NOT_PERSISTED["AttachmentConstraint"], "the reason names the 3.0 form"
