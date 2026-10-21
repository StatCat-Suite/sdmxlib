"""An item URN resolves to the item, at every layer that resolves URNs (#405).

The readers indexed `Concept` items and no others, so a reference to a `Code`,
a `Category` or a `Dimension` came back unresolved from a message that plainly
contained its target — and `InMemoryRegistry.get` and `storage.get_artefact`
answered None for the same URNs, for the same reason one layer up.

One mechanism serves all three (`model._items`), so the tests are together
rather than split by layer: what matters is that the same URN answers the same
way whether the caller asks a parsed message, an in-memory registry or DuckDB.

Every resolving case has a discrimination check beside it — an item the scheme
does not have, a scheme the store does not have, a class that does not match.
A fix that returned "something item-shaped" would pass the first half of this
file and fail the second.
"""

from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING, Any

import pytest

import sdmxlib as sl
from sdmxlib.model._items import find_item, owner_classes, resolve_item, scheme_urn_of, spellings
from sdmxlib.model._refshape import item_fields
from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.in_memory_registry import InMemoryRegistry
from sdmxlib.model.ref import Ref, UnresolvedRef
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.storage.lazy import LazyCodelist
from tests.support.factories import concept_ref

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit

_FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "fixtures"


def _item_urn(package: str, artefact_class: str, scheme: str, item_id: str, version: str = "1.0") -> SdmxUrn[Any]:
    return SdmxUrn.make(
        object,
        package=package,
        artefact_class=artefact_class,
        agency="AG",
        id=scheme,
        version=version,
        item_id=item_id,
    )


@pytest.fixture
def codelist() -> sl.Codelist:
    return sl.Codelist(
        id="CL_AREA",
        maintainer="AG",
        version="1.0",
        name={"en": "Area"},
        codes=[sl.Code(id="CA", name={"en": "Canada"}), sl.Code(id="ON", name={"en": "Ontario"})],
    )


@pytest.fixture
def category_scheme() -> sl.CategoryScheme:
    return sl.CategoryScheme(
        id="CS",
        maintainer="AG",
        version="1.0",
        name={"en": "cs"},
        categories=[sl.Category(id="ECON", name={"en": "e"}, categories=[sl.Category(id="GDP", name={"en": "g"})])],
    )


@pytest.fixture
def dsd() -> sl.DataStructure:
    return sl.DataStructure(
        id="DSD",
        maintainer="AG",
        version="1.0",
        name={"en": "dsd"},
        dimensions=[sl.Dimension(id="GEO", concept=concept_ref("GEO"))],
        time_dimension=sl.TimeDimension(concept=concept_ref("TIME_PERIOD")),
        attributes=[
            sl.Attribute(attachment=sl.AttachmentLevel.DATASET, id="OBS_STATUS", concept=concept_ref("OBS_STATUS"))
        ],
        measures=[sl.Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )


# ── owner_classes: derived, not listed ───────────────────────────────────


def test_owner_classes_answers_the_scheme_that_holds_the_item() -> None:
    """The inverse of `item_fields`, and the step an item URN cannot supply.

    An item URN carries the *item's* class — ``Code`` — while the index is
    keyed by the scheme's. Without this the lookup has no key to try.
    """
    assert [cls.__name__ for cls in owner_classes("Code")] == ["Codelist"]
    assert [cls.__name__ for cls in owner_classes("Category")] == ["CategoryScheme"]
    assert [cls.__name__ for cls in owner_classes("Dimension")] == ["DataStructure"]
    assert [cls.__name__ for cls in owner_classes("MetadataAttribute")] == ["MetadataStructure"]


def test_owner_classes_reaches_the_other_version_spelling() -> None:
    """A 3.0 ``DataAttribute`` URN has to find the model's ``Attribute``.

    The alias folding is in the derivation rather than at the call site, so
    every consumer gets it — the reader, the registry and the store — instead
    of the one that remembered.
    """
    assert [cls.__name__ for cls in owner_classes("DataAttribute")] == ["DataStructure"]
    assert [cls.__name__ for cls in owner_classes("PrimaryMeasure")] == ["DataStructure"]


def test_owner_classes_declines_rather_than_guesses() -> None:
    """Empty for a class no scheme holds. The caller then leaves the reference
    unresolved, which is what a closed world should say about something it does
    not have."""
    assert owner_classes("Codelist") == ()
    assert owner_classes("NotAnSdmxClass") == ()


def test_every_item_class_in_the_model_has_an_owner() -> None:
    """The derivation has to be *complete*, not merely right where it was tried.

    Walks every maintainable's declared item fields and asserts each item class
    resolves back to a scheme. A class that declared the URN pair but sat in no
    scheme's item field would be resolvable in name only — `owner_classes`
    would answer `()` and every reference to it would stay unresolved, which is
    #405's failure shape rather than a new one.
    """
    declared: set[str] = set()
    for scheme in _maintainable_classes():
        for _, item_cls in item_fields(scheme):
            declared.add(item_cls.sdmx_class)
    assert declared, "no item classes found at all — item_fields or the walk is broken"

    orphans = sorted(name for name in declared if not owner_classes(name))
    assert orphans == [], f"item classes no scheme owns: {orphans}"

    # Every alias spelling too — those are the ones a URN may arrive as.
    alias_orphans = sorted(alias for name in declared for alias in spellings(name) if not owner_classes(alias))
    assert alias_orphans == [], f"alias spellings with no owner: {alias_orphans}"


def _maintainable_classes() -> Iterator[type]:
    seen: set[str] = set()
    pending = list(MaintainableArtefact.__subclasses__())
    while pending:
        cls = pending.pop()
        if cls.__name__ in seen:
            continue
        seen.add(cls.__name__)
        pending.extend(cls.__subclasses__())
        yield cls


# ── find_item, over model artefacts ──────────────────────────────────────


def test_a_code_is_found_by_id(codelist: sl.Codelist) -> None:
    found = find_item(codelist, _item_urn("codelist", "Code", "CL_AREA", "ON"), sl.Codelist)
    assert isinstance(found, sl.Code)
    assert found.id == "ON"


def test_a_code_the_codelist_lacks_is_not_found(codelist: sl.Codelist) -> None:
    """Discrimination: the scheme is right, the item is not there."""
    assert find_item(codelist, _item_urn("codelist", "Code", "CL_AREA", "QC"), sl.Codelist) is None


def test_a_single_valued_component_field_is_searched(dsd: sl.DataStructure) -> None:
    """`DataStructure.time_dimension` is one component, not a list of them —
    and SDMX gives it its own item URN, so a reference to it is ordinary."""
    found = find_item(dsd, _item_urn("datastructure", "TimeDimension", "DSD", "TIME_PERIOD"), sl.DataStructure)
    assert isinstance(found, sl.TimeDimension)


def test_the_wrong_class_does_not_match_a_right_id(dsd: sl.DataStructure) -> None:
    """Discrimination on the class half: ``GEO`` is a dimension, so a URN
    calling it a measure names nothing, even though the id is in the DSD."""
    assert find_item(dsd, _item_urn("datastructure", "Measure", "DSD", "GEO"), sl.DataStructure) is None


def test_a_nested_category_is_found_by_its_path(category_scheme: sl.CategoryScheme) -> None:
    """The URN grammar gives a nested item its dotted path, and a 2.1 ``<Ref>``
    carries the same path in ``@id`` (a ``NestedIDType``)."""
    found = find_item(category_scheme, _item_urn("categoryscheme", "Category", "CS", "ECON.GDP"), sl.CategoryScheme)
    assert isinstance(found, sl.Category)
    assert found.id == "GDP"


def test_a_nested_category_is_not_found_by_its_leaf(category_scheme: sl.CategoryScheme) -> None:
    """A leaf id may repeat in another branch, so it names no one item (#439):
    a bare id is a root. Discrimination for the test above — the leaf is real."""
    assert find_item(category_scheme, _item_urn("categoryscheme", "Category", "CS", "GDP"), sl.CategoryScheme) is None


def test_a_dotted_path_that_does_not_exist_is_not_found(category_scheme: sl.CategoryScheme) -> None:
    """Discrimination for the dotted form: the parent is real, the child is not."""
    assert (
        find_item(category_scheme, _item_urn("categoryscheme", "Category", "CS", "ECON.NOPE"), sl.CategoryScheme)
        is None
    )


def test_a_dotted_path_through_the_wrong_parent_is_not_found(category_scheme: sl.CategoryScheme) -> None:
    """``GDP`` exists, but not under a category called ``TRADE`` — a dotted
    path is followed, not scanned for its last segment."""
    assert (
        find_item(category_scheme, _item_urn("categoryscheme", "Category", "CS", "TRADE.GDP"), sl.CategoryScheme)
        is None
    )


def test_scheme_urn_drops_the_item_and_takes_the_owner_class() -> None:
    urn = _item_urn("codelist", "Code", "CL_AREA", "CA")
    assert str(scheme_urn_of(urn, sl.Codelist)) == "urn:sdmx:org.sdmx.infomodel.codelist.Codelist=AG:CL_AREA(1.0)"


# ── resolve_item, over an index of schemes ───────────────────────────────


def test_resolve_item_finds_the_scheme_then_the_item(codelist: sl.Codelist) -> None:
    index = {str(codelist.urn): codelist}
    found = resolve_item(index, _item_urn("codelist", "Code", "CL_AREA", "CA"))
    assert isinstance(found, sl.Code)


def test_resolve_item_declines_when_the_scheme_is_absent(codelist: sl.Codelist) -> None:
    """Discrimination: the item id is real, the codelist named is not there."""
    index = {str(codelist.urn): codelist}
    assert resolve_item(index, _item_urn("codelist", "Code", "CL_GONE", "CA")) is None


def test_resolve_item_ignores_a_maintainable_urn(codelist: sl.Codelist) -> None:
    """A URN with no item id is not this function's business — the caller has
    already missed on it, and answering something here would mean inventing
    one."""
    assert resolve_item({str(codelist.urn): codelist}, codelist.urn) is None


# ── the readers: an intra-message reference (#405's report) ──────────────


def _structure(payload: bytes) -> sl.StructureMessage:
    """Parse, narrowed — `sl.parse` may answer either message kind."""
    msg = sl.parse(payload)
    assert isinstance(msg, sl.StructureMessage)
    return msg


def _parse(name: str) -> sl.StructureMessage:
    return _structure((_FIXTURES / name).read_bytes())


def _resolved(ref: Ref[Any] | None) -> Any:
    assert ref is not None
    return ref()


def test_a_hierarchical_code_resolves_to_a_code_in_the_message() -> None:
    """#405's first repro. The codelist is in the message; the reference came
    back unresolved and the caller found out on unwrap."""
    msg = _parse("sdmx_ml30/with_multiple_hierarchies.xml")
    assert [cl.id for cl in msg.codelists] == ["CL_AREA"]
    code = _resolved(msg.hierarchies[0].tree[0].code)
    assert isinstance(code, sl.Code)


def test_a_categorisation_resolves_both_ways() -> None:
    """The bug in one object: `source` is a maintainable and resolved, `target`
    is an item and did not — same artefact, same message, same index."""
    msg = _parse("sdmx_ml30/with_categories.xml")
    for categorisation in msg.categorisations:
        assert isinstance(_resolved(categorisation.source), sl.Dataflow)
        assert isinstance(_resolved(categorisation.target), sl.Category)


def test_a_categorisation_resolves_in_2_1_too() -> None:
    """All three readers built the index the same way, so all three had it."""
    msg = _parse("sdmx_ml21/complete.xml")
    assert msg.categorisations
    for categorisation in msg.categorisations:
        assert isinstance(_resolved(categorisation.target), sl.Category)


def test_a_hierarchy_association_resolves_to_the_dimension() -> None:
    """`linked_hierarchy` resolved while `linked_object` did not, in the same
    object — the difference being that one names a maintainable."""
    msg = _parse("sdmx_ml30/with_hierarchy_association.xml")
    association = msg.hierarchy_associations[0]
    assert isinstance(_resolved(association.linked_hierarchy), sl.Hierarchy)
    assert isinstance(_resolved(association.linked_object), sl.Dimension)


def test_a_reference_to_an_absent_item_stays_unresolved(codelist: sl.Codelist) -> None:
    """Discrimination at the reader layer: a dangling item reference must stay
    dangling. A partial message is normal, and `Ref.urn` is intact either way —
    which is exactly why the bug was silent."""
    missing = _item_urn("codelist", "Code", "CL_AREA", "QC")
    hierarchy = sl.Hierarchy(
        id="H",
        maintainer="AG",
        version="1.0",
        name={"en": "h"},
        tree=[sl.HierarchicalCode(id="QC", code=Ref[sl.Code].unresolved(missing))],
    )
    written = sl.to_xml(sl.StructureMessage(codelists=[codelist], hierarchies=[hierarchy]), version="3.0")
    parsed = _structure(written)
    with pytest.raises(UnresolvedRef):
        _resolved(parsed.hierarchies[0].tree[0].code)


# ── InMemoryRegistry ─────────────────────────────────────────────────────


def test_the_registry_answers_an_item_urn(codelist: sl.Codelist) -> None:
    registry = InMemoryRegistry()
    registry.add(codelist)
    found = registry.get(_item_urn("codelist", "Code", "CL_AREA", "CA"))
    assert isinstance(found, sl.Code)
    assert found.id == "CA"


def test_the_registry_still_answers_a_scheme_urn(codelist: sl.Codelist) -> None:
    """The exact-match path is untouched and still first."""
    registry = InMemoryRegistry()
    registry.add(codelist)
    assert registry.get(codelist.urn) is codelist


def test_the_registry_declines_an_item_it_does_not_have(codelist: sl.Codelist) -> None:
    """Discrimination: present codelist, absent code."""
    registry = InMemoryRegistry()
    registry.add(codelist)
    assert registry.get(_item_urn("codelist", "Code", "CL_AREA", "QC")) is None


def test_containment_agrees_with_get(codelist: sl.Codelist) -> None:
    """``in`` and `get` answer the same question, so they must answer it the
    same way — including for an item URN, and including when it is absent."""
    registry = InMemoryRegistry()
    registry.add(codelist)
    present = _item_urn("codelist", "Code", "CL_AREA", "CA")
    absent = _item_urn("codelist", "Code", "CL_AREA", "QC")
    assert present in registry
    assert absent not in registry
    assert (registry.get(present) is not None) is (present in registry)
    assert (registry.get(absent) is not None) is (absent in registry)


def test_the_registry_resolves_a_component_urn(dsd: sl.DataStructure) -> None:
    registry = InMemoryRegistry()
    registry.add(dsd)
    assert isinstance(registry.get(_item_urn("datastructure", "Dimension", "DSD", "GEO")), sl.Dimension)


# ── DuckDB storage ───────────────────────────────────────────────────────


@pytest.fixture
def stored(codelist: sl.Codelist, dsd: sl.DataStructure, category_scheme: sl.CategoryScheme) -> Iterator[Any]:
    with sl.LocalRegistry(":memory:") as registry:
        for artefact in (codelist, dsd, category_scheme):
            registry.add(artefact)
        yield registry


def test_the_store_answers_an_item_urn(stored: Any) -> None:
    found = stored.get(_item_urn("codelist", "Code", "CL_AREA", "CA"))
    assert isinstance(found, sl.Code)
    assert found.id == "CA"


def test_the_store_answers_a_component_urn(stored: Any) -> None:
    assert isinstance(stored.get(_item_urn("datastructure", "Dimension", "DSD", "GEO")), sl.Dimension)
    assert isinstance(stored.get(_item_urn("datastructure", "TimeDimension", "DSD", "TIME_PERIOD")), sl.TimeDimension)


def test_the_store_answers_a_nested_category_urn(stored: Any) -> None:
    found = stored.get(_item_urn("categoryscheme", "Category", "CS", "ECON.GDP"))
    assert isinstance(found, sl.Category)
    assert found.id == "GDP"
    assert stored.get(_item_urn("categoryscheme", "Category", "CS", "GDP")) is None, "a leaf is not a path"


def test_the_store_declines_an_absent_item(stored: Any) -> None:
    """Discrimination, both halves: an absent item and an absent scheme."""
    assert stored.get(_item_urn("codelist", "Code", "CL_AREA", "QC")) is None
    assert stored.get(_item_urn("codelist", "Code", "CL_GONE", "CA")) is None


def test_a_lazy_codelist_is_asked_for_one_code_not_all_of_them(monkeypatch: pytest.MonkeyPatch, stored: Any) -> None:
    """The store hands back a `LazyCodelist`, whose codes are a query.

    Reading them to find one would be the whole point of the facade lost: the
    bundle this library is measured against holds 579,951 codes in a single
    codelist. The subscript is the targeted lookup, so the assertion is that it
    is what gets called — **once**, not once per candidate field.

    A count rather than a duration, per CLAUDE.md: a wall-clock bound here
    would be a statement about the machine.
    """
    calls: list[str] = []
    real = LazyCodelist.__getitem__

    def counted(self: LazyCodelist, key: str | int) -> Any:
        calls.append(str(key))
        return real(self, key)

    monkeypatch.setattr(LazyCodelist, "__getitem__", counted)
    found = stored.get(_item_urn("codelist", "Code", "CL_AREA", "ON"))

    assert isinstance(found, sl.Code)
    assert calls == ["ON"], f"one targeted lookup for the code asked for, not {calls}"
