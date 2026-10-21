"""The static ref-shape must agree with a fresh reflective walk (#376).

`model/_refshape.py` decides from **declared types** which fields a
`Ref`-hunting walk needs to read, and whether a class can reach a `Ref` at all.
That replaced three runtime walks that decided the same thing from the
**values in front of them**, and it is what makes `_collect_refs` skip a `Code`
outright — 52% of a large eager read.

Trading a value-based answer for a type-based one is an assumption, and #376
says so plainly: a field annotated `str | None` that somehow held a `Ref` would
silently stop resolving. These tests are what stops that being a promise.

The oracle is `_reflective_shape` below — the walk `_refshape` replaced,
rewritten here from its behaviour rather than imported, so a change to the
implementation cannot change the oracle with it. Same rule as the query
oracles in `tests/support/oracle.py`.

**The direction that matters is one-way.** The static answer may be *wider*
than the dynamic one — `Level.coding_format` is walked though a `Facet` holds
no `Ref` — and that costs a wasted `getattr`. It must never be *narrower*, and
narrower is what these assert against.

**With exactly one exception**, added by #383: a field marked
`derived_view()` is skipped deliberately, because its contents are reachable
through another field of the same object — `Hierarchy._codes` is
`_flatten_nodes(self.tree)`, the same objects. Reflection has no way to know
that, so it reports those fields and the static answer is legitimately
narrower there.

The exception is subtracted below rather than the assertion being loosened,
and the burden it sheds is carried by
`tests/unit/test_derived_fields_are_reachable_anyway.py`, which proves on a
populated graph that a marked field holds nothing an unmarked one does not
already reach. Loosening this file without that one would have made the marker
a way to hide an edge from resolution, interning and validation at once.
"""

import enum
import inspect
import pathlib
import pkgutil
import typing
from importlib import import_module
from typing import Any

import attrs
import pytest

import sdmxlib.model
from sdmxlib.model._refshape import DERIVED_VIEW, can_reach_ref, walk_fields
from sdmxlib.model.collections import ArtefactList, ItemList
from sdmxlib.model.constraint import TimeBound
from sdmxlib.model.errors import ValidationError
from sdmxlib.model.ref import Ref

pytestmark = pytest.mark.unit


def _model_attrs_classes() -> list[type]:
    """Every attrs class reachable from `sdmxlib.model`.

    Discovered rather than listed, so a new artefact is covered on the day it
    lands. #148's comment records the opposite failure: a hand-listed recursion
    that covered two types while the model grew past a dozen.
    """
    found: dict[str, type] = {}
    for info in pkgutil.walk_packages(sdmxlib.model.__path__, prefix="sdmxlib.model."):
        module = import_module(info.name)
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if attrs.has(obj) and obj.__module__.startswith("sdmxlib.model"):
                found[f"{obj.__module__}.{obj.__qualname__}"] = obj
    return [found[k] for k in sorted(found)]


_CLASSES = _model_attrs_classes()


def _derived_fields(cls: type) -> set[str]:
    """Fields skipped on purpose — see the module docstring's exception."""
    return {f.name for f in attrs.fields(cls) if f.metadata.get(DERIVED_VIEW)}


def _reflective_shape(obj: object) -> set[str]:
    """Fields the *old* walk would have read something interesting from.

    The oracle. This is `_collect_refs` as it was before #376 — look at every
    field of the object in front of you, and note the ones holding a `Ref`, an
    `ItemList`, or another attrs instance. Restated here rather than imported,
    because an oracle that shares code with its subject can be wrong the same
    way.
    """
    cls = type(obj)
    if not attrs.has(cls):
        return set()
    interesting: set[str] = set()
    for f in attrs.fields(cls):
        value = getattr(obj, f.name, None)
        if value is None:
            continue
        if isinstance(value, (Ref, ItemList, ArtefactList)) or (attrs.has(type(value)) and not isinstance(value, type)):
            interesting.add(f.name)
    return interesting


#: Parsed rather than hand-built. A hand-built graph contains the fields its
#: author remembered, which is the same failure mode as a hand-listed
#: recursion; these files were written to exercise the *readers*, so they fill
#: in corners nobody would think to fill in here — structure maps, codelist
#: extensions, hierarchy associations, metadata structures, groups.
_FIXTURES = [
    ("sdmx_ml21", "complete.xml"),
    ("sdmx_ml21", "with_hierarchy.xml"),
    ("sdmx_ml21", "with_metadata_structure.xml"),
    ("sdmx_ml21", "with_structure_map.xml"),
    ("sdmx_ml30", "complete.xml"),
    ("sdmx_ml30", "with_categories.xml"),
    ("sdmx_ml30", "with_codelist_extension.xml"),
    ("sdmx_ml30", "with_groups.xml"),
    ("sdmx_ml30", "with_hierarchy_association.xml"),
    ("sdmx_ml30", "with_metadata.xml"),
    ("sdmx_ml30", "with_multiple_hierarchies.xml"),
    ("sdmx_ml30", "with_representation_map_variants.xml"),
]


@pytest.fixture(scope="module")
def rich_artefacts() -> list[object]:
    """Every artefact from the reader fixtures, as one flat list."""
    from sdmxlib.formats import parse

    root = pathlib.Path(__file__).parent.parent / "fixtures"
    artefacts: list[object] = []
    for folder, name in _FIXTURES:
        path = root / folder / name
        if not path.is_file():
            continue
        message = parse(path.read_bytes())
        artefacts.extend(message.all_artefacts())
    return artefacts


def _default_instance(cls: type) -> object | None:
    """An instance of *cls* built from its own defaults, or None if it needs args.

    A maintainable's id, maintainer and name have no defaults, and a
    `TimeRange` needs one bound (#492), so each is given the least it needs
    before being routed elsewhere.

    Deliberately crude. A class that cannot be default-constructed is covered
    by `test_walk_fields_covers_a_populated_graph` through the fixture
    graph instead, so this returning None is not a gap — it is a routing
    decision.
    """
    attempts: tuple[dict[str, object], ...] = (
        {},
        {"id": "X", "maintainer": "AG", "name": {"en": "x"}},
        {"start": TimeBound("2000")},
    )
    for kwargs in attempts:
        try:
            instance: object = cls(**kwargs)
        except TypeError, ValueError, ValidationError:
            continue
        return instance
    return None


# ── the one-way agreement ─────────────────────────────────────────────────────


@pytest.mark.parametrize("cls", _CLASSES, ids=lambda c: c.__qualname__)
def test_walk_fields_covers_everything_reflection_finds(cls):
    """No field the dynamic walk cared about may be missing from the static set.

    Uses a default-constructed instance where one exists. That is a weak
    instance — most optional `Ref` fields will be `None` — so this alone is not
    enough, and `test_walk_fields_covers_a_populated_graph` carries the real
    weight. It is here because it covers *every* class rather than the ones a
    fixture happens to build.
    """
    obj = _default_instance(cls)
    if obj is None:
        pytest.skip(f"{cls.__qualname__} cannot be default-constructed; covered by the populated graph")
    missing = _reflective_shape(obj) - set(walk_fields(cls)) - _derived_fields(cls)
    assert not missing, f"{cls.__qualname__}: reflection reads {sorted(missing)}, walk_fields does not"


@pytest.mark.parametrize("cls", _CLASSES, ids=lambda c: c.__qualname__)
def test_a_class_with_a_ref_field_can_reach_one(cls):
    """`can_reach_ref` must be True for anything whose own fields declare a `Ref`.

    The shallowest possible check, and the one that would have caught the worst
    version of this bug: a class that holds a `Ref` directly and is skipped.
    """
    declares_ref = any("Ref" in str(f.type) for f in attrs.fields(cls))
    if declares_ref:
        assert can_reach_ref(cls), f"{cls.__qualname__} declares a Ref field but is skipped"


def test_walk_fields_covers_a_populated_graph(rich_artefacts):
    """The real check: a graph with every optional field actually filled in.

    Walks the whole object graph and, at each node, compares what reflection
    would read against what `walk_fields` permits. Any node where the static
    set is narrower fails, naming the class and the field.
    """
    seen: set[int] = set()
    problems: list[str] = []

    def visit(obj: object) -> None:
        cls = type(obj)
        if id(obj) in seen or not attrs.has(cls):
            return
        seen.add(id(obj))
        missing = _reflective_shape(obj) - set(walk_fields(cls)) - _derived_fields(cls)
        if missing:
            problems.append(f"{cls.__qualname__}.{sorted(missing)}")
        if not can_reach_ref(cls) and any(isinstance(getattr(obj, f.name, None), Ref) for f in attrs.fields(cls)):
            problems.append(f"{cls.__qualname__} holds a Ref but can_reach_ref is False")
        # Descends through *every* field, derived ones included: this walker
        # exists to reach as many objects as possible, and skipping a derived
        # view here would shrink the coverage the assertions run over.
        for f in attrs.fields(cls):
            value = getattr(obj, f.name, None)
            if isinstance(value, (ItemList, ArtefactList)):
                for item in value:
                    visit(item)
            elif attrs.has(type(value)) and not isinstance(value, type):
                visit(value)

    for artefact in rich_artefacts:
        visit(artefact)

    assert seen, "the fixture graph produced no attrs objects — the assertions would be vacuous"
    assert not problems, f"static shape narrower than reflection at: {sorted(set(problems))}"


# ── witnesses that must live at module level ──────────────────────────────────
# attrs resolves a class's annotations against its *module* namespace, so a
# class defined inside a test function gets unresolved string annotations,
# which this module reads as "unreadable" and answers True for. That is safe,
# and it is also vacuous for any test meaning to exercise a real shape.


@attrs.define
class _SelfRef:
    """A cycle containing no `Ref` — the witness for the cycle set."""

    name: str = ""
    children: list[_SelfRef] = attrs.field(factory=list)


@attrs.define
class _SelfRefWithRef:
    """The same cycle, with a `Ref` at the far end of it."""

    target: Ref[object] | None = None
    children: list[_SelfRefWithRef] = attrs.field(factory=list)


@attrs.define
class _LegacyBag:
    """`typing.List` — a container origin with no element type."""

    items: typing.List[object] = attrs.field(factory=list)  # noqa: UP006 — the point of the test


# A forward reference stays a *string* on the attrs field until something
# resolves it, and an unresolved annotation is "unreadable", which answers True
# for free. That is safe but vacuous: the first version of `_SelfRef` passed its
# test while never recursing at all. Resolving them here is what makes these
# witnesses witness anything.
for _witness in (_SelfRef, _SelfRefWithRef):
    attrs.resolve_types(_witness)


# ── the specific claims #376 rests on ─────────────────────────────────────────


def test_code_cannot_reach_a_ref():
    """The whole point. If this ever becomes True, the 52% comes back.

    It would become True legitimately — a `Code` gaining an outgoing edge is a
    model change, not a bug — and then this test is the prompt to re-measure
    rather than a thing to delete.
    """
    from sdmxlib.model.codelist import Code

    assert not can_reach_ref(Code)
    assert walk_fields(Code) == ()


def test_a_hierarchical_code_walks_only_the_fields_that_can_carry_one():
    """Two of ten fields, not ten. The other half of #376's measurement."""
    from sdmxlib.model.hierarchy import HierarchicalCode

    fields = set(walk_fields(HierarchicalCode))
    assert {"code", "children"} <= fields
    assert len(fields) < len(attrs.fields(HierarchicalCode))


def test_an_unreadable_annotation_is_assumed_to_reach_a_ref():
    """Conservative where it cannot see — the property that makes this safe.

    An `object`-typed field could hold anything, so it must be walked. A
    version that returned False here would be fast and wrong, and nothing else
    in this file would catch it: no model class is annotated that way today.
    """

    @attrs.define
    class Opaque:
        payload: object = None

    assert can_reach_ref(Opaque)
    assert "payload" in walk_fields(Opaque)


def test_a_scalar_only_class_cannot_reach_a_ref():
    @attrs.define
    class Plain:
        name: str = ""
        count: int = 0

    assert not can_reach_ref(Plain)
    assert walk_fields(Plain) == ()


def test_a_self_referential_class_terminates():
    """A cycle with no `Ref` anywhere in it must terminate, not recurse.

    `HierarchicalCode.children` is an `ItemList[HierarchicalCode]`, so the model
    has the shape — but `HierarchicalCode.code` is a `Ref` and comes first, so
    `_reaches` returns True before it ever recurses. That makes the model a bad
    witness: a mutation check showed the cycle set could be deleted with every
    test still green.

    `_SelfRef` below is the witness that bites. No `Ref` anywhere, so the
    closure has to walk the whole cycle and come back False — which without
    `seen` is an unbounded recursion.

    Module-level, not local, because a class defined inside a function has an
    annotation attrs cannot resolve; it degrades to "unreadable", answers True
    immediately, and never recurses at all. The first version of this test did
    exactly that and asserted nothing.
    """
    assert can_reach_ref(_SelfRef) is False


def test_a_self_referential_class_that_does_hold_a_ref_still_terminates():
    assert can_reach_ref(_SelfRefWithRef) is True


def test_a_legacy_bare_generic_is_assumed_to_reach_a_ref():
    """`typing.List` has a container origin and no element type.

    The one way to reach `_parameterised`'s empty-args arm: a bare `typing.List`
    parameterises to `list` with `get_args() == ()`, unlike a bare `list`, which
    has no origin at all and takes the other branch. Found by mutation — the arm
    was uncovered and looked unreachable.
    """
    assert can_reach_ref(_LegacyBag)
    assert "items" in walk_fields(_LegacyBag)


def test_an_unparameterised_container_is_assumed_to_reach_a_ref():
    """A bare `ItemList` says nothing about what is in it.

    This is the case `_is_container` exists for, and it was *not* covered until
    a mutation check found it: with every model field parameterised
    (`ItemList[Code]`), the container arm was redundant — the element type's
    own `attrs.has` already included the field — so deleting `_is_container`
    broke nothing and the guard looked like decoration.

    It is not decoration. `_reaches` descends only through `attrs.has`
    mentions, so without this a class whose only route to a `Ref` ran through
    an unparameterised collection would be skipped and its refs silently left
    unresolved. Written against a synthetic class because the model has no such
    field today — which is exactly why nothing else catches it.
    """

    @attrs.define
    class Bag:
        items: ItemList[Any] = attrs.field(factory=ItemList)

    assert can_reach_ref(Bag)
    assert "items" in walk_fields(Bag)


def test_a_bare_list_field_is_assumed_to_reach_a_ref():
    """Same argument, for the container the model does not use but might."""

    @attrs.define
    class Loose:
        items: list[object] = attrs.field(factory=list)

    assert can_reach_ref(Loose)
    assert "items" in walk_fields(Loose)


def test_an_enum_field_is_not_walked():
    """Enums are attrs-less and hold nothing; a walk into one is pure cost."""

    class Colour(enum.Enum):
        RED = "red"

    @attrs.define
    class Painted:
        colour: Colour = Colour.RED

    assert not can_reach_ref(Painted)


def test_every_model_class_was_actually_discovered():
    """Guards the discovery itself.

    If `_model_attrs_classes` silently returned a handful, every parametrised
    test above would pass while covering almost nothing — the vacuity failure
    CLAUDE.md's rule 4 is about.
    """
    names = {c.__qualname__ for c in _CLASSES}
    assert len(_CLASSES) > 40, f"only discovered {len(_CLASSES)} attrs classes"
    assert {"Code", "Codelist", "HierarchicalCode", "DataStructure", "Dataflow", "MetadataAttribute"} <= names
